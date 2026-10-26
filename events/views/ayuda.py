from __future__ import annotations

from django.shortcuts import render

from .common import EVERYONE, event_view


@event_view(*EVERYONE)
def ayuda(request, event, access):
    """Help ("Ayuda", audit UX-17): the five tasks the caller's role asks about most, each with the screen where it happens."""
    return render(request, "events/ayuda.html", {"lede": f"Lo más común en {event.name}, en cinco pasos cortos."})
