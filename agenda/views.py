"""Eventos: what the church published for its people, signing up, and who came.

Every view goes through ``groups.views.community_view`` (the ``community`` module on, the person
the account is in this church) or ``church_admin_view`` (church ``manage``). Events are always
fetched inside ``request.church``, so a slug of another church is a 404. No view here reads or
writes anything of the camp: no participant, no membership, no interview.
"""

from django.contrib import messages
from django.db.models import Count, F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from events.models import Event
from events.services import throttle
from groups.views import church_admin_view, community_view

from . import services
from .forms import EventPageForm
from .models import EventPage, EventRegistration

ACTIVE = EventRegistration.Status.ACTIVE

# Why there is no button, said to the person. "inscrita" and "abierta" have their own panels.
STATE_NOTICES = {
    "terminado": services.ENDED,
    "sin_publicar": "Esta página todavía no está publicada: solo la administración de la iglesia la ve.",
    "sin_inscripcion": services.NO_REGISTRATION,
    "cerro": services.REGISTRATION_OVER,
    "llena": services.FULL + " Si quieres ir, pregunta en tu iglesia.",
}


def _event(request, slug: str) -> Event:
    return get_object_or_404(Event, church=request.church, slug=slug)


def _seen_event(request, slug: str):
    """The event, its page and the person's active registration, or 404 when this person may not
    see it: a member sees what is published (and active), or what they are registered for until it
    ends; the administration sees every event of the church."""
    event = _event(request, slug)
    page = EventPage.objects.filter(event=event).first()
    registration = EventRegistration.objects.filter(event=event, person=request.person, status=ACTIVE).first()
    published = page is not None and page.published and event.status == Event.Status.ACTIVE
    mine = registration is not None and not services.has_ended(event)
    if not (published or mine or request.is_church_admin):
        raise Http404
    return event, page, registration


# ---- the person ---------------------------------------------------------------------------------
@community_view
def event_list(request):
    events = services.upcoming_for(request.person, request.church)
    return render(
        request,
        "agenda/list.html",
        {
            "mine": [event for event in events if event.my_registration],
            "coming": [event for event in events if not event.my_registration],
        },
    )


@community_view
def detail(request, slug: str):
    event, page, registration = _seen_event(request, slug)
    today = timezone.localdate()
    taken = services.taken(event)
    state = services.state_of(event, page, registration, taken, today)
    return render(
        request,
        "agenda/detail.html",
        {
            "event": event,
            "page": page,
            "registration": registration,
            "state": state,
            "notice": STATE_NOTICES.get(state),
            "can_cancel": state == "inscrita" and not services.has_started(event, today),
            "places_left": services.places_left(page, taken) if page and page.registration_open else None,
        },
    )


@community_view
@require_POST
def register(request, slug: str):
    event, _, _ = _seen_event(request, slug)
    wait = throttle.consume(f"event-register:{request.user.pk}", limit=10, window_seconds=3600, block_seconds=600)
    if wait:
        messages.error(request, throttle.wait_message(wait))
        return redirect("agenda:detail", event.slug)
    try:
        services.register(event, request.person, request.user)
    except services.AgendaError as error:
        messages.error(request, error.message)
        return redirect("agenda:detail", event.slug)
    messages.success(request, f"Listo. Te inscribiste a {event.name}. Tu iglesia ya sabe que quieres ir.")
    return redirect("agenda:detail", event.slug)


@community_view
def cancel(request, slug: str):
    event = _event(request, slug)
    registration = get_object_or_404(EventRegistration, event=event, person=request.person, status=ACTIVE)
    if services.has_started(event):
        messages.error(request, services.STARTED)
        return redirect("agenda:detail", event.slug)
    if request.method == "POST":
        try:
            services.cancel(registration, request.user)
        except services.AgendaError as error:
            messages.error(request, error.message)
            return redirect("agenda:detail", event.slug)
        messages.success(request, f"Cancelaste tu inscripción a {event.name}.")
        return redirect("agenda:list")
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Quieres cancelar tu inscripción a {event.name}?",
            "text": "Tu lugar quedará libre para alguien más. Si cambias de opinión, puedes inscribirte otra vez mientras haya lugar.",
            "action": "Sí, cancelar mi inscripción",
            "back_url": reverse("agenda:detail", args=[event.slug]),
        },
    )


# ---- the administration -------------------------------------------------------------------------
@church_admin_view
def admin_list(request):
    events = list(
        Event.objects.filter(church=request.church)
        .select_related("page")
        .annotate(signed=Count("registrations", filter=Q(registrations__status=ACTIVE)))
        .order_by(F("starts_on").desc(nulls_last=True), "-id")
    )
    for event in events:
        event.published = getattr(event, "page", None) is not None and event.page.published
    return render(request, "agenda/admin_list.html", {"events": events})


@church_admin_view
def page_edit(request, slug: str):
    event = _event(request, slug)
    page = EventPage.objects.filter(event=event).first()
    was_published = bool(page and page.published)
    form = EventPageForm(request.POST or None, instance=page or EventPage(event=event), event=event)
    if request.method == "POST" and form.is_valid():
        try:
            saved = services.save_page(event, request.user, **form.cleaned_data)
        except services.AgendaError as error:
            form.add_error(None, error.message)
        else:
            if saved.published and not was_published:
                messages.success(request, f"Publicaste {event.name} para la iglesia.")
            else:
                messages.success(request, "Guardamos la página del evento.")
            return redirect("agenda:admin_list")
    return render(request, "agenda/page_form.html", {"form": form, "event": event, "page": page})


@church_admin_view
def registrations(request, slug: str):
    """Who signed up, with how to reach them, and from the event's first day on, who came."""
    event = _event(request, slug)
    if request.method == "POST":
        try:
            saved = services.record_attendance(event, request.POST.getlist("vino"), request.user)
        except services.AgendaError as error:
            messages.error(request, error.message)
        else:
            came = "Vino 1" if saved.present == 1 else f"Vinieron {saved.present}"
            messages.success(request, f"{came} de {saved.total} inscrito{'' if saved.total == 1 else 's'}.")
        return redirect("agenda:registrations", event.slug)
    rows = list(
        EventRegistration.objects.filter(event=event, status=ACTIVE)
        .select_related("person")
        .order_by("person__first_name", "person__last_name", "id")
    )
    recorded = [row for row in rows if row.attended is not None]
    return render(
        request,
        "agenda/registrations.html",
        {
            "event": event,
            "page": EventPage.objects.filter(event=event).first(),
            "rows": rows,
            "cancelled": EventRegistration.objects.filter(event=event, status=EventRegistration.Status.CANCELLED).count(),
            "started": services.has_started(event),
            "recorded": bool(recorded),
            "present": sum(1 for row in rows if row.attended),
        },
    )
