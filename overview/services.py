"""The numbers of "Panorama". Read-only, scoped to one church, a fixed number of queries whatever
the number of groups, and nothing that identifies a person: no name, no id of a person, no row
per person leaves this module."""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, F, Q, Window
from django.db.models.functions import Coalesce, RowNumber
from django.utils import timezone

from agenda.models import EventRegistration
from care.models import FollowUp
from events.models import Event
from groups.models import Group, GroupAttendance, GroupJoinRequest, GroupMeeting, GroupMembership
from serving.models import Assignment, ServiceDate, Team

TREND_MEETINGS = 8      # how many recorded meetings the trend of a group shows
STALE_DAYS = 21         # a group without a recorded meeting for this long is pointed out
JOINED_DAYS = 30
FINISHED_EVENTS = 5
DATES_PER_TEAM = 3
MAX_DATES = 60


def _groups(church, today) -> dict:
    groups = list(Group.objects.filter(church=church, status=Group.Status.ACTIVE).order_by("name", "id"))
    ids = [group.pk for group in groups]
    active = GroupMembership.objects.filter(group_id__in=ids, status=GroupMembership.Status.ACTIVE)
    members = dict(active.values_list("group_id").annotate(total=Count("id")).order_by())
    since = timezone.now() - timedelta(days=JOINED_DAYS)
    people = active.aggregate(
        total=Count("person_id", distinct=True),
        joined=Count("person_id", distinct=True, filter=Q(start_at__gte=since)),
    )
    waiting = dict(
        GroupJoinRequest.objects.filter(group_id__in=ids, status=GroupJoinRequest.Status.PENDING)
        .values_list("group_id").annotate(total=Count("id")).order_by()
    )
    # The last meetings of every group in one query: numbered inside each group, newest first.
    meetings = list(
        GroupMeeting.objects.filter(group_id__in=ids)
        .annotate(place=Window(RowNumber(), partition_by=[F("group_id")], order_by=[F("date").desc(), F("id").desc()]))
        .filter(place__lte=TREND_MEETINGS)
        .order_by("group_id", "date", "id")
        .values("id", "group_id", "date", "expected", "present_kept")
    )
    came = dict(
        GroupAttendance.objects.filter(meeting_id__in=[m["id"] for m in meetings if m["present_kept"] is None])
        .values_list("meeting_id").annotate(total=Count("id")).order_by()
    )
    by_group: dict[int, list[dict]] = {}
    for meeting in meetings:
        kept = meeting["present_kept"]
        meeting["present"] = came.get(meeting["id"], 0) if kept is None else kept
        by_group.setdefault(meeting["group_id"], []).append(meeting)

    stale_before = today - timedelta(days=STALE_DAYS)
    for group in groups:
        recorded = by_group.get(group.pk, [])  # oldest first
        group.member_count = members.get(group.pk, 0)
        group.waiting = waiting.get(group.pk, 0)
        group.last = recorded[-1] if recorded else None
        group.trend = [meeting["present"] for meeting in recorded] if len(recorded) > 1 else []
        if group.last:
            group.stale = group.last["date"] < stale_before
        else:
            group.stale = timezone.localtime(group.created_at).date() < stale_before
    return {
        "groups": groups,
        "group_people": people["total"],
        "group_joined": people["joined"],
        "groups_stale": sum(1 for group in groups if group.stale),
        "groups_waiting": sum(1 for group in groups if group.waiting),
    }


def _events(church, today) -> dict:
    signed = Q(registrations__status=EventRegistration.Status.ACTIVE)
    published = (
        Event.objects.filter(church=church, page__published=True)
        .select_related("page")
        .annotate(last_day=Coalesce("ends_on", "starts_on"), taken=Count("registrations", filter=signed))
    )
    upcoming = list(
        published.filter(status=Event.Status.ACTIVE, last_day__gte=today).order_by(F("starts_on").asc(nulls_last=True), "name", "id")
    )
    for event in upcoming:
        capacity = event.page.capacity
        event.capacity = capacity
        event.places_left = None if capacity is None else max(capacity - event.taken, 0)
        event.takes_sign_ups = event.page.registration_open or event.taken > 0
    finished = list(
        published.filter(last_day__lt=today)
        .annotate(
            came=Count("registrations", filter=signed & Q(registrations__attended=True)),
            marked=Count("registrations", filter=signed & Q(registrations__attended__isnull=False)),
        )
        .order_by("-last_day", "-id")[:FINISHED_EVENTS]
    )
    return {"upcoming": upcoming, "finished": finished}


def _serving(church, today) -> dict:
    state = Assignment.Status
    dates = (
        ServiceDate.objects.filter(team__church=church, team__status=Team.Status.ACTIVE, date__gte=today)
        .annotate(
            team_name=F("team__name"),
            confirmed=Count("assignments", filter=Q(assignments__status=state.ACCEPTED)),
            waiting=Count("assignments", filter=Q(assignments__status=state.PENDING)),
            declined=Count("assignments", filter=Q(assignments__status=state.DECLINED)),
        )
        .order_by("date", "starts_at", "id")[:MAX_DATES]
    )
    shown: dict[int, int] = {}
    rows = []
    for service in dates:
        shown[service.team_id] = shown.get(service.team_id, 0) + 1
        if shown[service.team_id] <= DATES_PER_TEAM:
            rows.append(service)
    rows.sort(key=lambda service: (service.team_name.lower(), service.team_id, service.date, service.pk))
    return {"serving_dates": rows}


def _care(church, viewer, today) -> dict:
    """Counts only. Never a follow-up about ``viewer``: the total would tell them it exists
    (``care.services.unassigned_count`` keeps the same rule)."""
    rows = FollowUp.objects.filter(church=church, status__in=FollowUp.OPEN)
    if viewer is not None:
        rows = rows.exclude(person=viewer)
    return rows.aggregate(
        care_open=Count("id"),
        care_late=Count("id", filter=Q(due_on__lt=today)),
        care_unowned=Count("id", filter=Q(owner__isnull=True)),
    )


def build(church, viewer, today=None) -> dict:
    """Everything the page shows. ``viewer`` is the reader's own person in the church, or None."""
    today = today or timezone.localdate()
    return {"today": today, **_groups(church, today), **_events(church, today), **_serving(church, today), **_care(church, viewer, today)}
