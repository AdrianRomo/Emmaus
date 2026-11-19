"""«Cambiar decisión de seguimiento»: an interview's follow-up decision, changed after it was taken.

The first decision of an interview left «Por decidir» stays ``interviews.update_follow_up_decision`` (once, Sí/No in
place). This changes a decision already taken, «necesita seguimiento» to «no necesita seguimiento» or back, with a
brief reason; who may is ``EventAccess.can_correct`` (the campero's coordinador, an administrador of the event).

What it does to the care work touches only the interview's ordinary care task (``kind=seguimiento``,
``confidential=False``; the database allows one that is not cancelled: one_open_care_follow_up_per_interview). A
confidential referral and a safety report keep their own workflow and history: nothing here creates, cancels, reopens
or edits one. The owner's decisions of 2026-11-18:

* To «necesita seguimiento»: an open task stays as it is; a closed one («Atendido», or «Cancelado» by its owner) is
  reopened, «Pendiente» with the same owner, back in their queue, and its earlier closing stays in its history (its
  audit rows); with none, one is created as the first decision creates it. Never two.
* To «no necesita seguimiento»: an open task is never closed here, whatever was done on it. It stays open and is marked
  «Decisión cambiada» for its owner to review, on its page and in every list of follow-ups, until they close it with its
  own audited form, keep it open with «Continuar seguimiento» (``continue_follow_up``: a brief reason, read on the task
  by whoever may manage it; the interview's decision stays), or the decision changes back. Nothing here reads a closing
  note or anything else only the care team sees, so what other roles see never depends on one.
* A reopened task says «Reabierto el …» on its page and on every list row until its owner moves it on (another status
  saved on it) or closes it.

Every change leaves a ``FollowUpDecisionChange`` (who, when, both decisions, the reason, what happened to the task),
shown on the interview's record, and a ``seguimiento_corregido`` audit row without the reason.
"""

from __future__ import annotations

from datetime import datetime

from django.db import transaction
from django.utils import timezone

from ..models import AuditEvent, FollowUp, FollowUpDecisionChange, Interview
from . import audit, follow_ups
from ..templatetags.emmaus import fecha_corta
from .interviews import InterviewError, NotAllowed, follow_up_suggestions

AUDIT_ACTION = "seguimiento_corregido"
DECIDED = (Interview.FollowUpDecision.REQUIRED, Interview.FollowUpDecision.NOT_REQUIRED)
REASON_MAX = 200
REASON_REQUIRED = "Escribe brevemente por qué cambias la decisión."
REASON_TOO_LONG = f"Escribe el motivo en {REASON_MAX} caracteres o menos."
Care = FollowUpDecisionChange.Care
CLOSED = (FollowUp.Status.DONE, FollowUp.Status.CANCELLED)


def other(decision: str) -> str:
    """The decision a change goes to: the one it is not."""
    return Interview.FollowUpDecision.NOT_REQUIRED if decision == Interview.FollowUpDecision.REQUIRED else Interview.FollowUpDecision.REQUIRED


def _ordinary(interview_id: int):
    return FollowUp.objects.filter(interview_id=interview_id, kind=FollowUp.Kind.CARE, confidential=False)


def ordinary_follow_up(interview: Interview, *, lock: bool = False) -> FollowUp | None:
    """The interview's ordinary care task: the one not cancelled (there is at most one), else the newest cancelled."""
    rows = _ordinary(interview.pk)
    if lock:
        rows = rows.select_for_update()
    return rows.exclude(status=FollowUp.Status.CANCELLED).first() or rows.filter(status=FollowUp.Status.CANCELLED).order_by("-updated_at", "-id").first()


def care_effect(decision: str, follow_up: FollowUp | None) -> str:
    """What changing to ``decision`` does to the ordinary task ("" when there is none to speak of). It depends on the
    task's status only: never on its notes."""
    if decision == Interview.FollowUpDecision.REQUIRED:
        if follow_up is None:
            return Care.CREATED
        return Care.KEPT if follow_up.is_open else Care.REOPENED
    if follow_up is None:
        return ""
    return Care.LEFT_OPEN if follow_up.is_open else Care.KEPT


def preview_sentence(interview: Interview) -> str:
    """What the change the form offers will do to the care task, in words, before it is done ("" when nothing)."""
    target = other(interview.follow_up_decision)
    follow_up = ordinary_follow_up(interview)
    effect = care_effect(target, follow_up)
    if effect == Care.REOPENED:
        state = "atendido" if follow_up.status == FollowUp.Status.DONE else "cancelado"
        return f"Su seguimiento está {state}: si la cambias, se reabrirá y volverá a la lista de quien lo atiende."
    if effect == Care.KEPT:
        if target == Interview.FollowUpDecision.REQUIRED:
            return "Ya tiene un seguimiento abierto: seguirá como está."
        return "Su seguimiento ya está cerrado y seguirá como está."
    return {
        Care.CREATED: "Si la cambias, se creará su seguimiento.",
        Care.LEFT_OPEN: "Su seguimiento sigue abierto: si la cambias, quien lo atiende lo revisará y lo cerrará.",
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
        follow_up = ordinary_follow_up(locked, lock=True)
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
        elif effect == Care.REOPENED:
            # Back in its owner's queue, the same owner. Its closing stays in its history: the audit row that closed it,
            # and this one, which says what it was. The closing note (care team only) is left as it was.
            was, closed_at = follow_up.status, follow_up.resolved_at
            follow_up.status = FollowUp.Status.PENDING
            follow_up.resolved_at = None
            follow_up.save(update_fields=["status", "resolved_at", "updated_at"])
            audit.record(
                access.user, locked.event, "seguimiento",
                f"Reabrió un seguimiento del campero #{code} al cambiar la decisión de su entrevista", follow_up,
                status=follow_up.status, reabierto=was, **({"cerrado": closed_at.isoformat()} if closed_at else {}),
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
    """Who and what, as «X necesita seguimiento.» says the first decision; then what happened to the task."""
    name = record.interview.participant.full_name
    if record.decision == Interview.FollowUpDecision.REQUIRED:
        text = f"Cambiaste la decisión de {name}: ahora necesita seguimiento."
        extra = {
            Care.CREATED: "Se creó su seguimiento.",
            Care.REOPENED: "Se reabrió su seguimiento.",
            Care.KEPT: "Ya tenía un seguimiento abierto y sigue como estaba.",
        }.get(record.care)
    else:
        text = f"Cambiaste la decisión de {name}: ya no necesita seguimiento."
        extra = "Su seguimiento sigue abierto hasta que quien lo atiende lo revise y lo cierre." if record.care == Care.LEFT_OPEN else None
    return f"{text} {extra}" if extra else text


# ---- «Continuar seguimiento» ---------------------------------------------------------------------

CONTINUE_ACTION = "seguimiento_continuado"


def continue_follow_up(access, follow_up: FollowUp, *, reason: str) -> FollowUpDecisionChange:
    """Owner decision of 2026-11-18: whoever may manage a task its interview's decision left open for review
    («no necesita seguimiento») keeps it open anyway, with a brief reason. The review mark goes, the task stays open in
    its owner's queue as it was, and the interview's decision and record do not change. The reason is kept on the
    change it answers, shown on the task to the people who may manage it; the audit row says who and when only.
    Nothing here reads a note."""
    reason = clean_reason(reason)
    if not reason:
        raise InterviewError("Escribe brevemente por qué continúas el seguimiento.")
    if len(reason) > REASON_MAX:
        raise InterviewError(REASON_TOO_LONG)
    with transaction.atomic():
        locked = FollowUp.objects.select_for_update().select_related("participant").get(pk=follow_up.pk)
        if not access.can_manage_follow_up(locked):
            raise NotAllowed("No tienes permiso para atender este seguimiento.")
        pending = waiting_on_owner([locked]).get(locked.pk)
        if pending is None:
            raise InterviewError(f"El seguimiento de {locked.participant.full_name} ya no espera una revisión.")
        record = FollowUpDecisionChange.objects.select_for_update().get(pk=pending.pk)
        record.continued_by = access.user
        record.continued_at = timezone.now()
        record.continued_reason = reason
        record.save(update_fields=["continued_by", "continued_at", "continued_reason"])
        audit.record(
            access.user, locked.event, CONTINUE_ACTION,
            f"Continuó un seguimiento del campero #{locked.participant.code} que su entrevista ya no pide", locked,
            status=locked.status,
        )
    return record


# ---- what the task's own screens and the lists show ---------------------------------------------


def _newest_changes(interview_ids) -> dict[int, FollowUpDecisionChange]:
    newest: dict[int, FollowUpDecisionChange] = {}
    for record in FollowUpDecisionChange.objects.filter(interview_id__in=interview_ids).select_related("changed_by", "continued_by"):
        newest.setdefault(record.interview_id, record)  # ordered newest first
    return newest


def _ordinary_open(rows) -> dict[int, FollowUp]:
    return {
        f.interview_id: f
        for f in rows
        if f.is_open and f.kind == FollowUp.Kind.CARE and not f.confidential and f.interview_id is not None
    }


def _states(rows) -> tuple[dict[int, FollowUpDecisionChange], dict[int, FollowUpDecisionChange], dict[int, FollowUpDecisionChange]]:
    """({id: change to review}, {id: change continued}, {id: change that reopened it}) for the open ordinary tasks of
    ``rows``, from their interview's newest change. A reopened task counts as reopened while it is «Pendiente» and
    nobody has saved another status on it since. Statuses only: never a note."""
    candidates = _ordinary_open(rows)
    if not candidates:
        return {}, {}, {}
    newest = _newest_changes(candidates)
    waiting, continued, reopened = {}, {}, {}
    for interview_id, f in candidates.items():
        record = newest.get(interview_id)
        if record is None or record.follow_up_id != f.id:
            continue
        if record.care == Care.LEFT_OPEN:
            (continued if record.continued_at else waiting)[f.id] = record
        elif record.care == Care.REOPENED and f.status == FollowUp.Status.PENDING:
            reopened[f.id] = record
    if reopened:
        moved = AuditEvent.objects.filter(
            action="seguimiento", object_type="FollowUp", object_id__in=[str(pk) for pk in reopened]
        ).exclude(metadata__status=FollowUp.Status.PENDING).values_list("object_id", "created_at")
        for object_id, at in moved:
            record = reopened.get(int(object_id))
            if record is not None and at > record.changed_at:
                del reopened[int(object_id)]  # its owner moved it on since («En proceso», closed and reopened by hand)
    return waiting, continued, reopened


def waiting_on_owner(rows) -> dict[int, FollowUpDecisionChange]:
    """{follow-up id: change} for the open ordinary tasks whose interview's newest change said «no necesita seguimiento»
    and left them open, not continued since: what their owner has to review, then close or continue."""
    return _states(rows)[0]


def reopened_label(record: FollowUpDecisionChange) -> str:
    return f"Reabierto el {fecha_corta(record.changed_at).split(',')[0]}"


def mark_for_review(rows, user) -> list:
    """The rows of a list of follow-ups, each with ``decision_change`` (the change to review: «Decisión cambiada»),
    ``reopened`` («Reabierto el …», or ""), ``review_is_mine`` (the viewer attends it: their row offers «Revisar») and
    ``reopened_is_mine``."""
    rows = list(rows)
    waiting, _, reopened = _states(rows)
    me = getattr(user, "id", None)
    for f in rows:
        f.decision_change = waiting.get(f.id)
        f.reopened = reopened_label(reopened[f.id]) if f.id in reopened else ""
        f.review_is_mine = bool(f.decision_change and f.assigned_to_id == me)
        f.reopened_is_mine = bool(f.reopened and f.assigned_to_id == me)
    return rows


def mine_first(rows) -> list:
    """A short list (a home shows five) with what waits on the viewer first (their reviews, then the tasks reopened for
    them), so it is never cut off; otherwise in its order, kind by kind (a list ordered by kind keeps it)."""
    return sorted(rows, key=lambda f: (f.kind, not f.review_is_mine, not f.reopened_is_mine))


def note_for(follow_up: FollowUp) -> dict | None:
    """What the task's page tells about its interview's decision while it is open: ``{"state": ..., "change": ...}``
    with state «revisar» (to review), «continuado» (kept open anyway: who, when, why) or «reabierto» (with its date)."""
    waiting, continued, reopened = _states([follow_up])
    if follow_up.pk in waiting:
        return {"state": "revisar", "change": waiting[follow_up.pk]}
    if follow_up.pk in continued:
        return {"state": "continuado", "change": continued[follow_up.pk]}
    if follow_up.pk in reopened:
        return {"state": "reabierto", "change": reopened[follow_up.pk], "label": reopened_label(reopened[follow_up.pk])}
    return None


def task_history(follow_up: FollowUp) -> list[dict]:
    """A reopened task's states, oldest first: registered, then each change of its status saved on it (who, when, which),
    from its audit rows; the reopening says what it was. Empty for a task never reopened. Only statuses: never a note,
    and a save that left the status as it was (an owner, a note) is not a state."""
    if follow_up.kind != FollowUp.Kind.CARE or follow_up.confidential:
        return []
    if not follow_up.decision_changes.filter(care=Care.REOPENED).exists():
        return []
    labels = dict(FollowUp.Status.choices)
    creator = follow_up.created_by.get_full_name() if follow_up.created_by else ""
    history = [{"when": follow_up.created_at, "who": creator, "label": "Registrado"}]
    last = FollowUp.Status.PENDING
    rows = AuditEvent.objects.filter(
        event_id=follow_up.event_id, action="seguimiento", object_type="FollowUp", object_id=str(follow_up.pk)
    ).order_by("created_at", "id")
    for row in rows:
        status = row.metadata.get("status")
        was = row.metadata.get("reabierto")
        if status not in labels or (status == last and was not in labels):
            continue
        if was in labels:
            if last != was:  # closed where no row says who: its status and when, from the reopening
                closed_at = row.metadata.get("cerrado")
                history.append({"when": datetime.fromisoformat(closed_at) if closed_at else None, "who": "", "label": labels[was]})
            label = f"Reabierto: su entrevista volvió a necesitar seguimiento (estaba {labels[was].lower()})"
        else:
            label = labels[status]
        history.append({"when": row.created_at, "who": row.actor_label, "label": label})
        last = status
    return history
