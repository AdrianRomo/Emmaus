"""Aprobación pastoral: who may publish what the church itself wrote (issue #99, decision 0014).

Two responsibilities, two church roles:

* **preparing** content is the administration's work (``church_admin``), as before: writing, correcting,
  and asking for the approval;
* **approving it for publication** belongs to a person who holds the church role of **pastor** in that
  same church (``can_approve``). The administration cannot approve, and nothing falls back to it: a
  church without an active pastor account publishes nothing new, and its screens say so.

What it covers: devotionals, reading plans and the public home of the church (the ``sitio`` app
registers that kind). A resource is only a link to a site the church does not host: it needs no approval
of rights and keeps its own publication step.

One rule for every kind, written once here:

* only ``approve`` publishes. It stores who approved, when, with which permitted use, and the
  fingerprint of the words that were approved (``ContentApproval.approved_digest``);
* the pastor approves what the preview showed: the form carries the fingerprint of that preview
  and ``approve`` refuses when the content is no longer that (``StaleContent``);
* a material edit of something published takes it **out of publication** until a pastor approves it
  again (``content_changed``). Emmaus keeps no copy of the approved words, so it cannot keep serving
  them while a different text waits: every screen that edits says so before saving;
* what was published before approvals existed stays published and is listed for the pastor
  (``unrecorded``), to approve item by item.

Nothing here reads what a person read, started or marked: no function touches ``DevotionalRead``,
``ReadingPlanProgress`` or ``ReadingPlanDayRead``, and none may. No e-mail is sent. An audit row
names the content by its kind and its id, never by a title, a reason or a note.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import formats, timezone

from core.models import Role, UserChurchRole
from people.audit import record_church

from .models import ContentApproval, Devotional, ReadingPlan

State = ContentApproval.State
Use = ContentApproval.Use

GONE = "Ese contenido ya no existe."
NOT_WAITING = "Esto ya no espera aprobación: alguien lo aprobó, lo devolvió o lo retiró mientras lo revisabas."
STALE = "El contenido cambió mientras lo revisabas. No se aprobó nada: léelo otra vez."
NO_USE = "Elige con qué permiso se publica."
NO_REASON = "Escribe por qué lo devuelves, para que la administración sepa qué corregir."
NO_DAYS = "Este plan todavía no tiene días. Agrega por lo menos uno antes de pedir su aprobación."


class ApprovalError(ValidationError):
    """A rule of the approvals refused the action; the message is ready for the screen."""


class StaleContent(Exception):
    """The content is no longer what the pastor's preview showed."""


def fingerprint(*parts) -> str:
    """A fingerprint of some words. Only the hash is stored or sent to a form."""
    return hashlib.sha256("\x1f".join(str(part) for part in parts).encode()).hexdigest()


def _line(text: str) -> str:
    return " ".join((text or "").split())


# ---- who ----------------------------------------------------------------------------------------
def can_approve(user, church) -> bool:
    """``user`` holds the church role of pastor in ``church`` itself (the same table that answers
    ``core.authz.has_scope`` and ``overview.access``). An event role called "Pastor", the
    administration's role and a pastor of another church answer no."""
    if church is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    return UserChurchRole.objects.filter(church=church, user=user, role=Role.PASTOR).exists()


def has_active_pastor(church) -> bool:
    """Whether anybody could approve today. Without one, nothing new is published."""
    return UserChurchRole.objects.filter(church=church, role=Role.PASTOR, user__is_active=True).exists()


# ---- the kinds of content -----------------------------------------------------------------------
class ContentKind:
    """How one kind of content is fetched, fingerprinted, published and shown. Every fetch is
    inside one church."""

    key = ""
    label = ""              # "Devocional"
    words = ""              # "un devocional": how an audit row and a message name it
    preview_template = ""

    def get(self, church, object_id, *, lock: bool = False):
        raise NotImplementedError

    def many(self, church, ids) -> dict:
        raise NotImplementedError

    def published(self, church) -> list:
        raise NotImplementedError

    def digest(self, obj) -> str:
        raise NotImplementedError

    def revision(self, obj) -> str:
        """The fingerprint of what the preview shows; by default, of the content itself."""
        return self.digest(obj)

    def is_published(self, obj) -> bool:
        raise NotImplementedError

    def not_ready(self, obj) -> str:
        """Why it cannot be published as it is ("" when it can)."""
        return ""

    def publish(self, obj, actor) -> None:
        raise NotImplementedError

    def title(self, obj) -> str:
        raise NotImplementedError

    def line(self, obj) -> str:
        return ""

    def preview(self, obj) -> dict:
        return {}

    def edit_url(self, obj) -> str:
        raise NotImplementedError

    def published_note(self, obj) -> str:
        """What the pastor reads after approving: where it is seen now."""
        return ""


class DevotionalKind(ContentKind):
    key = ContentApproval.Kind.DEVOTIONAL.value
    label = "Devocional"
    words = "un devocional"
    preview_template = "resources/_approval_devotional.html"

    def get(self, church, object_id, *, lock=False):
        found = Devotional.objects.filter(church=church, pk=object_id)
        return (found.select_for_update() if lock else found).first()

    def many(self, church, ids):
        return {row.pk: row for row in Devotional.objects.filter(church=church, pk__in=ids)}

    def published(self, church):
        return list(Devotional.objects.filter(church=church, status=Devotional.Status.PUBLISHED).order_by("-date", "-id"))

    def digest(self, obj):
        # The day is when it is read, not what it says: moving it is not a new text.
        return fingerprint(obj.title, obj.passage, obj.body, obj.author_name)

    def is_published(self, obj):
        return obj.is_published

    def publish(self, obj, actor):
        obj.status = Devotional.Status.PUBLISHED
        obj.save(update_fields=["status", "updated_at"])

    def title(self, obj):
        return obj.title

    def line(self, obj):
        return "Para el " + formats.date_format(obj.date, r"j \d\e F \d\e Y").lower() + "."

    def preview(self, obj):
        return {"devotional": obj}

    def edit_url(self, obj):
        return reverse("resources:devotional_edit", args=[obj.pk])

    def published_note(self, obj):
        if obj.is_readable:
            return "Las personas de tu iglesia ya lo pueden leer."
        return "Las personas de tu iglesia lo leerán cuando llegue su día."


class PlanKind(ContentKind):
    key = ContentApproval.Kind.PLAN.value
    label = "Plan de lectura"
    words = "un plan de lectura"
    preview_template = "resources/_approval_plan.html"

    def get(self, church, object_id, *, lock=False):
        found = ReadingPlan.objects.filter(church=church, pk=object_id)
        return (found.select_for_update() if lock else found).first()

    def many(self, church, ids):
        return {row.pk: row for row in ReadingPlan.objects.filter(church=church, pk__in=ids)}

    def published(self, church):
        return list(ReadingPlan.objects.filter(church=church, status=ReadingPlan.Status.PUBLISHED).order_by("title", "id"))

    def digest(self, obj):
        days = obj.days.order_by("position").values_list("position", "title", "reference", "reflection", "question")
        return fingerprint(obj.title, obj.description, obj.author_line, *("\x1e".join(str(part) for part in day) for day in days))

    def is_published(self, obj):
        return obj.is_published

    def not_ready(self, obj):
        return "" if obj.days.exists() else NO_DAYS

    def publish(self, obj, actor):
        obj.status = ReadingPlan.Status.PUBLISHED
        obj.rights_confirmed_by, obj.rights_confirmed_at = actor, timezone.now()
        obj.save(update_fields=["status", "rights_confirmed_by", "rights_confirmed_at", "updated_at"])

    def title(self, obj):
        return obj.title

    def line(self, obj):
        total = obj.days.count()
        return "1 día." if total == 1 else f"{total} días."

    def preview(self, obj):
        return {"plan": obj, "days": list(obj.days.order_by("position"))}

    def edit_url(self, obj):
        return reverse("resources:plan_days", args=[obj.pk])

    def published_note(self, obj):
        return "Las personas de tu iglesia ya lo encuentran en Planes de lectura."


KINDS: dict[str, ContentKind] = {}


def register(kind: ContentKind) -> None:
    """Add a kind of content (``sitio`` adds the public home when its app is ready)."""
    KINDS[kind.key] = kind


register(DevotionalKind())
register(PlanKind())
DEVOTIONAL, PLAN = KINDS[DevotionalKind.key], KINDS[PlanKind.key]


def kind_of(key: str) -> ContentKind | None:
    return KINDS.get(key)


# ---- reading the state --------------------------------------------------------------------------
def _row(church, kind: ContentKind, obj, *, lock: bool = False) -> ContentApproval | None:
    rows = ContentApproval.objects.filter(church=church, kind=kind.key, object_id=obj.pk)
    return (rows.select_for_update() if lock else rows).first()


def row_of(church, kind: ContentKind, obj) -> ContentApproval | None:
    """The approval row of one object, or None when nothing was ever asked or decided about it."""
    return _row(church, kind, obj) if obj is not None and obj.pk else None


def attach(church, kind: ContentKind, objects) -> None:
    """Give each object ``approval`` (its row or None), with one query for the whole list."""
    objects = list(objects)
    rows = ContentApproval.objects.filter(church=church, kind=kind.key, object_id__in=[obj.pk for obj in objects])
    found = {row.object_id: row for row in rows}
    for obj in objects:
        obj.approval = found.get(obj.pk)


def is_waiting(kind: ContentKind, obj, row: ContentApproval | None) -> bool:
    """Whether a pastor can approve it now: the administration asked, or it is published with no approval on record."""
    if row is not None and row.is_pending:
        return True
    return kind.is_published(obj) and (row is None or not row.is_approved)


@dataclass
class Entry:
    """One line of the pastor's list."""

    kind: ContentKind
    obj: object
    row: ContentApproval | None

    @property
    def title(self) -> str:
        return self.kind.title(self.obj)

    @property
    def line(self) -> str:
        return self.kind.line(self.obj)

    @property
    def url(self) -> str:
        return reverse("resources:approval_review", args=[self.kind.key, self.obj.pk])


def _entries(church, state: str) -> list[Entry]:
    rows = list(ContentApproval.objects.filter(church=church, state=state).order_by("requested_at", "id"))
    found = {key: kind.many(church, [row.object_id for row in rows if row.kind == key]) for key, kind in KINDS.items()}
    return [
        Entry(KINDS[row.kind], found[row.kind][row.object_id], row)
        for row in rows if row.kind in KINDS and row.object_id in found[row.kind]
    ]


def pending(church) -> list[Entry]:
    """What the administration asked a pastor to approve, oldest request first."""
    return _entries(church, State.PENDING)


def returned(church) -> list[Entry]:
    """What a pastor sent back, with the reason, until the administration asks again."""
    return _entries(church, State.RETURNED)


def pending_count(church) -> int:
    return ContentApproval.objects.filter(church=church, state=State.PENDING).count()


def unrecorded(church) -> list[Entry]:
    """What is published with no pastoral approval on record: published before approvals existed
    (the migration marked those rows) or by a tool that does not go through the screens."""
    entries = []
    for kind in KINDS.values():
        objects = kind.published(church)
        if not objects:
            continue
        rows = {
            row.object_id: row
            for row in ContentApproval.objects.filter(church=church, kind=kind.key, object_id__in=[obj.pk for obj in objects])
        }
        entries += [Entry(kind, obj, rows.get(obj.pk)) for obj in objects if obj.pk not in rows or not rows[obj.pk].is_approved]
    return entries


# ---- what the administration does ---------------------------------------------------------------
def request(church, kind: ContentKind, obj, actor) -> ContentApproval:
    """The administration asks a pastor to approve ``obj`` for publication. Idempotent. Call it inside
    the transaction that holds the object."""
    with transaction.atomic():
        row = _row(church, kind, obj, lock=True) or ContentApproval(church=church, kind=kind.key, object_id=obj.pk)
        if row.pk and row.is_pending:
            return row
        row.state, row.requested_by, row.requested_at = State.PENDING, actor, timezone.now()
        row.returned_by, row.returned_at, row.return_reason = None, None, ""
        row.save()
        record_church(church, actor, "contenido_aprobacion_pedida", f"Pidió la aprobación pastoral de {kind.words}", obj)
    return row


def withdraw(church, kind: ContentKind, obj, actor) -> bool:
    """The administration no longer asks for it (it went back to a draft, or was retired). True when a request was open."""
    with transaction.atomic():
        row = _row(church, kind, obj, lock=True)
        if row is None or not row.is_pending:
            return False
        if row.approved_at is None:
            row.delete()
        else:
            row.state = State.APPROVED if row.approved_digest == kind.digest(obj) else State.CHANGED
            row.save(update_fields=["state", "updated_at"])
        record_church(church, actor, "contenido_aprobacion_retirada", f"Retiró la solicitud de aprobación pastoral de {kind.words}", obj)
    return True


def content_changed(church, kind: ContentKind, obj, actor, *, taken_down: bool) -> None:
    """The words of ``obj`` changed. ``taken_down``: it was published and the caller just took it out of
    publication, in this same transaction. An approval never covers words it did not see."""
    with transaction.atomic():
        row = _row(church, kind, obj, lock=True)
        if taken_down:
            if row is None:
                row = ContentApproval(church=church, kind=kind.key, object_id=obj.pk, state=State.CHANGED)
                row.save()
            elif row.state in (State.APPROVED, State.LEGACY):
                row.state = State.CHANGED
                row.save(update_fields=["state", "updated_at"])
            record_church(
                church, actor, "contenido_fuera_de_publicacion",
                f"Cambió {kind.words} que estaba en publicación: dejó de verse hasta que un pastor lo apruebe otra vez", obj,
            )
        elif row is not None and row.is_approved:
            row.state = State.CHANGED
            row.save(update_fields=["state", "updated_at"])


# ---- what a pastor does -------------------------------------------------------------------------
def _for_decision(church, kind_key: str, object_id, actor):
    """(kind, the object locked, its row locked). The lock order is the one of every edit: the object, then its row."""
    if not can_approve(actor, church):
        raise PermissionDenied
    kind = kind_of(kind_key)
    obj = kind.get(church, object_id, lock=True) if kind is not None else None
    if obj is None:
        raise ApprovalError(GONE, code="missing")
    return kind, obj, _row(church, kind, obj, lock=True)


def approve(church, kind_key: str, object_id, actor, *, revision: str, permitted_use: str, source_note: str = "") -> ContentApproval:
    """A pastor approves the content for publication and it is published. ``revision`` is the
    fingerprint of the preview the pastor read: when the content is no longer that, ``StaleContent``."""
    note = _line(source_note)
    if permitted_use not in Use.values:
        raise ApprovalError(NO_USE, code="permitted_use")
    if len(note) > ContentApproval.NOTE_MAX:
        raise ApprovalError(f"La nota puede tener hasta {ContentApproval.NOTE_MAX} caracteres. Acórtala.", code="source_note")
    with transaction.atomic():
        kind, obj, row = _for_decision(church, kind_key, object_id, actor)
        if not is_waiting(kind, obj, row):
            raise ApprovalError(NOT_WAITING, code="state")
        if kind.revision(obj) != revision:
            raise StaleContent
        problem = kind.not_ready(obj)
        if problem:
            raise ApprovalError(problem, code="not_ready")
        row = row or ContentApproval(church=church, kind=kind.key, object_id=obj.pk)
        row.state, row.approved_digest = State.APPROVED, kind.digest(obj)
        row.approved_by, row.approved_at = actor, timezone.now()
        row.permitted_use, row.source_note = permitted_use, note
        row.returned_by, row.returned_at, row.return_reason = None, None, ""
        row.save()
        was_published = kind.is_published(obj)
        if not was_published:
            kind.publish(obj, actor)
        verb = "Aprobó" if was_published else "Aprobó y publicó"
        record_church(
            church, actor, "contenido_aprobado", f"{verb} {kind.words} ({Use(permitted_use).label.lower()})", obj,
            permitted_use=permitted_use,
        )
    return row


def send_back(church, kind_key: str, object_id, actor, *, reason: str) -> ContentApproval:
    """A pastor returns what the administration asked to publish, with a reason the staff reads. It
    stays unpublished. The reason is never e-mailed and never copied to the audit row."""
    reason = _line(reason)
    if not reason:
        raise ApprovalError(NO_REASON, code="reason")
    if len(reason) > ContentApproval.REASON_MAX:
        raise ApprovalError(f"El motivo puede tener hasta {ContentApproval.REASON_MAX} caracteres. Acórtalo.", code="reason")
    with transaction.atomic():
        kind, obj, row = _for_decision(church, kind_key, object_id, actor)
        if row is None or not row.is_pending:
            raise ApprovalError(NOT_WAITING, code="state")
        row.state, row.returned_by, row.returned_at, row.return_reason = State.RETURNED, actor, timezone.now(), reason
        row.save(update_fields=["state", "returned_by", "returned_at", "return_reason", "updated_at"])
        record_church(church, actor, "contenido_devuelto", f"Devolvió {kind.words} sin aprobarlo", obj)
    return row
