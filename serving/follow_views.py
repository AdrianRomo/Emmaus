"""Servir, third part: a team that follows a series of events, and the templates of its order
(``serving.follow``). The same rules as ``serving.rota_views``: ``community_view``, everything fetched
inside ``request.church`` (an id of another church is a 404), and each write is for who coordinates the
team or the administration (``views._coordinated_team`` → ``_may_manage``)."""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from agenda.models import EventSeries
from groups.views import community_view

from . import follow, services
from .models import PlanTemplate
from .rota_views import _number, _organize_url, _plan_date, _working_team
from .views import try_again


@community_view
@require_POST
@try_again
def follow_save(request, team_id: int):
    """Which series of events the team serves on, and the template its new dates start with."""
    team = _working_team(request, team_id)
    series = template = None
    chosen = _number(request.POST.get("serie"), 0)
    if chosen:
        # Another church's series, or one this person may not be offered, is "not in the list": never a 404
        # that would say it exists.
        series = EventSeries.objects.filter(pk=chosen, church=request.church).first()
        if series is None:
            messages.error(request, "Elige una serie de eventos de esta iglesia.")
            return redirect(_organize_url(team, "#serie"))
    chosen = _number(request.POST.get("plantilla"), 0)
    if chosen:
        template = PlanTemplate.objects.filter(pk=chosen, team=team).first()
        if template is None:
            messages.error(request, "Elige una plantilla de este equipo.")
            return redirect(_organize_url(team, "#serie"))
    try:
        written = follow.follow(team, request.user, series, is_church_admin=request.is_church_admin, default_template=template)
    except services.ServingError as error:
        messages.error(request, error.message)
        return redirect(_organize_url(team, "#serie"))
    if series is None:
        messages.success(request, "Guardado: el equipo no sigue ninguna serie. Las fechas que ya tenía se quedan como están.")
    elif written:
        messages.success(request, f"Guardado. Escribimos {written} fecha{'s' if written != 1 else ''} del equipo para las fechas de la serie. "
                                  "Todavía no se le pide a nadie: elige quién sirve o revisa lo que propone la rotación.")
    else:
        messages.success(request, "Guardado. No había fechas nuevas que escribir por ahora.")
    return redirect(reverse("serving:team", args=[team.pk]) if written else _organize_url(team, "#serie"))


@community_view
@require_POST
@try_again
def template_save(request, date_id: int):
    service_date, _ = _plan_date(request, date_id, to_edit=True)
    try:
        template = follow.save_template(service_date, request.user, request.POST.get("nombre", ""))
    except services.ServingError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, f"Guardaste la plantilla {template.name}: los momentos y sus minutos, sin responsables.")
    return redirect("serving:plan", service_date.pk)


@community_view
@require_POST
@try_again
def template_use(request, date_id: int):
    service_date, _ = _plan_date(request, date_id, to_edit=True)
    template = get_object_or_404(PlanTemplate, pk=_number(request.POST.get("plantilla"), 0), team=service_date.team)
    try:
        copied = follow.use_template(service_date, template, request.user)
    except services.ServingError as error:
        messages.error(request, error.message)
    else:
        messages.success(request, f"Empezaste el orden con la plantilla {template.name}: {copied} momento{'s' if copied != 1 else ''}. "
                                  "Elige quién queda a cargo de cada uno.")
    return redirect("serving:plan", service_date.pk)


@community_view
@try_again
def template_delete(request, team_id: int, template_id: int):
    team = _working_team(request, team_id)
    template = get_object_or_404(PlanTemplate, pk=template_id, team=team)
    back = _organize_url(team, "#plantillas")
    if request.method == "POST":
        follow.delete_template(template, request.user)
        messages.success(request, f"Quitaste la plantilla {template.name}.")
        return redirect(back)
    if request.method != "GET":
        raise Http404
    default = team.default_template_id == template.pk
    return render(
        request,
        "groups/confirm.html",
        {
            "title": f"¿Quitar la plantilla {template.name}?",
            "text": "Los órdenes que empezaron con ella se quedan como están."
                    + (" Las fechas nuevas del equipo empezaban con esta plantilla: empezarán sin orden." if default else ""),
            "action": "Sí, quitar la plantilla",
            "back_url": back,
        },
    )
