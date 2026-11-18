"""Planes de lectura: what a person may read of the church's reading plans, and every write to one.

A plan is several days in order. Each day holds a Scripture *reference* and what the church wrote
about it. Emmaus stores no Bible text and fetches none: the person reads the passage in their own
Bible. A plan is published only with the administration's explicit confirmation that the church
wrote it or has permission to publish it; who confirmed and when stay on the plan.

Who reads what:

* a **published** plan: every person of the church;
* a **draft**: only the administration (to check it before publishing);
* a **retired** plan: only the people who had already started it (they keep every day, their marks
  and "Ya lo leí", so nobody loses a plan half-way), and the administration. Nobody can start a
  retired plan; somebody who leaves one cannot open it again.

``ReadingPlanProgress`` and ``ReadingPlanDayRead`` are private to their person, like
``DevotionalRead``: the functions here answer for *one* person and nothing else. There is no count,
list or total of readers, no audit row about reading, no e-mail and no notice, and none may be added.
Days are never tied to a date: a person reads at their own pace.

Each write of the administration is one transaction with one church audit row. An audit row says
which day by its number; it never carries a title, a reference or the church's text.

Callers have already answered who may write (``resources.plan_views``: the church's administration).
"""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Count, Max
from django.utils import timezone

from people.audit import record_church

from .models import ReadingPlan, ReadingPlanDay, ReadingPlanDayRead, ReadingPlanProgress
from .services import ResourceError, _fits, _line, _text

CONFIRMATION = "Confirmo que este plan lo escribió nuestra iglesia o que tenemos permiso para publicarlo"
NOT_CONFIRMED = "Para publicar el plan, marca la confirmación."
NO_DAYS = "Este plan todavía no tiene días. Agrega por lo menos uno antes de publicarlo."
GONE = "Ese plan ya no existe."
OPEN = (ReadingPlan.Status.PUBLISHED, ReadingPlan.Status.RETIRED)


# ---- reading ------------------------------------------------------------------------------------
def progress_of(plan: ReadingPlan, person) -> ReadingPlanProgress | None:
    """This person's own progress in the plan, if they started it."""
    if person is None or person.church_id != plan.church_id:
        return None
    return ReadingPlanProgress.objects.filter(plan=plan, person=person).first()


def may_read(plan: ReadingPlan, progress) -> bool:
    """Published: everybody of the church. Retired: only who had started it. A draft: nobody."""
    return plan.is_published or (plan.is_retired and progress is not None)


def days_for(plan: ReadingPlan, progress) -> list[ReadingPlanDay]:
    """The days in order, each with ``is_read`` for the owner of ``progress`` (False without one)."""
    read = set(progress.marks.values_list("day_id", flat=True)) if progress else set()
    days = list(plan.days.order_by("position"))
    for day in days:
        day.is_read = day.pk in read
    return days


def first_unread(days) -> ReadingPlanDay | None:
    """Where to pick the plan up again: the first day without a mark. None when every day is read."""
    return next((day for day in days if not day.is_read), None)


def plans_for(church, person) -> list[ReadingPlan]:
    """What ``person`` finds in "Planes de lectura": the plans they started (published or retired)
    first, then the other published ones. Each carries ``day_count``, ``started``, ``finished`` and
    ``next_position`` (the first unread day; None when not started or finished) for this person only."""
    mine = {row.plan_id: row for row in ReadingPlanProgress.objects.filter(person=person, plan__church=church)}
    plans = list(
        ReadingPlan.objects.filter(church=church, status__in=OPEN).annotate(day_count=Count("days")).order_by("title", "id")
    )
    plans = [plan for plan in plans if plan.is_published or plan.pk in mine]
    read: dict[int, set[int]] = {}
    marks = ReadingPlanDayRead.objects.filter(progress__in=list(mine.values()))
    for progress_id, position in marks.values_list("progress_id", "day__position"):
        read.setdefault(progress_id, set()).add(position)
    for plan in plans:
        progress = mine.get(plan.pk)
        plan.started = progress is not None
        done = read.get(progress.pk, set()) if progress else set()
        plan.next_position = next((n for n in range(1, plan.day_count + 1) if n not in done), None) if progress else None
        plan.finished = plan.started and plan.next_position is None
    return sorted(plans, key=lambda plan: (not plan.started, plan.finished))


def current_for(church, person) -> tuple[ReadingPlan, int] | None:
    """For Mi iglesia: the plan this person started most recently and has not finished, and the
    number of its first unread day. One query when the person has started nothing."""
    rows = ReadingPlanProgress.objects.filter(person=person, plan__church=church, plan__status__in=OPEN)
    for progress in rows.select_related("plan").order_by("-started_at", "-id"):
        day = first_unread(days_for(progress.plan, progress))
        if day is not None:
            return progress.plan, day.position
    return None


def start(plan: ReadingPlan, person) -> ReadingPlanProgress:
    """The person starts the plan. Idempotent: starting twice keeps the first row and its marks."""
    # The status is asked again here: the caller's object may be older than a retirement.
    published = ReadingPlan.objects.filter(pk=plan.pk, status=ReadingPlan.Status.PUBLISHED).exists()
    if person is None or person.church_id != plan.church_id or not published:
        raise ResourceError("Ese plan no está publicado.", code="unreadable")
    try:
        with transaction.atomic():
            progress, _ = ReadingPlanProgress.objects.get_or_create(plan=plan, person=person)
    except IntegrityError:  # two tabs at once: the other one wrote it
        progress = ReadingPlanProgress.objects.get(plan=plan, person=person)
    return progress


def mark_day_read(day: ReadingPlanDay, person) -> bool:
    """The person said "Ya lo leí" on a day of a plan they started. Idempotent; True the first time."""
    progress = progress_of(day.plan, person)
    if progress is None or not may_read(day.plan, progress):
        raise ResourceError("Primero empieza el plan.", code="not_started")
    try:
        with transaction.atomic():
            _, created = ReadingPlanDayRead.objects.get_or_create(progress=progress, day=day)
    except IntegrityError:
        return False
    return created


def leave(plan: ReadingPlan, person) -> bool:
    """The person leaves the plan: the progress row and every mark are deleted. True if there was one."""
    if person is None:
        return False
    deleted, _ = ReadingPlanProgress.objects.filter(plan=plan, person=person).delete()
    return bool(deleted)


# ---- writing ------------------------------------------------------------------------------------
def _locked(plan: ReadingPlan) -> ReadingPlan:
    locked = ReadingPlan.objects.select_for_update(of=("self",)).select_related("church").filter(pk=plan.pk).first()
    if locked is None:
        raise ResourceError(GONE, code="missing")
    return locked


def _state_words(plan: ReadingPlan) -> str:
    return "un borrador de plan de lectura" if plan.is_draft else "un plan de lectura " + plan.get_status_display().lower()


def save_plan(church, actor, *, title: str, description: str = "", author_line: str = "", plan: ReadingPlan | None = None) -> ReadingPlan:
    """Write a new plan (a draft) or correct the title, the description or the author line of ``plan``."""
    title, author_line, description = _line(title), _line(author_line).rstrip("."), _text(description)
    if not title:
        raise ResourceError("Escribe el título del plan.", code="title")
    _fits(title, 120, "El título puede tener hasta 120 caracteres. Acórtalo.", "title")
    _fits(author_line, 120, "Esa línea puede tener hasta 120 caracteres.", "author_line")
    _fits(
        description, ReadingPlan.DESCRIPTION_MAX,
        f"La descripción puede tener hasta {ReadingPlan.DESCRIPTION_MAX} caracteres y esta tiene {len(description)}. Acórtala.",
        "description",
    )
    with transaction.atomic():
        if plan is None:
            plan, created = ReadingPlan(church=church, created_by=actor), True
        else:
            plan, created = _locked(plan), False
            if plan.church_id != church.pk:
                raise ResourceError(GONE, code="missing")
        plan.title, plan.description, plan.author_line = title, description, author_line
        plan.save()
        if created:
            record_church(church, actor, "plan_modificado", "Empezó un borrador de plan de lectura", plan)
        else:
            record_church(church, actor, "plan_modificado", f"Modificó los datos de {_state_words(plan)}", plan)
    return plan


def save_day(
    plan: ReadingPlan, actor, *, reference: str, title: str = "", reflection: str = "", question: str = "",
    day: ReadingPlanDay | None = None,
) -> ReadingPlanDay:
    """Add a day at the end of ``plan``, or correct ``day``. Allowed on a published plan too (a typo,
    one more day): it is audited, and the people who are reading see the change at once."""
    reference, title, question, reflection = _line(reference), _line(title), _line(question), _text(reflection)
    if not reference:
        raise ResourceError("Escribe la cita del día, por ejemplo Juan 15:1-8.", code="reference")
    _fits(reference, 80, "La cita puede tener hasta 80 caracteres. Escribe solo la cita, por ejemplo Juan 15:1-8.", "reference")
    _fits(title, 120, "El título puede tener hasta 120 caracteres. Acórtalo.", "title")
    _fits(question, 300, "La pregunta puede tener hasta 300 caracteres. Acórtala.", "question")
    _fits(
        reflection, ReadingPlanDay.REFLECTION_MAX,
        f"La reflexión puede tener hasta {ReadingPlanDay.REFLECTION_MAX} caracteres y esta tiene {len(reflection)}. Acórtala.",
        "reflection",
    )
    with transaction.atomic():
        plan = _locked(plan)
        if day is None:
            last = plan.days.aggregate(last=Max("position"))["last"] or 0
            day, verb = ReadingPlanDay(plan=plan, position=last + 1), "Agregó"
        else:
            day = ReadingPlanDay.objects.filter(pk=day.pk, plan=plan).first()
            if day is None:
                raise ResourceError("Ese día ya no existe.", code="missing")
            verb = "Modificó"
        day.reference, day.title, day.reflection, day.question = reference, title, reflection, question
        day.save()
        link = "a" if verb == "Agregó" else "de"
        record_church(plan.church, actor, "plan_modificado", f"{verb} el día {day.position} {link} {_state_words(plan)}", plan)
    return day


def delete_day(day: ReadingPlanDay, actor) -> None:
    """Take a day out of a draft; the days after it move up. A plan that was ever published keeps
    its days (deleting one would delete what people marked): correct the day instead."""
    with transaction.atomic():
        plan = _locked(day.plan)
        if not plan.is_draft:
            raise ResourceError("Un plan que ya se publicó conserva sus días. Puedes corregir el día, no quitarlo.", code="published")
        day = ReadingPlanDay.objects.filter(pk=day.pk, plan=plan).first()
        if day is None:
            return
        position = day.position
        day.delete()
        for later in plan.days.filter(position__gt=position).order_by("position"):  # one at a time: (plan, position) is unique
            later.position -= 1
            later.save(update_fields=["position"])
        record_church(plan.church, actor, "plan_modificado", f"Quitó el día {position} de un borrador de plan de lectura", plan)


def publish_plan(plan: ReadingPlan, actor, *, confirmed: bool) -> ReadingPlan:
    """Every person of the church can find it and start it. Refused without the confirmation of
    authorship or permission, and without at least one day. A retired plan can be published again,
    with a new confirmation. Publishing a published plan changes nothing."""
    with transaction.atomic():
        plan = _locked(plan)
        if plan.is_published:
            return plan
        if not confirmed:
            raise ResourceError(NOT_CONFIRMED, code="confirmation")
        total = plan.days.count()
        if not total:
            raise ResourceError(NO_DAYS, code="days")
        plan.status = ReadingPlan.Status.PUBLISHED
        plan.rights_confirmed_by, plan.rights_confirmed_at = actor, timezone.now()
        plan.save(update_fields=["status", "rights_confirmed_by", "rights_confirmed_at", "updated_at"])
        days = "1 día" if total == 1 else f"{total} días"
        record_church(
            plan.church, actor, "plan_publicado",
            f"Publicó un plan de lectura de {days} y confirmó que la iglesia lo escribió o tiene permiso para publicarlo", plan,
        )
    return plan


def retire_plan(plan: ReadingPlan, actor) -> ReadingPlan:
    """Nobody new finds it or starts it; who had started it keeps reading it. Nothing is deleted. Idempotent."""
    with transaction.atomic():
        plan = _locked(plan)
        if plan.is_retired:
            return plan
        plan.status = ReadingPlan.Status.RETIRED
        plan.save(update_fields=["status", "updated_at"])
        record_church(plan.church, actor, "plan_retirado", "Retiró un plan de lectura", plan)
    return plan
