"""The administration's page for the practice campero (issue #49): create it, pass it to a coordinador, remove it.

It shows who already practised and where the practice campero stands, never a word of any answer.
"""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse

from ..models import EventMembership
from ..services import practice as practice_svc
from .common import ADMIN, event_view

NOBODY = "nadie"


def _chosen_coordinator(event, raw: str) -> EventMembership | None:
    """The coordinador a form named, None for "nobody". A value that is not one of the event's raises PracticeError."""
    if raw in ("", NOBODY):
        return None
    membership = practice_svc.coordinators(event).filter(pk=int(raw)).first() if raw.isascii() and raw.isdigit() else None
    if membership is None:
        raise practice_svc.PracticeError("Elige a un coordinador con acceso a este evento.")
    return membership


@event_view(ADMIN)
def practice(request, event, access):
    here = reverse("events:practice", args=[event.slug])
    if request.method == "POST":
        action = request.POST.get("accion", "")
        try:
            if action == "crear":
                practice_svc.create(event, actor=request.user, coordinator=_chosen_coordinator(event, request.POST.get("coordinador", "")))
                messages.success(request, "Listo: el evento ya tiene su campero de práctica.")
            elif action == "pasar":
                coordinator = _chosen_coordinator(event, request.POST.get("coordinador", ""))
                practice_svc.hand_to(event, coordinator, actor=request.user)
                if coordinator is None:
                    messages.success(request, "El campero de práctica quedó como nuevo, sin coordinador.")
                else:
                    messages.success(request, f"El campero de práctica quedó como nuevo en la lista de {coordinator.display_name}.")
            elif action == "quitar":
                practice_svc.delete(event, actor=request.user)
                messages.success(request, "Se quitó el campero de práctica con sus entrevistas de práctica.")
            else:
                messages.error(request, "No entendimos qué querías hacer. Inténtalo otra vez.")
        except practice_svc.PracticeError as error:
            messages.error(request, error.message)
        return redirect(here)
    participant = practice_svc.get(event)
    practised = practice_svc.practised_user_ids(event)
    coordinators = list(practice_svc.coordinators(event))
    for coordinator in coordinators:
        coordinator.practised = coordinator.user_id in practised
    return render(
        request,
        "events/admin/practice.html",
        {
            "participant": participant,
            "coordinators": coordinators,
            "practised_count": sum(1 for c in coordinators if c.practised),
            "interviews": practice_svc.kept_interviews(participant) if participant else 0,
            "nobody": NOBODY,
        },
    )
