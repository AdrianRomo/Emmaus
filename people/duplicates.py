"""Possible duplicates among the people of ONE church: suggestions for its administration, nothing more.

Three conservative signals, each an exact match after a plain normalisation: the same e-mail, the
same phone, or the same full name together with the same date of birth. No score, no "similar"
names, nothing across churches, nothing in the background: this module only reads, and a person
decides (``people.merge``). A pair somebody dismissed ("No son la misma persona") is not suggested
again while its ``DuplicateDismissal`` exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations

from .models import DuplicateDismissal, Person

EMAIL, PHONE, NAME_BIRTH = "email", "phone", "name_birth"
SIGNAL_LABELS = {
    EMAIL: "Tienen el mismo correo",
    PHONE: "Tienen el mismo teléfono",
    NAME_BIRTH: "Tienen el mismo nombre y la misma fecha de nacimiento",
}
# A value shared by more records than this (the church's own phone on forty people) says nothing about any pair.
MAX_SHARED = 8
MIN_PHONE_DIGITS = 7


def normal_email(value: str) -> str:
    return (value or "").strip().lower()


def normal_phone(value: str) -> str:
    """Digits only, and the last ten of them: "+52 55 1111 2222" and "5511112222" are one phone.
    Fewer than seven digits is not a phone to compare."""
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= MIN_PHONE_DIGITS else ""


def keys_of(person) -> set[tuple[str, str]]:
    """The values a person can share with another, as (signal, value). ``person`` is a ``Person`` or a row of values."""
    get = person.get if isinstance(person, dict) else lambda name: getattr(person, name)
    keys = set()
    if normal_email(get("email")):
        keys.add((EMAIL, normal_email(get("email"))))
    if normal_phone(get("phone")):
        keys.add((PHONE, normal_phone(get("phone"))))
    if get("normalized_name") and get("birth_date") is not None:
        keys.add((NAME_BIRTH, f"{get('normalized_name')}|{get('birth_date').isoformat()}"))
    return keys


def signals_between(a: Person, b: Person) -> list[str]:
    """Which signals join two people, in a fixed order. Empty: nothing suggests they are the same."""
    shared = {signal for signal, _ in keys_of(a) & keys_of(b)}
    return [signal for signal in SIGNAL_LABELS if signal in shared]


def ordered(a_id: int, b_id: int) -> tuple[int, int]:
    return (a_id, b_id) if a_id < b_id else (b_id, a_id)


def is_dismissed(church, a_id: int, b_id: int) -> bool:
    low, high = ordered(a_id, b_id)
    return DuplicateDismissal.objects.filter(church=church, person_a_id=low, person_b_id=high).exists()


@dataclass
class Pair:
    a: Person  # the lower id
    b: Person
    signals: list[str]

    @property
    def reasons(self) -> list[str]:
        return [SIGNAL_LABELS[signal] for signal in self.signals]


def suggestions(church) -> list[Pair]:
    """Every pair of active people of ``church`` that shares a signal and was not dismissed. Three queries."""
    rows = list(
        Person.objects.filter(church=church, merged_into__isnull=True)
        .values("pk", "email", "phone", "normalized_name", "birth_date")
    )
    buckets: dict[tuple[str, str], list[int]] = {}
    for row in rows:
        for key in keys_of(row):
            buckets.setdefault(key, []).append(row["pk"])
    found: dict[tuple[int, int], set[str]] = {}
    for (signal, _), ids in buckets.items():
        if 2 <= len(ids) <= MAX_SHARED:
            for a_id, b_id in combinations(sorted(ids), 2):
                found.setdefault((a_id, b_id), set()).add(signal)
    if not found:
        return []
    dismissed = set(DuplicateDismissal.objects.filter(church=church).values_list("person_a_id", "person_b_id"))
    wanted = {pk for pair in found if pair not in dismissed for pk in pair}
    people = {person.pk: person for person in Person.objects.filter(church=church, pk__in=wanted).select_related("user")}
    pairs = [
        Pair(people[a_id], people[b_id], [signal for signal in SIGNAL_LABELS if signal in signals])
        for (a_id, b_id), signals in found.items()
        if (a_id, b_id) not in dismissed and a_id in people and b_id in people
    ]
    pairs.sort(key=lambda pair: (pair.a.normalized_name, pair.a.pk, pair.b.normalized_name, pair.b.pk))
    return pairs
