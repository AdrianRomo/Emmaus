"""Every write of the forms, and the rules that hold whoever asks.

Callers (``formularios.views``, ``formularios.public``) have already answered who may write; these
functions enforce the rest: what a form may ask, that what is published is what the administrator
read, that an answer goes to the version that is published right now, that the same contact
answering twice in a day is one answer and one task, and that every write leaves one church audit
row with ids and fixed words only: never a title, a question, an answer or a contact.

Lock order of the module: the ``Form`` row first, then anything under it.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import re
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from events.services.exports import safe_cell
from people import duplicates, flags
from people import services as people_services
from people.audit import record_church
from people.models import Person

from . import access
from .models import MAX_OPTIONS, MAX_QUESTIONS, OPTION_MAX, Form, FormReader, FormTask, FormVersion, Question, SeenSeal, Submission

Kind = Question.Kind
Status = Form.Status
YES, NO = "Sí", "No"
YES_NO = (YES, NO)
CONDITION_KINDS = (Kind.CHOICE, Kind.YES_NO)
# A form is open for a year at most: longer than that and "N days after it closes" is no retention at all.
MAX_OPEN_DAYS = 366
MAX_TASK_DAYS = 60
# Answers one form keeps at a time. A public address can be flooded; past this the form says it takes no more.
MAX_SUBMISSIONS = 2000
# Public answers of one form in ``FLOW_WINDOW`` after which the following ones are kept "por revisar" instead of
# listed: more than people send to a church form, little enough that a real campaign is only delayed a click.
FLOW_LIMIT = 20
FLOW_WINDOW = timedelta(minutes=10)
# Answers "por revisar" one form keeps. Past this a public form says, with the person's answers still on the
# page, that it could not keep the answer: the only refusal left, and it takes 20,000 sends to reach.
MAX_HELD = 20000
# Open tasks one form has at a time. Past this an answer is kept and listed but leaves no task.
MAX_OPEN_TASKS = 200
DUPLICATE_WINDOW = timedelta(days=1)
DEFAULT_CONSENT = "Acepto que {church} use estos datos solo para contactarme por este formulario."
CLOSED = "Este formulario ya está cerrado: ya no cambia ni recibe respuestas."
SENSITIVE_WARNING = (
    "No preguntes por salud, vida espiritual, conflictos familiares ni datos de menores de edad en un formulario general. "
    "Para eso están las conversaciones en persona y los procesos que tu iglesia ya cuida."
)
MINORS_OUT = (
    "Los formularios generales no sirven para inscribir a niñas, niños o adolescentes a algo: eso todavía no existe en Emmaus. "
    "Quien la iglesia tiene registrado como menor de edad no ve ni responde los formularios de la iglesia."
)


class FormError(ValidationError):
    """A rule of the forms refused the action; the message is ready for the screen."""


class StalePreview(Exception):
    """What the administrator confirmed is not what the draft holds any more: show it again."""


class FormFull(Exception):
    """The form already keeps as many answers as one form may (``MAX_SUBMISSIONS``)."""


class FormChanged(Exception):
    """The form was published again, closed or filled up while somebody was answering it."""


# ---- small helpers ------------------------------------------------------------------------------
def church_tz(church) -> ZoneInfo:
    try:
        return ZoneInfo(church.time_zone)
    except Exception:  # an invalid zone saved through the admin
        return ZoneInfo("America/Mexico_City")


def church_today(church, now=None) -> date:
    """The day it is where the church is: the instant, read in the church's zone."""
    return (now or timezone.now()).astimezone(church_tz(church)).date()


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f  ]")
_LINK = re.compile(r"(https?://|www\.)", re.IGNORECASE)
_PHONE = re.compile(r"\+?[0-9 ()\-]+")


def clean_line(text, limit: int, what: str, *, code: str, required: bool = True) -> str:
    """Words an administrator typed that other people will read without anybody approving them: one
    line (line breaks and runs of spaces become one space), no control characters, no links, no
    angle brackets, up to ``limit`` characters."""
    text = " ".join(_CONTROL.sub(" ", str(text or "")).split())
    if not text:
        if required:
            raise FormError(f"Escribe {what}.", code=code)
        return ""
    if len(text) > limit:
        raise FormError(f"Usa hasta {limit} caracteres en {what}; ahora tiene {len(text)}.", code=code)
    if _LINK.search(text) or "<" in text or ">" in text:
        raise FormError(f"No pongas enlaces ni los signos < y > en {what}.", code=code)
    return text


def clean_phone(phone: str) -> str:
    phone = " ".join(str(phone or "").split())
    if not phone:
        return ""
    if len(phone) > 30 or not _PHONE.fullmatch(phone) or len(re.sub(r"\D", "", phone)) < 8:
        raise FormError("Escribe el teléfono con al menos 8 números, por ejemplo 55 1234 5678.", code="phone")
    return phone


def contact_key(form_id: int, kind: str, value: str) -> str:
    """The same contact answering the same form, as a keyed hash (as ``feedback.services.token_for``):
    enough to notice a second answer, useless to list contacts. Empty for an empty value."""
    value = duplicates.normal_email(value) if kind == "email" else duplicates.normal_phone(value)
    if not value:
        return ""
    message = f"formularios:{form_id}:{kind}:{value}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def _locked(form: Form | int) -> Form:
    pk = form.pk if isinstance(form, Form) else form
    return Form.objects.select_for_update(of=("self",)).select_related("church", "current_version").get(pk=pk)


def _audit(form: Form, actor, action: str, summary: str, obj=None, **metadata) -> None:
    record_church(form.church, actor, action, summary, obj or form, form_id=form.pk, **metadata)


# ---- the state of a form ------------------------------------------------------------------------
def is_open(form: Form, today: date | None = None) -> bool:
    """Whether the form takes answers now: published, with a version, and today between its two days."""
    if form.status != Status.PUBLISHED or form.current_version_id is None:
        return False
    today = today or church_today(form.church)
    return form.opens_on <= today <= form.closes_on


def phase(form: Form, today: date | None = None) -> str:
    """One word for the staff's screens: borrador, programado, abierto, terminado, cerrado."""
    if form.status == Status.DRAFT:
        return "borrador"
    if form.status == Status.CLOSED:
        return "cerrado"
    today = today or church_today(form.church)
    if today < form.opens_on:
        return "programado"
    return "abierto" if today <= form.closes_on else "terminado"


PHASE_LABELS = {
    "borrador": ("Borrador", "pendiente"), "programado": ("Todavía no abre", "pendiente"), "abierto": ("Abierto", "en_curso"),
    "terminado": ("Ya terminó", "completada"), "cerrado": ("Cerrado", "descartada"),
}


def delete_after(form: Form) -> date:
    """The day an answer given to ``form`` now is deleted at the latest: what the person reads before answering.
    Each answer keeps its own (``Submission.delete_after``), which later changes of the form never push back."""
    return form.last_day + timedelta(days=form.retention_days)


def kept_until(submission: Submission) -> date:
    return submission.delete_after or delete_after(submission.form)


def shorten_kept_until(form: Form) -> int:
    """After the form's last day or retention changed: every answer whose own day is later than what the form
    says now is brought to it. Never the other way: a longer form or retention counts for new answers only."""
    limit = delete_after(form)
    return form.submissions.filter(Q(delete_after__gt=limit) | Q(delete_after__isnull=True)).update(delete_after=limit)


# ---- the form -----------------------------------------------------------------------------------
def _check_form(*, church, title, purpose, audience, retention_days, opens_on, closes_on, creates_task, task_owner, task_due_days, form=None):
    title = clean_line(title, Form.TITLE_MAX, "el título", code="title")
    purpose = clean_line(purpose, Form.PURPOSE_MAX, "para qué lo pides", code="purpose")
    if audience not in Form.Audience.values:
        raise FormError("Elige quién responde.", code="audience")
    if retention_days not in Form.RETENTION_DAYS:
        raise FormError("Elige cuánto se guardan las respuestas.", code="retention_days")
    if opens_on is None or closes_on is None:
        raise FormError("Escribe el día en que abre y el día en que cierra.", code="closes_on")
    if closes_on < opens_on:
        raise FormError("El día en que cierra no puede ser antes del día en que abre.", code="closes_on")
    if (closes_on - opens_on).days > MAX_OPEN_DAYS:
        raise FormError("Un formulario puede estar abierto hasta un año. Elige un día de cierre más cercano.", code="closes_on")
    if not 1 <= int(task_due_days) <= MAX_TASK_DAYS:
        raise FormError(f"Escribe entre 1 y {MAX_TASK_DAYS} días.", code="task_due_days")
    return title, purpose


def check_owner(form: Form, owner) -> None:
    """Who a task goes to must be somebody who reads the form's answers, with a person in the church."""
    if owner is None:
        return
    is_person = Person.objects.filter(church=form.church, user=owner, merged_into__isnull=True).exists()
    if not is_person or not access.can_read(owner, form):
        raise FormError("Esa persona no lee las respuestas de este formulario. Elige a alguien de la administración o de sus lectores.", code="task_owner")


def owner_choices(form: Form) -> list[Person]:
    """The people a task of ``form`` can go to: those with an account who read its answers now."""
    people = Person.objects.filter(church=form.church, merged_into__isnull=True, user__isnull=False, user__is_active=True)
    return [person for person in people.select_related("user").order_by("first_name", "last_name", "id") if access.can_read(person.user, form)]


def create_form(church, actor, *, title, purpose, audience, retention_days, opens_on, closes_on, creates_task=False,
                task_owner=None, task_due_days=7) -> Form:
    title, purpose = _check_form(
        church=church, title=title, purpose=purpose, audience=audience, retention_days=retention_days, opens_on=opens_on,
        closes_on=closes_on, creates_task=creates_task, task_owner=task_owner, task_due_days=task_due_days,
    )
    with transaction.atomic():
        form = Form.objects.create(
            church=church, title=title, purpose=purpose, audience=audience, retention_days=retention_days, opens_on=opens_on,
            closes_on=closes_on, creates_task=creates_task, task_due_days=task_due_days, created_by=actor,
        )
        if task_owner is not None:
            check_owner(form, task_owner)
            form.task_owner = task_owner
            form.save(update_fields=["task_owner", "updated_at"])
        _audit(form, actor, "formulario_creado", "Creó un formulario")
    return form


def update_form(form: Form, actor, *, title, purpose, audience, retention_days, opens_on, closes_on, creates_task,
                task_owner, task_due_days) -> Form:
    """The title and the purpose change the draft: people read the published ones until the form is
    published again. The days, the retention and the task rule hold from this save."""
    with transaction.atomic():
        form = _locked(form)
        if form.status == Status.CLOSED:
            raise FormError(CLOSED, code="closed")
        title, purpose = _check_form(
            church=form.church, title=title, purpose=purpose, audience=audience, retention_days=retention_days,
            opens_on=opens_on, closes_on=closes_on, creates_task=creates_task, task_owner=task_owner,
            task_due_days=task_due_days, form=form,
        )
        if form.current_version_id is not None:
            # People already read "we keep your answers N days" and who it was for: neither grows afterwards.
            if audience != form.audience:
                raise FormError("Un formulario que ya se publicó no cambia de quién lo responde. Crea otro formulario.", code="audience")
            if retention_days > form.retention_days:
                raise FormError(
                    "Ya hay personas a quienes se les dijo cuánto se guardan sus respuestas: ese tiempo se puede acortar, no alargar.",
                    code="retention_days",
                )
        if audience != Form.Audience.PUBLIC and form.questions.filter(kind=Kind.CONTACT).exists():
            raise FormError("Este formulario tiene una pregunta de datos de contacto, que solo va en formularios públicos. Quítala primero.", code="audience")
        check_owner(form, task_owner)
        form.title, form.purpose, form.audience, form.retention_days = title, purpose, audience, retention_days
        form.opens_on, form.closes_on = opens_on, closes_on
        form.creates_task, form.task_owner, form.task_due_days = creates_task, task_owner, task_due_days
        form.save(update_fields=["title", "purpose", "audience", "retention_days", "opens_on", "closes_on", "creates_task",
                                 "task_owner", "task_due_days", "updated_at"])
        shorten_kept_until(form)
        _audit(form, actor, "formulario_modificado", "Modificó un formulario")
    return form


def close_form(form: Form, actor) -> Form:
    """Stop taking answers today, for good. The retention is counted from today."""
    with transaction.atomic():
        form = _locked(form)
        if form.status == Status.CLOSED:
            return form
        if form.status == Status.DRAFT:
            raise FormError("Este formulario no se ha publicado: no hay nada que cerrar.", code="draft")
        form.status = Status.CLOSED
        form.closed_on = min(church_today(form.church), form.closes_on)
        form.save(update_fields=["status", "closed_on", "updated_at"])
        shorten_kept_until(form)
        _audit(form, actor, "formulario_cerrado", "Cerró un formulario")
    return form


# ---- readers ------------------------------------------------------------------------------------
def scope_choices(church) -> list[tuple[str, str]]:
    """The active groups and teams of ``church``, as ("group:3", "Quien guía Jóvenes")."""
    from groups.models import Group
    from serving.models import Team

    groups = Group.objects.filter(church=church, status=Group.Status.ACTIVE).order_by("name", "id")
    teams = Team.objects.filter(church=church, status=Team.Status.ACTIVE).order_by("name", "id")
    return [(f"group:{g.pk}", f"Quien guía el grupo {g.name}") for g in groups] + [
        (f"ministry:{t.pk}", f"Quien coordina el equipo {t.name}") for t in teams
    ]


def readers_of(form: Form) -> list[dict]:
    """The reader rows of ``form`` with the words for each; one whose group or team is gone or
    archived says so (it gives nothing meanwhile: ``access.can_read``)."""
    labels = dict(scope_choices(form.church))
    return [
        {"row": row, "label": labels.get(f"{row.scope_type}:{row.scope_id}", ""), "active": f"{row.scope_type}:{row.scope_id}" in labels}
        for row in form.readers.order_by("scope_type", "scope_id")
    ]


def add_reader(form: Form, actor, scope: str) -> FormReader:
    if scope not in dict(scope_choices(form.church)):
        raise FormError("Elige un grupo o un equipo activo de tu iglesia.", code="scope")
    scope_type, scope_id = scope.split(":")
    with transaction.atomic():
        form = _locked(form)
        reader, created = FormReader.objects.get_or_create(form=form, scope_type=scope_type, scope_id=int(scope_id), defaults={"added_by": actor})
        if created:
            _audit(form, actor, "formulario_lectores", "Agregó un lector a un formulario")
    return reader


def remove_reader(form: Form, actor, reader_id: int) -> bool:
    """Take a reader away. Tasks of the form that were theirs and that they can no longer read are
    left without an owner, so they wait for the administration instead of for nobody."""
    with transaction.atomic():
        form = _locked(form)
        deleted, _ = FormReader.objects.filter(form=form, pk=reader_id).delete()
        if not deleted:
            return False
        release_unreadable_tasks(form)
        _audit(form, actor, "formulario_lectores", "Quitó un lector de un formulario")
    return True


def release_unreadable_tasks(form: Form) -> int:
    released = 0
    owners = {task.owner for task in FormTask.objects.filter(form=form, status=FormTask.Status.OPEN, owner__isnull=False).select_related("owner")}
    for owner in owners:
        if not access.can_read(owner, form):
            released += FormTask.objects.filter(form=form, status=FormTask.Status.OPEN, owner=owner).update(owner=None)
    if form.task_owner_id and not access.can_read(form.task_owner, form):
        form.task_owner = None
        form.save(update_fields=["task_owner", "updated_at"])
    return released


# ---- questions (the draft) ----------------------------------------------------------------------
def default_consent(church) -> str:
    return DEFAULT_CONSENT.format(church=church.name)[: Question.CONSENT_MAX]


def _clean_options(options) -> list[str]:
    cleaned = []
    for option in options or []:
        option = clean_line(option, OPTION_MAX, "cada opción", code="options", required=False)
        if option and option not in cleaned:
            cleaned.append(option)
    if len(cleaned) < 2:
        raise FormError("Escribe al menos dos opciones distintas, una por renglón.", code="options")
    if len(cleaned) > MAX_OPTIONS:
        raise FormError(f"Una pregunta puede tener hasta {MAX_OPTIONS} opciones.", code="options")
    return cleaned


def values_of(question: Question) -> list[str]:
    """What an answer to ``question`` can be, when a condition can hang from it."""
    if question.kind == Kind.YES_NO:
        return list(YES_NO)
    return list(question.options) if question.kind == Kind.CHOICE else []


def condition_parents(form: Form, question: Question | None = None) -> list[Question]:
    """The questions a condition of ``question`` may hang from: one option or yes/no, before it,
    with no condition of their own (one level)."""
    rows = [q for q in form.questions.all() if q.kind in CONDITION_KINDS and q.show_if_question_id is None]
    if question is not None and question.pk:
        rows = [q for q in rows if (q.position, q.pk) < (question.position, question.pk)]
    return rows


def _repair_conditions(form: Form) -> int:
    """Drop the conditions that no longer hold (their question was moved after, changed kind or lost
    the option). Returns how many were dropped, so the screen can say it."""
    questions = list(form.questions.all())
    by_id = {q.pk: q for q in questions}
    dropped = 0
    for question in questions:
        if question.show_if_question_id is None:
            continue
        parent = by_id.get(question.show_if_question_id)
        holds = (
            parent is not None and parent.kind in CONDITION_KINDS and parent.show_if_question_id is None
            and (parent.position, parent.pk) < (question.position, question.pk) and question.show_if_value in values_of(parent)
            and question.kind != Kind.CONTACT
        )
        if not holds:
            question.show_if_question, question.show_if_value = None, ""
            question.save(update_fields=["show_if_question", "show_if_value"])
            dropped += 1
    return dropped


def save_question(form: Form, actor, *, question: Question | None = None, kind: str = "", label, required=False, options=None,
                  show_if_question_id=None, show_if_value="", ask_email=False, ask_phone=False, consent="",
                  acknowledge_long_text=False) -> tuple[Question, int]:
    """Add a question to the draft or change one. Returns it and how many conditions of other
    questions stopped holding and were dropped."""
    with transaction.atomic():
        form = _locked(form)
        if form.status == Status.CLOSED:
            raise FormError(CLOSED, code="closed")
        if question is None:
            if kind not in Kind.values:
                raise FormError("Elige un tipo de pregunta.", code="kind")
            if form.questions.count() >= MAX_QUESTIONS:
                raise FormError(f"Un formulario puede tener hasta {MAX_QUESTIONS} preguntas.", code="label")
            last = form.questions.order_by("-position").values_list("position", flat=True).first()
            question = Question(form=form, kind=kind, position=(last or 0) + 1)
        else:
            question = Question.objects.select_for_update().get(pk=question.pk, form=form)
        kind = question.kind
        question.label = clean_line(label, Question.LABEL_MAX, "la pregunta", code="label")
        question.required = bool(required)
        question.options = _clean_options(options) if kind in (Kind.CHOICE, Kind.MULTI) else []
        question.ask_email = question.ask_phone = False
        question.consent = ""
        if kind == Kind.CONTACT:
            if not form.is_public:
                raise FormError("Los datos de contacto solo se piden en un formulario público: en uno de la iglesia ya sabes quién responde.", code="kind")
            if form.questions.filter(kind=Kind.CONTACT).exclude(pk=question.pk).exists():
                raise FormError("Este formulario ya pide los datos de contacto una vez.", code="kind")
            if not (ask_email or ask_phone):
                raise FormError("Elige si pides el correo, el teléfono o los dos.", code="ask_email")
            question.ask_email, question.ask_phone = bool(ask_email), bool(ask_phone)
            question.consent = clean_line(consent, Question.CONSENT_MAX, "la frase que la persona acepta", code="consent")
        if kind == Kind.LONG and form.is_public and form.long_text_ack_at is None:
            if not acknowledge_long_text:
                raise FormError(
                    "En un formulario público, una respuesta larga es donde la gente escribe cosas delicadas. "
                    "Marca la casilla para confirmar que lo sabes, o usa una pregunta de texto corto.", code="acknowledge_long_text",
                )
            form.long_text_ack_by, form.long_text_ack_at = actor, timezone.now()
            form.save(update_fields=["long_text_ack_by", "long_text_ack_at", "updated_at"])
        question.show_if_question, question.show_if_value = None, ""
        if show_if_question_id:
            if kind == Kind.CONTACT:
                raise FormError("Los datos de contacto no pueden depender de otra respuesta.", code="show_if_question")
            parent = next((q for q in condition_parents(form, question) if q.pk == int(show_if_question_id)), None)
            if parent is None:
                raise FormError("Esa pregunta no puede decidir si se muestra esta. Elige una anterior, de una opción o de sí o no.", code="show_if_question")
            if question.pk and question.dependents.exists():
                raise FormError("Otra pregunta depende de esta, así que esta no puede depender de otra.", code="show_if_question")
            if show_if_value not in values_of(parent):
                raise FormError("Elige con qué respuesta se muestra, tal como está escrita en esa pregunta.", code="show_if_value")
            question.show_if_question, question.show_if_value = parent, show_if_value
        question.save()
        dropped = _repair_conditions(form)
        _audit(form, actor, "formulario_modificado", "Modificó las preguntas de un formulario")
    return question, dropped


def delete_question(form: Form, actor, question_id: int) -> int:
    with transaction.atomic():
        form = _locked(form)
        if form.status == Status.CLOSED:
            raise FormError(CLOSED, code="closed")
        if not Question.objects.filter(form=form, pk=question_id).delete()[0]:
            return 0
        dropped = _repair_conditions(form)
        _audit(form, actor, "formulario_modificado", "Modificó las preguntas de un formulario")
    return dropped


def move_question(form: Form, actor, question_id: int, up: bool, at: int | None = None) -> int:
    """One place up or down. ``at`` is the place (from 1) the question had on the page the person pressed on:
    when it is no longer there (the same press sent twice, somebody else moved it), nothing is moved and the
    page simply shows the order as it is, so a repeated send never moves a question twice."""
    with transaction.atomic():
        form = _locked(form)
        if form.status == Status.CLOSED:
            raise FormError(CLOSED, code="closed")
        questions = list(form.questions.select_for_update())
        index = next((i for i, q in enumerate(questions) if q.pk == question_id), None)
        other = None if index is None else index - 1 if up else index + 1
        if other is None or not 0 <= other < len(questions):
            return 0
        if at is not None and index + 1 != at:
            return 0
        questions[index], questions[other] = questions[other], questions[index]
        for position, question in enumerate(questions, start=1):
            if question.position != position:
                question.position = position
                question.save(update_fields=["position"])
        dropped = _repair_conditions(form)
        _audit(form, actor, "formulario_modificado", "Modificó las preguntas de un formulario")
    return dropped


# ---- versions -----------------------------------------------------------------------------------
def draft_schema(form: Form) -> list[dict]:
    """The draft's questions as a version stores them."""
    schema = []
    for question in form.questions.all():
        item = {"key": question.key, "kind": question.kind, "label": question.label, "required": question.required}
        if question.kind in (Kind.CHOICE, Kind.MULTI):
            item["options"] = list(question.options)
        if question.kind == Kind.CONTACT:
            item.update(ask_email=question.ask_email, ask_phone=question.ask_phone, consent=question.consent)
        if question.show_if_question_id:
            item["show_if"] = {"key": f"q{question.show_if_question_id}", "value": question.show_if_value}
        schema.append(item)
    return schema


def _published_words(form: Form):
    version = form.current_version
    return None if version is None else (version.title, version.purpose, version.schema)


def has_unpublished_changes(form: Form) -> bool:
    return _published_words(form) != (form.title, form.purpose, draft_schema(form))


def preview_token(form: Form) -> str:
    """Everything the publishing preview states: who answers, the days, the title, the purpose, the
    questions, how long answers are kept and whether each one leaves a task."""
    stated = [form.pk, form.audience, form.title, form.purpose, draft_schema(form), form.opens_on.isoformat(),
              form.closes_on.isoformat(), form.retention_days, form.creates_task, form.status]
    return hashlib.sha256(json.dumps(stated, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def publish_problems(form: Form) -> list[str]:
    """Why the draft cannot be published as it is, as sentences."""
    problems = []
    questions = list(form.questions.all())
    if form.status == Status.CLOSED:
        problems.append(CLOSED)
    if not questions:
        problems.append("Agrega al menos una pregunta.")
    if len(questions) > MAX_QUESTIONS:
        problems.append(f"Un formulario puede tener hasta {MAX_QUESTIONS} preguntas.")
    if form.is_public and form.long_text_ack_at is None and any(q.kind == Kind.LONG for q in questions):
        problems.append("Este formulario público tiene una pregunta de texto largo y nadie ha confirmado que sabe el riesgo. Abre esa pregunta y márcalo, o cámbiala por texto corto.")
    if not form.is_public and any(q.kind == Kind.CONTACT for q in questions):
        problems.append("Los datos de contacto solo se piden en un formulario público. Quita esa pregunta.")
    if form.closes_on < church_today(form.church) and form.status != Status.CLOSED:
        problems.append("El día de cierre ya pasó. Cámbialo antes de publicar.")
    return problems


def publish(form: Form, actor, token: str) -> tuple[Form, bool]:
    """Publish what the administrator read. Returns the form and whether a new version was written
    (publishing the same questions and words again writes none)."""
    with transaction.atomic():
        form = _locked(form)
        if not hmac.compare_digest(preview_token(form), token or ""):
            raise StalePreview
        problems = publish_problems(form)
        if problems:
            raise FormError(problems[0], code="publish")
        schema = draft_schema(form)
        new = _published_words(form) != (form.title, form.purpose, schema)
        if new:
            last = form.versions.order_by("-number").values_list("number", flat=True).first() or 0
            form.current_version = FormVersion.objects.create(
                form=form, number=last + 1, title=form.title, purpose=form.purpose, schema=schema, published_by=actor
            )
        was_draft = form.status == Status.DRAFT
        form.status = Status.PUBLISHED
        form.save(update_fields=["current_version", "status", "updated_at"])
        if new or was_draft:
            _audit(form, actor, "formulario_publicado", "Publicó un formulario o una versión nueva de sus preguntas",
                   value=form.current_version.number)
    return form, new


# ---- answers ------------------------------------------------------------------------------------
def _same_contact(form: Form, *, email_key: str, phone_key: str, now):
    """The earlier answers to ``form`` within a day that gave this e-mail or this phone."""
    same = Q()
    if email_key:
        same |= Q(email_key=email_key)
    if phone_key:
        same |= Q(phone_key=phone_key)
    if not same:
        return Submission.objects.none()
    return Submission.objects.filter(same, form=form, created_at__gt=now - DUPLICATE_WINDOW, created_at__lte=now)


def _leave_task(form: Form, submission: Submission, actor, now) -> FormTask | None:
    """The task of one answer, when the form asks for one and has room for it."""
    if not form.creates_task or form.tasks.filter(status=FormTask.Status.OPEN).count() >= MAX_OPEN_TASKS:
        return None
    owner = form.task_owner if form.task_owner_id and access.can_read(form.task_owner, form) else None
    task = FormTask.objects.create(
        submission=submission, form=form, owner=owner, due_on=church_today(form.church, now) + timedelta(days=form.task_due_days),
    )
    _audit(form, actor, "formulario_tarea", "Una respuesta a un formulario dejó un pendiente", task)
    return task


def submit(form: Form, version_id: int, *, answers: dict, contact: dict | None = None, person: Person | None = None,
           actor=None, now=None, shown_until: date | None = None) -> tuple[Submission, bool]:
    """Keep one answer to the version that is published right now, and its task if the form says so.

    Returns ``(submission, created)``. Raises ``FormChanged`` when the form is not open any more or its
    published version is another one, so the caller shows the form again; ``FormFull`` when nothing more fits.

    * A person of the church (an account: nobody else can send as them) answering the same version again within
      a day changes nothing: the first answer is returned, nothing is replaced and no second task is made.
    * A public answer is never discarded for repeating a contact, because anybody can type somebody else's
      e-mail: both are kept, the later one is marked ``same_contact``, and that contact leaves at most one task
      per form per day (the idempotency is the task's).
    * A public answer that arrives while the form is receiving more than ``FLOW_LIMIT`` in ``FLOW_WINDOW``, or
      when the form already lists ``MAX_SUBMISSIONS``, is kept "por revisar" (``held``): no task, no line in the
      activity log, out of the lists and the export until the administration accepts it.
    * ``delete_after`` is what the person was told: the form's day now, or ``shown_until`` (the day printed on
      the page they sent) when that is earlier.

    ``answers`` and ``contact`` come from ``formularios.fields.AnswerForm``, which validated them against the
    version's questions. The form's row is locked: two answers at once are written one after the other."""
    contact = contact or {}
    now = now or timezone.now()
    with transaction.atomic():
        form = _locked(form)
        version = form.current_version
        today = church_today(form.church, now)
        if not is_open(form, today) or version is None or version.pk != version_id:
            raise FormChanged
        if person is not None and (person.church_id != form.church_id or person.merged_into_id is not None or form.is_public):
            raise FormChanged
        if person is None and not form.is_public:
            raise FormChanged
        if person is not None and is_known_minor(person):
            raise FormChanged
        until = delete_after(form)
        if shown_until is not None and today <= shown_until < until:
            until = shown_until
        email_key = contact_key(form.pk, "email", contact.get("email", ""))
        phone_key = contact_key(form.pk, "phone", contact.get("phone", ""))
        held = same_contact = task_exists = False
        if person is not None:
            existing = Submission.objects.filter(
                form=form, version=version, person=person, created_at__gt=now - DUPLICATE_WINDOW
            ).order_by("id").first()
            if existing is not None:
                return existing, False
            if form.submissions.filter(held=False).count() >= MAX_SUBMISSIONS:
                raise FormFull
        else:
            earlier = _same_contact(form, email_key=email_key, phone_key=phone_key, now=now)
            same_contact = earlier.exists()
            task_exists = same_contact and FormTask.objects.filter(submission__in=earlier).exists()
            listed = form.submissions.filter(held=False)
            held = listed.count() >= MAX_SUBMISSIONS or listed.filter(created_at__gt=now - FLOW_WINDOW).count() >= FLOW_LIMIT
            if held and form.submissions.filter(held=True).count() >= MAX_HELD:
                raise FormFull
        submission = Submission.objects.create(
            form=form, version=version, person=person, answers=answers, purpose_shown=version.purpose,
            contact_first_name=contact.get("first_name", ""), contact_last_name=contact.get("last_name", ""),
            contact_email=contact.get("email", ""), contact_phone=contact.get("phone", ""),
            consent_shown=contact.get("consent", ""), email_key=email_key, phone_key=phone_key,
            delete_after=until, same_contact=same_contact, held=held,
        )
        if not held:
            _audit(form, actor, "formulario_respuesta", "Llegó una respuesta a un formulario", submission)
            if not task_exists:
                _leave_task(form, submission, actor, now)
    return submission, True


def is_known_minor(person: Person) -> bool:
    """Whether the church's records say ``person`` is under 18: the one rule, ``people.minors.known_minor``.
    Such a person neither sees nor answers the forms of the church in this slice."""
    from people.minors import known_minor

    return known_minor(person)


def answered_recently(form: Form, person: Person, now=None) -> bool:
    """Whether ``person`` already answered the published version within the day (for the screen)."""
    now = now or timezone.now()
    return form.current_version_id is not None and Submission.objects.filter(
        form=form, version_id=form.current_version_id, person=person, created_at__gt=now - DUPLICATE_WINDOW
    ).exists()


def same_contact_answers(submission: Submission):
    """The other answers to the same form that gave the same e-mail or phone."""
    same = Q()
    if submission.email_key:
        same |= Q(email_key=submission.email_key)
    if submission.phone_key:
        same |= Q(phone_key=submission.phone_key)
    if not same:
        return Submission.objects.none()
    return Submission.objects.filter(same, form_id=submission.form_id).exclude(pk=submission.pk)


# ---- the administration deletes or accepts answers ----------------------------------------------
def delete_submission(submission: Submission, actor) -> None:
    """One answer, and its task with it. The log keeps that it happened and in which form, nothing of the answer."""
    with transaction.atomic():
        form = _locked(submission.form_id)
        if Submission.objects.filter(pk=submission.pk, form=form).delete()[0]:
            _audit(form, actor, "formulario_respuestas_borradas_a_mano", "Borró una respuesta de un formulario", value=1)


def delete_held(form: Form, actor) -> int:
    """Every answer "por revisar" of ``form`` at once: one row in the log, with how many."""
    with transaction.atomic():
        form = _locked(form)
        total = form.submissions.filter(held=True).count()
        if total:
            form.submissions.filter(held=True).delete()
            _audit(form, actor, "formulario_respuestas_borradas_a_mano", "Borró las respuestas por revisar de un formulario", value=total)
    return total


def accept_held(submission: Submission, actor) -> Submission:
    """An answer "por revisar" is a person's after all: it is listed, logged and leaves its task like any other."""
    with transaction.atomic():
        form = _locked(submission.form_id)
        submission = Submission.objects.select_for_update(of=("self",)).get(pk=submission.pk, form=form)
        if not submission.held:
            return submission
        submission.held = False
        submission.save(update_fields=["held"])
        _audit(form, actor, "formulario_respuesta", "Aceptó una respuesta por revisar de un formulario", submission)
        around = _same_contact(form, email_key=submission.email_key, phone_key=submission.phone_key,
                               now=submission.created_at + DUPLICATE_WINDOW).exclude(pk=submission.pk)
        if not FormTask.objects.filter(submission__in=around).exists():
            _leave_task(form, submission, actor, timezone.now())
    return submission


def described(submission: Submission) -> list[tuple[str, str, str]]:
    """The answers of one submission as (key, question, answer), in the order of the version it
    answered. A question the person did not see or left empty is not listed."""
    rows = []
    for item in submission.version.schema:
        value = submission.answers.get(item["key"])
        if item["kind"] == Kind.CONTACT or value in (None, "", []):
            continue
        if isinstance(value, list):
            value = ", ".join(str(one) for one in value)
        elif item["kind"] == Kind.DATE:
            try:
                value = date.fromisoformat(value).strftime("%d/%m/%Y")
            except (TypeError, ValueError):
                value = str(value)
        rows.append((item["key"], item["label"], str(value)))
    return rows


def describe(submission: Submission) -> list[tuple[str, str]]:
    return [(label, value) for _key, label, value in described(submission)]


# ---- tasks --------------------------------------------------------------------------------------
def _locked_task(task: FormTask) -> FormTask:
    return FormTask.objects.select_for_update(of=("self",)).select_related("form", "form__church", "owner").get(pk=task.pk)


def set_task_status(task: FormTask, actor, status: str) -> FormTask:
    if status not in FormTask.Status.values:
        raise FormError("Un pendiente está pendiente o hecho.", code="status")
    with transaction.atomic():
        task = _locked_task(task)
        if task.status == status:
            return task
        task.status = status
        done = status == FormTask.Status.DONE
        task.closed_at, task.closed_by = (timezone.now(), actor) if done else (None, None)
        task.save(update_fields=["status", "closed_at", "closed_by", "updated_at"])
        _audit(task.form, actor, "formulario_tarea",
               "Marcó como hecho un pendiente de un formulario" if done else "Volvió a abrir un pendiente de un formulario", task)
    return task


def set_task_note(task: FormTask, actor, note: str) -> FormTask:
    """A short line of the staff's own. Never the answers: the screen says it and nothing fills it."""
    note = clean_line(note, FormTask.NOTE_MAX, "la nota", code="note", required=False)
    with transaction.atomic():
        task = _locked_task(task)
        if task.note == note:
            return task
        task.note = note
        task.save(update_fields=["note", "updated_at"])
        _audit(task.form, actor, "formulario_tarea", "Escribió o cambió la nota de un pendiente de un formulario", task)
    return task


def assign_task(task: FormTask, actor, owner, due_on) -> FormTask:
    """Who does it and by when (the administration's). ``owner`` is an account or None."""
    with transaction.atomic():
        task = _locked_task(task)
        if task.owner_id == (owner.pk if owner else None) and task.due_on == due_on:
            return task
        if not task.is_open:
            raise FormError("Este pendiente ya está hecho. Ábrelo otra vez para cambiarlo.", code="closed")
        check_owner(task.form, owner)
        if due_on and due_on != task.due_on and due_on < church_today(task.form.church):
            raise FormError("Esa fecha ya pasó. Elige un día de hoy en adelante, o déjala vacía.", code="due_on")
        task.owner, task.due_on = owner, due_on
        task.save(update_fields=["owner", "due_on", "updated_at"])
        _audit(task.form, actor, "formulario_tarea", "Asignó un pendiente de un formulario o cambió su fecha", task)
    return task


# ---- "Agregar a personas" -----------------------------------------------------------------------
def _answer_keys(submission: Submission) -> set[tuple[str, str]]:
    """The signals of ``people.duplicates`` an answer can carry: its e-mail and its phone (an answer has no date
    of birth, so the third one, name with date of birth, cannot match)."""
    return duplicates.keys_of({"email": submission.contact_email, "phone": submission.contact_phone,
                               "normalized_name": "", "birth_date": None})


def similar_people(submission: Submission) -> list[Person]:
    """Every person of the church who shares a signal of ``people.duplicates.keys_of`` with the contact of this
    answer. Each signal is marked on the person (``same_email``, ``same_phone``). One query over the values
    that matter, with no limit: a phone is compared after normalising, which the database cannot do."""
    wanted = _answer_keys(submission)
    if not wanted:
        return []
    rows = Person.objects.filter(church_id=submission.form.church_id, merged_into__isnull=True).exclude(email="", phone="")
    hits: dict[int, set[str]] = {}
    for row in rows.values("pk", "email", "phone").iterator():
        shared = wanted & duplicates.keys_of({**row, "normalized_name": "", "birth_date": None})
        if shared:
            hits[row["pk"]] = {signal for signal, _ in shared}
    people = list(Person.objects.filter(pk__in=hits).order_by("first_name", "last_name", "id"))
    for person in people:
        person.same_email = duplicates.EMAIL in hits[person.pk]
        person.same_phone = duplicates.PHONE in hits[person.pk]
    return people


def add_to_people(submission: Submission, actor, *, existing: Person | None = None, another: bool = False) -> Person:
    """The contact of a public answer becomes a ``Person`` (through ``people.services.save_person``,
    audited like any other), or is recognized as ``existing``. Never automatic: an administrator
    asked for it on the screen.

    While somebody of the church shares the e-mail or the phone (``similar_people``) no new record is made: the
    administrator picks that person. ``another`` is their explicit "es otra persona", written in the log; it is
    possible only over a shared phone (families share one). A shared e-mail is never overridden: one e-mail is
    one person in a church (the rule of ``people.forms.PersonForm``)."""
    with transaction.atomic():
        form = _locked(submission.form_id)
        submission = Submission.objects.select_for_update(of=("self",)).select_related("form").get(pk=submission.pk, form=form)
        if not flags.enabled(form.church, "people"):
            raise FormError("Tu iglesia no usa Personas en Emmaus.", code="no_people")
        if submission.person_id is not None:
            raise FormError("Esta respuesta ya es de una persona de la iglesia.", code="already")
        if not form.is_public or not submission.contact_first_name or submission.held:
            raise FormError("Esta respuesta no trae datos de contacto.", code="no_contact")
        override = False
        if existing is not None:
            existing = Person.objects.select_for_update().filter(pk=existing.pk, church=form.church, merged_into__isnull=True).first()
            if existing is None:
                raise FormError("Esa persona no es de esta iglesia.", code="other_church")
            person = existing
        else:
            similar = similar_people(submission)
            if any(one.same_email for one in similar):
                raise FormError("Otra persona de la iglesia ya tiene este correo. Si es la misma, elígela en la lista.", code="duplicate")
            if similar and not another:
                raise FormError("Alguien de la iglesia ya tiene este teléfono. Elígela en la lista, o di que es otra persona.", code="duplicate")
            override = bool(similar)
            email = duplicates.normal_email(submission.contact_email)
            person = people_services.save_person(
                Person(
                    church=form.church, first_name=submission.contact_first_name, last_name=submission.contact_last_name,
                    email=email, phone=submission.contact_phone, created_from=Person.CreatedFrom.MANUAL,
                    preferred_contact_method=Person.ContactMethod.EMAIL if email else Person.ContactMethod.PHONE,
                ),
                actor, created=True,
            )
        submission.person = person
        submission.save(update_fields=["person"])
        extra = {"override": "mismo_telefono"} if override else {}
        _audit(form, actor, "formulario_persona_agregada",
               "Agregó a personas de la iglesia a quien respondió un formulario, aunque alguien más tiene ese teléfono" if override
               else "Agregó a personas de la iglesia a quien respondió un formulario", submission, person_id=person.pk, **extra)
    return person


# ---- export -------------------------------------------------------------------------------------
def export_rows(form: Form) -> tuple[list[str], list[list[str]]]:
    """(header, rows) of the answers of ONE form, every cell through ``safe_cell``: when, the version,
    who (the person, or the contact of a public form), and one column per question of every version,
    in the order they first appeared and named as their latest version names them."""
    versions = list(form.versions.order_by("number"))
    columns: dict[str, str] = {}
    for version in versions:
        for item in version.schema:
            if item["kind"] != Kind.CONTACT:
                columns[item["key"]] = item["label"]
    who = ["Nombre", "Apellidos", "Correo", "Teléfono"] if form.is_public else ["Persona"]
    header = ["Recibida", "Versión", *who, *columns.values()]
    tz = church_tz(form.church)
    rows = []
    for submission in form.submissions.filter(held=False).select_related("version", "person").order_by("created_at", "id"):
        by_key = {key: value for key, _label, value in described(submission)}
        if form.is_public:
            person = [submission.contact_first_name, submission.contact_last_name, submission.contact_email, submission.contact_phone]
        else:
            person = [submission.person.full_name if submission.person else ""]
        rows.append([
            submission.created_at.astimezone(tz).strftime("%Y-%m-%d %H:%M"), str(submission.version.number), *person,
            *(by_key.get(key, "") for key in columns),
        ])
    return [safe_cell(cell) for cell in header], [[safe_cell(cell) for cell in row] for row in rows]


def export_csv(form: Form, actor) -> str:
    """The CSV as text, and one audit row that says how many answers left Emmaus."""
    header, rows = export_rows(form)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows(rows)
    _audit(form, actor, "formulario_exportado", "Descargó las respuestas de un formulario", value=len(rows))
    return out.getvalue()


# ---- retention ----------------------------------------------------------------------------------
def prune(apply: bool = False, now=None) -> dict:
    """Delete every answer whose own last day came (``Submission.delete_after``: what its person was told, or
    earlier), and with it its task, in every church whatever its module flags (it only deletes). The form, its
    questions and its versions stay. Also forgets the seals of public pages that could no longer be accepted.
    Returns counts only; ``submissions`` is what the scheduler prints."""
    now = now or timezone.now()
    forms = submissions = 0
    for form in Form.objects.filter(submissions__isnull=False).distinct().select_related("church"):
        today = church_today(form.church, now)
        with transaction.atomic():
            form = _locked(form)
            due = Q(delete_after__lte=today)
            if delete_after(form) <= today:
                due |= Q(delete_after__isnull=True)  # written before each answer kept its own day: the form's
            rows = form.submissions.filter(due)
            total = rows.count()
            if not total:
                continue
            if apply:
                rows.delete()
                _audit(form, None, "formulario_respuestas_borradas", "Se borraron las respuestas de un formulario al terminar el tiempo que se guardan", value=total)
            forms += 1
            submissions += total
    if apply:
        SeenSeal.objects.filter(expires_at__lt=now).delete()
    return {"forms": forms, "submissions": submissions}
