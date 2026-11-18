"""Read scopes of Servir. Authorization itself is ``core.authz.has_scope`` (decisions 0003 and 0016)."""

from access.models import RoleAssignment, in_force
from core.authz import MINISTRY_MANAGE_ROLES


def managed_team_ids(user, church) -> list[int]:
    """Ids of the teams ``user`` coordinates right now, from the same rows ``has_scope`` reads."""
    if user is None or not user.is_active:
        return []
    return list(
        RoleAssignment.objects.filter(
            in_force(), subject_user=user, church=church, scope_type="ministry", role__in=MINISTRY_MANAGE_ROLES
        ).values_list("scope_id", flat=True)
    )
