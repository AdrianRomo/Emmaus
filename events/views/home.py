from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse

from ..access import accessible_events, administered_churches, resolve_access
from ..forms import EventForm, StationChoiceForm
from ..models import Event, InterviewStation
from ..services import audit
from ..services.event_questionnaires import use_version
from ..services.questionnaire import DEFAULT_CAMP_QUESTIONNAIRE, create_questionnaire_from_definition
from .common import COORDINATOR, EVERYONE, event_view, remember_station, role_home_url, safe_next


@login_required
def events_home(request):
    """Entry point after login: go straight to the last event, the only one in course or the only one, else list them."""
    events = list(accessible_events(request.user).order_by("-starts_on", "name"))
    can_create = administered_churches(request.user).exists()
    last = request.session.get("current_event")
    if not request.GET.get("todos"):
        preferred = next((e for e in events if e.slug == last), None)
        active = [e for e in events if e.status == Event.Status.ACTIVE]
        if preferred is None and len(active) == 1:  # closed and draft events do not hide the camp in course
            preferred = active[0]
        if preferred is None and len(events) == 1:
            preferred = events[0]
        if preferred is not None and preferred.status != Event.Status.ARCHIVED:
            return redirect("events:home", slug=preferred.slug)
    rows = []
    for event in events:
        access = resolve_access(request.user, event)
        if access:
            rows.append({"event": event, "access": access})
    return render(request, "events/events_list.html", {"rows": rows, "can_create": can_create})


@login_required
def event_create(request):
    churches = list(administered_churches(request.user))
    if not churches:
        raise PermissionDenied
    church = churches[0]
    form = EventForm(request.POST or None, initial={"kind": Event.Kind.CAMP, "status": Event.Status.ACTIVE})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            event = form.save(commit=False)
            event.church = church
            event.created_by = request.user
            questionnaire = (
                church.questionnaires.filter(versions__status="publicada").order_by("-created_at").first()
            )
            if questionnaire:
                version = questionnaire.versions.filter(status="publicada").order_by("-number").first()
            else:
                version = create_questionnaire_from_definition(church, DEFAULT_CAMP_QUESTIONNAIRE, request.user)
            event.save()
            use_version(event, version)  # the event starts with one questionnaire; Cuestionarios adds more
            for number in range(1, 11):
                InterviewStation.objects.create(event=event, number=number, name=f"Computadora {number}")
            audit.record(request.user, event, "evento_creado", f"Creó el evento {event.name}", event)
        messages.success(request, f"Listo. {event.name} ya está creado con 10 computadoras de entrevista.")
        return redirect("events:admin_home", slug=event.slug)
    return render(request, "events/event_form.html", {"form": form, "church": church})


@event_view(*EVERYONE)
def event_home(request, event, access):
    return redirect(role_home_url(event, access))


@event_view(*EVERYONE)
def choose_station(request, event, access):
    if not access.can_conduct_interviews:
        raise PermissionDenied
    form = StationChoiceForm(request.POST or None, event=event)
    next_url = safe_next(
        request, request.GET.get("siguiente") or request.POST.get("siguiente"), reverse("events:interview_search", args=[event.slug])
    )
    if request.method == "POST" and form.is_valid():
        remember_station(request, event, form.cleaned_data["station"])
        messages.success(request, f"Esta computadora quedó registrada como {form.cleaned_data['station'].name}.")
        return redirect(next_url)
    return render(request, "events/choose_station.html", {"form": form, "next_url": next_url, "shell": "bar" if access.role == COORDINATOR else "rail"})
