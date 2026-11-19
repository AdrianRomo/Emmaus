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
from .models import MAX_OPTIONS, MAX_QUESTIONS, OPTION_MAX, Form, FormReader, FormTask, FormVersion, Question, Submission

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
DUPLICATE_WINDOW = timedelta(days=1)
DEFAULT_CONSENT = "Acepto que {church} use estos datos solo para contactarme por este formulario."
CLOSED = "Este formulario ya está cerrado: ya no cambia ni recibe respuestas."
SENSITIVE_WARNING = (
    "No preguntes por salud, vida espiritual, conflictos familiares ni datos de menores de edad en un formulario general. "
    "Para eso están las conversaciones en persona y los procesos que tu iglesia ya cuida."
)
MINORS_OUT = "Los formularios generales no sirven para inscribir a niñas, niños o adolescentes a algo: eso todavía no existe en Emmaus."


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


def contact_key(version_id: int, kind: str, value: str) -> str:
    """The same contact answering the same version, as a keyed hash (as ``feedback.services.token_for``):
    enough to notice a second answer, useless to list contacts. Empty for an empty value."""
    value = duplicates.normal_email(value) if kind == "email" else duplicates.normal_phone(value)
    if not value:
        return ""
    message = f"formularios:{version_id}:{kind}:{value}".encode()
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
    """The last day the answers of ``form`` are kept."""
    return form.last_day + timedelta(days=form.retention_days)


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


def move_question(form: Form, actor, question_id: int, up: bool) -> int:
    with transaction.atomic():
        form = _locked(form)
        if form.status == Status.CLOSED:
            raise FormError(CLOSED, code="closed")
        questions = list(form.questions.select_for_update())
        index = next((i for i, q in enumerate(questions) if q.pk == question_id), None)
        other = None if index is None else index - 1 if up else index + 1
        if other is None or not 0 <= other < len(questions):
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
def _recent_duplicate(form: Form, version: FormVersion, *, person, email_key: str, phone_key: str, now):
    same = Q()
    if person is not None:
        same |= Q(person=person)
    if email_key:
        same |= Q(email_key=email_key)
    if phone_key:
        same |= Q(phone_key=phone_key)
    if not same:
        return None
    return Submission.objects.filter(same, form=form, version=version, created_at__gt=now - DUPLICATE_WINDOW).order_by("id").first()


def submit(form: Form, version_id: int, *, answers: dict, contact: dict | None = None, person: Person | None = None,
           actor=None, now=None) -> tuple[Submission, bool]:
    """Keep one answer to the version that is published right now, and its task if the form says so.

    Returns ``(submission, created)``. The same person, the same e-mail or the same phone answering
    the same version again within a day changes nothing: the first answer is returned, nothing is
    replaced and no second task is made. Raises ``FormChanged`` when the form is not open any more
    or its published version is another one, so the caller shows the form again; ``FormFull`` when
    the form keeps as many answers as one form may.

    ``answers`` and ``contact`` come from ``formularios.fields.AnswerForm``, which validated them
    against the version's questions. The form's row is locked: two answers at once are written one
    after the other, so both see each other."""
    contact = contact or {}
    now = now or timezone.now()
    with transaction.atomic():
        form = _locked(form)
        version = form.current_version
        if not is_open(form, church_today(form.church, now)) or version is None or version.pk != version_id:
            raise FormChanged
        if person is not None and (person.church_id != form.church_id or person.merged_into_id is not None or form.is_public):
            raise FormChanged
        if person is None and not form.is_public:
            raise FormChanged
        email_key = contact_key(version.pk, "email", contact.get("email", ""))
        phone_key = contact_key(version.pk, "phone", contact.get("phone", ""))
        existing = _recent_duplicate(form, version, person=person, email_key=email_key, phone_key=phone_key, now=now)
        if existing is not None:
            return existing, False
        if form.submissions.count() >= MAX_SUBMISSIONS:
            raise FormFull
        submission = Submission.objects.create(
            form=form, version=version, person=person, answers=answers, purpose_shown=version.purpose,
            contact_first_name=contact.get("first_name", ""), contact_last_name=contact.get("last_name", ""),
            contact_email=contact.get("email", ""), contact_phone=contact.get("phone", ""),
            consent_shown=contact.get("consent", ""), email_key=email_key, phone_key=phone_key,
        )
        _audit(form, actor, "formulario_respuesta", "Llegó una respuesta a un formulario", submission)
        if form.creates_task:
            owner = form.task_owner if form.task_owner_id and access.can_read(form.task_owner, form) else None
            task = FormTask.objects.create(
                submission=submission, form=form, owner=owner,
                due_on=church_today(form.church, now) + timedelta(days=form.task_due_days),
            )
            _audit(form, actor, "formulario_tarea", "Una respuesta a un formulario dejó un pendiente", task)
    return submission, True


def answered_recently(form: Form, person: Person, now=None) -> bool:
    """Whether ``person`` already answered the published version within the day (for the screen)."""
    now = now or timezone.now()
    return form.current_version_id is not None and Submission.objects.filter(
        form=form, version_id=form.current_version_id, person=person, created_at__gt=now - DUPLICATE_WINDOW
    ).exists()


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
def similar_people(submission: Submission) -> list[Person]:
    """People of the church with the same e-mail or the same phone as the contact of this answer:
    the two signals ``people.duplicates`` uses that an answer can carry. A hint for a human."""
    email, phone = duplicates.normal_email(submission.contact_email), duplicates.normal_phone(submission.contact_phone)
    if not email and not phone:
        return []
    people = Person.objects.filter(church_id=submission.form.church_id, merged_into__isnull=True)
    match = Q()
    if email:
        match |= Q(email__iexact=email)
    if phone:
        match |= ~Q(phone="")
    found = [
        person for person in people.filter(match).order_by("first_name", "last_name", "id")[:500]
        if (email and duplicates.normal_email(person.email) == email) or (phone and duplicates.normal_phone(person.phone) == phone)
    ]
    return found[:5]


def add_to_people(submission: Submission, actor, *, existing: Person | None = None) -> Person:
    """The contact of a public answer becomes a ``Person`` (through ``people.services.save_person``,
    audited like any other), or is recognized as ``existing``. Never automatic: an administrator
    asked for it on the screen. A new record is refused while somebody of the church has the same
    e-mail (the rule of ``people.forms.PersonForm``): the administrator picks that person instead."""
    with transaction.atomic():
        form = _locked(submission.form_id)
        submission = Submission.objects.select_for_update(of=("self",)).get(pk=submission.pk, form=form)
        if not flags.enabled(form.church, "people"):
            raise FormError("Tu iglesia no usa Personas en Emmaus.", code="no_people")
        if submission.person_id is not None:
            raise FormError("Esta respuesta ya es de una persona de la iglesia.", code="already")
        if not form.is_public or not submission.contact_first_name:
            raise FormError("Esta respuesta no trae datos de contacto.", code="no_contact")
        if existing is not None:
            existing = Person.objects.select_for_update().filter(pk=existing.pk, church=form.church, merged_into__isnull=True).first()
            if existing is None:
                raise FormError("Esa persona no es de esta iglesia.", code="other_church")
            person = existing
        else:
            email = duplicates.normal_email(submission.contact_email)
            if email and Person.objects.filter(church=form.church, email__iexact=email, merged_into__isnull=True).exists():
                raise FormError("Otra persona de la iglesia ya tiene este correo. Si es la misma, elígela en la lista.", code="duplicate")
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
        _audit(form, actor, "formulario_persona_agregada", "Agregó a personas de la iglesia a quien respondió un formulario",
               submission, person_id=person.pk)
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
    for submission in form.submissions.select_related("version", "person").order_by("created_at", "id"):
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
    """Delete the answers (and with them their tasks) of every form whose retention ended: more than
    ``retention_days`` days after its last day, in every church whatever its module flags (it only
    deletes). The form, its questions and its versions stay. Returns counts only."""
    now = now or timezone.now()
    forms = submissions = 0
    for form in Form.objects.exclude(status=Status.DRAFT).filter(submissions__isnull=False).distinct().select_related("church"):
        if delete_after(form) >= church_today(form.church, now):
            continue
        with transaction.atomic():
            form = _locked(form)
            if delete_after(form) >= church_today(form.church, now):
                continue  # its last day was moved while this pass ran
            total = form.submissions.count()
            if not total:
                continue
            if apply:
                form.submissions.all().delete()
                _audit(form, None, "formulario_respuestas_borradas", "Se borraron las respuestas de un formulario al terminar el tiempo que se guardan", value=total)
            forms += 1
            submissions += total
    return {"forms": forms, "submissions": submissions}
