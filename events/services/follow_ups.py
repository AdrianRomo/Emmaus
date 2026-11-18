"""Who attends a follow-up: its default owner, the ones nobody has, and the reminder to the people who have some.

Nothing here widens who may see a follow-up. An owner is only ever somebody who could already work on it
(``EventAccess.can_manage_follow_up``), and the counts come from ``EventAccess.follow_ups()``.
"""

from __future__ import annotations

from collections import Counter

from django.contrib.auth.models import User
from django.db.models import QuerySet

from ..access import resolve_access
from ..models import Event, FollowUp, TutorAssignment


def default_owner(follow_up: FollowUp) -> User | None:
    """The campero's coordinador; if they cannot attend it, that coordinador's tutor; otherwise nobody.

    "Cannot attend it" is the rule every screen uses (``can_manage_follow_up``), asked for that person now: a
    confidential follow-up or a safety report never goes to somebody without confidential access, and a person
    who left the team gets nothing. With nobody left it stays unassigned, and "Sin responsable" counts it.
    """
    coordinator = follow_up.participant.coordinator
    if coordinator is None:
        return None
    candidates = [coordinator]
    assignment = TutorAssignment.objects.filter(coordinator=coordinator).select_related("tutor__user").first()
    if assignment:
        candidates.append(assignment.tutor)
    for membership in candidates:
        if not membership.is_active or not membership.user.is_active:
            continue
        access = resolve_access(membership.user, follow_up.event)
        if access is not None and access.can_manage_follow_up(follow_up):
            return membership.user
    return None


def create(**fields) -> FollowUp:
    """A new follow-up with its default owner. Every follow-up of the camp is created here."""
    follow_up = FollowUp.objects.create(**fields)
    if follow_up.assigned_to_id is None:
        owner = default_owner(follow_up)
        if owner is not None:
            follow_up.assigned_to = owner
            follow_up.save(update_fields=["assigned_to", "updated_at"])
    return follow_up


def unassigned_open(access) -> QuerySet[FollowUp]:
    """The open follow-ups this person may see that nobody attends: what "Sin responsable" counts and lists."""
    return access.follow_ups().filter(status__in=FollowUp.OPEN_STATUSES, assigned_to__isnull=True)


def owners_to_remind(event: Event) -> list[tuple[User, int]]:
    """(owner, how many open follow-ups they attend) for the reminder: only people who can still open them.

    Somebody who left the team, lost the access a follow-up needs, or has no e-mail address gets no reminder;
    their follow-ups show as before on "Seguimiento" for whoever reassigns them.
    """
    open_rows = FollowUp.objects.filter(event=event, status__in=FollowUp.OPEN_STATUSES, assigned_to__isnull=False)
    owner_ids = Counter(open_rows.values_list("assigned_to_id", flat=True))
    owners = []
    for user in User.objects.filter(id__in=owner_ids, is_active=True).exclude(email="").order_by("id"):
        access = resolve_access(user, event)
        if access is None:
            continue
        count = access.follow_ups().filter(status__in=FollowUp.OPEN_STATUSES, assigned_to=user).count()
        if count:
            owners.append((user, count))
    return owners
