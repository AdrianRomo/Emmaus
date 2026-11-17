"""Who may open "Panorama". One question, asked by the view and by the shell."""

from core.models import Role, UserChurchRole

# The church roles that look after the whole church. A mentor does not; an event role (even "Pastor" of an event)
# and guiding a group grant nothing here: they are other scopes (AGENTS.md, invariant 2).
OVERVIEW_ROLES = frozenset({Role.PASTOR, Role.CHURCH_ADMIN})


def can_see_overview(user, church) -> bool:
    """``user`` holds the church role of pastor or administrator in ``church`` itself."""
    if church is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    return UserChurchRole.objects.filter(church=church, user=user, role__in=OVERVIEW_ROLES).exists()
