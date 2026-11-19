"""Every write to a space or a booking, and the questions the screens ask about them.

The rule that matters: two bookings of one space that still hold it (``solicitada`` or ``aprobada``)
never share a minute, counting the time to set up before and to put away after. It is enforced here
and only here, always the same way: inside one transaction, lock the row of the **space**, then read
the bookings that overlap, then write. Whoever arrives second waits at the lock and reads the first
one's booking. The database has no constraint for it (decision recorded in
``docs/v2/wave9/espacios.md``: PostgreSQL's exclusion constraint over a space and a range needs the
``btree_gist`` extension), so no code may create or move a booking without ``_locked_space``.

Lock order, everywhere: the space first, then the booking.

The administration may keep a booking over another one only by naming the bookings it saw
(``seen``): if a different one overlaps by the time the lock is held, the answer is the list again,
never a silent override. Each write stores one church audit row with ids and fixed words: never the
title of a booking, the name of a space or the reason of a rejection.

Callers have already answered who may act (``espacios.views``, ``espacios.access``).
"""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from people.audit import record_church
from people.models import Person

from .models import MAX_BUFFER_MINUTES, Booking, Space

Status = Booking.Status
HOLDING = Booking.HOLDING
MAX_HOURS = 24  # one booking; several days are several bookings (a series is a later slice)
DECIDED = "Esta reserva ya no está por decidir: alguien más la aprobó, la rechazó o la canceló. Revisa la lista otra vez."


class SpaceError(ValidationError):
    """A rule of the spaces refused the action; the message is ready for the screen."""


class Clash(SpaceError):
    """The hours are taken. ``bookings`` are the ones in the way, earliest first."""

    def __init__(self, bookings):
        super().__init__("Ese horario se empalma con otra reserva del mismo espacio.", code="clash")
        self.bookings = list(bookings)


def church_tz(church) -> ZoneInfo:
    """The zone the church's hours are said in (``core.Church.time_zone``), as the events do with theirs."""
    try:
        return ZoneInfo(church.time_zone)
    except Exception:  # pragma: no cover - an invalid zone saved through the admin
        return ZoneInfo("America/Mexico_City")


NO_SUCH_HOUR = "Esa hora no existe ese día: es cuando se adelanta el reloj por el cambio de horario. Elige otra hora."


def instant(value: datetime) -> datetime:
    """``value`` as a moment in UTC, which is what every comparison here uses: two hours in the same
    zone compare by the clock on the wall, and on the night the clock changes that is not the order
    they happen in. An hour the clock skips (02:30 the night it jumps from 02:00 to 03:00) is refused.
    An hour that happens twice (the night the clock goes back) is the first of the two, always."""
    if timezone.is_naive(value):
        raise ValueError("Booking times must be timezone-aware.")
    moment = value.replace(fold=0).astimezone(UTC)
    if moment.astimezone(value.tzinfo).replace(tzinfo=None) != value.replace(tzinfo=None, fold=0):
        raise SpaceError(NO_SUCH_HOUR, code="no_such_hour")
    return moment


def local_instant(day: date, hour: time, tz) -> datetime:
    """The moment ``hour`` of ``day`` is in ``tz`` (see ``instant``)."""
    return instant(datetime.combine(day, hour, tzinfo=tz))


def localize(booking: Booking, tz) -> Booking:
    """Attach the day and the hours of ``booking`` in ``tz``: plain dates and times, so a template
    cannot convert them to another zone."""
    start, end = booking.starts_at.astimezone(tz), booking.ends_at.astimezone(tz)
    booking.day, booking.start_time = start.date(), start.time()
    booking.end_day, booking.end_time = end.date(), end.time()
    booking.same_day = booking.day == booking.end_day
    booking.block_start = booking.blocked_from.astimezone(tz).time()
    booking.block_end = booking.blocked_until.astimezone(tz).time()
    booking.has_buffer = bool(booking.setup_minutes or booking.teardown_minutes)
    # The days the space is held, buffers included. A block that ends at midnight does not touch the next day.
    booking.block_day = booking.blocked_from.astimezone(tz).date()
    booking.block_last_day = (booking.blocked_until - timedelta(microseconds=1)).astimezone(tz).date()
    return booking


def _day_slice(booking: Booking, day: date, tz) -> Booking:
    """A copy of ``booking`` for one day of a week: the hours it holds the space **that day**
    (``day_hours``, "22:30 a 24:00") and what they are (``day_part``: the use itself, or only the
    time to set up before it or to put away after it)."""
    row = copy.copy(booking)
    opens = datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)
    closes = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    start, end = max(booking.blocked_from, opens), min(booking.blocked_until, closes)
    until = "24:00" if end == closes else f"{end.astimezone(tz):%H:%M}"
    row.day_hours = f"{start.astimezone(tz):%H:%M} a {until}"
    if booking.starts_at < closes and booking.ends_at > opens:
        row.day_part = "uso"
    else:
        row.day_part = "preparacion" if booking.starts_at >= closes else "recogida"
    row.split = booking.block_day != booking.block_last_day
    return row


# ---- spaces -------------------------------------------------------------------------------------
def save_space(church, actor, *, space: Space | None = None, name: str, kind: str, capacity=None, notes: str = "",
               needs_approval: bool = True, active: bool = True) -> Space:
    name = " ".join(name.split())
    with transaction.atomic():
        taken = Space.objects.filter(church=church, name__iexact=name)
        if space is not None:
            space = Space.objects.select_for_update().get(pk=space.pk, church=church)
            taken = taken.exclude(pk=space.pk)
        if taken.exists():
            raise SpaceError("Ya hay un espacio con ese nombre. Escribe otro para no confundirlos.", code="name")
        created = space is None
        if created:
            space = Space(church=church, created_by=actor)
        space.name, space.kind, space.capacity, space.notes = name, kind, capacity, notes.strip()
        space.needs_approval, space.active = needs_approval, active
        space.save()
        record_church(
            church, actor, "espacio_creado" if created else "espacio_modificado",
            "Agregó un espacio o un equipo." if created else "Modificó un espacio o un equipo.", space, space_id=space.pk,
        )
    return space


# ---- reading ------------------------------------------------------------------------------------
def overlapping(space_id: int, blocked_from: datetime, blocked_until: datetime, *, exclude: int | None = None):
    """The bookings that hold ``space`` for any minute of the window, earliest first. Touching ends do not overlap."""
    rows = Booking.objects.filter(
        space_id=space_id, status__in=HOLDING, blocked_from__lt=blocked_until, blocked_until__gt=blocked_from,
    )
    if exclude is not None:
        rows = rows.exclude(pk=exclude)
    return rows.order_by("blocked_from", "id")


def week_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week(space: Space, monday: date, tz) -> list[dict]:
    """Seven days from ``monday``, each with what holds the space that day, localized: a booking is
    listed on every day its blocked hours touch, the time to set up and to put away included, so a
    day never reads as free while a request for it would be refused."""
    start = datetime.combine(monday, time.min, tzinfo=tz)
    end = datetime.combine(monday + timedelta(days=7), time.min, tzinfo=tz)
    rows = [localize(row, tz) for row in overlapping(space.pk, start, end).select_related("requested_by", "event").order_by("starts_at", "id")]
    days = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        days.append({"date": day, "bookings": [_day_slice(row, day, tz) for row in rows if row.block_day <= day <= row.block_last_day]})
    return days


def pending_count(church) -> int:
    """Requests the administration has not decided and that have not ended."""
    return Booking.objects.filter(space__church=church, status=Status.REQUESTED, ends_at__gt=timezone.now()).count()


def duration_text(length: timedelta) -> str:
    """"2 h", "1 h 30 min", "45 min"; nothing at all is "Sin uso"."""
    minutes = round(length.total_seconds() / 60)
    if minutes <= 0:
        return "Sin uso"
    hours, rest = divmod(minutes, 60)
    return " ".join(part for part in (f"{hours} h" if hours else "", f"{rest} min" if rest else "") if part)


def usage(church, first: date, last: date, tz) -> list[dict]:
    """How long each space was in use from ``first`` to ``last`` (both included): the booked time
    of approved bookings, cut at the edges of the range, **without** the time to set up or put away.
    It is the time the space was occupied: an hour two approved bookings share (the administration
    kept one over the other) counts once. Bookings and time, nothing about people."""
    start = datetime.combine(first, time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    totals = {space.pk: {"space": space, "bookings": 0, "spans": []} for space in Space.objects.filter(church=church)}
    rows = Booking.objects.filter(
        space__church=church, status=Status.APPROVED, starts_at__lt=end, ends_at__gt=start,
    ).order_by("starts_at", "id").values_list("space_id", "starts_at", "ends_at")
    for space_id, starts_at, ends_at in rows:
        total = totals[space_id]
        total["bookings"] += 1
        begins, stops = max(starts_at, start), min(ends_at, end)
        spans = total["spans"]
        if spans and begins <= spans[-1][1]:      # ordered by start: it can only reach into the last span
            spans[-1][1] = max(spans[-1][1], stops)
        else:
            spans.append([begins, stops])
    result = []
    for total in totals.values():
        if not total["space"].active and not total["bookings"]:
            continue
        total["used"] = sum((stops - begins for begins, stops in total.pop("spans")), timedelta())
        total["used_text"] = duration_text(total["used"])
        result.append(total)
    return sorted(result, key=lambda total: (total["space"].kind, total["space"].name.lower(), total["space"].pk))


# ---- writing ------------------------------------------------------------------------------------
def _locked_space(space_id: int) -> Space:
    """The one lock every booking of a space is written under. Call inside ``atomic()``."""
    # of=("self",): only the space's row. Without it PostgreSQL also locks the joined church row, and every
    # booking of every space of the church would wait in one line.
    return Space.objects.select_for_update(of=("self",)).select_related("church").get(pk=space_id)


def _locked_booking(booking_id: int) -> Booking:
    return Booking.objects.select_for_update(of=("self",)).select_related("space", "space__church").get(pk=booking_id)


def _check_clash(space: Space, booking: Booking, seen) -> bool:
    """Raise ``Clash`` unless nothing overlaps or the administration saw exactly what overlaps now.
    True when the booking is kept over another one."""
    clashes = list(overlapping(space.pk, booking.blocked_from, booking.blocked_until, exclude=booking.pk).select_related("requested_by", "event"))
    if not clashes:
        return False
    if seen is None or not {row.pk for row in clashes} <= {int(pk) for pk in seen}:
        raise Clash(clashes)
    return True


def _audit(booking: Booking, actor, action: str, summary: str, **metadata) -> None:
    record_church(booking.space.church, actor, action, summary, booking, space_id=booking.space_id, **metadata)


def request_booking(space: Space, person: Person, actor, *, title: str, starts_at: datetime, ends_at: datetime,
                    setup_minutes: int = 0, teardown_minutes: int = 0, event=None, approve: bool = False,
                    seen=None) -> Booking:
    """A new booking of ``space`` asked by ``person``. It holds its hours from this moment.

    ``approve``: the caller is the administration, which does not ask itself. ``seen``: the ids of
    the bookings the administration saw in the way and decided to keep this one over (only read
    together with ``approve``). A space that needs no approval gives an approved booking to anybody
    who may ask.
    """
    title = " ".join(title.split())
    if not title:
        raise SpaceError("Escribe para qué es la reserva.", code="title")
    starts_at, ends_at = instant(starts_at), instant(ends_at)   # moments, never hours on a wall clock
    if ends_at <= starts_at:
        raise SpaceError("La hora en que termina tiene que ser después de la hora en que empieza.", code="ends_at")
    if ends_at - starts_at > timedelta(hours=MAX_HOURS):
        raise SpaceError("Una reserva dura a lo más un día. Para varios días, haz una reserva por día.", code="ends_at")
    if not 0 <= setup_minutes <= MAX_BUFFER_MINUTES or not 0 <= teardown_minutes <= MAX_BUFFER_MINUTES:
        raise SpaceError(f"El tiempo para preparar o recoger es de {MAX_BUFFER_MINUTES} minutos a lo más.", code="setup_minutes")
    if starts_at <= timezone.now():
        raise SpaceError("Esa hora ya pasó. Elige un día y una hora que todavía no lleguen.", code="starts_at")
    try:
        with transaction.atomic():
            return _write_request(space, person, actor, title, starts_at, ends_at, setup_minutes, teardown_minutes, event, approve, seen)
    except IntegrityError as error:
        # The database refused the row (its own checks on the hours). Never a server error for who asked.
        raise SpaceError("No pudimos guardar la reserva con esas horas. Revisa el día y las horas.", code="hours") from error


def _write_request(space, person, actor, title, starts_at, ends_at, setup_minutes, teardown_minutes, event, approve, seen) -> Booking:
    space = _locked_space(space.pk)
    if not space.active:
        raise SpaceError("Este espacio ya no se puede reservar.", code="space")
    # Read now, not from the caller's copy: a person absorbed by a merge a moment ago asks for nothing.
    if not Person.objects.filter(pk=person.pk, church_id=space.church_id, merged_into__isnull=True).exists():
        raise SpaceError("Esa persona no es de esta iglesia.", code="person")
    if event is not None and event.church_id != space.church_id:
        raise SpaceError("Ese evento no es de esta iglesia.", code="event")
    booking = Booking(
        space=space, title=title, starts_at=starts_at, ends_at=ends_at, setup_minutes=setup_minutes,
        teardown_minutes=teardown_minutes, requested_by=person, event=event,
    )
    booking.set_block()
    over = _check_clash(space, booking, seen if approve else None)
    if approve or not space.needs_approval:
        booking.status, booking.decided_at = Status.APPROVED, timezone.now()
        booking.decided_by = actor if approve else None
    booking.overrode_clash = over
    booking.save()
    _audit(booking, actor, "espacio_reserva_solicitada", "Pidió la reserva de un espacio.",
           **({"event_id": event.pk} if event is not None else {}))
    if booking.status == Status.APPROVED:
        _audit_approval(booking, actor, over, direct=not approve)
    return booking


def _audit_approval(booking: Booking, actor, over: bool, *, direct: bool = False) -> None:
    if over:
        _audit(booking, actor, "espacio_reserva_sobre_choque",
               "Aprobó la reserva de un espacio aunque se empalma con otra, después de ver cuál.", override="empalme")
    elif direct:
        _audit(booking, actor, "espacio_reserva_aprobada", "La reserva de un espacio que no pide aprobación quedó aprobada.", source="directa")
    else:
        _audit(booking, actor, "espacio_reserva_aprobada", "Aprobó la reserva de un espacio.")


def approve(booking: Booking, actor, *, seen=None) -> Booking:
    """The administration approves a request. With another booking in the way it raises ``Clash``
    unless ``seen`` names every booking that overlaps now."""
    with transaction.atomic():
        space = _locked_space(booking.space_id)
        booking = _locked_booking(booking.pk)
        if booking.status != Status.REQUESTED:
            raise SpaceError(DECIDED, code="decided")
        over = _check_clash(space, booking, seen)
        booking.status, booking.decided_by, booking.decided_at = Status.APPROVED, actor, timezone.now()
        booking.overrode_clash = booking.overrode_clash or over
        booking.save(update_fields=["status", "decided_by", "decided_at", "overrode_clash", "updated_at"])
        _audit_approval(booking, actor, over)
    return booking


def reject(booking: Booking, actor, reason: str) -> Booking:
    """The administration says no, with a reason only the administration reads."""
    reason = " ".join(reason.split())
    if not reason:
        raise SpaceError("Escribe el motivo: lo lee la administración cuando alguien pregunta.", code="reason")
    with transaction.atomic():
        booking = _locked_booking(booking.pk)
        if booking.status != Status.REQUESTED:
            raise SpaceError(DECIDED, code="decided")
        booking.status, booking.decided_by, booking.decided_at = Status.REJECTED, actor, timezone.now()
        booking.decision_reason = reason[:200]
        booking.save(update_fields=["status", "decided_by", "decided_at", "decision_reason", "updated_at"])
        _audit(booking, actor, "espacio_reserva_rechazada", "Rechazó la reserva de un espacio.")
    return booking


def cancel(booking: Booking, actor, *, by_admin: bool) -> Booking:
    """Free the hours of a booking that has not ended: who asked for it, or the administration."""
    with transaction.atomic():
        booking = _locked_booking(booking.pk)
        if booking.status not in HOLDING:
            raise SpaceError("Esta reserva ya estaba cancelada o rechazada.", code="decided")
        if booking.ends_at <= timezone.now():
            raise SpaceError("Esta reserva ya terminó: no se puede cancelar.", code="past")
        booking.status, booking.decided_by, booking.decided_at = Status.CANCELLED, actor, timezone.now()
        booking.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
        _audit(booking, actor, "espacio_reserva_cancelada", "Canceló la reserva de un espacio.",
               source="administracion" if by_admin else "propia")
    return booking


def mine(person: Person, tz, limit: int = 20) -> list[Booking]:
    """What ``person`` asked for and has not ended, plus what was rejected or cancelled in the last two weeks."""
    now = timezone.now()
    rows = (
        Booking.objects.filter(requested_by=person, space__church_id=person.church_id)
        .filter(Q(status__in=HOLDING, ends_at__gt=now) | Q(status__in=(Status.REJECTED, Status.CANCELLED), decided_at__gte=now - timedelta(days=14), ends_at__gt=now))
        .select_related("space", "event").order_by("starts_at", "id")[:limit]
    )
    return [localize(row, tz) for row in rows]
