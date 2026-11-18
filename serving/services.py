"""Servir: the church's volunteer teams, their dates, who is asked to serve and what they answer.

Each write is one transaction that changes the rows, the ``access.RoleAssignment`` row derived
from a membership (decisions 0003 and 0016) and stores one church audit row with ids and fixed
words only. Callers have already answered who may ask (``serving.views``, through
``core.authz.has_scope``); these functions enforce the rules that hold whoever asks, and raise
``ServingError`` with a sentence ready for the screen.

Who coordinates a team is answered by the projection: scope ``ministry``, the team's id, the role
"coordina", in force while the membership is active and the team is not archived. Nothing here
creates a ``UserChurchRole``, a group membership or an event membership: being in a team gives
nothing outside that team.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import OuterRef
from django.utils import timezone

from access.models import RoleAssignment, ScopeType
from access.sources import account_changed
from people.audit import record_church

from .models import Assignment, ServiceDate, Team, TeamMembership, Unavailable

SOURCE = "serving.TeamMembership"

ACTIVE = TeamMembership.Status.ACTIVE
COORDINATOR = TeamMembership.Role.COORDINATOR
SERVES = TeamMembership.Role.SERVES
PENDING = Assignment.Status.PENDING
ACCEPTED = Assignment.Status.ACCEPTED
DECLINED = Assignment.Status.DECLINED

ARCHIVED = "Este equipo está archivado."
PAST = "Esa fecha ya pasó: ya no se puede cambiar."
TOO_LATE = "Tu respuesta se puede cambiar hasta un día antes. Si ya no puedes servir, avisa a quien coordina el equipo."


class ServingError(ValidationError):
    """A rule of the team refused the action; the message is ready for the screen."""


# ---- the projection -----------------------------------------------------------------------------
def _assignment_fields(membership: TeamMembership) -> dict:
    """The role row a membership stands for. It grants while the membership is active and the team
    is not archived: nobody keeps coordinating a team that stopped serving."""
    in_force = membership.status == ACTIVE and membership.team.is_active
    return {
        "subject_user_id": membership.person.user_id,
        "church_id": membership.team.church_id,
        "scope_type": ScopeType.MINISTRY,
        "scope_id": membership.team_id,
        "role": membership.role,
        "status": RoleAssignment.Status.ACTIVE if in_force else RoleAssignment.Status.INACTIVE,
        "start_at": membership.created_at,
        "end_at": None,
    }


def sync_assignment(membership: TeamMembership) -> RoleAssignment | None:
    """Write (or delete) the role row derived from ``membership``. A person without an account
    holds nothing: there is nobody to sign in as them.

    The row is derived from the membership, its team and the person's account as they are in the
    database now, read under a lock on the membership: never from what the caller loaded earlier,
    which a concurrent service or a link of the account may have changed since. Callers hold the
    team's lock first (the services do; ``sync_person_assignments`` takes it), so the order is
    always team, then membership."""
    source_id = membership.pk
    with transaction.atomic():
        membership = (
            TeamMembership.objects.select_for_update(of=("self",)).select_related("team", "person")
            .filter(pk=source_id).first()
        )
        if membership is None or membership.person.user_id is None:
            RoleAssignment.objects.filter(source_table=SOURCE, source_id=source_id).delete()
            return None
        row, _ = RoleAssignment.objects.update_or_create(
            source_table=SOURCE, source_id=source_id, defaults=_assignment_fields(membership)
        )
    return row


def sync_person_assignments(sender, instance, created=False, raw=False, **kwargs) -> None:
    """``post_save`` of ``Person``, only when its account was linked, unlinked or replaced. It takes
    the locks the services take, in their order (each team, then its membership), so it can never
    write back a role that ``remove_member``, ``set_member`` or ``archive_team`` is ending."""
    if not account_changed(instance, created, raw):
        return
    with transaction.atomic():
        team_ids = sorted(set(TeamMembership.objects.filter(person=instance).values_list("team_id", flat=True)))
        list(Team.objects.select_for_update().filter(pk__in=team_ids).order_by("pk"))
        for membership in TeamMembership.objects.filter(person=instance, team_id__in=team_ids).order_by("team_id", "pk"):
            sync_assignment(membership)


def live_memberships():
    """``access.sources``: the membership a role row came from, when it still grants what the row says."""
    return TeamMembership.objects.filter(
        pk=OuterRef("source_id"), team_id=OuterRef("scope_id"), team__church_id=OuterRef("church_id"),
        team__status=Team.Status.ACTIVE, status=ACTIVE, role=OuterRef("role"), person__user_id=OuterRef("subject_user_id"),
    )


def expected_assignments() -> dict[int, dict]:
    """What the projection must hold, keyed by membership id (``rebuild_role_assignments``)."""
    memberships = TeamMembership.objects.filter(person__user__isnull=False).select_related("team", "person")
    return {membership.pk: _assignment_fields(membership) for membership in memberships}


# ---- what a person sees -------------------------------------------------------------------------
def _upcoming(person, today=None):
    """The person's assignments from today on in teams that are still active, by date."""
    today = today or timezone.localdate()
    return (
        Assignment.objects.filter(person=person, service_date__date__gte=today, service_date__team__status=Team.Status.ACTIVE)
        .select_related("service_date", "service_date__team")
        .order_by("service_date__date", "service_date__starts_at", "id")
    )


def my_dates(person, today=None, limit: int = 5) -> list[Assignment]:
    """The person's assignments from today on that are not declined, by date."""
    return list(_upcoming(person, today).exclude(status=DECLINED)[:limit])


def all_my_dates(person, today=None) -> list[Assignment]:
    """Every assignment from today on, the declined ones too: "Tus fechas", where an answer can change."""
    return list(_upcoming(person, today))


def pending_count(person, today=None) -> int:
    """How many dates from today on still wait for this person's answer."""
    return _upcoming(person, today).filter(status=PENDING).count()


def has_teams(person) -> bool:
    """The person is an active member of any active team."""
    return TeamMembership.objects.filter(person=person, status=ACTIVE, team__status=Team.Status.ACTIVE).exists()


def coordinator_names(team_ids) -> dict[int, list[str]]:
    """Who coordinates each team, by name only: what anybody in the team may read."""
    names: dict[int, list[str]] = {}
    rows = TeamMembership.objects.filter(team_id__in=list(team_ids), status=ACTIVE, role=COORDINATOR)
    for membership in rows.select_related("person").order_by("person__first_name", "person__last_name", "id"):
        names.setdefault(membership.team_id, []).append(membership.person.full_name)
    return names


def unavailable_ids(person_ids, day) -> set[int]:
    """Which of these people marked ``day`` as a day they cannot serve."""
    return set(Unavailable.objects.filter(person_id__in=list(person_ids), date=day).values_list("person_id", flat=True))


def assigned_on(person, day) -> bool:
    """The person is asked to serve on ``day`` and has not said no."""
    return (
        Assignment.objects.filter(person=person, service_date__date=day, service_date__team__status=Team.Status.ACTIVE)
        .exclude(status=DECLINED)
        .exists()
    )


# ---- teams (the administration) -----------------------------------------------------------------
def save_team(church, actor, *, team: Team | None = None, name: str, description: str = "") -> Team:
    """Create a team of ``church`` or change its name and description."""
    name, description = (name or "").strip(), (description or "").strip()
    if not name:
        raise ServingError("Escribe el nombre del equipo.", code="no_name")
    with transaction.atomic():
        if team is None:
            team = Team.objects.create(church=church, name=name, description=description, created_by=actor)
            record_church(church, actor, "equipo_creado", f"Creó el equipo {team.name}", team)
            return team
        team = Team.objects.select_for_update().get(pk=team.pk, church=church)
        team.name, team.description = name, description
        team.save(update_fields=["name", "description", "updated_at"])
        record_church(church, actor, "equipo_modificado", f"Modificó el equipo {team.name}", team)
    return team


def archive_team(team: Team, actor) -> Team:
    """The team stops serving: its dates leave everybody's "Tus fechas". Nothing is deleted."""
    with transaction.atomic():
        team = Team.objects.select_for_update().get(pk=team.pk)
        if not team.is_active:
            return team
        team.status = Team.Status.ARCHIVED
        team.save(update_fields=["status", "updated_at"])
        for membership in TeamMembership.objects.filter(team=team).order_by("pk"):
            sync_assignment(membership)  # every role in the team ends with it
        record_church(team.church, actor, "equipo_archivado", f"Archivó el equipo {team.name}", team)
    return team


def set_member(team: Team, person, actor, role: str = SERVES) -> TeamMembership:
    """Put ``person`` in ``team`` with ``role``, or change the role of who is already in."""
    if role not in TeamMembership.Role.values:
        raise ValueError(f"Unknown team role {role!r}.")
    if person.church_id != team.church_id or person.merged_into_id is not None:
        raise ServingError("Esta persona no es de la iglesia del equipo.", code="other_church")
    with transaction.atomic():
        team = Team.objects.select_for_update().get(pk=team.pk)
        if not team.is_active:
            raise ServingError(ARCHIVED, code="archived")
        membership = TeamMembership.objects.select_for_update().filter(team=team, person=person).first()
        if membership is not None and membership.status == ACTIVE and membership.role == role:
            return membership
        if membership is None:
            membership = TeamMembership(team=team, person=person)
        membership.role, membership.status = role, ACTIVE
        membership.save()
        sync_assignment(membership)
        words = "coordina" if role == COORDINATOR else "sirve en"
        record_church(team.church, actor, "equipo_integrante", f"{person.full_name} ahora {words} el equipo {team.name}",
                      team, person_id=person.pk, role=role)
    return membership


def remove_member(team: Team, person, actor, today=None) -> None:
    """Take ``person`` out of ``team``. The dates from today on stop counting on them; what
    already happened stays as it was."""
    today = today or timezone.localdate()
    with transaction.atomic():
        team = Team.objects.select_for_update().get(pk=team.pk)  # with assign(): no date is given to who is leaving
        membership = TeamMembership.objects.select_for_update().filter(team=team, person=person, status=ACTIVE).first()
        if membership is None:
            return
        membership.team = team
        membership.status = TeamMembership.Status.INACTIVE
        membership.save(update_fields=["status", "updated_at"])
        sync_assignment(membership)
        Assignment.objects.filter(person=person, service_date__team=team, service_date__date__gte=today).delete()
        record_church(team.church, actor, "equipo_integrante", f"Quitó a {person.full_name} del equipo {team.name}",
                      team, person_id=person.pk, role="ninguno")


# ---- dates (who coordinates) --------------------------------------------------------------------
def _open_date(service_date: ServiceDate, today=None) -> ServiceDate:
    """The date, locked, when it can still change: its team is active and the day has not passed."""
    today = today or timezone.localdate()
    locked = ServiceDate.objects.select_for_update().get(pk=service_date.pk)
    team = Team.objects.get(pk=locked.team_id)
    if not team.is_active:
        raise ServingError(ARCHIVED, code="archived")
    if locked.date < today:
        raise ServingError(PAST, code="past")
    locked.team = team
    return locked


def save_date(team: Team, actor, *, service_date: ServiceDate | None = None, date, what: str, starts_at=None,
              place: str = "", today=None) -> ServiceDate:
    """Create a date of ``team`` or change one, from today on only. Moving a date to another day
    asks everybody again: an answer was given for the day it had."""
    today = today or timezone.localdate()
    what, place = (what or "").strip(), (place or "").strip()
    if not what:
        raise ServingError("Escribe qué se necesita ese día.", code="no_what")
    if date < today:
        raise ServingError("Elige un día de hoy en adelante.", code="past")
    with transaction.atomic():
        if service_date is None:
            if not Team.objects.select_for_update().get(pk=team.pk).is_active:
                raise ServingError(ARCHIVED, code="archived")
            service_date = ServiceDate.objects.create(team=team, date=date, starts_at=starts_at, what=what, place=place, created_by=actor)
            record_church(team.church, actor, "equipo_fecha", f"Agregó una fecha al equipo {team.name}", service_date)
            return service_date
        service_date = _open_date(service_date, today)
        moved = service_date.date != date
        if moved:
            asked = Assignment.objects.filter(service_date=service_date)
            counted_on = list(asked.exclude(status=DECLINED).values_list("person_id", flat=True))
            blocked = Unavailable.objects.filter(person_id__in=counted_on, date=date).select_related("person").first()
            if blocked is not None:
                raise ServingError(
                    f"{blocked.person.first_name} marcó que ese día no puede. Quítale la fecha antes de cambiar el día.",
                    code="unavailable",
                )
            # Everybody is asked again for the new day, except who said no and cannot that day either.
            cannot = Unavailable.objects.filter(date=date).values_list("person_id", flat=True)
            asked.exclude(person_id__in=cannot).update(status=PENDING, responded_at=None)
        service_date.date, service_date.starts_at, service_date.what, service_date.place = date, starts_at, what, place
        service_date.save(update_fields=["date", "starts_at", "what", "place", "updated_at"])
        record_church(service_date.team.church, actor, "equipo_fecha", f"Modificó una fecha del equipo {service_date.team.name}", service_date)
    return service_date


def delete_date(service_date: ServiceDate, actor, today=None) -> None:
    """Delete a date that has not passed, with the answers it had."""
    with transaction.atomic():
        service_date = _open_date(service_date, today)
        team = service_date.team
        record_church(team.church, actor, "equipo_fecha", f"Borró una fecha del equipo {team.name}", service_date)
        service_date.delete()


def assign(service_date: ServiceDate, person, actor, today=None) -> Assignment:
    """Ask ``person`` to serve on ``service_date``. Only active members of the team; never
    somebody who marked that day as one they cannot serve."""
    with transaction.atomic():
        service_date = _open_date(service_date, today)
        team = service_date.team
        Team.objects.select_for_update().get(pk=team.pk)  # with remove_member()
        if not TeamMembership.objects.filter(team=team, person=person, status=ACTIVE).exists():
            raise ServingError(f"{person.first_name} no es parte del equipo {team.name}.", code="not_member")
        existing = Assignment.objects.select_for_update().filter(service_date=service_date, person=person).first()
        if existing is not None:
            return existing
        if Unavailable.objects.filter(person=person, date=service_date.date).exists():
            raise ServingError(f"{person.first_name} marcó que ese día no puede.", code="unavailable")
        assignment = Assignment.objects.create(service_date=service_date, person=person)
        record_church(team.church, actor, "equipo_asignacion", f"Pidió a {person.full_name} servir en una fecha del equipo {team.name}",
                      service_date, person_id=person.pk)
    return assignment


def unassign(service_date: ServiceDate, person, actor, today=None) -> None:
    """The date no longer counts on ``person``; their answer goes with it."""
    with transaction.atomic():
        service_date = _open_date(service_date, today)
        deleted, _ = Assignment.objects.filter(service_date=service_date, person=person).delete()
        if deleted:
            team = service_date.team
            record_church(team.church, actor, "equipo_asignacion", f"Quitó a {person.full_name} de una fecha del equipo {team.name}",
                          service_date, person_id=person.pk)


# ---- the person's writes ------------------------------------------------------------------------
def respond(assignment: Assignment, accepted: bool, actor, today=None) -> Assignment:
    """The person says whether they can serve. A first answer is taken until the day itself; an
    answer already given can change until the day before."""
    today = today or timezone.localdate()
    wanted = ACCEPTED if accepted else DECLINED
    with transaction.atomic():
        assignment = Assignment.objects.select_for_update().get(pk=assignment.pk)
        service_date = ServiceDate.objects.select_related("team", "team__church").get(pk=assignment.service_date_id)
        team = service_date.team
        if not team.is_active:
            raise ServingError(ARCHIVED, code="archived")
        if service_date.date < today:
            raise ServingError(PAST, code="past")
        if assignment.status == wanted:
            return assignment
        if assignment.status != PENDING and service_date.date <= today:
            raise ServingError(TOO_LATE, code="too_late")
        assignment.status, assignment.responded_at = wanted, timezone.now()
        assignment.save(update_fields=["status", "responded_at"])
        person = assignment.person
        words = "confirmó" if accepted else "dijo que no puede en"
        record_church(team.church, actor, "equipo_respuesta", f"{person.full_name} {words} una fecha del equipo {team.name}",
                      service_date, person_id=person.pk)
    return assignment


def set_unavailable(person, day, actor, today=None) -> Unavailable:
    """"Ese día no puedo", for every team at once. A date the person is already asked for stays
    as it is: they decline it themselves."""
    today = today or timezone.localdate()
    if day < today:
        raise ServingError("Elige un día de hoy en adelante.", code="past")
    with transaction.atomic():
        row, created = Unavailable.objects.get_or_create(person=person, date=day)
        if created:
            record_church(person.church, actor, "equipo_respuesta", f"{person.full_name} marcó un día que no puede servir",
                          row, person_id=person.pk)
    return row


def clear_unavailable(unavailable: Unavailable, actor) -> None:
    """The person can serve that day after all."""
    with transaction.atomic():
        row = Unavailable.objects.select_for_update().filter(pk=unavailable.pk).first()
        if row is None:
            return
        person = row.person
        record_church(person.church, actor, "equipo_respuesta", f"{person.full_name} quitó un día que no podía servir",
                      row, person_id=person.pk)
        row.delete()
