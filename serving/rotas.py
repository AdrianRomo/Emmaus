"""Servir, second part: roles and what a date needs, the rotation, swaps and the order of a date.

Like ``serving.services``: every write is one transaction with one church audit row of ids and
fixed words, the callers have already answered who may ask (``serving.rota_views``, through
``core.authz.has_scope``), and a refused rule raises ``ServingError`` with a sentence for the screen.

One rule decides who can take a role on a date, for the rotation, for a swap and for the lists the
screens offer (``excluded``). Somebody is left out when they:

* are not an active member of the team, or their record was joined to another (``merged_into``);
* marked that day as one they cannot serve;
* were already asked for that date, whatever they answered;
* serve that same day on another date, of any team, and have not said no;
* are not an adult by a date of birth the church verified, when the role is for adults only. An
  unknown age counts as not adult there.

Locks, always in this order (``serving.services`` says it for every writer of Servir): the team,
the date, the assignment, the swap request. Every swap writer holds the team, so taking somebody
out of the team (``services.remove_member``) can never run between "can they take it?" and the
date changing hands.

Eligibility is evaluated when it is READ, for anything in the future: an assignment made while
somebody could take a role says nothing once the role became "solo personas adultas" or their date
of birth was corrected. ``unfit`` flags it for who coordinates, it stops counting towards what the
date needs, and an order never names somebody who can no longer be named (``plan_items``).

Moments are compared as instants: a date with an hour starts at that hour, a date without one lasts
its whole day. "Coming" is "starts after now".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from people.audit import record_church
from people.models import Person

from . import services
from .models import (
    Assignment, PlanItem, RoleNeed, RotationEntry, ServiceDate, ServiceReminder, SwapRequest, Team, TeamMembership, TeamRole,
)
from .services import ACCEPTED, ACTIVE, ARCHIVED, DECLINED, ServingError, is_adult_on, known_minor_on

HORIZON_WEEKS = 8  # as the series of events
MAX_EVERY_WEEKS = 12
MAX_NEED = 20
SWAP_DAYS = 7
MAX_PLAN_ITEMS = 40
MAX_MINUTES = 300
Swap = SwapRequest.Status

GONE = "Esta solicitud ya no está vigente."
NOT_COMING = "Esa fecha ya empezó o ya pasó."

# Why somebody cannot take a date, as the person who is choosing reads it.
REASONS = {
    "not_member": "ya no es parte del equipo",
    "unavailable": "marcó que ese día no puede",
    "asked": "ya tiene esa fecha",
    "busy": "ya sirve ese día en otra fecha",
    "adults_only": "el rol es solo para personas adultas y la iglesia no ha confirmado su edad",
}


class StaleProposal(Exception):
    """What was confirmed is not what the rotation would propose now: show it again."""


# ---- moments ------------------------------------------------------------------------------------
def start_instant(service_date: ServiceDate) -> datetime:
    """When the date stops being "coming": its hour, or the end of its day when it has none."""
    if service_date.starts_at is not None:
        naive = datetime.combine(service_date.date, service_date.starts_at)
    else:
        naive = datetime.combine(service_date.date + timedelta(days=1), time.min)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def is_coming(service_date: ServiceDate, now=None) -> bool:
    return start_instant(service_date) > (now or timezone.now())


# ---- who can take a role on a date --------------------------------------------------------------
def excluded(service_date: ServiceDate, role: TeamRole | None, person_ids) -> dict[int, str]:
    """For each of ``person_ids`` that cannot take ``role`` on ``service_date``: the first reason
    (a key of ``REASONS``). Who is not in the answer can."""
    ids = list(person_ids)
    day = service_date.date
    members = dict(
        TeamMembership.objects.filter(team_id=service_date.team_id, status=ACTIVE, person_id__in=ids, person__merged_into__isnull=True)
        .values_list("person_id", "pk")
    )
    cannot = services.unavailable_ids(ids, day)
    asked = set(Assignment.objects.filter(service_date=service_date, person_id__in=ids).values_list("person_id", flat=True))
    busy = busy_elsewhere(ids, day, service_date.pk)
    not_adult: set[int] = set()
    if role is not None and role.adults_only:
        not_adult = {person.pk for person in Person.objects.filter(pk__in=ids) if not is_adult_on(person, day)}
    out: dict[int, str] = {}
    for pk in ids:
        if pk not in members:
            out[pk] = "not_member"
        elif pk in cannot:
            out[pk] = "unavailable"
        elif pk in asked:
            out[pk] = "asked"
        elif pk in busy:
            out[pk] = "busy"
        elif pk in not_adult:
            out[pk] = "adults_only"
    return out


def busy_elsewhere(person_ids, day, service_date_id=None) -> set[int]:
    """Who of ``person_ids`` serves on ``day`` on another date, of any active team, and has not said no."""
    rows = Assignment.objects.filter(
        person_id__in=list(person_ids), service_date__date=day, service_date__team__status=Team.Status.ACTIVE
    ).exclude(status=DECLINED)
    if service_date_id is not None:
        rows = rows.exclude(service_date_id=service_date_id)
    return set(rows.values_list("person_id", flat=True))


def unfit(assignment: Assignment) -> bool:
    """The person can no longer cover the role this assignment gave them, as things are now: the
    role is for adults and the church has not verified them as adult on that day, or their record
    was joined to another. Read every time, never stored. Needs ``person``, ``role`` and
    ``service_date`` loaded."""
    if assignment.person.merged_into_id is not None:
        return True
    role = assignment.role
    return role is not None and role.active and role.adults_only and not is_adult_on(assignment.person, assignment.service_date.date)


def _members(team: Team) -> list[Person]:
    """The active people of the team, by name. Never a record that was joined to another."""
    rows = TeamMembership.objects.filter(team=team, status=ACTIVE, person__merged_into__isnull=True).select_related("person")
    return [row.person for row in rows.order_by("person__first_name", "person__last_name", "id")]


# ---- roles and what a date needs ----------------------------------------------------------------
def _one_line(text: str, limit: int, empty: str) -> str:
    """Short text somebody typed that other people will read: one line, trimmed, within ``limit``."""
    text = (text or "").strip()
    if not text:
        raise ServingError(empty, code="empty")
    if text.splitlines() != [text]:
        raise ServingError("Escribe una sola línea.", code="lines")
    text = " ".join(text.split())
    if len(text) > limit:
        raise ServingError(f"Escribe {limit} caracteres o menos.", code="too_long")
    return text


def _active_team(team: Team) -> Team:
    team = Team.objects.select_for_update().get(pk=team.pk)
    if not team.is_active:
        raise ServingError(ARCHIVED, code="archived")
    return team


def active_roles(team: Team) -> list[TeamRole]:
    return list(TeamRole.objects.filter(team=team, active=True))


class RoleNeedsConfirmation(Exception):
    """Saving the role as "solo personas adultas" would leave these future assignments with a role
    their person cannot cover. ``token`` covers exactly those rows: confirm with it."""

    def __init__(self, affected: list[Assignment], token: str):
        super().__init__("confirm")
        self.affected, self.token = affected, token


def _holders_who_cannot(role: TeamRole, now) -> list[Assignment]:
    """The assignments of dates that are coming, not declined, that hold ``role`` and whose person
    is not an adult the church verified on that day."""
    today = timezone.localtime(now).date()
    rows = (
        Assignment.objects.filter(role=role, service_date__date__gte=today).exclude(status=DECLINED)
        .select_related("person", "service_date").order_by("service_date__date", "service_date__starts_at", "pk")
    )
    return [row for row in rows if is_coming(row.service_date, now) and not is_adult_on(row.person, row.service_date.date)]


def _release_token(role: TeamRole, affected: list[Assignment]) -> str:
    return hashlib.sha256(json.dumps([role.pk, [row.pk for row in affected]]).encode()).hexdigest()


def save_role(team: Team, actor, *, role: TeamRole | None = None, name: str, adults_only: bool = False,
              release_token: str = "", now=None) -> TeamRole:
    """Add a role to ``team`` or change one. A name that was retired comes back as it was.

    Making a role "solo personas adultas" while people the church has not verified as adults hold
    it on dates that are coming is not done silently: ``RoleNeedsConfirmation`` lists them, and
    saving again with its ``token`` keeps each of them on their date WITHOUT the role (the place
    is missing again, and the coordinator fills it). If the list changed meanwhile, it is shown again."""
    name = _one_line(name, 60, "Escribe el nombre del rol.")
    now = now or timezone.now()
    with transaction.atomic():
        team = _active_team(team)
        same = TeamRole.objects.select_for_update().filter(team=team, name__iexact=name).first()
        if role is None:
            if same is not None and same.active:
                raise ServingError("El equipo ya tiene un rol con ese nombre.", code="duplicate")
            role = same or TeamRole(team=team)
        else:
            role = TeamRole.objects.select_for_update().get(pk=role.pk, team=team)
            if same is not None and same.pk != role.pk:
                raise ServingError("El equipo ya tiene un rol con ese nombre.", code="duplicate")
        released = 0
        if adults_only and role.pk is not None:
            # Under the team's lock nobody assigns in this team: the list cannot grow behind us.
            affected = _holders_who_cannot(role, now)
            if affected:
                token = _release_token(role, affected)
                if release_token != token:
                    raise RoleNeedsConfirmation(affected, token)
                ids = services._lock_assignments(Assignment.objects.filter(pk__in=[row.pk for row in affected]))
                released = Assignment.objects.filter(pk__in=ids, role=role).update(role=None)
        role.name, role.adults_only, role.active = name, bool(adults_only), True
        role.save()
        record_church(team.church, actor, "equipo_rol", f"Agregó o modificó un rol del equipo {team.name}", role,
                      value="solo_adultos" if role.adults_only else "abierto")
        if released:
            record_church(team.church, actor, "equipo_asignacion",
                          f"Dejó sin rol a quien ya no puede cubrirlo en fechas del equipo {team.name}", role, value=str(released))
    return role


def retire_role(role: TeamRole, actor, today=None) -> None:
    """The role is no longer used: the dates from today on stop asking for it and its rotation
    ends. Who already has a date with it keeps the date and the word."""
    today = today or timezone.localdate()
    with transaction.atomic():
        team = _active_team(role.team)
        role = TeamRole.objects.select_for_update().get(pk=role.pk, team=team)
        if not role.active:
            return
        role.active = False
        role.save(update_fields=["active"])
        RoleNeed.objects.filter(role=role, service_date__date__gte=today).delete()
        RotationEntry.objects.filter(role=role).delete()
        record_church(team.church, actor, "equipo_rol", f"Retiró un rol del equipo {team.name}", role, value="retirado")


def set_needs(service_date: ServiceDate, actor, counts: dict[int, int], today=None) -> None:
    """How many people of each role the date needs. A role not named, or with zero, needs nobody."""
    with transaction.atomic():
        service_date = services._open_date(service_date, today)
        roles = {role.pk: role for role in active_roles(service_date.team)}
        wanted = {}
        for role_id, count in counts.items():
            if role_id in roles and count:
                if not 0 < count <= MAX_NEED:
                    raise ServingError(f"Escribe un número entre 0 y {MAX_NEED} para cada rol.", code="count")
                wanted[role_id] = count
        current = dict(RoleNeed.objects.filter(service_date=service_date).values_list("role_id", "count"))
        if current == wanted:
            return
        RoleNeed.objects.filter(service_date=service_date).exclude(role_id__in=wanted).delete()
        for role_id, count in wanted.items():
            RoleNeed.objects.update_or_create(service_date=service_date, role_id=role_id, defaults={"count": count})
        team = service_date.team
        record_church(team.church, actor, "equipo_fecha", f"Cambió lo que necesita una fecha del equipo {team.name}", service_date)


@dataclass
class Missing:
    role: TeamRole
    needed: int
    filled: int

    @property
    def missing(self) -> int:
        return max(0, self.needed - self.filled)


def missing_by_date(dates) -> dict[int, list[Missing]]:
    """For each date, what it needs of each role and how many people who have not said no, and can
    still cover the role (``unfit``), hold it."""
    dates = list(dates)
    out: dict[int, list[Missing]] = {service_date.pk: [] for service_date in dates}
    filled: dict[tuple[int, int], int] = {}
    held = (
        Assignment.objects.filter(service_date__in=dates, role__isnull=False).exclude(status=DECLINED)
        .select_related("person", "role", "service_date")
    )
    for row in held:
        if not unfit(row):  # read now: who can no longer cover the role does not fill its place
            key = (row.service_date_id, row.role_id)
            filled[key] = filled.get(key, 0) + 1
    for need in RoleNeed.objects.filter(service_date__in=dates, role__active=True).select_related("role").order_by("role__name", "id"):
        out[need.service_date_id].append(Missing(need.role, need.count, filled.get((need.service_date_id, need.role_id), 0)))
    return out


# ---- the team's settings ------------------------------------------------------------------------
def save_settings(team: Team, actor, *, swap_needs_approval: bool, reminder_days: int) -> Team:
    if reminder_days not in (0, 2, 5):
        raise ServingError("Elige cuándo se envía el recordatorio.", code="reminder_days")
    with transaction.atomic():
        team = _active_team(team)
        if (team.swap_needs_approval, team.reminder_days) == (bool(swap_needs_approval), reminder_days):
            return team
        team.swap_needs_approval, team.reminder_days = bool(swap_needs_approval), reminder_days
        team.save(update_fields=["swap_needs_approval", "reminder_days", "updated_at"])
        record_church(team.church, actor, "equipo_ajustes", f"Cambió los ajustes de cambios y recordatorios del equipo {team.name}", team,
                      value=f"{'visto_bueno' if team.swap_needs_approval else 'sin_visto_bueno'},{reminder_days}")
    return team


# ---- the rotation -------------------------------------------------------------------------------
def rotation_orders(team: Team) -> dict[int, list[int]]:
    """role id -> the people of its rotation, in order."""
    orders: dict[int, list[int]] = {}
    rows = RotationEntry.objects.filter(role__team=team, role__active=True).order_by("role_id", "position", "id")
    for role_id, person_id in rows.values_list("role_id", "person_id"):
        orders.setdefault(role_id, []).append(person_id)
    return orders


def save_rotation(team: Team, actor, *, every_weeks: int, orders: dict[int, list[int]]) -> None:
    """The pattern of the team: each role's people, in order, and how many weeks pass before the
    same person is proposed again. It assigns nothing."""
    if not 1 <= every_weeks <= MAX_EVERY_WEEKS:
        raise ServingError(f"Escribe un número de semanas entre 1 y {MAX_EVERY_WEEKS}.", code="every_weeks")
    with transaction.atomic():
        team = _active_team(team)
        roles = {role.pk for role in active_roles(team)}
        members = {person.pk for person in _members(team)}
        for role_id, people in orders.items():
            if role_id not in roles or not set(people) <= members or len(set(people)) != len(people):
                raise ServingError("Elige solo personas y roles de este equipo.", code="foreign")
        RotationEntry.objects.filter(role__team=team).delete()
        RotationEntry.objects.bulk_create(
            RotationEntry(role_id=role_id, person_id=person_id, position=position)
            for role_id, people in orders.items() for position, person_id in enumerate(people, start=1)
        )
        team.rotation_every_weeks = every_weeks
        team.save(update_fields=["rotation_every_weeks", "updated_at"])
        record_church(team.church, actor, "equipo_rotacion", f"Guardó la rotación del equipo {team.name}", team)


@dataclass
class Slot:
    """One place still missing on a date: who the rotation proposes (or nobody) and who else could."""

    key: str
    role: TeamRole
    proposed: int | None
    options: list[Person]
    number: int = 1  # which of the places of this role still missing on the date, and of how many
    of: int = 1
    why: str = ""  # when nobody can be chosen: one sentence that says why, never about whom

    @property
    def label(self) -> str:
        return f"{self.role.name}, lugar {self.number} de {self.of}" if self.of > 1 else self.role.name


@dataclass
class DateProposal:
    service_date: ServiceDate
    slots: list[Slot] = field(default_factory=list)


@dataclass
class Proposal:
    dates: list[DateProposal]
    token: str

    @property
    def slot_count(self) -> int:
        return sum(len(entry.slots) for entry in self.dates)

    @property
    def proposed_count(self) -> int:
        return sum(1 for entry in self.dates for slot in entry.slots if slot.proposed)


def _why_nobody(role: TeamRole, people: dict, out_of: dict[int, str]) -> str:
    """Why no name can be chosen for a place, in one sentence that names nobody and no person's reason."""
    if not people:
        return "Todavía no hay nadie en el equipo."
    reasons = set(out_of.values())
    if role.adults_only and "adults_only" in reasons:
        if reasons == {"adults_only"}:
            return "Este rol es solo para personas adultas y la iglesia no ha confirmado la edad de nadie del equipo. La administración confirma las edades desde Personas."
        return ("Este rol es solo para personas adultas: de quienes podrían ese día, la iglesia no ha confirmado la edad de nadie. "
                "La administración confirma las edades desde Personas.")
    return "Nadie del equipo puede ese día: cada quien ya tiene esta fecha, sirve ese día en otra o marcó que no puede."


def build_proposal(team: Team, now=None, weeks: int = HORIZON_WEEKS) -> Proposal:
    """What the rotation proposes for the places still missing on the team's dates of the next
    ``weeks`` weeks. It reads and writes nothing: asking twice gives the same answer, and a place
    somebody already holds (by hand or from an earlier confirmation) is never proposed again.

    Round-robin and nothing else: for each role, the people of its rotation in order, starting
    after whoever had the role last; a person is skipped when ``excluded`` says so, or when they
    have a date of this team (held or proposed here) less than ``rotation_every_weeks`` weeks
    away. Nobody is scored."""
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    team = Team.objects.get(pk=team.pk)  # the pattern as it is saved now, not as the caller loaded it
    people = {person.pk: person for person in _members(team)}
    orders = {role_id: [pk for pk in ids if pk in people] for role_id, ids in rotation_orders(team).items()}
    rest = timedelta(weeks=team.rotation_every_weeks)
    dates = [
        service_date for service_date in
        ServiceDate.objects.filter(team=team, date__gte=today, date__lte=today + timedelta(weeks=weeks)).order_by("date", "starts_at", "id")
        if is_coming(service_date, now)
    ]
    missing = missing_by_date(dates)
    # The days each person already has in this team (any date they have not declined), near the window.
    held: dict[int, set] = {}
    near = Assignment.objects.filter(
        service_date__team=team, service_date__date__gte=today - rest, service_date__date__lte=today + timedelta(weeks=weeks) + rest,
    ).exclude(status=DECLINED)
    for person_id, day in near.values_list("person_id", "service_date__date"):
        held.setdefault(person_id, set()).add(day)
    # Where each role's turn is: after the last person who had it before the window's first open date.
    pointer: dict[int, int] = {}
    for role_id, order in orders.items():
        last = (
            Assignment.objects.filter(service_date__team=team, role_id=role_id, person_id__in=order).exclude(status=DECLINED)
            .order_by("-service_date__date", "-service_date__starts_at", "-id").values_list("person_id", flat=True).first()
        )
        pointer[role_id] = (order.index(last) + 1) % len(order) if last in order and order else 0

    proposed_days: dict[int, set] = {}
    stated, out = [], []
    for service_date in dates:
        entry = DateProposal(service_date)
        taken_here: set[int] = set()
        state = {"id": service_date.pk, "day": service_date.date.isoformat(), "hour": str(service_date.starts_at or ""), "slots": []}
        state["held"] = sorted(Assignment.objects.filter(service_date=service_date).values_list("person_id", "status", "role_id"))
        for need in missing.get(service_date.pk, []):
            state.setdefault("needs", []).append([need.role.pk, need.needed, need.filled])
            if not need.missing:
                continue
            out_of = excluded(service_date, need.role, people)
            options = [person for pk, person in people.items() if pk not in out_of]
            order = orders.get(need.role.pk, [])
            for number in range(need.missing):
                chosen = None
                for step in range(len(order)):
                    index = (pointer[need.role.pk] + step) % len(order)
                    pk = order[index]
                    if pk in out_of or pk in taken_here or service_date.date in proposed_days.get(pk, set()):
                        continue
                    days = held.get(pk, set()) | proposed_days.get(pk, set())
                    if any(abs(service_date.date - day) < rest for day in days):
                        continue
                    chosen, pointer[need.role.pk] = pk, (index + 1) % len(order)
                    break
                if chosen is not None:
                    taken_here.add(chosen)
                    proposed_days.setdefault(chosen, set()).add(service_date.date)
                key = f"{service_date.pk}-{need.role.pk}-{number}"
                entry.slots.append(Slot(key, need.role, chosen, options, number + 1, need.missing, _why_nobody(need.role, people, out_of) if not options else ""))
                state["slots"].append([key, chosen, [person.pk for person in options]])
        stated.append(state)
        if entry.slots:
            out.append(entry)
    digest = hashlib.sha256(json.dumps([team.pk, team.rotation_every_weeks, stated], sort_keys=True, default=str).encode()).hexdigest()
    return Proposal(out, digest)


def confirm_proposal(team: Team, actor, *, token: str, choices: dict[str, int | None], now=None) -> int:
    """Assign what the coordinator confirmed: for each missing place, the person chosen on the
    screen (the proposed one unless it was changed) or nobody. ``token`` is the proposal's as it
    was shown: if the dates, what they need, who holds them or who can take them changed since,
    nothing is assigned and ``StaleProposal`` says to show it again."""
    now = now or timezone.now()
    today = timezone.localtime(now).date()
    with transaction.atomic():
        horizon = today + timedelta(weeks=HORIZON_WEEKS)
        team = _active_team(team)  # the team, then its dates: the lock order of Servir
        list(ServiceDate.objects.select_for_update().filter(team=team, date__gte=today, date__lte=horizon).order_by("pk"))
        proposal = build_proposal(team, now)
        if proposal.token != token:
            raise StaleProposal
        people = {person.pk: person for person in _members(team)}
        per_day: dict = {}
        done = 0
        for entry in proposal.dates:
            for slot in entry.slots:
                chosen = choices.get(slot.key)
                if chosen is None:
                    continue
                if chosen not in {person.pk for person in slot.options}:
                    raise ServingError("Elige para cada lugar a una de las personas de su lista.", code="not_an_option")
                if chosen in per_day.setdefault(entry.service_date.date, set()):
                    raise ServingError(
                        f"Elegiste a {people[chosen].first_name} dos veces el mismo día. Deja a cada persona en un solo lugar por día.",
                        code="twice",
                    )
                per_day[entry.service_date.date].add(chosen)
                services.assign(entry.service_date, people[chosen], actor, today=today, role=slot.role)
                done += 1
        if done:
            record_church(team.church, actor, "equipo_rotacion", f"Confirmó lo que propuso la rotación del equipo {team.name}", team)
    return done


# ---- swaps --------------------------------------------------------------------------------------
def _expires_at(swap: SwapRequest) -> datetime:
    return swap.created_at + timedelta(days=SWAP_DAYS)


def is_live(swap: SwapRequest, now=None) -> bool:
    """The request still waits for somebody: open, younger than ``SWAP_DAYS``, its date is coming,
    and the date is still the asker's and they have not said no to it."""
    now = now or timezone.now()
    assignment = swap.assignment
    return (
        swap.status in SwapRequest.OPEN and _expires_at(swap) > now and is_coming(assignment.service_date, now)
        and assignment.status != DECLINED and assignment.person_id == swap.from_person_id
    )


def _close_expired(assignment_id: int, now) -> None:
    """Mark as expired the open requests of an assignment that nobody can answer any more."""
    for swap in SwapRequest.objects.filter(assignment_id=assignment_id, status__in=SwapRequest.OPEN).select_related("assignment__service_date"):
        if not is_live(swap, now):
            SwapRequest.objects.filter(pk=swap.pk, status__in=SwapRequest.OPEN).update(status=Swap.EXPIRED, decided_at=now)


def may_ask_swap(assignment: Assignment, now=None) -> bool:
    """The person can ask a teammate to take this date: it is coming, they have not said no, and
    the record does not say they are a minor (a minor's dates are arranged with who coordinates)."""
    service_date = assignment.service_date
    return (
        service_date.team.is_active and assignment.status != DECLINED and is_coming(service_date, now)
        and not known_minor_on(assignment.person, service_date.date)
    )


def swap_candidates(assignment: Assignment) -> list[Person]:
    """The teammates who could take the date: ``excluded`` lets them, and their record does not say
    they are a minor (where a minor will be is not shown to a teammate)."""
    service_date = assignment.service_date
    people = [person for person in _members(service_date.team) if person.pk != assignment.person_id]
    out_of = excluded(service_date, assignment.role, [person.pk for person in people])
    return [person for person in people if person.pk not in out_of and not known_minor_on(person, service_date.date)]


def _locked(assignment_id: int):
    """The team, the date and the assignment, locked in the order every writer of Servir uses and
    read again under the locks."""
    ids = Assignment.objects.filter(pk=assignment_id).values_list("service_date_id", "service_date__team_id").first()
    if ids is None:
        raise ServingError(GONE, code="gone")
    date_id, team_id = ids
    team = Team.objects.select_for_update(of=("self",)).select_related("church").get(pk=team_id)
    service_date = ServiceDate.objects.select_for_update().filter(pk=date_id, team_id=team_id).first()
    assignment = Assignment.objects.select_for_update(of=("self",)).select_related("person", "role").filter(pk=assignment_id).first()
    if service_date is None or assignment is None:
        raise ServingError(GONE, code="gone")
    service_date.team = team
    assignment.service_date = service_date
    return service_date, assignment


def request_swap(assignment: Assignment, to_person: Person, actor, now=None) -> SwapRequest:
    """``assignment``'s person asks ``to_person`` to take the date. Nothing moves yet."""
    now = now or timezone.now()
    with transaction.atomic():
        service_date, assignment = _locked(assignment.pk)
        team = service_date.team
        if not team.is_active:
            raise ServingError(ARCHIVED, code="archived")
        if not is_coming(service_date, now):
            raise ServingError(NOT_COMING, code="past")
        if not may_ask_swap(assignment, now):
            raise ServingError("Para esta fecha habla con quien coordina el equipo.", code="not_allowed")
        _close_expired(assignment.pk, now)
        if SwapRequest.objects.filter(assignment=assignment, status__in=SwapRequest.OPEN).exists():
            raise ServingError("Ya pediste un cambio para esta fecha. Retíralo antes de pedir otro.", code="open")
        if to_person.pk not in {person.pk for person in swap_candidates(assignment)}:
            raise ServingError("Esa persona no puede tomar esta fecha. Elige a alguien de la lista.", code="not_eligible")
        try:
            with transaction.atomic():
                swap = SwapRequest.objects.create(assignment=assignment, from_person=assignment.person, to_person=to_person)
        except IntegrityError:
            raise ServingError("Ya pediste un cambio para esta fecha. Retíralo antes de pedir otro.", code="open") from None
        record_church(team.church, actor, "equipo_cambio", f"{assignment.person.full_name} pidió un cambio de fecha en el equipo {team.name}",
                      swap, person_id=assignment.person_id, other_person_id=to_person.pk, value="pedido")
    return swap


def _open_swap(swap_id: int, expected: str, now):
    """The swap in ``expected`` state with its date, team and assignment, all locked and read again.
    A request that expired, was answered or whose assignment is no longer the asker's is refused."""
    assignment_id = SwapRequest.objects.filter(pk=swap_id).values_list("assignment_id", flat=True).first()
    if assignment_id is None:
        raise ServingError(GONE, code="gone")
    service_date, assignment = _locked(assignment_id)
    swap = SwapRequest.objects.select_for_update(of=("self",)).select_related("from_person", "to_person").get(pk=swap_id)
    swap.assignment = assignment
    if swap.status != expected:
        raise ServingError(GONE, code="gone")
    if not service_date.team.is_active or assignment.person_id != swap.from_person_id or assignment.status == DECLINED:
        SwapRequest.objects.filter(pk=swap.pk).update(status=Swap.CANCELLED, decided_at=now)
        raise _Expired(GONE, code="gone")
    if not is_live(swap, now):
        SwapRequest.objects.filter(pk=swap.pk).update(status=Swap.EXPIRED, decided_at=now)
        raise _Expired(GONE, code="expired")
    return service_date, assignment, swap


class _Expired(ServingError):
    """Refused, and the request was closed while refusing: the caller's transaction must commit."""


def _target_can(service_date, assignment, swap, to_whom: str) -> None:
    """The teammate can still take the date now, by the same rule as when they were asked."""
    why = excluded(service_date, assignment.role, [swap.to_person_id]).get(swap.to_person_id)
    if why is None and known_minor_on(swap.to_person, service_date.date):
        why = "not_member"
    if why is not None:
        if to_whom == "self":
            words = {"unavailable": "marcaste que ese día no puedes", "busy": "ya sirves ese día en otra fecha", "asked": "ya tienes esa fecha",
                     "not_member": "ya no eres parte del equipo"}.get(why, "ese rol no lo puedes tomar")
            raise ServingError(f"No puedes tomar esta fecha: {words}.", code=why)
        raise ServingError(f"{swap.to_person.first_name} ya no puede tomar esta fecha: {REASONS[why]}.", code=why)


def _move(service_date, assignment, swap, now) -> None:
    """The date changes hands: the assignment is now the teammate's, confirmed (accepting the swap
    was their yes), and its reminder, if one went out, was the other person's."""
    assignment.person, assignment.status, assignment.responded_at = swap.to_person, ACCEPTED, now
    assignment.save(update_fields=["person", "status", "responded_at"])
    ServiceReminder.objects.filter(assignment=assignment).delete()
    swap.status, swap.decided_at = Swap.DONE, now
    swap.save(update_fields=["status", "responded_at", "decided_at", "decided_by"])


def answer_swap(swap: SwapRequest, accepted: bool, actor, now=None) -> SwapRequest:
    """The teammate answers. On "yes" the date moves at once, or waits for who coordinates when the
    team asks for that. Everything is checked again under the locks: a second "yes", a "yes" after
    the date started or after seven days, and a teammate who meanwhile cannot, are all refused."""
    now = now or timezone.now()
    swap_id = swap.pk

    def work():
        try:
            service_date, assignment, swap = _open_swap(swap_id, Swap.PENDING, now)
        except _Expired as error:
            return error
        team = service_date.team
        swap.responded_at = now
        if not accepted:
            swap.status = Swap.DECLINED
            swap.save(update_fields=["status", "responded_at"])
            word = "declinado"
        else:
            _target_can(service_date, assignment, swap, "self")
            if team.swap_needs_approval:
                swap.status = Swap.TO_APPROVE
                swap.save(update_fields=["status", "responded_at"])
                word = "aceptado_por_aprobar"
            else:
                _move(service_date, assignment, swap, now)
                word = "hecho"
        record_church(team.church, actor, "equipo_cambio", f"{swap.to_person.full_name} respondió a un cambio de fecha en el equipo {team.name}",
                      swap, person_id=swap.from_person_id, other_person_id=swap.to_person_id, value=word)
        return swap

    return _unwrap(_atomic(work))


def decide_swap(swap: SwapRequest, approved: bool, actor, now=None) -> SwapRequest:
    """Who coordinates says yes or no to a swap the teammate already accepted."""
    now = now or timezone.now()
    swap_id = swap.pk

    def work():
        try:
            service_date, assignment, swap = _open_swap(swap_id, Swap.TO_APPROVE, now)
        except _Expired as error:
            return error
        team = service_date.team
        swap.decided_by = actor
        if approved:
            _target_can(service_date, assignment, swap, "other")
            _move(service_date, assignment, swap, now)
        else:
            swap.status, swap.decided_at = Swap.REJECTED, now
            swap.save(update_fields=["status", "decided_at", "decided_by"])
        record_church(team.church, actor, "equipo_cambio", f"Respondió a un cambio de fecha en el equipo {team.name}",
                      swap, person_id=swap.from_person_id, other_person_id=swap.to_person_id, value="aprobado" if approved else "rechazado")
        return swap

    return _unwrap(_atomic(work))


def cancel_swap(swap: SwapRequest, actor, now=None) -> None:
    """Who asked takes the request back, while nobody has finished it."""
    now = now or timezone.now()
    with transaction.atomic():
        assignment_id = SwapRequest.objects.filter(pk=swap.pk).values_list("assignment_id", flat=True).first()
        if assignment_id is None:
            return
        service_date, _ = _locked(assignment_id)
        swap = SwapRequest.objects.select_for_update(of=("self",)).get(pk=swap.pk)
        if swap.status not in SwapRequest.OPEN:
            return
        swap.status, swap.decided_at = Swap.CANCELLED, now
        swap.save(update_fields=["status", "decided_at"])
        team = service_date.team
        record_church(team.church, actor, "equipo_cambio", f"Retiró un cambio de fecha en el equipo {team.name}",
                      swap, person_id=swap.from_person_id, other_person_id=swap.to_person_id, value="retirado")


def _atomic(work):
    with transaction.atomic():
        return work()


def _unwrap(result):
    """``work`` returns the refusal instead of raising it when the request was closed while
    refusing, so that the closing write commits; it is raised here, outside the transaction."""
    if isinstance(result, ServingError):
        raise ServingError(result.message, code=result.code)
    return result


def _with_dates(swaps):
    return swaps.select_related("assignment__service_date__team", "assignment__role", "from_person", "to_person")


def swaps_to_answer(person, now=None) -> list[SwapRequest]:
    """What teammates asked this person and still waits for their answer."""
    rows = _with_dates(SwapRequest.objects.filter(to_person=person, status=Swap.PENDING))
    return [swap for swap in rows.order_by("assignment__service_date__date", "id") if is_live(swap, now)]


def my_swaps(person, now=None) -> list[SwapRequest]:
    """What this person asked: what still waits, and what was answered in the last ``SWAP_DAYS`` days."""
    now = now or timezone.now()
    rows = _with_dates(SwapRequest.objects.filter(from_person=person, created_at__gte=now - timedelta(days=2 * SWAP_DAYS)))
    out = []
    for swap in rows.exclude(status=Swap.CANCELLED).order_by("-created_at", "-id"):
        swap.live = is_live(swap, now)
        swap.expired = swap.status in SwapRequest.OPEN and not swap.live
        if swap.live or (swap.responded_at or swap.decided_at or _expires_at(swap)) >= now - timedelta(days=SWAP_DAYS):
            out.append(swap)
    return out


def team_swaps(team: Team, now=None) -> list[SwapRequest]:
    """For who coordinates: the swaps of the team that wait, and the ones done in the last 30 days."""
    now = now or timezone.now()
    rows = _with_dates(SwapRequest.objects.filter(assignment__service_date__team=team))
    out = []
    for swap in rows.filter(status__in=[*SwapRequest.OPEN, Swap.DONE]).order_by("-created_at", "-id"):
        swap.live = is_live(swap, now)
        if swap.live or (swap.status == Swap.DONE and swap.decided_at and swap.decided_at >= now - timedelta(days=30)):
            out.append(swap)
    return out


def open_swap_ids(assignment_ids, now=None) -> dict[int, SwapRequest]:
    """assignment id -> its request that still waits."""
    rows = _with_dates(SwapRequest.objects.filter(assignment_id__in=list(assignment_ids), status__in=SwapRequest.OPEN))
    return {swap.assignment_id: swap for swap in rows if is_live(swap, now)}


# ---- the order of a date ------------------------------------------------------------------------
LINK_MARKS = ("://", "www.", ".com/", ".mx/")


def _plan_title(title: str) -> str:
    title = _one_line(title, 80, "Escribe qué sigue en el orden.")
    if any(mark in title.lower() for mark in LINK_MARKS):
        raise ServingError("El orden no lleva enlaces. Escribe solo el nombre del momento.", code="link")
    return title


def _plan_minutes(minutes) -> int:
    if not isinstance(minutes, int) or not 1 <= minutes <= MAX_MINUTES:
        raise ServingError(f"Escribe los minutos, entre 1 y {MAX_MINUTES}.", code="minutes")
    return minutes


def plan_people(service_date: ServiceDate) -> list[Person]:
    """Who can be in charge of a moment: the team's active people whose record does not say they are a minor."""
    return [person for person in _members(service_date.team) if not known_minor_on(person, service_date.date)]


def _plan_responsible(service_date: ServiceDate, person) -> Person | None:
    if person is None:
        return None
    if person.pk not in {candidate.pk for candidate in plan_people(service_date)}:
        raise ServingError("Elige como responsable a alguien del equipo.", code="responsible")
    return person


def plan_items(service_date: ServiceDate) -> list[PlanItem]:
    """The moments of the date, to SHOW. Who is in charge is read now: a person who left the team,
    whose record was joined to another, or whose record says they are a minor is not named (the
    moment reads as having nobody in charge). The row is not written here; these objects are for a
    template and are never saved."""
    items = list(PlanItem.objects.filter(service_date=service_date).select_related("responsible"))
    allowed = {person.pk for person in plan_people(service_date)} if any(item.responsible_id for item in items) else set()
    for item in items:
        if item.responsible_id is not None and item.responsible_id not in allowed:
            item.responsible = None
    return items


def may_read_plan(person, service_date: ServiceDate) -> bool:
    """Who serves that date (and has not said no) reads its order. Who coordinates is asked apart."""
    return Assignment.objects.filter(service_date=service_date, person=person).exclude(status=DECLINED).exists()


def _audit_plan(service_date: ServiceDate, actor) -> None:
    team = service_date.team
    record_church(team.church, actor, "equipo_orden", f"Modificó el orden de una fecha del equipo {team.name}", service_date)


def save_plan_item(service_date: ServiceDate, actor, *, item: PlanItem | None = None, title: str, minutes, responsible=None,
                   today=None) -> PlanItem:
    title, minutes = _plan_title(title), _plan_minutes(minutes)
    with transaction.atomic():
        service_date = services._open_date(service_date, today)
        responsible = _plan_responsible(service_date, responsible)
        if item is None:
            if PlanItem.objects.filter(service_date=service_date).count() >= MAX_PLAN_ITEMS:
                raise ServingError(f"El orden de una fecha tiene {MAX_PLAN_ITEMS} momentos como máximo.", code="too_many")
            last = PlanItem.objects.filter(service_date=service_date).aggregate(last=Max("position"))["last"] or 0
            item = PlanItem(service_date=service_date, position=last + 1)
        else:
            item = PlanItem.objects.select_for_update(of=("self",)).get(pk=item.pk, service_date=service_date)
        item.title, item.minutes, item.responsible = title, minutes, responsible
        item.save()
        _audit_plan(service_date, actor)
    return item


def delete_plan_item(item: PlanItem, actor, today=None) -> None:
    with transaction.atomic():
        service_date = services._open_date(item.service_date, today)
        if PlanItem.objects.filter(pk=item.pk, service_date=service_date).delete()[0]:
            _audit_plan(service_date, actor)


def move_plan_item(item: PlanItem, up: bool, actor, today=None) -> None:
    """Swap the moment with the one before (or after) it."""
    with transaction.atomic():
        service_date = services._open_date(item.service_date, today)
        items = list(PlanItem.objects.select_for_update().filter(service_date=service_date).order_by("position", "id"))
        index = next((number for number, row in enumerate(items) if row.pk == item.pk), None)
        other = None if index is None else index - 1 if up else index + 1
        if other is None or not 0 <= other < len(items):
            return
        items[index], items[other] = items[other], items[index]
        for position, row in enumerate(items, start=1):
            if row.position != position:
                row.position = position
                row.save(update_fields=["position"])
        _audit_plan(service_date, actor)


def previous_with_plan(service_date: ServiceDate) -> ServiceDate | None:
    """The team's latest earlier date that has an order."""
    return (
        ServiceDate.objects.filter(team_id=service_date.team_id, date__lt=service_date.date, plan_items__isnull=False)
        .order_by("-date", "-starts_at", "-id").distinct().first()
    )


def copy_previous_plan(service_date: ServiceDate, actor, today=None) -> int:
    """Start this date's order from the previous one's. Only into an empty order; a responsible
    person who is no longer in the team is left out."""
    with transaction.atomic():
        service_date = services._open_date(service_date, today)
        if PlanItem.objects.filter(service_date=service_date).exists():
            raise ServingError("Esta fecha ya tiene un orden. Para copiar el anterior, quita primero sus momentos.", code="not_empty")
        source = previous_with_plan(service_date)
        if source is None:
            raise ServingError("Ninguna fecha anterior del equipo tiene un orden.", code="no_previous")
        allowed = {person.pk for person in plan_people(service_date)}
        rows = [
            PlanItem(service_date=service_date, position=position, title=row.title, minutes=row.minutes,
                     responsible_id=row.responsible_id if row.responsible_id in allowed else None)
            for position, row in enumerate(PlanItem.objects.filter(service_date=source).order_by("position", "id"), start=1)
        ]
        PlanItem.objects.bulk_create(rows)
        _audit_plan(service_date, actor)
    return len(rows)
