"""«Cambiar decisión de seguimiento»: an interview's follow-up decision, changed after it was taken.

The first decision of an interview left «Por decidir» stays ``interviews.update_follow_up_decision`` (once, Sí/No in
place). This changes a decision already taken, «necesita seguimiento» to «no necesita seguimiento» or back, with a
brief reason; who may is ``EventAccess.can_correct`` (the campero's coordinador, an administrador of the event).

What it does to the care work touches only the interview's ordinary care follow-up (``kind=seguimiento``,
``confidential=False``). A confidential referral and a safety report keep their own workflow and history: nothing here
creates, cancels, reopens or edits one.

* To «necesita seguimiento»: the ordinary follow-up is created as the first decision creates it, once. One that is
  not cancelled (open or already attended) stays as it is (the database allows one: one_open_care_follow_up_per_interview).
* To «no necesita seguimiento»: an open follow-up nobody has started (``care_started``) is cancelled, and its page says
  why. One already started is never cancelled here: it stays open, and its page and the «Seguimiento» list tell whoever
  attends it that the decision changed (who, when, the reason) until they close it there, with its own audited form.

Every change leaves a ``FollowUpDecisionChange`` (who, when, both decisions, the reason, what happened to the follow-up),
shown on the interview's record, and a ``seguimiento_corregido`` audit row without the reason.
"""

from __future__ import annotations

from django.db import transaction

from ..models import AuditEvent, FollowUp, FollowUpDecisionChange, Interview
from . import audit, follow_ups
from .interviews import InterviewError, NotAllowed, follow_up_suggestions

AUDIT_ACTION = "seguimiento_corregido"
DECIDED = (Interview.FollowUpDecision.REQUIRED, Interview.FollowUpDecision.NOT_REQUIRED)
REASON_MAX = 200
REASON_REQUIRED = "Escribe brevemente por qué cambias la decisión."
REASON_TOO_LONG = f"Escribe el motivo en {REASON_MAX} caracteres o menos."
Care = FollowUpDecisionChange.Care


def other(decision: str) -> str:
    """The decision a change goes to: the one it is not."""
    return Interview.FollowUpDecision.NOT_REQUIRED if decision == Interview.FollowUpDecision.REQUIRED else Interview.FollowUpDecision.REQUIRED


def ordinary_follow_up(interview: Interview) -> FollowUp | None:
    """The interview's ordinary care follow-up that counts: the one not cancelled (there is at most one)."""
    return (
        FollowUp.objects.filter(interview=interview, kind=FollowUp.Kind.CARE, confidential=False)
        .exclude(status=FollowUp.Status.CANCELLED)
        .first()
    )


def care_started(follow_up: FollowUp) -> bool:
    """Somebody already worked on it: it is no longer «Pendiente», it has details or a closing note, or somebody saved
    it on its own screens (its status, who attends it or its note: each save leaves a ``seguimiento`` audit row on it).
    Being given its default owner when it was created is not work."""
    if follow_up.status != FollowUp.Status.PENDING or follow_up.details.strip() or follow_up.resolution_note.strip():
        return True
    return AuditEvent.objects.filter(
        event_id=follow_up.event_id, action="seguimiento", object_type="FollowUp", object_id=str(follow_up.pk)
    ).exists()


def care_effect(decision: str, follow_up: FollowUp | None) -> str:
    """What changing to ``decision`` does to the ordinary follow-up ("" when there is none to speak of)."""
    if decision == Interview.FollowUpDecision.REQUIRED:
        return Care.KEPT if follow_up is not None else Care.CREATED
    if follow_up is None:
        return ""
    if not follow_up.is_open:
        return Care.KEPT
    return Care.LEFT_OPEN if care_started(follow_up) else Care.CANCELLED


def preview(interview: Interview) -> str:
    """``care_effect`` of the change the form offers, for its page to say before anything is done."""
    return care_effect(other(interview.follow_up_decision), ordinary_follow_up(interview))


def preview_sentence(interview: Interview) -> str:
    """What the change the form offers will do to the care follow-up, in words ("" when nothing)."""
    effect = preview(interview)
    if effect == Care.KEPT:
        if other(interview.follow_up_decision) == Interview.FollowUpDecision.REQUIRED:
            return "Ya tiene un seguimiento: seguirá como está."
        return "Su seguimiento ya está atendido y seguirá como está."
    return {
        Care.CREATED: "Si la cambias, se creará su seguimiento.",
        Care.CANCELLED: "Si la cambias, su seguimiento se cancelará: nadie lo ha empezado.",
        Care.LEFT_OPEN: "Su seguimiento ya se empezó: si la cambias, seguirá abierto y quien lo atiende verá el cambio para cerrarlo.",
    }.get(effect, "")


def clean_reason(reason: str | None) -> str:
    return " ".join((reason or "").split())


def change(access, interview: Interview, decision: str, *, reason: str) -> FollowUpDecisionChange:
    """Change a decision already taken. Refuses a decision that is not taken yet («Por decidir»: decide it on the
    record), and one that is already what was asked (a second tab sending the same change)."""
    reason = clean_reason(reason)
    if decision not in DECIDED:
        raise InterviewError("Elige la nueva decisión de seguimiento.")
    if not reason:
        raise InterviewError(REASON_REQUIRED)
    if len(reason) > REASON_MAX:
        raise InterviewError(REASON_TOO_LONG)
    with transaction.atomic():
        locked = Interview.objects.select_for_update().select_related("participant", "event").get(pk=interview.pk)
        if not access.can_correct(locked):
            raise NotAllowed("No tienes permiso para cambiar esta entrevista.")
        previous = locked.follow_up_decision
        name = locked.participant.full_name
        if previous not in DECIDED:
            raise InterviewError(f"El seguimiento de {name} todavía no se decide: decídelo en su entrevista.")
        if previous == decision:
            raise InterviewError(f"Ya estaba así: {name} {FollowUpDecisionChange.WORDS[decision]}.")
        locked.follow_up_decision = decision
        locked.revision += 1
        locked.save(update_fields=["follow_up_decision", "revision"])
        follow_up = (
            FollowUp.objects.select_for_update()
            .filter(interview=locked, kind=FollowUp.Kind.CARE, confidential=False)
            .exclude(status=FollowUp.Status.CANCELLED)
            .first()
        )
        effect = care_effect(decision, follow_up)
        code = locked.participant.code
        if effect == Care.CREATED:
            # As the first decision creates it (update_follow_up_decision): the reason from the hints, its default owner.
            suggestions = follow_up_suggestions(locked)
            follow_up = follow_ups.create(
                event=locked.event,
                participant=locked.participant,
                interview=locked,
                kind=FollowUp.Kind.CARE,
                reason=suggestions[0][0] if suggestions else FollowUp.Reason.COORDINATOR,
                created_by=access.user,
            )
        elif effect == Care.CANCELLED:
            follow_up.status = FollowUp.Status.CANCELLED
            follow_up.save(update_fields=["status", "updated_at"])
            audit.record(
                access.user, locked.event, "seguimiento",
                f"Canceló un seguimiento del campero #{code} al cambiar la decisión de su entrevista", follow_up,
                status=follow_up.status,
            )
        record = FollowUpDecisionChange.objects.create(
            interview=locked, previous=previous, decision=decision, reason=reason, changed_by=access.user,
            follow_up=follow_up, care=effect,
        )
        audit.record(
            access.user, locked.event, AUDIT_ACTION, f"Cambió la decisión de seguimiento del campero #{code}", locked,
            antes=previous, despues=decision, seguimiento=effect,
        )
    return record


def message(record: FollowUpDecisionChange) -> str:
    """Who and what, as «X necesita seguimiento.» says the first decision; then what happened to the follow-up."""
    name = record.interview.participant.full_name
    if record.decision == Interview.FollowUpDecision.REQUIRED:
        text = f"Cambiaste la decisión de {name}: ahora necesita seguimiento."
    else:
        text = f"Cambiaste la decisión de {name}: ya no necesita seguimiento."
    extra = {
        Care.CREATED: "Se creó su seguimiento.",
        Care.CANCELLED: "Se canceló su seguimiento, que nadie había empezado.",
        Care.LEFT_OPEN: "Su seguimiento ya se había empezado: sigue abierto para que quien lo atiende lo cierre.",
    }.get(record.care)
    if record.care == Care.KEPT and record.decision == Interview.FollowUpDecision.REQUIRED:
        extra = "Ya tenía un seguimiento y sigue como estaba."
    return f"{text} {extra}" if extra else text


# ---- what the follow-up's own screens show ------------------------------------------------------


def waiting_on_owner(rows) -> dict[int, FollowUpDecisionChange]:
    """{follow-up id: change} for the open ordinary follow-ups whose interview's newest change said «no necesita
    seguimiento» and left them open because their care had started: what their owner has to resolve."""
    candidates = {
        f.interview_id: f
        for f in rows
        if f.is_open and f.kind == FollowUp.Kind.CARE and not f.confidential and f.interview_id is not None
    }
    if not candidates:
        return {}
    newest: dict[int, FollowUpDecisionChange] = {}
    for record in FollowUpDecisionChange.objects.filter(interview_id__in=candidates).select_related("changed_by"):
        newest.setdefault(record.interview_id, record)  # ordered newest first
    return {
        f.id: record
        for interview_id, f in candidates.items()
        if (record := newest.get(interview_id)) and record.care == Care.LEFT_OPEN and record.follow_up_id == f.id
    }


def note_for(follow_up: FollowUp) -> FollowUpDecisionChange | None:
    """The change the follow-up's page tells about: the one its owner has to resolve (``care == abierto``), or the one
    that cancelled it (``care == cancelado``)."""
    waiting = waiting_on_owner([follow_up]).get(follow_up.id)
    if waiting is not None:
        return waiting
    if follow_up.status == FollowUp.Status.CANCELLED:
        return follow_up.decision_changes.filter(care=Care.CANCELLED).select_related("changed_by").first()
    return None
