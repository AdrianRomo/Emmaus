""""Ahora y después": what the programme of an event says for this moment, for the camp's homes.

The programme (``EventScheduleItem``: day, hour, what, where) is written by the administration in "Eventos de la
iglesia", "Programa", and is public to everybody of the event; this reads today's rows in the event's own time zone
and says what is happening and what follows. Nothing here is about a person. The camp's homes show it only when the
church has the ``community`` module on (the programme lives there): off, they are what they were, byte for byte.

Rules, in words: an item with an end runs until that hour; an item without one runs until the next item of the day
starts (the last one, until the day ends); when two overlap, "ahora" is the one that started last.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from django.utils import timezone

from events.models import Event
from events.services.metrics import local_tz
from people import flags

from .models import EventScheduleItem


@dataclass(frozen=True)
class NowNext:
    """What the strip says. ``over``: the day had a programme and its last item ended."""

    now: EventScheduleItem | None = None
    now_until: time | None = None  # only an end the administration wrote: never one worked out from the next item
    next: EventScheduleItem | None = None
    over: bool = False


def _end(items: list[EventScheduleItem], index: int) -> time | None:
    """When item ``index`` stops being "ahora"; None: it runs until the day ends."""
    item = items[index]
    if item.ends_at and item.ends_at > item.starts_at:
        return item.ends_at
    if item.ends_at:  # an end before its start: it crosses midnight, so today it does not end
        return None
    later = [other.starts_at for other in items[index + 1:] if other.starts_at > item.starts_at]
    return later[0] if later else None


def now_and_next(event: Event, now: datetime | None = None) -> NowNext | None:
    """None when there is nothing to say: the module is off, or today (in the event's time zone) has no programme."""
    if not flags.enabled(event.church, "community"):
        return None
    local = (now or timezone.now()).astimezone(local_tz(event))
    items = list(EventScheduleItem.objects.filter(event=event, day=local.date()).order_by("starts_at", "id"))
    if not items:
        return None
    clock = local.time()
    current = None
    for index, item in enumerate(items):
        end = _end(items, index)
        if item.starts_at <= clock and (end is None or clock < end):
            current = item  # the one that started last wins
    following = next((item for item in items if item.starts_at > clock), None)
    if current is None and following is None:
        return NowNext(over=True)
    written_end = current.ends_at if current and current.ends_at and current.ends_at > current.starts_at else None
    return NowNext(now=current, now_until=written_end, next=following)
