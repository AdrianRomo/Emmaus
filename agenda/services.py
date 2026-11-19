"""Events for the people of the church: what a person may see, and every write.

Each write is one transaction that changes the row and stores one church audit row with ids and
counts only. Callers have already answered who may ask (``groups.views.community_view`` and
``church_admin_view``); these functions enforce the rules that hold whoever asks.

Nothing here creates an ``EventParticipant``, an ``EventMembership`` or a ``RoleAssignment``: a
registration says "I want to come" and nothing else.

ONE LOCK ORDER, for every write here: the person (``_fresh``), then the event's page
(``_locked_page``), then registration rows (``_locked``, or a locked queryset of the event's rows).
A function may skip a level, never take them in another order: two requests about the same person
or the same event then wait for each other instead of deadlocking on PostgreSQL.
"""

from __future__ import annotations

import logging
import unicodedata
from collections import namedtuple
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from core.notify import notify
from events.models import Event, normalize_name
from people import flags, policy
from people import services as people_services
from people.audit import record_church
from people.models import Person

from .models import EventFaq, EventNotice, EventPage, EventRegistration, EventScheduleItem, GuardianConsent

ACTIVE = EventRegistration.Status.ACTIVE
PENDING = EventRegistration.Status.PENDING
CANCELLED = EventRegistration.Status.CANCELLED
WAITING = EventRegistration.Status.WAITING
OFFERED = EventRegistration.Status.OFFERED
# Who is in: confirmed, or waiting for the team to verify an age or for a guardian's authorization.
HOLDING = EventRegistration.HOLDING
# What uses a place of the capacity: who is in, and a place offered to somebody of the waiting list.
TAKING = EventRegistration.TAKING
# On the waiting list: in line (no place), or with an offer to answer (a place held until it expires).
LISTED = EventRegistration.LISTED
Outcome = EventRegistration.WaitingOutcome
logger = logging.getLogger(__name__)
AGE = EventRegistration.PendingReason.AGE.value
CONSENT = EventRegistration.PendingReason.CONSENT.value

# The words a rule answers with. The screens say the same sentences (agenda.views.STATE_NOTICES).
NOT_PUBLISHED = "Este evento todavía no está publicado para la iglesia."
NO_REGISTRATION = "La inscripción a este evento no se hace desde Emmaus. Pregunta en tu iglesia."
REGISTRATION_OVER = "La inscripción a este evento ya cerró."
ENDED = "Este evento ya terminó."
FULL = "Ya no hay lugares."
STARTED = "El evento ya empezó: tu inscripción ya no se puede cancelar desde aquí. Avisa en tu iglesia."
PENDING_NEXT = "Tu inscripción queda pendiente hasta que tu mamá, papá o tutor la autorice con el equipo de la iglesia."
AGE_NEXT = "Tu inscripción queda pendiente: el equipo de la iglesia tiene que confirmar tu edad."
LIST_FROZEN = "La inscripción desde Emmaus está cerrada: pregunta en tu iglesia."
LIST_STARTED = "El evento ya empezó: el lugar que te ofrecimos ya no se puede aceptar desde aquí. Pregunta en tu iglesia."
OFFER_OVER = "El lugar que te ofrecimos ya venció. Si todavía quieres ir, puedes anotarte otra vez en la lista de espera."
NO_OFFER = "Por ahora no tienes un lugar ofrecido en este evento."
OFFER_KIND = "lugar_ofrecido"
CALL_IS_NOT_ENOUGH = (
    "Una llamada o un mensaje no basta para confirmar: hace falta el formato firmado en papel o verificar en "
    "persona la identidad y el parentesco de quien autoriza."
)

Attendance = namedtuple("Attendance", "present total")
# Where a registration stands: ``status`` and, when it waits, the ``reason`` (``age`` or ``consent``).
Standing = namedtuple("Standing", "status reason")
CONFIRMED = Standing(ACTIVE.value, "")
SETTLED_FIELDS = ["status", "pending_reason", "attended", "checked_in_at", "checked_in_by"]


class WalkupOverrideReason(models.TextChoices):
    """Why the administration writes somebody down over a rule of the event's page. Fixed words: the
    audit keeps the choice, never a sentence somebody typed."""

    ARRIVED = "llego", "Llegó sin inscribirse y el equipo decidió recibirle"
    INVITED = "invitado", "Viene con alguien de la iglesia que le invitó"
    COORDINATION = "coordinacion", "Lo autorizó quien coordina el evento"


# The rules of the page a walk-up meets, as the administration reads them. ``llena`` is the capacity:
# it is overridden by its own checkbox. The minors' rule is not here: nothing overrides it.
WALKUP_RULES = {
    "sin_pagina": "Este evento no tiene página: no está publicado para la iglesia.",
    "sin_publicar": "Este evento no está publicado para la iglesia.",
    "sin_inscripcion": "La inscripción a este evento no se hace desde Emmaus.",
    "cerro": "La inscripción a este evento ya cerró.",
    "llena": "El evento está lleno: ya no hay lugares.",
}
FULL_RULE = "llena"


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
    """The places in use: a registration that waits for a guardian holds its place too, and so does
    a place offered to somebody of the waiting list until they answer or it expires. Somebody who
    waits in line holds none."""
    return EventRegistration.objects.filter(event=event, status__in=TAKING).count()


def age_on(birth_date: date, day: date) -> int:
    return day.year - birth_date.year - ((day.month, day.day) < (birth_date.month, birth_date.day))


def is_minor_at(event: Event, birth_date: date, today=None) -> bool:
    """Under 18 on the event's first day (today, for an event without one)."""
    return age_on(birth_date, event.starts_on or today or timezone.localdate()) < 18


def standing(event: Event, page: EventPage | None, *, person=None, said_minor: bool = False, age_checked: bool = False,
             consent=None, assume_rule: bool = False, today=None) -> Standing:
    """THE function that decides whether a registration is confirmed (decision 0015). Every path
    asks it: signing up, signing up again, a walk-up, a visitor who becomes a person, turning the
    rule on, verifying an age, recording or withdrawing an authorization.

    | Who                                                   | Stands             |
    |-------------------------------------------------------|--------------------|
    | adult by a date of birth the team verified            | confirmed          |
    | adult by a date somebody typed                        | pending, ``age``   |
    | unknown date of birth                                 | pending, ``age``   |
    | minor (a typed date is believed; so is the team's word)| pending, ``consent``|
    | minor with an authorization that confirms             | confirmed          |
    | authorization withdrawn, or only a call or a message  | pending, ``consent``|
    | visitor the team saw and said "es mayor de edad"      | confirmed          |
    | visitor nobody said anything about                    | pending, ``age``   |

    ``person`` is None for a visitor: the team's word at the door is all there is, "es menor de
    edad" (``said_minor``) or "es mayor de edad" (``age_checked``); a visitor written down while the
    event did not ask has neither and waits for that look. For a person of the church
    ``said_minor`` counts only while the date of birth is unknown, and only towards "minor";
    ``age_checked`` never counts. ``consent`` is the row's ``GuardianConsent`` or None; what
    confirms is ``GuardianConsent.confirms``. An event that does not ask (``page``) confirms
    everybody; ``assume_rule`` answers as if it asked, for a row that is already waiting."""
    if not assume_rule and (page is None or not page.requires_guardian_consent):
        return CONFIRMED
    if person is None:
        minor = bool(said_minor)
        if not minor and not age_checked:
            return Standing(PENDING.value, AGE)
    elif person.birth_date is None:
        if not said_minor:
            return Standing(PENDING.value, AGE)
        minor = True
    else:
        minor = is_minor_at(event, person.birth_date, today)
        if not minor and not person.birth_date_verified:
            return Standing(PENDING.value, AGE)
    if not minor or (consent is not None and consent.confirms):
        return CONFIRMED
    return Standing(PENDING.value, CONSENT)


def _settle(registration: EventRegistration, page: EventPage | None, actor=None, *, assume_rule: bool = False) -> bool:
    """Give the row the standing ``standing`` decides; the caller saves ``SETTLED_FIELDS``. A row that
    waits loses its attendance mark; a row the team wrote at the door that stops waiting once the
    event started is marked as present. Returns whether anything changed. With the rule off (and no
    ``assume_rule``) nothing is touched: turning the rule off confirms nobody."""
    if not assume_rule and (page is None or not page.requires_guardian_consent):
        return False
    new = standing(
        registration.event, page, person=registration.person, said_minor=registration.guest_is_minor,
        age_checked=registration.guest_age_checked, consent=getattr(registration, "consent", None), assume_rule=assume_rule,
    )
    old = (registration.status, registration.pending_reason)
    registration.status, registration.pending_reason = new
    if new.status == PENDING:
        registration.attended, registration.checked_in_at, registration.checked_in_by = None, None, None
    elif old[0] == PENDING and registration.is_walkup and has_started(registration.event):
        registration.attended, registration.checked_in_at, registration.checked_in_by = True, timezone.now(), actor
    return old != tuple(new)


STANDING_WORDS = {"": "quedó confirmada", AGE: "quedó pendiente de confirmar su edad", CONSENT: "quedó pendiente de autorización"}


def _review(registration: EventRegistration, page: EventPage | None, actor=None, *, assume_rule: bool = False) -> bool:
    """Settle one row, save it and, when its standing changed, leave an audit row with ids and one word."""
    if not _settle(registration, page, actor, assume_rule=assume_rule):
        return False
    registration.save(update_fields=SETTLED_FIELDS)
    event = registration.event
    record_church(event.church, actor, "evento_inscripcion_revisada",
                  f"La inscripción de {registration.display_name} a {event.name} {STANDING_WORDS[registration.pending_reason]}",
                  registration, event_id=event.pk, standing=registration.pending_reason or registration.status,
                  **_who(registration))
    return True


def reevaluate_person(person, actor=None) -> int:
    """What the church knows about a person's age changed (``people.models.age_facts_changed``): every
    registration of theirs that holds a place in an event that asks and has not ended is settled again."""
    today = timezone.localdate()
    with transaction.atomic():
        rows = (
            EventRegistration.objects.select_for_update(of=("self",))
            .filter(person=person, status__in=HOLDING, event__page__requires_guardian_consent=True)
            .filter(_not_ended(today, "event__"))
            .select_related("event", "event__church", "event__page", "consent")
        )
        changed = 0
        for row in rows:
            row.person = person
            changed += _review(row, row.event.page, actor)
    return changed


def resettle_event(event: Event, page: EventPage | None, actor=None, *, apply: bool = True) -> dict:
    """Settle EVERY registration that holds a place in ``event`` with ``standing`` (nothing happens
    when its page does not ask). Saving the page, moving the event's first day and
    ``resettle_registrations`` all come here. Returns counts: ``reviewed``, and the rows whose
    standing changed by where they stand now (``inscrita``, ``age``, ``consent``). ``apply=False``
    counts what would change and writes nothing."""
    counts = {"reviewed": 0, "inscrita": 0, AGE: 0, CONSENT: 0}
    if page is None or not page.requires_guardian_consent:
        return counts
    with transaction.atomic():
        held = EventRegistration.objects.filter(event=event, status__in=HOLDING).select_related("person", "consent")
        if apply:
            held = held.select_for_update(of=("self",))
        for row in held:
            row.event = event
            counts["reviewed"] += 1
            if _review(row, page, actor) if apply else _settle(row, page, actor):
                counts[row.pending_reason or row.status] += 1
    return counts


def resettle_all(actor=None, *, apply: bool = False, today=None) -> dict:
    """Every event that asks for the authorization and has not ended, through ``resettle_event``:
    for rows that were confirmed by older code, or under older words of the authorization
    (``manage.py resettle_registrations``). Idempotent: a second run changes nothing."""
    today = today or timezone.localdate()
    totals = {"events": 0, "reviewed": 0, "inscrita": 0, AGE: 0, CONSENT: 0}
    pages = EventPage.objects.filter(requires_guardian_consent=True).filter(_not_ended(today, "event__"))
    for page in pages.select_related("event", "event__church").order_by("event_id"):
        totals["events"] += 1
        for key, value in resettle_event(page.event, page, actor, apply=apply).items():
            totals[key] += value
    return totals


def event_first_day_moved(event: Event) -> dict:
    """The first day of ``event`` changed, and with it who is a minor on that day
    (``is_minor_at``): its registrations are settled again."""
    return resettle_event(event, EventPage.objects.filter(event=event, requires_guardian_consent=True).first())


def places_left(page: EventPage, taken_count: int) -> int | None:
    return None if page.capacity is None else max(page.capacity - taken_count, 0)


def list_block(event: Event, page: EventPage | None, today=None) -> str:
    """Why the waiting list of ``event`` does not move right now, as one word, or empty when it does:

    * ``terminado``: the event is over.
    * ``empezo``: the event started. No more offers (the door and its override take over) and an
      offer already made can no longer be accepted.
    * ``sin_publicar`` / ``sin_inscripcion``: the list is FROZEN because the administration
      unpublished the page (or the event is not active) or set it to "No hay inscripción desde
      Emmaus". No new offers, and an offer already made cannot be accepted until that changes.

    The closing DATE is not here on purpose: it stops new sign-ups only. Who is on the list signed up
    in time, so offers and acceptances go on after it. A deadline runs whatever this says: an offer
    that reaches it while the list is frozen expires like any other."""
    today = today or timezone.localdate()
    if has_ended(event, today):
        return "terminado"
    if has_started(event, today):
        return "empezo"
    if page is None or not page.published or event.status != Event.Status.ACTIVE:
        return "sin_publicar"
    if not page.registration_open:
        return "sin_inscripcion"
    return ""


def offer_is_open(registration, now=None) -> bool:
    """The row is an offered place the person can still accept."""
    return (
        registration is not None and registration.status == OFFERED
        and registration.offer_expires_at is not None and registration.offer_expires_at > (now or timezone.now())
    )


def state_of(event: Event, page: EventPage | None, registration, taken_count: int, today=None) -> str:
    """Where one person stands with one event, as one word the screens turn into a sentence:
    ``terminado``, ``inscrita``, ``pendiente`` (a minor whose guardian has not authorized yet),
    ``ofrecido`` (a place of the waiting list offered to them, to accept or let go), ``en_espera``
    (in line), ``sin_publicar``, ``sin_inscripcion``, ``cerro``, ``lista`` (full, and who signs up
    waits in line), ``llena`` or ``abierta``. An offer past its deadline counts as no registration;
    one that cannot be accepted right now (``list_block``) is ``ofrecido_pausa``. Once the event has
    started nobody joins the list: a full event is ``llena``."""
    today = today or timezone.localdate()
    if has_ended(event, today):
        return "terminado"
    if registration is not None and registration.is_active:
        return "inscrita"
    if registration is not None and registration.is_pending:
        return "pendiente"
    if offer_is_open(registration):
        # An offer that cannot be accepted right now (the list is frozen, or the event started) is not "ofrecido".
        return "ofrecido_pausa" if list_block(event, page, today) else "ofrecido"
    if registration is not None and registration.is_waiting:
        return "en_espera"
    if page is None or not page.published or event.status != Event.Status.ACTIVE:
        return "sin_publicar"
    if not page.registration_open:
        return "sin_inscripcion"
    if page.registration_closes_on and page.registration_closes_on < today:
        return "cerro"
    if page.capacity is not None and taken_count >= page.capacity:
        return "lista" if page.waiting_list and not has_started(event, today) else "llena"
    return "abierta"


def upcoming_for(person, church, today=None) -> list[Event]:
    """The events of ``church`` this person may see, by first day: published, active and not
    ended, plus the ones they are registered for until they end. Each event carries ``page``,
    ``my_registration`` (the confirmed registration or None), ``my_place`` (that one, or the one
    that waits for a guardian's authorization), ``my_wait`` (their row on the waiting list: in line,
    or an offered place they can still accept), ``taken`` and ``state``."""
    today = today or timezone.localdate()
    mine, listed = {}, {}
    for row in EventRegistration.objects.filter(person=person, status__in=(*HOLDING, *LISTED), event__church=church):
        if row.status in HOLDING:
            mine[row.event_id] = row
        elif row.is_waiting or offer_is_open(row):
            listed[row.event_id] = row
    events = (
        Event.objects.filter(church=church, page__isnull=False)
        .filter(_not_ended(today))
        .filter(Q(page__published=True, status=Event.Status.ACTIVE) | Q(pk__in=[*mine, *listed]))
        .select_related("page")
        .annotate(taken=Count("registrations", filter=Q(registrations__status__in=TAKING)))
    )
    events = sorted(events, key=lambda event: (event.starts_on or event.ends_on, event.name, event.pk))
    for event in events:
        event.my_place = mine.get(event.pk)
        event.my_wait = listed.get(event.pk)
        event.my_registration = event.my_place if event.my_place is not None and event.my_place.is_active else None
        event.state = state_of(event, event.page, event.my_place or event.my_wait, event.taken, today)
    # A change to an event is published as its notice (#101): the event's row says a recent one is there (a
    # decision under the owner's delegation), never a church notice tied to it by guess
    recent = recent_notices([event.pk for event in events])
    for event in events:
        event.recent_notice = recent.get(event.pk)
    return events


RECENT_NOTICE_DAYS = 7


def recent_notices(event_ids, now=None) -> dict:
    """{event id: its newest public notice published in the last RECENT_NOTICE_DAYS days}, already published."""
    if not event_ids:
        return {}
    now = now or timezone.now()
    latest = {}
    for notice in EventNotice.objects.filter(
        event_id__in=event_ids, published_at__lte=now, published_at__gte=now - timedelta(days=RECENT_NOTICE_DAYS)
    ).order_by("-published_at", "-id"):
        latest.setdefault(notice.event_id, notice)
    return latest


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
def register(event: Event, person, actor, *, base_url: str | None = None) -> EventRegistration:
    """Sign ``person`` up. Idempotent for who is already in (confirmed or waiting for a guardian)
    and for who is on the waiting list (in line or with an offer: signing up again moves nothing);
    after a cancellation the same row comes back. Places are counted with the page row locked, so
    the last one is given once.

    A full event whose page keeps a waiting list (``EventPage.waiting_list``) puts the person at
    the end of the list (``en_espera``): no place, no standing yet. Check ``is_waiting`` on what
    comes back.

    When the event asks for a guardian's authorization, ``standing`` decides whether the row is
    confirmed or waits (``pendiente``): for the team to verify the age, or for a guardian. An
    authorization of this same registration that still confirms survives a cancellation."""
    if person.church_id != event.church_id or person.merged_into_id is not None:
        raise AgendaError("Esta persona no es de la iglesia del evento.", code="other_church")
    today = timezone.localdate()
    with transaction.atomic():
        # What the church knows about the age NOW, not what the caller read a while ago; locked
        # first, so a change of the date waits and then finds this registration.
        person = _fresh(person)
        page = EventPage.objects.select_for_update().filter(event=event).first()
        if page is None or not page.published or event.status != Event.Status.ACTIVE:
            raise AgendaError(NOT_PUBLISHED, code="not_published")
        if has_ended(event, today):
            raise AgendaError(ENDED, code="ended")
        # First the list: an offer past its deadline is released and a free place goes to who waits,
        # so nobody who arrives now takes a place ahead of the list.
        _advance(event, page, base_url=base_url)
        registration = EventRegistration.objects.select_for_update().filter(event=event, person=person).first()
        if registration is not None and registration.status in (*HOLDING, *LISTED):
            return registration
        if not page.registration_open:
            raise AgendaError(NO_REGISTRATION, code="no_registration")
        if page.registration_closes_on and page.registration_closes_on < today:
            raise AgendaError(REGISTRATION_OVER, code="registration_over")
        full = page.capacity is not None and taken(event) >= page.capacity
        if full and (not page.waiting_list or has_started(event, today)):  # a list that will not move takes nobody
            raise AgendaError(FULL, code="full")
        if full:
            # In line: no place and no standing. ``standing`` is asked the day the offer is accepted.
            where, since = Standing(WAITING.value, ""), timezone.now()
        else:
            since = None
            where = standing(
                event, page, person=person, today=today,
                said_minor=registration is not None and registration.guest_is_minor,
                consent=getattr(registration, "consent", None) if registration is not None else None,
            )
        if registration is None:
            try:
                with transaction.atomic():
                    registration = EventRegistration.objects.create(
                        event=event, person=person, status=where.status, pending_reason=where.reason, waiting_since=since
                    )
            except IntegrityError:  # two taps at once: the other one created it
                return EventRegistration.objects.get(event=event, person=person)
        else:
            registration.status, registration.pending_reason = where
            registration.cancelled_at = None
            registration.annulled_reason = ""
            registration.attended = None
            registration.checked_in_at = None
            registration.checked_in_by = None
            registration.registered_by = None
            registration.waiting_since, registration.offer_expires_at, registration.waiting_outcome = since, None, ""
            registration.save(update_fields=[*SETTLED_FIELDS, "cancelled_at", "annulled_reason", "registered_by", *LIST_FIELDS])
        if full:
            record_church(event.church, actor, "evento_lista_espera",
                          f"{person.full_name} se anotó en la lista de espera de {event.name}", registration,
                          event_id=event.pk, person_id=person.pk)
        else:
            summary = f"{person.full_name} se inscribió a {event.name}{PENDING_SUFFIX[where.reason]}"
            record_church(event.church, actor, "evento_inscripcion", summary, registration,
                          event_id=event.pk, person_id=person.pk)
    return registration


def set_birth_date(person, birth_date: date, actor):
    """The person says their own date of birth, once: only when the church does not know it yet.
    After that it is the administration's to correct (Personas), like the name. A date typed here
    is a claim: it is never verified, so it can make a sign-up wait for a guardian (a minor is
    believed) and can never confirm one (``standing``)."""
    today = timezone.localdate()
    if birth_date > today or age_on(birth_date, today) > 120:
        raise AgendaError("Revisa la fecha de nacimiento.", code="birth_date")
    if person.birth_date is None:
        person.birth_date = birth_date
        people_services.save_person(person, actor, created=False)
    return person


def cancel(registration: EventRegistration, actor, *, base_url: str | None = None) -> EventRegistration:
    """The person is no longer coming (confirmed or still waiting for a guardian). Not once the
    event has started: by then the church counts on the list it has. An authorization already
    recorded stays with the row. The freed place is offered to the first of the waiting list."""
    with transaction.atomic():
        page = _locked_page(registration.event_id)
        registration = _locked(registration.pk)
        if registration.status not in HOLDING:
            return registration
        event = registration.event
        if has_started(event) or has_ended(event):
            raise AgendaError(STARTED, code="started")
        registration.status = CANCELLED
        registration.pending_reason = ""
        registration.cancelled_at = timezone.now()
        registration.save(update_fields=["status", "pending_reason", "cancelled_at"])
        record_church(event.church, actor, "evento_inscripcion_cancelada",
                      f"{registration.display_name} canceló su inscripción a {event.name}",
                      registration, event_id=event.pk, **_who(registration))
        _advance(event, page, base_url=base_url)
    return registration


def _locked_page(event_id: int) -> EventPage | None:
    """The page row of an event, locked: THE lock every change of who holds a place, who waits and
    who has an offer happens under, taken before any registration row."""
    return EventPage.objects.select_for_update().filter(event_id=event_id).first()


def _fresh(person) -> Person:
    """The person as the database has them, locked: ``standing`` never reads a stale date of birth."""
    return Person.objects.select_for_update().get(pk=person.pk)


def _locked(registration_id: int) -> EventRegistration:
    """The row locked for a write. ``of=("self",)``: ``person`` is nullable, and PostgreSQL refuses
    FOR UPDATE on the nullable side of an outer join."""
    return (
        EventRegistration.objects.select_for_update(of=("self",))
        .select_related("event", "event__church", "person", "consent")
        .get(pk=registration_id)
    )


PENDING_SUFFIX = {"": "", AGE: "; queda pendiente de confirmar su edad", CONSENT: "; queda pendiente de autorización"}


def _who(registration: EventRegistration) -> dict:
    """Audit metadata that says which row, by ids only."""
    if registration.person_id is None:
        return {"registration_id": registration.pk}
    return {"registration_id": registration.pk, "person_id": registration.person_id}


# ---- the waiting list (decision 0011) -------------------------------------------------------------
# A freed place is OFFERED to the first of the list, with a deadline, and only the person turns the
# offer into a registration. Every move happens with the page row locked (``_locked_page``):
#
#   en_espera --(a place is free: ``_advance``)--> ofrecida --(``accept_offer`` + ``standing``)--> inscrita | pendiente
#   en_espera --(``leave_waiting`` | ``remove_from_waiting``)--> cancelada
#   ofrecida  --(``leave_waiting`` | ``remove_from_waiting`` | the deadline: ``_advance``)--> cancelada
#
# Nothing else touches a row of the list: not attendance, not signing up again, not an edit of the
# page, not ``resettle_event``, not a walk-up.
LIST_FIELDS = ["waiting_since", "offer_expires_at", "waiting_outcome"]


def waiting_position(registration: EventRegistration) -> int | None:
    """The place in line of a row that waits, counting from 1; None for any other row. Who has an
    offer is no longer in line."""
    if registration is None or not registration.is_waiting:
        return None
    since = registration.waiting_since
    ahead = EventRegistration.objects.filter(event_id=registration.event_id, status=WAITING).filter(
        Q(waiting_since__lt=since) | Q(waiting_since=since, pk__lt=registration.pk)
    )
    return ahead.count() + 1


def position_words(position: int) -> str:
    """The place in line as the person reads it."""
    if position == 1:
        return "Eres la primera persona en la lista de espera."
    return f"Eres la persona {position} en la lista de espera."


def waiting_rows(event: Event) -> list[EventRegistration]:
    """The list as the administration reads it: first who has an offer to answer (by deadline), then
    who waits, in order. Each row that waits carries ``position``."""
    rows = EventRegistration.objects.filter(event=event, status__in=LISTED).select_related("person")
    offered = sorted((row for row in rows if row.is_offered), key=lambda row: (row.offer_expires_at, row.pk))
    waiting = sorted((row for row in rows if row.is_waiting), key=lambda row: (row.waiting_since, row.pk))
    for position, row in enumerate(waiting, start=1):
        row.position = position
    return [*offered, *waiting]


def last_list_outcome(event: Event, person) -> str:
    """How this person's time on the list ended when it ended without a place and they have not
    signed up again: ``expiro``, ``retirada`` or empty. For the sentence on the event's page."""
    row = EventRegistration.objects.filter(event=event, person=person, status=CANCELLED).first()
    if row is None or row.waiting_outcome not in (Outcome.EXPIRED, Outcome.REMOVED):
        return ""
    return row.waiting_outcome


def _off_the_list(registration: EventRegistration, outcome: str, now) -> None:
    registration.status = CANCELLED
    registration.pending_reason = ""
    registration.cancelled_at = now
    registration.waiting_outcome = outcome
    registration.offer_expires_at = None
    registration.save(update_fields=["status", "pending_reason", "cancelled_at", "waiting_outcome", "offer_expires_at"])


def _announce_offer(registration: EventRegistration, base_url: str | None) -> None:
    """After the transaction commits, one e-mail that says a place was freed and where to answer:
    the church's name and the address of the event's page, nothing else (``core.notify``). Without a
    ``base_url`` or an address nothing is sent, and the person reads the offer in Emmaus. A failure
    is logged and never reaches whoever freed the place."""
    person, event = registration.person, registration.event
    if not base_url or person is None:  # a row without a person has nobody to mail: never an error here
        return
    address = person.email or (person.user.email if person.user_id else "")
    if not address:
        return
    context = {"church_name": event.church.name, "link": f"{base_url.rstrip('/')}{reverse('agenda:detail', args=[event.slug])}"}

    def send_offer_after_commit() -> None:
        try:
            notify(OFFER_KIND, address, context)
        except ValueError:  # a church name or an address ``core.notify`` refuses; its message names no value
            logger.exception("The e-mail about an offered place could not be written.")

    transaction.on_commit(send_offer_after_commit, robust=True)


def _advance(event: Event, page: EventPage | None, *, base_url: str | None = None, now=None) -> dict:
    """Move the waiting list of ``event``. THE CALLER HOLDS THE PAGE ROW LOCK (``_locked_page``).

    1. Every offer past its deadline is released: the row becomes ``cancelada`` with the fixed
       reason "No respondió a tiempo"; the person reads it and may join the list again, at the end.
    2. While there is a free place (capacity - confirmed - pending - offered) and somebody waits,
       the first in line gets the offer, held ``policy.waiting_offer_hours`` hours. Not while
       ``list_block`` says the list does not move: the page is not published or takes no sign-up
       from Emmaus (frozen), or the event started or is over.

    It never confirms anybody. Returns counts: ``released`` and ``offered``. Idempotent: a second
    call with nothing new changes nothing. The audit rows are the system's, with ids and a fixed word."""
    now = now or timezone.now()
    moved = {"released": 0, "offered": 0}
    rows = EventRegistration.objects.select_for_update(of=("self",)).select_related("person").filter(event=event)
    for row in rows.filter(status=OFFERED, offer_expires_at__lte=now).order_by("offer_expires_at", "pk"):
        row.event = event
        _off_the_list(row, Outcome.EXPIRED, now)
        record_church(event.church, None, "evento_lista_vencida",
                      f"Venció el lugar ofrecido a {row.display_name} en {event.name}", row,
                      event_id=event.pk, reason=Outcome.EXPIRED.value, **_who(row))
        moved["released"] += 1
    if list_block(event, page):
        return moved
    waiting = rows.filter(status=WAITING).order_by("waiting_since", "pk")
    if page.capacity is not None:
        free = page.capacity - taken(event)
        if free <= 0:
            return moved
        waiting = waiting[:free]
    deadline = now + timedelta(hours=policy.waiting_offer_hours(event.church))
    for row in waiting:
        row.event = event
        row.status, row.offer_expires_at = OFFERED, deadline
        row.save(update_fields=["status", "offer_expires_at"])
        record_church(event.church, None, "evento_lista_oferta",
                      f"Se ofreció un lugar de {event.name} a {row.display_name}, que seguía en la lista de espera", row,
                      event_id=event.pk, **_who(row))
        _announce_offer(row, base_url)
        moved["offered"] += 1
    return moved


def advance(event: Event, *, base_url: str | None = None, now=None) -> dict:
    """Move the waiting list of one event under its page lock (``_advance``): what the command and
    the screens that read the list call."""
    with transaction.atomic():
        return _advance(event, _locked_page(event.pk), base_url=base_url, now=now)


def expire_offers(*, apply: bool = False, base_url: str | None = None, now=None) -> dict:
    """Every event with an offer past its deadline (``manage.py expire_waiting_offers`` and the
    scheduler's pass). Counts only: ``events``, ``expired`` and, when it writes, ``released``,
    ``offered`` (the places that went to the next in line) and ``failed`` (events whose list could
    not be moved). ``apply=False`` writes nothing. Idempotent.

    Only events of a church that uses the ``community`` module. While the module is off nothing of
    that church is counted, expires, is offered or is mailed: the list waits as it is, and an offer
    whose deadline passed meanwhile is released on the first pass after the module is back.

    One event never stops the next: an event that fails keeps its own transaction's changes undone,
    is counted in ``failed`` and logged by its error's class name."""
    now = now or timezone.now()
    due = EventRegistration.objects.filter(status=OFFERED, offer_expires_at__lte=now)
    event_ids = sorted(set(due.values_list("event_id", flat=True)))
    totals = {"events": 0, "expired": 0, "released": 0, "offered": 0, "failed": 0}
    if not event_ids:
        return totals
    events = [
        event for event in Event.objects.filter(pk__in=event_ids).select_related("church").order_by("pk")
        if flags.enabled(event.church, "community")
    ]
    totals["events"] = len(events)
    totals["expired"] = due.filter(event__in=events).count()
    if apply:
        for event in events:
            try:
                moved = advance(event, base_url=base_url, now=now)
            except Exception as error:  # noqa: BLE001 - the other events' lists still move
                totals["failed"] += 1
                logger.error("The waiting list of an event could not be moved: %s", type(error).__name__)
                continue
            for key, value in moved.items():
                totals[key] += value
    return totals


def accept_offer(registration: EventRegistration, actor, *, base_url: str | None = None) -> EventRegistration:
    """"Aceptar mi lugar": the person takes the place that was offered to them. Accepting is a
    sign-up, not a confirmation: ``standing`` decides, exactly as for ``register``, so in an event
    that protects minors a minor waits for a guardian's authorization and an adult whose age the
    team has not verified waits for that. The place was held by the offer, so the capacity is not
    asked again. Idempotent for who is already in. An offer past its deadline is released here (and
    the place offered to the next one) and the person reads ``OFFER_OVER``. While the list does not
    move (``list_block``: frozen, or the event started) the offer cannot be accepted and the person
    reads why; the closing date of the sign-up does not stop an acceptance."""
    if registration.person_id is None:
        raise AgendaError(NO_OFFER, code="no_offer")
    refusal = None
    with transaction.atomic():
        person = _fresh(registration.person)
        page = _locked_page(registration.event_id)
        registration = _locked(registration.pk)
        event = registration.event
        was_offered = registration.status == OFFERED
        _advance(event, page, base_url=base_url)
        registration = _locked(registration.pk)
        if registration.status in HOLDING:
            return registration
        if registration.status != OFFERED:
            # Not raised in here: the release above has to be kept.
            refusal = AgendaError(OFFER_OVER, code="offer_over") if was_offered else AgendaError(NO_OFFER, code="no_offer")
        else:
            block = list_block(event, page)
            if block == "terminado":
                raise AgendaError(ENDED, code="ended")
            if block == "empezo":
                raise AgendaError(LIST_STARTED, code="started")
            if block:
                raise AgendaError(LIST_FROZEN, code="frozen")
            registration.person = person
            where = standing(event, page, person=person, said_minor=registration.guest_is_minor,
                             consent=getattr(registration, "consent", None))
            registration.status, registration.pending_reason = where
            registration.attended, registration.checked_in_at, registration.checked_in_by = None, None, None
            registration.waiting_since, registration.offer_expires_at, registration.waiting_outcome = None, None, ""
            registration.save(update_fields=[*SETTLED_FIELDS, *LIST_FIELDS])
            record_church(event.church, actor, "evento_lista_aceptada",
                          f"{person.full_name} aceptó el lugar que se le ofreció en {event.name}{PENDING_SUFFIX[where.reason]}",
                          registration, event_id=event.pk, standing=where.reason or where.status, **_who(registration))
    if refusal is not None:
        raise refusal
    return registration


def _take_off_the_list(registration: EventRegistration, actor, *, by_admin: bool, base_url: str | None) -> EventRegistration:
    with transaction.atomic():
        page = _locked_page(registration.event_id)
        registration = _locked(registration.pk)
        if registration.status not in LISTED:
            return registration
        event = registration.event
        had_offer = registration.is_offered
        if by_admin:
            outcome, action = Outcome.REMOVED, "evento_lista_retirada"
            summary = f"Quitó a {registration.display_name} de la lista de espera de {event.name}"
        elif had_offer:
            outcome, action = Outcome.DECLINED, "evento_lista_salida"
            summary = f"{registration.display_name} no aceptó el lugar que se le ofreció en {event.name}"
        else:
            outcome, action = Outcome.LEFT, "evento_lista_salida"
            summary = f"{registration.display_name} salió de la lista de espera de {event.name}"
        _off_the_list(registration, outcome, timezone.now())
        record_church(event.church, actor, action, summary, registration, event_id=event.pk, reason=outcome.value,
                      **_who(registration))
        _advance(event, page, base_url=base_url)
    return registration


def leave_waiting(registration: EventRegistration, actor, *, base_url: str | None = None) -> EventRegistration:
    """The person leaves the list, or lets the offered place go: it goes to the next in line."""
    return _take_off_the_list(registration, actor, by_admin=False, base_url=base_url)


def remove_from_waiting(registration: EventRegistration, actor, *, base_url: str | None = None) -> EventRegistration:
    """"Quitar de la lista de espera": the one thing the administration does to the list. Audited
    with a fixed reason. There is no function that moves somebody up the list or confirms them from it."""
    return _take_off_the_list(registration, actor, by_admin=True, base_url=base_url)


# ---- the administration's writes ----------------------------------------------------------------
PAGE_FIELDS = (
    "published", "description", "starts_at", "registration", "capacity", "registration_closes_on",
    "requires_guardian_consent", "waiting_list", "public",
)


def save_page(event: Event, actor, *, base_url: str | None = None, **fields) -> EventPage:
    """Create or change the page of ``event``. ``fields`` are the ones of ``PAGE_FIELDS``; the ones
    left out keep their value. Publishing an event that has no first day is refused: nobody would
    ever see it.

    While the page asks for a guardian's authorization, saving it settles EVERY registration that
    holds a place with ``standing``, unknown dates of birth included: nothing stays confirmed by
    default, and a row that starts waiting loses its attendance mark (decision 0015, point 3).
    Turning the rule off confirms nobody: who was waiting keeps waiting.

    More places (or a page published again) move the waiting list: each free place is OFFERED to
    who is first in line. Nobody on the list is signed up by an edit of the page."""
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
        resettle_event(event, page, actor)
        _advance(event, page, base_url=base_url)
        if page.published and not was_published:
            action, summary = "evento_publicado", f"Publicó el evento {event.name} para la iglesia"
        elif was_published and not page.published:
            action, summary = "evento_pagina", f"Dejó de publicar el evento {event.name}"
        else:
            action, summary = "evento_pagina", f"Modificó la página del evento {event.name}"
        record_church(event.church, actor, action, summary, page, event_id=event.pk)
    return page


def record_attendance(event: Event, present_ids, actor, shown_ids=None) -> Attendance:
    """Who came, among the confirmed registrations; ``present_ids`` are registration ids (a visitor
    has no person). Saving again replaces the answer of everybody: who is not marked did not come.
    A registration that waits for a guardian is not on this list and cannot be marked. The audit
    row carries the count, never the names."""
    if not has_started(event):
        raise AgendaError("El evento todavía no empieza. La asistencia se registra desde su primer día.", code="not_started")
    wanted = {int(value) for value in present_ids if str(value).isdigit()}
    # ``shown_ids``: the rows the screen listed. Somebody registered meanwhile by another person of
    # the team was not on it, and keeps the mark they have.
    shown = None if shown_ids is None else {int(value) for value in shown_ids if str(value).isdigit()}
    with transaction.atomic():
        rows = list(EventRegistration.objects.select_for_update().filter(event=event, status=ACTIVE))
        now = timezone.now()
        present = 0
        for row in rows:
            if shown is not None and row.pk not in shown:
                present += bool(row.attended)
                continue
            came = row.pk in wanted
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


# ---- somebody who arrived -----------------------------------------------------------------------
CONSENT_FIELDS = (
    "guardian_name", "relationship", "method", "guardian_phone", "allows_participation", "allows_transport", "allows_photos",
)


def _clean_consent(fields: dict) -> dict:
    unknown = sorted(set(fields) - set(CONSENT_FIELDS))
    if unknown:
        raise ValueError(f"Unknown consent fields: {', '.join(unknown)}.")
    clean = {
        "guardian_name": " ".join(str(fields.get("guardian_name", "")).split())[:160],
        "relationship": fields.get("relationship", ""),
        "method": fields.get("method", ""),
        "guardian_phone": str(fields.get("guardian_phone", "")).strip()[:40],
        "allows_participation": fields.get("allows_participation") is True,
        "allows_transport": fields.get("allows_transport") is True,
        "allows_photos": fields.get("allows_photos") is True,
    }
    if not clean["guardian_name"]:
        raise AgendaError("Escribe el nombre de quien autoriza.", code="consent")
    if clean["relationship"] not in GuardianConsent.Relationship.values or clean["method"] not in GuardianConsent.Method.values:
        raise AgendaError("Elige quién autoriza y cómo lo hizo.", code="consent")
    if not clean["allows_participation"]:
        # Transport and photographs are choices; taking part is what an authorization is.
        raise AgendaError("Marca que autoriza que participe en este evento: sin eso no hay autorización que registrar.",
                          code="consent")
    return clean


def _write_consent(registration: EventRegistration, actor, clean: dict) -> GuardianConsent:
    """Store what a guardian said for one registration and its audit row: ids and the method, never
    the guardian's name or phone. It is recorded under the current version of the words
    (``people.policy.consent_policy_version``). Whether it confirms is ``GuardianConsent.confirms``."""
    event = registration.event
    consent, _ = GuardianConsent.objects.update_or_create(
        registration=registration,
        defaults={**clean, "recorded_by": actor, "recorded_at": timezone.now(), "revoked_at": None, "revoked_by": None,
                  "policy_version": policy.consent_policy_version(event.church)},
    )
    registration.consent = consent
    verb = "Registró la autorización" if consent.confirms else "Anotó un contacto, que no confirma, sobre la autorización"
    record_church(event.church, actor, "evento_autorizacion", f"{verb} de {registration.display_name} para {event.name}",
                  registration, event_id=event.pk, method=consent.method, **_who(registration))
    return consent


def walkup_blocks(event: Event, page: EventPage | None, taken_count: int, today=None) -> list[str]:
    """The rules of the page that stand between somebody who arrived and the list, as keys of
    ``WALKUP_RULES``, in the order the screen says them. Empty: the team just writes them down."""
    today = today or timezone.localdate()
    if page is None:
        return ["sin_pagina"]
    blocks = []
    if not page.published or event.status != Event.Status.ACTIVE:
        blocks.append("sin_publicar")
    if not page.registration_open:
        blocks.append("sin_inscripcion")
    elif page.registration_closes_on and page.registration_closes_on < today:
        blocks.append("cerro")
    if page.capacity is not None and taken_count >= page.capacity:
        blocks.append(FULL_RULE)
    return blocks


def register_walkup(event: Event, actor, *, person=None, first_name: str = "", last_name: str = "", phone: str = "",
                    is_minor: bool = False, seen_adult: bool = False, consent: dict | None = None, override=(),
                    override_reason: str = "", base_url: str | None = None) -> EventRegistration:
    """Somebody arrived and the team writes them down: a person of the church, or a visitor by
    name (no account, no ``Person``). From the event's first day on.

    A walk-up respects the page like a sign-up does (``walkup_blocks``): no page or not published,
    no sign-up from Emmaus, sign-up closed, no places left. The administration may go over each of
    those on purpose: ``override`` names the rules (keys of ``WALKUP_RULES``; the capacity,
    ``llena``, only when it is named) and ``override_reason`` is one of ``WalkupOverrideReason``.
    Both go to the audit row as fixed words.

    The capacity counts a place offered to somebody of the waiting list as taken
    (``taken``: confirmed + pending + offered), so an override adds a place over the capacity and
    never uses the offered one: who has the offer can still accept it. Somebody who is on the list
    is not written down from here: the administration first takes them off the list, on purpose.

    The minors' rule is never overridden: ``standing`` decides, whatever ``override`` says. A row
    that waits is not marked as present. ``is_minor`` and ``seen_adult`` are the team's word at the
    door, one or the other: all there is for a visitor (with neither, in an event that asks, the
    visitor waits for the team to say). For a person of the church ``is_minor`` counts only while
    the date of birth is unknown and ``seen_adult`` never counts: the record decides.
    ``consent`` (the fields of ``CONSENT_FIELDS``) records the authorization in this same step."""
    if not has_started(event):
        raise AgendaError("El evento todavía no empieza. Aquí registras a quien llega desde su primer día.", code="not_started")
    first_name, last_name, phone = " ".join(first_name.split()), " ".join(last_name.split()), phone.strip()
    said_minor = bool(is_minor)
    if said_minor and seen_adult:
        raise AgendaError("Elige una sola: es mayor de edad o es menor de edad.", code="age")
    if person is not None:
        if person.church_id != event.church_id or person.merged_into_id is not None:
            raise AgendaError("Esta persona no es de la iglesia del evento.", code="other_church")
        first_name = last_name = phone = ""
        seen_adult = False
    elif not first_name:
        raise AgendaError("Escribe el nombre de quien llegó.", code="no_name")
    clean = _clean_consent(consent) if consent else None
    now = timezone.now()
    with transaction.atomic():
        if person is not None:
            person = _fresh(person)  # the date of birth as it is now, not as the caller read it
            said_minor = said_minor and person.birth_date is None
            is_minor = is_minor_at(event, person.birth_date) if person.birth_date is not None else said_minor
        if clean and not is_minor:
            raise AgendaError("La autorización es solo para menores de edad. Marca «Es menor de edad».", code="consent")
        page = EventPage.objects.select_for_update().filter(event=event).first()
        _advance(event, page, base_url=base_url)
        registration = None
        if person is not None:
            registration = (
                EventRegistration.objects.select_for_update(of=("self",)).select_related("consent")
                .filter(event=event, person=person).first()
            )
            if registration is not None and registration.status in HOLDING:
                raise AgendaError(f"{person.full_name} ya está en la lista de inscritos.", code="already")
            if registration is not None and registration.status in LISTED:
                raise AgendaError(
                    f"{person.full_name} está en la lista de espera de este evento. Si la vas a recibir de todos "
                    "modos, primero quítala de la lista de espera en Inscritos.", code="listed",
                )
        blocks = walkup_blocks(event, page, taken(event))
        allowed = set(override)
        for rule in blocks:
            if rule not in allowed:
                raise AgendaError(WALKUP_RULES[rule], code="full" if rule == FULL_RULE else rule)
        if blocks and override_reason not in WalkupOverrideReason.values:
            raise AgendaError("Elige por qué la registras de todos modos.", code="override_reason")
        # What a guardian said in this step, or what this row already had: ``standing`` reads it.
        evidence = GuardianConsent(**clean) if clean else (getattr(registration, "consent", None) if registration else None)
        where = standing(event, page, person=person, said_minor=said_minor, age_checked=seen_adult, consent=evidence)
        waits = where.status == PENDING
        values = {
            "status": where.status,
            "pending_reason": where.reason,
            "registered_by": actor,
            "cancelled_at": None,
            "annulled_reason": "",
            "guest_is_minor": said_minor,
            "guest_age_checked": bool(seen_adult),
            "attended": None if waits else True,
            "checked_in_at": None if waits else now,
            "checked_in_by": None if waits else actor,
            # A row that once left the waiting list starts clean, as in ``register``: what the
            # person reads about the list is not a leftover of another time.
            "waiting_since": None,
            "offer_expires_at": None,
            "waiting_outcome": "",
        }
        if registration is None:
            registration = EventRegistration.objects.create(
                event=event, person=person, guest_first_name=first_name[:120], guest_last_name=last_name[:120],
                guest_phone=phone[:40], **values,
            )
        else:
            for name, value in values.items():
                setattr(registration, name, value)
            registration.save(update_fields=list(values))
        registration.event = event
        summary = f"Registró a {registration.display_name}, que llegó a {event.name}{PENDING_SUFFIX[where.reason]}"
        metadata = _who(registration)
        if person is None or said_minor:
            metadata["minor"] = said_minor  # the team's word at the door, kept as it was said
        if seen_adult:
            metadata["age_checked"] = True
        if blocks:
            summary += "; pasó por encima de una regla del evento"
            metadata.update(override=blocks, reason=override_reason)
        record_church(event.church, actor, "evento_registro_puerta", summary, registration, event_id=event.pk, **metadata)
        if clean:
            _write_consent(registration, actor, clean)
    return registration


def annul_walkup(registration: EventRegistration, actor, reason: str, *, base_url: str | None = None) -> EventRegistration:
    """"Anular registro": the team wrote somebody down at the door by mistake. Only a row the team
    wrote (a visitor, or a person of the church it registered); who signed up by themselves
    cancels by themselves. The row frees its place, leaves every list and every count, and stays in
    the database as ``cancelada`` with the reason, for the audit. An authorization recorded on the
    row is withdrawn with it."""
    if reason not in EventRegistration.AnnulReason.values:
        raise AgendaError("Elige por qué anulas este registro.", code="reason")
    with transaction.atomic():
        page = _locked_page(registration.event_id)
        registration = _locked(registration.pk)
        if not registration.is_walkup or registration.status not in HOLDING:
            raise AgendaError("Este registro no se puede anular desde aquí.", code="not_walkup")
        event = registration.event
        registration.status = CANCELLED
        registration.pending_reason = ""
        registration.annulled_reason = reason
        registration.cancelled_at = timezone.now()
        registration.attended, registration.checked_in_at, registration.checked_in_by = None, None, None
        registration.save(update_fields=[*SETTLED_FIELDS, "annulled_reason", "cancelled_at"])
        # The row was a mistake, and so is what was recorded on it: an authorization written for
        # the wrong minor must not confirm them the day they sign up or arrive for real.
        consent = registration.recorded_consent
        if consent is not None:
            consent.revoked_at, consent.revoked_by = timezone.now(), actor
            consent.save(update_fields=["revoked_at", "revoked_by"])
        record_church(event.church, actor, "evento_registro_anulado",
                      f"Anuló el registro de {registration.display_name} en {event.name}",
                      registration, event_id=event.pk, reason=reason, **_who(registration))
        _advance(event, page, base_url=base_url)
    return registration


def confirm_guest_age(registration: EventRegistration, actor, *, is_minor: bool) -> EventRegistration:
    """The team says what it saw of a visitor who waits for that (written down while the event did
    not ask): "es mayor de edad" confirms the row, "es menor de edad" sends it on to wait for a
    guardian's authorization. Audited with ids and the one word."""
    with transaction.atomic():
        registration = _locked(registration.pk)
        if registration.person_id is not None or registration.waits_for != AGE:
            raise AgendaError("Esta visita no está esperando que confirmes su edad.", code="not_age")
        event = registration.event
        registration.guest_is_minor, registration.guest_age_checked = bool(is_minor), not is_minor
        _settle(registration, EventPage.objects.filter(event=event).first(), actor, assume_rule=True)
        registration.save(update_fields=[*SETTLED_FIELDS, "guest_is_minor", "guest_age_checked"])
        word = "menor" if is_minor else "mayor"
        record_church(event.church, actor, "evento_visita_edad",
                      f"Dijo que {registration.display_name}, visita de {event.name}, es {word} de edad",
                      registration, event_id=event.pk, minor=bool(is_minor), **_who(registration))
    return registration


def confirm_age(registration: EventRegistration, actor, method: str, birth_date: date | None = None) -> EventRegistration:
    """"Confirmar edad": the team verified the age of who waits for it
    (``people.services.verify_birth_date``), and the row is settled again: an adult is confirmed, a
    minor goes on to wait for a guardian's authorization."""
    with transaction.atomic():
        if registration.person_id is not None:
            _fresh(registration.person)  # the person first, then the row: the order every other write locks in
        registration = _locked(registration.pk)
        if registration.person_id is None or registration.waits_for != AGE:
            raise AgendaError("Esta inscripción no está esperando que confirmes la edad.", code="not_age")
        try:
            # Its ``age_facts_changed`` settles every registration of the person where the rule is on.
            people_services.verify_birth_date(registration.person, actor, method, birth_date)
        except people_services.PeopleError as error:
            raise AgendaError(error.message, code=error.code) from error
        registration = _locked(registration.pk)
        _review(registration, EventPage.objects.filter(event=registration.event).first(), actor, assume_rule=True)
    return registration


# ---- the authorization of a guardian ------------------------------------------------------------
def record_consent(registration: EventRegistration, actor, **fields) -> EventRegistration:
    """What a mother, father or guardian said, for a row that waits for it. ``fields`` are the ones
    of ``CONSENT_FIELDS``. A signed form or an in-person verification confirms the row (and marks
    as present somebody the team wrote down at the door); a call or a message is kept as a note of
    contact and the row keeps waiting. Check ``is_active`` on what comes back."""
    clean = _clean_consent(fields)
    with transaction.atomic():
        registration = _locked(registration.pk)
        if not registration.is_pending:
            raise AgendaError("Esta inscripción no está pendiente de autorización.", code="not_pending")
        if registration.waits_for == AGE:
            raise AgendaError("Primero confirma su edad: la autorización es solo para menores de edad.", code="age_first")
        _write_consent(registration, actor, clean)
        _settle(registration, EventPage.objects.filter(event=registration.event).first(), actor, assume_rule=True)
        registration.save(update_fields=SETTLED_FIELDS)
    return registration


def revoke_consent(registration: EventRegistration, actor) -> EventRegistration:
    """The authorization no longer holds: the registration waits again, keeps its place and is no
    longer counted as present."""
    with transaction.atomic():
        registration = _locked(registration.pk)
        consent = registration.recorded_consent
        if consent is None or not registration.is_active:
            raise AgendaError("Esta inscripción no tiene una autorización vigente.", code="no_consent")
        consent.revoked_at = timezone.now()
        consent.revoked_by = actor
        consent.save(update_fields=["revoked_at", "revoked_by"])
        event = registration.event
        _settle(registration, EventPage.objects.filter(event=event).first(), actor, assume_rule=True)
        registration.save(update_fields=SETTLED_FIELDS)
        record_church(event.church, actor, "evento_autorizacion_retirada",
                      f"Retiró la autorización de {registration.display_name} para {event.name}",
                      registration, event_id=event.pk, method=consent.method, **_who(registration))
    return registration


# ---- a visitor becomes a person of the church ---------------------------------------------------
def _digits(phone: str) -> str:
    return "".join(char for char in phone if char.isdigit())


def similar_people(registration: EventRegistration, limit: int = 5) -> list[Person]:
    """People of the event's church who may be this visitor already: the same phone, the whole
    name inside theirs, or two words of the name in common. A hint for a human; nothing is ever
    linked by name alone."""
    tokens = set(normalize_name(registration.display_name).split())
    phone = _digits(registration.guest_phone)
    people = Person.objects.filter(church_id=registration.event.church_id, merged_into__isnull=True)
    match = Q()
    for token in tokens:
        match |= Q(normalized_name__contains=token)
    if phone:
        match |= ~Q(phone="")
    found = []
    # A hint beside one visitor, five names at most on purpose: the screen calls them "parecidas", not a list of the
    # church, and "Es alguien nuevo" stays under them. 500 bounds the work in a very large church.
    for person in people.filter(match).order_by("first_name", "last_name", "id")[:500]:
        theirs = set(person.normalized_name.split())
        same_phone = bool(phone) and _digits(person.phone) == phone
        if same_phone or (tokens and tokens <= theirs) or len(tokens & theirs) >= 2:
            found.append(person)
    return found[:limit]


def promote_guest(registration: EventRegistration, actor, *, existing=None) -> Person:
    """"Agregar a personas de la iglesia": the visitor of this row becomes a ``Person`` (created by
    ``people.services.save_person``, so it is audited like any other), or is recognized as
    ``existing``, a person the administration picked. The row keeps its place and the guest fields
    are cleared. Only for a church that uses the ``people`` module.

    In an event that asks for a guardian's authorization the row now belongs to somebody the church
    has a record of, so ``standing`` settles it with what that record says: an unknown or unverified
    date of birth waits for the team to confirm the age. What the team said at the door ("es menor
    de edad") stays with the row while the date is unknown, and it is never contradicted silently:
    a visitor marked as a minor cannot be recognized as an adult."""
    with transaction.atomic():
        if existing is not None:
            existing = _fresh(existing)  # the person first, then the row: the module's lock order
        registration = _locked(registration.pk)
        event = registration.event
        if not flags.enabled(event.church, "people"):
            raise AgendaError("Tu iglesia no usa Personas en Emmaus.", code="no_people")
        if registration.person_id is not None:
            raise AgendaError("Esta inscripción ya es de una persona de la iglesia.", code="not_guest")
        if existing is not None:
            if existing.church_id != event.church_id or existing.merged_into_id is not None:
                raise AgendaError("Esta persona no es de la iglesia del evento.", code="other_church")
            if EventRegistration.objects.filter(event=event, person=existing).exists():
                raise AgendaError(
                    f"{existing.full_name} ya tiene su propia inscripción a este evento. Revisa la lista.", code="already"
                )
            if registration.guest_is_minor and existing.birth_date and not is_minor_at(event, existing.birth_date):
                raise AgendaError(
                    f"En la entrada registraron a esta visita como menor de edad, y {existing.full_name} es mayor de "
                    "edad según su fecha de nacimiento. Revisa si es la misma persona.", code="age_conflict",
                )
            person = existing
        else:
            person = people_services.save_person(
                Person(
                    church=event.church, first_name=registration.guest_first_name, last_name=registration.guest_last_name,
                    phone=registration.guest_phone, created_from=Person.CreatedFrom.EVENT,
                    preferred_contact_method=Person.ContactMethod.PHONE if registration.guest_phone else Person.ContactMethod.EMAIL,
                ),
                actor, created=True,
            )
        registration.person = person
        registration.guest_first_name = registration.guest_last_name = registration.guest_phone = ""
        registration.guest_is_minor = registration.guest_is_minor and person.birth_date is None
        _settle(registration, EventPage.objects.filter(event=event).first(), actor)
        registration.save(update_fields=["person", "guest_first_name", "guest_last_name", "guest_phone", "guest_is_minor",
                                         *SETTLED_FIELDS])
        record_church(event.church, actor, "evento_visita_persona",
                      f"Agregó a {person.full_name}, visita de {event.name}, a las personas de la iglesia",
                      registration, event_id=event.pk, **_who(registration))
    return person


# ---- somebody to walk with a visitor ------------------------------------------------------------
def came_as_visitor(registration: EventRegistration) -> bool:
    """The row is of a person the church has because they came to an event as a visitor."""
    return registration.person_id is not None and registration.person.created_from == Person.CreatedFrom.EVENT


def open_follow_ups(church, person_ids) -> dict[int, int]:
    """For each of these people of ``church`` who has an open follow-up: its id (the oldest one)."""
    from care.models import FollowUp

    rows = FollowUp.objects.filter(church=church, person_id__in=list(person_ids), status__in=FollowUp.OPEN)
    found: dict[int, int] = {}
    for person_id, pk in rows.order_by("created_at", "id").values_list("person_id", "id"):
        found.setdefault(person_id, pk)
    return found


def ask_to_accompany(registration: EventRegistration, actor):
    """"Pedir que alguien le acompañe": one follow-up about a person who came as a visitor, with the
    fixed reason "Vino por primera vez" and nobody responsible yet, through ``care.services.create``
    (which audits it, with the person's id and no reason). No free text. While the person has an open
    follow-up, of any reason, no other is created. Returns ``(follow_up, created)``. The caller is
    the church's administration and is never the person the follow-up is about."""
    from care import services as care
    from care.models import FollowUp

    with transaction.atomic():
        # The person first, then the row (the module's lock order). Whose row it is, is read from the
        # database (the caller's object may be older). Locked: two taps at once create one follow-up.
        person_id = EventRegistration.objects.filter(pk=registration.pk).values_list("person_id", flat=True).first()
        if person_id is None:
            raise AgendaError("Esta inscripción no es de alguien que llegó como visita.", code="not_visitor")
        person = Person.objects.select_for_update().get(pk=person_id)
        registration = _locked(registration.pk)
        event = registration.event
        if registration.status not in HOLDING or registration.person_id != person.pk or not came_as_visitor(registration):
            raise AgendaError("Esta inscripción no es de alguien que llegó como visita.", code="not_visitor")
        if person.user_id is not None and person.user_id == getattr(actor, "pk", None):
            raise AgendaError("No puedes pedir un seguimiento sobre ti.", code="self")
        existing = FollowUp.objects.filter(church=event.church, person=person, status__in=FollowUp.OPEN).order_by("created_at", "id").first()
        if existing is not None:
            return existing, False
        try:
            return care.create(event.church, actor, person=person, reason=FollowUp.Reason.VISIT.value), True
        except care.CareError as error:
            raise AgendaError(error.message, code=error.code) from error


# ---- frequent questions and the programme -------------------------------------------------------
def save_faq(event: Event, actor, faq: EventFaq | None = None, *, question: str, answer: str, position: int = 1) -> EventFaq:
    question, answer = " ".join(question.split()), answer.strip()
    if not question or not answer:
        raise AgendaError("Escribe la pregunta y su respuesta.", code="empty")
    with transaction.atomic():
        created = faq is None
        faq = faq or EventFaq(event=event)
        if faq.event_id != event.pk:
            raise ValueError("The question belongs to another event.")
        faq.question, faq.answer, faq.position = question[:200], answer, position
        faq.save()
        verb = "Agregó una pregunta frecuente a" if created else "Modificó una pregunta frecuente de"
        record_church(event.church, actor, "evento_preguntas", f"{verb} {event.name}", faq, event_id=event.pk)
    return faq


def delete_faq(faq: EventFaq, actor) -> None:
    event = faq.event
    with transaction.atomic():
        record_church(event.church, actor, "evento_preguntas", f"Borró una pregunta frecuente de {event.name}", faq,
                      event_id=event.pk)
        faq.delete()


# ---- public notices (issue #96) -------------------------------------------------------------------
PUBLIC_NOTICES_SHOWN = 20


def public_notices(event: Event):
    """What a reader sees: the notices in force, newest first, the latest ones only. No table of people."""
    return EventNotice.objects.filter(event=event, retired_at__isnull=True).order_by("-published_at", "-id")[:PUBLIC_NOTICES_SHOWN]


def visible_text(text: str) -> str:
    """``text`` without what nobody sees: spaces, line breaks, control and format characters (a zero-width space,
    a byte-order mark). Only to tell whether somebody wrote anything; what is stored is what was typed."""
    return "".join(ch for ch in text if unicodedata.category(ch)[0] not in "ZC")


def _locked_notice(notice: EventNotice) -> EventNotice:
    """The row as it is now, locked: what the caller holds may be older than what another administrator did."""
    return EventNotice.objects.select_for_update().select_related("event__church").get(pk=notice.pk)


def save_public_notice(event: Event, actor, notice: EventNotice | None = None, *, title: str, body: str) -> EventNotice:
    """Publish a notice, or change one in force. The audit row says which notice and of which event, never its text.
    A notice somebody retired meanwhile is gone (``AgendaError`` with the code ``retired``): it is never brought back."""
    title, body = " ".join(title.split()), body.strip()
    if not visible_text(title) or not visible_text(body):
        raise AgendaError("Escribe el título y el aviso.", code="empty")
    title = title[:120]
    with transaction.atomic():
        if notice is None:
            notice = EventNotice.objects.create(event=event, created_by=actor, title=title, body=body)
            record_church(event.church, actor, "evento_aviso_publico_publicado", f"Publicó un aviso público en {event.name}",
                          notice, event_id=event.pk)
            return notice
        if notice.event_id != event.pk:
            raise ValueError("The notice belongs to another event.")
        notice = _locked_notice(notice)
        if notice.retired:
            raise AgendaError("Este aviso ya se retiró.", code="retired")
        if (notice.title, notice.body) == (title, body):
            return notice
        notice.title, notice.body, notice.edited_at = title, body, timezone.now()
        notice.save(update_fields=["title", "body", "edited_at", "updated_at"])
        record_church(event.church, actor, "evento_aviso_publico_modificado", f"Modificó un aviso público de {event.name}",
                      notice, event_id=event.pk)
    return notice


def retire_public_notice(notice: EventNotice, actor) -> EventNotice:
    """Take a notice off the pages. The row stays, for the administration only, until somebody deletes it."""
    with transaction.atomic():
        notice = _locked_notice(notice)
        if notice.retired:
            return notice
        event = notice.event
        notice.retired_at = timezone.now()
        notice.save(update_fields=["retired_at", "updated_at"])
        record_church(event.church, actor, "evento_aviso_publico_retirado", f"Retiró un aviso público de {event.name}", notice,
                      event_id=event.pk)
    return notice


def delete_public_notice(notice: EventNotice, actor) -> None:
    """Delete a retired notice for good: its text is gone. The audit row keeps which notice it was, by id."""
    with transaction.atomic():
        notice = _locked_notice(notice)
        if not notice.retired:
            raise AgendaError("Primero retira el aviso.", code="in_force")
        event = notice.event
        record_church(event.church, actor, "evento_aviso_publico_borrado", f"Borró un aviso público retirado de {event.name}",
                      notice, event_id=event.pk)
        notice.delete()


def schedule_day_error(event: Event, day: date) -> str | None:
    """Why ``day`` cannot be in the programme of ``event``, or None. An event without dates takes any day."""
    first, last = event.starts_on or event.ends_on, last_day(event)
    if first is None or first <= day <= last:
        return None
    if first == last:
        return "El evento es un solo día: elige ese día."
    return "Elige un día dentro de las fechas del evento."


def save_schedule_item(event: Event, actor, item: EventScheduleItem | None = None, *, day: date, starts_at,
                       title: str, ends_at=None, place: str = "") -> EventScheduleItem:
    title, place = " ".join(title.split()), " ".join(place.split())
    if not title:
        raise AgendaError("Escribe qué es.", code="empty")
    error = schedule_day_error(event, day)
    if error:
        raise AgendaError(error, code="day")
    if ends_at is not None and ends_at <= starts_at:
        raise AgendaError("La hora en que termina tiene que ser después de la hora en que empieza.", code="hours")
    with transaction.atomic():
        created = item is None
        item = item or EventScheduleItem(event=event)
        if item.event_id != event.pk:
            raise ValueError("The schedule item belongs to another event.")
        item.day, item.starts_at, item.ends_at, item.title, item.place = day, starts_at, ends_at, title[:120], place[:120]
        item.save()
        verb = "Agregó un momento al programa de" if created else "Modificó el programa de"
        record_church(event.church, actor, "evento_programa", f"{verb} {event.name}", item, event_id=event.pk)
    return item


def delete_schedule_item(item: EventScheduleItem, actor) -> None:
    event = item.event
    with transaction.atomic():
        record_church(event.church, actor, "evento_programa", f"Quitó un momento del programa de {event.name}", item,
                      event_id=event.pk)
        item.delete()


def schedule_by_day(event: Event) -> list[tuple[date, list[EventScheduleItem]]]:
    """The programme as the screens draw it: each day with its moments by hour."""
    days: dict[date, list[EventScheduleItem]] = {}
    for item in EventScheduleItem.objects.filter(event=event).order_by("day", "starts_at", "id"):
        days.setdefault(item.day, []).append(item)
    return list(days.items())
