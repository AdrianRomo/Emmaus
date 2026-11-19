from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from core import tenancy
from core.onboarding import for_church_role

from ..access import resolve_access
from ..forms import EventForm, StationChoiceForm
from ..models import Event, Interview, InterviewStation
from ..services import audit
from ..services.event_questionnaires import use_version
from ..services.questionnaire import DEFAULT_CAMP_QUESTIONNAIRE, create_questionnaire_from_definition
from ..services.metrics import STALE_MINUTES
from .common import EVERYONE, event_view, remember_station, role_home_url, safe_next


@login_required
def events_home(request):
    """Entry point after login: go straight to the last event, the only one in course or the only one, else list them."""
    # A church screen: the events of the church this request acts in, and those where the account holds an event
    # role of its own (core.tenancy.listed_events). An account of one church gets the list it always had.
    where = tenancy.resolve(request)
    events = list(tenancy.listed_events(request, where).order_by("-starts_on", "name"))
    here = where.church.pk if where.church is not None and where.others else None
    if here is not None:
        events.sort(key=lambda event: event.church_id != here)  # stable: this church's first, then the others
    can_create = tenancy.church_for_new_event(request) is not None
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
            # Said aloud, for an account of several churches: an event of another church than the one it works in.
            rows.append({"event": event, "access": access, "other_church": event.church if here not in (None, event.church_id) else None})
    # The welcome of the church role (or of someone with no role yet), the first time; put away with "Entendido"
    welcome = for_church_role(request.user, where.role, can_create_event=can_create)
    return render(request, "events/events_list.html", {"rows": rows, "can_create": can_create, "welcome": welcome})


@login_required
def live_camps(request):
    """«¿A qué campamento entras?»: the camps underway that this person administers, each to its administration home,
    and «Todos los eventos». One left: straight to it; none: the usual home."""
    from ..services.landing import live_camps_administered

    camps = live_camps_administered(request.user)
    if len(camps) == 1:
        return redirect("events:admin_home", slug=camps[0].slug)
    if not camps:
        return redirect("dashboard")
    return render(request, "events/live_camps.html", {"camps": camps})


@login_required
def event_create(request):
    # In the church this request acts in, and only for who administers it (core.tenancy.church_for_new_event).
    church = tenancy.church_for_new_event(request)
    if church is None:
        raise PermissionDenied
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
    # No "shell" key here: the name is the redesigned shell's (core.context_processors), and a string in its place
    # drew an empty rail on this page, also for a church that keeps Atrio's bar.
    # The computers where someone else has an interview moving now (activity in the last STALE_MINUTES) are marked
    # "En uso", so two laptops do not take the same name; who it is is not said
    since = timezone.now() - timedelta(minutes=STALE_MINUTES)
    in_use = set(
        Interview.objects.filter(event=event, status=Interview.Status.IN_PROGRESS, last_activity_at__gte=since, station__isnull=False)
        .exclude(conducted_by=request.user)
        .values_list("station_id", flat=True)
    )
    return render(request, "events/choose_station.html", {"form": form, "next_url": next_url, "in_use": in_use})
