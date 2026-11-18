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
from people import flags, policy
from people.models import Person

from . import services
from .forms import (
    AnnulForm,
    BirthDateForm,
    ConfirmAgeForm,
    ConsentForm,
    EventFaqForm,
    EventPageForm,
    EventScheduleItemForm,
    GuestAgeForm,
    WalkUpForm,
)
from .models import EventFaq, EventPage, EventRegistration, EventScheduleItem

ACTIVE = EventRegistration.Status.ACTIVE
PENDING = EventRegistration.Status.PENDING
HOLDING = EventRegistration.HOLDING
LISTED = EventRegistration.LISTED
AGE = services.AGE

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


def _base_url(request) -> str:
    """The address of this Emmaus as the person reached it: what the link of an e-mail starts with."""
    return request.build_absolute_uri("/")


def _seen_event(request, slug: str):
    """The event, its page and the person's registration (confirmed, waiting for a guardian's
    authorization, or on the waiting list), or 404 when this person may not
    see it: a member sees what is published (and active), or what they are registered for until it
    ends; the administration sees every event of the church."""
    event = _event(request, slug)
    page = EventPage.objects.filter(event=event).first()
    registration = (
        EventRegistration.objects.filter(event=event, person=request.person, status__in=(*HOLDING, *LISTED))
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
            "mine": [event for event in events if event.my_place or event.my_wait],
            "coming": [event for event in events if not (event.my_place or event.my_wait)],
        },
    )


@community_view
def detail(request, slug: str):
    event, page, registration = _seen_event(request, slug)
    today = timezone.localdate()
    if registration is not None and registration.is_offered and not services.offer_is_open(registration):
        # The deadline passed and nobody moved the list yet: release the place and offer it to the next one.
        services.advance(event, base_url=_base_url(request))
        registration = None
    taken = services.taken(event)
    state = services.state_of(event, page, registration, taken, today)
    position = services.waiting_position(registration)
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
            # Why a registration waits and what to do, in plain words: the age, or the authorization.
            "waits_for": registration.waits_for if registration is not None else "",
            "withdrawn": state == "pendiente" and registration.waits_for != AGE and registration.consent_withdrawn,
            "pending_text": services.PENDING_NEXT,
            "age_text": services.AGE_NEXT,
            # The waiting list: the place in line as words, how long an offer is held, and how the
            # last time on the list ended when it ended without a place.
            "position_text": services.position_words(position) if position else "",
            "offer_hours": policy.waiting_offer_hours(event.church),
            "list_outcome": services.last_list_outcome(event, request.person) if registration is None else "",
            "faqs": EventFaq.objects.filter(event=event),
            "schedule": services.schedule_by_day(event),
        },
    )


@community_view
@require_POST
def accept_offer(request, slug: str):
    """"Aceptar mi lugar": the person takes the place the waiting list offered. It is a sign-up like
    any other: ``agenda.services.standing`` decides whether it is confirmed or waits."""
    event = _event(request, slug)
    registration = get_object_or_404(EventRegistration, event=event, person=request.person, status__in=(*HOLDING, *LISTED))
    try:
        registration = services.accept_offer(registration, request.user, base_url=_base_url(request))
    except services.AgendaError as error:
        messages.error(request, error.message)
        return redirect("agenda:detail", event.slug)
    if registration.is_pending:
        what_next = services.AGE_NEXT if registration.waits_for == AGE else services.PENDING_NEXT
        messages.info(request, f"Aceptaste tu lugar en {event.name}. {what_next}")
    else:
        messages.success(request, f"Listo. Aceptaste tu lugar: te inscribiste a {event.name}.")
    return redirect("agenda:detail", event.slug)


@community_view
def leave_waiting(request, slug: str):
    """Leave the waiting list, or let the offered place go. Its own page: one question, then yes."""
    event = _event(request, slug)
    registration = get_object_or_404(EventRegistration, event=event, person=request.person, status__in=LISTED)
    offered = services.offer_is_open(registration)
    if request.method == "POST":
        services.leave_waiting(registration, request.user, base_url=_base_url(request))
        if offered:
            messages.success(request, f"Dejaste pasar el lugar en {event.name}. Se lo ofreceremos a la siguiente persona de la lista.")
        else:
            messages.success(request, f"Saliste de la lista de espera de {event.name}.")
        return redirect("agenda:detail", event.slug)
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Quieres dejar pasar tu lugar en {event.name}?" if offered
            else f"¿Quieres salir de la lista de espera de {event.name}?",
            "text": "El lugar se le ofrecerá a la siguiente persona de la lista. Si cambias de opinión, puedes anotarte otra "
            "vez, al final de la lista." if offered
            else "Si cambias de opinión, puedes anotarte otra vez, al final de la lista.",
            "action": "Sí, dejar pasar el lugar" if offered else "Sí, salir de la lista",
            "back_url": reverse("agenda:detail", args=[event.slug]),
        },
    )


@community_view
@require_POST
def register(request, slug: str):
    event, page, held = _seen_event(request, slug)
    wait = throttle.consume(f"event-register:{request.user.pk}", limit=10, window_seconds=3600, block_seconds=600)
    if wait:
        messages.error(request, throttle.wait_message(wait))
        return redirect("agenda:detail", event.slug)
    # The date of birth is asked only when the event needs it, the church does not know it and the
    # person can sign up. It may be left empty; typed, it is a claim the team still has to verify.
    asks = (
        page is not None and page.requires_guardian_consent and request.person.birth_date is None and held is None
        and services.state_of(event, page, None, services.taken(event)) == "abierta"
    )
    if asks:
        form = BirthDateForm(request.POST) if "birth_date" in request.POST else BirthDateForm()
        typed = form.cleaned_data.get("birth_date") if form.is_bound and form.is_valid() else None
        if typed:
            try:
                services.set_birth_date(request.person, typed, request.user)
            except services.AgendaError as error:
                form.add_error("birth_date", error.message)
        if not form.is_bound or form.errors:
            return render(request, "agenda/birth_date.html", {"form": form, "event": event})
    try:
        registration = services.register(event, request.person, request.user, base_url=_base_url(request))
    except services.AgendaError as error:
        messages.error(request, error.message)
        return redirect("agenda:detail", event.slug)
    if registration.is_waiting:
        position = services.position_words(services.waiting_position(registration))
        messages.info(request, f"Te anotamos en la lista de espera de {event.name}. {position}")
    elif registration.is_offered:
        messages.info(request, f"Tienes un lugar ofrecido en {event.name}. Acéptalo aquí para inscribirte.")
    elif registration.is_pending:
        what_next = services.AGE_NEXT if registration.waits_for == AGE else services.PENDING_NEXT
        messages.info(request, f"Recibimos tu inscripción a {event.name}. {what_next}")
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
            services.cancel(registration, request.user, base_url=_base_url(request))
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
            waiting_age=Count("registrations", filter=Q(registrations__status=PENDING, registrations__pending_reason=AGE)),
            waiting=Count("registrations", filter=Q(registrations__status=PENDING) & ~Q(registrations__pending_reason=AGE)),
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
    form = EventPageForm(request.POST or None, instance=page or EventPage(event=event, requires_guardian_consent=policy.minors_consent_default(event.church)),
        event=event,
    )
    if request.method == "POST" and form.is_valid():
        try:
            saved = services.save_page(event, request.user, base_url=_base_url(request), **form.cleaned_data)
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
    """Who signed up or arrived, with how to reach them; first the review queue (whose age the team
    has to confirm, then who waits for a guardian's authorization); and from the event's first day
    on, who came. A registration the team annulled is in no list and no count."""
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
    # Opening the list moves it: an offer past its deadline is released and offered to the next one.
    services.advance(event, base_url=_base_url(request))
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
            "waiting_age": [row for row in held if row.waits_for == AGE],
            "waiting": [row for row in held if row.is_pending and row.waits_for != AGE],
            # The waiting list, in its order: who has an offer to answer, then who waits.
            "listed": services.waiting_rows(event),
            "cancelled": EventRegistration.objects.filter(
                event=event, status=EventRegistration.Status.CANCELLED, annulled_reason="", waiting_outcome=""
            ).count(),
            "started": services.has_started(event),
            "recorded": bool(recorded),
            "present": sum(1 for row in rows if row.attended),
            "people_on": flags.enabled(request.church, "people"),
        },
    )


@church_admin_view
def walkup(request, slug: str):
    """"Registrar a alguien que llegó": a person of this church found by name, or a visitor by
    name. Nobody needs an account to be written down. When a rule of the event's page stands in the
    way (not published, no sign-up, closed, full) the form says which one and asks for an explicit
    override with a reason; the minors' rule has no override."""
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
    requires_consent = bool(page and page.requires_guardian_consent)
    blocks = services.walkup_blocks(event, page, services.taken(event))
    form = WalkUpForm(
        request.POST or None, person=person, minor_known=minor_known, requires_consent=requires_consent, blocks=blocks,
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            registration = services.register_walkup(
                event, request.user, person=person, first_name=data.get("first_name", ""),
                last_name=data.get("last_name", ""), phone=data.get("phone", ""), is_minor=data["is_minor"],
                seen_adult=data["seen_adult"], consent=data["consent"], override=data["overrides"], override_reason=data.get("override_reason", ""),
                base_url=_base_url(request),
            )
        except services.AgendaError as error:
            form.add_error(None, error.message)
        else:
            if registration.waits_for == AGE:
                messages.info(
                    request,
                    f"Registraste a {registration.display_name}. Queda pendiente hasta que confirmes su edad: este "
                    "evento cuida a los menores de edad.",
                )
            elif registration.is_pending:
                messages.info(
                    request,
                    f"Registraste a {registration.display_name}. Queda pendiente hasta que registres la autorización "
                    "de su madre, padre o tutor"
                    + (f". {services.CALL_IS_NOT_ENOUGH}" if registration.recorded_consent else "."),
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
         "found": found, "back": back, "block_texts": [services.WALKUP_RULES[rule] for rule in blocks],
         # A person of the church is confirmed only with an age the team verified.
         "age_unverified": requires_consent and person is not None and minor_known is not True
         and not person.birth_date_verified},
    )


@church_admin_view
def consent(request, slug: str, registration_id: int):
    """"Registrar autorización": who authorized a minor for this event, for what and how the team
    knows. A signed form or an in-person verification confirms; a call or a message is a note."""
    event = _event(request, slug)
    registration = _registration(event, registration_id, status=PENDING)
    if registration.waits_for == AGE:
        raise Http404
    form = ConsentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            registration = services.record_consent(registration, request.user, **form.cleaned_data)
        except services.AgendaError as error:
            form.add_error(None, error.message)
        else:
            if registration.is_active:
                messages.success(request, f"Registraste la autorización. La inscripción de {registration.display_name} quedó confirmada.")
            else:
                messages.info(
                    request,
                    f"Guardamos la nota de contacto. La inscripción de {registration.display_name} sigue pendiente. "
                    f"{services.CALL_IS_NOT_ENOUGH}",
                )
            return redirect(_back_to_list(event))
    return render(request, "agenda/consent_form.html", {"event": event, "registration": registration, "form": form,
                                                        "back": _back_to_list(event)})


@church_admin_view
def confirm_age(request, slug: str, registration_id: int):
    """"Confirmar edad": the team verified the age of somebody whose registration waits for it."""
    event = _event(request, slug)
    registration = _registration(event, registration_id, status=PENDING, pending_reason=AGE, person__isnull=False)
    person = registration.person
    form = ConfirmAgeForm(request.POST if request.method == "POST" else None, initial={"birth_date": person.birth_date})
    if request.method == "POST" and form.is_valid():
        try:
            registration = services.confirm_age(
                registration, request.user, form.cleaned_data["method"], form.cleaned_data["birth_date"]
            )
        except services.AgendaError as error:
            form.add_error("birth_date" if error.code == "birth_date" else None, error.message)
        else:
            if registration.is_active:
                messages.success(request, f"Confirmaste su edad. La inscripción de {registration.display_name} quedó confirmada.")
            else:
                messages.info(
                    request,
                    f"Confirmaste su edad: {registration.display_name} es menor de edad. Su inscripción queda pendiente "
                    "hasta que registres la autorización de su madre, padre o tutor.",
                )
            return redirect(_back_to_list(event))
    return render(request, "agenda/confirm_age.html", {"event": event, "registration": registration, "person": person,
                                                       "form": form, "back": _back_to_list(event)})


@church_admin_view
def guest_age(request, slug: str, registration_id: int):
    """A visitor written down while the event did not ask waits in "Edad por confirmar": the team says what it saw."""
    event = _event(request, slug)
    registration = _registration(event, registration_id, status=PENDING, pending_reason=AGE, person__isnull=True)
    form = GuestAgeForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            registration = services.confirm_guest_age(registration, request.user, is_minor=form.cleaned_data["age"] == "menor")
        except services.AgendaError as error:
            form.add_error(None, error.message)
        else:
            if registration.is_active:
                messages.success(request, f"Listo. El registro de {registration.display_name} quedó confirmado.")
            else:
                messages.info(
                    request,
                    f"{registration.display_name} es menor de edad. Su registro queda pendiente hasta que registres la "
                    "autorización de su madre, padre o tutor.",
                )
            return redirect(_back_to_list(event))
    return render(request, "agenda/guest_age.html", {"event": event, "registration": registration, "form": form,
                                                     "back": _back_to_list(event)})


@church_admin_view
def annul(request, slug: str, registration_id: int):
    """"Anular registro": a row the team wrote at the door by mistake. Its own page: a reason, then yes."""
    event = _event(request, slug)
    registration = _registration(event, registration_id, status__in=HOLDING, registered_by__isnull=False)
    form = AnnulForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            services.annul_walkup(registration, request.user, form.cleaned_data["reason"], base_url=_base_url(request))
        except services.AgendaError as error:
            form.add_error(None, error.message)
        else:
            messages.success(request, f"Anulaste el registro de {registration.display_name}. Su lugar quedó libre.")
            return redirect(_back_to_list(event))
    return render(request, "agenda/annul.html", {"event": event, "registration": registration, "form": form,
                                                 "back": _back_to_list(event)})


@church_admin_view
def waiting_remove(request, slug: str, registration_id: int):
    """"Quitar de la lista de espera": all the administration does to the list. Nothing here moves
    somebody up or signs them up; the reason kept in the audit is a fixed word."""
    event = _event(request, slug)
    registration = _registration(event, registration_id, status__in=LISTED)
    if request.method == "POST":
        services.remove_from_waiting(registration, request.user, base_url=_base_url(request))
        messages.success(request, f"Quitaste a {registration.display_name} de la lista de espera.")
        return redirect(_back_to_list(event))
    offered = registration.is_offered
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Quieres quitar a {registration.display_name} de la lista de espera?",
            "text": "Tiene un lugar ofrecido: al quitarla, ese lugar se le ofrece a la siguiente persona de la lista. "
            "Ella podrá anotarse otra vez, al final de la lista." if offered
            else "Las demás personas de la lista avanzan un lugar. Ella podrá anotarse otra vez, al final de la lista.",
            "action": "Sí, quitar de la lista",
            "back_url": _back_to_list(event),
        },
    )


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
         "back": _back_to_list(event),
         "asks_age": EventPage.objects.filter(event=event, requires_guardian_consent=True).exists()},
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
