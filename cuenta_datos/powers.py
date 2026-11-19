"""«Quién puede qué»: every account that holds a power in one church, read from the tables that
authorization itself reads (issue #98, part B). Read-only: nothing is changed from here.

| Kind | Read from | The rule that reads the same rows |
|---|---|---|
| Church roles | ``core.UserChurchRole`` | ``core.authz.has_scope`` (church) |
| Group guides | ``access.RoleAssignment`` in force and backed by its source | ``has_scope`` (group, manage) |
| Team coordinators | the same, scope ``ministry`` | ``has_scope`` (ministry, manage) |
| Form readers | ``formularios.FormReader`` + the two above | ``formularios.access.can_read`` |
| Event teams | ``events.EventMembership`` (active) | ``events.access.resolve_access`` |
| Superusers | the account's flag: a count, never names | ``resolve_access`` |

``cuenta_datos/tests/test_powers.py`` (``DriftTests``), on a synthetic church that holds every kind:
each section, account by account and both ways (nobody missing, nobody extra), against its own source of
truth: church roles against the role table behind ``has_scope`` (church); guides and coordinators against
``has_scope`` (group / ministry, manage); form readers against ``formularios.access.can_read``; event teams
against ``resolve_access`` and the membership it answers from. And every account ``has_scope`` lets manage
something is on some section. It proves the screen and those rules agree on that fixture, not on every
church; a NEW kind of power (another table authorization reads) still needs its own section and test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from access.models import RoleAssignment, in_force
from access.sources import backed_by_source
from core.authz import GROUP_MANAGE_ROLES, MINISTRY_MANAGE_ROLES
from core.models import Invitation, Role, UserChurchRole
from events.access import ROLE_LABELS
from events.models import Event, EventMembership
from formularios.models import FormReader
from formularios.services import church_tz
from groups.models import Group, GroupMembership
from people.models import Person
from resources import approvals
from serving.models import Team

STALE_DAYS = 180
# A church role that is a way to take part, not a power over anything.
NOT_A_POWER = frozenset({Role.PARTICIPANT})
CHURCH_ROLE_WORDS = {
    Role.CHURCH_ADMIN: "Administración de la iglesia",
    Role.PASTOR: "Pastor de la iglesia",
    Role.MENTOR: "Mentor",
}


@dataclass
class Holder:
    user_id: int
    name: str
    what: str
    since: date | None = None
    granted_by: str = ""
    last_login: date | None = None
    flags: list[str] = field(default_factory=list)
    url: str = ""
    url_label: str = ""


@dataclass
class Section:
    key: str
    title: str
    note: str
    holders: list[Holder]
    empty: str


@dataclass
class Review:
    sections: list[Section]
    superusers: int
    no_pastor: bool
    lone_pastor_named_by_admin: bool
    flagged: int

    def user_ids(self) -> set[int]:
        return {holder.user_id for section in self.sections for holder in section.holders}


class _People:
    """Names, last sign-in and the two flags of an account, for the accounts of one church."""

    def __init__(self, church, user_ids, now):
        self.church, self.zone, self.now = church, church_tz(church), now
        self.users = get_user_model().objects.in_bulk(user_ids)
        self.live: dict[int, str] = {}
        self.hidden: set[int] = set()
        for person in Person.objects.filter(church=church, user_id__in=user_ids).order_by("id"):
            if person.merged_into_id is None:
                self.live[person.user_id] = person.full_name
            else:
                self.hidden.add(person.user_id)
        self.hidden -= set(self.live)

    def name(self, user_id) -> str:
        user = self.users.get(user_id)
        if user is None:
            return ""
        return self.live.get(user_id) or user.get_full_name().strip() or user.email or "Cuenta sin nombre"

    def day(self, moment) -> date | None:
        return timezone.localtime(moment, self.zone).date() if moment else None

    def holder(self, user_id, what, *, since=None, granted_by=None, url="", url_label="") -> Holder:
        user = self.users[user_id]
        flags = []
        if user.last_login is None:
            flags.append("Nunca ha entrado")
        elif user.last_login < self.now - timedelta(days=STALE_DAYS):
            flags.append(f"No entra desde hace más de {STALE_DAYS} días")
        if user_id in self.hidden:
            flags.append("Su registro de persona se unió a otro")
        return Holder(
            user_id, self.name(user_id), what, self.day(since), self.name(granted_by) if granted_by else "",
            self.day(user.last_login), flags, url, url_label,
        )


def _projected(church, scope_type: str, roles) -> list[RoleAssignment]:
    """The rows ``has_scope`` would answer "manage" from, for one projected scope."""
    return list(
        RoleAssignment.objects.filter(
            in_force(), backed_by_source(scope_type), church=church, scope_type=scope_type, role__in=roles,
            subject_user__is_active=True,
        ).order_by("scope_id", "id")
    )


def build(church, *, can_change: bool, now=None) -> Review:
    """``can_change``: the viewer is the administration, so each row links to the screen where the
    power is changed. A pastor reads the same rows without those links (those screens would refuse)."""
    now = now or timezone.now()
    church_roles = list(
        UserChurchRole.objects.filter(church=church, user__is_active=True).exclude(role__in=NOT_A_POWER).order_by("role", "id")
    )
    guides = _projected(church, "group", GROUP_MANAGE_ROLES)
    coordinators = _projected(church, "ministry", MINISTRY_MANAGE_ROLES)
    memberships = list(
        EventMembership.objects.filter(event__church=church, is_active=True, user__is_active=True)
        .select_related("event").order_by("-event__starts_on", "event_id", "role", "id")
    )
    groups = Group.objects.filter(church=church).in_bulk({row.scope_id for row in guides})
    teams = Team.objects.filter(church=church).in_bulk({row.scope_id for row in coordinators})
    added_by = dict(
        GroupMembership.objects.filter(pk__in=[row.source_id for row in guides], group__church=church).values_list("pk", "added_by_id")
    )
    readers = list(FormReader.objects.filter(form__church=church).select_related("form").order_by("form__title", "id"))

    # Who invited each church role, when an accepted invitation of this church says so.
    invited: dict[tuple[str, str], int] = {}
    for email, role, by in Invitation.objects.filter(
        church=church, accepted_at__isnull=False, invited_by__isnull=False
    ).order_by("accepted_at").values_list("email", "role", "invited_by_id"):
        invited[(email.casefold(), role)] = by

    user_ids = {row.user_id for row in church_roles} | {row.subject_user_id for row in guides + coordinators}
    user_ids |= {row.user_id for row in memberships}
    granters = set(added_by.values()) | set(invited.values()) | {row.created_by_id for row in memberships}
    granters |= {row.added_by_id for row in readers}
    people = _People(church, (user_ids | granters) - {None}, now)

    users_url = reverse("user_list")
    roles_section = Section(
        "iglesia", "Roles de la iglesia",
        "La administración maneja todo en la iglesia y en cada uno de sus eventos. Un pastor aprueba lo que se publica, lee las "
        "peticiones de oración, es el único que las enciende o apaga, igual que el directorio entre miembros, y entra como pastor a cada evento. Un mentor acompaña a quienes le asignan.",
        [
            people.holder(
                row.user_id, CHURCH_ROLE_WORDS.get(row.role, row.get_role_display()), since=row.created_at,
                granted_by=invited.get(((people.users[row.user_id].email or "").casefold(), row.role)),
                url=users_url, url_label="Personas con acceso",
            )
            for row in church_roles
        ],
        "Nadie tiene un rol de iglesia.",
    )

    def scoped(rows, names, words, url_name, url_label, granted=None) -> list[Holder]:
        found = []
        for row in rows:
            scope = names.get(row.scope_id)
            if scope is None:
                continue  # a row whose group or team is of another church or is gone grants nothing here
            archived = "" if scope.status == type(scope).Status.ACTIVE else " (archivado)"
            found.append(people.holder(
                row.subject_user_id, f"{words} {scope.name}{archived}", since=row.start_at,
                granted_by=(granted or {}).get(row.source_id),
                url=reverse(url_name, args=[scope.pk]) if can_change else "", url_label=url_label,
            ))
        return sorted(found, key=lambda holder: (holder.what.casefold(), holder.name.casefold()))

    guide_holders = scoped(guides, groups, "Guía de", "community:group_people", "Personas del grupo", added_by)
    coordinator_holders = scoped(coordinators, teams, "Coordina", "serving:team", "Equipo")

    # A form's reader is a scope, never a list of people: who reads it is whoever guides that active group or
    # coordinates that active team today (formularios.access.can_read).
    by_scope: dict[tuple[str, int], list[int]] = {}
    for kind, rows in (("group", guides), ("ministry", coordinators)):
        for row in rows:
            by_scope.setdefault((kind, row.scope_id), []).append(row.subject_user_id)
    reader_holders = []
    for reader in readers:
        scope = (groups if reader.scope_type == "group" else teams).get(reader.scope_id)
        if scope is None or scope.status != type(scope).Status.ACTIVE:
            continue
        through = "por guiar" if reader.scope_type == "group" else "por coordinar"
        for user_id in by_scope.get((reader.scope_type, reader.scope_id), []):
            reader_holders.append(people.holder(
                user_id, f"Lee las respuestas de «{reader.form.title}» {through} {scope.name}", since=reader.added_at,
                granted_by=reader.added_by_id,
                url=reverse("formularios:readers", args=[reader.form_id]) if can_change else "", url_label="Lectores del formulario",
            ))

    event_holders = [
        people.holder(
            row.user_id,
            f"{ROLE_LABELS.get(row.role, row.role)} en {row.event.name}" + (" (archivado)" if row.event.status == Event.Status.ARCHIVED else ""),
            since=row.created_at, granted_by=row.created_by_id,
            url=reverse("events:team", args=[row.event.slug]) if can_change and row.event.camp_tools else "", url_label="Equipo del evento",
        )
        for row in memberships
    ]

    sections = [
        roles_section,
        Section("grupos", "Quienes guían un grupo",
                "Aceptan o rechazan solicitudes, pasan lista y ven a las personas de su grupo. Nada fuera de su grupo.",
                guide_holders, "Ningún grupo tiene guía con cuenta."),
        Section("equipos", "Quienes coordinan un equipo de servicio",
                "Organizan las fechas de su equipo y a quién se le pide servir. Nada fuera de su equipo.",
                coordinator_holders, "Ningún equipo tiene coordinación con cuenta."),
        Section("formularios", "Quienes leen respuestas de formularios",
                "La administración lee todos los formularios. Además, estas personas leen las respuestas de un formulario "
                "mientras guíen ese grupo o coordinen ese equipo.",
                reader_holders, "Ningún formulario tiene lectores además de la administración."),
        Section("eventos", "Equipos de los eventos de esta iglesia",
                "Un rol de evento vale solo dentro de ese evento: no da nada en la iglesia ni en otro evento.",
                event_holders, "Ningún evento de esta iglesia tiene equipo propio."),
    ]

    pastors = [row for row in church_roles if row.role == Role.PASTOR]
    admin_ids = {row.user_id for row in church_roles if row.role == Role.CHURCH_ADMIN}
    lone = False
    if len(pastors) == 1:
        only = pastors[0]
        by = invited.get(((people.users[only.user_id].email or "").casefold(), Role.PASTOR))
        lone = only.user_id in admin_ids or (by is not None and by in admin_ids)
    return Review(
        sections=sections,
        superusers=get_user_model().objects.filter(is_superuser=True, is_active=True).count(),
        no_pastor=not approvals.has_active_pastor(church),
        lone_pastor_named_by_admin=lone,
        flagged=sum(1 for section in sections for holder in section.holders if holder.flags),
    )
