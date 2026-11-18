"""Where an ordinary sign-in lands for someone who administers a camp (owner decision).

When exactly one camp this person administers is underway today (in course, and today between its first and last
day), the sign-in opens that camp's administration home; with several, a chooser; with none, the usual home. Only an
ordinary sign-in: a direct link (``next``) is always honoured first (Django's LoginView), and visiting the home later
is not redirected."""

from __future__ import annotations

from django.urls import reverse
from django.utils import timezone

from ..access import accessible_events, resolve_access
from ..models import Event, MembershipRole


def live_camps_administered(user) -> list[Event]:
    """The camps underway today that ``user`` administers (Administrador of the event, or of its church)."""
    today = timezone.localdate()
    camps = accessible_events(user).filter(kind=Event.Kind.CAMP, status=Event.Status.ACTIVE, starts_on__lte=today)
    live = []
    for event in camps.order_by("starts_on", "name"):
        if (event.ends_on or event.starts_on) < today:
            continue
        access = resolve_access(user, event)
        if access and access.role == MembershipRole.ADMIN:
            live.append(event)
    return live


def sign_in_destination(user) -> str | None:
    """The camp's administration home (one live camp), the chooser (several), or None (the usual home)."""
    live = live_camps_administered(user)
    if len(live) == 1:
        return reverse("events:admin_home", args=[live[0].slug])
    if len(live) > 1:
        return reverse("events:live_camps")
    return None
