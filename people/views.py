"""The church's people, for its administration: who they are, which campero they were, and their account.

Every view: the ``people`` module on for the current church (404 otherwise), then church ``manage``
(``core.authz.has_scope``; 403 otherwise). Every person is fetched inside ``request.church``: an id
of another church is a 404. Camperos are read only through ``events.access.resolve_access``, the
camp's own chokepoint, and only their name, age and sede are shown here: never an answer.
"""

from functools import wraps

from django.contrib import messages
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.views.decorators.http import require_POST

from core.authz import Action, has_scope
from events.access import resolve_access
from events.models import Event, EventParticipant, normalize_name

from . import services
from .decorators import module_required
from .forms import PersonForm
from .models import EventParticipantLink, Person

PAGE_SIZE = 30


def church_admin_required(view_func):
    @module_required("people")
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not has_scope(request.user, request.church, "church", request.church.pk, Action.MANAGE):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped


def _people(church):
    return Person.objects.filter(church=church, merged_into__isnull=True)


def _person(request, person_id: int) -> Person:
    return get_object_or_404(_people(request.church).select_related("user"), pk=person_id)


def _name_filter(queryset, query: str, field: str = "normalized_name"):
    for token in normalize_name(query).split():
        queryset = queryset.filter(**{f"{field}__contains": token})
    return queryset


@church_admin_required
def person_list(request):
    query = request.GET.get("q", "").strip()
    people = _name_filter(_people(request.church), query) if query else _people(request.church)
    page = Paginator(people.select_related("user").order_by("first_name", "last_name", "id"), PAGE_SIZE).get_page(
        request.GET.get("pagina")
    )
    return render(request, "people/person_list.html", {"page": page, "query": query, "total": _people(request.church).count()})


@church_admin_required
def person_create(request):
    form = PersonForm(request.POST or None, church=request.church)
    if request.method == "POST" and form.is_valid():
        person = form.save(commit=False)
        person.church = request.church
        person.created_from = Person.CreatedFrom.MANUAL
        services.save_person(person, request.user, created=True)
        messages.success(request, f"Registraste a {person.full_name}.")
        return redirect("people:detail", person.pk)
    return render(request, "people/person_form.html", {"form": form, "person": None})


@church_admin_required
def person_edit(request, person_id: int):
    person = _person(request, person_id)
    form = PersonForm(request.POST or None, instance=person, church=request.church)
    if request.method == "POST" and form.is_valid():
        services.save_person(form.save(commit=False), request.user, created=False)
        messages.success(request, "Guardamos los cambios.")
        return redirect("people:detail", person.pk)
    return render(request, "people/person_form.html", {"form": form, "person": person})


@church_admin_required
def person_detail(request, person_id: int):
    person = _person(request, person_id)
    links = person.event_links.select_related("event").order_by("-event__starts_on")
    memberships = person.group_memberships.filter(status="activa", group__status="activo").select_related("group")
    return render(
        request,
        "people/person_detail.html",
        {
            "person": person,
            "links": links,
            "memberships": memberships,
            "needs_guardian": person.user_id is None and services.was_minor_at_an_event(person),
            "can_get_link": _can_issue_activation_link(person),
        },
    )


def _can_issue_activation_link(person: Person) -> bool:
    """Only for an account that never set a password and holds nothing else: such an account can be
    opened only with the link. Any other account uses "¿Olvidaste tu contraseña?", which goes to its owner."""
    user = person.user
    if user is None or user.has_usable_password() or user.is_staff or user.is_superuser:
        return False
    return not user.church_roles.exists() and not user.event_memberships.exists()


def _activation_link(request, user) -> str:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    return request.build_absolute_uri(
        reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": default_token_generator.make_token(user)})
    )


def _show_link(request, person: Person):
    return render(
        request,
        "people/access_granted.html",
        {"person": person, "link": _activation_link(request, person.user), "church": request.church},
    )


@church_admin_required
@require_POST
def person_grant_access(request, person_id: int):
    """"Dar acceso": a new account and its one-time link on screen, or (after an explicit yes) the
    account that already uses the person's e-mail. Nothing is e-mailed: the link is shown, and the
    screen says so."""
    person = _person(request, person_id)
    try:
        user, is_new = services.grant_access(
            person,
            request.user,
            link_existing=request.POST.get("vincular_cuenta") == "si",
            minor_confirmed=request.POST.get("tutor_de_acuerdo") == "si",
        )
    except services.ExistingAccount:
        return render(request, "people/access_existing.html", {"person": person,
                                                                 "tutor_de_acuerdo": request.POST.get("tutor_de_acuerdo", "")})
    except services.PeopleError as error:
        messages.error(request, error.message)
        return redirect("people:detail", person.pk)
    person.refresh_from_db()
    if is_new:
        return _show_link(request, person)
    messages.success(request, f"{person.full_name} ya puede entrar con la cuenta que tenía.")
    return redirect("people:detail", person.pk)


@church_admin_required
@require_POST
def person_access_link(request, person_id: int):
    """Another one-time link for an account that has not created its password yet."""
    person = _person(request, person_id)
    if not _can_issue_activation_link(person):
        messages.error(request, "Esta persona ya creó su contraseña. Si la olvidó, puede pedir otra en la pantalla de entrada.")
        return redirect("people:detail", person.pk)
    return _show_link(request, person)


# ---- from an event: campero -> person -----------------------------------------------------------
def _over_events(request):
    events = Event.objects.filter(church=request.church, camp_tools=True).order_by("-starts_on", "name")
    return [event for event in events if services.event_is_over(event)]


def _event_access(request, slug: str):
    event = get_object_or_404(Event, slug=slug, church=request.church)
    access = resolve_access(request.user, event)
    if access is None or not access.can_manage_event:
        raise PermissionDenied
    if not services.event_is_over(event):
        raise Http404
    return event, access


@church_admin_required
def from_event_list(request):
    rows = []
    for event in _over_events(request):
        total = EventParticipant.objects.filter(event=event).count()
        linked = EventParticipantLink.objects.filter(event=event).count()
        rows.append({"event": event, "total": total, "linked": linked, "missing": total - linked})
    return render(request, "people/from_event_list.html", {"rows": rows})


@church_admin_required
def from_event(request, slug: str):
    event, access = _event_access(request, slug)
    query = request.GET.get("q", "").strip()
    camperos = access.participants(include_inactive=True).filter(person_link__isnull=True).select_related("sede")
    if query:
        camperos = _name_filter(camperos, query)
    page = Paginator(camperos.order_by("full_name", "id"), PAGE_SIZE).get_page(request.GET.get("pagina"))
    people = list(_people(request.church).exclude(event_links__event=event).only("id", "first_name", "last_name", "normalized_name"))
    for campero in page:
        tokens = set(campero.normalized_name.split())
        # A hint for the administrator, never a decision: people of this church who share at least two words of the name.
        campero.candidates = [p for p in people if len(tokens & set(p.normalized_name.split())) >= 2][:3]
    linked = EventParticipantLink.objects.filter(event=event).count()
    return render(request, "people/from_event.html", {"event": event, "page": page, "query": query, "linked": linked})


@church_admin_required
@require_POST
def from_event_link(request, slug: str, public_id):
    """One campero, one decision: "Es una persona nueva" or "Es <persona>"."""
    event, access = _event_access(request, slug)
    campero = get_object_or_404(access.participants(include_inactive=True), public_id=public_id)
    back = redirect(f"{reverse('people:from_event', args=[event.slug])}?{request.POST.get('volver', '')}".rstrip("?"))
    try:
        choice = request.POST.get("persona", "")
        if choice == "nueva":
            person = services.create_person_from_participant(campero, request.user)
        else:
            person = get_object_or_404(_people(request.church), pk=int(choice) if choice.isdigit() else 0)
            services.link_participant(person, campero, request.user)
    except services.PeopleError as error:
        messages.error(request, error.message)
        return back
    messages.success(request, f"{person.full_name} quedó en las personas de la iglesia, con su paso por {event.name}.")
    return back


@church_admin_required
@require_POST
def person_unlink(request, person_id: int, link_id: int):
    person = _person(request, person_id)
    link = get_object_or_404(EventParticipantLink.objects.select_related("event", "person"), pk=link_id, person=person)
    services.unlink_participant(link, request.user)
    messages.success(request, f"Quitamos el vínculo con {link.event.name}. Los datos del evento no cambiaron.")
    return redirect("people:detail", person.pk)
