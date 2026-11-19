"""«Pendientes»: what waits for one signed-in person of the church, in one read-only list.

It owns no data and writes nothing. Each source is read with the rule its own screen applies, asked
again on every request, so a row never outlives the permission behind it:

| Source | Who gets a row | Rule reused |
|---|---|---|
| A follow-up of a person | its owner | ``care.services.mine`` (what «Acompañar» lists) |
| Follow-ups without an owner | the administration | ``care.services.unassigned_count`` |
| Requests to join a group | who guides that active group | ``groups.access.managed_group_ids`` (``has_scope``) |
| Requests of a space | the administration | ``espacios.services.pending_count`` |
| Content to approve | a pastor of the church | ``resources.approvals.can_approve`` |
| A task of a form | its owner, while they read that form | ``formularios.access.can_read`` |
| Tasks of forms without an owner | the administration | church ``manage`` |
| A swap of a date somebody asked this person | who was asked | ``serving.rotas.swaps_to_answer`` (what «Servir» lists) |
| Swaps that wait for the coordinator's approval | who coordinates that active team, or the administration | ``has_scope`` (ministry, manage), as ``serving.views._may_manage`` |
| Requests of a person to have their data removed | the administration | ``cuenta_datos.services.open_count`` (church ``manage``) |

A row carries a kind, neutral words, a day and the address of the screen where the work is done.
Never the reason of a follow-up, the name of who it is about, the title of a booking, an answer or a
contact, and for a swap never who asked, the role or the day's name. The camp's follow-ups and the
discipleship ones are not here: they stay behind their own confidential screens (``events.access``,
``core.access``). Neither are the prayer requests (``oracion``): nothing of them, not even how many, is shown
outside their readers' own screens, so this module never reads that app.
"""

from dataclasses import dataclass
from datetime import date

from django.urls import reverse

from care import services as care_services
from cuenta_datos import services as data_requests
from core.authz import Action, has_scope
from espacios import services as spaces_services
from groups.access import managed_group_ids
from groups.models import Group, GroupJoinRequest
from resources import approvals
from serving import rotas
from serving.models import SwapRequest, Team

from . import access, services
from .models import FormTask


@dataclass
class Row:
    kind: str
    title: str
    url: str
    due_on: date | None = None
    overdue: bool = False
    count: int = 1  # how many things wait behind this row


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def rows(user, church, person, *, is_church_admin: bool) -> list[Row]:
    """Every row for ``user`` in ``church``: the dated ones first (overdue, then by day), then the rest."""
    found: list[Row] = []
    today = services.church_today(church)

    if person is not None:
        for follow_up in care_services.mine(person):
            found.append(Row("Acompañar", "Buscar a una persona de la iglesia", reverse("care:detail", args=[follow_up.pk]),
                             follow_up.due_on, bool(follow_up.due_on and follow_up.due_on < today)))

    managed = Group.objects.filter(pk__in=managed_group_ids(user, church), church=church, status=Group.Status.ACTIVE).order_by("name", "id")
    asked: dict[int, int] = {}
    for group_id in GroupJoinRequest.objects.filter(group__in=managed, status=GroupJoinRequest.Status.PENDING).values_list("group_id", flat=True):
        asked[group_id] = asked.get(group_id, 0) + 1
    for group in managed:
        if asked.get(group.pk):
            found.append(Row("Grupos", f"{_plural(asked[group.pk], 'solicitud', 'solicitudes')} para unirse a {group.name}",
                             reverse("community:group_people", args=[group.pk]), count=asked[group.pk]))

    if approvals.can_approve(user, church):
        waiting = approvals.pending_count(church)
        if waiting:
            found.append(Row("Aprobación pastoral", _plural(waiting, "contenido por aprobar", "contenidos por aprobar"),
                             reverse("resources:approval_queue"), count=waiting))

    # A task whose owner lost the role that let them read its form is not theirs any more: it waits for the administration.
    for task in my_form_tasks(user, church, is_church_admin=is_church_admin):
        found.append(Row("Formularios", f"Dar seguimiento a una respuesta de «{task.form.title}»",
                         reverse("formularios:submission", args=[task.form_id, task.submission_id]),
                         task.due_on, bool(task.due_on and task.due_on < today)))

    found.extend(swap_rows(user, church, person, is_church_admin=is_church_admin))

    if is_church_admin:
        loose = care_services.unassigned_count(church, person)
        if loose:
            found.append(Row("Acompañar", _plural(loose, "seguimiento sin responsable", "seguimientos sin responsable"),
                             reverse("care:admin_list"), count=loose))
        bookings = spaces_services.pending_count(church)
        if bookings:
            found.append(Row("Espacios", _plural(bookings, "solicitud de reserva por decidir", "solicitudes de reserva por decidir"),
                             reverse("espacios:queue"), count=bookings))
        unowned = unowned_tasks(church).count()
        if unowned:
            found.append(Row("Formularios", _plural(unowned, "pendiente de formulario sin responsable", "pendientes de formularios sin responsable"),
                             reverse("formularios:loose_tasks"), count=unowned))
        removals = data_requests.open_count(church)
        if removals:
            found.append(Row("Datos personales", _plural(removals, "solicitud sobre datos personales", "solicitudes sobre datos personales"),
                             reverse("cuenta_datos:requests"), count=removals))

    found.sort(key=lambda row: (row.due_on is None, not row.overdue, row.due_on or date.max))
    return found


def swap_rows(user, church, person, *, is_church_admin: bool) -> list[Row]:
    """The swaps of dates to serve that wait for this account, with the rules of «Servir» (``serving.rota_views``):
    one row for each swap a teammate asked this person and that still waits for their answer, and one row per
    active team this account coordinates (every active team, for the administration) with swaps that wait for
    its approval. A swap that expired, or whose date passed, waits for nobody. Never who asked or which role."""
    found: list[Row] = []
    if person is not None:
        for swap in rotas.swaps_to_answer(person):
            found.append(Row("Servir", "Te pidieron un cambio de fecha", reverse("serving:home") + "#cambios",
                             swap.assignment.service_date.date))
    waiting: dict[int, int] = {}
    to_approve = SwapRequest.objects.filter(
        assignment__service_date__team__church=church, assignment__service_date__team__status=Team.Status.ACTIVE,
        status=SwapRequest.Status.TO_APPROVE,
    ).select_related("assignment__service_date__team")
    for swap in to_approve:
        if rotas.is_live(swap):
            team_id = swap.assignment.service_date.team_id
            waiting[team_id] = waiting.get(team_id, 0) + 1
    for team in Team.objects.filter(pk__in=waiting, church=church).order_by("name", "id"):
        if is_church_admin or has_scope(user, church, "ministry", team.pk, Action.MANAGE):
            found.append(Row("Servir", f"{_plural(waiting[team.pk], 'cambio de fecha espera', 'cambios de fecha esperan')} tu visto bueno en {team.name}",
                             reverse("serving:team", args=[team.pk]), count=waiting[team.pk]))
    return found


def unowned_tasks(church):
    """The open tasks of forms of ``church`` nobody is doing: without an owner, or whose owner no
    longer reads that form (the role that let them ended). The administration's to hand out."""
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    from .models import Form

    tasks = FormTask.objects.filter(form__church=church, status=FormTask.Status.OPEN)
    lost = Q()
    pairs = tasks.filter(owner__isnull=False).order_by().values_list("owner_id", "form_id").distinct()
    users = get_user_model().objects.in_bulk({owner for owner, _ in pairs})
    forms = Form.objects.select_related("church").in_bulk({form for _, form in pairs})
    for owner_id, form_id in pairs:
        if not access.can_read(users[owner_id], forms[form_id]):
            lost |= Q(owner_id=owner_id, form_id=form_id)
    return tasks.filter(Q(owner__isnull=True) | lost)


def my_form_tasks(user, church, *, is_church_admin: bool) -> list[FormTask]:
    """The open tasks ``user`` owns in forms of ``church`` whose answers they read right now."""
    tasks = list(FormTask.objects.filter(form__church=church, owner=user, status=FormTask.Status.OPEN).select_related("form").order_by("due_on", "id"))
    if tasks and not is_church_admin:
        readable = access.readable_form_ids(user, church)
        tasks = [task for task in tasks if task.form_id in readable]
    return tasks


def count(user, church, person, *, is_church_admin: bool, care_mine: int | None = None, group_requests: int | None = None) -> int:
    """How many things wait: what the shell's badge says, with count queries instead of the rows.
    ``care_mine`` and ``group_requests`` are numbers the shell already has from the same rules
    (``care.services.open_count``, the pending requests of the active groups the account guides).
    ``formularios/tests`` holds this to the sum of ``rows`` for every kind of person."""
    if care_mine is None:
        care_mine = care_services.open_count(person) if person is not None else 0
    if group_requests is None:
        managed = Group.objects.filter(pk__in=managed_group_ids(user, church), church=church, status=Group.Status.ACTIVE)
        group_requests = GroupJoinRequest.objects.filter(group__in=managed, status=GroupJoinRequest.Status.PENDING).count()
    total = care_mine + group_requests + len(my_form_tasks(user, church, is_church_admin=is_church_admin))
    total += sum(row.count for row in swap_rows(user, church, person, is_church_admin=is_church_admin))
    if approvals.can_approve(user, church):
        total += approvals.pending_count(church)
    if is_church_admin:
        total += care_services.unassigned_count(church, person) + spaces_services.pending_count(church) + unowned_tasks(church).count()
        total += data_requests.open_count(church)
    return total
