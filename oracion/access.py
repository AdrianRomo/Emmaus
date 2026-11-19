"""Who writes a prayer request and who reads one. Asked on every request: nothing here is cached.

Readers are the people who hold the church role of **pastor** in that church *now*
(``resources.approvals.can_approve``, the same question the pastoral approval asks). Losing the role
ends the access at the next request. The administration, a guide of a group, the owner of a
follow-up, a pastor of another church and a superuser are not readers: no rule here looks at
``is_superuser`` or ``is_staff``, and ``has_scope`` is never asked, because no scope grants this.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from people import policy

from core.models import Role, UserChurchRole
from people.models import Person
from resources.approvals import can_approve

from .models import PrayerRequest

NAMELESS = "Una persona con el rol de pastor"


def alive(rows, now=None):
    """Only the requests whose date has not passed. Every screen, count and action reads through this:
    a request past ``expires_at`` (or older than the platform's 90 days) is gone for everybody whether
    or not the job that deletes it has run."""
    now = now or timezone.now()
    return rows.filter(expires_at__gt=now, created_at__gt=now - timedelta(days=policy.PRAYER_RETENTION_DAYS))


def own(user, church):
    """What the ACCOUNT ``user`` wrote in ``church`` and is still there. Never by person record."""
    if church is None or not getattr(user, "is_authenticated", False):
        return PrayerRequest.objects.none()
    return alive(PrayerRequest.objects.filter(church=church, author=user))


def name_writers(church, prayers: list) -> None:
    """``prayer.writer_name`` for a pastor's screen: the name of the person the writing account is in
    this church today, else the account's own name."""
    people = {
        person.user_id: person.full_name
        for person in Person.objects.filter(church=church, user_id__in={p.author_id for p in prayers}, merged_into__isnull=True)
    }
    for prayer in prayers:
        account = prayer.author
        prayer.writer_name = people.get(prayer.author_id) or (account.get_full_name().strip() if account else "") or "Una persona de tu iglesia"


def is_reader(user, church) -> bool:
    """``user`` holds the church role of pastor in ``church`` today."""
    return can_approve(user, church)


def readers(church, *, apart_from=None) -> list:
    """The accounts that read the church's requests today, by name. ``apart_from``: the writer's own
    account, which never reads its own request as a pastor."""
    rows = UserChurchRole.objects.filter(church=church, role=Role.PASTOR, user__is_active=True).select_related("user")
    users = [row.user for row in rows if apart_from is None or row.user_id != getattr(apart_from, "pk", None)]
    full = {
        person.user_id: person.full_name
        for person in Person.objects.filter(church=church, user__in=users, merged_into__isnull=True)
    }
    for user in users:
        # A name, never an address: the form shows this to every adult of the church.
        user.reader_name = full.get(user.pk) or user.get_full_name().strip() or NAMELESS
    return sorted(users, key=lambda user: (user.reader_name.casefold(), user.pk))


def known_minor(person: Person) -> bool:
    """Whether anything the church holds says ``person`` is under 18.

    The date of birth decides when there is one, typed or verified (the age on that date, as
    ``agenda.services.age_on`` counts it). Without one, the person is treated as an adult only when
    no record marks them as a minor: a camp they went to under the event's adult age, a door of an
    event where the team said "es menor de edad", or a guardian's authorization on record."""
    from agenda.models import EventRegistration, GuardianConsent
    from agenda.services import age_on
    from people.services import was_minor_at_an_event

    if person.birth_date is not None:
        return age_on(person.birth_date, timezone.localdate()) < 18
    return (
        was_minor_at_an_event(person)
        or EventRegistration.objects.filter(person=person, guest_is_minor=True).exists()
        or GuardianConsent.objects.filter(registration__person=person).exists()
    )


def may_write(person: Person | None) -> bool:
    """A person of the church with an account, not joined into another record and not a known minor."""
    return bool(person is not None and person.user_id and not person.merged_into_id and not known_minor(person))


def readable(user, church):
    """The requests of ``church`` that ``user`` may read now: none unless they are one of its pastors;
    then every request but their own and the ones somebody addressed to another pastor alone."""
    rows = PrayerRequest.objects.filter(church=church)
    if church is None or not is_reader(user, church):
        return rows.none()
    return alive(rows).filter(Q(restricted=False) | Q(only_reader=user), author__isnull=False).exclude(author=user)


def reader_of(prayer: PrayerRequest):
    """Who can read ``prayer`` today, as ``readers`` returns them (empty: nobody)."""
    everybody = readers(prayer.church, apart_from=prayer.author)
    if not prayer.restricted:
        return everybody
    return [user for user in everybody if user.pk == prayer.only_reader_id]
