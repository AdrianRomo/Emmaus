"""Opinions about an event that ended. Who may give one: a person of the church who was signed up
and was not marked as absent, once, during the days of ``people.policy.feedback_open_days`` after the event's last day. Reading: totals
and comments for the administration, only once those days passed (results that grew answer by
answer would show each new one) and only from the church's minimum reporting size on."""

import hashlib
import hmac
from datetime import timedelta

from django.conf import settings

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef
from django.utils import timezone

from agenda.models import EventRegistration
from events.models import Event
from people import policy

from .models import EventFeedback, FeedbackGiven

ACTIVE = EventRegistration.Status.ACTIVE


class FeedbackError(ValidationError):
    """A rule refused the opinion; the message is ready for the screen."""


def token_for(event: Event, person) -> str:
    """Who answered, as a keyed hash: enough to refuse a second answer, useless to list people."""
    message = f"feedback:{event.pk}:{person.pk}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def has_given(person, event: Event) -> bool:
    return FeedbackGiven.objects.filter(event=event, token=token_for(event, person)).exists()


def _open_days(event: Event) -> int:
    return policy.feedback_open_days(event.church)


def _last_day(event: Event):
    return event.ends_on or event.starts_on


def is_open(event: Event, today=None) -> bool:
    today = today or timezone.localdate()
    day = _last_day(event)
    return day is not None and day < today <= day + timedelta(days=_open_days(event))


def _went(person, event: Event) -> bool:
    return EventRegistration.objects.filter(event=event, person=person, status=ACTIVE).exclude(attended=False).exists()


def can_give(person, event: Event, today=None) -> bool:
    return (
        is_open(event, today) and _went(person, event)
        and not has_given(person, event)
    )


def pending_for(person, church, today=None) -> list[Event]:
    """The events of ``church`` this person can still give an opinion on, most recent first."""
    today = today or timezone.localdate()
    went = EventRegistration.objects.filter(event=OuterRef("pk"), person=person, status=ACTIVE).exclude(attended=False)
    events = [e for e in Event.objects.filter(Exists(went), church=church).select_related("church") if is_open(e, today)]
    return sorted((e for e in events if not has_given(person, e)), key=_last_day, reverse=True)


def give(person, event: Event, *, rating: str, recommend: str, comment: str = "") -> None:
    if rating not in EventFeedback.Rating.values or recommend not in EventFeedback.Recommend.values:
        raise ValueError("Unknown answer.")
    if not is_open(event) or not _went(person, event):
        raise FeedbackError("Ya no se puede opinar sobre este evento.")
    try:
        with transaction.atomic():
            FeedbackGiven.objects.create(event=event, token=token_for(event, person))  # the unique row decides a double send
            EventFeedback.objects.create(event=event, rating=rating, recommend=recommend, comment=comment.strip()[:500])
    except IntegrityError:
        raise FeedbackError("Ya habías dado tu opinión de este evento. Gracias.") from None


def results(event: Event, today=None) -> dict:
    """Totals per answer and the comments, once nobody can answer any more and there are enough
    opinions to hide whose each is (the church's minimum reporting size). Before that: only how
    many arrived. An event that never reaches the minimum never shows its answers."""
    rows = EventFeedback.objects.filter(event=event)
    total = rows.count()
    by_rating = dict(rows.values_list("rating").annotate(n=Count("pk")))
    by_recommend = dict(rows.values_list("recommend").annotate(n=Count("pk")))
    still_open = is_open(event, today)
    minimum = policy.min_reporting_size(event.church)
    enough = total >= minimum and not still_open
    closes_on = _last_day(event) + timedelta(days=_open_days(event)) if still_open else None
    return {
        "total": total,
        "enough": enough,
        "minimum": minimum,
        "open": still_open,
        "closes_on": closes_on,
        "ratings": [(label, by_rating.get(value, 0)) for value, label in EventFeedback.Rating.choices] if enough else [],
        "recommends": [(label, by_recommend.get(value, 0)) for value, label in EventFeedback.Recommend.choices] if enough else [],
        "comments": sorted(c for c in rows.exclude(comment="").values_list("comment", flat=True)) if enough else [],
    }
