"""Church policies: the rules a church sets for itself, in one place (decision 0015).

Every function takes the church. A church's own choices are one row of ``policies.ChurchPolicy``,
written from "Políticas de la iglesia" (``policies.views``); a church without a row, an empty
column and ``church=None`` give the product default. Some values have a floor the platform enforces
whatever a church chooses: a safeguard or a privacy minimum is not a preference. The floors and
ceilings are applied here, on every read, as well as by the form: a row edited by hand or by a bug
never lowers one.

The row is read once per church object (a request loads its church once) and again after any row
is saved. Nothing is kept outside the object the caller passed.

Why a value one administrator raised is read by every other request at once, with several workers:
what was read lives only as an attribute of the ``Church`` object the caller passed, and no
``Church`` object outlives a request (``core.authz.current_church`` and every queryset build new
ones; nothing in the project keeps one in a module, a cache or an ``lru_cache``). So the first read
of any request, in any process, asks the table. The counter in ``policies.models`` is per process
and only ever makes an object read *again* (after a save in the same process, mostly for tests and
for the request that saved); it is never what makes another worker see the change, and a counter
that did not move cannot keep an answer alive past the object that holds it. The longest a stale
answer can live is the rest of the one request that had already read before the other one saved.
Keep it so: do not pass these functions a ``Church`` held in a module or a process-level cache.
"""

from typing import NamedTuple

from django.conf import settings

# Fewer people than this and a total says who: no attendance breakdown, no count of answers,
# no results of a survey. Platform floor; a church may raise it, never lower it.
MIN_REPORTING_SIZE_FLOOR = 5
# Above this, a church of ordinary size would see no totals at all.
MIN_REPORTING_SIZE_CEILING = 20
# Days after an event's last day during which who went can give an opinion.
FEEDBACK_OPEN_DAYS = 14
FEEDBACK_OPEN_DAYS_MIN = 7
FEEDBACK_OPEN_DAYS_MAX = 30
# Group attendance (``prune_attendance``): months the rows that say who came are kept, and months the
# meeting's totals are kept. Product defaults to validate with each church's privacy notice (issue #76).
ATTENDANCE_IDENTIFIABLE_MONTHS = 12
ATTENDANCE_IDENTIFIABLE_MONTHS_FLOOR = 6
ATTENDANCE_IDENTIFIABLE_MONTHS_CEILING = 24
ATTENDANCE_AGGREGATE_MONTHS = 24
ATTENDANCE_AGGREGATE_MONTHS_FLOOR = 12
ATTENDANCE_AGGREGATE_MONTHS_CEILING = 60
# Months a guardian's authorization is kept after the event (initial policy, to be approved: issue #75).
CONSENT_RETENTION_MONTHS = 24
# The version of the words a guardian's authorization is recorded under. "borrador": the form's text
# is NOT approved legal text; the church's leadership and its adviser approve it (issue #75). A new
# version makes the authorizations recorded under an older one stop confirming a new sign-up.
CONSENT_POLICY_VERSION = "borrador-2026-11"
# Hours somebody on an event's waiting list has to accept a place that was offered (decision 0011).
# Under the floor a person who reads the notice at night would lose the place before the morning.
WAITING_OFFER_HOURS = 48
WAITING_OFFER_HOURS_FLOOR = 12

# The lowest and highest value a church may store, by column of ``policies.ChurchPolicy``.
BOUNDS = {
    "min_reporting_size": (MIN_REPORTING_SIZE_FLOOR, MIN_REPORTING_SIZE_CEILING),
    "feedback_open_days": (FEEDBACK_OPEN_DAYS_MIN, FEEDBACK_OPEN_DAYS_MAX),
    "attendance_identifiable_months": (ATTENDANCE_IDENTIFIABLE_MONTHS_FLOOR, ATTENDANCE_IDENTIFIABLE_MONTHS_CEILING),
    "attendance_aggregate_months": (ATTENDANCE_AGGREGATE_MONTHS_FLOOR, ATTENDANCE_AGGREGATE_MONTHS_CEILING),
}
STORED = (*BOUNDS, "urgent_notice_pastors", "minors_need_consent_by_default")
_CACHE = "_emmaus_church_policy"


class AttendanceRetention(NamedTuple):
    identifiable_months: int
    aggregate_months: int


def _stored(church) -> dict:
    """The church's row as a dict, ``{}`` without one or for anything that is not a saved church."""
    if church is None or not isinstance(getattr(church, "pk", None), int):
        return {}
    from policies.models import ChurchPolicy, generation

    now = generation()
    kept = getattr(church, _CACHE, None)
    if isinstance(kept, tuple) and kept[0] == now:
        return kept[1]
    values = ChurchPolicy.objects.filter(church_id=church.pk).values(*STORED).first() or {}
    try:
        setattr(church, _CACHE, (now, values))
    except AttributeError:  # an object that takes no attributes: read every time
        pass
    return values


def _within(name: str, value, default: int) -> int:
    """``value`` inside the platform's bounds for ``name``; the default when there is none."""
    low, high = BOUNDS[name]
    if value is None or isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(low, min(high, value))


def consent_policy_version(church=None) -> str:
    return CONSENT_POLICY_VERSION


def min_reporting_size(church=None) -> int:
    return _within("min_reporting_size", _stored(church).get("min_reporting_size"), MIN_REPORTING_SIZE_FLOOR)


def feedback_open_days(church=None) -> int:
    return _within("feedback_open_days", _stored(church).get("feedback_open_days"), FEEDBACK_OPEN_DAYS)


def urgent_notice_roles(church=None) -> frozenset[str]:
    """Who may publish an urgent notice: church roles, by their value in ``core.models.Role``.
    The administration always; the pastors when the church said so."""
    if _stored(church).get("urgent_notice_pastors") is True:
        return frozenset({"church_admin", "pastor"})
    return frozenset({"church_admin"})


def minors_consent_default(church=None) -> bool:
    """Whether "Los menores de edad necesitan autorización" starts switched on when the
    administration creates the page of an event. Each event still decides for itself."""
    return _stored(church).get("minors_need_consent_by_default") is True


def waiting_offer_hours(church=None) -> int:
    """Hours an offered place of an event's waiting list is held for the person before it goes to
    the next one. One value for every church today (``settings.EMMAUS_WAITING_OFFER_HOURS`` for an
    operator), never under the floor."""
    hours = getattr(settings, "EMMAUS_WAITING_OFFER_HOURS", WAITING_OFFER_HOURS)
    if isinstance(hours, bool) or not isinstance(hours, int):
        return WAITING_OFFER_HOURS
    return max(WAITING_OFFER_HOURS_FLOOR, hours)


def attendance_retention(church=None) -> AttendanceRetention:
    """Months the names of who came are kept and months the totals are kept, in that order.

    Without a choice of the church: the two settings an operator may set, as they are (what
    ``prune_attendance`` read before). A church's own numbers are held inside the platform's bounds,
    and the totals are never kept for less time than the names."""
    identifiable = getattr(settings, "EMMAUS_ATTENDANCE_IDENTIFIABLE_MONTHS", ATTENDANCE_IDENTIFIABLE_MONTHS)
    aggregate = getattr(settings, "EMMAUS_ATTENDANCE_AGGREGATE_MONTHS", ATTENDANCE_AGGREGATE_MONTHS)
    stored = _stored(church)
    own_identifiable = stored.get("attendance_identifiable_months")
    own_aggregate = stored.get("attendance_aggregate_months")
    if own_identifiable is None and own_aggregate is None:
        return AttendanceRetention(identifiable, aggregate)
    identifiable = _within("attendance_identifiable_months", own_identifiable, _within(
        "attendance_identifiable_months", identifiable, ATTENDANCE_IDENTIFIABLE_MONTHS))
    aggregate = _within("attendance_aggregate_months", own_aggregate, _within(
        "attendance_aggregate_months", aggregate, ATTENDANCE_AGGREGATE_MONTHS))
    return AttendanceRetention(identifiable, max(aggregate, identifiable))


def forget(church=None) -> None:
    """Drop what was read: the next call asks the table again (after a write outside ``save``)."""
    from policies.models import forget as forget_everywhere

    forget_everywhere()
    if church is not None and hasattr(church, _CACHE):
        delattr(church, _CACHE)
