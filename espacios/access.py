"""Who may do what with the spaces. Nothing here invents a role: the administration is
``core.authz.has_scope`` (church ``manage``, already on ``request.is_church_admin``), a pastor is
the church role in ``core.UserChurchRole``, and guiding a group or coordinating a team are the rows
``has_scope`` reads (``groups.access.managed_group_ids``, ``serving.access.managed_team_ids``).

* See the calendar of a space: every person of the church with an account.
* Ask for a booking: who is responsible for something in the church: the administration, a pastor,
  who guides an active group, who coordinates an active team. Having taken part in an event, or
  holding an event role, gives nothing here.
* Read the title of a booking, who asked and the event it is for: the administration and who asked.
  Everybody else reads "Ocupado": a title can say something pastoral.
* Approve, reject, cancel any booking, manage the spaces, the usage report: the administration.
"""

from core.models import Role, UserChurchRole
from groups.access import managed_group_ids
from groups.models import Group
from serving.access import managed_team_ids
from serving.models import Team


def can_request(user, church, *, is_church_admin: bool | None = None, guides_group: bool | None = None) -> bool:
    """``is_church_admin`` and ``guides_group`` are answers the caller already has (the view's
    ``has_scope`` result, the shell's list of active groups the account guides): they save a query,
    they are never a way to grant."""
    if church is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if is_church_admin or guides_group:
        return True
    if UserChurchRole.objects.filter(church=church, user=user, role__in=(Role.PASTOR, Role.CHURCH_ADMIN)).exists():
        return True
    groups = managed_group_ids(user, church)
    if groups and Group.objects.filter(pk__in=groups, church=church, status=Group.Status.ACTIVE).exists():
        return True
    teams = managed_team_ids(user, church)
    return bool(teams) and Team.objects.filter(pk__in=teams, church=church, status=Team.Status.ACTIVE).exists()


def can_read_details(booking, person, is_church_admin: bool) -> bool:
    """The title, who asked and the linked event of ``booking``."""
    return bool(is_church_admin or (person is not None and booking.requested_by_id == person.pk))
