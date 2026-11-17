"""Opinions about an event that ended. Who may give one: a person of the church who was signed up
and was not marked as absent, once, during the 30 days after the event's last day. Reading: totals
for the administration, and the comments only from ``MIN_TO_SHOW`` opinions on, so that a comment
cannot be traced to the one person who answered."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from agenda.models import EventRegistration
from events.models import Event

from .models import EventFeedback, FeedbackGiven

OPEN_DAYS = 30
MIN_TO_SHOW = 3
ACTIVE = EventRegistration.Status.ACTIVE


class FeedbackError(ValidationError):
    """A rule refused the opinion; the message is ready for the screen."""


def _last_day(event: Event):
    return event.ends_on or event.starts_on


def is_open(event: Event, today=None) -> bool:
    today = today or timezone.localdate()
    day = _last_day(event)
    return day is not None and day < today <= day + timedelta(days=OPEN_DAYS)


def _went(person, event: Event) -> bool:
    return EventRegistration.objects.filter(event=event, person=person, status=ACTIVE).exclude(attended=False).exists()


def can_give(person, event: Event, today=None) -> bool:
    return (
        is_open(event, today) and _went(person, event)
        and not FeedbackGiven.objects.filter(event=event, person=person).exists()
    )


def pending_for(person, church, today=None) -> list[Event]:
    """The events of ``church`` this person can still give an opinion on, most recent first."""
    today = today or timezone.localdate()
    since = today - timedelta(days=OPEN_DAYS)
    events = (
        Event.objects.filter(church=church, registrations__person=person, registrations__status=ACTIVE)
        .exclude(registrations__person=person, registrations__attended=False)
        .exclude(feedback_given__person=person)
        .distinct()
    )
    return sorted((e for e in events if is_open(e, today) and _last_day(e) >= since), key=_last_day, reverse=True)


def give(person, event: Event, *, rating: str, recommend: str, comment: str = "") -> None:
    if rating not in EventFeedback.Rating.values or recommend not in EventFeedback.Recommend.values:
        raise ValueError("Unknown answer.")
    if not is_open(event) or not _went(person, event):
        raise FeedbackError("Ya no se puede opinar sobre este evento.")
    try:
        with transaction.atomic():
            FeedbackGiven.objects.create(event=event, person=person)  # the unique row decides a double send
            EventFeedback.objects.create(event=event, rating=rating, recommend=recommend, comment=comment.strip()[:500])
    except IntegrityError:
        raise FeedbackError("Ya habías dado tu opinión de este evento. Gracias.") from None


def results(event: Event) -> dict:
    """Totals per answer, and the comments once there are enough opinions to hide whose each is."""
    rows = EventFeedback.objects.filter(event=event)
    total = rows.count()
    by_rating = dict(rows.values_list("rating").annotate(n=Count("id")))
    by_recommend = dict(rows.values_list("recommend").annotate(n=Count("id")))
    enough = total >= MIN_TO_SHOW
    return {
        "total": total,
        "enough": enough,
        "missing": max(MIN_TO_SHOW - total, 0),
        "ratings": [(label, by_rating.get(value, 0)) for value, label in EventFeedback.Rating.choices] if enough else [],
        "recommends": [(label, by_recommend.get(value, 0)) for value, label in EventFeedback.Recommend.choices] if enough else [],
        # Shuffled by the database's id order would follow the order of arrival: sort by text instead.
        "comments": sorted(c for c in rows.exclude(comment="").values_list("comment", flat=True)) if enough else [],
    }
