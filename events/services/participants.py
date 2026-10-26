"""Registering, importing, and correcting camperos."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Max
from django.utils.text import slugify

from ..models import (
    ConsentRecord,
    Event,
    EventMembership,
    EventParticipant,
    MembershipRole,
    Sede,
    normalize_name,
)
from . import audit


def possible_duplicates(event: Event, full_name: str, *, exclude_id: int | None = None):
    """Same name after removing accents, case and spacing, or the same words in another order.

    We only warn. Two different people can share a name, so nothing is ever merged.
    """
    normalized = normalize_name(full_name)
    if not normalized:
        return EventParticipant.objects.none()
    tokens = sorted(normalized.split())
    candidates = EventParticipant.objects.filter(event=event).select_related("coordinator__user")
    if exclude_id:
        candidates = candidates.exclude(pk=exclude_id)
    first, last = tokens[0], tokens[-1]
    narrowed = candidates.filter(normalized_name__contains=first).filter(normalized_name__contains=last)
    return [p for p in narrowed if p.normalized_name == normalized or sorted(p.normalized_name.split()) == tokens]


def _next_number(event: Event) -> int:
    # Serialize number allocation per event; the row lock is held until the transaction ends.
    Event.objects.select_for_update().filter(pk=event.pk).first()
    current = EventParticipant.objects.filter(event=event).aggregate(top=Max("number"))["top"] or 0
    return current + 1


@transaction.atomic
def create_participant(
    event: Event,
    *,
    actor,
    full_name: str,
    age: int,
    room: str,
    church_group: str,
    coordinator: EventMembership | None,
    guardian_authorization: bool = False,
    log: bool = True,
    sede: Sede | None = None,
) -> EventParticipant:
    participant = EventParticipant.objects.create(
        event=event,
        number=_next_number(event),
        full_name=full_name,
        age=age,
        room=room,
        church_group=church_group,
        sede=sede,
        coordinator=coordinator,
        created_by=actor,
        updated_by=actor,
    )
    if guardian_authorization:
        ConsentRecord.objects.create(
            participant=participant,
            kind=ConsentRecord.Kind.GUARDIAN,
            method=ConsentRecord.Method.PAPER,
            recorded_by=actor,
        )
    if log:
        audit.record(actor, event, "campero_creado", f"Registró al campero #{participant.code}", participant)
    return participant


@transaction.atomic
def update_participant(participant: EventParticipant, *, actor, **changes) -> EventParticipant:
    changed = []
    for field_name, value in changes.items():
        if getattr(participant, field_name) != value:
            setattr(participant, field_name, value)
            changed.append(field_name)
    if not changed:
        return participant
    participant.updated_by = actor
    participant.save()
    action = "campero_reasignado" if changed == ["coordinator"] else "campero_modificado"
    labels = {
        "full_name": "nombre",
        "age": "edad",
        "room": "cuarto",
        "church_group": "iglesia o grupo",
        "sede": "sede",
        "coordinator": "coordinador",
        "is_active": "participación",
        "withdrawn_reason": "motivo de baja",
    }
    audit.record(
        actor,
        participant.event,
        action,
        f"Cambió {', '.join(labels.get(c, c) for c in changed)} del campero #{participant.code}",
        participant,
        fields=changed,
    )
    return participant


@transaction.atomic
def delete_participant_data(participant: EventParticipant, *, actor) -> None:
    """Authorized erasure: removes the person, their answers, consents and follow-ups.

    Snapshots keep only aggregate numbers, so they stay as historical records.
    """
    event = participant.event
    code = participant.code
    audit.record(actor, event, "campero_eliminado", f"Eliminó los datos del campero #{code}", None, number=participant.number)
    participant.delete()


def record_guardian_authorization(participant: EventParticipant, *, actor, granted: bool = True, notes: str = "") -> ConsentRecord:
    record = ConsentRecord.objects.create(
        participant=participant,
        kind=ConsentRecord.Kind.GUARDIAN,
        status=ConsentRecord.Status.GRANTED if granted else ConsentRecord.Status.DENIED,
        method=ConsentRecord.Method.PAPER,
        notes=notes,
        recorded_by=actor,
    )
    audit.record(
        actor,
        participant.event,
        "autorizacion",
        f"Registró la autorización del padre, madre o tutor legal del campero #{participant.code}",
        participant,
    )
    return record


def latest_consent(participant: EventParticipant, kind: str) -> ConsentRecord | None:
    return participant.consents.filter(kind=kind).order_by("-recorded_at", "-id").first()


def guardian_authorized(participant: EventParticipant) -> bool:
    record = latest_consent(participant, ConsentRecord.Kind.GUARDIAN)
    return bool(record and record.status == ConsentRecord.Status.GRANTED)


# ---- CSV import ------------------------------------------------------------------------

HEADER_ALIASES = {
    "full_name": {"nombre", "nombre completo", "campero", "nombre del campero", "participante"},
    "age": {"edad", "años"},
    "room": {"cuarto", "habitacion", "habitación", "cabaña", "cabana"},
    "church_group": {"iglesia", "iglesia o grupo", "grupo", "congregacion", "congregación"},
    "coordinator": {"coordinador", "coordinador asignado", "correo del coordinador"},
    "guardian": {"autorizacion", "autorización", "permiso", "autorizacion firmada", "autorización firmada"},
    "sede": {"sede", "sede de la iglesia", "campus"},
}
# The file's headers, in this order, as import.html and the example file (`sample_csv`) show them.
IMPORT_COLUMNS = ["Nombre", "Edad", "Cuarto", "Iglesia o grupo", "Coordinador", "Autorización", "Sede"]
YES_VALUES = {"si", "sí", "s", "x", "1", "true", "verdadero", "yes"}
MAX_IMPORT_ROWS = 1500


@dataclass
class ImportRow:
    line: int
    full_name: str = ""
    age: int | None = None
    room: str = ""
    church_group: str = ""
    coordinator_id: int | None = None
    coordinator_label: str = ""
    guardian: bool = False
    sede_id: int | None = None
    sede_label: str = ""
    errors: list[str] = field(default_factory=list)
    duplicate_of: list[str] = field(default_factory=list)
    duplicate_in_file: int | None = None

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def as_session(self) -> dict:
        return self.__dict__.copy()


def _canonical_headers(fieldnames) -> dict[str, str]:
    mapping = {}
    for raw in fieldnames or []:
        key = normalize_name(raw or "").replace("_", " ")
        for target, aliases in HEADER_ALIASES.items():
            if key in {normalize_name(a) for a in aliases}:
                mapping[raw] = target
    return mapping


def parse_import(event: Event, uploaded_bytes: bytes) -> tuple[list[ImportRow], list[str]]:
    """Validate a CSV without writing anything. Returns rows and file-level problems."""
    try:
        text = uploaded_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = uploaded_bytes.decode("latin-1")
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = _canonical_headers(reader.fieldnames)
    problems = []
    missing = [label for key, label in [("full_name", "Nombre"), ("age", "Edad"), ("room", "Cuarto"), ("church_group", "Iglesia o grupo")] if key not in headers.values()]
    if missing:
        problems.append("Faltan estas columnas: " + ", ".join(missing) + ".")
        return [], problems

    coordinators = list(
        EventMembership.objects.filter(event=event, role=MembershipRole.COORDINATOR, is_active=True).select_related("user")
    )
    by_email = {c.user.email.lower(): c for c in coordinators if c.user.email}
    by_name = {normalize_name(c.display_name): c for c in coordinators}
    sedes = list(event.allowed_sedes())
    sede_by_key: dict[str, list[Sede]] = {}
    for sede in sedes:  # name or identifier, without case or accents: "Mérida", "MERIDA", "merida"
        for key in (slugify(sede.slug), slugify(sede.name)):
            # A name with no letters or digits reads as "": a cell with only "-" must not land on it.
            if key and sede not in sede_by_key.setdefault(key, []):
                sede_by_key[key].append(sede)

    rows: list[ImportRow] = []
    seen_names: dict[str, int] = {}
    for index, raw in enumerate(reader, start=2):
        if index - 1 > MAX_IMPORT_ROWS:
            problems.append(f"El archivo tiene más de {MAX_IMPORT_ROWS} filas. Divídelo en partes.")
            break
        values = {headers[k]: (v or "").strip() for k, v in raw.items() if k in headers}
        if not any(values.values()):
            continue
        row = ImportRow(line=index)
        row.full_name = " ".join(values.get("full_name", "").split())
        if not row.full_name:
            row.errors.append("Falta el nombre.")
        age_raw = values.get("age", "")
        try:
            row.age = int(age_raw)
            if not 5 <= row.age <= 99:
                row.errors.append("La edad debe estar entre 5 y 99.")
        except ValueError:
            row.errors.append("La edad debe ser un número." if age_raw else "Falta la edad.")
        row.room = values.get("room", "")[:40]
        if not row.room:
            row.errors.append("Falta el cuarto.")
        row.church_group = values.get("church_group", "")[:120]
        if not row.church_group:
            row.errors.append("Falta la iglesia o grupo.")
        coordinator_raw = values.get("coordinator", "")
        if coordinator_raw:
            match = by_email.get(coordinator_raw.lower()) or by_name.get(normalize_name(coordinator_raw))
            if match:
                row.coordinator_id = match.id
                row.coordinator_label = match.display_name
            else:
                row.errors.append(f"No encontramos al coordinador “{coordinator_raw}” en este evento.")
        row.guardian = normalize_name(values.get("guardian", "")) in YES_VALUES
        sede_raw = values.get("sede", "")
        if sede_raw:
            matches = sede_by_key.get(slugify(sede_raw), [])
            if len(matches) == 1:
                row.sede_id, row.sede_label = matches[0].id, matches[0].name
            elif matches:
                # Two sedes that read the same (made outside SedeForm, which refuses it): never pick one silently.
                row.errors.append(
                    f"Sede ambigua: «{sede_raw}». Coincide con {', '.join(s.name for s in matches)}: "
                    "cambia el nombre o el identificador de una de ellas en Sedes de la iglesia."
                )
            elif sedes:
                row.errors.append(f"Sede desconocida: «{sede_raw}». Usa una de: {', '.join(s.name for s in sedes)}.")
            else:
                row.errors.append(f"Sede desconocida: «{sede_raw}». La iglesia no tiene sedes activas: deja la columna vacía.")
        if row.full_name:
            key = normalize_name(row.full_name)
            if key in seen_names:
                row.duplicate_in_file = seen_names[key]
            else:
                seen_names[key] = row.line
            row.duplicate_of = [f"#{p.code} {p.full_name} ({p.age} años, cuarto {p.room})" for p in possible_duplicates(event, row.full_name)]
        rows.append(row)
    if not rows and not problems:
        problems.append("El archivo no tiene filas con datos.")
    return rows, problems


@transaction.atomic
@dataclass
class ImportResult:
    created: int = 0
    # Rows imported without the sede their preview showed: it was deleted, deactivated or left the event's list
    # between the preview (kept in the session) and the confirmation.
    sede_unavailable: int = 0

    @property
    def message(self) -> str:
        text = "Se importó 1 campero." if self.created == 1 else f"Se importaron {self.created} camperos."
        if self.sede_unavailable == 1:
            text += " 1 campero quedó sin sede porque la sede ya no estaba disponible."
        elif self.sede_unavailable:
            text += f" {self.sede_unavailable} camperos quedaron sin sede porque la sede ya no estaba disponible."
        return text


def apply_import(event: Event, rows: list[dict], *, actor, include_lines: set[int]) -> ImportResult:
    result = ImportResult()
    valid_coordinators = set(
        EventMembership.objects.filter(event=event, role=MembershipRole.COORDINATOR).values_list("id", flat=True)
    )
    # Checked again on confirm, with the same rule as the preview: the preview lives in the session.
    allowed_sedes = {s.id: s for s in event.allowed_sedes()}
    for row in rows:
        if row["errors"] or row["line"] not in include_lines:
            continue
        coordinator_id = row["coordinator_id"] if row["coordinator_id"] in valid_coordinators else None
        sede_id = row.get("sede_id")  # .get: a preview stored before sedes existed has no key
        sede = allowed_sedes.get(sede_id)
        if sede_id and sede is None:
            result.sede_unavailable += 1
        create_participant(
            event,
            actor=actor,
            full_name=row["full_name"],
            age=row["age"],
            room=row["room"],
            church_group=row["church_group"],
            coordinator=EventMembership.objects.get(pk=coordinator_id) if coordinator_id else None,
            guardian_authorization=row["guardian"],
            log=False,
            sede=sede,
        )
        result.created += 1
    created = result.created
    summary = "Importó 1 campero desde un archivo" if created == 1 else f"Importó {created} camperos desde un archivo"
    audit.record(actor, event, "importacion", summary, None, count=created, sin_sede=result.sede_unavailable)
    return result
