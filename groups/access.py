"""Read scopes of the community screens. Authorization itself is ``core.authz.has_scope``."""

from access.models import RoleAssignment, in_force
from access.sources import backed_by_source
from core.authz import GROUP_MANAGE_ROLES

from .models import Group


def church_groups(church):
    """The groups a person of the church may read about: the active ones of their own church."""
    return Group.objects.filter(church=church, status=Group.Status.ACTIVE)


def managed_group_ids(user, church) -> list[int]:
    """Ids of the groups ``user`` guides right now, from the same rows ``has_scope`` reads."""
    return list(
        RoleAssignment.objects.filter(
            in_force(), backed_by_source("group"), subject_user=user, church=church, scope_type="group",
            role__in=GROUP_MANAGE_ROLES,
        ).values_list("scope_id", flat=True)
    )
