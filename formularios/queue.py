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

A row carries a kind, neutral words, a day and the address of the screen where the work is done.
Never the reason of a follow-up, the name of who it is about, the title of a booking, an answer or a
contact. The camp's follow-ups and the discipleship ones are not here: they stay behind their own
confidential screens (``events.access``, ``core.access``).
"""

from dataclasses import dataclass
from datetime import date

from django.urls import reverse

from care import services as care_services
from espacios import services as spaces_services
from groups.access import managed_group_ids
from groups.models import Group, GroupJoinRequest
from resources import approvals

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

    found.sort(key=lambda row: (row.due_on is None, not row.overdue, row.due_on or date.max))
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
    if approvals.can_approve(user, church):
        total += approvals.pending_count(church)
    if is_church_admin:
        total += care_services.unassigned_count(church, person) + spaces_services.pending_count(church) + unowned_tasks(church).count()
    return total
