"""Which questionnaires an event uses, in what order, and how far each campero got.

An event may run several questionnaires at once (``EventQuestionnaire`` rows, ordered). With
``Event.questionnaires_in_order`` a campero answers them in that order; otherwise in any order.

``EventParticipant.interview_status`` is derived from the campero's interviews:

* ``en_curso``: the campero has a live interview. ``start_interview`` refuses a second one, so there
  is at most one, and every "in progress" list of interviews keeps working.
* ``completada``: every questionnaire of the event has a completed interview.
* ``pendiente``: otherwise, also when some are done and the next is not started, so the campero
  stays in every "next up" list.

With one questionnaire this is what the field always meant.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.db.models import Count, IntegerField, Prefetch, Q, Value

from ..models import (
    Event,
    EventParticipant,
    EventQuestionnaire,
    Interview,
    InterviewStatus,
    QuestionnaireVersion,
)

UP = "subir"
DOWN = "bajar"

# QuestionnaireProgress.state
COMPLETED = "completada"
IN_PROGRESS = "en_curso"
PENDING = "pendiente"
BLOCKED = "bloqueado"


def links_for(event) -> list[EventQuestionnaire]:
    """The event's links in order, read from the database (not the per-instance list of Event.ordered_links)."""
    return list(
        EventQuestionnaire.objects.filter(event=event).select_related("version__questionnaire").order_by("order", "id")
    )


def _lock_event(event) -> None:
    """Serialize changes to one event's links (no-op on SQLite).

    FOR NO KEY UPDATE, not FOR UPDATE: the callers lock the event, then its camperos (refresh_interview_statuses),
    while start_interview/finalize hold a campero's row and, at commit, the deferred foreign keys of the new interview
    and audit rows take FOR KEY SHARE on the event row. FOR UPDATE conflicts with FOR KEY SHARE, so PostgreSQL would
    deadlock the two; FOR NO KEY UPDATE does not, and still serializes the callers among themselves."""
    Event.objects.select_for_update(no_key=True).filter(pk=event.pk).values_list("pk", flat=True).first()


def _renumber(event) -> None:
    for position, link in enumerate(EventQuestionnaire.objects.filter(event=event).order_by("order", "id"), start=1):
        if link.order != position:
            link.order = position
            link.save(update_fields=["order"])


@transaction.atomic
def use_version(event, version, *, actor=None) -> EventQuestionnaire:
    """Use ``version`` in the event: in place of another version of the same questionnaire (same place in the
    order), else as the last questionnaire. ``actor`` is accepted for the callers; the view writes the audit event."""
    if version.questionnaire.church_id != event.church_id:
        raise ValueError("Ese cuestionario es de otra iglesia.")
    if version.status != QuestionnaireVersion.Status.PUBLISHED:
        raise ValueError("Solo se puede usar una versión publicada.")
    _lock_event(event)
    link = EventQuestionnaire.objects.filter(event=event, questionnaire_id=version.questionnaire_id).first()
    if link is None:
        last = EventQuestionnaire.objects.filter(event=event).order_by("-order").values_list("order", flat=True).first()
        link = EventQuestionnaire.objects.create(
            event=event, questionnaire_id=version.questionnaire_id, version=version, order=(last or 0) + 1
        )
    elif link.version_id != version.id:
        link.version = version
        link.save(update_fields=["version"])
    event.forget_questionnaire_links()
    refresh_interview_statuses(event)
    return link


@transaction.atomic
def stop_using(event, questionnaire) -> None:
    """The event stops using ``questionnaire`` (an instance or its id). Its interviews stay as they are.

    Not the last one while the event has interviews: with no questionnaire every campero would turn "pendiente"
    and no interview could start (ValueError)."""
    _lock_event(event)
    link = EventQuestionnaire.objects.filter(event=event, questionnaire=questionnaire).first()
    if link is None:
        return
    is_last = not EventQuestionnaire.objects.filter(event=event).exclude(pk=link.pk).exists()
    if is_last and Interview.objects.filter(event=event).exclude(status=Interview.Status.DISCARDED).exists():
        raise ValueError(
            "No se puede quitar el único cuestionario mientras el evento tenga entrevistas. "
            "Agrega otro primero o usa otra versión."
        )
    link.delete()
    _renumber(event)
    event.forget_questionnaire_links()
    refresh_interview_statuses(event)


@transaction.atomic
def move(event, questionnaire, direction: str) -> None:
    """Swap ``questionnaire`` with its neighbour: "subir" towards the first, "bajar" towards the last.
    Nothing happens at the ends or when the event does not use it."""
    if direction not in (UP, DOWN):
        raise ValueError(f"Dirección desconocida: {direction!r}")
    _lock_event(event)
    questionnaire_id = getattr(questionnaire, "pk", questionnaire)
    links = list(EventQuestionnaire.objects.filter(event=event).order_by("order", "id"))
    index = next((i for i, link in enumerate(links) if link.questionnaire_id == questionnaire_id), None)
    if index is None:
        return
    other = index - 1 if direction == UP else index + 1
    if not 0 <= other < len(links):
        return
    links[index], links[other] = links[other], links[index]
    for position, link in enumerate(links, start=1):
        if link.order != position:
            link.order = position
            link.save(update_fields=["order"])
    event.forget_questionnaire_links()


def set_in_order(event, value: bool) -> None:
    event.questionnaires_in_order = bool(value)
    event.save(update_fields=["questionnaires_in_order", "updated_at"])


@dataclass
class QuestionnaireProgress:
    link: EventQuestionnaire
    interview: Interview | None  # the campero's completed or in-progress interview for link.questionnaire
    state: str  # "completada" | "en_curso" | "pendiente" | "bloqueado"


def progress_for(participant, links=None) -> list[QuestionnaireProgress]:
    """Where the campero stands in each questionnaire of the event, in the event's order."""
    links = links_for(participant.event_id) if links is None else links
    by_questionnaire: dict[int, Interview] = {}
    for interview in participant.interviews.exclude(status=Interview.Status.DISCARDED).select_related("version"):
        by_questionnaire.setdefault(interview.questionnaire_id, interview)  # newest first (Interview.Meta.ordering)
    in_order = participant.event.questionnaires_in_order
    progress: list[QuestionnaireProgress] = []
    earlier_done = True
    for link in links:
        interview = by_questionnaire.get(link.questionnaire_id)
        if interview is not None and interview.status == Interview.Status.COMPLETED:
            state = COMPLETED
        elif interview is not None:
            state = IN_PROGRESS
        elif in_order and not earlier_done:
            state = BLOCKED
        else:
            state = PENDING
        progress.append(QuestionnaireProgress(link=link, interview=interview, state=state))
        earlier_done = earlier_done and state == COMPLETED
    return progress


def pending_links(participant, links=None) -> list[EventQuestionnaire]:
    """The questionnaires the campero can start now: not done, not in progress, not waiting for an earlier one
    (in order: at most one, the first not done)."""
    return [p.link for p in progress_for(participant, links) if p.state == PENDING]


def _derive(kept_interviews, links) -> str:
    """``kept_interviews``: the campero's interviews that are not discarded."""
    if any(i.status == Interview.Status.IN_PROGRESS for i in kept_interviews):
        return InterviewStatus.IN_PROGRESS
    if not links:
        return InterviewStatus.PENDING
    done = {i.questionnaire_id for i in kept_interviews if i.status == Interview.Status.COMPLETED}
    if all(link.questionnaire_id in done for link in links):
        return InterviewStatus.COMPLETED
    return InterviewStatus.PENDING


def derive_interview_status(participant, links=None) -> str:
    links = links_for(participant.event_id) if links is None else links
    kept = participant.interviews.exclude(status=Interview.Status.DISCARDED).only("status", "questionnaire_id")
    return _derive(list(kept), links)


@transaction.atomic
def refresh_interview_statuses(event) -> int:
    """Recompute every campero's status (active or not) after the event's questionnaires changed.
    Saves only the rows that change; returns how many changed."""
    links = links_for(event)
    participants = (
        EventParticipant.objects.filter(event=event)
        .select_for_update()
        .order_by("pk")  # one lock order for every caller
        .prefetch_related(
            Prefetch(
                "interviews",
                queryset=Interview.objects.exclude(status=Interview.Status.DISCARDED).only(
                    "participant_id", "status", "questionnaire_id"
                ),
                to_attr="kept_interviews",
            )
        )
    )
    changed = 0
    for participant in participants:
        status = _derive(participant.kept_interviews, links)
        if status != participant.interview_status:
            participant.interview_status = status
            participant.save(update_fields=["interview_status", "updated_at"])
            changed += 1
    return changed


def annotate_questionnaire_progress(participants, event):
    """``done_count``: questionnaires of the event the campero completed; ``questionnaire_total``: how many the event uses."""
    linked = list(EventQuestionnaire.objects.filter(event=event).values_list("questionnaire_id", flat=True))
    if not linked:
        return participants.annotate(
            done_count=Value(0, output_field=IntegerField()), questionnaire_total=Value(0, output_field=IntegerField())
        )
    return participants.annotate(
        done_count=Count(
            "interviews",
            filter=Q(interviews__status=Interview.Status.COMPLETED, interviews__questionnaire_id__in=linked),
            distinct=True,
        ),
        questionnaire_total=Value(len(linked), output_field=IntegerField()),
    )
