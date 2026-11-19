"""Importing people from a CSV, reviewed before anything is written, and undoing an import.

What a file can never do, whoever uploads it (the views have already asked for church administration):

* It only ever creates people of the church of the request. It reads no other church: a row equal to a
  person of another church looks exactly like a new person.
* A row that matches a person the church already has is NOT created and changes nothing on that person.
  "Matches" is the conservative rule of ``people.duplicates``: the same e-mail, the same phone, or the
  same name with the same date of birth. The same rule finds rows repeated inside the file. Nothing is
  merged and nothing is updated: the administration does that by hand, one person at a time.
* It never creates an account, never sends an e-mail, and a date of birth it carries is a claim like one
  typed in "Registrar a una persona": ``Person.save`` never lets a new row arrive verified, so a minor
  created here stands exactly where one registered by hand stands (no account without the guardian's
  yes, no registration confirmed without a verified age).
* It never creates a tag: an unknown tag is an error of that row (a typo must not become a label).
* Nothing is written while reviewing, and the file is not stored anywhere: the preview carries the
  parsed cells back to the browser in a signed, compressed field (``pack``), and the confirmation
  brings them back. ``plan`` reads them against the database for the preview and again, under the
  church's lock, inside the confirmation's transaction; ``token`` covers what the preview said about
  every row, so a confirmation of something else writes nothing and shows the preview again.
* One audit row per import and per undo, with counts only.

Parsing is the camp's (``events.services.participants.csv_rows``: UTF-8 with or without BOM, or
Latin-1; comma, semicolon or tab) and so are the lenient header match and the formula rule.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.apps import apps
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import DataError, transaction
from django.utils import timezone

from core.services import full_name_to_parts
from events.models import normalize_name
from events.services.participants import FORMULA_STARTS, csv_rows

from . import duplicates
from .audit import record_church
from .models import ChurchSettings, PeopleImport, Person, PersonTag, Tag
from .services import PeopleError
from .text import has_control, normalized_fits

COLUMNS = ["Nombre", "Apellidos", "Correo", "Teléfono", "Fecha de nacimiento", "Etapa", "Etiquetas"]
HEADER_ALIASES = {
    "first_name": {"nombre", "nombres", "nombre(s)", "nombre de pila"},
    "full_name": {"nombre completo", "nombre y apellidos", "persona"},
    "last_name": {"apellidos", "apellido", "apellido(s)"},
    "email": {"correo", "correo electronico", "email", "e-mail", "mail"},
    "phone": {"telefono", "tel", "celular", "telefono celular", "whatsapp", "movil"},
    "birth_date": {"fecha de nacimiento", "nacimiento", "fecha nacimiento"},
    "stage": {"etapa"},
    "tags": {"etiquetas", "etiqueta"},
}
STAGE_WORDS = {normalize_name(label): value for value, label in Person.Stage.choices}
NORMALIZED_NAME_MAX = Person._meta.get_field("normalized_name").max_length
MAX_ROWS = 2000
MAX_BYTES = 1024 * 1024
MAX_CELL = 200  # a longer cell is an error of its row (never cut: a cut value can be valid and be somebody else's)
UNDO_HOURS = 24
PACK_SALT = "people.imports.rows"
PACK_MAX_AGE = 2 * 60 * 60  # a preview left open longer than this is uploaded again

NEW, EXISTS, REPEATED, ERROR = "nueva", "ya_existe", "repetida", "error"


class StalePreview(Exception):
    """What was confirmed is not what the file and the database say now: nothing was written."""


class BadPack(Exception):
    """The rows that came back with the confirmation are not the ones this server signed (or are too old)."""


# ---- reading the file -----------------------------------------------------------------------------
def _canonical_headers(fieldnames) -> dict[str, str]:
    """The camp imports' lenient match: case, accents, extra spaces and underscores do not matter."""
    mapping = {}
    for raw in fieldnames or []:
        key = normalize_name(raw or "").replace("_", " ")
        for target, aliases in HEADER_ALIASES.items():
            if key in {normalize_name(alias) for alias in aliases}:
                mapping[raw] = target
    return mapping


def read_file(uploaded_bytes: bytes) -> tuple[list[dict], list[str]]:
    """The file's cells, trimmed, and the problems that stop the whole file. No database, nothing written."""
    if len(uploaded_bytes) > MAX_BYTES:
        return [], ["El archivo es muy grande. El máximo es 1 MB."]
    fieldnames, file_rows, unreadable = csv_rows(uploaded_bytes)
    if unreadable:
        return [], [unreadable]
    headers = _canonical_headers(fieldnames)
    targets = set(headers.values())
    if "first_name" not in targets and "full_name" not in targets:
        return [], ["Falta la columna Nombre. La primera fila del archivo debe tener los nombres de las columnas."]
    rows: list[dict] = []
    for index, raw in enumerate(file_rows, start=2):
        values, unfit = {}, []
        for key, value in raw.items():
            if key not in headers:
                continue
            value = (value if isinstance(value, str) else "").strip(" ")
            # A cell that cannot be stored as it is is never cleaned or cut: its row is refused, and the cell is
            # not carried to the confirmation. ``_basic`` turns the code into the sentence of the preview.
            if has_control(value):
                unfit.append("control")
                value = ""
            elif len(value) > MAX_CELL:
                unfit.append("long")
                value = ""
            values[headers[key]] = re.sub(r" {2,}", " ", value)
        if not any(values.values()) and not unfit:
            continue
        if len(rows) >= MAX_ROWS:
            return [], [f"El archivo tiene más de {MAX_ROWS:,} filas con datos. Divídelo en partes y súbelas una por una.".replace(",", " ")]
        rows.append({"line": index, **{key: values.get(key, "") for key in HEADER_ALIASES}, "unfit": sorted(set(unfit))})
    if not rows:
        return [], ["El archivo no tiene filas con datos."]
    return rows, []


def pack(church, user, rows: list[dict]) -> str:
    """The parsed cells, signed and compressed, for the confirmation to bring back. Not stored on the server."""
    return signing.dumps({"church": church.pk, "user": user.pk, "rows": rows}, salt=PACK_SALT, compress=True)


def unpack(church, user, packed: str) -> list[dict]:
    try:
        payload = signing.loads(packed or "", salt=PACK_SALT, max_age=PACK_MAX_AGE)
    except signing.BadSignature as error:  # also an expired one
        raise BadPack from error
    if payload.get("church") != church.pk or payload.get("user") != user.pk or not isinstance(payload.get("rows"), list):
        raise BadPack  # signed for another church or another account: never this church's import
    return payload["rows"]


# ---- the plan -------------------------------------------------------------------------------------
@dataclass
class Row:
    line: int
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    birth_date: date | None = None
    stage: str = ""
    tag_ids: list[int] = field(default_factory=list)
    tag_names: list[str] = field(default_factory=list)
    status: str = NEW
    errors: list[str] = field(default_factory=list)
    reason: str = ""  # why it is not created: the signal, in words
    match: Person | None = None  # the person of THIS church it matches
    same_as_line: int | None = None
    is_minor: bool = False

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def stage_label(self) -> str:
        return Person.Stage(self.stage).label if self.stage else ""


@dataclass
class Plan:
    rows: list[Row]
    token: str = ""

    def of(self, status: str) -> list[Row]:
        return [row for row in self.rows if row.status == status]

    @property
    def new(self) -> list[Row]:
        return self.of(NEW)

    @property
    def minors(self) -> int:
        return sum(1 for row in self.new if row.is_minor)


def _starts_like_formula(text: str) -> bool:
    return text.startswith(FORMULA_STARTS)


def _unquoted(text: str) -> str:
    """A cell our own export protected ("'+52 55…") reads as what the person had typed."""
    return text[1:] if text[:1] == "'" and text[1:2] in FORMULA_STARTS else text


def _parse_date(text: str) -> date | None:
    for pattern in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _is_minor(born: date, today: date) -> bool:
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day)) < 18


def _basic(raw: dict, tags: dict[str, Tag], today: date) -> Row:
    """One row by itself: the same limits as "Registrar a una persona" (``people.forms.PersonForm``)."""
    row = Row(line=int(raw.get("line") or 0))
    wrong = row.errors.append
    unfit = raw.get("unfit") or []
    if "control" in unfit:
        wrong("Una celda tiene caracteres que no se ven (un salto de línea, un tabulador u otro carácter de control). Escríbela de nuevo a mano.")
    if "long" in unfit:
        wrong(f"Una celda es demasiado larga: escribe {MAX_CELL} caracteres o menos.")
    first, last = str(raw.get("first_name", "")), str(raw.get("last_name", ""))
    if not first and raw.get("full_name"):
        first, rest = full_name_to_parts(str(raw["full_name"]))
        last = last or rest
    row.first_name, row.last_name = first, last
    if not first and unfit:
        pass  # the cell was refused above: "falta el nombre" would be a second, false reason
    elif not first:
        wrong("Falta el nombre.")
    elif len(first) > 120 or len(last) > 120:
        wrong("El nombre o los apellidos son muy largos: escribe 120 caracteres o menos en cada uno.")
    elif _starts_like_formula(first) or _starts_like_formula(last):
        wrong("El nombre y los apellidos no pueden empezar con =, +, - o @.")
    elif not normalized_fits(f"{first} {last}", NORMALIZED_NAME_MAX):
        wrong("El nombre tiene letras especiales que ocupan más de lo que cabe: escríbelo con letras comunes.")
    email = str(raw.get("email", "")).strip().lower()
    row.email = email
    if email:
        try:
            validate_email(email)
        except ValidationError:
            wrong("El correo no es válido. Escribe uno como nombre@ejemplo.com.")
        else:
            if len(email) > 254:
                wrong("El correo es muy largo.")
    row.phone = _unquoted(str(raw.get("phone", "")))
    if len(row.phone) > 40:
        wrong("El teléfono es muy largo: escribe 40 caracteres o menos.")
    elif row.phone and (_starts_like_formula(row.phone.lstrip("+")) or not any(ch.isdigit() for ch in row.phone)):
        wrong("El teléfono no es válido: escribe solo el número.")
    born = str(raw.get("birth_date", ""))
    if born:
        row.birth_date = _parse_date(born)
        if row.birth_date is None:
            wrong("La fecha de nacimiento no se entiende. Escríbela así: día/mes/año, por ejemplo 14/03/2009.")
        elif row.birth_date > today or today.year - row.birth_date.year > 120:
            row.birth_date = None
            wrong("Revisa la fecha de nacimiento: no puede ser un día que no ha llegado ni de hace más de 120 años.")
        else:
            row.is_minor = _is_minor(row.birth_date, today)
    stage = normalize_name(str(raw.get("stage", "")))
    if stage in STAGE_WORDS:
        row.stage = STAGE_WORDS[stage]
    elif stage:
        wrong("No conocemos esa etapa. Usa visita, asistente, miembro o ya no asiste, o deja la celda vacía.")
    for name in [piece.strip() for piece in str(raw.get("tags", "")).replace("|", ";").replace(",", ";").split(";")]:
        key = normalize_name(name)
        if not key:
            continue
        tag = tags.get(key)
        if tag is None:
            wrong("Tiene una etiqueta que tu iglesia no ha creado. Créala primero en Etiquetas o quítala del archivo.")
            break
        if tag.pk not in row.tag_ids:
            row.tag_ids.append(tag.pk)
            row.tag_names.append(tag.name)
    if row.errors:
        row.status = ERROR
    return row


def _signals(row: Row) -> set[tuple[str, str]]:
    return duplicates.keys_of({
        "email": row.email, "phone": row.phone, "normalized_name": normalize_name(row.full_name), "birth_date": row.birth_date,
    })


SIGNAL_WORDS = {
    duplicates.EMAIL: "el mismo correo", duplicates.PHONE: "el mismo teléfono",
    duplicates.NAME_BIRTH: "el mismo nombre y la misma fecha de nacimiento",
}


def plan(church, raw_rows: list[dict]) -> Plan:
    """What confirming would do with each row, read against the database as it is now. Writes nothing.
    Three queries whatever the size of the file: the church's tags, its active people, the matched people."""
    today = timezone.localdate()
    tags = {tag.normalized_name: tag for tag in Tag.objects.filter(church=church)}
    rows = [_basic(raw, tags, today) for raw in raw_rows if isinstance(raw, dict)]

    # The people this church already has, by each value they could share. Only this church, only active records.
    known: dict[tuple[str, str], int] = {}
    existing = Person.objects.filter(church=church, merged_into__isnull=True).order_by("pk")
    for person in existing.values("pk", "email", "phone", "normalized_name", "birth_date"):
        for key in duplicates.keys_of(person):
            known.setdefault(key, person["pk"])

    seen: dict[tuple[str, str], int] = {}  # a value of an earlier row of this file -> its line
    whole: dict[tuple, int] = {}  # a row with nothing to compare but its name, repeated word for word
    matched: dict[int, list[Row]] = {}
    for row in rows:
        if row.status == ERROR:
            continue
        keys = _signals(row)
        ordered = [key for signal in duplicates.SIGNAL_LABELS for key in sorted(keys) if key[0] == signal]
        hit = next((key for key in ordered if key in known), None)
        if hit is not None:
            row.status = EXISTS
            row.reason = SIGNAL_WORDS[hit[0]]
            matched.setdefault(known[hit], []).append(row)
            continue
        again = next((key for key in ordered if key in seen), None)
        identity = (normalize_name(row.full_name), row.email, row.phone, row.birth_date)
        if again is not None:
            row.status, row.reason, row.same_as_line = REPEATED, SIGNAL_WORDS[again[0]], seen[again]
            continue
        if not keys and identity in whole:
            row.status, row.reason, row.same_as_line = REPEATED, "el mismo nombre y ningún otro dato", whole[identity]
            continue
        for key in keys:
            seen[key] = row.line
        whole.setdefault(identity, row.line)
    for person in Person.objects.filter(church=church, pk__in=matched):
        for row in matched[person.pk]:
            row.match = person

    result = Plan(rows)
    result.token = _token(church, result)
    return result


def _token(church, result: Plan) -> str:
    """Everything the preview says about every row: what happens to it, whom it matches, and what it would write."""
    stated = [
        [row.line, row.status, row.match.pk if row.match else None, row.same_as_line, row.first_name, row.last_name, row.email,
         row.phone, row.birth_date.isoformat() if row.birth_date else None, row.is_minor, row.stage, row.tag_ids, row.errors]
        for row in result.rows
    ]
    return hashlib.sha256(json.dumps([church.pk, stated]).encode()).hexdigest()


# ---- the import -----------------------------------------------------------------------------------
def _lock_church(church) -> None:
    """One import (and one change of the church's tags) at a time: the row of its module flags, which this app owns."""
    list(ChurchSettings.objects.select_for_update().filter(church=church))


def apply(church, raw_rows: list[dict], actor, *, token: str) -> PeopleImport:
    """Create the people of the rows planned as new. Everything or nothing, one transaction, under the
    church's lock; the plan is made again there and compared with ``token`` (``StalePreview``)."""
    try:
        with transaction.atomic():
            _lock_church(church)
            current = plan(church, raw_rows)
            if current.token != token:
                raise StalePreview
            new = current.new
            if not new:
                raise PeopleError("Este archivo no tiene a nadie por registrar.", code="empty")
            record = PeopleImport.objects.create(church=church, imported_by=actor, created_count=len(new))
            today = timezone.localdate()
            links = []
            for row in new:
                method = Person.ContactMethod.EMAIL if row.email else Person.ContactMethod.PHONE if row.phone else Person.ContactMethod.UNSPECIFIED
                person = Person(
                    church=church, first_name=row.first_name, last_name=row.last_name, email=row.email, phone=row.phone,
                    birth_date=row.birth_date, preferred_contact_method=method, stage=row.stage,
                    stage_changed_on=today if row.stage else None, created_from=Person.CreatedFrom.IMPORT, imported_in=record,
                )
                person.save()  # never ``bulk_create``: ``save`` normalises the name and refuses a verified date on a new row
                links.extend(PersonTag(person=person, tag_id=tag_id, added_by=actor) for tag_id in row.tag_ids)
            PersonTag.objects.bulk_create(links)
            record_church(church, actor, "personas_importadas", "Importó personas desde un archivo", record,
                          import_id=record.pk, count=len(new))
            return record
    except DataError as error:  # a value the database cannot hold got past the preview: nothing was written
        raise PeopleError("No pudimos registrar a estas personas: algún dato del archivo no se puede guardar. No se registró a nadie.",
                          code="data") from error


# ---- undo -----------------------------------------------------------------------------------------
def undoable_until(record: PeopleImport):
    return record.imported_at + timedelta(hours=UNDO_HOURS)


def can_undo(record: PeopleImport, now=None) -> bool:
    return record.undone_at is None and (now or timezone.now()) < undoable_until(record)


# Tags are the import's own labels (or the administration's since): they go with the row. Everything else that
# points at a person is something the person gained, and keeps the person.
IGNORED_RELATIONS = frozenset({("people.PersonTag", "person")})


@dataclass
class UndoPlan:
    delete: list[int]
    keep: list[int]
    token: str = ""


def undo_plan(record: PeopleImport) -> UndoPlan:
    """Which of the people this import created can be deleted now: the ones that are still active, have no
    account, and that no row of any table points at (``people.merge.relations_to_person``: every foreign
    key to a person, the ones of modules added later included). The rest stay, and are counted."""
    from .merge import relations_to_person

    people = Person.objects.filter(imported_in=record, church_id=record.church_id)
    ids = sorted(people.values_list("pk", flat=True))
    held = set(people.filter(merged_into__isnull=False).values_list("pk", flat=True))
    held |= set(people.filter(user__isnull=False).values_list("pk", flat=True))
    for label, name in relations_to_person():
        if (label, name) in IGNORED_RELATIONS or not ids:
            continue
        rows = apps.get_model(label)._base_manager.filter(**{f"{name}__in": ids})
        held |= set(rows.values_list(f"{name}_id", flat=True))
    result = UndoPlan([pk for pk in ids if pk not in held], [pk for pk in ids if pk in held])
    result.token = hashlib.sha256(json.dumps([record.pk, result.delete, result.keep]).encode()).hexdigest()
    return result


def undo(church, import_id: int, actor, *, token: str) -> PeopleImport:
    """"Deshacer la importación", within ``UNDO_HOURS``: delete exactly the people it created that gained
    nothing since, and report how many stayed. ``token``: what the screen said would be deleted."""
    with transaction.atomic():
        record = PeopleImport.objects.select_for_update(of=("self",)).filter(pk=import_id, church=church).first()
        if record is None:
            raise PeopleError("Esta importación no existe.", code="gone")
        if record.undone_at is not None:
            raise PeopleError("Esta importación ya se había deshecho.", code="undone")
        now = timezone.now()
        if not can_undo(record, now):
            raise PeopleError(f"Pasaron más de {UNDO_HOURS} horas: esta importación ya no se puede deshacer.", code="late")
        # Lock the rows, then read what points at them: a row locked for update cannot gain a reference meanwhile.
        list(Person.objects.select_for_update().filter(imported_in=record, church=church).order_by("pk"))
        current = undo_plan(record)
        if current.token != token:
            raise StalePreview
        Person.objects.filter(pk__in=current.delete, imported_in=record, church=church).delete()
        record.undone_at, record.undone_by = now, actor
        record.undo_report = {"deleted": len(current.delete), "kept": len(current.keep)}
        record.save(update_fields=["undone_at", "undone_by", "undo_report"])
        record_church(church, actor, "personas_importacion_deshecha", "Deshizo una importación de personas", record,
                      import_id=record.pk, count=len(current.delete), kept=len(current.keep))
        return record
