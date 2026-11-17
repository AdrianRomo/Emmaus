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

from events.models import Event, normalize_name
from events.services import throttle
from groups.views import church_admin_view, community_view
from people import flags
from people.models import Person

from . import services
from .forms import BirthDateForm, ConsentForm, EventFaqForm, EventPageForm, EventScheduleItemForm, WalkUpForm
from .models import EventFaq, EventPage, EventRegistration, EventScheduleItem

ACTIVE = EventRegistration.Status.ACTIVE
PENDING = EventRegistration.Status.PENDING
HOLDING = EventRegistration.HOLDING

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
    """The event, its page and the person's registration (confirmed, or waiting for a guardian's
    authorization), or 404 when this person may not
    see it: a member sees what is published (and active), or what they are registered for until it
    ends; the administration sees every event of the church."""
    event = _event(request, slug)
    page = EventPage.objects.filter(event=event).first()
    registration = (
        EventRegistration.objects.filter(event=event, person=request.person, status__in=HOLDING)
        .select_related("consent").first()
    )
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
            "mine": [event for event in events if event.my_place],
            "coming": [event for event in events if not event.my_place],
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
            "can_cancel": state in ("inscrita", "pendiente") and not services.has_started(event, today),
            "places_left": services.places_left(page, taken) if page and page.registration_open else None,
            # The member reads one word about the authorization, never who gave it or how.
            "authorized": state == "inscrita" and registration.valid_consent is not None,
            "pending_text": services.PENDING_NEXT,
            "faqs": EventFaq.objects.filter(event=event),
            "schedule": services.schedule_by_day(event),
        },
    )


@community_view
@require_POST
def register(request, slug: str):
    event, page, _ = _seen_event(request, slug)
    wait = throttle.consume(f"event-register:{request.user.pk}", limit=10, window_seconds=3600, block_seconds=600)
    if wait:
        messages.error(request, throttle.wait_message(wait))
        return redirect("agenda:detail", event.slug)
    # The date of birth is asked only when the event needs it and the church does not know it.
    asks = page is not None and page.requires_guardian_consent and request.person.birth_date is None
    form = BirthDateForm(request.POST) if asks and "birth_date" in request.POST else None
    try:
        if form is not None and form.is_valid():
            services.set_birth_date(request.person, form.cleaned_data["birth_date"], request.user)
        if form is None or form.is_valid():
            registration = services.register(event, request.person, request.user)
    except services.AgendaError as error:
        if error.code == "birth_date_needed":
            form = BirthDateForm()
        elif error.code == "birth_date" and form is not None:
            form.add_error("birth_date", error.message)
        else:
            messages.error(request, error.message)
            return redirect("agenda:detail", event.slug)
    if form is not None and (not form.is_bound or not form.is_valid()):
        return render(request, "agenda/birth_date.html", {"form": form, "event": event})
    if registration.is_pending:
        messages.info(request, f"Recibimos tu inscripción a {event.name}. {services.PENDING_NEXT}")
    else:
        messages.success(request, f"Listo. Te inscribiste a {event.name}. Tu iglesia ya sabe que quieres ir.")
    return redirect("agenda:detail", event.slug)


@community_view
def cancel(request, slug: str):
    event = _event(request, slug)
    registration = get_object_or_404(EventRegistration, event=event, person=request.person, status__in=HOLDING)
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
        .annotate(
            signed=Count("registrations", filter=Q(registrations__status=ACTIVE)),
            waiting=Count("registrations", filter=Q(registrations__status=PENDING)),
        )
        .order_by(F("starts_on").desc(nulls_last=True), "-id")
    )
    for event in events:
        event.published = getattr(event, "page", None) is not None and event.page.published
        event.ended = event.starts_on is not None and services.has_ended(event)
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
    return render(
        request,
        "agenda/page_form.html",
        {
            "form": form, "event": event, "page": page,
            "faq_count": EventFaq.objects.filter(event=event).count(),
            "schedule_count": EventScheduleItem.objects.filter(event=event).count(),
        },
    )


def _registration(event: Event, registration_id: int, **filters) -> EventRegistration:
    return get_object_or_404(
        EventRegistration.objects.select_related("person", "consent"), event=event, pk=registration_id, **filters
    )


def _back_to_list(event: Event) -> str:
    return reverse("agenda:registrations", args=[event.slug])


@church_admin_view
def registrations(request, slug: str):
    """Who signed up or arrived, with how to reach them; first who waits for a guardian's
    authorization; and from the event's first day on, who came."""
    event = _event(request, slug)
    if request.method == "POST":
        try:
            saved = services.record_attendance(event, request.POST.getlist("vino"), request.user, request.POST.getlist("lista") or None)
        except services.AgendaError as error:
            messages.error(request, error.message)
        else:
            came = "Vino 1" if saved.present == 1 else f"Vinieron {saved.present}"
            messages.success(request, f"{came} de {saved.total} inscrito{'' if saved.total == 1 else 's'}.")
        return redirect("agenda:registrations", event.slug)
    held = sorted(
        EventRegistration.objects.filter(event=event, status__in=HOLDING).select_related("person", "consent"),
        key=lambda row: (normalize_name(row.display_name), row.pk),
    )
    rows = [row for row in held if row.is_active]
    recorded = [row for row in rows if row.attended is not None]
    return render(
        request,
        "agenda/registrations.html",
        {
            "event": event,
            "page": EventPage.objects.filter(event=event).first(),
            "rows": rows,
            "waiting": [row for row in held if row.is_pending],
            "cancelled": EventRegistration.objects.filter(event=event, status=EventRegistration.Status.CANCELLED).count(),
            "started": services.has_started(event),
            "recorded": bool(recorded),
            "present": sum(1 for row in rows if row.attended),
            "people_on": flags.enabled(request.church, "people"),
        },
    )


@church_admin_view
def walkup(request, slug: str):
    """"Registrar a alguien que llegó": a person of this church found by name, or a visitor by
    name. Nobody needs an account to be written down."""
    event = _event(request, slug)
    back = _back_to_list(event)
    if not services.has_started(event):
        messages.error(request, "El evento todavía no empieza. Aquí registras a quien llega desde su primer día.")
        return redirect(back)
    page = EventPage.objects.filter(event=event).first()
    people = Person.objects.filter(church=request.church, merged_into__isnull=True)
    chosen = request.POST.get("persona") or request.GET.get("persona")
    person = get_object_or_404(people, pk=chosen) if chosen and chosen.isdigit() else None
    minor_known = None
    if person is not None and person.birth_date is not None:
        minor_known = services.is_minor_at(event, person.birth_date)
    form = WalkUpForm(
        request.POST or None, person=person, minor_known=minor_known,
        requires_consent=bool(page and page.requires_guardian_consent),
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            registration = services.register_walkup(
                event, request.user, person=person, first_name=data.get("first_name", ""),
                last_name=data.get("last_name", ""), phone=data.get("phone", ""), is_minor=data["is_minor"],
                consent=data["consent"],
            )
        except services.AgendaError as error:
            form.add_error(None, error.message)
        else:
            if registration.is_pending:
                messages.info(
                    request,
                    f"Registraste a {registration.display_name}. Queda pendiente hasta que registres la autorización "
                    "de su madre, padre o tutor.",
                )
            else:
                messages.success(request, f"Registraste a {registration.display_name}. Ya quedó en la lista como que vino.")
            return redirect(back)
    query = request.GET.get("q", "").strip()
    found = []
    if query and person is None:
        here = EventRegistration.objects.filter(event=event, status__in=HOLDING, person__isnull=False)
        matches = people.exclude(pk__in=here.values("person_id"))
        for token in normalize_name(query).split():
            matches = matches.filter(normalized_name__contains=token)
        found = list(matches.order_by("first_name", "last_name", "id")[:20])
    return render(
        request,
        "agenda/walkup.html",
        {"event": event, "page": page, "form": form, "person": person, "minor_known": minor_known, "query": query,
         "found": found, "back": back},
    )


@church_admin_view
def consent(request, slug: str, registration_id: int):
    """"Registrar autorización": who authorized a minor for this event and how the team knows."""
    event = _event(request, slug)
    registration = _registration(event, registration_id, status=PENDING)
    form = ConsentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.record_consent(registration, request.user, **form.cleaned_data)
        except services.AgendaError as error:
            form.add_error(None, error.message)
        else:
            messages.success(request, f"Registraste la autorización. La inscripción de {registration.display_name} quedó confirmada.")
            return redirect(_back_to_list(event))
    return render(request, "agenda/consent_form.html", {"event": event, "registration": registration, "form": form,
                                                        "back": _back_to_list(event)})


@church_admin_view
def consent_revoke(request, slug: str, registration_id: int):
    event = _event(request, slug)
    registration = _registration(event, registration_id, status=ACTIVE, consent__revoked_at__isnull=True)
    if request.method == "POST":
        try:
            services.revoke_consent(registration, request.user)
        except services.AgendaError as error:
            messages.error(request, error.message)
        else:
            messages.success(request, f"Retiraste la autorización. La inscripción de {registration.display_name} quedó pendiente.")
        return redirect(_back_to_list(event))
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Quieres retirar la autorización de {registration.display_name}?",
            "text": "Su inscripción volverá a quedar pendiente y conservará su lugar. Si ya la habías marcado como que vino, esa marca se quita.",
            "action": "Sí, retirar la autorización",
            "back_url": _back_to_list(event),
        },
    )


@church_admin_view
def guest_promote(request, slug: str, registration_id: int):
    """"Agregar a personas de la iglesia": first whoever may already be this visitor, then the new person."""
    event = _event(request, slug)
    if not flags.enabled(request.church, "people"):
        raise Http404
    registration = _registration(event, registration_id, status__in=HOLDING, person__isnull=True)
    if request.method == "POST":
        existing = None
        chosen = request.POST.get("persona", "")
        if chosen:
            existing = get_object_or_404(Person, church=request.church, merged_into__isnull=True, pk=chosen if chosen.isdigit() else 0)
        try:
            person = services.promote_guest(registration, request.user, existing=existing)
        except services.AgendaError as error:
            messages.error(request, error.message)
            return redirect("agenda:guest_promote", event.slug, registration.pk)
        if existing is None:
            messages.success(request, f"Agregaste a {person.full_name} a las personas de la iglesia.")
        else:
            messages.success(request, f"Listo. Esta inscripción ahora es de {person.full_name}.")
        return redirect(_back_to_list(event))
    return render(
        request,
        "agenda/guest_promote.html",
        {"event": event, "registration": registration, "similar": services.similar_people(registration),
         "back": _back_to_list(event)},
    )


# ---- frequent questions and the programme -------------------------------------------------------
def _page_url(event: Event) -> str:
    return reverse("agenda:page_edit", args=[event.slug])


@church_admin_view
def faq(request, slug: str):
    event = _event(request, slug)
    faqs = list(EventFaq.objects.filter(event=event))
    form = EventFaqForm(request.POST or None, initial={"position": len(faqs) + 1})
    if request.method == "POST" and form.is_valid():
        services.save_faq(event, request.user, **form.cleaned_data)
        messages.success(request, "Agregaste la pregunta.")
        return redirect("agenda:faq", event.slug)
    return render(request, "agenda/faq.html", {"event": event, "faqs": faqs, "form": form, "back": _page_url(event)})


@church_admin_view
def faq_edit(request, slug: str, faq_id: int):
    event = _event(request, slug)
    item = get_object_or_404(EventFaq, event=event, pk=faq_id)
    form = EventFaqForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        # The form validates; the service writes (``item`` is re-read so the form's assignment is not what is saved).
        services.save_faq(event, request.user, EventFaq.objects.get(pk=item.pk), **form.cleaned_data)
        messages.success(request, "Guardamos la pregunta.")
        return redirect("agenda:faq", event.slug)
    return render(
        request,
        "agenda/item_form.html",
        {"event": event, "form": form, "title": "Editar la pregunta", "back": reverse("agenda:faq", args=[event.slug]),
         "back_label": "Volver a las preguntas", "delete_url": reverse("agenda:faq_delete", args=[event.slug, item.pk]),
         "delete_label": "Borrar esta pregunta"},
    )


@church_admin_view
def faq_delete(request, slug: str, faq_id: int):
    event = _event(request, slug)
    item = get_object_or_404(EventFaq, event=event, pk=faq_id)
    if request.method == "POST":
        services.delete_faq(item, request.user)
        messages.success(request, "Borraste la pregunta.")
        return redirect("agenda:faq", event.slug)
    return render(
        request,
        "groups/confirm.html",
        {"title": "¿Quieres borrar esta pregunta?", "text": item.question, "action": "Sí, borrar la pregunta",
         "back_url": reverse("agenda:faq", args=[event.slug])},
    )


@church_admin_view
def schedule(request, slug: str):
    event = _event(request, slug)
    form = EventScheduleItemForm(request.POST or None, event=event)
    if request.method == "POST" and form.is_valid():
        try:
            services.save_schedule_item(event, request.user, **form.cleaned_data)
        except services.AgendaError as error:
            form.add_error(None, error.message)
        else:
            messages.success(request, "Agregaste el momento al programa.")
            return redirect("agenda:schedule", event.slug)
    return render(
        request,
        "agenda/schedule.html",
        {"event": event, "days": services.schedule_by_day(event), "form": form, "back": _page_url(event)},
    )


@church_admin_view
def schedule_edit(request, slug: str, item_id: int):
    event = _event(request, slug)
    item = get_object_or_404(EventScheduleItem, event=event, pk=item_id)
    form = EventScheduleItemForm(request.POST or None, instance=item, event=event)
    if request.method == "POST" and form.is_valid():
        try:
            services.save_schedule_item(event, request.user, EventScheduleItem.objects.get(pk=item.pk), **form.cleaned_data)
        except services.AgendaError as error:
            form.add_error(None, error.message)
        else:
            messages.success(request, "Guardamos el programa.")
            return redirect("agenda:schedule", event.slug)
    return render(
        request,
        "agenda/item_form.html",
        {"event": event, "form": form, "title": "Editar el momento del programa",
         "back": reverse("agenda:schedule", args=[event.slug]), "back_label": "Volver al programa",
         "delete_url": reverse("agenda:schedule_delete", args=[event.slug, item.pk]), "delete_label": "Quitar del programa"},
    )


@church_admin_view
def schedule_delete(request, slug: str, item_id: int):
    event = _event(request, slug)
    item = get_object_or_404(EventScheduleItem, event=event, pk=item_id)
    if request.method == "POST":
        services.delete_schedule_item(item, request.user)
        messages.success(request, "Quitaste el momento del programa.")
        return redirect("agenda:schedule", event.slug)
    return render(
        request,
        "groups/confirm.html",
        {"title": "¿Quieres quitar este momento del programa?", "text": item.title, "action": "Sí, quitarlo",
         "back_url": reverse("agenda:schedule", args=[event.slug])},
    )
