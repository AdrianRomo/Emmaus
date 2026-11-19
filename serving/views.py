"""Servir: a person's dates and teams, the work of who coordinates a team, and the administration's.

Every view goes through ``groups.views.community_view`` (the ``community`` module on, the person
the account is in this church). Teams, dates and assignments are always fetched inside
``request.church``, so an id of another church is a 404, POSTs included.

What needs more than being of the church is decided by ``core.authz.has_scope`` (decisions 0003
and 0016): ``manage`` on the ``ministry`` scope of one team opens that team's dates and people to
who coordinates it; church ``manage`` (the administration) opens every team of its church and is
the only one that adds people or names who coordinates. Nothing is implied into the team scope:
the views say ``is_church_admin or has_scope(ministry manage)`` explicitly, as the groups do.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import OperationalError, transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import formats, timezone
from django.views.decorators.http import require_POST

from core.nav import safe_next
from core.paging import first_rows
from core.authz import Action, has_scope
from events.models import normalize_name
from groups.views import church_admin_view, community_view
from people.models import Person

from . import reminders, rotas, services
from .access import managed_team_ids
from .forms import ServiceDateForm, TeamForm, UnavailableForm
from .models import Assignment, ServiceDate, Team, TeamMembership, Unavailable

ACTIVE = TeamMembership.Status.ACTIVE
COORDINATOR = TeamMembership.Role.COORDINATOR
MEMBER_ORDER = ("person__first_name", "person__last_name", "id")


BUSY = "Alguien más estaba cambiando lo mismo en este momento. No se guardó nada: inténtalo de nuevo."


def try_again(view_func):
    """A view that writes: when the database gives up on a lock (two people changing the same date
    at the same instant), nothing was written and the person is told so, plainly, on "Servir"."""
    from functools import wraps

    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        try:
            return view_func(request, *args, **kwargs)
        except OperationalError:
            if request.method != "POST":
                raise
            messages.error(request, BUSY)
            return redirect("serving:home")

    return wrapped


def _weekday_date(day) -> str:
    return formats.date_format(day, r"l j \d\e F").lower()


def _team(request, team_id: int, *, include_archived: bool = False) -> Team:
    team = get_object_or_404(Team, pk=team_id, church=request.church)
    if not team.is_active and not include_archived:
        raise Http404
    return team


def _may_manage(request, team: Team) -> bool:
    return request.is_church_admin or has_scope(request.user, request.church, "ministry", team.pk, Action.MANAGE)


def _coordinated_team(request, team_id: int) -> Team:
    """The team, for who coordinates it or the administration; anybody else of the church gets a 403."""
    team = _team(request, team_id, include_archived=request.is_church_admin)
    if not _may_manage(request, team):
        raise PermissionDenied
    return team


def _coordinated_date(request, date_id: int) -> ServiceDate:
    """A date that can still change, for who coordinates its team or the administration."""
    service_date = get_object_or_404(ServiceDate.objects.select_related("team"), pk=date_id, team__church=request.church)
    team = service_date.team
    if not request.is_church_admin:
        if not team.is_active:
            raise Http404
        if not _may_manage(request, team):
            raise PermissionDenied
    if not team.is_active or service_date.date < timezone.localdate():
        raise Http404
    return service_date


# ---- the person ---------------------------------------------------------------------------------
@community_view
def home(request):
    person, today = request.person, timezone.localdate()
    changing = request.GET.get("cambiar", "")
    dates = services.all_my_dates(person, today)
    now = timezone.now()
    waiting = rotas.open_swap_ids([assignment.pk for assignment in dates], now)
    with_plan = set(
        ServiceDate.objects.filter(pk__in=[assignment.service_date_id for assignment in dates], plan_items__isnull=False)
        .values_list("pk", flat=True)
    )
    for assignment in dates:
        assignment.can_change = assignment.service_date.date > today
        assignment.asks = assignment.status == Assignment.Status.PENDING or (
            assignment.can_change and changing == str(assignment.pk)
        )
        assignment.person = person
        assignment.swap = waiting.get(assignment.pk)
        assignment.can_swap = assignment.swap is None and rotas.may_ask_swap(assignment, now)
        assignment.has_plan = assignment.status != Assignment.Status.DECLINED and assignment.service_date_id in with_plan
    memberships = list(
        TeamMembership.objects.filter(person=person, status=ACTIVE, team__status=Team.Status.ACTIVE)
        .select_related("team").order_by("team__name", "id")
    )
    names = services.coordinator_names([membership.team_id for membership in memberships])
    managed = set() if request.is_church_admin else set(managed_team_ids(request.user, request.church))
    for membership in memberships:
        membership.coordinator_names = names.get(membership.team_id, [])
        membership.can_open = request.is_church_admin or membership.team_id in managed
    return render(
        request,
        "serving/home.html",
        {
            "dates": dates,
            "memberships": memberships,
            "days": Unavailable.objects.filter(person=person, date__gte=today),
            "today": today,
            "swaps_to_answer": rotas.swaps_to_answer(person, now),
            "my_swaps": rotas.my_swaps(person, now),
            "reminders_offered": reminders.team_sends(person),
            "reminders_on": reminders.wants_reminders(person),
        },
    )


@community_view
@require_POST
@try_again
def respond(request, assignment_id: int):
    assignment = get_object_or_404(
        Assignment.objects.select_related("service_date", "service_date__team"),
        pk=assignment_id, person=request.person, service_date__team__church=request.church,
        service_date__team__status=Team.Status.ACTIVE,
    )
    answer = request.POST.get("respuesta")
    if answer not in ("aceptar", "declinar"):
        raise Http404
    # Back to where the answer was given ("Mi iglesia" or Servir), so the page shows the answer in place
    back = safe_next(request, request.POST.get("siguiente"), reverse("serving:home"))
    try:
        services.respond(assignment, answer == "aceptar", request.user)
    except services.ServingError as error:
        messages.error(request, error.message)
        return redirect(back)
    day = _weekday_date(assignment.service_date.date)
    # Says exactly what was saved; the team's page shows the answer to who coordinates (nothing is sent)
    at = assignment.service_date.starts_at
    if answer == "aceptar":
        messages.success(request, f"Confirmaste el {day}{at and ', a las ' + at.strftime('%H:%M') or ''}.")
    else:
        messages.success(request, f"Dijiste que no puedes el {day}.")
    return redirect(back)


@community_view
@require_POST
@try_again
def unavailable_add(request):
    form = UnavailableForm(request.POST)
    if not form.is_valid():
        messages.error(request, form.errors["dia"][0])
        return redirect(reverse("serving:home") + "#dias")
    day = form.cleaned_data["dia"]
    try:
        services.set_unavailable(request.person, day, request.user)
    except services.ServingError as error:
        messages.error(request, error.message)
        return redirect(reverse("serving:home") + "#dias")
    if services.assigned_on(request.person, day):
        messages.warning(
            request,
            f"Marcamos el {_weekday_date(day)}. Ese día ya tienes una fecha para servir: sigue en pie. "
            "Si no vas a poder, dilo también en esa fecha con «No puedo».",
        )
    else:
        messages.success(request, f"Marcamos el {_weekday_date(day)}: ese día no te pedirán servir.")
    return redirect(reverse("serving:home") + "#dias")


@community_view
@require_POST
@try_again
def unavailable_remove(request, day_id: int):
    day = get_object_or_404(Unavailable, pk=day_id, person=request.person)
    services.clear_unavailable(day, request.user)
    messages.success(request, f"Quitamos el {_weekday_date(day.date)}.")
    return redirect(reverse("serving:home") + "#dias")


# ---- who coordinates a team ---------------------------------------------------------------------
@community_view
def team(request, team_id: int):
    team = _coordinated_team(request, team_id)
    today = timezone.localdate()
    dates = list(ServiceDate.objects.filter(team=team, date__gte=today).prefetch_related("assignments__person", "assignments__role"))
    missing = rotas.missing_by_date(dates)
    for service_date in dates:
        service_date.people = sorted(service_date.assignments.all(), key=lambda row: (row.person.full_name, row.pk))
        for row in service_date.people:
            row.service_date = service_date
            # Read now, not when it was assigned: the role may have become for adults, or the age corrected.
            row.unfit = row.status != Assignment.Status.DECLINED and rotas.unfit(row)
        service_date.confirmed = sum(1 for row in service_date.people if row.status == Assignment.Status.ACCEPTED)
        service_date.role_needs = missing.get(service_date.pk, [])
        waiting = sum(1 for row in service_date.people if row.status == Assignment.Status.PENDING)
        # Nobody serves that day: somebody was asked and every one asked said no (none confirmed, none still to
        # answer: a pending answer may still be a yes). A date nobody was asked for says so on its own row.
        service_date.nobody = bool(service_date.people) and not service_date.confirmed and not waiting
    members = list(TeamMembership.objects.filter(team=team, status=ACTIVE).select_related("person").order_by("role", *MEMBER_ORDER))
    query, found, found_total = request.GET.get("q", "").strip(), [], 0
    if request.is_church_admin and query:
        # Only the administration searches the church's people; who coordinates sees only who is in the team.
        found = Person.objects.filter(church=request.church, merged_into__isnull=True)
        for token in normalize_name(query).split():
            found = found.filter(normalized_name__contains=token)
        # A short list under a search box, which says when it stops (core/paging.py::first_rows)
        found, found_total = first_rows(found.exclude(pk__in=[membership.person_id for membership in members]), 10)
    return render(
        request,
        "serving/team.html",
        {"team": team, "dates": dates, "members": members, "member_count": len(members), "query": query, "found": found,
         "found_total": found_total, "today": today, "swaps": rotas.team_swaps(team) if team.is_active else [],
         "uncovered": [service_date for service_date in dates if service_date.nobody] if team.is_active else []},
    )


def _date_page(request, team: Team, service_date: ServiceDate | None):
    """Create or edit one date, say how many people of each role it needs and choose who serves,
    saved together or not at all."""
    form = ServiceDateForm(request.POST or None, instance=service_date)
    members = list(
        TeamMembership.objects.filter(team=team, status=ACTIVE, person__merged_into__isnull=True)
        .select_related("person").order_by(*MEMBER_ORDER)
    )
    assigned = {row.person_id: row for row in service_date.assignments.select_related("role")} if service_date else {}
    chosen = {int(value) for value in request.POST.getlist("sirve") if value.isdigit()}
    roles = rotas.active_roles(team)
    by_id = {role.pk: role for role in roles}
    posted = request.method == "POST"

    def number(name: str) -> int:
        value = request.POST.get(name, "").strip()
        return int(value) if value.isdigit() and len(value) <= 4 else 0

    warned: list = []
    if posted and form.is_valid():
        # Who serves that day on another date can still be asked: who coordinates may know better.
        # It is said first, with the names, and saving again with the same names goes ahead.
        day = form.cleaned_data["date"]
        moved = service_date is not None and service_date.date != day
        asking = {pk for pk in chosen if moved or pk not in assigned} & {membership.person_id for membership in members}
        elsewhere = rotas.busy_elsewhere(asking, day, service_date.pk if service_date else None)
        stated = ",".join(str(pk) for pk in sorted(elsewhere))
        if elsewhere and request.POST.get("ocupados", "") != stated:
            warned = [membership.person for membership in members if membership.person_id in elsewhere]
    if posted and form.is_valid() and not warned:
        try:
            with transaction.atomic():
                saved = services.save_date(team, request.user, service_date=service_date, **form.cleaned_data)
                if roles:
                    rotas.set_needs(saved, request.user, {role.pk: number(f"necesita_{role.pk}") for role in roles})
                for membership in members:
                    if membership.person_id in chosen:
                        # A team without roles keeps assigning as before; with roles, the one chosen (or none).
                        wanted = number(f"rol_{membership.person_id}")
                        current = assigned.get(membership.person_id)
                        if not roles or (current is not None and current.role_id == wanted):
                            role = services.KEEP
                        elif wanted and wanted not in by_id:
                            raise services.ServingError("Elige un rol de este equipo.", code="no_role")
                        else:
                            role = by_id.get(wanted)
                        services.assign(saved, membership.person, request.user, role=role)
                    elif membership.person_id in assigned:
                        services.unassign(saved, membership.person, request.user)
        except services.ServingError as error:
            form.add_error(None, error.message)
        else:
            messages.success(request, f"Guardamos la fecha del {_weekday_date(saved.date)}.")
            return redirect("serving:team", team.pk)
    busy = services.unavailable_ids([membership.person_id for membership in members], service_date.date) if service_date else set()
    for membership in members:
        membership.assignment = assigned.get(membership.person_id)
        membership.busy = membership.person_id in busy
        # Who said "ese día no puedo" cannot be chosen; who was asked before saying it stays until taken out.
        membership.blocked = membership.busy and membership.assignment is None
        membership.checked = membership.person_id in chosen if posted else membership.assignment is not None
        current = membership.assignment.role if membership.assignment else None
        membership.role_id = number(f"rol_{membership.person_id}") if posted else (current.pk if current else 0)
        # A role that was retired stays on who has it, and is offered only to them.
        membership.old_role = current if current is not None and not current.active else None
        membership.unfit = False
        if membership.assignment is not None and membership.assignment.status != Assignment.Status.DECLINED:
            membership.assignment.service_date, membership.assignment.person = service_date, membership.person
            membership.unfit = rotas.unfit(membership.assignment)
    needs = {need.role.pk: need for need in rotas.missing_by_date([service_date])[service_date.pk]} if service_date else {}
    for role in roles:
        need = needs.get(role.pk)
        role.needed = number(f"necesita_{role.pk}") if posted else (need.needed if need else 0)
        role.state = need
    return render(request, "serving/date_form.html", {
        "form": form, "team": team, "service_date": service_date, "members": members, "roles": roles,
        "warned": warned, "warned_ids": ",".join(str(person.pk) for person in sorted(warned, key=lambda person: person.pk)),
    })


@community_view
@try_again
def date_create(request, team_id: int):
    team = _coordinated_team(request, team_id)
    if not team.is_active:
        raise Http404
    return _date_page(request, team, None)


@community_view
@try_again
def date_detail(request, date_id: int):
    service_date = _coordinated_date(request, date_id)
    return _date_page(request, service_date.team, service_date)


@community_view
@try_again
def date_delete(request, date_id: int):
    service_date = _coordinated_date(request, date_id)
    team, day = service_date.team, _weekday_date(service_date.date)
    if request.method == "POST":
        try:
            services.delete_date(service_date, request.user)
        except services.ServingError as error:
            messages.error(request, error.message)
        else:
            messages.success(request, f"Borraste la fecha del {day}.")
        return redirect("serving:team", team.pk)
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Borrar la fecha del {day}?",
            "text": f"{service_date.what}. Quienes iban a servir dejarán de verla en «Tus fechas». Vale la pena avisarles.",
            "action": "Sí, borrar la fecha",
            "back_url": reverse("serving:date_detail", args=[service_date.pk]),
        },
    )


# ---- the administration -------------------------------------------------------------------------
@church_admin_view
def team_admin(request):
    today = timezone.localdate()
    teams = list(
        Team.objects.filter(church=request.church).annotate(
            member_count=Count("memberships", filter=Q(memberships__status=ACTIVE), distinct=True),
            date_count=Count("dates", filter=Q(dates__date__gte=today), distinct=True),
        ).order_by("status", "name", "id")
    )
    names = services.coordinator_names([team.pk for team in teams])
    for team in teams:
        team.coordinator_names = names.get(team.pk, [])
    return render(request, "serving/team_admin.html", {"teams": teams})


@church_admin_view
@try_again
def team_create(request):
    form = TeamForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        team = services.save_team(request.church, request.user, **form.cleaned_data)
        messages.success(request, f"Creaste el equipo {team.name}.")
        return redirect("serving:team", team.pk)
    return render(request, "serving/team_form.html", {"form": form, "team": None})


@church_admin_view
@try_again
def team_edit(request, team_id: int):
    team = _team(request, team_id)
    form = TeamForm(request.POST or None, instance=team)
    if request.method == "POST" and form.is_valid():
        services.save_team(request.church, request.user, team=team, **form.cleaned_data)
        messages.success(request, "Guardado.")
        return redirect("serving:team", team.pk)
    return render(request, "serving/team_form.html", {"form": form, "team": team})


@church_admin_view
@try_again
def team_archive(request, team_id: int):
    team = _team(request, team_id)
    if request.method == "POST":
        services.archive_team(team, request.user)
        messages.success(request, f"Archivaste el equipo {team.name}.")
        return redirect("serving:team_admin")
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Archivar el equipo {team.name}?",
            "text": "Sus fechas dejarán de aparecer en «Tus fechas» de quienes sirven y quien lo coordina dejará de administrarlo. No se borra nada.",
            "action": "Sí, archivar el equipo",
            "back_url": reverse("serving:team_edit", args=[team.pk]),
        },
    )


@church_admin_view
@require_POST
@try_again
def member_add(request, team_id: int):
    """The administration puts a person of the church in a team, to serve or to coordinate it."""
    team = _team(request, team_id)
    chosen = request.POST.get("persona", "")
    person = get_object_or_404(Person, pk=int(chosen) if chosen.isdigit() else 0, church=request.church, merged_into__isnull=True)
    role = COORDINATOR if request.POST.get("rol") == "coordina" else TeamMembership.Role.SERVES
    try:
        services.set_member(team, person, request.user, role=role)
    except services.ServingError as error:
        messages.error(request, error.message)
        return redirect("serving:team", team.pk)
    if role == COORDINATOR:
        note = "" if person.user_id else " Todavía no tiene acceso a Emmaus: dáselo desde Personas para que pueda organizar las fechas."
        messages.success(request, f"{person.full_name} ahora coordina el equipo {team.name}.{note}")
    else:
        messages.success(request, f"{person.full_name} sirve en el equipo {team.name}.")
    return redirect(reverse("serving:team", args=[team.pk]) + "#integrantes")


@church_admin_view
@try_again
def member_remove(request, team_id: int, membership_id: int):
    team = _team(request, team_id)
    membership = get_object_or_404(TeamMembership.objects.select_related("person"), pk=membership_id, team=team, status=ACTIVE)
    person = membership.person
    if request.method == "POST":
        services.remove_member(team, person, request.user)
        messages.success(request, f"{person.full_name} ya no es parte del equipo {team.name}.")
        return redirect(reverse("serving:team", args=[team.pk]) + "#integrantes")
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Quitar a {person.full_name} del equipo {team.name}?",
            "text": "Sus fechas de hoy en adelante con este equipo se quitan. Puedes agregarle otra vez cuando quieras.",
            "action": "Sí, quitar del equipo",
            "back_url": reverse("serving:team", args=[team.pk]),
        },
    )
