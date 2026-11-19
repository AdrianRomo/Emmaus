"""Who may do what with the forms. No role is invented here.

* Create, edit, publish, close a form; name its readers; export; "Agregar a personas": the church's
  administration (``core.authz.has_scope`` church ``manage``, already on ``request.is_church_admin``).
* Read the answers of ONE form and work on its tasks: the administration, and whoever holds right
  now the scope a ``FormReader`` of that form names: the guide of that group, the coordinator of that
  team (``has_scope`` ``manage`` on it), while the group or the team is active.
* A pastor reads no form by being a pastor. Having answered a form, or having taken part in an
  event, gives nothing.

Everything is asked again on each request: a row of readers is a scope, never a list of people.
"""

from core.authz import Action, has_scope
from groups.access import managed_group_ids
from groups.models import Group
from serving.access import managed_team_ids
from serving.models import Team

from .models import Form, FormReader

GROUP, MINISTRY = FormReader.Scope.GROUP, FormReader.Scope.MINISTRY


def _usable(user) -> bool:
    return bool(getattr(user, "is_authenticated", False) and user.is_active)


def is_admin(user, church) -> bool:
    return church is not None and has_scope(user, church, "church", church.pk, Action.MANAGE)


def _scope_active(church, scope_type: str, scope_id: int) -> bool:
    if scope_type == GROUP:
        return Group.objects.filter(pk=scope_id, church=church, status=Group.Status.ACTIVE).exists()
    return Team.objects.filter(pk=scope_id, church=church, status=Team.Status.ACTIVE).exists()


def can_read(user, form: Form, *, is_church_admin: bool | None = None) -> bool:
    """The answers of ``form`` and its tasks. ``is_church_admin`` is an answer the caller already
    has for the form's own church; it saves a query and is never a way to grant."""
    if not _usable(user):
        return False
    church = form.church
    if is_church_admin is None:
        is_church_admin = is_admin(user, church)
    if is_church_admin:
        return True
    for scope_type, scope_id in form.readers.values_list("scope_type", "scope_id"):
        if has_scope(user, church, scope_type, scope_id, Action.MANAGE) and _scope_active(church, scope_type, scope_id):
            return True
    return False


def readable_form_ids(user, church) -> set[int]:
    """The forms of ``church`` whose answers ``user`` reads through a group or a team, from the same
    rows ``has_scope`` reads. The administration reads all of them and does not need this."""
    if not _usable(user) or church is None:
        return set()
    wanted: dict[str, dict[int, set[int]]] = {GROUP: {}, MINISTRY: {}}
    for form_id, scope_type, scope_id in FormReader.objects.filter(form__church=church).values_list("form_id", "scope_type", "scope_id"):
        wanted[scope_type].setdefault(scope_id, set()).add(form_id)
    found: set[int] = set()
    if wanted[GROUP]:
        mine = set(managed_group_ids(user, church)) & set(wanted[GROUP])
        for group_id in Group.objects.filter(pk__in=mine, church=church, status=Group.Status.ACTIVE).values_list("pk", flat=True):
            found |= wanted[GROUP][group_id]
    if wanted[MINISTRY]:
        mine = set(managed_team_ids(user, church)) & set(wanted[MINISTRY])
        for team_id in Team.objects.filter(pk__in=mine, church=church, status=Team.Status.ACTIVE).values_list("pk", flat=True):
            found |= wanted[MINISTRY][team_id]
    return found


def readable_forms(user, church, *, is_church_admin: bool):
    forms = Form.objects.filter(church=church)
    return forms if is_church_admin else forms.filter(pk__in=readable_form_ids(user, church))
