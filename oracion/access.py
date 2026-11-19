"""Who writes a prayer request and who reads one. Asked on every request: nothing here is cached.

Readers are the people who hold the church role of **pastor** in that church *now*
(``resources.approvals.can_approve``, the same question the pastoral approval asks). Losing the role
ends the access at the next request. The administration, a guide of a group, the owner of a
follow-up, a pastor of another church and a superuser are not readers: no rule here looks at
``is_superuser`` or ``is_staff``, and ``has_scope`` is never asked, because no scope grants this.
"""

from __future__ import annotations

from django.db.models import Q
from django.utils import timezone

from core.models import Role, UserChurchRole
from people.models import Person
from resources.approvals import can_approve

from .models import PrayerRequest

NAMELESS = "Una persona con el rol de pastor"


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
    return rows.filter(Q(restricted=False) | Q(only_reader=user)).exclude(person__user=user)


def reader_of(prayer: PrayerRequest):
    """Who can read ``prayer`` today, as ``readers`` returns them (empty: nobody)."""
    writer = prayer.person.user if prayer.person.user_id else None
    everybody = readers(prayer.church, apart_from=writer)
    if not prayer.restricted:
        return everybody
    return [user for user in everybody if user.pk == prayer.only_reader_id]
