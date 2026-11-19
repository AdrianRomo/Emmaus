"""Two records of one church are the same person: the preview, the merge and its undo.

The rules that hold whoever calls (the views have already asked ``has_scope`` for church
administration):

* One church, two different active people, one pair at a time, decided by a person. Nothing here
  runs by itself and nothing looks across churches.
* ``TABLES`` is the decision taken for every foreign key to ``Person`` (docs/v2/wave9/personas.md).
  A relation nobody decided about (``undecided_relations``) refuses the merge while it holds rows of
  either person: a module added later cannot be merged by accident.
* A merge never writes a date of birth or its verification, never reads or moves a camp answer, and
  never copies a role: it moves the memberships and re-derives ``access.RoleAssignment`` from them
  with the sources' own ``sync_assignment`` (decision 0016).
* Everything is one transaction. LOCK ORDER, the same for a merge and an undo: (the ``PersonMerge``
  row, for an undo), then the serving teams of both people by id, then both people by id, then rows.
  Teams come first because ``serving.services`` lock a team and then write a membership or an
  assignment, which needs the person's row (a key-share lock on PostgreSQL): people first would be
  the opposite order. ``agenda`` locks the person and then the registration, as here. If a
  membership in a team that was not locked turns up once the people are locked, nothing is written
  and ``Busy`` is raised (the screen says to try again), as for a deadlock the database reports.
* The state is read again under the locks, the rows to move included: the preview the
  administrator saw is only compared with it (``token`` covers the two records, every row id that
  moves, what stays, the account and the roles gained). If anything differs, nothing is merged.
* Registrations are settled again with ``agenda.services.reevaluate_person`` (``standing`` is the
  one rule) after a merge and after an undo, for both people, so a row never stands on the age
  facts of the other record.
* The manifest holds ids and, for ``UNDO_DAYS``, the survivor's previous contact values. The undo
  puts back exactly the rows it moved that still point at the survivor and says what it could not.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from django.apps import apps
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from events.models import normalize_name

from . import duplicates, services
from .audit import record_church
from .models import CampusAffiliation, DuplicateDismissal, EventParticipantLink, Person, PersonMerge
from .duplicates import NAME_BIRTH
from .services import PeopleError

UNDO_DAYS = 30
CONTACT_FIELDS = ("email", "phone", "preferred_contact_method")


class MergeRefused(PeopleError):
    """The merge (or its undo) was refused; ``reasons`` are sentences ready for the screen."""

    def __init__(self, reasons: list[str]):
        super().__init__(reasons[0], code="refused")
        self.reasons = reasons


class StalePreview(Exception):
    """What the administrator confirmed is not what the database holds any more: show it again."""


class Busy(Exception):
    """Somebody else was writing the same rows; nothing was written. Asking again is safe."""


def _always() -> Q:
    return Q()


def _inactive() -> Q:
    return Q(status="inactiva")


def _past_date() -> Q:
    return Q(service_date__date__lt=timezone.localdate())


@dataclass(frozen=True)
class Table:
    """One foreign key to ``Person`` and what a merge does with its rows.

    ``unique_with``: the other columns of the rule "one per person and …" (``()``: one per person;
    ``None``: no such rule, every row moves). ``scope``: the rows the rule applies to, when it is a
    partial one. A row of the absorbed record whose key the survivor already has cannot move:
    it stays where it is if it matches ``stays()``, and otherwise the merge is refused with
    ``refusal``. ``private``: the person's own marks, moved without being counted on any screen.
    """

    model: str
    field: str
    one: str
    many: str
    unique_with: tuple[str, ...] | None = None
    scope: Q | None = None
    stays: Callable[[], Q] | None = _always
    refusal: str = ""
    private: bool = False

    @property
    def key(self) -> str:
        return f"{self.model}.{self.field}"

    def rows(self):
        return apps.get_model(self.model)._base_manager.all()


TABLES: tuple[Table, ...] = (
    Table("people.CampusAffiliation", "person", "sede", "sedes", unique_with=("sede_id",)),
    Table(
        "groups.GroupMembership", "person", "grupo en el que está o estuvo", "grupos en los que está o estuvo",
        unique_with=("group_id",), stays=_inactive,
        refusal="Los dos registros están en un mismo grupo. Saca del grupo al registro que se va a unir y vuelve a intentarlo.",
    ),
    Table(
        "groups.GroupJoinRequest", "person", "solicitud para unirse a un grupo", "solicitudes para unirse a grupos",
        unique_with=("group_id",), scope=Q(status="pendiente"), stays=None,
        refusal="Los dos registros pidieron unirse al mismo grupo y nadie ha respondido. Responde primero una de las dos solicitudes.",
    ),
    Table("groups.GroupAttendance", "person", "asistencia a una reunión de grupo", "asistencias a reuniones de grupo",
          unique_with=("meeting_id",)),
    Table("notices.AnnouncementRead", "person", "aviso leído", "avisos leídos", unique_with=("announcement_id",)),
    Table("notices.EmailPreference", "person", "preferencia de avisos por correo", "preferencias de avisos por correo",
          unique_with=()),
    Table("notices.NoticeEmail", "person", "registro de un correo de aviso", "registros de correos de avisos",
          unique_with=("announcement_id", "version")),
    Table(
        "agenda.EventRegistration", "person", "inscripción a un evento, con su autorización si la tiene",
        "inscripciones a eventos, con su autorización si la tienen", unique_with=("event_id",), stays=None,
        refusal=(
            "Los dos registros tienen una inscripción al mismo evento. Una inscripción guarda un lugar, la asistencia y, si es de una persona menor de edad, "
            "la autorización de su tutor: Emmaus no elige cuál vale. Esta unión no se puede hacer."
        ),
    ),
    Table("resources.DevotionalRead", "person", "", "", unique_with=("devotional_id",), private=True),
    Table("resources.ReadingPlanProgress", "person", "", "", unique_with=("plan_id",), private=True),
    Table(
        "serving.TeamMembership", "person", "equipo en el que sirve o sirvió", "equipos en los que sirve o sirvió",
        unique_with=("team_id",), stays=_inactive,
        refusal="Los dos registros están en un mismo equipo. Saca del equipo al registro que se va a unir y vuelve a intentarlo.",
    ),
    Table(
        "serving.Assignment", "person", "fecha para servir", "fechas para servir", unique_with=("service_date_id",),
        stays=_past_date,
        refusal="A los dos registros se les pidió servir en una misma fecha que todavía no pasa. Quita de esa fecha al registro que se va a unir.",
    ),
    Table("serving.Unavailable", "person", "día marcado como «no puedo»", "días marcados como «no puedo»", unique_with=("date",)),
    Table("care.FollowUp", "person", "seguimiento sobre esta persona", "seguimientos sobre esta persona"),
    Table("care.FollowUp", "owner", "seguimiento a su cargo", "seguimientos a su cargo"),
    Table("care.FollowUpStep", "by", "paso de seguimiento que registró", "pasos de seguimiento que registró"),
    # Who asked for a space. The words of the booking are not read; the survivor keeps the requests, and with them
    # what ``espacios.access`` gives to who asked. ``decided_by`` is an account, not a person: untouched.
    Table("espacios.Booking", "requested_by", "reserva de un espacio que pidió", "reservas de espacios que pidió"),
    # A column nobody reads since feedback/0002. Whether somebody gave an opinion is theirs alone: not counted.
    Table("feedback.FeedbackGiven", "person", "", "", private=True),
    # Who answered a form of the church, or the record "Agregar a personas" made from a public answer. The answers
    # are not read; every row moves to the survivor (no "one per person" rule: the same-day rule is by time).
    Table("formularios.Submission", "person", "respuesta a un formulario", "respuestas a formularios"),
)
CAMP_KEY = "people.EventParticipantLink.person"
CAMP_LABELS = ("vínculo con un evento en el que participó", "vínculos con eventos en los que participó")
# Relations to ``Person`` that are not rows to move: the merge itself, the camp link (moved through its
# services) and the records of this module.
HANDLED_ELSEWHERE = frozenset({
    ("people.Person", "merged_into"), ("people.EventParticipantLink", "person"),
    ("people.PersonMerge", "survivor"), ("people.PersonMerge", "absorbed"),
    ("people.DuplicateDismissal", "person_a"), ("people.DuplicateDismissal", "person_b"),
})
_BY_KEY = {table.key: table for table in TABLES}


def relations_to_person() -> list[tuple[str, str]]:
    """Every relation of every installed model whose target is ``Person``, as (model label, field)."""
    found = []
    for model in apps.get_models(include_auto_created=True):
        for relation in model._meta.get_fields():
            if relation.is_relation and getattr(relation, "related_model", None) is Person and (relation.concrete or relation.many_to_many):
                if not relation.auto_created:
                    found.append((model._meta.label, relation.name))
    return sorted(found)


def undecided_relations() -> list[tuple[str, str]]:
    decided = {(table.model, table.field) for table in TABLES} | HANDLED_ELSEWHERE
    return [relation for relation in relations_to_person() if relation not in decided]


# ---- the preview ----------------------------------------------------------------------------------
@dataclass
class FieldRow:
    name: str
    label: str
    survivor: str
    absorbed: str
    # "same", "keeps" (the survivor's, no choice), "fills" (the survivor had none), "choose"
    state: str


@dataclass
class Preview:
    survivor: Person
    absorbed: Person
    signals: list[str]
    refusals: list[str] = field(default_factory=list)
    fields: list[FieldRow] = field(default_factory=list)
    moves: dict[str, list[int]] = field(default_factory=dict)
    stays: dict[str, int] = field(default_factory=dict)
    camp_links: list[EventParticipantLink] = field(default_factory=list)
    demote: list[int] = field(default_factory=list)
    account: str = "none"  # "none", "survivor" (already has it), "moves"
    groups_gained: int = 0
    teams_gained: int = 0
    # Pending requests to a group whose other record is already in it: closed, not moved.
    close_requests: list[int] = field(default_factory=list)
    # Two minors (or an authorization on record) with the same date of birth and not the same full name:
    # they may be siblings who share a parent's phone or e-mail. The administrator confirms it apart.
    sibling_warning: bool = False
    token: str = ""

    @property
    def allowed(self) -> bool:
        return not self.refusals

    @staticmethod
    def _counted(counts: dict[str, int]) -> list[str]:
        lines = []
        for key, total in counts.items():
            table = _BY_KEY.get(key)
            one, many = CAMP_LABELS if key == CAMP_KEY else ((table.one, table.many) if table else ("", ""))
            if total and one:
                lines.append(f"{total} {one if total == 1 else many}")
        return lines

    @property
    def move_lines(self) -> list[str]:
        """Counts by kind of what moves, never the content. Private marks are not here on purpose."""
        counts = {key: len(ids) for key, ids in self.moves.items()}
        counts[CAMP_KEY] = len(self.camp_links)
        return self._counted(counts)

    @property
    def stay_lines(self) -> list[str]:
        return self._counted(self.stays)

    @property
    def moves_registrations(self) -> bool:
        return bool(self.moves.get("agenda.EventRegistration.person"))

    @property
    def choices(self) -> list[FieldRow]:
        return [row for row in self.fields if row.state == "choose"]


def _is_minor_by_date(person: Person) -> bool:
    if person.birth_date is None:
        return False
    today = timezone.localdate()
    born = person.birth_date
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day)) < 18


def _may_be_minor_or_has_consent(person: Person) -> bool:
    """Under 18 by the date of birth, marked as a minor at the door of an event, a campero under the
    event's adult age with no date of birth, or with a guardian's authorization on record (in force or not)."""
    from agenda.models import EventRegistration, GuardianConsent

    return (
        _is_minor_by_date(person)
        or services.was_minor_at_an_event(person)
        or EventRegistration.objects.filter(person=person, guest_is_minor=True).exists()
        or GuardianConsent.objects.filter(registration__person=person).exists()
    )


def _display(person: Person, name: str) -> str:
    if name == "preferred_contact_method":
        return person.get_preferred_contact_method_display()
    return getattr(person, name)


def _same(name: str, a: str, b: str) -> bool:
    if name == "email":
        return duplicates.normal_email(a) == duplicates.normal_email(b)
    if name == "phone":
        return a.strip() == b.strip() or (bool(duplicates.normal_phone(a)) and duplicates.normal_phone(a) == duplicates.normal_phone(b))
    return a == b


def _token(survivor: Person, absorbed: Person) -> str:
    facts = [
        [
            person.pk, person.first_name, person.last_name, person.email, person.phone, person.preferred_contact_method,
            person.birth_date.isoformat() if person.birth_date else None,
            person.birth_date_verified_at.isoformat() if person.birth_date_verified_at else None,
            person.user_id, person.merged_into_id,
        ]
        for person in (survivor, absorbed)
    ]
    return hashlib.sha256(json.dumps(facts).encode()).hexdigest()


def _digest(preview: Preview) -> str:
    """Everything the preview states, so that a confirmation is of what was read: the two records, every
    row that moves (by id), what stays, the camp links, the account and the roles an account gains."""
    stated = [
        _token(preview.survivor, preview.absorbed), sorted(preview.moves.items()), sorted(preview.stays.items()),
        [link.pk for link in preview.camp_links], preview.demote, preview.close_requests, preview.account,
        preview.groups_gained, preview.teams_gained, preview.signals, preview.sibling_warning, preview.refusals,
    ]
    return hashlib.sha256(json.dumps(stated).encode()).hexdigest()


def undoable_until(merge: PersonMerge):
    return merge.merged_at + timedelta(days=UNDO_DAYS)


def can_undo(merge: PersonMerge, now=None) -> bool:
    return merge.undone_at is None and (now or timezone.now()) < undoable_until(merge)


def build_preview(church, survivor: Person, absorbed: Person) -> Preview:
    """What a merge of ``absorbed`` into ``survivor`` would do, and every reason it may not happen.
    Reads only; the same function decides under the lock in ``merge``."""
    preview = _build(church, survivor, absorbed)
    preview.token = _digest(preview)
    return preview


def _build(church, survivor: Person, absorbed: Person) -> Preview:
    preview = Preview(survivor, absorbed, [])
    refuse = preview.refusals.append
    if (
        survivor.pk == absorbed.pk or survivor.church_id != church.pk or absorbed.church_id != church.pk
        or survivor.merged_into_id is not None or absorbed.merged_into_id is not None
    ):
        refuse("Estos dos registros ya no se pueden unir: alguno ya fue unido o no es de esta iglesia.")
        return preview
    # The same rule as the list of the review: what is never suggested is never merged.
    preview.signals = duplicates.suggested_signals(church, survivor.pk, absorbed.pk)
    if duplicates.is_dismissed(church, survivor.pk, absorbed.pk):
        refuse("Alguien de la administración dijo que no son la misma persona. Si fue un error, vuelve a poner el par en revisión desde «Posibles duplicados».")
    elif not preview.signals:
        refuse(
            "Nada indica que sean la misma persona: no comparten correo, teléfono, ni nombre con fecha de nacimiento, "
            "o lo que comparten lo tienen muchas personas más. Si lo son, corrige primero sus datos."
        )

    # ---- the account
    if survivor.user_id and absorbed.user_id:
        refuse(
            "Los dos registros tienen acceso a Emmaus, cada uno con su cuenta. Decidir con cuál se entra necesita a una persona: "
            "por ahora no se pueden unir."
        )
    elif absorbed.user_id:
        preview.account = "moves"
    elif survivor.user_id:
        preview.account = "survivor"

    # ---- age: a merge never writes a date of birth, and never changes who counts as a minor
    born_a, born_b = survivor.birth_date, absorbed.birth_date
    same_date = born_a is not None and born_a == born_b
    protected = _may_be_minor_or_has_consent(survivor) or _may_be_minor_or_has_consent(absorbed)
    if born_a is not None and born_b is not None and born_a != born_b:
        refuse("Tienen fechas de nacimiento distintas. Si son la misma persona, corrige primero la que esté mal en «Editar los datos».")
    elif not same_date and protected:
        refuse(
            "Alguno de los dos registros es o pudo ser de una persona menor de edad, o tiene la autorización de su tutor, y no tienen escrita la misma fecha de "
            "nacimiento. Para no cambiar quién cuenta como menor ni llevar una autorización a otra persona, esta unión no se puede hacer."
        )
    elif born_a is None and born_b is not None:
        refuse("Solo el registro que se uniría tiene fecha de nacimiento, y una unión no la copia. Deja como el registro que se queda al que sí la tiene.")
    elif same_date and absorbed.birth_date_verified and not survivor.birth_date_verified:
        refuse("Solo el registro que se uniría tiene la edad confirmada por el equipo, y una unión no copia esa confirmación. Deja como el registro que se queda al que sí la tiene.")

    # ---- siblings: twins have the same date of birth and often a parent's phone and e-mail
    if protected and same_date and NAME_BIRTH not in preview.signals:
        if normalize_name(survivor.first_name) != normalize_name(absorbed.first_name):
            refuse(
                "Son menores de edad (o tienen la autorización de su tutor), nacieron el mismo día y no se llaman igual: "
                "pueden ser hermanos que comparten el teléfono o el correo de su mamá o su papá. Esta unión no se puede hacer."
            )
        else:
            preview.sibling_warning = True

    # ---- an earlier merge that can still be undone
    if any(can_undo(earlier) for earlier in PersonMerge.objects.filter(survivor=absorbed, undone_at__isnull=True)):
        refuse(f"El registro que se uniría recibió otra unión hace menos de {UNDO_DAYS} días. Mientras esa se pueda deshacer, no se puede unir a otra persona.")

    # ---- the rows
    for table in TABLES:
        mine = table.rows().filter(**{table.field: absorbed.pk})
        ids = sorted(mine.values_list("pk", flat=True))
        conflict: list[int] = []
        if ids and table.unique_with is not None:
            theirs = table.rows().filter(**{table.field: survivor.pk})
            in_scope = set(ids)
            if table.scope is not None:
                theirs = theirs.filter(table.scope)
                in_scope = set(mine.filter(table.scope).values_list("pk", flat=True))
            if not table.unique_with:
                conflict = ids if theirs.exists() else []
            else:
                taken = set(theirs.values_list(*table.unique_with))
                conflict = [row[0] for row in mine.values_list("pk", *table.unique_with) if row[0] in in_scope and tuple(row[1:]) in taken]
        if conflict:
            staying = table.stays() if table.stays is not None else None
            allowed = mine.filter(pk__in=conflict).filter(staying).count() if staying is not None else 0
            if allowed != len(conflict):
                refuse(table.refusal)
            preview.stays[table.key] = len(conflict)
        held = set(conflict)
        preview.moves[table.key] = [pk for pk in ids if pk not in held]

    # A pending request to a group the other record is already in is not moved: it is closed with the
    # group's own service (nobody is left asking to join a group they are in), and counted with what stays.
    from groups.models import GroupJoinRequest, GroupMembership

    requests_key, members_key = "groups.GroupJoinRequest.person", "groups.GroupMembership.person"
    in_groups = set(GroupMembership.objects.filter(person=survivor, status="activa").values_list("group_id", flat=True))
    arriving_groups = set(
        GroupMembership.objects.filter(pk__in=preview.moves[members_key], status="activa").values_list("group_id", flat=True)
    )
    closing = set(GroupJoinRequest.objects.filter(pk__in=preview.moves[requests_key], status="pendiente", group_id__in=in_groups)
                  .values_list("pk", flat=True))
    preview.moves[requests_key] = [pk for pk in preview.moves[requests_key] if pk not in closing]
    closing |= set(GroupJoinRequest.objects.filter(person=survivor, status="pendiente", group_id__in=arriving_groups)
                   .values_list("pk", flat=True))
    preview.close_requests = sorted(closing)
    if closing:
        preview.stays[requests_key] = preview.stays.get(requests_key, 0) + len(closing)

    # A second primary sede cannot arrive as primary.
    arriving = preview.moves["people.CampusAffiliation.person"]
    if arriving and CampusAffiliation.objects.filter(person=survivor, is_primary=True).exists():
        preview.demote = sorted(CampusAffiliation.objects.filter(pk__in=arriving, is_primary=True).values_list("pk", flat=True))

    # ---- the camp: never an answer, only the link, and only through its own services
    preview.camp_links = list(EventParticipantLink.objects.filter(person=absorbed).select_related("event", "participant").order_by("pk"))
    survivor_events = set(EventParticipantLink.objects.filter(person=survivor).values_list("event_id", flat=True))
    if any(link.event_id in survivor_events for link in preview.camp_links):
        refuse(
            "Los dos registros están vinculados con un campero del mismo evento. Dos camperos de un evento no pueden ser una sola persona "
            "sin que alguien revise: quita primero el vínculo que esté mal."
        )
    elif any(not services.event_is_over(link.event) for link in preview.camp_links):
        refuse("El registro que se uniría está vinculado con un evento que no ha terminado. Podrás unirlos cuando termine.")

    # ---- a module nobody decided about
    both = [survivor.pk, absorbed.pk]
    for label, name in undecided_relations():
        if apps.get_model(label)._base_manager.filter(**{f"{name}__in": both}).exists():
            refuse("Alguno de los dos registros tiene datos de una parte de Emmaus que todavía no sabemos unir con seguridad. Esta unión no se puede hacer por ahora.")
            break

    # ---- what the survivor keeps, field by field
    preview.fields.append(FieldRow("name", "Nombre", survivor.full_name, absorbed.full_name,
                                   "same" if survivor.full_name == absorbed.full_name else "keeps"))
    labels = {"email": "Correo", "phone": "Teléfono", "preferred_contact_method": "Medio de contacto preferido"}
    for name in CONTACT_FIELDS:
        own, other = getattr(survivor, name), getattr(absorbed, name)
        if not other or _same(name, own, other):
            state = "same" if other else "keeps"
        elif not own:
            state = "fills"
        else:
            state = "choose"
        preview.fields.append(FieldRow(name, labels[name], _display(survivor, name), _display(absorbed, name), state))

    # ---- roles: derived from the memberships, never copied. Said before, because an account gains them.
    if preview.account != "none":
        from groups.models import GroupMembership
        from serving.models import TeamMembership

        # The memberships that reach an account they did not have: the ones that arrive, or the survivor's own.
        gaining = Q(pk__in=preview.moves["groups.GroupMembership.person"]) if preview.account == "survivor" else Q(person=survivor)
        preview.groups_gained = GroupMembership.objects.filter(gaining, role="lider", status="activa", group__status="activo").count()
        gaining = Q(pk__in=preview.moves["serving.TeamMembership.person"]) if preview.account == "survivor" else Q(person=survivor)
        preview.teams_gained = TeamMembership.objects.filter(gaining, role="coordina", status="activa", team__status="activo").count()
    return preview


# ---- the projection -------------------------------------------------------------------------------
def _team_ids(person_ids: list[int]) -> list[int]:
    from serving.models import TeamMembership

    return sorted(set(TeamMembership.objects.filter(person_id__in=person_ids).values_list("team_id", flat=True)))


def _lock_teams(person_ids: list[int]) -> list[int]:
    """Before the people (the module's lock order). Returns the ids of the teams it locked."""
    from serving.models import Team

    team_ids = _team_ids(person_ids)
    list(Team.objects.select_for_update().filter(pk__in=team_ids).order_by("pk"))
    return team_ids


def _teams_still(locked: list[int], person_ids: list[int]) -> None:
    """With the people locked nobody can add a membership of theirs any more. One added between the two
    locks would be in a team that is not locked: nothing is written, and the caller asks again."""
    if not set(_team_ids(person_ids)) <= set(locked):
        raise Busy


def _resettle(people: list[Person], actor) -> int:
    """How many registrations changed where they stand: each person's rows, by that person's own age facts."""
    from agenda import services as agenda

    return sum(agenda.reevaluate_person(person, actor) for person in people)


def rebuild_roles(person_id: int) -> None:
    """Re-derive every ``access.RoleAssignment`` row of one person from the memberships, with the
    sources' own writers. The caller holds the person's lock and the teams' (``_lock_teams``)."""
    from groups import services as groups
    from groups.models import GroupMembership
    from serving import services as serving
    from serving.models import TeamMembership

    for membership in GroupMembership.objects.filter(person_id=person_id).order_by("group_id", "pk"):
        groups.sync_assignment(membership)
    for membership in TeamMembership.objects.filter(person_id=person_id).order_by("team_id", "pk"):
        serving.sync_assignment(membership)


def _locked_pair(church, first_id: int, second_id: int) -> tuple[Person | None, Person | None]:
    """Both rows locked in id order, as the database has them now, and only if they are of ``church``."""
    locked = {
        person.pk: person
        for person in Person.objects.select_for_update().filter(pk__in=[first_id, second_id], church=church).order_by("pk")
    }
    return locked.get(first_id), locked.get(second_id)


# ---- merge ----------------------------------------------------------------------------------------
GONE = "Estos dos registros ya no se pueden unir: alguien más ya unió alguno, o no son de esta iglesia."


def merge(church, survivor_id: int, absorbed_id: int, actor, *, choices: dict[str, int], token: str,
          confirmed_siblings: bool = False) -> PersonMerge:
    """Join ``absorbed`` into ``survivor``. ``choices``: for each contact field the two disagree on,
    the id of the record whose value stays (never a typed value). ``token``: the preview the
    administrator confirmed. ``confirmed_siblings``: the administrator said apart that they are not two
    siblings, when the preview asked. Raises ``MergeRefused``, ``StalePreview`` or ``Busy``; then nothing was written."""
    if survivor_id == absorbed_id:
        raise MergeRefused([GONE])
    try:
        with transaction.atomic():
            teams = _lock_teams([survivor_id, absorbed_id])
            survivor, absorbed = _locked_pair(church, survivor_id, absorbed_id)
            if survivor is None or absorbed is None or survivor.merged_into_id is not None or absorbed.merged_into_id is not None:
                raise MergeRefused([GONE])
            _teams_still(teams, [survivor.pk, absorbed.pk])
            # Read again under the locks: these are the rows that move, whatever the screen had listed.
            preview = build_preview(church, survivor, absorbed)
            if not preview.allowed:
                raise MergeRefused(preview.refusals)
            if token != preview.token:
                raise StalePreview
            if preview.sibling_warning and not confirmed_siblings:
                raise MergeRefused(["Confirma que no son dos hermanos: marca la casilla y vuelve a intentarlo."])
            changes = {}
            for row in preview.fields:
                if row.state == "fills":
                    changes[row.name] = getattr(absorbed, row.name)
                elif row.state == "choose":
                    if choices.get(row.name) not in (survivor.pk, absorbed.pk):
                        raise MergeRefused([f"Elige cuál {row.label.lower()} se queda."])
                    if choices[row.name] == absorbed.pk:
                        changes[row.name] = getattr(absorbed, row.name)

            manifest = {
                "version": 1,
                "moved": {},
                "camp_participants": [],
                "demoted_affiliations": preview.demote,
                "moved_user_id": None,
                "closed_requests": [],
                "fields_changed": sorted(changes),
                # The only contact data kept, and only while the undo is possible (``clear_expired``).
                "survivor_before": {name: getattr(survivor, name) for name in sorted(changes)},
            }
            if preview.demote:
                CampusAffiliation.objects.filter(pk__in=preview.demote, person=absorbed).update(is_primary=False)
            for table in TABLES:
                ids = preview.moves[table.key]
                if ids:
                    table.rows().filter(pk__in=ids, **{table.field: absorbed.pk}).update(**{table.field: survivor.pk})
                    manifest["moved"][table.key] = ids
            for link in preview.camp_links:
                participant = link.participant
                services.unlink_participant(link, actor)
                services.link_participant(survivor, participant, actor)
                manifest["camp_participants"].append(participant.pk)
            if preview.close_requests:
                from groups.models import GroupJoinRequest

                # Closed here and not with the member's "retirar mi solicitud": nobody withdrew anything, and the
                # activity log must not say so. Ids only, as every row of this module.
                pending = GroupJoinRequest.objects.select_for_update().filter(pk__in=preview.close_requests, status="pendiente")
                for request in pending.order_by("pk"):
                    request.status = GroupJoinRequest.Status.WITHDRAWN
                    request.decided_at = timezone.now()
                    request.save(update_fields=["status", "decided_at"])
                    record_church(church, actor, "grupo_solicitud_cerrada_union",
                                  "Se cerró una solicitud a un grupo porque se unieron dos registros de la misma persona", request,
                                  group_id=request.group_id, person_id=request.person_id)
                manifest["closed_requests"] = preview.close_requests

            # The absorbed record lets go of its account before the survivor takes it (one person per church and account).
            if preview.account == "moves":
                manifest["moved_user_id"] = absorbed.user_id
                absorbed.user = None
            absorbed.merged_into = survivor
            absorbed.save(update_fields=["user", "merged_into", "updated_at"])
            written = list(changes)
            for name, value in changes.items():
                setattr(survivor, name, value)
            if manifest["moved_user_id"] is not None:
                survivor.user_id = manifest["moved_user_id"]
                written.append("user")
            if written:
                survivor.save(update_fields=[*written, "updated_at"])
            rebuild_roles(absorbed.pk)
            rebuild_roles(survivor.pk)
            # The rows that arrived stand now by the survivor's age facts (``agenda.services.standing``).
            manifest["resettled"] = _resettle([survivor, absorbed], actor)

            record = PersonMerge.objects.create(church=church, survivor=survivor, absorbed=absorbed, merged_by=actor, manifest=manifest)
            record_church(church, actor, "persona_union", "Unió dos registros de la misma persona", record,
                          person_id=survivor.pk, other_person_id=absorbed.pk, merge_id=record.pk)
            return record
    except IntegrityError as error:  # a rule of the database said no: nothing was written
        raise MergeRefused(["No se pudo hacer la unión: algo cambió mientras la confirmabas. Revisa de nuevo."]) from error
    except MergeRefused:
        raise
    except PeopleError as error:  # the camp link's own rules
        raise MergeRefused([error.message]) from error


# ---- undo -----------------------------------------------------------------------------------------
def undo(church, merge_id: int, actor) -> PersonMerge:
    """"Deshacer la unión", within ``UNDO_DAYS``: the absorbed record is a person again and gets back
    the rows the merge moved that still exist and still point at the survivor. What cannot be put
    back is counted in ``undo_report``; rows the survivor gained since then stay with the survivor."""
    with transaction.atomic():
        record = PersonMerge.objects.select_for_update(of=("self",)).filter(pk=merge_id, church=church).first()
        if record is None:
            raise MergeRefused(["Esta unión no existe."])
        if record.undone_at is not None:
            raise MergeRefused(["Esta unión ya se había deshecho."])
        now = timezone.now()
        if not can_undo(record, now):
            raise MergeRefused([f"Pasaron más de {UNDO_DAYS} días: esta unión ya no se puede deshacer."])
        teams = _lock_teams([record.survivor_id, record.absorbed_id])
        survivor, absorbed = _locked_pair(church, record.survivor_id, record.absorbed_id)
        if survivor is None or absorbed is None or absorbed.merged_into_id != survivor.pk:
            raise MergeRefused(["Los registros de esta unión cambiaron y ya no se puede deshacer."])
        if survivor.merged_into_id is not None:
            raise MergeRefused(["El registro que se quedó se unió después a otro. Deshaz primero esa unión."])
        _teams_still(teams, [survivor.pk, absorbed.pk])
        manifest = record.manifest
        report: dict = {"not_restored": {}, "private_not_restored": False, "account_not_restored": False, "fields_not_restored": [],
                        "consent_kept": 0, "resettled": 0}

        # The account goes back only if the survivor still holds the same one.
        moved_user_id = manifest.get("moved_user_id")
        survivor_writes = []
        if moved_user_id is not None:
            if survivor.user_id == moved_user_id:
                survivor.user = None
                survivor_writes.append("user")
            else:
                moved_user_id = None
                report["account_not_restored"] = True
        # A contact value goes back only if the survivor still has the one the merge gave it.
        before = manifest.get("survivor_before") or {}
        for name in manifest.get("fields_changed", []):
            if name in before and getattr(survivor, name) == getattr(absorbed, name):
                setattr(survivor, name, before[name])
                survivor_writes.append(name)
            else:
                report["fields_not_restored"].append(name)
        if survivor_writes:
            survivor.save(update_fields=[*survivor_writes, "updated_at"])
        absorbed.merged_into = None
        if moved_user_id is not None:
            absorbed.user_id = moved_user_id
        absorbed.save(update_fields=["user", "merged_into", "updated_at"])

        for key, ids in manifest.get("moved", {}).items():
            table = _BY_KEY.get(key)
            restored = 0
            if key == "agenda.EventRegistration.person":
                # An authorization a guardian gave AFTER the merge was given for the person the team had in front
                # of it, the survivor: that registration stays with the survivor, it never travels to the other record.
                from agenda.models import GuardianConsent

                kept = set(GuardianConsent.objects.filter(registration_id__in=ids, registration__person=survivor,
                                                          recorded_at__gte=record.merged_at).values_list("registration_id", flat=True))
                report["consent_kept"] = len(kept)
                ids = [pk for pk in ids if pk not in kept]
            if table is not None:
                restored = _move_back(table, ids, survivor.pk, absorbed.pk)
            if restored != len(ids):
                if table is not None and table.private:
                    report["private_not_restored"] = True
                else:
                    report["not_restored"][key] = len(ids) - restored
        demoted = manifest.get("demoted_affiliations") or []
        if demoted and not CampusAffiliation.objects.filter(person=absorbed, is_primary=True).exists():
            again = CampusAffiliation.objects.filter(pk__in=demoted, person=absorbed, is_primary=False).order_by("pk").first()
            if again is not None:
                CampusAffiliation.objects.filter(pk=again.pk).update(is_primary=True)

        lost = 0
        for participant_id in manifest.get("camp_participants", []):
            link = EventParticipantLink.objects.filter(participant_id=participant_id, person=survivor).select_related("participant", "event", "person").first()
            if link is None:
                lost += 1
                continue
            try:
                with transaction.atomic():
                    participant = link.participant
                    services.unlink_participant(link, actor)
                    services.link_participant(absorbed, participant, actor)
            except PeopleError:
                lost += 1
        if lost:
            report["not_restored"][CAMP_KEY] = lost

        # The requests the merge closed are pending again, if nobody touched them and the rule "one pending" lets them.
        closed = manifest.get("closed_requests") or []
        if closed:
            from groups.models import GroupJoinRequest

            reopened = 0
            for pk in closed:
                try:
                    with transaction.atomic():
                        reopened += GroupJoinRequest.objects.filter(
                            pk=pk, status="retirada", decided_by__isnull=True, person_id__in=[survivor.pk, absorbed.pk]
                        ).update(status="pendiente", decided_at=None)
                except IntegrityError:
                    continue
            if reopened != len(closed):
                key = "groups.GroupJoinRequest.person"
                report["not_restored"][key] = report["not_restored"].get(key, 0) + len(closed) - reopened

        rebuild_roles(survivor.pk)
        rebuild_roles(absorbed.pk)
        # Each record's registrations stand by its own age facts again: an age the team confirmed for the
        # survivor after the merge confirms nothing for the record that comes back.
        report["resettled"] = _resettle([survivor, absorbed], actor)
        manifest["survivor_before"] = {}
        record.manifest = manifest
        record.contact_cleared_at = now
        record.undone_at = now
        record.undone_by = actor
        record.undo_report = report
        record.save(update_fields=["manifest", "contact_cleared_at", "undone_at", "undone_by", "undo_report"])
        record_church(church, actor, "persona_union_deshecha", "Deshizo la unión de dos registros de persona", record,
                      person_id=survivor.pk, other_person_id=absorbed.pk, merge_id=record.pk)
        return record


def _move_back(table: Table, ids: list[int], survivor_id: int, absorbed_id: int) -> int:
    """How many of ``ids`` went back. Only rows that still point at the survivor are touched; a row that
    cannot return (the absorbed record's rule "one per …" refuses it) stays with the survivor."""
    rows = table.rows().filter(pk__in=ids, **{table.field: survivor_id})
    try:
        with transaction.atomic():
            return rows.update(**{table.field: absorbed_id})
    except IntegrityError:
        restored = 0
        for pk in rows.values_list("pk", flat=True):
            try:
                with transaction.atomic():
                    restored += table.rows().filter(pk=pk, **{table.field: survivor_id}).update(**{table.field: absorbed_id})
            except IntegrityError:
                continue
        return restored


def undo_report_lines(record: PersonMerge) -> list[str]:
    """What the undo could not put back, as sentences. Private marks are named without a number."""
    report = record.undo_report or {}
    lines = Preview._counted(report.get("not_restored", {}))
    if report.get("account_not_restored"):
        lines.append("el acceso a Emmaus: la cuenta ya no estaba en la persona que se quedó")
    labels = {"email": "el correo", "phone": "el teléfono", "preferred_contact_method": "el medio de contacto preferido"}
    for name in report.get("fields_not_restored", []):
        lines.append(f"{labels.get(name, name)} de la persona que se quedó: alguien lo cambió después de la unión, o ya se había borrado el dato anterior")
    if report.get("private_not_restored"):
        lines.append("algunas de sus marcas privadas de lectura")
    kept = report.get("consent_kept") or 0
    if kept:
        lines.append(
            f"{kept} inscripción a un evento, porque" if kept == 1 else f"{kept} inscripciones a eventos, porque"
        )
        lines[-1] += " después de la unión se registró la autorización de su tutor: esa autorización es de la persona que se quedó"
    return lines


def undo_change_lines(record: PersonMerge) -> list[str]:
    """What the undo changed besides moving rows back: registrations that wait again."""
    changed = (record.undo_report or {}).get("resettled") or 0
    if not changed:
        return []
    one = "1 inscripción a un evento cambió de estado: se revisó con la edad y la autorización que tiene cada registro por separado"
    many = f"{changed} inscripciones a eventos cambiaron de estado: se revisaron con la edad y la autorización que tiene cada registro por separado"
    return [one if changed == 1 else many]


# ---- dismissals -----------------------------------------------------------------------------------
def dismiss(church, a_id: int, b_id: int, actor) -> DuplicateDismissal:
    """"No son la misma persona". Both must be active people of ``church``; saying it twice is one row."""
    if a_id == b_id:
        raise MergeRefused([GONE])
    low, high = duplicates.ordered(a_id, b_id)
    with transaction.atomic():
        first, second = _locked_pair(church, low, high)
        if first is None or second is None or first.merged_into_id is not None or second.merged_into_id is not None:
            raise MergeRefused([GONE])
        row, created = DuplicateDismissal.objects.get_or_create(
            person_a=first, person_b=second, defaults={"church": church, "dismissed_by": actor}
        )
        if created:
            record_church(church, actor, "persona_duplicado_descartado", "Dijo que dos registros no son la misma persona", row,
                          person_id=low, other_person_id=high)
    return row


def reopen(church, dismissal_id: int, actor) -> None:
    """Put a dismissed pair back in the review (somebody dismissed it by mistake)."""
    with transaction.atomic():
        row = DuplicateDismissal.objects.select_for_update().filter(pk=dismissal_id, church=church).first()
        if row is None:
            raise MergeRefused(["Este par ya no está descartado."])
        low, high = row.person_a_id, row.person_b_id
        record_church(church, actor, "persona_duplicado_reabierto", "Volvió a poner en revisión dos registros que había descartado", row,
                      person_id=low, other_person_id=high)
        row.delete()


# ---- retention ------------------------------------------------------------------------------------
def clear_expired(now=None, *, apply: bool = False) -> int:
    """Empty the contact values of the manifests whose undo is no longer possible. Returns how many
    merges that is (or would be, without ``apply``). For EVERY church, whatever its module flags: it
    only deletes data nothing needs any more, and a church that switched the module off must not keep
    it for that. The scheduler's job ``uniones_personas`` calls it on every pass;
    ``prune_person_merges`` runs the same by hand."""
    now = now or timezone.now()
    expired = PersonMerge.objects.filter(contact_cleared_at__isnull=True, merged_at__lte=now - timedelta(days=UNDO_DAYS))
    if not apply:
        return expired.count()
    cleared = 0
    with transaction.atomic():
        for record in expired.select_for_update().order_by("pk"):
            record.manifest = {**record.manifest, "survivor_before": {}}
            record.contact_cleared_at = now
            record.save(update_fields=["manifest", "contact_cleared_at"])
            cleared += 1
    return cleared
