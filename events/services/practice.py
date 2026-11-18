"""The practice campero (issue #49): a synthetic row the team rehearses the questionnaire with before the camp.

One per event (a database constraint), created, passed around and removed by the administration. It is a real
``EventParticipant`` row with ``is_practice``, so the interview screens run on it exactly as on a camper; what keeps it
out of every list, count, cutoff and export is ``.real()`` (see ``events.models``). Nothing here reads an answer.

"Ya practicó" is a fact about a person, and it must outlive the practice interview (the next rehearsal deletes it):
it is read from the activity log, where ``interviews.finalize`` leaves a ``practica_completada`` row.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction

from ..models import PRACTICE_NAME, PRACTICE_NUMBER, PRACTICE_PLACE, AuditEvent, Event, EventMembership, EventParticipant, Interview, MembershipRole
from . import audit


class PracticeError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def get(event: Event) -> EventParticipant | None:
    return EventParticipant.objects.practice().filter(event=event).select_related("coordinator__user").first()


def coordinators(event: Event):
    """Who the practice campero can be passed to: the coordinadores with access to the event."""
    return (
        EventMembership.objects.filter(event=event, role=MembershipRole.COORDINATOR, is_active=True, user__is_active=True)
        .select_related("user")
        .order_by("user__first_name", "user__last_name", "id")
    )


def practised_user_ids(event: Event) -> set[int]:
    """The accounts that completed a practice interview in this event, whenever it was."""
    return set(
        AuditEvent.objects.filter(event=event, action=audit.PRACTICE_COMPLETED, actor__isnull=False).values_list("actor_id", flat=True)
    )


def kept_interviews(participant: EventParticipant) -> int:
    return participant.interviews.exclude(status=Interview.Status.DISCARDED).count()


def _lock(event: Event) -> None:
    Event.objects.select_for_update().filter(pk=event.pk).first()


def _new(event: Event, coordinator: EventMembership | None, actor) -> EventParticipant:
    return EventParticipant.objects.create(
        event=event,
        number=PRACTICE_NUMBER,
        full_name=PRACTICE_NAME,
        age=min(max(event.adult_age, 18), 99),  # an adult: no guardian authorization to invent
        room=PRACTICE_PLACE,  # the interview screens show both: words, not two empty rows
        church_group=PRACTICE_PLACE,
        coordinator=coordinator,
        is_practice=True,
        created_by=actor,
        updated_by=actor,
    )


def _checked(event: Event, coordinator: EventMembership | None) -> EventMembership | None:
    if coordinator is None:
        return None
    if coordinator.event_id != event.id or coordinator.role != MembershipRole.COORDINATOR or not coordinator.is_active:
        raise PracticeError("Elige a un coordinador con acceso a este evento.")
    return coordinator


def create(event: Event, *, actor, coordinator: EventMembership | None = None) -> EventParticipant:
    """The event's practice campero; the one it already has when there is one (a double click creates nothing)."""
    coordinator = _checked(event, coordinator)
    try:
        with transaction.atomic():
            _lock(event)
            existing = get(event)
            if existing is not None:
                return existing
            participant = _new(event, coordinator, actor)
            audit.record(actor, event, "practica_creada", "Creó el campero de práctica", participant)
    except IntegrityError:  # number 0 is taken by a row that is not the practice campero: nothing here may renumber it
        raise PracticeError("No se pudo crear el campero de práctica: ya hay un campero con el número 0.") from None
    return participant


@transaction.atomic
def delete(event: Event, *, actor) -> int:
    """Remove the practice campero with its interviews, answers, authorizations and follow-ups. Returns how many
    interviews went with it (0 also when there was no practice campero)."""
    _lock(event)
    participant = get(event)
    if participant is None:
        return 0
    interviews = kept_interviews(participant)
    participant.delete()
    audit.record(actor, event, "practica_eliminada", "Quitó el campero de práctica y sus entrevistas de práctica", None, interviews=interviews)
    return interviews


@transaction.atomic
def hand_to(event: Event, coordinator: EventMembership | None, *, actor) -> EventParticipant:
    """Pass the practice campero to ``coordinator`` (None: nobody), clean: whatever the last rehearsal left is deleted,
    so the next person starts from the first question. Also "Dejarlo como nuevo" for the same person."""
    coordinator = _checked(event, coordinator)
    _lock(event)
    current = get(event)
    if current is None:
        raise PracticeError("Este evento no tiene campero de práctica.")
    interviews = kept_interviews(current)
    current.delete()  # with its interviews, answers, authorizations and follow-ups
    participant = _new(event, coordinator, actor)
    who = f"a {coordinator.display_name}" if coordinator else "sin coordinador"
    audit.record(actor, event, "practica_asignada", f"Dejó listo el campero de práctica, {who}", participant, interviews=interviews)
    return participant
