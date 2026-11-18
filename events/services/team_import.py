"""Importing the event team from a CSV (issue #50): name, e-mail, role, team, tutor.

Three rules the file can never bend:

* A row only ever asks for an event role of *this* event: coordinador, tutor or pastor. It cannot make an
  Administrador, cannot grant confidential access (``can_view_sensitive`` is always False here, whatever columns
  the file carries) and never touches a church role.
* It is idempotent by e-mail: an account that already has any access to the event, or whose access was removed,
  is left exactly as it is. Changing a role or giving access back is "Editar", one person at a time, on purpose.
* Nothing is written while reviewing. The session keeps only the file's cells; ``plan`` reads them against the
  database every time, for the preview and again inside the confirmation's transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from ..access import resolve_access
from ..models import Event, EventMembership, MembershipRole, normalize_name
from . import audit
from . import team as team_svc
from .participants import FORMULA_STARTS, csv_reader

TEAM_COLUMNS = ["Nombre", "Correo", "Rol", "Equipo", "Tutor"]
HEADER_ALIASES = {
    "full_name": {"nombre", "nombre completo", "integrante", "persona"},
    "email": {"correo", "correo electronico", "correo electrónico", "email", "e-mail", "mail"},
    "role": {"rol", "funcion", "función", "puesto"},
    "team": {"equipo", "nombre del equipo"},
    "tutor": {"tutor", "tutora", "tutor que lo supervisa", "correo del tutor"},
}
REQUIRED = [("full_name", "Nombre"), ("email", "Correo"), ("role", "Rol")]
# The roles a file may give. "Administrador" is not one of them: see the module docstring.
ROLE_WORDS = {
    "coordinador": MembershipRole.COORDINATOR, "coordinadora": MembershipRole.COORDINATOR,
    "tutor": MembershipRole.TUTOR, "tutora": MembershipRole.TUTOR,
    "pastor": MembershipRole.PASTOR, "pastora": MembershipRole.PASTOR,
}
ADMIN_WORDS = {"administrador", "administradora", "admin", "administracion"}
MAX_TEAM_ROWS = 200
MAX_TEAM_BYTES = 1024 * 1024
MAX_CELL = 200  # a cell kept in the session; longer ones are cut, and the name or e-mail then fails its own check

NEW, UNCHANGED = "nuevo", "sin_cambios"


@dataclass
class TeamRow:
    line: int
    full_name: str = ""
    email: str = ""
    role: str = ""
    team_name: str = ""
    tutor_label: str = ""
    tutor_membership_id: int | None = None  # a tutor already in the team
    tutor_email: str = ""  # or one that this same file adds
    has_account: bool = False
    status: str = NEW
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def is_new(self) -> bool:
        return not self.errors and self.status == NEW

    @property
    def is_unchanged(self) -> bool:
        return not self.errors and self.status == UNCHANGED

    @property
    def role_label(self) -> str:
        return MembershipRole(self.role).label if self.role else ""


def _canonical_headers(fieldnames) -> dict[str, str]:
    mapping = {}
    for raw in fieldnames or []:
        key = normalize_name(raw or "").replace("_", " ")
        for target, aliases in HEADER_ALIASES.items():
            if key in {normalize_name(a) for a in aliases}:
                mapping[raw] = target
    return mapping


def read_file(uploaded_bytes: bytes) -> tuple[list[dict], list[str]]:
    """The file's cells, trimmed, and the problems that stop the whole file. No database, nothing written."""
    reader = csv_reader(uploaded_bytes)
    headers = _canonical_headers(reader.fieldnames)
    missing = [label for key, label in REQUIRED if key not in headers.values()]
    if missing:
        return [], ["Faltan estas columnas: " + ", ".join(missing) + "."]
    rows: list[dict] = []
    for index, raw in enumerate(reader, start=2):
        values = {headers[k]: " ".join((v or "").split())[:MAX_CELL] for k, v in raw.items() if k in headers}
        if not any(values.values()):
            continue
        if len(rows) >= MAX_TEAM_ROWS:
            return [], [f"El archivo tiene más de {MAX_TEAM_ROWS} personas. Divídelo en partes y súbelas una por una."]
        rows.append({"line": index, **{key: values.get(key, "") for key in HEADER_ALIASES}})
    if not rows:
        return [], ["El archivo no tiene filas con datos."]
    return rows, []


def _starts_like_formula(text: str) -> bool:
    return text.startswith(FORMULA_STARTS)


def _basic(raw: dict) -> TeamRow:
    row = TeamRow(line=int(raw["line"]))
    row.full_name = str(raw.get("full_name", ""))
    if not row.full_name:
        row.errors.append("Falta el nombre.")
    elif len(row.full_name) > 150:
        row.errors.append("El nombre es muy largo: escribe 150 caracteres o menos.")
    elif _starts_like_formula(row.full_name):
        row.errors.append("El nombre no puede empezar con =, +, - o @.")
    email = str(raw.get("email", "")).strip().lower()
    row.email = email
    if not email:
        row.errors.append("Falta el correo.")
    else:
        try:
            validate_email(email)
        except ValidationError:
            row.errors.append(f"«{email}» no es un correo válido. Escribe uno como nombre@ejemplo.com.")
        else:
            if len(email) > 150:  # it becomes the account's username
                row.errors.append("El correo es muy largo: usa uno de 150 caracteres o menos.")
    role_raw = str(raw.get("role", ""))
    role_key = normalize_name(role_raw)
    if role_key in ROLE_WORDS:
        row.role = ROLE_WORDS[role_key]
    elif not role_key:
        row.errors.append("Falta el rol: coordinador, tutor o pastor.")
    elif role_key in ADMIN_WORDS:
        row.errors.append("Un archivo no puede dar el rol de administrador. Agrega a esa persona en Equipo, Agregar persona.")
    else:
        row.errors.append(f"No conocemos el rol «{role_raw}». Usa coordinador, tutor o pastor.")
    return row


def plan(event: Event, raw_rows: list[dict]) -> list[TeamRow]:
    """What confirming would do with each row, read against the database as it is now. Writes nothing."""
    rows = [_basic(raw) for raw in raw_rows]
    raws = {int(raw["line"]): raw for raw in raw_rows}

    first_line: dict[str, int] = {}
    for row in rows:
        if not row.email:
            continue
        if row.email in first_line:
            row.errors.append(f"El correo está repetido: ya aparece en la fila {first_line[row.email]}.")
        else:
            first_line[row.email] = row.line

    for row in rows:
        if row.errors:
            continue
        team = str(raws[row.line].get("team", ""))
        if row.role == MembershipRole.TUTOR and team:
            if len(team) > 80:
                row.errors.append("El nombre del equipo es muy largo: escribe 80 caracteres o menos.")
            elif _starts_like_formula(team):
                row.errors.append("El nombre del equipo no puede empezar con =, +, - o @.")
            else:
                row.team_name = team
        if row.errors:
            continue
        user = team_svc.account_for_email(row.email)
        if user is None:
            continue
        row.has_account = True
        membership = EventMembership.objects.filter(event=event, user=user).first()
        if membership is not None and membership.is_active:
            row.status = UNCHANGED
            current = membership.get_role_display().lower()
            row.notes.append(f"Ya está en el equipo como {current}.")
            if membership.role != row.role:
                row.notes.append(f"El archivo dice {row.role_label.lower()}: si quieres cambiarlo, hazlo en Equipo, Editar.")
        elif membership is not None:
            row.status = UNCHANGED
            row.notes.append("Se le retiró el acceso a este evento. Si quieres devolvérselo, hazlo en Equipo, Editar.")
        else:
            access = resolve_access(user, event)
            if access is not None:  # a church administrator or pastor, or the system's administration
                row.status = UNCHANGED
                row.notes.append(f"Ya tiene acceso a este evento como {access.role_label.lower()}.")
            else:
                row.notes.append("Ya tiene cuenta en Emmaus: se usa esa cuenta y su nombre no cambia.")

    _resolve_tutors(event, rows, raws)
    return rows


def _resolve_tutors(event: Event, rows: list[TeamRow], raws: dict[int, dict]) -> None:
    """Each new coordinator's tutor: the "Tutor" cell (an e-mail, a full name or a team name), or else the "Equipo"
    cell (a team name). Looked up among the event's active tutors and the tutors this file adds."""
    by_email: dict[str, set] = {}
    by_name: dict[str, set] = {}
    by_team: dict[str, set] = {}
    labels: dict[tuple, str] = {}

    def offer(target: tuple, label: str, email: str, name: str, team: str) -> None:
        labels[target] = label
        if email:
            by_email.setdefault(email.lower(), set()).add(target)
        if normalize_name(name):
            by_name.setdefault(normalize_name(name), set()).add(target)
        if normalize_name(team):
            by_team.setdefault(normalize_name(team), set()).add(target)

    existing = EventMembership.objects.filter(event=event, role=MembershipRole.TUTOR, is_active=True).select_related("user")
    existing_emails = set()
    for tutor in existing:
        existing_emails.add((tutor.user.email or "").lower())
        offer(("id", tutor.id), tutor.display_name, tutor.user.email or "", tutor.display_name, tutor.team_name)
    for row in rows:
        if row.is_new and row.role == MembershipRole.TUTOR and row.email not in existing_emails:
            offer(("email", row.email), row.full_name, row.email, row.full_name, row.team_name)

    for row in rows:
        raw = raws[row.line]
        tutor_cell, team_cell = str(raw.get("tutor", "")), str(raw.get("team", ""))
        if row.errors or row.status != NEW:
            continue
        if row.role != MembershipRole.COORDINATOR:
            if tutor_cell:
                row.notes.append("La columna Tutor solo se usa con coordinadores: aquí no se toma en cuenta.")
            if team_cell and row.role != MembershipRole.TUTOR:
                row.notes.append("La columna Equipo solo se usa con tutores y coordinadores: aquí no se toma en cuenta.")
            continue
        reference = tutor_cell or team_cell
        if not reference:
            row.notes.append("Sin tutor por ahora: puedes asignarlo en Equipo, Asignar tutores.")
            continue
        key = normalize_name(reference)
        found = by_email.get(reference.lower()) or by_name.get(key) or by_team.get(key) or set()
        if tutor_cell and not found:
            row.errors.append(f"No encontramos al tutor «{reference}» ni en el equipo ni en este archivo.")
        elif not found:
            row.errors.append(f"No encontramos el equipo «{reference}» ni en el evento ni en este archivo.")
        elif len(found) > 1:
            names = ", ".join(sorted(labels[t] for t in found))
            row.errors.append(f"«{reference}» coincide con más de un tutor ({names}). Escribe el correo del tutor.")
        else:
            (kind, value), = found
            row.tutor_label = labels[(kind, value)]
            if kind == "id":
                row.tutor_membership_id = value
            else:
                row.tutor_email = value


@dataclass
class TeamImportResult:
    added: list = field(default_factory=list)  # team.AddedMember, in file order
    unchanged: int = 0
    without_tutor: list[str] = field(default_factory=list)  # coordinators whose tutor of the file was not imported


def apply(event: Event, raw_rows: list[dict], *, actor, include_lines: set[int]) -> TeamImportResult:
    """Add the new people of the marked rows, tutors first so a coordinator can name a tutor of the same file.

    Everything or nothing: one transaction. Each person goes through ``team.add_member`` (the path of "Agregar
    persona", with its audit row), never with confidential access. Rows planned as "sin cambios" are counted and
    not touched.
    """
    result = TeamImportResult()
    with transaction.atomic():
        # One import at a time per event: two administrators confirming the same file add each person once.
        Event.objects.select_for_update().filter(pk=event.pk).first()
        rows = plan(event, raw_rows)
        result.unchanged = sum(1 for r in rows if r.is_unchanged)
        chosen = [r for r in rows if r.is_new and r.line in include_lines]
        tutors_by_email: dict[str, EventMembership] = {}
        added_by_line: dict[int, team_svc.AddedMember] = {}
        for row in sorted(chosen, key=lambda r: r.role != MembershipRole.TUTOR):  # stable: tutors, then the rest
            tutor = None
            if row.role == MembershipRole.COORDINATOR:
                if row.tutor_membership_id:
                    tutor = EventMembership.objects.filter(
                        pk=row.tutor_membership_id, event=event, role=MembershipRole.TUTOR, is_active=True
                    ).first()
                elif row.tutor_email:
                    tutor = tutors_by_email.get(row.tutor_email)
                if tutor is None and (row.tutor_membership_id or row.tutor_email):
                    result.without_tutor.append(row.full_name)
            try:
                added = team_svc.add_member(
                    event, actor, full_name=row.full_name, email=row.email, role=row.role,
                    team_name=row.team_name, can_view_sensitive=False, tutor=tutor,
                )
            except team_svc.AlreadyMember:  # cannot happen under the lock; kept so a surprise never duplicates
                result.unchanged += 1
                continue
            added_by_line[row.line] = added
            if row.role == MembershipRole.TUTOR:
                tutors_by_email[row.email] = added.membership
        result.added = [added_by_line[line] for line in sorted(added_by_line)]
        count = len(result.added)
        if count:  # a file that adds nobody leaves no trace at all: importing it again changes nothing
            summary = ("Importó 1 integrante del equipo desde un archivo" if count == 1
                       else f"Importó {count} integrantes del equipo desde un archivo")
            audit.record(actor, event, "importacion_equipo", summary, None, count=count, sin_cambios=result.unchanged)
    return result
