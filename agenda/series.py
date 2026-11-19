"""Series: what the church does every week (issue #93, first slice).

A series (``EventSeries``) is a template. Its dates are ordinary simple events, written ahead for a rolling
window (``WINDOW_WEEKS``) by ``generate``: when the series is saved, and on every pass of the scheduler
(``generate_due``, the job ``series_eventos``). Each date has its own page, capacity, list and attendance,
because it is an event like any other; capacity, offers, the waiting list and the door are ``agenda.services``.

What a new date takes is what a copy takes in ``agenda.duplicate``, and less: the basics of the event and the
configuration of its page, from the template. Never a person, a registration, an authorization, a public
notice, an opinion or the attendance: there is no source event to take them from. Unlike a copy, a date is
published (and public) when the series says so: that is what the administration decided on the series.

The rules, in words:

* A day is written once: ``SeriesOccurrence`` is unique by series and day, so two passes at the same time
  cannot write it twice, and a day that was called off is not written again.
* A date keeps its local hour: the event stores a day, an hour and a time zone, never an instant, so 10:00
  is 10:00 before and after a change of clock in its zone.
* Changing the series changes only the dates that are still to come, that nobody changed by hand and that
  nobody signed up for. The others are left exactly as they are and shown to the administration to review.
* Ending a series stops new dates. The ones that exist stay.

Locks, always in this order: the series, a date's page, its event, its occurrence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.text import slugify

from events.models import Event
from people import flags, policy
from people.audit import record_church

from .models import EventPage, EventRegistration, EventSeries, SeriesOccurrence

logger = logging.getLogger(__name__)

# How far ahead the dates of a series are written.
WINDOW_WEEKS = 8

ACTIVE = SeriesOccurrence.Status.ACTIVE
CANCELLED = SeriesOccurrence.Status.CANCELLED
OFF_RULE = SeriesOccurrence.Status.OFF_RULE
# Somebody is in, or in line: a date with one of these is never moved by a change of the series.
SIGNED_UP = (*EventRegistration.HOLDING, *EventRegistration.LISTED)

# What a date takes from the series: a subset of what a copy of an event takes (``agenda.duplicate``), plus
# the name, and the two switches a copy never takes and a series sets on purpose. The tests compare the two
# (this module cannot import ``agenda.duplicate``, which holds views).
TEMPLATE_EVENT_FIELDS = ("name", "location", "time_zone")
TEMPLATE_PAGE_FIELDS = ("description", "starts_at", "registration", "capacity", "published", "public")
SCHEDULE_FIELDS = ("weekdays", "interval_weeks", "starts_on", "ends_on")
SERIES_FIELDS = (*TEMPLATE_EVENT_FIELDS, *TEMPLATE_PAGE_FIELDS, *SCHEDULE_FIELDS, "duration_minutes")

WEEKDAY_NAMES = dict(EventSeries.WEEKDAYS)
WEEKDAY_PLURALS = {0: "los lunes", 1: "los martes", 2: "los miércoles", 3: "los jueves", 4: "los viernes",
                   5: "los sábados", 6: "los domingos"}


# ---- the rule -----------------------------------------------------------------------------------
def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def falls_on(series: EventSeries, day: date) -> bool:
    """Whether the series, as it is written now, happens on ``day``."""
    if day < series.starts_on or (series.ends_on and day > series.ends_on):
        return False
    if day.weekday() not in series.weekday_numbers:
        return False
    weeks = (_monday(day) - _monday(series.starts_on)).days // 7
    return weeks % max(series.interval_weeks, 1) == 0


def days_between(series: EventSeries, first: date, last: date) -> list[date]:
    days, day = [], max(first, series.starts_on)
    last = min(last, series.ends_on) if series.ends_on else last
    while day <= last:
        if falls_on(series, day):
            days.append(day)
        day += timedelta(days=1)
    return days


def horizon(today: date) -> date:
    return today + timedelta(weeks=WINDOW_WEEKS)


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} y {words[-1]}"


def rule_words(series: EventSeries) -> str:
    """"cada domingo", "cada martes y jueves", "cada 2 semanas, los domingos"."""
    numbers = series.weekday_numbers
    if not numbers:
        return "sin día de la semana"
    if series.interval_weeks <= 1:
        return "cada " + _join([WEEKDAY_NAMES[number] for number in numbers])
    return f"cada {series.interval_weeks} semanas, " + _join([WEEKDAY_PLURALS[number] for number in numbers])


def occurrence_of(event: Event) -> SeriesOccurrence | None:
    """The date of a series this event is, or None for an event on its own."""
    try:
        return event.series_occurrence
    except SeriesOccurrence.DoesNotExist:
        return None


# ---- writing the dates --------------------------------------------------------------------------
def _slug(series: EventSeries, day: date) -> str:
    """The address of one date: the series' name and the day. Formed here, never typed."""
    suffix = f"-{day:%Y-%m-%d}"
    limit = Event._meta.get_field("slug").max_length
    base = slugify(series.name)[: limit - len(suffix) - 4].strip("-") or "evento"
    slug, n = base + suffix, 2
    while Event.objects.filter(slug=slug).exists():
        slug, n = f"{base}{suffix}-{n}", n + 1
    return slug


def _event_values(series: EventSeries) -> dict:
    return {name: getattr(series, name) for name in TEMPLATE_EVENT_FIELDS}


def _page_values(series: EventSeries) -> dict:
    return {name: getattr(series, name) for name in TEMPLATE_PAGE_FIELDS}


def _write_day(series: EventSeries, day: date, actor) -> bool:
    """One date: the event, its page and the series' memory of the day, or nothing at all when another
    pass wrote the day first (the constraint refuses the second row and the event goes with it)."""
    for _attempt in range(3):
        try:
            with transaction.atomic():
                event = Event(church=series.church, created_by=actor, camp_tools=False, join_by_name=False,
                              status=Event.Status.ACTIVE, kind=Event.Kind.OTHER, slug=_slug(series, day),
                              starts_on=day, ends_on=day, **_event_values(series))
                event.save()
                # As for any new simple event: the church's policy decides whether minors need an authorization.
                EventPage.objects.create(event=event, created_by=actor,
                                         requires_guardian_consent=policy.minors_consent_default(series.church),
                                         **_page_values(series))
                SeriesOccurrence.objects.create(series=series, day=day, event=event)
            return True
        except IntegrityError:
            if SeriesOccurrence.objects.filter(series=series, day=day).exists():
                return False
            # Not the day: another event took the address meanwhile. Form the next one and try again.
    raise IntegrityError("A date of a series could not be written.")


def generate(series: EventSeries, actor=None, today: date | None = None) -> int:
    """Write the dates of ``series`` that are missing between today and the horizon. Returns how many it
    wrote. A second run writes nothing; a series that ended, or of a church without the module, writes nothing."""
    today = today or timezone.localdate()
    with transaction.atomic():
        locked = EventSeries.objects.select_for_update().get(pk=series.pk)
        if not locked.active or not flags.enabled(locked.church, "community"):
            return 0
        until = horizon(today)
        wanted = days_between(locked, today, until)
        known = {row.day: row for row in SeriesOccurrence.objects.filter(series=locked, day__in=wanted)}
        written = 0
        for day in wanted:
            row = known.get(day)
            if row is None:
                written += _write_day(locked, day, actor)
            elif row.status == OFF_RULE:
                _follow(locked, row.pk, today)  # the series falls on this day again
        locked.generated_through = until
        locked.save(update_fields=["generated_through"])
        if written:
            record_church(locked.church, actor, "serie_fechas", "Se escribieron fechas de una serie de eventos", locked,
                          value=written)
    return written


def generate_due(today: date | None = None) -> dict:
    """The scheduler's pass: every series that is not written up to the horizon. Counts only. One series
    that fails never stops the next; it is counted in ``failed`` and logged by its error's class name.
    While a church has the module off its series wait as they are."""
    today = today or timezone.localdate()
    until = horizon(today)
    due = (
        EventSeries.objects.filter(active=True, church__active=True, starts_on__lte=until)
        .filter(Q(generated_through__isnull=True) | Q(generated_through__lt=until))
        .exclude(ends_on__isnull=False, generated_through__isnull=False, ends_on__lte=F("generated_through"))
        .select_related("church").order_by("pk")
    )
    totals = {"series": 0, "created": 0, "failed": 0}
    for series in due:
        if not flags.enabled(series.church, "community"):
            continue
        totals["series"] += 1
        try:
            totals["created"] += generate(series, today=today)
        except Exception as error:  # noqa: BLE001 - the other series are still written
            totals["failed"] += 1
            logger.error("The dates of a series could not be written: %s", type(error).__name__)
    return totals


# ---- the administration's writes ----------------------------------------------------------------
@dataclass
class Applied:
    """What a change of the series did to its dates. ``kept``: dates left as they were because somebody is
    signed up or somebody changed them by hand, and that are not what the series says now."""

    changed: int = 0
    retired: int = 0
    kept: list[SeriesOccurrence] = field(default_factory=list)


def _clean(fields: dict) -> dict:
    unknown = sorted(set(fields) - set(SERIES_FIELDS))
    if unknown:
        raise ValueError(f"Unknown series fields: {', '.join(unknown)}.")
    return fields


def create_series(church, actor, today: date | None = None, **fields) -> EventSeries:
    with transaction.atomic():
        series = EventSeries.objects.create(church=church, created_by=actor, **_clean(fields))
        record_church(church, actor, "serie_creada", "Creó una serie de eventos", series)
        generate(series, actor, today)
    return series


def update_series(series: EventSeries, actor, today: date | None = None, **fields) -> Applied:
    """Change the template and bring the dates that may follow it up to date (see the module's rules)."""
    today = today or timezone.localdate()
    with transaction.atomic():
        locked = EventSeries.objects.select_for_update().get(pk=series.pk)
        for name, value in _clean(fields).items():
            setattr(locked, name, value)
        locked.generated_through = None  # the rule may have changed: look at the whole window again
        locked.save()
        applied = _apply(locked, today)
        record_church(locked.church, actor, "serie_modificada", "Modificó una serie de eventos", locked, value=applied.changed)
        generate(locked, actor, today)
    return applied


def set_active(series: EventSeries, actor, active: bool, today: date | None = None) -> EventSeries:
    """End the series (nothing new is written, what exists stays) or take it up again."""
    with transaction.atomic():
        locked = EventSeries.objects.select_for_update().get(pk=series.pk)
        if locked.active != active:
            locked.active = active
            locked.generated_through = None
            locked.save(update_fields=["active", "generated_through", "updated_at"])
            record_church(locked.church, actor, "serie_modificada",
                          "Reanudó una serie de eventos" if active else "Terminó una serie de eventos", locked,
                          reason="reanudada" if active else "terminada")
        if active:
            generate(locked, actor, today)
    return locked


def _differs(series: EventSeries, event: Event, page: EventPage | None) -> bool:
    if page is None or any(getattr(event, name) != value for name, value in _event_values(series).items()):
        return True
    return any(getattr(page, name) != value for name, value in _page_values(series).items())


def _follow(series: EventSeries, occurrence_id: int, today: date, applied: Applied | None = None) -> None:
    """Bring one date in line with the series, if it may be: lock its page, then read everything again."""
    applied = applied if applied is not None else Applied()
    row = SeriesOccurrence.objects.filter(pk=occurrence_id).first()
    if row is None:
        return
    # The page's lock is the one a sign-up takes: nobody signs up while this decides.
    page = EventPage.objects.select_for_update().filter(event_id=row.event_id).first()
    row = SeriesOccurrence.objects.select_for_update(of=("self",)).select_related("event").get(pk=occurrence_id)
    event = row.event
    if row.status == CANCELLED or (event.ends_on or event.starts_on or row.day) < today:
        return
    on_rule = falls_on(series, row.day)
    taken = EventRegistration.objects.filter(event_id=event.pk, status__in=SIGNED_UP).exists()
    if row.edited_at is not None or taken:
        if row.status == ACTIVE and (not on_rule or _differs(series, event, page)):
            row.off_rule = not on_rule
            row.has_people = taken
            applied.kept.append(row)
        return
    now = timezone.now()
    if not on_rule:
        if row.status != OFF_RULE:
            EventPage.objects.filter(event_id=event.pk).update(published=False, updated_at=now)
            row.status = OFF_RULE
            row.save(update_fields=["status"])
            applied.retired += 1
        return
    if row.status == OFF_RULE or _differs(series, event, page):
        Event.objects.filter(pk=event.pk).update(updated_at=now, **_event_values(series))
        if page is None:
            EventPage.objects.create(event=event, requires_guardian_consent=policy.minors_consent_default(series.church),
                                     **_page_values(series))
        else:
            EventPage.objects.filter(pk=page.pk).update(updated_at=now, **_page_values(series))
        applied.changed += 1
    if row.status != ACTIVE:
        row.status = ACTIVE
        row.save(update_fields=["status"])


def _apply(series: EventSeries, today: date) -> Applied:
    applied = Applied()
    ids = SeriesOccurrence.objects.filter(series=series, day__gte=today).order_by("day").values_list("pk", flat=True)
    for occurrence_id in list(ids):
        _follow(series, occurrence_id, today, applied)
    return applied


def to_review(series: EventSeries, today: date | None = None) -> list[SeriesOccurrence]:
    """The dates to come that do not say what the series says and that Emmaus will not change: somebody is
    signed up. (A date changed by hand differs on purpose and is not listed.) Reads only."""
    today = today or timezone.localdate()
    rows = (
        SeriesOccurrence.objects.filter(series=series, day__gte=today, status=ACTIVE, edited_at__isnull=True)
        .select_related("event", "event__page").order_by("day")
    )
    taken = set(EventRegistration.objects.filter(event__series_occurrence__series=series, status__in=SIGNED_UP)
                .values_list("event_id", flat=True))
    found = []
    for row in rows:
        page = getattr(row.event, "page", None)
        row.off_rule = not falls_on(series, row.day)
        if row.event_id in taken and (row.off_rule or _differs(series, row.event, page)):
            found.append(row)
    return found


def touched(event: Event) -> None:
    """Somebody changed this event or its page by hand: if it is a date of a series, the series stops
    changing it. Called by ``agenda.services.save_page`` and ``agenda.simple.update_simple_event``."""
    SeriesOccurrence.objects.filter(event_id=event.pk, edited_at__isnull=True).update(edited_at=timezone.now())


def _locked_occurrence(occurrence: SeriesOccurrence) -> tuple[SeriesOccurrence, EventPage | None]:
    page = EventPage.objects.select_for_update().filter(event_id=occurrence.event_id).first()
    row = SeriesOccurrence.objects.select_for_update(of=("self",)).select_related("event", "series").get(pk=occurrence.pk)
    return row, page


def call_off(occurrence: SeriesOccurrence, actor) -> SeriesOccurrence:
    """"Este día no hay": the date's page stops being published (so nobody new sees it or signs up, and
    its public page and the calendar drop it) and the day is remembered, so it is not written again.
    Nothing about who signed up changes: the administration tells them. Idempotent."""
    with transaction.atomic():
        row, page = _locked_occurrence(occurrence)
        if row.status == CANCELLED:
            return row
        row.published_before = bool(page and page.published) and row.status == ACTIVE
        row.status, row.cancelled_at, row.cancelled_by = CANCELLED, timezone.now(), actor
        row.save(update_fields=["status", "cancelled_at", "cancelled_by", "published_before"])
        if page is not None and page.published:
            EventPage.objects.filter(pk=page.pk).update(published=False, updated_at=timezone.now())
        record_church(row.series.church, actor, "serie_fecha", "Dijo que un día de una serie no hay", row,
                      event_id=row.event_id, reason="no_hay")
    return row


def restore(occurrence: SeriesOccurrence, actor) -> SeriesOccurrence:
    """"Sí hay": undo ``call_off``. The page is published again only if it was when the day was called off."""
    with transaction.atomic():
        row, page = _locked_occurrence(occurrence)
        if row.status != CANCELLED:
            return row
        row.status, row.cancelled_at, row.cancelled_by = ACTIVE, None, None
        row.save(update_fields=["status", "cancelled_at", "cancelled_by"])
        if page is not None and row.published_before and not page.published:
            EventPage.objects.filter(pk=page.pk).update(published=True, updated_at=timezone.now())
        record_church(row.series.church, actor, "serie_fecha", "Dijo que un día de una serie sí hay", row,
                      event_id=row.event_id, reason="si_hay")
    return row


# ---- what people read ---------------------------------------------------------------------------
def collapse(events: list[Event]) -> list[Event]:
    """A list of events without the same thing eight times: of each series, the next date only, with
    ``event.series`` (so the row can say "se repite cada domingo") and ``event.series_more`` (how many more
    dates the list had). A date this person signed up for, or waits for, always keeps its own row."""
    shown, firsts = [], {}
    for event in events:
        row = occurrence_of(event)
        event.series, event.series_more = (row.series if row else None), 0
        event.called_off = bool(row and row.cancelled)
        event.series_rule = rule_words(row.series) if row else ""
        mine = getattr(event, "my_place", None) or getattr(event, "my_wait", None)
        if row is None or mine:
            shown.append(event)
        elif row.series_id in firsts:
            firsts[row.series_id].series_more += 1
        else:
            firsts[row.series_id] = event
            shown.append(event)
    return shown
