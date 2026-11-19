"""The directory between members: who is in it, who reads it, and every write of it.

Off twice by default. The church: a switch only a person with the church role of **pastor** moves,
after accepting on screen what the church offers its people (``DirectoryPolicy``). The person: nobody
appears until they say so themselves, with their own account (``DirectoryEntry``); each of phone,
e-mail, groups and serving teams is a separate choice. What holds whoever calls:

* **Nothing is cached and no row decides alone.** ``visible_entries`` asks again on every request:
  the switch, the wording in force, the stage (miembro or asistente), an active account, that the
  record was not joined into another, and that nothing says the person is a minor. Any of those
  changing takes the person out at the next request, with no write.
* **Who reads** (``may_read``): a person who is in it (reciprocity), plus the church's administration
  and its pastors, who already see people. Nobody else, and nobody of another church.
* **Only the person writes their own entry.** ``opt_in`` and ``opt_out`` take the account of the
  request and refuse a person that account is not. There is no function here that takes a list of
  people, and none that an administrator can call for somebody else.
* **A reader's screen is given ``Row`` objects, never a ``Person``**: a row holds the name and only
  the values the person chose, so a field that was not chosen cannot reach a template.
* Audit rows carry ids and the fixed names of the fields, never a value.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.authz import Action, has_scope
from events.models import normalize_name

from .audit import record_church
from .minors import exclude_known_minors, known_minor
from .models import DirectoryEntry, DirectoryPolicy, Person

# The version of what the church offers (the pastor's screen) and of what a person accepts when they
# choose to appear. Change it when either changes in substance: every church is then off until a pastor
# accepts again, and every person is hidden until they accept again (their choices stay for the form).
WORDING_VERSION = "2026-11"

# What a person may add to their name, each one a separate choice. Nothing else is ever in the directory.
FIELDS = ("phone", "email", "groups", "teams")
FIELD_LABELS = {
    "phone": "tu teléfono", "email": "tu correo", "groups": "los grupos a los que perteneces", "teams": "los equipos en los que sirves",
}
# Who takes part: the two stages the administration writes for somebody who is part of the church today.
STAGES = (Person.Stage.MEMBER, Person.Stage.ATTENDER)

# Reading. A page is short and every view of the list (a page, a search) counts against the account:
# see docs/v2/wave12/directorio.md for the worst case these numbers allow.
PAGE_SIZE = 20
VIEW_LIMIT = {"limit": 15, "window_seconds": 60 * 60, "block_seconds": 20 * 60}

NOT_ACCEPTED = "Para encenderlo, marca que leíste y aceptas lo que tu iglesia ofrece."
WORDING_CHANGED = "Esta página cambió mientras la leías. No se encendió nada: léela otra vez."
NOT_PASTOR = "Solo quien tiene el rol de pastor en esta iglesia puede cambiarlo."
NOT_OFFERED = "Tu iglesia no ofrece el directorio en este momento. No se guardó nada."
NOT_YOURS = "Solo tú puedes elegir si apareces en el directorio. No se guardó nada."
PREVIEW_CHANGED = (
    "Algo de lo que viste cambió mientras decidías: tus datos, tus grupos o tus equipos. No se guardó nada: "
    "mira otra vez cómo te verán."
)


class DirectoryError(ValidationError):
    """A rule refused the action; the message is ready for the screen and never quotes a person's data."""


# ---- the church's switch --------------------------------------------------------------------------
@dataclass(frozen=True)
class State:
    enabled: bool  # the directory answers today: switched on AND accepted under the wording in force
    outdated: bool = False  # switched on under an older wording: a pastor has to accept again


def state(church) -> State:
    row = DirectoryPolicy.objects.filter(church=church).values("enabled", "wording_version").first() or {}
    switched, current = bool(row.get("enabled")), row.get("wording_version") == WORDING_VERSION
    return State(switched and current, switched and not current)


def is_on(church) -> bool:
    return church is not None and DirectoryPolicy.objects.filter(church=church, enabled=True, wording_version=WORDING_VERSION).exists()


def is_pastor(user, church) -> bool:
    """``user`` holds the church role of pastor in ``church`` today (the question the pastoral approval asks)."""
    from resources.approvals import can_approve

    return can_approve(user, church)


def _pastor_or_refuse(church, actor) -> None:
    if not is_pastor(actor, church):
        raise DirectoryError(NOT_PASTOR, code="role")


def switch_on(church, actor, *, accepted: bool, wording: str) -> DirectoryPolicy:
    """A pastor accepts what was on the screen (``wording``: its version) and the directory starts to answer.
    It makes nobody appear: only who had already chosen to, under the wording in force."""
    _pastor_or_refuse(church, actor)
    if not accepted:
        raise DirectoryError(NOT_ACCEPTED, code="accepted")
    if wording != WORDING_VERSION:
        raise DirectoryError(WORDING_CHANGED, code="stale")
    with transaction.atomic():
        row, _ = DirectoryPolicy.objects.select_for_update().get_or_create(church=church)
        if row.enabled and row.wording_version == WORDING_VERSION:
            return row
        row.enabled, row.accepted_by, row.accepted_at, row.wording_version = True, actor, timezone.now(), WORDING_VERSION
        row.save(update_fields=["enabled", "accepted_by", "accepted_at", "wording_version", "updated_at"])
        record_church(church, actor, "directorio_politica", "Encendió el directorio entre miembros y aceptó lo que la iglesia ofrece",
                      obj=row, value=True)
    return row


def switch_off(church, actor) -> bool:
    """Nobody reads the directory from now on. Only the church's row changes: every person's choice stays."""
    _pastor_or_refuse(church, actor)
    with transaction.atomic():
        row = DirectoryPolicy.objects.select_for_update().filter(church=church).first()
        if row is None or not row.enabled:
            return False
        row.enabled = False
        row.save(update_fields=["enabled", "updated_at"])
        record_church(church, actor, "directorio_politica", "Apagó el directorio entre miembros", obj=row, value=False)
    return True


# ---- who takes part, who reads ------------------------------------------------------------------
def may_take_part(person: Person | None) -> bool:
    """Whether ``person`` may be offered the choice at all: a record in force, with an account, that the
    administration has as member or attender, and that nothing marks as a minor."""
    return bool(
        person is not None and person.user_id and not person.merged_into_id and person.stage in STAGES and not known_minor(person)
    )


def entry_of(person: Person | None) -> DirectoryEntry | None:
    return DirectoryEntry.objects.filter(person=person).first() if person is not None else None


def is_listed(person: Person | None) -> bool:
    """``person`` said yes, to the wording in force. (Whether they are *shown* also needs ``may_take_part``.)"""
    return person is not None and DirectoryEntry.objects.filter(person=person, listed=True, wording_version=WORDING_VERSION).exists()


def is_staff_reader(user, church) -> bool:
    """The administration and the pastors of ``church``: they already see its people."""
    return has_scope(user, church, "church", church.pk, Action.MANAGE) or is_pastor(user, church)


def may_read(user, church, person: Person | None) -> bool:
    """Asked on every request. The switch first: off, nobody reads, whatever their role."""
    if church is None or not getattr(user, "is_authenticated", False) or not is_on(church):
        return False
    if is_staff_reader(user, church):
        return True
    return bool(
        person is not None and person.church_id == church.pk and person.user_id == user.pk
        and is_listed(person) and may_take_part(person)
    )


def visible_entries(church):
    """The entries of ``church`` that are shown at this moment. The one read path of the directory."""
    if not is_on(church):
        return DirectoryEntry.objects.none()
    rows = DirectoryEntry.objects.filter(
        listed=True, wording_version=WORDING_VERSION, person__church=church, person__merged_into__isnull=True,
        person__stage__in=STAGES, person__user__isnull=False, person__user__is_active=True,
    )
    return exclude_known_minors(rows)


def search(church, query: str):
    """``visible_entries`` by name, and by nothing else: a phone or an address typed here finds nobody,
    so the directory never answers "whose number is this"."""
    rows = visible_entries(church)
    for token in normalize_name(query).split():
        rows = rows.filter(person__normalized_name__contains=token)
    return rows.select_related("person").order_by("person__first_name", "person__last_name", "person_id")


# ---- what a reader is given -----------------------------------------------------------------------
@dataclass
class Row:
    """One person as the directory shows them: the name, and only what they chose that has a value."""

    name: str
    phone: str = ""
    email: str = ""
    groups: list[str] = field(default_factory=list)
    teams: list[str] = field(default_factory=list)


def _names(church, person_ids: list[int], kind: str) -> dict[int, list[str]]:
    """The active groups (or serving teams) of ``church`` each of those people is in today, by name."""
    if not person_ids:
        return {}
    if kind == "groups":
        from groups.models import Group, GroupMembership

        pairs = GroupMembership.objects.filter(
            person_id__in=person_ids, status=GroupMembership.Status.ACTIVE, group__church=church, group__status=Group.Status.ACTIVE,
        ).order_by("group__name", "group_id").values_list("person_id", "group__name")
    else:
        from serving.models import Team, TeamMembership

        pairs = TeamMembership.objects.filter(
            person_id__in=person_ids, status=TeamMembership.Status.ACTIVE, team__church=church, team__status=Team.Status.ACTIVE,
        ).order_by("team__name", "team_id").values_list("person_id", "team__name")
    found: dict[int, list[str]] = {}
    for person_id, name in pairs:
        found.setdefault(person_id, []).append(name)
    return found


def rows_for(church, entries) -> list[Row]:
    """``entries`` (with their person) as rows. Two more queries for a whole page, whatever its size."""
    entries = list(entries)
    groups = _names(church, [e.person_id for e in entries if e.show_groups], "groups")
    teams = _names(church, [e.person_id for e in entries if e.show_teams], "teams")
    return [
        Row(
            name=e.person.full_name,
            phone=e.person.phone.strip() if e.show_phone else "",
            email=e.person.email.strip() if e.show_email else "",
            groups=groups.get(e.person_id, []) if e.show_groups else [],
            teams=teams.get(e.person_id, []) if e.show_teams else [],
        )
        for e in entries
    ]


def clean_fields(chosen) -> tuple[str, ...]:
    """The fields of ``chosen`` that exist, in the fixed order. Anything else is dropped, never stored."""
    wanted = set(chosen or ())
    return tuple(name for name in FIELDS if name in wanted)


def preview(church, person: Person, fields) -> Row:
    """``person`` exactly as others would see them with ``fields``: built by the same function as the list."""
    fields = clean_fields(fields)
    draft = DirectoryEntry(person=person, listed=True, **{f"show_{name}": name in fields for name in FIELDS})
    return rows_for(church, [draft])[0]


def preview_token(person: Person, fields, row: Row) -> str:
    """A fingerprint of everything the preview stated: the wording, the fields and each value shown.
    Only the hash travels; a confirmation whose values changed meanwhile shows the preview again."""
    parts = [WORDING_VERSION, str(person.pk), ",".join(clean_fields(fields)), row.name, row.phone, row.email, *row.groups, "\x1e", *row.teams]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


# ---- the person's own choice ----------------------------------------------------------------------
def _own_locked(church, person: Person, actor) -> Person:
    """The person's row, read again under a lock (a merge of the record waits for it, and it for a merge).
    Refuses anybody who is not that person: this is what keeps the administration from choosing for somebody."""
    locked = Person.objects.select_for_update().filter(pk=getattr(person, "pk", None), church=church).first()
    if locked is None or locked.merged_into_id is not None or not locked.user_id or locked.user_id != getattr(actor, "pk", None):
        raise DirectoryError(NOT_YOURS, code="not_yours")
    return locked


def opt_in(church, person: Person, actor, *, fields, token: str) -> DirectoryEntry:
    """``actor`` (the account that is ``person``) appears in the directory with their name and ``fields``,
    having seen the preview ``token`` covers. Replaces what they had chosen before."""
    fields = clean_fields(fields)
    with transaction.atomic():
        person = _own_locked(church, person, actor)
        if not is_on(church) or not may_take_part(person):
            raise DirectoryError(NOT_OFFERED, code="off")
        if token != preview_token(person, fields, preview(church, person, fields)):
            raise DirectoryError(PREVIEW_CHANGED, code="stale")
        entry, _ = DirectoryEntry.objects.select_for_update().get_or_create(person=person)
        entry.listed, entry.accepted_at, entry.wording_version = True, timezone.now(), WORDING_VERSION
        for name in FIELDS:
            setattr(entry, f"show_{name}", name in fields)
        entry.save(update_fields=["listed", "accepted_at", "wording_version", *(f"show_{name}" for name in FIELDS), "updated_at"])
        record_church(church, actor, "directorio_eleccion", "Eligió aparecer en el directorio entre miembros", obj=entry,
                      person_id=person.pk, value=True, fields=list(fields))
    return entry


def opt_out(church, person: Person, actor) -> bool:
    """Immediate and total: the person leaves and every choice is cleared. It needs nothing but being that
    person: not the switch, not a stage. True when there was something to take back."""
    with transaction.atomic():
        person = _own_locked(church, person, actor)
        entry = DirectoryEntry.objects.select_for_update().filter(person=person).first()
        if entry is None or not (entry.listed or any(getattr(entry, f"show_{name}") for name in FIELDS)):
            return False
        entry.listed = False
        for name in FIELDS:
            setattr(entry, f"show_{name}", False)
        entry.save(update_fields=["listed", *(f"show_{name}" for name in FIELDS), "updated_at"])
        record_church(church, actor, "directorio_eleccion", "Dejó de aparecer en el directorio entre miembros", obj=entry,
                      person_id=person.pk, value=False, fields=[])
    return True


# ---- the navigation -------------------------------------------------------------------------------
def shows_link(user, church, person: Person | None) -> bool:
    """«Directorio» in the member's navigation: the switch is on AND this person may read it."""
    return may_read(user, church, person)


def shows_choice(church, person: Person | None) -> bool:
    """«Aparecer en el directorio» in "Mis datos": while it is offered to this person, or while they still have
    a choice of their own to take back (the switch off, a stage changed, a new wording, a record that now says
    they are a minor): then the page offers only to take it back."""
    if person is None or not person.user_id or person.merged_into_id:
        return False
    if is_on(church) and may_take_part(person):
        return True
    return DirectoryEntry.objects.filter(person=person, listed=True).exists()


def profile_context(church, person: Person | None) -> dict:
    """What "Mis datos" says about the directory: whether the row is there, whether the person is shown today,
    and whether their phone or e-mail is, so the page's sentence about who sees them stays true."""
    if not shows_choice(church, person):
        return {"directory_choice": False}
    entry = entry_of(person)
    shown = bool(
        entry is not None and entry.listed and entry.wording_version == WORDING_VERSION and is_on(church) and may_take_part(person)
    )
    return {
        "directory_choice": True, "directory_shown": shown,
        "directory_contact": shown and (entry.show_phone or entry.show_email),
    }
