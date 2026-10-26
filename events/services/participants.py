"""Registering, importing, and correcting camperos."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Max

from ..models import (
    ConsentRecord,
    Event,
    EventMembership,
    EventParticipant,
    MembershipRole,
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
) -> EventParticipant:
    participant = EventParticipant.objects.create(
        event=event,
        number=_next_number(event),
        full_name=full_name,
        age=age,
        room=room,
        church_group=church_group,
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


@transaction.atomic
def record_guardian_authorization(participant: EventParticipant, *, actor, granted: bool = True, notes: str = "") -> ConsentRecord:
    """The signed guardian authorization is in the church's hands, attested by ``actor``. Once: a second
    confirmation (a double click, two tabs) returns the record already there."""
    EventParticipant.objects.select_for_update().filter(pk=participant.pk).first()
    current = latest_consent(participant, ConsentRecord.Kind.GUARDIAN)
    if granted and current is not None and current.status == ConsentRecord.Status.GRANTED:
        return current
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
        "autorizacion_registrada",
        f"Registró la autorización del padre, madre o tutor legal del campero #{participant.code}",
        participant,
    )
    return record


@transaction.atomic
def withdraw_guardian_authorization(participant: EventParticipant, *, actor, reason: str) -> ConsentRecord | None:
    """Undo "Marcar como recibida": a newer record (revocado) with the reason; the earlier one stays as history.

    Only checks from now on read it (``guardian_authorized``): an interview already completed is not touched,
    and one in progress is not stopped (the campero is already talking); the audit row then names it. Returns
    None when there was no authorization to remove. The reason stays on the record (and in the campero's data
    export), not in the activity log.
    """
    from ..models import Interview

    EventParticipant.objects.select_for_update().filter(pk=participant.pk).first()
    current = latest_consent(participant, ConsentRecord.Kind.GUARDIAN)
    if current is None or current.status != ConsentRecord.Status.GRANTED:
        return None
    record = ConsentRecord.objects.create(
        participant=participant,
        kind=ConsentRecord.Kind.GUARDIAN,
        status=ConsentRecord.Status.REVOKED,
        method=current.method,
        notes=reason[:200],
        recorded_by=actor,
    )
    in_progress = participant.interviews.filter(status=Interview.Status.IN_PROGRESS).first()
    summary = f"Quitó la autorización del padre, madre o tutor legal del campero #{participant.code}"
    audit.record(
        actor,
        participant.event,
        "autorizacion_retirada",
        summary + (", con una entrevista en curso que se podrá terminar" if in_progress else ""),
        participant,
        **({"interview": str(in_progress.public_id)} if in_progress else {}),
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
}
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
def apply_import(event: Event, rows: list[dict], *, actor, include_lines: set[int]) -> int:
    created = 0
    valid_coordinators = set(
        EventMembership.objects.filter(event=event, role=MembershipRole.COORDINATOR).values_list("id", flat=True)
    )
    for row in rows:
        if row["errors"] or row["line"] not in include_lines:
            continue
        coordinator_id = row["coordinator_id"] if row["coordinator_id"] in valid_coordinators else None
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
        )
        created += 1
    summary = "Importó 1 campero desde un archivo" if created == 1 else f"Importó {created} camperos desde un archivo"
    audit.record(actor, event, "importacion", summary, None, count=created)
    return created
