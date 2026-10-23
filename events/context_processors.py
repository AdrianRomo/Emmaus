from __future__ import annotations

from django.utils import timezone

from .views.common import current_station, nav_items


def event_context(request):
    access = getattr(request, "access", None)
    if access is None:
        return {}
    event = access.event
    match = getattr(request, "resolver_match", None)
    url_name = match.url_name if match else ""
    items = nav_items(event, access)
    for item in items:
        item["active"] = url_name in item["names"]
    return {
        "event": event,
        "access": access,
        "event_nav": items,
        "shell_template": "events/shell_bar.html" if access.is_coordinator else "events/shell_rail.html",
        "current_url_name": url_name,
        "today": timezone.localdate(),
        "station": current_station(request, event) if access.can_conduct_interviews else None,
    }
