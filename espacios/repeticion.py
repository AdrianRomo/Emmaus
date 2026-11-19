"""Bookings that repeat (issue #94, the part left out of the first slice).

A ``BookingSeries`` is a rule ("cada miércoles, de 19:00 a 21:00, hasta el 30 de mayo"), as
``agenda.EventSeries`` is. Its dates are ordinary ``Booking`` rows written ahead for a rolling window
(``WINDOW_WEEKS``, the window of the series of events) by ``generate``: when the series is approved,
and on every pass of the scheduler (``generate_due``, part of the job ``lo_que_se_repite``).

The rules, in words:

* **The clash rule is every booking's, and a series never goes around it.** Each date is written
  under the space's lock after reading what overlaps, exactly as ``services.request_booking`` does.
  A date whose hours are held by another booking is NOT written: the day is remembered as
  ``ocupado`` and listed on the series for the administration, which may book it with the existing
  audited override (``book_day``, after seeing what is in the way) or leave it.
* **Nothing is held beyond the window.** A one-off request for a day the series has not reached is
  accepted, and that day of the series will then be listed as ``ocupado``. The screens say so.
* **A day is decided once**: ``BookingSeriesDay`` is unique by series and day. A day somebody
  cancelled ("Este día no") keeps its row, so it is not booked again.
* **A series is approved or rejected once.** While it waits it holds nothing (a decision recorded
  in ``docs/v2/wave12/repeticion.md``); its dates are written approved, by who approved the series.
* **Hours are the church's wall clock**: 10:00 is 10:00 before and after a change of clock. A day
  on which that local hour does not exist is skipped and remembered as ``sin_hora``.
* **A series tied to a series of events** (the administration only) takes its days from that
  series' dates that are on (never one that was called off), the hour each event starts, and its
  own duration and buffers.
* Ending a series stops new dates. The ones that exist stay, each cancelled by itself if wanted.

Locks, always in this order: the space (the one lock of every booking), the series, a day's row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from agenda import series as event_series_rules
from agenda.models import EventSeries, SeriesOccurrence
from people import flags
from people.audit import record_church
from people.models import Person

from . import services
from .models import MAX_BUFFER_MINUTES, MAX_INTERVAL_WEEKS, Booking, BookingSeries, BookingSeriesDay, Space
from .services import SpaceError

logger = logging.getLogger(__name__)

# How far ahead the dates are written: the window of the series of events.
WINDOW_WEEKS = event_series_rules.WINDOW_WEEKS
# The last day a series may name, counted from the day it is asked: about six months.
MAX_AHEAD_DAYS = 183
# Series of one space that wait or are in force at once: nobody fills a table by asking again and again.
MAX_OPEN_PER_SPACE = 40
# Series of one space that ONE person has waiting for a decision at once: one person cannot use up the 40 above.
MAX_WAITING_PER_REQUESTER = 3

Status = BookingSeries.Status
Outcome = BookingSeriesDay.Outcome
SERIES_DECIDED = "Esta serie ya no está por decidir: alguien más la aprobó, la rechazó o la terminó. Revisa la lista otra vez."
NOT_LIVE = "Esta serie ya no aparta fechas: terminó o no está aprobada."


# ---- the rule -----------------------------------------------------------------------------------
def today_of(church, now: datetime | None = None) -> date:
    return (now or timezone.now()).astimezone(services.church_tz(church)).date()


def falls_on(series: BookingSeries, day: date) -> bool:
    """Whether the rule of ``series`` (not a series of events it may follow) gives ``day``."""
    if not series.starts_on <= day <= series.ends_on:
        return False
    days = (day - series.starts_on).days
    return days % (7 * max(series.interval_weeks, 1)) == 0


def rule_days(series: BookingSeries, first: date, last: date) -> list[date]:
    step = timedelta(weeks=max(series.interval_weeks, 1))
    day, last, days = series.starts_on, min(last, series.ends_on), []
    while day <= last:
        if day >= first:
            days.append(day)
        day += step
    return days


def rule_words(series: BookingSeries) -> str:
    """"cada miércoles", "cada 2 semanas, los miércoles"; for a series that follows a series of
    events, "en cada fecha de una serie de eventos" (its name is the administration's to read)."""
    if series.event_series_id is not None:
        return "en cada fecha de una serie de eventos"
    weekday = series.starts_on.weekday()
    if series.interval_weeks <= 1:
        return f"cada {event_series_rules.WEEKDAY_NAMES[weekday]}"
    return f"cada {series.interval_weeks} semanas, {event_series_rules.WEEKDAY_PLURALS[weekday]}"


def duration(series: BookingSeries) -> timedelta:
    start = datetime.combine(date.min, series.starts_at)
    return datetime.combine(date.min, series.ends_at) - start


@dataclass
class Slot:
    """One date the series should have: ``day`` is its key, ``on`` the day it happens, ``hour``
    when it starts there, ``event`` the event it is for (a series that follows a series of events)."""

    day: date
    on: date
    hour: time
    event: object = None


def _followed(series: BookingSeries, church_id: int) -> EventSeries | None:
    """The series of events ``series`` follows, when it is of the same church. Never another's."""
    followed = series.event_series
    return followed if followed is not None and followed.church_id == church_id else None


def _slots(series: BookingSeries, church_id: int, first: date, last: date) -> list[Slot]:
    first, last = max(first, series.starts_on), min(last, series.ends_on)
    if series.event_series_id is None:
        return [Slot(day, day, series.starts_at) for day in rule_days(series, first, last)]
    followed = _followed(series, church_id)
    if followed is None:
        return []
    rows = (
        SeriesOccurrence.objects.filter(series=followed, status=SeriesOccurrence.Status.ACTIVE, event__church_id=church_id,
                                        event__starts_on__gte=first, event__starts_on__lte=last)
        .select_related("event", "event__page").order_by("day", "id")
    )
    slots = []
    for row in rows:
        page = getattr(row.event, "page", None)
        slots.append(Slot(row.day, row.event.starts_on, (page.starts_at if page else None) or series.starts_at, row.event))
    return slots


def _hours(series: BookingSeries, slot: Slot, tz) -> tuple[datetime, datetime]:
    """The two moments of one date. ``SpaceError`` (``no_such_hour``) when the clock skips either."""
    start = datetime.combine(slot.on, slot.hour)
    end = start + duration(series)
    return services.instant(start.replace(tzinfo=tz)), services.instant(end.replace(tzinfo=tz))


# ---- writing the dates --------------------------------------------------------------------------
def _locked_series(series_id: int) -> BookingSeries:
    return BookingSeries.objects.select_for_update(of=("self",)).select_related("event_series").get(pk=series_id)


def _remember(series: BookingSeries, day: date, outcome: str, booking: Booking | None = None) -> bool:
    """Write the series' memory of ``day`` (and its booking). False when another pass wrote the day first."""
    try:
        with transaction.atomic():
            if booking is not None:
                booking.save()
            BookingSeriesDay.objects.create(series=series, day=day, outcome=outcome, booking=booking)
        return True
    except IntegrityError:
        return False


def _new_booking(space: Space, series: BookingSeries, slot: Slot, starts_at: datetime, ends_at: datetime, now) -> Booking:
    booking = Booking(
        space=space, title=series.title, starts_at=starts_at, ends_at=ends_at, setup_minutes=series.setup_minutes,
        teardown_minutes=series.teardown_minutes, requested_by_id=series.requested_by_id, event=slot.event,
        status=Booking.Status.APPROVED, decided_by_id=series.decided_by_id, decided_at=now,
    )
    booking.set_block()
    return booking


def _generate(space: Space, series: BookingSeries, actor, now: datetime) -> int:
    """With the space and the series locked. See ``generate``."""
    church = space.church
    if not series.live or not space.active or not church.active or not flags.enabled(church, "community"):
        return 0
    tz = services.church_tz(church)
    today = now.astimezone(tz).date()
    slots = _slots(series, church.pk, today, event_series_rules.horizon(today))
    known = set(BookingSeriesDay.objects.filter(series=series, day__in=[slot.day for slot in slots]).values_list("day", flat=True))
    written = 0
    for slot in slots:
        if slot.day in known:
            continue
        try:
            starts_at, ends_at = _hours(series, slot, tz)
        except SpaceError:
            _remember(series, slot.day, Outcome.NO_HOUR)
            continue
        if starts_at <= now:
            continue  # today's hour passed: nothing to hold
        booking = _new_booking(space, series, slot, starts_at, ends_at, now)
        # The rule of every booking, never bypassed: what holds those hours stays, and the day is listed.
        if services.overlapping(space.pk, booking.blocked_from, booking.blocked_until).exists():
            _remember(series, slot.day, Outcome.TAKEN)
            continue
        written += _remember(series, slot.day, Outcome.BOOKED, booking)
    if written:
        record_church(church, actor, "espacio_serie_fechas", "Se apartaron fechas de una reserva que se repite.", series,
                      space_id=space.pk, count=written)
    return written


def generate(series: BookingSeries, actor=None, now: datetime | None = None) -> int:
    """Write the dates of ``series`` that are missing between now and the horizon; returns how many
    bookings it made. A second run makes none. A series that is not approved or ended, a space that
    is switched off, a church that is not active or does not use the module: nothing."""
    now = now or timezone.now()
    with transaction.atomic():
        space = services._locked_space(series.space_id)
        return _generate(space, _locked_series(series.pk), actor, now)


def generate_due(now: datetime | None = None) -> dict:
    """The scheduler's pass over every series in force. Counts only. One series that fails never
    stops the next: it is counted in ``failed`` and logged by its error's class name."""
    now = now or timezone.now()
    due = (
        BookingSeries.objects.filter(status=Status.APPROVED, space__active=True, space__church__active=True,
                                     ends_on__gte=now.date() - timedelta(days=1))
        .select_related("space__church").order_by("pk")
    )
    totals = {"series": 0, "created": 0, "failed": 0}
    for series in due:
        if not flags.enabled(series.space.church, "community"):
            continue
        totals["series"] += 1
        try:
            totals["created"] += generate(series, now=now)
        except Exception as error:  # noqa: BLE001 - the other series are still written
            totals["failed"] += 1
            logger.error("The dates of a repeating booking could not be written: %s", type(error).__name__)
    return totals


# ---- asking, deciding, ending -------------------------------------------------------------------
def _audit(series: BookingSeries, church, actor, action: str, summary: str, **metadata) -> None:
    record_church(church, actor, action, summary, series, space_id=series.space_id, **metadata)


def request_series(space: Space, person: Person, actor, *, title: str, starts_on: date, ends_on: date, starts_at: time,
                   ends_at: time, interval_weeks: int = 1, setup_minutes: int = 0, teardown_minutes: int = 0,
                   event_series: EventSeries | None = None, approve: bool = False, now: datetime | None = None) -> BookingSeries:
    """A booking of ``space`` that repeats, asked by ``person``. ``approve``: the caller is the
    administration (its own series is approved at once, and only it may name ``event_series``). A
    space that needs no approval gives an approved series to anybody who may ask. An approved
    series writes its first dates here; one that waits holds nothing."""
    now = now or timezone.now()
    title = " ".join((title or "").split())
    if not title:
        raise SpaceError("Escribe para qué es la reserva.", code="title")
    if not isinstance(interval_weeks, int) or not 1 <= interval_weeks <= MAX_INTERVAL_WEEKS:
        raise SpaceError(f"Una reserva se repite cada semana o cada 2, 3 o {MAX_INTERVAL_WEEKS} semanas.", code="interval_weeks")
    if ends_at <= starts_at:
        raise SpaceError("La hora en que termina tiene que ser después de la hora en que empieza.", code="ends_at")
    if not 0 <= setup_minutes <= MAX_BUFFER_MINUTES or not 0 <= teardown_minutes <= MAX_BUFFER_MINUTES:
        raise SpaceError(f"El tiempo para preparar o recoger es de {MAX_BUFFER_MINUTES} minutos a lo más.", code="setup_minutes")
    today = today_of(space.church, now)
    if starts_on < today:
        raise SpaceError("Ese día ya pasó. Elige desde qué día se repite, de hoy en adelante.", code="starts_on")
    if ends_on < starts_on:
        raise SpaceError("El último día tiene que ser igual o posterior al primero.", code="ends_on")
    if ends_on > today + timedelta(days=MAX_AHEAD_DAYS):
        raise SpaceError("Una reserva se repite a lo más seis meses. Elige un último día más cercano; después puedes pedirla otra vez.",
                         code="ends_on")
    if event_series is not None and not approve:
        raise SpaceError("Solo la administración liga una reserva a una serie de eventos.", code="event_series")
    with transaction.atomic():
        space = services._locked_space(space.pk)
        if not space.active:
            raise SpaceError("Este espacio ya no se puede reservar.", code="space")
        if not Person.objects.filter(pk=person.pk, church_id=space.church_id, merged_into__isnull=True).exists():
            raise SpaceError("Esa persona no es de esta iglesia.", code="person")
        if event_series is not None:
            event_series = EventSeries.objects.filter(pk=event_series.pk, church_id=space.church_id, active=True).first()
            if event_series is None:
                raise SpaceError("Esa serie de eventos no es de esta iglesia o ya terminó.", code="event_series")
        open_now = BookingSeries.objects.filter(space=space, status__in=(Status.REQUESTED, Status.APPROVED), ends_on__gte=today)
        if open_now.count() >= MAX_OPEN_PER_SPACE:
            raise SpaceError("Este espacio ya tiene muchas reservas que se repiten. Termina alguna antes de pedir otra.", code="too_many")
        direct = not approve and not space.needs_approval
        if not approve and not direct and open_now.filter(status=Status.REQUESTED, requested_by=person).count() >= MAX_WAITING_PER_REQUESTER:
            raise SpaceError(
                f"Ya tienes {MAX_WAITING_PER_REQUESTER} reservas que se repiten esperando respuesta en este espacio. Espera a que "
                "la administración las decida, o retira alguna, antes de pedir otra.", code="too_many_waiting",
            )
        series = BookingSeries(
            space=space, title=title, interval_weeks=interval_weeks, starts_on=starts_on, ends_on=ends_on, starts_at=starts_at,
            ends_at=ends_at, setup_minutes=setup_minutes, teardown_minutes=teardown_minutes, requested_by=person,
            event_series=event_series,
        )
        if approve or direct:
            series.status, series.decided_at, series.decided_by = Status.APPROVED, now, actor if approve else None
        series.save()
        _audit(series, space.church, actor, "espacio_serie_solicitada", "Pidió una reserva que se repite.")
        if series.live:
            _audit(series, space.church, actor, "espacio_serie_aprobada",
                   "La reserva que se repite de un espacio que no pide aprobación quedó aprobada." if direct
                   else "Aprobó una reserva que se repite.", **({"source": "directa"} if direct else {}))
            _generate(space, series, actor, now)
    return series


def approve_series(series: BookingSeries, actor, now: datetime | None = None) -> BookingSeries:
    """The administration approves the whole series, once, and its first dates are written. A date
    whose hours are taken is not written and is not approved over anything: it is listed."""
    now = now or timezone.now()
    with transaction.atomic():
        space = services._locked_space(series.space_id)
        series = _locked_series(series.pk)
        if series.status != Status.REQUESTED:
            raise SpaceError(SERIES_DECIDED, code="decided")
        series.status, series.decided_by, series.decided_at = Status.APPROVED, actor, now
        series.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
        _audit(series, space.church, actor, "espacio_serie_aprobada", "Aprobó una reserva que se repite.")
        _generate(space, series, actor, now)
    return series


def reject_series(series: BookingSeries, actor, reason: str) -> BookingSeries:
    reason = " ".join((reason or "").split())
    if not reason:
        raise SpaceError("Escribe el motivo: lo lee la administración cuando alguien pregunta.", code="reason")
    with transaction.atomic():
        space = services._locked_space(series.space_id)
        series = _locked_series(series.pk)
        if series.status != Status.REQUESTED:
            raise SpaceError(SERIES_DECIDED, code="decided")
        series.status, series.decided_by, series.decided_at = Status.REJECTED, actor, timezone.now()
        series.decision_reason = reason[:200]
        series.save(update_fields=["status", "decided_by", "decided_at", "decision_reason", "updated_at"])
        _audit(series, space.church, actor, "espacio_serie_rechazada", "Rechazó una reserva que se repite.")
    return series


def end_series(series: BookingSeries, actor, *, by_admin: bool) -> BookingSeries:
    """No more dates are written. The bookings that exist stay as they are. Who asked ends their
    own (also one that still waits: the request is withdrawn); the administration ends any."""
    with transaction.atomic():
        space = services._locked_space(series.space_id)
        series = _locked_series(series.pk)
        if series.status not in (Status.REQUESTED, Status.APPROVED):
            raise SpaceError("Esta serie ya había terminado o fue rechazada.", code="decided")
        series.status, series.ended_at, series.ended_by = Status.ENDED, timezone.now(), actor
        series.save(update_fields=["status", "ended_at", "ended_by", "updated_at"])
        _audit(series, space.church, actor, "espacio_serie_terminada", "Terminó una reserva que se repite.",
               source="administracion" if by_admin else "propia")
    return series


def _slot_of(series: BookingSeries, church_id: int, day: date) -> Slot | None:
    """The date the series has on ``day`` as things are now, or None (a day its series of events called off)."""
    first, last = (day, day) if series.event_series_id is None else (series.starts_on, series.ends_on)
    return next((slot for slot in _slots(series, church_id, first, last) if slot.day == day), None)


def book_day(row: BookingSeriesDay, actor, *, seen=None, now: datetime | None = None) -> Booking:
    """The administration books a day the series could not (``ocupado``). With something still in the
    way it raises ``services.Clash`` unless ``seen`` names every booking that overlaps now: the
    override of every booking, audited the same way. With nothing in the way any more, it just books."""
    now = now or timezone.now()
    with transaction.atomic():
        space = services._locked_space(row.series.space_id)
        series = _locked_series(row.series_id)
        row = BookingSeriesDay.objects.select_for_update(of=("self",)).get(pk=row.pk, series=series)
        if not series.live:
            raise SpaceError(NOT_LIVE, code="decided")
        if row.outcome != Outcome.TAKEN or row.booking_id is not None:
            raise SpaceError("Ese día ya no está pendiente: revisa la serie otra vez.", code="decided")
        if not space.active:
            raise SpaceError("Este espacio ya no se puede reservar.", code="space")
        slot = _slot_of(series, space.church_id, row.day)
        if slot is None:
            raise SpaceError("Ese día ya no es una fecha de la serie.", code="gone")
        starts_at, ends_at = _hours(series, slot, services.church_tz(space.church))
        if starts_at <= now:
            raise SpaceError("Esa hora ya pasó.", code="past")
        booking = _new_booking(space, series, slot, starts_at, ends_at, now)
        booking.decided_by = actor
        over = services._check_clash(space, booking, seen)
        booking.overrode_clash = over
        booking.save()
        row.outcome, row.booking = Outcome.BOOKED, booking
        row.save(update_fields=["outcome", "booking"])
        services._audit_approval(booking, actor, over)
    return booking


def in_the_way(row: BookingSeriesDay, tz) -> tuple[Booking | None, list[Booking]]:
    """What ``book_day`` would book for ``row`` (not saved) and what overlaps it now. Reads only."""
    series = row.series
    slot = _slot_of(series, series.space.church_id, row.day)
    if slot is None:
        return None, []
    starts_at, ends_at = _hours(series, slot, tz)
    booking = _new_booking(series.space, series, slot, starts_at, ends_at, timezone.now())
    clashes = list(services.overlapping(series.space_id, booking.blocked_from, booking.blocked_until).select_related("requested_by", "event"))
    return booking, clashes


# ---- what people read ---------------------------------------------------------------------------
def series_of(booking: Booking) -> BookingSeries | None:
    """The series ``booking`` is a date of, or None for a booking on its own."""
    try:
        return booking.series_day.series
    except BookingSeriesDay.DoesNotExist:
        return None


def describe(series: BookingSeries) -> BookingSeries:
    """Attach what a template says about the rule: plain words and plain hours."""
    series.rule = rule_words(series)
    series.start_time, series.end_time = series.starts_at, series.ends_at
    series.has_buffer = bool(series.setup_minutes or series.teardown_minutes)
    series.open = series.status in (Status.REQUESTED, Status.APPROVED)
    return series


def days_of(series: BookingSeries, tz, now: datetime | None = None) -> dict:
    """The days of ``series`` for its screen: ``coming`` (booked and not ended), ``off`` (cancelled
    one by one), ``unbooked`` (could not be booked, still to come) and ``past`` (how many ended)."""
    now = now or timezone.now()
    today = now.astimezone(tz).date()
    out = {"coming": [], "off": [], "unbooked": [], "past": 0}
    for row in BookingSeriesDay.objects.filter(series=series).select_related("booking", "booking__space").order_by("day", "id"):
        booking = row.booking
        if booking is None:
            if row.day >= today:
                out["unbooked"].append(row)
            continue
        services.localize(booking, tz)
        if booking.ends_at <= now:
            out["past"] += 1
        elif booking.status == Booking.Status.APPROVED:
            out["coming"].append(row)
        else:
            out["off"].append(row)
    return out


def usual(space: Space, monday: date, tz, now: datetime | None = None) -> dict[date, list[dict]]:
    """For the administration's week: the days of that week a series in force would take and has
    not reached yet (they are beyond the window, so nothing holds them). Each entry: the series,
    its rule in words and the hours. A day the followed series of events called off (or that no longer falls
    on its rule) is not "usual": it is skipped, as the generator skips it. Reads only; nothing here refuses a request."""
    now = now or timezone.now()
    today = now.astimezone(tz).date()
    sunday = monday + timedelta(days=6)
    found: dict[date, list[dict]] = {}
    live = BookingSeries.objects.filter(space=space, status=Status.APPROVED, starts_on__lte=sunday, ends_on__gte=max(monday, today))
    for series in live.select_related("event_series").order_by("starts_at", "id"):
        skipped = set(BookingSeriesDay.objects.filter(series=series, day__gte=monday, day__lte=sunday).values_list("day", flat=True))
        followed = _followed(series, space.church_id)
        if followed is not None:
            skipped |= set(
                SeriesOccurrence.objects.filter(series=followed, day__gte=monday, day__lte=sunday)
                .exclude(status=SeriesOccurrence.Status.ACTIVE).values_list("day", flat=True)
            )
        for offset in range(7):
            day = monday + timedelta(days=offset)
            if day < today or day in skipped or not series.starts_on <= day <= series.ends_on:
                continue
            if series.event_series_id is None:
                start, on = series.starts_at, falls_on(series, day)
            else:
                on = followed is not None and followed.active and event_series_rules.falls_on(followed, day)
                start = followed.starts_at if on else None
            if on:
                end = (datetime.combine(day, start) + duration(series)).time()
                found.setdefault(day, []).append({"series": series, "rule": rule_words(series), "start": start, "end": end})
    return found


def mine(person: Person, now: datetime | None = None) -> list[BookingSeries]:
    """The series ``person`` asked for that wait or are in force, plus what ended or was rejected in the last two weeks."""
    now = now or timezone.now()
    recent = now - timedelta(days=14)
    rows = (
        BookingSeries.objects.filter(requested_by=person, space__church_id=person.church_id)
        .filter(Q(status__in=(Status.REQUESTED, Status.APPROVED)) | Q(status=Status.REJECTED, decided_at__gte=recent)
                | Q(status=Status.ENDED, ended_at__gte=recent))
        .select_related("space").order_by("starts_on", "id")[:20]
    )
    return [describe(row) for row in rows]


def waiting(church):
    """The series the administration has not decided."""
    return BookingSeries.objects.filter(space__church=church, status=Status.REQUESTED)
