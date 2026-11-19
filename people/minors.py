"""Whether the church's records say a person is under 18: one rule, asked on every request.

It began in ``oracion.access`` (which still exports it) and is shared by the forms, the prayer
requests and the directory between members. ``exclude_known_minors`` is the same rule for a whole
list, written as a filter so that a directory page costs no query per row; a test holds the two
against each other for every case.
"""

from __future__ import annotations

from datetime import date

from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from .models import EventParticipantLink, Person


def known_minor(person: Person) -> bool:
    """Whether anything the church holds says ``person`` is under 18.

    The date of birth decides when there is one, typed or verified (the age on that date, as
    ``agenda.services.age_on`` counts it). Without one, the person is treated as an adult only when
    no record marks them as a minor: a camp they went to under the event's adult age, a door of an
    event where the team said "es menor de edad", or a guardian's authorization on record."""
    from agenda.models import EventRegistration, GuardianConsent
    from agenda.services import age_on

    from .services import was_minor_at_an_event

    if person.birth_date is not None:
        return age_on(person.birth_date, timezone.localdate()) < 18
    return (
        was_minor_at_an_event(person)
        or EventRegistration.objects.filter(person=person, guest_is_minor=True).exists()
        or GuardianConsent.objects.filter(registration__person=person).exists()
    )


def _eighteen_years_before(day: date) -> date:
    """The latest date of birth of somebody who is 18 on ``day`` (29 February counts from 1 March, as ``age_on`` does)."""
    try:
        return day.replace(year=day.year - 18)
    except ValueError:  # ``day`` is 29 February and the year 18 before has none
        return day.replace(year=day.year - 18, day=28)


def exclude_known_minors(rows, person: str = "person"):
    """``rows`` (a queryset with a relation ``person`` to ``Person``; ``""`` for people themselves)
    without anybody ``known_minor`` would answer yes for. Read at the moment of the query: a date of
    birth corrected a minute ago, or a registration marked "es menor" at a door, takes the person out."""
    from agenda.models import EventRegistration, GuardianConsent

    prefix = f"{person}__" if person else ""
    key = OuterRef(f"{prefix}pk")
    adult_by_date = Q(**{f"{prefix}birth_date__lte": _eighteen_years_before(timezone.localdate())})
    no_date = Q(**{f"{prefix}birth_date__isnull": True})
    marked = (
        Exists(EventParticipantLink.objects.filter(person=key, participant__age__lt=F("event__adult_age")))
        | Exists(EventRegistration.objects.filter(person=key, guest_is_minor=True))
        | Exists(GuardianConsent.objects.filter(registration__person=key))
    )
    return rows.filter(adult_by_date | (no_date & ~marked))
