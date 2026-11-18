"""The numbers of "Panorama". Read-only, scoped to one church, a fixed number of queries whatever
the number of groups, and nothing that identifies a person: no name, no id of a person, no row
per person leaves this module.

**The minimum reporting size** (decisions 0009 and 0015; ``people.policy.min_reporting_size``, 5).
A total over very few people says who, so a number that fails the rule never leaves this module: the
attribute is ``None`` and a flag beside it says why, and the template writes words in its place.

1. *A small group* (fewer active members than the minimum) shows no number at all: "Menos de 5"
   for its people, no last attendance, no trend.
2. *A meeting* whose list was taken over fewer people than the minimum (``expected``) shows no
   attendance, and is left out of the trend, whatever the size of the group today.
3. *The figures of the whole church* ("Personas en grupos", "Se unieron a un grupo") count only the
   people of the groups that meet the minimum, and the page says how many small groups were left
   out. Every exact number on the page is then about those groups alone, so no subtraction between
   figures reaches a small group. A figure that is itself under the minimum reads "Menos de 5".
4. *A finished event* with fewer sign-ups than the minimum shows "Menos de 5" for both columns; with
   enough sign-ups, an attendance under the minimum still reads "Menos de 5".
5. *A serving date* for which fewer people than the minimum were asked shows no answer counts.

Counts that are not about what people did are left as they are: how many groups, how many requests
wait, the places of an event that is still coming, the open follow-ups.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, F, Q, Window
from django.db.models.functions import Coalesce, RowNumber
from django.utils import timezone

from agenda.models import EventRegistration
from care.models import FollowUp
from events.models import Event
from groups.models import Group, GroupAttendance, GroupJoinRequest, GroupMeeting, GroupMembership
from people.policy import min_reporting_size
from serving.models import Assignment, ServiceDate, Team

TREND_MEETINGS = 8      # how many recorded meetings the trend of a group is taken from
GRACE_DAYS = 2          # a meeting is not "por registrar" until this many days have passed
EARLY_DAYS = 3          # a list taken up to this many days before a due meeting counts as that meeting's
MISSING_CAP = 3         # "3 o más": the page does not count further back
JOINED_DAYS = 30
FINISHED_EVENTS = 5
DATES_PER_TEAM = 3
MAX_DATES = 60


def missing_lists(group, last_recorded, today) -> tuple[int, object]:
    """How many of the group's last due meetings have no list (up to ``MISSING_CAP``), and the day of the
    most recent one. A meeting is due by the group's own rhythm (``Group.last_meeting_day``), so a monthly
    group three weeks after it met has nothing missing, and neither has a group without a fixed day.
    Not counted: a meeting of the last ``GRACE_DAYS`` days, one from before the group existed, and any
    that came before the last list the group did record (the page points at where support is needed
    now, not at old gaps)."""
    floor = timezone.localtime(group.created_at).date() - timedelta(days=1)
    if last_recorded is not None:
        floor = max(floor, last_recorded + timedelta(days=EARLY_DAYS))
    count, newest = 0, None
    day = group.last_meeting_day(today - timedelta(days=GRACE_DAYS + 1))
    while day is not None and day > floor and count < MISSING_CAP:
        count += 1
        newest = newest or day
        day = group.last_meeting_day(day - timedelta(days=1))
    return count, newest


def _groups(church, today, minimum) -> dict:
    groups = list(Group.objects.filter(church=church, status=Group.Status.ACTIVE).order_by("name", "id"))
    ids = [group.pk for group in groups]
    active = GroupMembership.objects.filter(group_id__in=ids, status=GroupMembership.Status.ACTIVE)
    members = dict(active.values_list("group_id").annotate(total=Count("id")).order_by())
    reported = [pk for pk in ids if members.get(pk, 0) >= minimum]
    since = timezone.now() - timedelta(days=JOINED_DAYS)
    # Rule 3: only the people of the groups that meet the minimum.
    people = active.filter(group_id__in=reported).aggregate(
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
    by_group: dict[int, list[dict]] = {}
    for meeting in meetings:
        # Rules 1 and 2: who came is not even counted for a meeting whose total will not be shown.
        meeting["shown"] = meeting["group_id"] in reported and meeting["expected"] >= minimum
        by_group.setdefault(meeting["group_id"], []).append(meeting)
    came = dict(
        GroupAttendance.objects.filter(meeting_id__in=[m["id"] for m in meetings if m["shown"] and m["present_kept"] is None])
        .values_list("meeting_id").annotate(total=Count("id")).order_by()
    )

    for group in groups:
        recorded = by_group.get(group.pk, [])  # oldest first
        shown = []
        for meeting in recorded:
            if meeting["shown"]:
                kept = meeting["present_kept"]
                shown.append({"date": meeting["date"], "expected": meeting["expected"], "present": came.get(meeting["id"], 0) if kept is None else kept})
        group.small = group.pk not in reported
        group.member_count = None if group.small else members[group.pk]
        group.waiting = waiting.get(group.pk, 0)
        last = recorded[-1] if recorded else None
        # The day of the last list is always told; how many came only when the meeting met the minimum.
        group.last = (shown[-1] if last["shown"] else {"date": last["date"], "expected": None, "present": None}) if last else None
        group.trend = [meeting["present"] for meeting in shown] if len(shown) > 1 else []
        group.missing, group.missing_day = missing_lists(group, last["date"] if last else None, today)
        # The next meeting is the one after the last list: today's meeting, once it has its list, is not "next".
        after = max(today, last["date"] + timedelta(days=1)) if last else today
        group.next_day = None if group.missing else group.next_meeting(after)
    small = sum(1 for group in groups if group.small)
    return {
        "groups": groups,
        "groups_small": small,
        "group_people": _figure(people["total"], minimum),
        "group_joined": _figure(people["joined"], minimum),
        "groups_missing": sum(1 for group in groups if group.missing),
        "groups_waiting": sum(1 for group in groups if group.waiting),
    }


def _figure(value: int, minimum: int) -> int | None:
    """The number, or None when it is under the minimum reporting size (the page writes "Menos de 5")."""
    return value if value >= minimum else None


def _events(church, today, minimum) -> dict:
    signed = Q(registrations__status=EventRegistration.Status.ACTIVE)
    published = (
        Event.objects.filter(church=church, page__published=True)
        .select_related("page")
        .annotate(
            last_day=Coalesce("ends_on", "starts_on"), taken=Count("registrations", filter=signed),
            # a minor waiting for the authorization holds a place (decision 0010)
            held=Count("registrations", filter=signed | Q(registrations__status=EventRegistration.Status.PENDING)),
        )
    )
    upcoming = list(
        published.filter(status=Event.Status.ACTIVE, last_day__gte=today).order_by(F("starts_on").asc(nulls_last=True), "name", "id")
    )
    for event in upcoming:
        capacity = event.page.capacity
        event.capacity = capacity
        event.places_left = None if capacity is None else max(capacity - event.held, 0)
        event.takes_sign_ups = event.page.registration_open or event.taken > 0
    finished = list(
        published.filter(last_day__lt=today)
        .annotate(
            came=Count("registrations", filter=signed & Q(registrations__attended=True)),
            marked=Count("registrations", filter=signed & Q(registrations__attended__isnull=False)),
        )
        .order_by("-last_day", "-id")[:FINISHED_EVENTS]
    )
    for event in finished:  # rule 4
        event.taken = _figure(event.taken, minimum)
        event.came = None if event.taken is None else _figure(event.came, minimum)
        event.marked, event.held = bool(event.marked), None
    return {"upcoming": upcoming, "finished": finished}


def _serving(church, today, minimum) -> dict:
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
            service.few = service.confirmed + service.waiting + service.declined < minimum  # rule 5
            if service.few:
                service.confirmed = service.waiting = service.declined = None
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
    minimum = min_reporting_size(church)
    return {
        "today": today, "minimum": minimum,
        **_groups(church, today, minimum), **_events(church, today, minimum), **_serving(church, today, minimum),
        **_care(church, viewer, today),
    }
