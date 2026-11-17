"""Events for the people of the church: what a person may see, and every write.

Each write is one transaction that changes the row and stores one church audit row with ids and
counts only. Callers have already answered who may ask (``groups.views.community_view`` and
``church_admin_view``); these functions enforce the rules that hold whoever asks.

Nothing here creates an ``EventParticipant``, an ``EventMembership`` or a ``RoleAssignment``: a
registration says "I want to come" and nothing else.
"""

from __future__ import annotations

from collections import namedtuple

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from events.models import Event
from people.audit import record_church

from .models import EventPage, EventRegistration

ACTIVE = EventRegistration.Status.ACTIVE

# The words a rule answers with. The screens say the same sentences (agenda.views.STATE_NOTICES).
NOT_PUBLISHED = "Este evento todavía no está publicado para la iglesia."
NO_REGISTRATION = "La inscripción a este evento no se hace desde Emmaus. Pregunta en tu iglesia."
REGISTRATION_OVER = "La inscripción a este evento ya cerró."
ENDED = "Este evento ya terminó."
FULL = "Ya no hay lugares."
STARTED = "El evento ya empezó: tu inscripción ya no se puede cancelar desde aquí. Avisa en tu iglesia."

Attendance = namedtuple("Attendance", "present total")


class AgendaError(ValidationError):
    """A rule of the event refused the action; the message is ready for the screen."""


# ---- dates --------------------------------------------------------------------------------------
def last_day(event: Event):
    return event.ends_on or event.starts_on


def has_ended(event: Event, today=None) -> bool:
    """An event without any date is not coming: it counts as ended."""
    today = today or timezone.localdate()
    day = last_day(event)
    return day is None or day < today


def has_started(event: Event, today=None) -> bool:
    today = today or timezone.localdate()
    return event.starts_on is not None and event.starts_on <= today


def _not_ended(today, prefix: str = "") -> Q:
    return Q(**{f"{prefix}ends_on__gte": today}) | Q(**{f"{prefix}ends_on__isnull": True, f"{prefix}starts_on__gte": today})


# ---- what a person sees -------------------------------------------------------------------------
def taken(event: Event) -> int:
    return EventRegistration.objects.filter(event=event, status=ACTIVE).count()


def places_left(page: EventPage, taken_count: int) -> int | None:
    return None if page.capacity is None else max(page.capacity - taken_count, 0)


def state_of(event: Event, page: EventPage | None, registration, taken_count: int, today=None) -> str:
    """Where one person stands with one event, as one word the screens turn into a sentence:
    ``terminado``, ``inscrita``, ``sin_publicar``, ``sin_inscripcion``, ``cerro``, ``llena`` or
    ``abierta``."""
    today = today or timezone.localdate()
    if has_ended(event, today):
        return "terminado"
    if registration is not None and registration.is_active:
        return "inscrita"
    if page is None or not page.published or event.status != Event.Status.ACTIVE:
        return "sin_publicar"
    if not page.registration_open:
        return "sin_inscripcion"
    if page.registration_closes_on and page.registration_closes_on < today:
        return "cerro"
    if page.capacity is not None and taken_count >= page.capacity:
        return "llena"
    return "abierta"


def upcoming_for(person, church, today=None) -> list[Event]:
    """The events of ``church`` this person may see, by first day: published, active and not
    ended, plus the ones they are registered for until they end. Each event carries ``page``,
    ``my_registration`` (the active registration or None), ``taken`` and ``state``."""
    today = today or timezone.localdate()
    mine = {
        row.event_id: row
        for row in EventRegistration.objects.filter(person=person, status=ACTIVE, event__church=church)
    }
    events = (
        Event.objects.filter(church=church, page__isnull=False)
        .filter(_not_ended(today))
        .filter(Q(page__published=True, status=Event.Status.ACTIVE) | Q(pk__in=list(mine)))
        .select_related("page")
        .annotate(taken=Count("registrations", filter=Q(registrations__status=ACTIVE)))
    )
    events = sorted(events, key=lambda event: (event.starts_on or event.ends_on, event.name, event.pk))
    for event in events:
        event.my_registration = mine.get(event.pk)
        event.state = state_of(event, event.page, event.my_registration, event.taken, today)
    return events


def my_registrations(person, today=None) -> list[EventRegistration]:
    """The person's active registrations to events that have not ended, by first day."""
    today = today or timezone.localdate()
    rows = (
        EventRegistration.objects.filter(person=person, status=ACTIVE)
        .filter(_not_ended(today, "event__"))
        .select_related("event")
    )
    return sorted(rows, key=lambda row: (row.event.starts_on or row.event.ends_on, row.event.name, row.pk))


# ---- the person's writes ------------------------------------------------------------------------
def register(event: Event, person, actor) -> EventRegistration:
    """Sign ``person`` up. Idempotent for who is already in; after a cancellation the same row
    comes back. Places are counted with the page row locked, so the last one is given once."""
    if person.church_id != event.church_id or person.merged_into_id is not None:
        raise AgendaError("Esta persona no es de la iglesia del evento.", code="other_church")
    today = timezone.localdate()
    with transaction.atomic():
        page = EventPage.objects.select_for_update().filter(event=event).first()
        if page is None or not page.published or event.status != Event.Status.ACTIVE:
            raise AgendaError(NOT_PUBLISHED, code="not_published")
        if has_ended(event, today):
            raise AgendaError(ENDED, code="ended")
        registration = EventRegistration.objects.select_for_update().filter(event=event, person=person).first()
        if registration is not None and registration.is_active:
            return registration
        if not page.registration_open:
            raise AgendaError(NO_REGISTRATION, code="no_registration")
        if page.registration_closes_on and page.registration_closes_on < today:
            raise AgendaError(REGISTRATION_OVER, code="registration_over")
        if page.capacity is not None and taken(event) >= page.capacity:
            raise AgendaError(FULL, code="full")
        if registration is None:
            try:
                with transaction.atomic():
                    registration = EventRegistration.objects.create(event=event, person=person)
            except IntegrityError:  # two taps at once: the other one created it
                return EventRegistration.objects.get(event=event, person=person)
        else:
            registration.status = ACTIVE
            registration.cancelled_at = None
            registration.attended = None
            registration.checked_in_at = None
            registration.checked_in_by = None
            registration.save(update_fields=["status", "cancelled_at", "attended", "checked_in_at", "checked_in_by"])
        record_church(event.church, actor, "evento_inscripcion", f"{person.full_name} se inscribió a {event.name}",
                      registration, event_id=event.pk, person_id=person.pk)
    return registration


def cancel(registration: EventRegistration, actor) -> EventRegistration:
    """The person is no longer coming. Not once the event has started: by then the church counts
    on the list it has."""
    with transaction.atomic():
        registration = (
            EventRegistration.objects.select_for_update().select_related("event", "person").get(pk=registration.pk)
        )
        if not registration.is_active:
            return registration
        event, person = registration.event, registration.person
        if has_started(event) or has_ended(event):
            raise AgendaError(STARTED, code="started")
        registration.status = EventRegistration.Status.CANCELLED
        registration.cancelled_at = timezone.now()
        registration.save(update_fields=["status", "cancelled_at"])
        record_church(event.church, actor, "evento_inscripcion_cancelada",
                      f"{person.full_name} canceló su inscripción a {event.name}",
                      registration, event_id=event.pk, person_id=person.pk)
    return registration


# ---- the administration's writes ----------------------------------------------------------------
PAGE_FIELDS = ("published", "description", "starts_at", "registration", "capacity", "registration_closes_on")


def save_page(event: Event, actor, **fields) -> EventPage:
    """Create or change the page of ``event``. ``fields`` are the ones of ``PAGE_FIELDS``; the ones
    left out keep their value. Publishing an event that has no first day is refused: nobody would
    ever see it."""
    unknown = sorted(set(fields) - set(PAGE_FIELDS))
    if unknown:
        raise ValueError(f"Unknown event page fields: {', '.join(unknown)}.")
    with transaction.atomic():
        page = EventPage.objects.select_for_update().filter(event=event).first()
        was_published = bool(page and page.published)
        if page is None:
            page = EventPage(event=event, created_by=actor)
        for name, value in fields.items():
            setattr(page, name, value)
        if page.registration not in EventPage.Registration.values:
            raise ValueError(f"Unknown registration mode {page.registration!r}.")
        if page.published and event.starts_on is None:
            raise AgendaError("Este evento no tiene fecha de inicio. Ponla en la configuración del evento y después publícalo.",
                              code="no_date")
        page.save()
        if page.published and not was_published:
            action, summary = "evento_publicado", f"Publicó el evento {event.name} para la iglesia"
        elif was_published and not page.published:
            action, summary = "evento_pagina", f"Dejó de publicar el evento {event.name}"
        else:
            action, summary = "evento_pagina", f"Modificó la página del evento {event.name}"
        record_church(event.church, actor, action, summary, page, event_id=event.pk)
    return page


def record_attendance(event: Event, present_ids, actor) -> Attendance:
    """Who came, among the people registered. Saving again replaces the answer of everybody: who
    is not marked did not come. The audit row carries the count, never the names."""
    if not has_started(event):
        raise AgendaError("El evento todavía no empieza. La asistencia se registra desde su primer día.", code="not_started")
    wanted = {int(value) for value in present_ids if str(value).isdigit()}
    with transaction.atomic():
        rows = list(EventRegistration.objects.select_for_update().filter(event=event, status=ACTIVE))
        now = timezone.now()
        present = 0
        for row in rows:
            came = row.person_id in wanted
            present += came
            if row.attended is came:
                continue
            row.attended = came
            row.checked_in_at = now if came else None
            row.checked_in_by = actor if came else None
            row.save(update_fields=["attended", "checked_in_at", "checked_in_by"])
        record_church(event.church, actor, "evento_asistencia",
                      f"Registró la asistencia de {event.name}: {present} de {len(rows)}", event, event_id=event.pk)
    return Attendance(present, len(rows))
