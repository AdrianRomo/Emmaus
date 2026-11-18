"""Events for the people of the church: what a person may see, and every write.

Each write is one transaction that changes the row and stores one church audit row with ids and
counts only. Callers have already answered who may ask (``groups.views.community_view`` and
``church_admin_view``); these functions enforce the rules that hold whoever asks.

Nothing here creates an ``EventParticipant``, an ``EventMembership`` or a ``RoleAssignment``: a
registration says "I want to come" and nothing else.
"""

from __future__ import annotations

from collections import namedtuple
from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import Count, Q
from django.utils import timezone

from events.models import Event, normalize_name
from people import flags, policy
from people import services as people_services
from people.audit import record_church
from people.models import Person

from .models import EventFaq, EventPage, EventRegistration, EventScheduleItem, GuardianConsent

ACTIVE = EventRegistration.Status.ACTIVE
PENDING = EventRegistration.Status.PENDING
CANCELLED = EventRegistration.Status.CANCELLED
# Who holds a place: confirmed, or waiting for the team to verify an age or for a guardian's authorization.
HOLDING = EventRegistration.HOLDING
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
    """The places in use: a registration that waits for a guardian holds its place too."""
    return EventRegistration.objects.filter(event=event, status__in=HOLDING).count()


def age_on(birth_date: date, day: date) -> int:
    return day.year - birth_date.year - ((day.month, day.day) < (birth_date.month, birth_date.day))


def is_minor_at(event: Event, birth_date: date, today=None) -> bool:
    """Under 18 on the event's first day (today, for an event without one)."""
    return age_on(birth_date, event.starts_on or today or timezone.localdate()) < 18


def standing(event: Event, page: EventPage | None, *, person=None, said_minor: bool = False, consent=None,
             assume_rule: bool = False, today=None) -> Standing:
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
    | visitor the team did not mark as a minor              | confirmed          |

    ``person`` is None for a visitor: the team saw them at the door, and its word (``said_minor``) is
    all there is. For a person of the church ``said_minor`` counts only while the date of birth is
    unknown, and only towards "minor". ``consent`` is the row's ``GuardianConsent`` or None; what
    confirms is ``GuardianConsent.confirms``. An event that does not ask (``page``) confirms
    everybody; ``assume_rule`` answers as if it asked, for a row that is already waiting."""
    if not assume_rule and (page is None or not page.requires_guardian_consent):
        return CONFIRMED
    if person is None:
        minor = bool(said_minor)
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
        consent=getattr(registration, "consent", None), assume_rule=assume_rule,
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


def places_left(page: EventPage, taken_count: int) -> int | None:
    return None if page.capacity is None else max(page.capacity - taken_count, 0)


def state_of(event: Event, page: EventPage | None, registration, taken_count: int, today=None) -> str:
    """Where one person stands with one event, as one word the screens turn into a sentence:
    ``terminado``, ``inscrita``, ``pendiente`` (a minor whose guardian has not authorized yet),
    ``sin_publicar``, ``sin_inscripcion``, ``cerro``, ``llena`` or ``abierta``."""
    today = today or timezone.localdate()
    if has_ended(event, today):
        return "terminado"
    if registration is not None and registration.is_active:
        return "inscrita"
    if registration is not None and registration.is_pending:
        return "pendiente"
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
    ``my_registration`` (the confirmed registration or None), ``my_place`` (that one, or the one
    that waits for a guardian's authorization), ``taken`` and ``state``."""
    today = today or timezone.localdate()
    mine = {
        row.event_id: row
        for row in EventRegistration.objects.filter(person=person, status__in=HOLDING, event__church=church)
    }
    events = (
        Event.objects.filter(church=church, page__isnull=False)
        .filter(_not_ended(today))
        .filter(Q(page__published=True, status=Event.Status.ACTIVE) | Q(pk__in=list(mine)))
        .select_related("page")
        .annotate(taken=Count("registrations", filter=Q(registrations__status__in=HOLDING)))
    )
    events = sorted(events, key=lambda event: (event.starts_on or event.ends_on, event.name, event.pk))
    for event in events:
        event.my_place = mine.get(event.pk)
        event.my_registration = event.my_place if event.my_place is not None and event.my_place.is_active else None
        event.state = state_of(event, event.page, event.my_place, event.taken, today)
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
    """Sign ``person`` up. Idempotent for who is already in (confirmed or waiting for a guardian);
    after a cancellation the same row comes back. Places are counted with the page row locked, so
    the last one is given once.

    When the event asks for a guardian's authorization, ``standing`` decides whether the row is
    confirmed or waits (``pendiente``): for the team to verify the age, or for a guardian. An
    authorization of this same registration that still confirms survives a cancellation."""
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
        if registration is not None and registration.status in HOLDING:
            return registration
        if not page.registration_open:
            raise AgendaError(NO_REGISTRATION, code="no_registration")
        if page.registration_closes_on and page.registration_closes_on < today:
            raise AgendaError(REGISTRATION_OVER, code="registration_over")
        if page.capacity is not None and taken(event) >= page.capacity:
            raise AgendaError(FULL, code="full")
        where = standing(
            event, page, person=person, today=today,
            said_minor=registration is not None and registration.guest_is_minor,
            consent=getattr(registration, "consent", None) if registration is not None else None,
        )
        if registration is None:
            try:
                with transaction.atomic():
                    registration = EventRegistration.objects.create(
                        event=event, person=person, status=where.status, pending_reason=where.reason
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
            registration.save(update_fields=[*SETTLED_FIELDS, "cancelled_at", "annulled_reason", "registered_by"])
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


def cancel(registration: EventRegistration, actor) -> EventRegistration:
    """The person is no longer coming (confirmed or still waiting for a guardian). Not once the
    event has started: by then the church counts on the list it has. An authorization already
    recorded stays with the row."""
    with transaction.atomic():
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
    return registration


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


# ---- the administration's writes ----------------------------------------------------------------
PAGE_FIELDS = (
    "published", "description", "starts_at", "registration", "capacity", "registration_closes_on",
    "requires_guardian_consent",
)


def save_page(event: Event, actor, **fields) -> EventPage:
    """Create or change the page of ``event``. ``fields`` are the ones of ``PAGE_FIELDS``; the ones
    left out keep their value. Publishing an event that has no first day is refused: nobody would
    ever see it.

    While the page asks for a guardian's authorization, saving it settles EVERY registration that
    holds a place with ``standing``, unknown dates of birth included: nothing stays confirmed by
    default, and a row that starts waiting loses its attendance mark (decision 0015, point 3).
    Turning the rule off confirms nobody: who was waiting keeps waiting."""
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
        if page.requires_guardian_consent:
            held = (
                EventRegistration.objects.select_for_update(of=("self",)).filter(event=event, status__in=HOLDING)
                .select_related("person", "consent")
            )
            for row in held:
                row.event = event
                _review(row, page, actor)
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
                    is_minor: bool = False, consent: dict | None = None, override=(), override_reason: str = "",
                    ) -> EventRegistration:
    """Somebody arrived and the team writes them down: a person of the church, or a visitor by
    name (no account, no ``Person``). From the event's first day on.

    A walk-up respects the page like a sign-up does (``walkup_blocks``): no page or not published,
    no sign-up from Emmaus, sign-up closed, no places left. The administration may go over each of
    those on purpose: ``override`` names the rules (keys of ``WALKUP_RULES``; the capacity,
    ``llena``, only when it is named) and ``override_reason`` is one of ``WalkupOverrideReason``.
    Both go to the audit row as fixed words.

    The minors' rule is never overridden: ``standing`` decides, whatever ``override`` says. A row
    that waits is not marked as present. ``is_minor`` is the team's word: all there is for a
    visitor, and for a person of the church it counts only while the date of birth is unknown.
    ``consent`` (the fields of ``CONSENT_FIELDS``) records the authorization in this same step."""
    if not has_started(event):
        raise AgendaError("El evento todavía no empieza. Aquí registras a quien llega desde su primer día.", code="not_started")
    first_name, last_name, phone = " ".join(first_name.split()), " ".join(last_name.split()), phone.strip()
    said_minor = bool(is_minor)
    if person is not None:
        if person.church_id != event.church_id or person.merged_into_id is not None:
            raise AgendaError("Esta persona no es de la iglesia del evento.", code="other_church")
        first_name = last_name = phone = ""
        said_minor = said_minor and person.birth_date is None
        is_minor = is_minor_at(event, person.birth_date) if person.birth_date is not None else said_minor
    elif not first_name:
        raise AgendaError("Escribe el nombre de quien llegó.", code="no_name")
    clean = _clean_consent(consent) if consent else None
    if clean and not is_minor:
        raise AgendaError("La autorización es solo para menores de edad. Marca «Es menor de edad».", code="consent")
    now = timezone.now()
    with transaction.atomic():
        page = EventPage.objects.select_for_update().filter(event=event).first()
        registration = None
        if person is not None:
            registration = (
                EventRegistration.objects.select_for_update(of=("self",)).select_related("consent")
                .filter(event=event, person=person).first()
            )
            if registration is not None and registration.status in HOLDING:
                raise AgendaError(f"{person.full_name} ya está en la lista de inscritos.", code="already")
        blocks = walkup_blocks(event, page, taken(event))
        allowed = set(override)
        for rule in blocks:
            if rule not in allowed:
                raise AgendaError(WALKUP_RULES[rule], code="full" if rule == FULL_RULE else rule)
        if blocks and override_reason not in WalkupOverrideReason.values:
            raise AgendaError("Elige por qué la registras de todos modos.", code="override_reason")
        # What a guardian said in this step, or what this row already had: ``standing`` reads it.
        evidence = GuardianConsent(**clean) if clean else (getattr(registration, "consent", None) if registration else None)
        where = standing(event, page, person=person, said_minor=said_minor, consent=evidence)
        waits = where.status == PENDING
        values = {
            "status": where.status,
            "pending_reason": where.reason,
            "registered_by": actor,
            "cancelled_at": None,
            "annulled_reason": "",
            "guest_is_minor": said_minor,
            "attended": None if waits else True,
            "checked_in_at": None if waits else now,
            "checked_in_by": None if waits else actor,
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
        if blocks:
            summary += "; pasó por encima de una regla del evento"
            metadata.update(override=blocks, reason=override_reason)
        record_church(event.church, actor, "evento_registro_puerta", summary, registration, event_id=event.pk, **metadata)
        if clean:
            _write_consent(registration, actor, clean)
    return registration


def annul_walkup(registration: EventRegistration, actor, reason: str) -> EventRegistration:
    """"Anular registro": the team wrote somebody down at the door by mistake. Only a row the team
    wrote (a visitor, or a person of the church it registered); who signed up by themselves
    cancels by themselves. The row frees its place, leaves every list and every count, and stays in
    the database as ``cancelada`` with the reason, for the audit."""
    if reason not in EventRegistration.AnnulReason.values:
        raise AgendaError("Elige por qué anulas este registro.", code="reason")
    with transaction.atomic():
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
        record_church(event.church, actor, "evento_registro_anulado",
                      f"Anuló el registro de {registration.display_name} en {event.name}",
                      registration, event_id=event.pk, reason=reason, **_who(registration))
    return registration


def confirm_age(registration: EventRegistration, actor, method: str, birth_date: date | None = None) -> EventRegistration:
    """"Confirmar edad": the team verified the age of who waits for it
    (``people.services.verify_birth_date``), and the row is settled again: an adult is confirmed, a
    minor goes on to wait for a guardian's authorization."""
    with transaction.atomic():
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
