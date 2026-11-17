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
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from events.models import Event, normalize_name
from people import flags
from people import services as people_services
from people.audit import record_church
from people.models import Person

from .models import EventFaq, EventPage, EventRegistration, EventScheduleItem, GuardianConsent

ACTIVE = EventRegistration.Status.ACTIVE
PENDING = EventRegistration.Status.PENDING
CANCELLED = EventRegistration.Status.CANCELLED
# Who holds a place: confirmed, or a minor waiting for the authorization of a guardian.
HOLDING = EventRegistration.HOLDING

# The words a rule answers with. The screens say the same sentences (agenda.views.STATE_NOTICES).
NOT_PUBLISHED = "Este evento todavía no está publicado para la iglesia."
NO_REGISTRATION = "La inscripción a este evento no se hace desde Emmaus. Pregunta en tu iglesia."
REGISTRATION_OVER = "La inscripción a este evento ya cerró."
ENDED = "Este evento ya terminó."
FULL = "Ya no hay lugares."
STARTED = "El evento ya empezó: tu inscripción ya no se puede cancelar desde aquí. Avisa en tu iglesia."
BIRTH_DATE_NEEDED = "Para inscribirte a este evento necesitamos tu fecha de nacimiento."
PENDING_NEXT = "Tu inscripción queda pendiente hasta que tu mamá, papá o tutor la autorice con el equipo de la iglesia."
FULL_AT_THE_DOOR = (
    "Ya no hay lugares: el evento está lleno. Para registrar a alguien más, sube el número de lugares en "
    "Página del evento."
)

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
    """The places in use: a registration that waits for a guardian holds its place too."""
    return EventRegistration.objects.filter(event=event, status__in=HOLDING).count()


def age_on(birth_date: date, day: date) -> int:
    return day.year - birth_date.year - ((day.month, day.day) < (birth_date.month, birth_date.day))


def is_minor_at(event: Event, birth_date: date, today=None) -> bool:
    """Under 18 on the event's first day (today, for an event without one)."""
    return age_on(birth_date, event.starts_on or today or timezone.localdate()) < 18


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

    When the event asks for a guardian's authorization, the person's date of birth decides: unknown
    is refused with ``birth_date_needed`` (the screen asks for it), under 18 on the first day is
    registered as ``pendiente`` unless an authorization for this registration is already in force."""
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
        status = ACTIVE
        if page.requires_guardian_consent:
            if person.birth_date is None:
                raise AgendaError(BIRTH_DATE_NEEDED, code="birth_date_needed")
            authorized = registration is not None and registration.valid_consent is not None
            if is_minor_at(event, person.birth_date, today) and not authorized:
                status = PENDING
        if registration is None:
            try:
                with transaction.atomic():
                    registration = EventRegistration.objects.create(event=event, person=person, status=status)
            except IntegrityError:  # two taps at once: the other one created it
                return EventRegistration.objects.get(event=event, person=person)
        else:
            registration.status = status
            registration.cancelled_at = None
            registration.attended = None
            registration.checked_in_at = None
            registration.checked_in_by = None
            registration.registered_by = None
            registration.save(
                update_fields=["status", "cancelled_at", "attended", "checked_in_at", "checked_in_by", "registered_by"]
            )
        summary = f"{person.full_name} se inscribió a {event.name}"
        if status == PENDING:
            summary += "; queda pendiente de autorización"
        record_church(event.church, actor, "evento_inscripcion", summary, registration,
                      event_id=event.pk, person_id=person.pk)
    return registration


def set_birth_date(person, birth_date: date, actor):
    """The person says their own date of birth, once: only when the church does not know it yet.
    After that it is the administration's to correct (Personas), like the name."""
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
        registration.cancelled_at = timezone.now()
        registration.save(update_fields=["status", "cancelled_at"])
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
    """Who came, among the confirmed registrations; ``present_ids`` are registration ids (a visitor
    has no person). Saving again replaces the answer of everybody: who is not marked did not come.
    A registration that waits for a guardian is not on this list and cannot be marked. The audit
    row carries the count, never the names."""
    if not has_started(event):
        raise AgendaError("El evento todavía no empieza. La asistencia se registra desde su primer día.", code="not_started")
    wanted = {int(value) for value in present_ids if str(value).isdigit()}
    with transaction.atomic():
        rows = list(EventRegistration.objects.select_for_update().filter(event=event, status=ACTIVE))
        now = timezone.now()
        present = 0
        for row in rows:
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
CONSENT_FIELDS = ("guardian_name", "relationship", "method", "guardian_phone")


def _clean_consent(fields: dict) -> dict:
    unknown = sorted(set(fields) - set(CONSENT_FIELDS))
    if unknown:
        raise ValueError(f"Unknown consent fields: {', '.join(unknown)}.")
    clean = {
        "guardian_name": " ".join(str(fields.get("guardian_name", "")).split())[:160],
        "relationship": fields.get("relationship", ""),
        "method": fields.get("method", ""),
        "guardian_phone": str(fields.get("guardian_phone", "")).strip()[:40],
    }
    if not clean["guardian_name"]:
        raise AgendaError("Escribe el nombre de quien autoriza.", code="consent")
    if clean["relationship"] not in GuardianConsent.Relationship.values or clean["method"] not in GuardianConsent.Method.values:
        raise AgendaError("Elige quién autoriza y cómo lo hizo.", code="consent")
    return clean


def _write_consent(registration: EventRegistration, actor, clean: dict) -> GuardianConsent:
    """Store the authorization of one registration and its audit row: ids and the method, never
    the guardian's name or phone."""
    consent, _ = GuardianConsent.objects.update_or_create(
        registration=registration,
        defaults={**clean, "recorded_by": actor, "recorded_at": timezone.now(), "revoked_at": None, "revoked_by": None},
    )
    registration.consent = consent
    event = registration.event
    record_church(event.church, actor, "evento_autorizacion",
                  f"Registró la autorización de {registration.display_name} para {event.name}",
                  registration, event_id=event.pk, method=consent.method, **_who(registration))
    return consent


def register_walkup(event: Event, actor, *, person=None, first_name: str = "", last_name: str = "", phone: str = "",
                    is_minor: bool = False, consent: dict | None = None) -> EventRegistration:
    """Somebody arrived and the team writes them down: a person of the church, or a visitor by
    name (no account, no ``Person``). From the event's first day on, whatever the page says about
    signing up from Emmaus; never past the event's capacity.

    The row is confirmed and marked as present, unless the event asks for a guardian's
    authorization and this is a minor: then it is ``pendiente`` until the authorization is recorded
    (``consent``: the fields of ``CONSENT_FIELDS``, in this same step, or later with
    ``record_consent``). A minor is who the team says is one, or who the date of birth says."""
    if not has_started(event):
        raise AgendaError("El evento todavía no empieza. Aquí registras a quien llega desde su primer día.", code="not_started")
    first_name, last_name, phone = " ".join(first_name.split()), " ".join(last_name.split()), phone.strip()
    if person is not None:
        if person.church_id != event.church_id or person.merged_into_id is not None:
            raise AgendaError("Esta persona no es de la iglesia del evento.", code="other_church")
        first_name = last_name = phone = ""
        if person.birth_date is not None:
            is_minor = is_minor or is_minor_at(event, person.birth_date)
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
        if page is not None and page.capacity is not None and taken(event) >= page.capacity:
            raise AgendaError(FULL_AT_THE_DOOR, code="full")
        requires = page is not None and page.requires_guardian_consent
        authorized = clean is not None or (registration is not None and registration.valid_consent is not None)
        waits = requires and is_minor and not authorized
        values = {
            "status": PENDING if waits else ACTIVE,
            "registered_by": actor,
            "cancelled_at": None,
            "attended": None if waits else True,
            "checked_in_at": None if waits else now,
            "checked_in_by": None if waits else actor,
        }
        if registration is None:
            registration = EventRegistration.objects.create(
                event=event, person=person, guest_first_name=first_name[:120], guest_last_name=last_name[:120],
                guest_phone=phone[:40], guest_is_minor=bool(is_minor and person is None), **values,
            )
        else:
            for name, value in values.items():
                setattr(registration, name, value)
            registration.save(update_fields=list(values))
        registration.event = event
        summary = f"Registró a {registration.display_name}, que llegó a {event.name}"
        if waits:
            summary += "; queda pendiente de autorización"
        record_church(event.church, actor, "evento_registro_puerta", summary, registration,
                      event_id=event.pk, **_who(registration))
        if clean:
            _write_consent(registration, actor, clean)
    return registration


# ---- the authorization of a guardian ------------------------------------------------------------
def record_consent(registration: EventRegistration, actor, **fields) -> EventRegistration:
    """A mother, father or guardian authorized: the only way out of ``pendiente``. ``fields`` are
    the ones of ``CONSENT_FIELDS``. Somebody the team wrote down at the door is there, so the row
    is also marked as present."""
    clean = _clean_consent(fields)
    with transaction.atomic():
        registration = _locked(registration.pk)
        if not registration.is_pending:
            raise AgendaError("Esta inscripción no está pendiente de autorización.", code="not_pending")
        _write_consent(registration, actor, clean)
        registration.status = ACTIVE
        if registration.registered_by_id is not None and has_started(registration.event):
            registration.attended = True
            registration.checked_in_at = timezone.now()
            registration.checked_in_by = actor
        registration.save(update_fields=["status", "attended", "checked_in_at", "checked_in_by"])
    return registration


def revoke_consent(registration: EventRegistration, actor) -> EventRegistration:
    """The authorization no longer holds: the registration waits again, keeps its place and is no
    longer counted as present."""
    with transaction.atomic():
        registration = _locked(registration.pk)
        consent = registration.valid_consent
        if consent is None or not registration.is_active:
            raise AgendaError("Esta inscripción no tiene una autorización vigente.", code="no_consent")
        consent.revoked_at = timezone.now()
        consent.revoked_by = actor
        consent.save(update_fields=["revoked_at", "revoked_by"])
        registration.status = PENDING
        registration.attended = None
        registration.checked_in_at = None
        registration.checked_in_by = None
        registration.save(update_fields=["status", "attended", "checked_in_at", "checked_in_by"])
        event = registration.event
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
    ``existing``, a person the administration picked. The row keeps its status and its place; the
    guest fields are cleared. Only for a church that uses the ``people`` module."""
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
        registration.guest_is_minor = False
        registration.save(update_fields=["person", "guest_first_name", "guest_last_name", "guest_phone", "guest_is_minor"])
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
