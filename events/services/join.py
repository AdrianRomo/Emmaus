"""Finding an event from what someone typed: the code on the printed sign, or the event's name."""

from __future__ import annotations

import re
import unicodedata
from datetime import timedelta

from django.utils import timezone
from django.utils.text import slugify

from ..models import AccessRequest, Event

# Longer than any slug (80) or code (8): anything past this cannot match, so it is not sent to the database.
MAX_KEY_LENGTH = 160
# Decided requests are kept a month (who approved what stays in the activity log after that).
DECIDED_REQUEST_DAYS = 30


def _name_key(text: str) -> str:
    """'Campamento de Jóvenes 2026', 'jovenes_2026', 'JÓVENES-2026' -> one form: accents, case and separators gone."""
    return re.sub(r"-+", "-", slugify(text).replace("_", "-")).strip("-")


def find_event_by_key(text) -> Event | None:
    """The event whose join code, slug or name matches ``text``; ``None`` when nothing does. Never raises.

    The code matches in any case (and in full-width characters). A name matches the slug or the event's
    name however it was typed: accents, case, spaces, hyphens and underscores do not matter. A name only
    works while ``join_by_name`` is on (a new code turns it off). An exact slug wins; otherwise ties go to
    the oldest event. Archived events are never found.
    """
    value = unicodedata.normalize("NFKC", " ".join(str(text or "").split()))  # also strips the ends
    if not value or len(value) > MAX_KEY_LENGTH or "\x00" in value:
        return None
    events = Event.objects.exclude(status=Event.Status.ARCHIVED).select_related("church")
    event = events.filter(join_code__iexact=value.upper()).first()
    if event is not None:
        return event
    named = events.filter(join_by_name=True).order_by("pk")
    exact = named.filter(slug__iexact=value).first()
    if exact is not None:
        return exact
    key = _name_key(value)
    if not key:
        return None
    # Tens of events at most: compared in Python, so the database never sees a pattern built from user input.
    return next((e for e in named if key in (_name_key(e.slug), _name_key(e.name))), None)


def purge_decided_requests(now=None) -> int:
    """Delete approved and rejected requests decided more than DECIDED_REQUEST_DAYS ago. Pending ones stay."""
    cutoff = (now or timezone.now()) - timedelta(days=DECIDED_REQUEST_DAYS)
    deleted, _ = AccessRequest.objects.exclude(status=AccessRequest.Status.PENDING).filter(decided_at__lt=cutoff).delete()
    return deleted
