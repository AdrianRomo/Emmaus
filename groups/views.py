"""Mi iglesia: the home of a person of the church, the groups, and the tools of who guides one.

Every view: the ``community`` module on for the current church (404 otherwise), then the person
the account is in that church (``request.person``; 404 without one). Groups are always fetched
inside ``request.church``, so an id of another church is a 404. What needs more than being of
the church asks ``core.authz.has_scope``: group ``manage`` for its guides, church ``manage`` for
the administration. No view here reads an interview, an answer or a camp follow-up.
"""

from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core import notify
from core.authz import Action, has_scope
from events.models import Event, normalize_name
from events.services import throttle
from people import flags
from people.decorators import module_required
from people.models import Person
from people.services import ensure_own_person

from . import services
from .access import church_groups, managed_group_ids
from .forms import GroupForm
from .models import Group, GroupJoinRequest, GroupMembership

ACTIVE = GroupMembership.Status.ACTIVE
PENDING = GroupJoinRequest.Status.PENDING


def community_view(view_func):
    @module_required("community")
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        request.person = ensure_own_person(request.user, request.church)
        if request.person is None:
            raise Http404
        request.is_church_admin = has_scope(request.user, request.church, "church", request.church.pk, Action.MANAGE)
        return view_func(request, *args, **kwargs)

    return wrapped


def church_admin_view(view_func):
    @community_view
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.is_church_admin:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped


def _leaders(group_ids) -> dict[int, list[str]]:
    """Who guides each group, by name: what somebody looking for a group reads."""
    names: dict[int, list[str]] = {}
    rows = GroupMembership.objects.filter(group_id__in=group_ids, status=ACTIVE, role=GroupMembership.Role.LEADER)
    for membership in rows.select_related("person").order_by("person__first_name", "id"):
        names.setdefault(membership.group_id, []).append(membership.person.full_name)
    return names


def _with_leaders(groups) -> list[Group]:
    groups = list(groups)
    leaders = _leaders([group.pk for group in groups])
    for group in groups:
        group.leader_names = leaders.get(group.pk, [])
    return groups


# ---- home ---------------------------------------------------------------------------------------
@community_view
def home(request):
    church, person, today = request.church, request.person, timezone.localdate()
    memberships = list(
        GroupMembership.objects.filter(person=person, status=ACTIVE, group__status=Group.Status.ACTIVE).select_related("group")
    )
    for membership in memberships:
        membership.next_meeting = membership.group.next_meeting(today)
    my_requests = list(GroupJoinRequest.objects.filter(person=person, status=PENDING).select_related("group"))
    month_ago = timezone.now() - timezone.timedelta(days=30)
    declined = list(
        GroupJoinRequest.objects.filter(person=person, status=GroupJoinRequest.Status.DECLINED, decided_at__gte=month_ago)
        .select_related("group")
    )
    managed = managed_group_ids(request.user, church)
    to_decide = (
        GroupJoinRequest.objects.filter(group_id__in=managed, status=PENDING, group__status=Group.Status.ACTIVE)
        .values("group_id", "group__name").annotate(total=Count("id")).order_by("group__name")
    )
    to_decide = list(to_decide)
    upcoming = Event.objects.filter(church=church, status=Event.Status.ACTIVE, ends_on__gte=today).order_by("starts_on", "name")[:5]
    history = person.event_links.select_related("event").order_by("-event__starts_on")
    open_groups = church_groups(church).filter(accepts_requests=True).exists()
    any_group = Group.objects.filter(church=church).exists()

    sign = None
    if to_decide:
        total = sum(row["total"] for row in to_decide)
        first = to_decide[0]
        sign = {
            "title": "¿Recibes a 1 persona en tu grupo?" if total == 1 else f"¿Recibes a {total} personas en tu grupo?",
            "text": "Pidieron unirse y esperan tu respuesta.",
            "url": reverse("community:group_people", args=[first["group_id"]]),
            "action": "Ver solicitudes",
        }
    elif request.is_church_admin and not any_group:
        sign = {
            "title": "¿Creamos el primer grupo?",
            "text": "Con un grupo creado, las personas de la iglesia ya pueden pedir unirse.",
            "url": reverse("community:group_create"),
            "action": "Crear un grupo",
        }
    elif not memberships and not my_requests and open_groups:
        sign = {
            "title": "¿Buscas un grupo?",
            "text": "Hay grupos de la iglesia que reciben a personas nuevas.",
            "url": reverse("community:groups"),
            "action": "Ver los grupos",
        }
    return render(
        request,
        "groups/home.html",
        {
            "church": church,
            "person": person,
            "sign": sign,
            "memberships": memberships,
            "my_requests": my_requests,
            "declined": declined,
            "upcoming": upcoming,
            "history": history,
            "open_groups": open_groups,
            "people_enabled": request.is_church_admin and flags.enabled(church, "people"),
        },
    )


# ---- discovery ----------------------------------------------------------------------------------
@community_view
def groups(request):
    person = request.person
    mine = set(GroupMembership.objects.filter(person=person, status=ACTIVE).values_list("group_id", flat=True))
    asked = set(GroupJoinRequest.objects.filter(person=person, status=PENDING).values_list("group_id", flat=True))
    rows = _with_leaders(church_groups(request.church).select_related("sede").order_by("name", "id"))
    for group in rows:
        group.state = "mine" if group.pk in mine else "asked" if group.pk in asked else "open" if group.is_open else "closed"
    return render(request, "groups/groups.html", {"groups": rows})


def _group(request, group_id: int, *, include_archived: bool = False) -> Group:
    queryset = Group.objects.filter(church=request.church).select_related("sede")
    if not include_archived:
        queryset = queryset.filter(status=Group.Status.ACTIVE)
    return get_object_or_404(queryset, pk=group_id)


def _can_manage(request, group: Group) -> bool:
    """Who guides the group, or the church's administration (which creates groups and names their guides)."""
    return request.is_church_admin or has_scope(request.user, request.church, "group", group.pk, Action.MANAGE)


@community_view
def group_detail(request, group_id: int):
    group = _group(request, group_id, include_archived=request.is_church_admin)
    person = request.person
    membership = GroupMembership.objects.filter(group=group, person=person, status=ACTIVE).first()
    pending = GroupJoinRequest.objects.filter(group=group, person=person, status=PENDING).first()
    return render(
        request,
        "groups/group_detail.html",
        {
            "group": _with_leaders([group])[0],
            "membership": membership,
            "pending": pending,
            "next_meeting": group.next_meeting() if membership else None,
            "can_manage": _can_manage(request, group),
            "members": GroupMembership.objects.filter(group=group, status=ACTIVE).count(),
        },
    )


def _absolute(request, name: str, *args) -> str:
    return request.build_absolute_uri(reverse(name, args=args))


@community_view
@require_POST
def group_request(request, group_id: int):
    group = _group(request, group_id)
    wait = throttle.consume(f"group-join:{request.user.pk}", limit=10, window_seconds=3600, block_seconds=600)
    if wait:
        messages.error(request, throttle.wait_message(wait))
        return redirect("community:group_detail", group.pk)
    try:
        join_request, created = services.request_to_join(group, request.person, request.user)
    except services.GroupError as error:
        messages.error(request, error.message)
        return redirect("community:group_detail", group.pk)
    if created:
        emails = list(
            GroupMembership.objects.filter(group=group, status=ACTIVE, role=GroupMembership.Role.LEADER, person__user__isnull=False)
            .exclude(person__user__email="").values_list("person__user__email", flat=True)
        )
        outcome = notify.NOT_CONFIGURED
        if emails:
            outcome = notify.notify(
                "solicitud_grupo_recibida", emails,
                {"church_name": group.church.name, "link": _absolute(request, "community:group_people", group.pk)},
            )
        # The message never claims an e-mail that did not go out (core.notify).
        if outcome == notify.SENT:
            messages.success(request, f"Listo. Pediste unirte a {group.name} y le avisamos por correo a quien lo guía.")
        else:
            messages.success(request, f"Listo. Pediste unirte a {group.name}. Quien lo guía verá tu solicitud cuando entre a Emmaus.")
    return redirect("community:group_detail", group.pk)


@community_view
@require_POST
def group_withdraw(request, group_id: int):
    group = _group(request, group_id, include_archived=True)
    pending = GroupJoinRequest.objects.filter(group=group, person=request.person, status=PENDING).first()
    if pending:
        services.withdraw_request(pending, request.user)
        messages.success(request, f"Retiraste tu solicitud a {group.name}.")
    return redirect("community:group_detail", group.pk) if group.is_active else redirect("community:home")


@community_view
def group_leave(request, group_id: int):
    group = _group(request, group_id)
    membership = get_object_or_404(GroupMembership, group=group, person=request.person, status=ACTIVE)
    if request.method == "POST":
        services.end_membership(membership, request.user)
        messages.success(request, f"Saliste de {group.name}.")
        return redirect("community:home")
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Quieres salir de {group.name}?",
            "text": "Dejarás de ver dónde se reúne. Si cambias de opinión, puedes pedir unirte otra vez.",
            "action": "Sí, salir del grupo",
            "back_url": reverse("community:group_detail", args=[group.pk]),
        },
    )


# ---- who guides a group -------------------------------------------------------------------------
def _managed_group(request, group_id: int) -> Group:
    group = _group(request, group_id, include_archived=request.is_church_admin)
    if not _can_manage(request, group):
        raise PermissionDenied
    return group


@community_view
def group_people(request, group_id: int):
    group = _managed_group(request, group_id)
    requests = GroupJoinRequest.objects.filter(group=group, status=PENDING).select_related("person")
    members = GroupMembership.objects.filter(group=group, status=ACTIVE).select_related("person", "person__user")
    query, found = request.GET.get("q", "").strip(), []
    if request.is_church_admin and query:
        # Only the administration searches the church's people; a guide sees only who asked and who is in.
        found = Person.objects.filter(church=request.church, merged_into__isnull=True)
        for token in normalize_name(query).split():
            found = found.filter(normalized_name__contains=token)
        found = found.exclude(group_memberships__in=members)[:10]
    return render(
        request,
        "groups/group_people.html",
        {"group": group, "requests": requests, "members": members, "query": query, "found": found,
         "me": request.person},
    )


@community_view
@require_POST
def request_decide(request, group_id: int, request_id: int):
    group = _managed_group(request, group_id)
    join_request = get_object_or_404(GroupJoinRequest.objects.select_related("person", "person__user"), pk=request_id, group=group)
    accept = request.POST.get("decision") == "aceptar"
    try:
        services.decide_request(join_request, request.user, accept=accept)
    except services.GroupError as error:
        messages.error(request, error.message)
        return redirect("community:group_people", group.pk)
    person = join_request.person
    outcome = notify.NOT_CONFIGURED
    if person.user_id and person.user.email:
        outcome = notify.notify(
            "solicitud_grupo_decidida", person.user.email,
            {"church_name": group.church.name, "link": _absolute(request, "community:home")},
        )
    told = " Le avisamos por correo." if outcome == notify.SENT else " Lo verá cuando entre a Emmaus."
    if accept:
        messages.success(request, f"{person.full_name} ya es parte de {group.name}.{told}")
    else:
        messages.success(request, f"No aceptaste la solicitud de {person.full_name}.{told}")
    return redirect("community:group_people", group.pk)


@community_view
def member_remove(request, group_id: int, membership_id: int):
    group = _managed_group(request, group_id)
    membership = get_object_or_404(GroupMembership.objects.select_related("person"), pk=membership_id, group=group, status=ACTIVE)
    if membership.is_leader and not request.is_church_admin:
        raise PermissionDenied  # who guides a group is the administration's decision
    if request.method == "POST":
        services.end_membership(membership, request.user)
        messages.success(request, f"{membership.person.full_name} ya no es parte de {group.name}.")
        return redirect("community:group_people", group.pk)
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Quitar a {membership.person.full_name} de {group.name}?",
            "text": "Dejará de ver dónde se reúne el grupo. Podrá pedir unirse otra vez.",
            "action": "Sí, quitar del grupo",
            "back_url": reverse("community:group_people", args=[group.pk]),
        },
    )


# ---- the administration -------------------------------------------------------------------------
@church_admin_view
def group_admin_list(request):
    groups = Group.objects.filter(church=request.church).annotate(
        member_count=Count("memberships", filter=Q(memberships__status=ACTIVE), distinct=True),
        pending_count=Count("join_requests", filter=Q(join_requests__status=PENDING), distinct=True),
    ).order_by("status", "name", "id")
    return render(request, "groups/admin_list.html", {"groups": _with_leaders(groups)})


@church_admin_view
def group_create(request):
    form = GroupForm(request.POST or None, church=request.church)
    if request.method == "POST" and form.is_valid():
        group = form.save(commit=False)
        group.church = request.church
        services.save_group(group, request.user, created=True)
        messages.success(request, f"Creaste el grupo {group.name}. Ahora elige quién lo guía.")
        return redirect("community:group_people", group.pk)
    return render(request, "groups/group_form.html", {"form": form, "group": None})


@church_admin_view
def group_edit(request, group_id: int):
    group = _group(request, group_id)
    form = GroupForm(request.POST or None, instance=group, church=request.church)
    if request.method == "POST" and form.is_valid():
        services.save_group(form.save(commit=False), request.user, created=False)
        messages.success(request, "Guardamos los cambios.")
        return redirect("community:group_detail", group.pk)
    return render(request, "groups/group_form.html", {"form": form, "group": group})


@church_admin_view
def group_archive(request, group_id: int):
    group = _group(request, group_id)
    if request.method == "POST":
        services.archive_group(group, request.user)
        messages.success(request, f"Archivaste el grupo {group.name}.")
        return redirect("community:group_admin_list")
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Archivar el grupo {group.name}?",
            "text": "Saldrá de la lista de grupos, sus solicitudes se cerrarán y quienes lo guían dejarán de administrarlo. No se borra nada.",
            "action": "Sí, archivar el grupo",
            "back_url": reverse("community:group_edit", args=[group.pk]),
        },
    )


@church_admin_view
@require_POST
def member_add(request, group_id: int):
    """The administration puts a person of the church in a group, as member or as its guide."""
    group = _group(request, group_id)
    person = get_object_or_404(Person, pk=request.POST.get("persona") or 0, church=request.church, merged_into__isnull=True)
    role = GroupMembership.Role.LEADER if request.POST.get("rol") == "lider" else GroupMembership.Role.MEMBER
    try:
        services.set_membership(group, person, request.user, role=role)
    except services.GroupError as error:
        messages.error(request, error.message)
        return redirect("community:group_people", group.pk)
    if role == GroupMembership.Role.LEADER:
        note = "" if person.user_id else " Todavía no tiene acceso a Emmaus: dáselo desde Personas para que pueda responder solicitudes."
        messages.success(request, f"{person.full_name} ahora guía {group.name}.{note}")
    else:
        messages.success(request, f"{person.full_name} ya es parte de {group.name}.")
    return redirect("community:group_people", group.pk)
