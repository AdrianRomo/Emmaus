"""Servir, second part: roles, the rotation, swaps, reminders and the order of a date.

The same rules as ``serving.views``: every view goes through ``community_view`` (the module on, the
person of the account in ``request.church``), everything is fetched inside ``request.church`` (an
id of another church is a 404), and what needs more than being of the church is
``is_church_admin or has_scope(ministry, manage)`` through ``views._coordinated_team``.

A swap is between two people of a team: who asked reaches their own requests, who was asked
reaches what they were asked, and only who coordinates the team (or the administration, which
manages every team) sees the team's. Nobody else gets a row.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from groups.views import community_view
from people.models import Person

from . import reminders, rotas, services
from .models import Assignment, PlanItem, PlanTemplate, ServiceDate, SwapRequest, Team, TeamRole
from .views import _coordinated_team, _may_manage, _weekday_date, try_again


def _number(value, default=None):
    value = (value or "").strip()
    return int(value) if value.isdigit() and len(value) <= 4 else default


def _working_team(request, team_id: int) -> Team:
    """A team that still serves, for who coordinates it or the administration."""
    team = _coordinated_team(request, team_id)
    if not team.is_active:
        raise Http404
    return team


def _date_words(service_date: ServiceDate) -> str:
    """"Equipo X, lunes 23 de noviembre, a las 10:30, en Y." for the head of a page about one date."""
    words = f"Equipo {service_date.team.name}, {_weekday_date(service_date.date)}"
    if service_date.starts_at:
        words += f", a las {service_date.starts_at:%-H:%M}"
    if service_date.place:
        words += f", en {service_date.place}"
    return words + "."


def _organize_url(team: Team, anchor: str) -> str:
    return reverse("serving:organize", args=[team.pk]) + anchor


# ---- who coordinates: roles, rotation and settings ----------------------------------------------
@community_view
def organize(request, team_id: int):
    team = _working_team(request, team_id)
    members = rotas._members(team)
    orders = rotas.rotation_orders(team)
    roles = rotas.active_roles(team)
    for role in roles:
        places = {person_id: position for position, person_id in enumerate(orders.get(role.pk, []), start=1)}
        role.places = [(person, places.get(person.pk, "")) for person in members]
    from . import follow

    # What the team follows is always offered, also when this person could not choose it afresh (a series still in
    # preparation that the administration set): its name is on the team's dates already.
    series = list(follow.series_choices(team, is_church_admin=request.is_church_admin))
    if team.follows_series_id is not None and team.follows_series_id not in {row.pk for row in series}:
        series.insert(0, team.follows_series)
    for row in series:
        row.rule = follow.event_series_rules.rule_words(row)
    return render(request, "serving/organize.html", {"team": team, "roles": roles, "members": members,
                                                       "weeks": range(1, rotas.MAX_EVERY_WEEKS + 1), "series": series,
                                                       "templates": follow.templates_of(team), "max_need": rotas.MAX_NEED,
                                                       "max_templates": follow.MAX_TEMPLATES})


@community_view
@require_POST
@try_again
def role_save(request, team_id: int):
    team = _working_team(request, team_id)
    role = None
    if request.POST.get("rol"):
        role = get_object_or_404(TeamRole, pk=_number(request.POST.get("rol"), 0), team=team, active=True)
    fields = {"name": request.POST.get("nombre", ""), "adults_only": request.POST.get("solo_adultos") == "si"}
    # "Personas por fecha": absent from the form (an older page) leaves it as it is; anything that is not a number is refused.
    if "por_fecha" in request.POST:
        fields["default_count"] = _number(request.POST.get("por_fecha") or "0", -1)
    try:
        saved = rotas.save_role(team, request.user, role=role, release_token=request.POST.get("confirmar", ""), **fields)
    except rotas.RoleNeedsConfirmation as pending:
        # People the church has not verified as adults hold this role on dates that are coming: say who,
        # and what saving does, before anything changes.
        if request.POST.get("confirmar"):
            messages.warning(request, "La lista cambió desde que la viste. Revísala otra vez antes de confirmar.")
        return render(request, "serving/role_confirm.html", {"team": team, "role": role, "affected": pending.affected,
                                                              "token": pending.token, **fields})
    except services.ServingError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, f"Guardamos el rol {saved.name}.")
    return redirect(_organize_url(team, "#roles"))


@community_view
@require_POST
@try_again
def role_retire(request, team_id: int, role_id: int):
    team = _working_team(request, team_id)
    role = get_object_or_404(TeamRole, pk=role_id, team=team, active=True)
    try:
        rotas.retire_role(role, request.user)
    except services.ServingError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, f"El rol {role.name} ya no se usa. Quien ya tenía una fecha con ese rol la conserva.")
    return redirect(_organize_url(team, "#roles"))


@community_view
@require_POST
@try_again
def rotation_save(request, team_id: int):
    team = _working_team(request, team_id)
    members = rotas._members(team)
    orders = {}
    for role in rotas.active_roles(team):
        placed = []
        for index, person in enumerate(members):
            position = _number(request.POST.get(f"orden_{role.pk}_{person.pk}"))
            if position:
                placed.append((position, index, person.pk))
        orders[role.pk] = [person_id for _, _, person_id in sorted(placed)]
    try:
        rotas.save_rotation(team, request.user, every_weeks=_number(request.POST.get("cada"), 0), orders=orders)
    except services.ServingError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, "Guardamos la rotación. Todavía no se asigna nada: revisa lo que propone y confírmalo.")
    return redirect(_organize_url(team, "#rotacion"))


@community_view
@require_POST
@try_again
def settings_save(request, team_id: int):
    team = _working_team(request, team_id)
    try:
        rotas.save_settings(team, request.user, swap_needs_approval=request.POST.get("visto_bueno") == "si",
                            reminder_days=_number(request.POST.get("recordatorio"), -1))
    except services.ServingError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, "Guardamos los ajustes del equipo.")
    return redirect(_organize_url(team, "#ajustes"))


@community_view
@try_again
def rotation(request, team_id: int):
    """What the rotation proposes for the next weeks, to change and confirm. Nothing is assigned on GET."""
    team = _working_team(request, team_id)
    chosen: dict[str, int | None] = {}
    if request.method == "POST":
        proposal = rotas.build_proposal(team)
        for entry in proposal.dates:
            for slot in entry.slots:
                chosen[slot.key] = _number(request.POST.get(f"lugar_{slot.key}"))
        try:
            done = rotas.confirm_proposal(team, request.user, token=request.POST.get("token", ""), choices=chosen)
        except rotas.StaleProposal:
            messages.warning(request, "Algo cambió en las fechas o en quién puede servir desde que abriste esta página. No asignamos nada: revisa la propuesta otra vez.")
            chosen = {}
        except services.ServingError as error:
            messages.error(request, error.message)
        else:
            if done:
                messages.success(request, f"Le pediste servir a {done} persona{'s' if done != 1 else ''}. Cada quien lo verá en «Tus fechas» y dirá si puede.")
            else:
                messages.info(request, "No elegiste a nadie: no se asignó nada.")
            return redirect("serving:team", team.pk)
    proposal = rotas.build_proposal(team)
    for entry in proposal.dates:
        for slot in entry.slots:
            slot.selected = chosen.get(slot.key, slot.proposed) if chosen else slot.proposed
    return render(request, "serving/rotation.html", {"team": team, "proposal": proposal, "weeks": rotas.HORIZON_WEEKS,
                                                       "has_rotation": bool(rotas.rotation_orders(team))})


# ---- swaps --------------------------------------------------------------------------------------
def _own_assignment(request, assignment_id: int) -> Assignment:
    return get_object_or_404(
        Assignment.objects.select_related("service_date__team", "person", "role"), pk=assignment_id, person=request.person,
        service_date__team__church=request.church, service_date__team__status=Team.Status.ACTIVE,
    )


def _swap(request, swap_id: int, **who) -> SwapRequest:
    return get_object_or_404(
        SwapRequest.objects.select_related("assignment__service_date__team", "from_person", "to_person"), pk=swap_id,
        assignment__service_date__team__church=request.church, **who,
    )


@community_view
@try_again
def swap_create(request, assignment_id: int):
    assignment = _own_assignment(request, assignment_id)
    if not rotas.may_ask_swap(assignment):
        raise Http404
    candidates = rotas.swap_candidates(assignment)
    if request.method == "POST":
        chosen = _number(request.POST.get("persona"), 0)
        person = next((candidate for candidate in candidates if candidate.pk == chosen), None)
        try:
            if person is None:
                raise services.ServingError("Elige a quién le pides el cambio.", code="none")
            rotas.request_swap(assignment, person, request.user)
        except services.ServingError as error:
            messages.error(request, error.message)
        else:
            messages.success(request, f"Le pediste el cambio a {person.first_name}. Lo verá en «Servir» cuando entre a Emmaus; la fecha sigue siendo tuya hasta que acepte.")
            return redirect(reverse("serving:home") + "#cambios")
    waiting = rotas.open_swap_ids([assignment.pk]).get(assignment.pk)
    meta = _date_words(assignment.service_date) + (f" Tu rol: {assignment.role.name}." if assignment.role else "")
    return render(request, "serving/swap_form.html", {"assignment": assignment, "day": assignment.service_date, "meta": meta,
                                                        "candidates": candidates, "waiting": waiting})


@community_view
@require_POST
@try_again
def swap_answer(request, swap_id: int):
    swap = _swap(request, swap_id, to_person=request.person)
    answer = request.POST.get("respuesta")
    if answer not in ("aceptar", "declinar"):
        raise Http404
    try:
        swap = rotas.answer_swap(swap, answer == "aceptar", request.user)
    except services.ServingError as error:
        messages.error(request, error.message)
        return redirect(reverse("serving:home") + "#cambios")
    day = _weekday_date(swap.assignment.service_date.date)
    if swap.status == SwapRequest.Status.DONE:
        messages.success(request, f"Tomaste la fecha del {day}. Ya está en «Tus fechas», confirmada.")
    elif swap.status == SwapRequest.Status.TO_APPROVE:
        messages.success(request, f"Aceptaste la fecha del {day}. Falta el visto bueno de quien coordina el equipo: hasta entonces la fecha no cambia de manos.")
    else:
        messages.success(request, f"Dijiste que no puedes tomar la fecha del {day}.")
    return redirect(reverse("serving:home") + "#cambios")


@community_view
@require_POST
@try_again
def swap_cancel(request, swap_id: int):
    swap = _swap(request, swap_id, from_person=request.person)
    rotas.cancel_swap(swap, request.user)
    messages.success(request, "Retiraste tu solicitud de cambio. La fecha sigue siendo tuya.")
    return redirect(reverse("serving:home") + "#cambios")


@community_view
@require_POST
@try_again
def swap_decide(request, swap_id: int):
    swap = _swap(request, swap_id)
    team = swap.assignment.service_date.team
    if not team.is_active:
        raise Http404
    if not _may_manage(request, team):
        raise PermissionDenied
    decision = request.POST.get("decision")
    if decision not in ("aprobar", "rechazar"):
        raise Http404
    try:
        rotas.decide_swap(swap, decision == "aprobar", request.user)
    except services.ServingError as error:
        messages.error(request, error.message)
    else:
        if decision == "aprobar":
            messages.success(request, f"Diste el visto bueno: la fecha ahora es de {swap.to_person.full_name}.")
        else:
            messages.success(request, f"No diste el visto bueno: la fecha sigue siendo de {swap.from_person.full_name}.")
    return redirect(reverse("serving:team", args=[team.pk]) + "#cambios")


@community_view
@require_POST
@try_again
def reminders_choice(request):
    wanted = request.POST.get("recordatorios")
    if wanted not in ("si", "no"):
        raise Http404
    reminders.set_reminders(request.person, wanted == "si")
    if wanted == "si":
        messages.success(request, "Guardamos tu elección: los equipos que envían recordatorios te escribirán antes de tus fechas.")
    else:
        messages.success(request, "Guardamos tu elección: ya no te enviaremos recordatorios de tus fechas por correo.")
    return redirect(reverse("serving:home") + "#recordatorios")


# ---- the order of a date ------------------------------------------------------------------------
def _plan_date(request, date_id: int, *, to_edit: bool) -> tuple[ServiceDate, bool]:
    """A date that has not passed, of an active team of this church, and whether the caller may
    edit its order. Who serves that date reads it; anybody else of the church gets a 403."""
    service_date = get_object_or_404(
        ServiceDate.objects.select_related("team"), pk=date_id, team__church=request.church, team__status=Team.Status.ACTIVE,
        date__gte=timezone.localdate(),
    )
    manages = _may_manage(request, service_date.team)
    if not manages and (to_edit or not rotas.may_read_plan(request.person, service_date)):
        raise PermissionDenied
    return service_date, manages


def _plan_fields(request, service_date: ServiceDate) -> dict:
    chosen = _number(request.POST.get("responsable"), 0)
    responsible = None
    if chosen:
        responsible = Person.objects.filter(pk=chosen, church=request.church, merged_into__isnull=True).first()
        if responsible is None:
            raise services.ServingError("Elige como responsable a alguien del equipo.", code="responsible")
    return {"title": request.POST.get("titulo", ""), "minutes": _number(request.POST.get("minutos")), "responsible": responsible}


@community_view
@try_again
def plan(request, date_id: int):
    service_date, manages = _plan_date(request, date_id, to_edit=request.method == "POST")
    if request.method == "POST":
        try:
            rotas.save_plan_item(service_date, request.user, **_plan_fields(request, service_date))
        except services.ServingError as error:
            messages.error(request, error.message)
        else:
            messages.success(request, "Agregamos el momento al orden.")
        return redirect(reverse("serving:plan", args=[service_date.pk]) + "#agregar")
    items = rotas.plan_items(service_date)
    return render(request, "serving/plan.html", {
        "meta": _date_words(service_date),
        "day": service_date, "team": service_date.team, "items": items, "manages": manages,
        "total": sum(item.minutes for item in items), "people": rotas.plan_people(service_date) if manages else [],
        "previous": rotas.previous_with_plan(service_date) if manages and not items else None,
        "templates": list(PlanTemplate.objects.filter(team=service_date.team)) if manages else [],
        "called_off": service_date.called_off,
    })


@community_view
@require_POST
@try_again
def plan_item(request, date_id: int, item_id: int):
    service_date, _ = _plan_date(request, date_id, to_edit=True)
    item = get_object_or_404(PlanItem, pk=item_id, service_date=service_date)
    action = request.POST.get("accion")
    try:
        if action == "guardar":
            rotas.save_plan_item(service_date, request.user, item=item, **_plan_fields(request, service_date))
            messages.success(request, "Guardamos el cambio.")
        elif action == "quitar":
            rotas.delete_plan_item(item, request.user)
            messages.success(request, "Quitamos ese momento del orden.")
        elif action in ("subir", "bajar"):
            rotas.move_plan_item(item, action == "subir", request.user)
        else:
            raise Http404
    except services.ServingError as error:
        messages.error(request, error.message)
    return redirect("serving:plan", service_date.pk)


@community_view
@require_POST
@try_again
def plan_copy(request, date_id: int):
    service_date, _ = _plan_date(request, date_id, to_edit=True)
    try:
        copied = rotas.copy_previous_plan(service_date, request.user)
    except services.ServingError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, f"Copiamos {copied} momento{'s' if copied != 1 else ''} del orden anterior. Revísalos: son de otra fecha.")
    return redirect("serving:plan", service_date.pk)
