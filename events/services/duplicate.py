"""Duplicate an event as a template for the next one (issue #51): its configuration, never its people's data.

A camp is copied here; a simple event of the church in ``agenda/duplicate.py``. The copy is always of the same type
as the original (decision 0015: a simple event and a camp do not convert into each other).

What is copied is decided here, relation by relation and field by field. ``RELATIONS`` names every model that points
at ``Event`` and ``COPIED_FIELDS``/``FRESH_FIELDS`` every column of ``Event``: the tests compare both with the models,
so a table or a column added later fails them until somebody decides whether the next camp inherits it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from django.db import transaction

from ..models import Event, EventMembership, InterviewStation, MembershipAlias, TutorAssignment
from . import audit
from .event_questionnaires import links_for, use_version

COPIED = "copied"  # always: it is configuration
WITH_TEAM = "with_team"  # only when the administrator asks for the team
NEVER = "never"  # people's data, or the history of the original: the copy starts without any
OWN = "own"  # not copied, but the copy writes its own first rows (the audit of its creation)

# Every relation that points at Event, by its accessor (Event._meta.related_objects).
RELATIONS = {
    "questionnaire_links": COPIED,  # the versions in use, in the same order (the versions belong to the church)
    "stations": COPIED,
    "memberships": WITH_TEAM,  # without confidential access; each person is told again
    "tutor_assignments": WITH_TEAM,
    "membership_aliases": WITH_TEAM,  # the names the church's lists use for each coordinator (issue #50)
    "access_requests": NEVER,
    "participants": NEVER,  # camperos; with them go consents, sessions and person links
    "interviews": NEVER,  # with them go answers and their revisions
    "follow_ups": NEVER,
    "snapshots": NEVER,  # cutoffs and reports
    "audit_events": OWN,
    "person_links": NEVER,
    "page": NEVER,  # a camp's page for the church is written again: it says dates and prices
    "registrations": NEVER,
    "faqs": NEVER,
    "schedule_items": NEVER,
    "feedback": NEVER,
    "feedback_given": NEVER,
}

# Event's columns. Copied: what the administration set up. Fresh: what belongs to one event only.
COPIED_FIELDS = (
    "church", "kind", "location", "time_zone", "camp_tools", "questionnaires_in_order", "confidential_level",
    "reports_enabled", "report_interval_minutes", "report_window_start", "report_window_end", "min_group_size",
    "adult_age", "requires_guardian_authorization", "privacy_notice", "safeguarding_instructions", "retention_days",
)
FRESH_FIELDS = (
    "id", "name", "slug", "status", "starts_on", "ends_on",  # asked, or "En preparación"
    "join_code", "join_by_name",  # a new sign: the old code never opens the new event
    "procedures_approved_at", "procedures_approved_by",  # the church confirms its procedures for each event
    "data_purged_at", "created_by", "created_at", "updated_at",
)
COPIED_M2M = ("sedes",)

_YEAR = re.compile(r"(?<!\d)(19|20)\d\d(?!\d)")


def suggest_name(name: str) -> str:
    """"Campamento 2026" -> "Campamento 2027"; without a year, "Campamento (copia)"."""
    years = list(_YEAR.finditer(name))
    if years:
        last = years[-1]
        return f"{name[: last.start()]}{int(last.group(0)) + 1}{name[last.end():]}"
    return f"{name} (copia)"


@dataclass
class Duplicated:
    event: Event
    stations: int = 0
    questionnaires: int = 0
    members: list[EventMembership] = field(default_factory=list)  # with copy_team: who to write to


@transaction.atomic
def duplicate_camp(source: Event, actor, *, name: str, slug: str, starts_on=None, ends_on=None,
                   copy_team: bool = False) -> Duplicated:
    """A new camp of the same church with ``source``'s configuration, "En preparación", with its own address and
    join code. No campero, interview, answer, follow-up, consent, cutoff, report or audit row comes along.

    With ``copy_team`` the people with active access to ``source`` get the same role in the copy, without
    confidential access (somebody grants it again, and it is recorded). Nothing of their old invitation is reused:
    the caller sends each one a new message (``Duplicated.members``)."""
    if not source.camp_tools:
        raise ValueError("Un evento sencillo se duplica desde Eventos de la iglesia.")
    copy = Event(**{name_: getattr(source, name_) for name_ in COPIED_FIELDS})
    copy.name, copy.slug = name, slug
    copy.starts_on, copy.ends_on = starts_on, ends_on
    copy.status = Event.Status.DRAFT
    copy.created_by = actor
    copy.save()  # draws its own join code
    copy.sedes.set(source.sedes.all())

    result = Duplicated(event=copy)
    for link in links_for(source):
        use_version(copy, link.version, actor=actor)
        result.questionnaires += 1
    stations = [
        InterviewStation(event=copy, number=station.number, name=station.name, is_active=station.is_active)
        for station in source.stations.all()
    ]
    InterviewStation.objects.bulk_create(stations)
    result.stations = len(stations)

    audit.record(actor, copy, "evento_creado", f"Creó el evento {copy.name} como copia de {source.name}", copy,
                 source_event=source.pk)
    audit.record(actor, source, "evento_duplicado", f"Duplicó este evento como plantilla: {copy.name}", copy,
                 with_team=bool(copy_team))
    if copy_team:
        result.members = _copy_team(source, copy, actor)
    return result


def _copy_team(source: Event, copy: Event, actor) -> list[EventMembership]:
    """The same accounts with the same role and team name, each one recorded like "Agregar persona" records it; then
    who supervises whom. By account, never by e-mail: two accounts may share an address."""
    added: dict[int, EventMembership] = {}
    memberships = EventMembership.objects.filter(event=source, is_active=True, user__is_active=True)
    for membership in memberships.select_related("user").order_by("pk"):
        new = EventMembership.objects.create(
            event=copy, user=membership.user, role=membership.role, team_name=membership.team_name,
            can_view_sensitive=False, created_by=actor,
        )
        audit.record(actor, copy, "integrante_agregado",
                     f"Agregó a {new.display_name} como {new.get_role_display().lower()}", new, copiado=True)
        added[membership.pk] = new
    for assignment in TutorAssignment.objects.filter(event=source):
        tutor, coordinator = added.get(assignment.tutor_id), added.get(assignment.coordinator_id)
        if tutor and coordinator:
            TutorAssignment.objects.create(event=copy, tutor=tutor, coordinator=coordinator, created_by=actor)
    # The names the church's lists use for each coordinator (issue #50) go with the team: the next camp's list
    # is written by the same people. The audit row of each copied member already says "copiado".
    for alias in MembershipAlias.objects.filter(event=source, membership_id__in=added):
        MembershipAlias.objects.create(event=copy, membership=added[alias.membership_id], alias=alias.alias, created_by=actor)
    return list(added.values())
