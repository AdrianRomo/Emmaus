"""The numbers of "Panorama". Read-only, scoped to one church, a fixed number of queries whatever
the number of groups, and nothing that identifies a person: no name, no id of a person, no row
per person leaves this module.

**The minimum reporting size** (decisions 0009 and 0015; ``people.policy.min_reporting_size``, 5).
A total over very few people says who, so a number that fails the rule never leaves this module:
what leaves is ``None`` or words, and the template writes them.

*A whole* (the people of a group, the people a list was taken over, the sign-ups of an event, the
people asked to serve on a date) is shown only when it is at least the minimum.

*A part of a whole* (who came out of who was expected, who attended out of who signed up, each
answer out of the people asked, who joined lately out of the people in groups) goes through one
function, ``share``. The exact number is shown only when **the part and its complement (whole −
part) are both at least the minimum**: "2 de 12" counts a few people, and "11 de 12" counts the one
who was absent. Otherwise words, chosen so that neither says a number:

* whole of 9 or more: "Menos de 5" (the part is 0 to 4) or "Casi todas" (the complement is 0 to 4);
* whole of 5 to 8, where those two ranges would overlap and "Casi todas" of five would mean
  "all five": "Menos de la mitad" or "La mitad o más";
* whole under the minimum: nothing ("Sin desglose de asistencia por privacidad", "Menos de 5").

So an exact part needs a whole of at least twice the minimum. *Parts that add up to the whole* (the
three answers of a serving date) are exact only when all of them pass; if one does not, the others
read "5 o más", because two exact parts would give the third by subtraction (``shares``).

Where this applies:

1. *A small group* (fewer active members than the minimum) shows no number at all: "Menos de 5"
   for its people, no last attendance, no trend.
2. *A meeting* whose list was taken over fewer people than the minimum shows no attendance and is
   left out of the trend. The others show their attendance, and the trend, through ``share``.
3. *The figures of the whole church* count only the people of the groups that meet the minimum, and
   the page says how many small groups were left out: every number on the page is then about those
   groups alone, so no subtraction between figures reaches a small group. "Se unieron a un grupo"
   is a part of "Personas en grupos".
4. *An event*, coming or finished, with fewer sign-ups than the minimum reads "Menos de 5" and its
   free places are told without a number (the same figure must not be exact the day before it is
   hidden). Free places are also told without a number when they would count the few minors who
   wait for an authorization. Who attended a finished event is a part of its sign-ups.
5. *A serving date* for which fewer people than the minimum were asked shows no answers; the
   others show each answer as a part of the people asked.

What no rule of this kind can hide: a number that crosses the minimum from one day to the next says
that it crossed. Counts that are not about what people did are left as they are: how many groups,
how many requests wait, the open follow-ups.
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
# Panorama is a summary, so these three are short on purpose and each table's caption says so ("Los últimos eventos
# que terminaron", "Las próximas fechas de cada equipo"); the whole lists are in "Eventos de la iglesia" and "Equipos".
FINISHED_EVENTS = 5
DATES_PER_TEAM = 3
MAX_DATES = 60          # dates read to find three per team; never a list on the screen


def share(part: int, whole: int, minimum: int) -> dict | None:
    """A part of a whole, as the page may tell it (the module's docstring has the rule): None when the
    whole is under the minimum, else ``level`` and ``label``, with the number in ``value`` only when the
    part and its complement are both at least the minimum."""
    if whole < minimum:
        return None
    if part >= minimum and whole - part >= minimum:
        return {"level": "exact", "value": part, "label": str(part)}
    if whole < 2 * minimum - 1:
        # Too few for two ranges of ``minimum`` values each: halves, so that no word means "all" or "none".
        return {"level": "under_half", "value": None, "label": "Menos de la mitad"} if part * 2 < whole else {"level": "half_up", "value": None, "label": "La mitad o más"}
    if part < minimum:
        return {"level": "few", "value": None, "label": f"Menos de {minimum}"}
    return {"level": "most", "value": None, "label": "Casi todas"}


def shares(parts: list[int], minimum: int) -> list[str] | None:
    """Parts that add up to their whole, each as the page may tell it. Exact only when every one of them
    is: with one in words, two exact numbers would give it away by subtraction, so they read "5 o más"."""
    whole = sum(parts)
    told = [share(part, whole, minimum) for part in parts]
    if told[0] is None:
        return None
    if all(one["level"] == "exact" for one in told):
        return [one["label"] for one in told]
    return [f"{minimum} o más" if one["level"] == "exact" else one["label"] for one in told]


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
                present = came.get(meeting["id"], 0) if kept is None else kept
                shown.append({"date": meeting["date"], "expected": meeting["expected"], "share": share(present, meeting["expected"], minimum)})
        group.small = group.pk not in reported
        group.member_count = None if group.small else members[group.pk]
        group.waiting = waiting.get(group.pk, 0)
        last = recorded[-1] if recorded else None
        # The day of the last list is always told; how many came only when the meeting met the minimum.
        group.last = (shown[-1] if last["shown"] else {"date": last["date"], "expected": None, "share": None}) if last else None
        group.trend = [meeting["share"]["label"].lower() for meeting in shown] if len(shown) > 1 else []
        group.missing, group.missing_day = missing_lists(group, last["date"] if last else None, today)
        # The next meeting is the one after the last list: today's meeting, once it has its list, is not "next".
        after = max(today, last["date"] + timedelta(days=1)) if last else today
        group.next_day = None if group.missing else group.next_meeting(after)
    small = sum(1 for group in groups if group.small)
    return {
        "groups": groups,
        "groups_small": small,
        "group_people": _figure(people["total"], minimum),
        # A part of the people in groups: None with the whole, else a number or words (``share``).
        "group_joined": joined["label"] if (joined := share(people["joined"], people["total"], minimum)) else None,
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
        event.takes_sign_ups = event.page.registration_open or event.taken > 0
        # Rule 4. Free places count who holds one: exact only beside an exact number of sign-ups, and not when the
        # difference between the two would count a few minors who wait for their authorization.
        left = None if capacity is None else max(capacity - event.held, 0)
        waiting = event.held - event.taken
        event.taken = _figure(event.taken, minimum)
        event.places_left = left if event.taken is not None and (waiting == 0 or waiting >= minimum) else None
        event.places_word = "" if left is None else ("Hay lugares" if left else "Ya no hay lugares")
        event.held = None
    finished = list(
        published.filter(last_day__lt=today)
        .annotate(
            came=Count("registrations", filter=signed & Q(registrations__attended=True)),
            marked=Count("registrations", filter=signed & Q(registrations__attended__isnull=False)),
        )
        .order_by("-last_day", "-id")[:FINISHED_EVENTS]
    )
    for event in finished:  # rule 4
        came = share(event.came, event.taken, minimum)
        event.taken = _figure(event.taken, minimum)
        event.came = came["label"] if came else None
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
            told = shares([service.confirmed, service.waiting, service.declined], minimum)  # rule 5
            service.few = told is None
            service.confirmed, service.waiting, service.declined = told or (None, None, None)
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
