"""Authorization for the new modules (decision 0003): one question, deny by default.

``has_scope(user, church, scope_type, scope_id, action)`` answers whether ``user`` may ``view`` or
``manage`` one scope of ``church``. Church and event scopes are answered by the tables and the
function that already decide them (``UserChurchRole``, ``events.access.resolve_access``), so the
camp's answers cannot drift; group and ministry (a serving team) scopes are answered by
``access.RoleAssignment``. Nothing is implied across scopes. No action here answers a question about confidential answers, follow-ups
or pastoral notes: those stay behind ``events.access``.

``core/access.py`` and ``events/access.py`` are not edited; the legacy screens keep using them.
"""

from __future__ import annotations

from django.db import models

from .models import Church, Role, UserChurchRole

SESSION_KEY = "current_church"

# Group roles that may manage their group (groups.GroupMembership.Role.LEADER). A literal: core must not import groups.
GROUP_MANAGE_ROLES = frozenset({"lider"})
# Team roles that may manage their team (serving.TeamMembership.Role.COORDINATOR), decision 0016.
MINISTRY_MANAGE_ROLES = frozenset({"coordina"})
# The scopes answered by the projection, and the roles that manage each.
PROJECTED_MANAGE_ROLES = {"group": GROUP_MANAGE_ROLES, "ministry": MINISTRY_MANAGE_ROLES}


class Action(models.TextChoices):
    VIEW = "view", "Ver"
    MANAGE = "manage", "Administrar"


SCOPE_TYPES = frozenset({"church", "event", *PROJECTED_MANAGE_ROLES})


def _usable(user) -> bool:
    return bool(getattr(user, "is_authenticated", False) and user.is_active)


def has_scope(user, church: Church | None, scope_type: str, scope_id, action: str) -> bool:
    if scope_type not in SCOPE_TYPES:
        raise ValueError(f"Unknown scope type {scope_type!r}.")
    if action not in Action.values:
        raise ValueError(f"Unknown action {action!r}.")
    if not _usable(user) or church is None or scope_id is None:
        return False
    try:
        scope_id = int(scope_id)
    except (TypeError, ValueError):
        return False

    if scope_type == "church":
        if scope_id != church.pk:
            return False
        role = UserChurchRole.objects.filter(church=church, user=user).values_list("role", flat=True).first()
        if role is None:
            return False
        return True if action == Action.VIEW else role == Role.CHURCH_ADMIN

    if scope_type == "event":
        from events.access import resolve_access
        from events.models import Event

        event = Event.objects.filter(pk=scope_id, church=church).first()
        if event is None:
            return False
        access = resolve_access(user, event)
        if access is None:
            return False
        return True if action == Action.VIEW else access.can_manage_event

    from access.models import RoleAssignment, in_force
    from access.sources import backed_by_source

    # One query: the role rows in force that their source membership still backs (decision 0016, rule 7).
    roles = set(
        RoleAssignment.objects.filter(
            in_force(), backed_by_source(scope_type), subject_user=user, church=church, scope_type=scope_type,
            scope_id=scope_id,
        ).values_list("role", flat=True)
    )
    if not roles:
        return False
    return True if action == Action.VIEW else bool(roles & PROJECTED_MANAGE_ROLES[scope_type])


def churches_of(user) -> list[Church]:
    """Every church the account belongs to, oldest tie first: where it holds a church role, then
    where it is a person (``people.Person.user``). A role elsewhere or an event role adds nothing."""
    if not _usable(user):
        return []
    from people.models import Person

    churches = [role.church for role in UserChurchRole.objects.filter(user=user).select_related("church").order_by("id")]
    seen = {church.pk for church in churches}
    for person in Person.objects.filter(user=user, merged_into__isnull=True).select_related("church").order_by("id"):
        if person.church_id not in seen:
            seen.add(person.church_id)
            churches.append(person.church)
    return [church for church in churches if church.active]


def current_church(request) -> Church | None:
    """The church this request acts in for the new modules.

    The session may choose among the churches the account already belongs to (``switch_church``
    writes it); a stale or forged value is dropped and grants nothing. Without one: the church of
    the first church role, as ``core.access.get_user_church`` answers, else the first church where
    the account is a person.
    """
    churches = churches_of(request.user)
    if not churches:
        return None
    chosen = request.session.get(SESSION_KEY)
    if chosen is not None:
        for church in churches:
            if church.pk == chosen:
                return church
        request.session.pop(SESSION_KEY, None)
    return churches[0]


def switch_church(request, church_id) -> Church | None:
    """Choose the current church (called from a POST view). None, and nothing stored, when the
    account does not belong to that church."""
    for church in churches_of(request.user):
        if str(church.pk) == str(church_id):
            request.session[SESSION_KEY] = church.pk
            return church
    return None
