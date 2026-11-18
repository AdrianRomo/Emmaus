"""Who a person is expected to reach, and every write to a follow-up. Each write is one transaction
that saves the row and one church audit row. The audit row carries the id of the person it is about
and a fixed sentence: never their name, the reason, who the owner is or any contact data.

Callers have already answered who may write (``care.views``); these functions enforce what holds
whoever asks: everybody is of the same church, the owner has an account and is not the person the
follow-up is about, a closed follow-up does not change.
"""

from __future__ import annotations

from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from people.audit import record_church
from people.models import Person

from .models import FollowUp, FollowUpStep

Status = FollowUp.Status
CLOSED = "Este seguimiento ya está cerrado. Si hace falta buscar otra vez a la persona, crea uno nuevo."


class CareError(ValidationError):
    """A rule of the follow-ups refused the action; the message is ready for the screen."""


# ---- reading ------------------------------------------------------------------------------------
def _open_of(person: Person):
    return FollowUp.objects.filter(church_id=person.church_id, owner=person, status__in=FollowUp.OPEN)


def mine(person: Person, limit: int | None = None) -> list[FollowUp]:
    """The open follow-ups ``person`` owns: the overdue ones first, then by due date (those without
    a date last), then the oldest. Each one carries ``is_overdue``."""
    today = timezone.localdate()
    rows = list(_open_of(person).select_related("person"))
    for row in rows:
        row.is_overdue = bool(row.due_on and row.due_on < today)
    rows.sort(key=lambda row: (not row.is_overdue, row.due_on is None, row.due_on or date.max, row.created_at, row.pk))
    return rows if limit is None else rows[:limit]


def open_count(person: Person) -> int:
    return _open_of(person).count()


def unassigned_count(church, viewer: Person | None = None) -> int:
    """Open follow-ups nobody is responsible for: what the administration has to hand to somebody.
    Never one about ``viewer``: the count would tell them it exists (they cannot open it either)."""
    rows = FollowUp.objects.filter(church=church, status__in=FollowUp.OPEN, owner__isnull=True)
    if viewer is not None:
        rows = rows.exclude(person=viewer)
    return rows.count()


# ---- writing ------------------------------------------------------------------------------------
def _check_owner(church_id: int, about_id: int, owner: Person | None) -> None:
    if owner is None:
        return
    if owner.church_id != church_id or owner.merged_into_id:
        raise CareError("Esa persona no es de esta iglesia. Elige a alguien más como responsable.", code="owner")
    if not owner.user_id:
        raise CareError(
            f"{owner.first_name} todavía no tiene acceso a Emmaus, así que no vería el seguimiento. "
            "Dale acceso en Personas o elige a alguien más.",
            code="owner",
        )
    if owner.pk == about_id:
        raise CareError("Una persona no puede ser responsable de su propio seguimiento. Elige a alguien más.", code="owner")


def _check_due(due_on) -> None:
    if due_on and due_on < timezone.localdate():
        raise CareError("Esa fecha ya pasó. Elige un día de hoy en adelante, o déjala vacía.", code="due_on")


def _locked(follow_up: FollowUp) -> FollowUp:
    return FollowUp.objects.select_for_update(of=("self",)).select_related("church", "person", "owner").get(pk=follow_up.pk)


def _audit(follow_up: FollowUp, actor, action: str, summary: str) -> None:
    record_church(follow_up.church, actor, action, summary, follow_up, person_id=follow_up.person_id)


def create(church, actor, *, person: Person, reason: str, owner: Person | None = None, due_on=None) -> FollowUp:
    """A new follow-up about ``person``, pending. The owner and the date may come later."""
    if person.church_id != church.pk or person.merged_into_id:
        raise CareError("Esa persona no es de esta iglesia.", code="person")
    if reason not in FollowUp.Reason.values:
        raise CareError("Elige por qué hay que buscarla.", code="reason")
    _check_owner(church.pk, person.pk, owner)
    _check_due(due_on)
    with transaction.atomic():
        follow_up = FollowUp.objects.create(
            church=church, person=person, reason=reason, owner=owner, due_on=due_on, created_by=actor
        )
        _audit(follow_up, actor, "acompanar_creado", "Creó un seguimiento de persona")
    return follow_up


def assign(follow_up: FollowUp, actor, owner: Person | None) -> FollowUp:
    """Name who will do it, change who, or leave it without an owner (``None``)."""
    with transaction.atomic():
        follow_up = _locked(follow_up)
        if follow_up.owner_id == (owner.pk if owner else None):
            return follow_up
        if not follow_up.is_open:
            raise CareError(CLOSED, code="closed")
        _check_owner(follow_up.church_id, follow_up.person_id, owner)
        follow_up.owner = owner
        follow_up.save(update_fields=["owner", "updated_at"])
        verb = "Asignó" if owner else "Dejó sin responsable"
        _audit(follow_up, actor, "acompanar_asignado", f"{verb} un seguimiento de persona")
    return follow_up


def set_due(follow_up: FollowUp, actor, due_on) -> FollowUp:
    """The day it should be done by, or no day."""
    with transaction.atomic():
        follow_up = _locked(follow_up)
        if follow_up.due_on == due_on:
            return follow_up
        if not follow_up.is_open:
            raise CareError(CLOSED, code="closed")
        _check_due(due_on)
        follow_up.due_on = due_on
        follow_up.save(update_fields=["due_on", "updated_at"])
        _audit(follow_up, actor, "acompanar_asignado", "Cambió la fecha de un seguimiento de persona")
    return follow_up


def add_step(follow_up: FollowUp, actor, *, kind: str, by: Person | None, on=None) -> FollowUpStep:
    """One thing done to reach the person. The first one moves a pending follow-up to "En proceso"."""
    on = on or timezone.localdate()
    if kind not in FollowUpStep.Kind.values:
        raise CareError("Elige qué hiciste.", code="kind")
    if on > timezone.localdate():
        raise CareError("Ese día todavía no llega. Guarda lo que ya hiciste.", code="on")
    with transaction.atomic():
        follow_up = _locked(follow_up)
        if not follow_up.is_open:
            raise CareError("Este seguimiento ya está cerrado: ya no se le agrega nada.", code="closed")
        step = FollowUpStep.objects.create(follow_up=follow_up, kind=kind, on=on, by=by)
        if follow_up.status == Status.PENDING:
            follow_up.status = Status.IN_PROCESS
            follow_up.save(update_fields=["status", "updated_at"])
        _audit(follow_up, actor, "acompanar_paso", "Registró un paso de un seguimiento de persona")
    return step


STATUS_SUMMARIES = {
    Status.IN_PROCESS: "Puso en proceso un seguimiento de persona",
    Status.DONE: "Marcó como atendido un seguimiento de persona",
    Status.CANCELLED: "Canceló un seguimiento de persona",
}


def set_status(follow_up: FollowUp, actor, status: str) -> FollowUp:
    """Move an open follow-up to "En proceso", "Atendido" or "Cancelado". A closed one stays closed."""
    if status not in STATUS_SUMMARIES:
        raise CareError("Un seguimiento no puede volver a ese estado.", code="status")
    with transaction.atomic():
        follow_up = _locked(follow_up)
        if follow_up.status == status:
            return follow_up
        if not follow_up.is_open:
            raise CareError(CLOSED, code="closed")
        follow_up.status = status
        follow_up.closed_at = None if status == Status.IN_PROCESS else timezone.now()
        follow_up.save(update_fields=["status", "closed_at", "updated_at"])
        _audit(follow_up, actor, "acompanar_estado", STATUS_SUMMARIES[Status(status)])
    return follow_up
