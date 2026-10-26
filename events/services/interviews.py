"""Interview lifecycle for shared interview stations.

Concurrency model
-----------------
* One live interview per campero is a database constraint (partial unique index).
* Every write takes ``SELECT ... FOR UPDATE`` on the interview row, then checks:
  1. the editing browser holds the current *claim* (a random token whose hash is stored
     on the interview and which expires after ``CLAIM_MINUTES`` without activity), and
  2. the form's ``revision`` matches the row, so a stale tab cannot overwrite newer answers.
* Finalizing is idempotent: a repeated submit of a completed interview returns it unchanged.
* "Liberar" (``revoke_claim``) frees the interview but keeps the released token's hash with no
  expiry, so the browser that held it is refused (``ClaimReleased``) instead of silently taking
  it back on its next save. Other browsers may open it; the same browser may too, on purpose,
  by opening it again (``claim(reopen=True)``) or with "Tomar el control aquí".

Supervised capture ("Entrevista a solas")
----------------------------------------
The coordinator's login is *ended* before the campero touches the computer. The browser
then holds only an anonymous session bound to one ``ParticipantSession`` (one interview,
one station). Coming back to privileged screens requires the coordinator's password.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth import authenticate, login, logout
from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import (
    AnswerRevision,
    ConsentRecord,
    Event,
    EventParticipant,
    FollowUp,
    Interview,
    InterviewStation,
    InterviewStatus,
    ParticipantSession,
    hash_token,
    new_token,
)
from . import audit
from .participants import guardian_authorized
from .questionnaire import (
    AnswerValue,
    Step,
    all_questions,
    build_steps,
    describe_answer,
    is_visible,
    load_answers,
    options_by_code,
    validate_step,
    visible_steps,
    write_answers,
)

CLAIM_MINUTES = 15
KIOSK_IDLE_MINUTES = 10
KIOSK_MAX_MINUTES = 60
UNLOCK_MAX_ATTEMPTS = 5
UNLOCK_COOLDOWN_SECONDS = 60
HIDDEN_BY_CORRECTION = "ocultada por corrección"  # AnswerRevision.reason of an answer a correction hid
SESSION_LOCKS_KEY = "interview_claims"
KIOSK_KEY = "kiosk"
# What happened and what to do, the same on every screen that loses the interview.
CLAIM_LOST_MESSAGE = (
    "Esta entrevista se abrió en otra computadora o pasó mucho tiempo sin cambios. Vuelve a abrirla desde tu inicio."
)
NOT_IN_PROGRESS_MESSAGE = "Esta entrevista ya no está en curso. En la ficha del campero ves cómo quedó."
KIOSK_CLOSED_MESSAGE = "Esta pantalla ya se cerró. Avisa a tu coordinador."
WRONG_PASSWORD_MESSAGE = "La contraseña no es correcta. Revísala e inténtalo otra vez."


class InterviewError(Exception):
    """A user-facing problem. ``message`` is shown as-is, in Spanish."""

    code = "error"

    def __init__(self, message: str, *, interview: Interview | None = None):
        super().__init__(message)
        self.message = message
        self.interview = interview


class AlreadyCompleted(InterviewError):
    code = "completada"


class AlreadyInProgress(InterviewError):
    code = "en_curso"


class ClaimConflict(InterviewError):
    code = "en_otra_computadora"


class ClaimReleased(ClaimConflict):
    """This browser's claim was released by an administrator ("Computadoras en uso" → "Liberar")."""

    code = "liberada"


class ClaimLost(InterviewError):
    code = "sin_control"


class StaleRevision(InterviewError):
    code = "desactualizada"


class ConsentMissing(InterviewError):
    code = "sin_autorizacion"


class NotAllowed(InterviewError):
    code = "sin_permiso"


class KioskEnded(InterviewError):
    code = "sesion_terminada"


# ---- claims stored in the (server-side) Django session ------------------------------------


def _session_claims(session) -> dict:
    return session.get(SESSION_LOCKS_KEY, {})


def session_claim_token(session, interview: Interview) -> str | None:
    return _session_claims(session).get(str(interview.public_id))


def _remember_claim(session, interview: Interview, token: str) -> None:
    claims = dict(_session_claims(session))
    claims[str(interview.public_id)] = token
    # Keep the session small: only the latest few claims matter.
    session[SESSION_LOCKS_KEY] = dict(list(claims.items())[-8:])


def _forget_claim(session, interview: Interview) -> None:
    claims = dict(_session_claims(session))
    if claims.pop(str(interview.public_id), None) is not None:
        session[SESSION_LOCKS_KEY] = claims


def _set_claim(interview: Interview, user, minutes: int = CLAIM_MINUTES) -> str:
    token = new_token()
    now = timezone.now()
    interview.lock_token_hash = hash_token(token)
    interview.lock_holder = user
    interview.lock_expires_at = now + timedelta(minutes=minutes)
    return token


def _clear_claim(interview: Interview) -> None:
    interview.lock_token_hash = ""
    interview.lock_holder = None
    interview.lock_expires_at = None


def _release_claim(interview: Interview) -> None:
    """Free the interview, but keep the released token's hash (with no expiry) on the row.

    Nobody holds it any more (``lock_is_active`` is false), so any browser may open it. The
    browser that held it is recognized by ``_is_released_claim`` and refused until it opens
    the interview again on purpose. ``_set_claim`` always sets an expiry and ``_clear_claim``
    empties the hash, so "hash without expiry" means "released" and nothing else.
    """
    interview.lock_holder = None
    interview.lock_expires_at = None


def _is_released_claim(interview: Interview, token: str | None) -> bool:
    if not (token and interview.lock_token_hash) or interview.lock_expires_at is not None:
        return False
    return secrets.compare_digest(interview.lock_token_hash, hash_token(token))


def claim_released_here(interview: Interview, session) -> bool:
    """True when "Liberar" stopped this browser and no other browser has opened the interview since."""
    return _is_released_claim(interview, session_claim_token(session, interview))


def _close_participant_sessions(interview: Interview, reason: str) -> None:
    ParticipantSession.objects.filter(interview=interview).exclude(state=ParticipantSession.State.CLOSED).update(
        state=ParticipantSession.State.CLOSED, ended_at=timezone.now(), end_reason=reason
    )


# ---- consent ----------------------------------------------------------------------------------


@dataclass
class ConsentRequirement:
    key: str
    label: str
    help: str
    satisfied: bool
    confirm_now: bool


def consent_requirements(participant: EventParticipant) -> list[ConsentRequirement]:
    event = participant.event
    requirements = []
    if participant.is_minor:
        if event.requires_guardian_authorization:
            has_guardian = guardian_authorized(participant)
            requirements.append(
                ConsentRequirement(
                    key="guardian",
                    label="Tengo la autorización firmada de su padre, madre o tutor legal",
                    help="Confírmalo solo si viste el formato firmado o aparece en la lista de autorizaciones.",
                    satisfied=has_guardian,
                    confirm_now=not has_guardian,
                )
            )
        requirements.append(
            ConsentRequirement(
                key="assent",
                label=f"Le expliqué el aviso de privacidad a {participant.first_name} y está de acuerdo en conversar",
                help="Si no quiere conversar, no inicies la entrevista. Puede decidirlo en otro momento.",
                satisfied=False,
                confirm_now=True,
            )
        )
    else:
        requirements.append(
            ConsentRequirement(
                key="consent",
                label=f"Le expliqué el aviso de privacidad a {participant.first_name} y acepta participar",
                help="Puede elegir “Prefiero no responder” en cualquier pregunta.",
                satisfied=False,
                confirm_now=True,
            )
        )
    return requirements


def _record_consents(participant, interview, confirmed: set[str], actor) -> None:
    for requirement in consent_requirements(participant):
        if requirement.satisfied:
            continue
        if requirement.key not in confirmed:
            raise ConsentMissing("Para comenzar, confirma cada autorización de la lista.")
        kind = {
            "guardian": ConsentRecord.Kind.GUARDIAN,
            "assent": ConsentRecord.Kind.ASSENT,
            "consent": ConsentRecord.Kind.SELF,
        }[requirement.key]
        ConsentRecord.objects.create(
            participant=participant,
            interview=interview if kind != ConsentRecord.Kind.GUARDIAN else None,
            kind=kind,
            method=ConsentRecord.Method.PAPER if kind == ConsentRecord.Kind.GUARDIAN else ConsentRecord.Method.VERBAL,
            recorded_by=actor,
        )


def event_blockers(event: Event) -> list[str]:
    """Why interviews cannot start yet, each with what to do (a coordinator reads them)."""
    problems = []
    if event.status == Event.Status.DRAFT:
        problems.append("El evento todavía no está en curso. Avisa a la administración.")
    elif event.status != Event.Status.ACTIVE:
        problems.append("El evento ya terminó. Si falta alguna entrevista, avisa a la administración.")
    if not event.questionnaire_version_id:
        problems.append("Todavía no hay un cuestionario publicado para este evento. Avisa a la administración.")
    if not event.procedures_approved:
        problems.append(
            "La administración todavía no confirma los procedimientos de autorización y protección de la iglesia. Avísale."
        )
    return problems


# ---- lifecycle --------------------------------------------------------------------------------


def start_interview(
    access,
    participant: EventParticipant,
    *,
    mode: str,
    station: InterviewStation | None,
    confirmed_consents: set[str],
    session,
) -> Interview:
    event = access.event
    blockers = event_blockers(event)
    if blockers:
        raise NotAllowed(blockers[0])
    if mode not in Interview.Mode.values:
        raise InterviewError("Elige cómo se va a responder la entrevista.")
    try:
        with transaction.atomic():
            locked = EventParticipant.objects.select_for_update().get(pk=participant.pk, event=event)
            if not access.can_interview(locked):
                raise NotAllowed("Este campero no está asignado a ti.")
            existing = Interview.objects.filter(participant=locked).exclude(status=Interview.Status.DISCARDED).first()
            if existing and existing.status == Interview.Status.COMPLETED:
                raise AlreadyCompleted("Este campero ya tiene una entrevista completada.", interview=existing)
            if existing:
                raise AlreadyInProgress("Este campero ya tiene una entrevista en curso.", interview=existing)
            interview = Interview(
                event=event,
                participant=locked,
                version=event.questionnaire_version,
                mode=mode,
                conducted_by=access.user,
                station=station,
            )
            token = _set_claim(interview, access.user)
            interview.save()
            _record_consents(locked, interview, confirmed_consents, access.user)
            locked.interview_status = InterviewStatus.IN_PROGRESS
            locked.updated_by = access.user
            locked.save(update_fields=["interview_status", "updated_by", "updated_at"])
            audit.record(
                access.user,
                event,
                "entrevista_iniciada",
                f"Inició la entrevista del campero #{locked.code} ({interview.get_mode_display().lower()})",
                interview,
                station=station.name if station else "",
            )
    except IntegrityError:
        # Another station created the interview between our check and insert.
        existing = Interview.objects.filter(participant=participant).exclude(status=Interview.Status.DISCARDED).first()
        raise AlreadyInProgress("Otra computadora acaba de iniciar la entrevista de este campero.", interview=existing)
    _remember_claim(session, interview, token)
    return interview


def claim(access, interview: Interview, session, *, take_over: bool = False, station=None, reopen: bool = False) -> Interview:
    """Make this browser the editor of an in-progress interview.

    ``reopen`` means the person opened the interview on purpose ("Retomar entrevista" on
    "Mi inicio", a list, the camper page). Only then may a browser whose claim an administrator
    released pick the interview up again without "Tomar el control aquí"; a save, the review
    or the hand-over from that browser raise ``ClaimReleased``.
    """
    with transaction.atomic():
        locked = Interview.objects.select_for_update().select_related("participant").get(pk=interview.pk)
        if locked.status != Interview.Status.IN_PROGRESS:
            raise AlreadyCompleted(NOT_IN_PROGRESS_MESSAGE, interview=locked)
        if not access.can_interview(locked.participant):
            raise NotAllowed("Este campero no está asignado a ti.")
        token = session_claim_token(session, locked)
        now = timezone.now()
        if locked.lock_matches(token, now):
            locked.lock_expires_at = now + timedelta(minutes=CLAIM_MINUTES)
            locked.save(update_fields=["lock_expires_at"])
            return locked
        if not (take_over or reopen) and _is_released_claim(locked, token):
            raise ClaimReleased(
                "La administración liberó esta entrevista. Esta computadora ya no puede guardar.", interview=locked
            )
        if locked.lock_is_active(now) and not take_over:
            raise ClaimConflict("Esta entrevista está abierta en otra computadora.", interview=locked)
        was_active = locked.lock_is_active(now)
        _close_participant_sessions(locked, ParticipantSession.EndReason.TAKEN_OVER)
        token = _set_claim(locked, access.user)
        if station is not None:
            locked.station = station
        locked.save(update_fields=["lock_token_hash", "lock_holder", "lock_expires_at", "station"])
        audit.record(
            access.user,
            locked.event,
            "entrevista_control" if was_active else "entrevista_retomada",
            f"{'Tomó el control de' if was_active else 'Retomó'} la entrevista del campero #{locked.participant.code}",
            locked,
        )
    _remember_claim(session, locked, token)
    return locked


def _check_writable(locked: Interview, token: str | None, revision) -> None:
    if locked.status == Interview.Status.COMPLETED:
        raise AlreadyCompleted("Esta entrevista ya se completó.", interview=locked)
    if locked.status == Interview.Status.DISCARDED:
        raise InterviewError("Esta entrevista se descartó.", interview=locked)
    if not locked.lock_matches(token):
        raise ClaimLost(CLAIM_LOST_MESSAGE, interview=locked)
    try:
        posted = int(revision)
    except (TypeError, ValueError):
        posted = -1
    if posted != locked.revision:
        raise StaleRevision(
            "Las respuestas cambiaron desde que abriste esta pantalla. Te mostramos la versión más reciente: revísala y guarda otra vez.",
            interview=locked,
        )


def save_step(interview: Interview, step: Step, data, *, token: str | None, actor, by_participant: bool) -> tuple[Interview, dict]:
    with transaction.atomic():
        locked = Interview.objects.select_for_update().select_related("version", "participant").get(pk=interview.pk)
        _check_writable(locked, token, data.get("revision"))
        answers = load_answers(locked)
        cleaned, errors = validate_step(step, data, answers)
        if errors:
            return locked, errors
        write_answers(locked, cleaned, actor=actor, by_participant=by_participant)
        now = timezone.now()
        locked.revision += 1
        locked.last_activity_at = now
        if not by_participant:
            locked.lock_expires_at = now + timedelta(minutes=CLAIM_MINUTES)
        locked.save(update_fields=["revision", "last_activity_at", "lock_expires_at"])
    return locked, {}


def follow_up_suggestions(
    interview: Interview, answers: dict[int, AnswerValue] | None = None, *, visible_ids: set[int] | None = None
) -> list[tuple[str, str]]:
    """Deterministic hints for the coordinator. They never decide anything on their own.

    Only questions the viewer may see contribute (``visible_ids``), so a hint can never
    reveal a confidential answer. Confidential requests for support are routed to the
    care team separately (see ``_confidential_support_requested``).
    """
    answers = answers if answers is not None else load_answers(interview)
    reasons: list[str] = []
    for question in all_questions(interview.version):
        if question.sensitivity == question.Sensitivity.CONFIDENTIAL:
            continue
        if visible_ids is not None and question.id not in visible_ids:
            continue
        value = answers.get(question.id)
        if not value:
            continue
        flagged = [code for code, option in options_by_code(question).items() if code in value.codes and option.suggests_follow_up]
        if "hablar_lider" in flagged:
            reasons.append(FollowUp.Reason.TALK_TO_LEADER)
        if any(code != "hablar_lider" for code in flagged):
            reasons.append(FollowUp.Reason.SPIRITUAL_DECISION)
    ordered = [r for r in (FollowUp.Reason.TALK_TO_LEADER, FollowUp.Reason.SPIRITUAL_DECISION) if r in reasons]
    labels = dict(FollowUp.Reason.choices)
    return [(code, labels[code]) for code in ordered]


def _confidential_support_requested(interview: Interview, answers: dict[int, AnswerValue]) -> bool:
    for question in all_questions(interview.version):
        if question.sensitivity != question.Sensitivity.CONFIDENTIAL:
            continue
        value = answers.get(question.id)
        if value and any(option.suggests_follow_up for code, option in options_by_code(question).items() if code in value.codes):
            return True
    return False


def finalize(interview: Interview, *, token: str | None, revision, decision: str, actor, session=None) -> Interview:
    if decision not in Interview.FollowUpDecision.values:
        raise InterviewError("Indica si este campero necesita seguimiento.")
    with transaction.atomic():
        locked = Interview.objects.select_for_update().select_related("participant", "version").get(pk=interview.pk)
        if locked.status == Interview.Status.COMPLETED:
            return locked  # idempotent: a second click or a browser retry changes nothing
        _check_writable(locked, token, revision)
        answers = load_answers(locked)
        by_id = {q.id: q for q in all_questions(locked.version)}
        missing = [
            q
            for step in visible_steps(locked.version, answers)
            for q in step.questions
            if q.required and is_visible(q, answers, by_id) and (answers.get(q.id) is None or answers[q.id].is_empty)
        ]
        if missing:
            # Name the question: the review lists them all, and "Cambiar" sits next to each one.
            raise InterviewError(f"Falta responder “{missing[0].text}”. Toca “Cambiar” junto a ella.", interview=locked)
        now = timezone.now()
        locked.status = Interview.Status.COMPLETED
        locked.completed_at = now
        locked.last_activity_at = now
        locked.follow_up_decision = decision
        locked.revision += 1
        _clear_claim(locked)
        locked.save()
        participant = locked.participant
        participant.interview_status = InterviewStatus.COMPLETED
        participant.updated_by = actor
        participant.save(update_fields=["interview_status", "updated_by", "updated_at"])
        _close_participant_sessions(locked, ParticipantSession.EndReason.UNLOCKED)
        open_care = FollowUp.objects.filter(interview=locked, kind=FollowUp.Kind.CARE).exclude(status=FollowUp.Status.CANCELLED)
        if decision == Interview.FollowUpDecision.REQUIRED and not open_care.filter(confidential=False).exists():
            suggestions = follow_up_suggestions(locked, answers)
            FollowUp.objects.create(
                event=locked.event,
                participant=participant,
                interview=locked,
                kind=FollowUp.Kind.CARE,
                reason=suggestions[0][0] if suggestions else FollowUp.Reason.COORDINATOR,
                created_by=actor,
            )
        if _confidential_support_requested(locked, answers) and not open_care.filter(confidential=True).exists():
            # The campero asked for support in a confidential area: only the care team sees this,
            # whatever the coordinator decided, and without saying which area.
            FollowUp.objects.create(
                event=locked.event,
                participant=participant,
                interview=locked,
                kind=FollowUp.Kind.CARE,
                reason=FollowUp.Reason.SUPPORT_REQUESTED,
                confidential=True,
                created_by=None,
            )
        audit.record(actor, locked.event, "entrevista_completada", f"Completó la entrevista del campero #{participant.code}", locked)
    if session is not None:
        _forget_claim(session, locked)
    return locked


def discard(interview: Interview, *, actor, reason: str, session=None, check_claim: bool = False) -> Interview:
    """Delete every answer and mark the interview discarded.

    With ``check_claim`` (the discard screen), this browser must hold the claim, checked under the
    row lock like a step save, so a released or taken-over browser cannot discard.
    """
    token = session_claim_token(session, interview) if check_claim and session is not None else None
    with transaction.atomic():
        locked = Interview.objects.select_for_update().select_related("participant").get(pk=interview.pk)
        if locked.status != Interview.Status.IN_PROGRESS:
            raise InterviewError("Solo se puede descartar una entrevista en curso.", interview=locked)
        if check_claim and not locked.lock_matches(token):
            raise ClaimLost(CLAIM_LOST_MESSAGE, interview=locked)
        # Data minimization: a discarded interview keeps no answers.
        locked.answers.all().delete()
        locked.status = Interview.Status.DISCARDED
        locked.discarded_at = timezone.now()
        locked.discarded_by = actor
        locked.discarded_reason = reason[:200]
        _clear_claim(locked)
        locked.save()
        _close_participant_sessions(locked, ParticipantSession.EndReason.DISCARDED)
        participant = locked.participant
        participant.interview_status = InterviewStatus.PENDING
        participant.save(update_fields=["interview_status", "updated_at"])
        audit.record(actor, locked.event, "entrevista_descartada", f"Descartó la entrevista del campero #{participant.code}", locked)
    if session is not None:
        _forget_claim(session, locked)
    return locked


def release_stale_claims(event: Event | None = None) -> int:
    """Expired claims are already ignored by every check; this just tidies the rows.

    Released claims have no expiry, so they are left alone and their browser stays refused.
    """
    qs = Interview.objects.filter(status=Interview.Status.IN_PROGRESS, lock_expires_at__lt=timezone.now())
    if event is not None:
        qs = qs.filter(event=event)
    return qs.update(lock_token_hash="", lock_holder=None, lock_expires_at=None)


def update_follow_up_decision(access, interview: Interview, decision: str) -> Interview:
    """Resolve a "Por decidir" follow-up decision after the interview was completed."""
    if decision not in (Interview.FollowUpDecision.REQUIRED, Interview.FollowUpDecision.NOT_REQUIRED):
        raise InterviewError("Elige si requiere seguimiento o no.")
    with transaction.atomic():
        locked = Interview.objects.select_for_update().select_related("participant").get(pk=interview.pk)
        if not access.can_correct(locked):
            raise NotAllowed("No tienes permiso para cambiar esta entrevista.")
        if locked.follow_up_decision != Interview.FollowUpDecision.TO_EVALUATE:
            raise InterviewError("El seguimiento de esta entrevista ya se decidió.")
        locked.follow_up_decision = decision
        locked.revision += 1
        locked.save(update_fields=["follow_up_decision", "revision"])
        if decision == Interview.FollowUpDecision.REQUIRED and not FollowUp.objects.filter(
            interview=locked, kind=FollowUp.Kind.CARE, confidential=False
        ).exclude(status=FollowUp.Status.CANCELLED).exists():
            suggestions = follow_up_suggestions(locked)
            FollowUp.objects.create(
                event=locked.event,
                participant=locked.participant,
                interview=locked,
                kind=FollowUp.Kind.CARE,
                reason=suggestions[0][0] if suggestions else FollowUp.Reason.COORDINATOR,
                created_by=access.user,
            )
        audit.record(access.user, locked.event, "seguimiento", f"Decidió el seguimiento del campero #{locked.participant.code}", locked, decision=decision)
    return locked


# ---- corrections --------------------------------------------------------------------------------


def correctable_step(access, interview: Interview, step: Step) -> Step:
    """The part of a screen this person may correct: the answers they can read.

    Corrections are decided per answer, not per screen. A question that shares its screen
    with a confidential follow-up can be corrected by whoever can read it; the follow-up is
    left off the correction screen (and out of the form) for anyone without confidential
    access. A screen whose main question the person cannot read is refused.
    """
    if not access.can_view_answer(interview, step.question):
        raise NotAllowed("Esta respuesta es confidencial. Solo el equipo de cuidado autorizado puede corregirla.")
    children = [child for child in step.children if access.can_view_answer(interview, child)]
    if len(children) == len(step.children):
        return step
    return Step(step.number, step.section, step.section_number, step.question, children)


def correct_step(access, interview: Interview, step: Step, data, *, reason: str) -> tuple[Interview, dict]:
    reason = (reason or "").strip()
    with transaction.atomic():
        locked = Interview.objects.select_for_update().select_related("participant", "version").get(pk=interview.pk)
        if not access.can_correct(locked):
            raise NotAllowed("No tienes permiso para corregir esta entrevista.")
        # Work from the whole screen whatever the caller passed, then keep what this person may correct.
        full = next((s for s in build_steps(locked.version) if s.number == step.number), None)
        if full is None:
            raise InterviewError("Esa pregunta no existe en esta entrevista.")
        editable = correctable_step(access, locked, full)
        try:
            posted = int(data.get("revision"))
        except (TypeError, ValueError):
            posted = -1
        if posted != locked.revision:
            raise StaleRevision("Alguien más cambió esta entrevista. Revisa la versión más reciente.", interview=locked)
        answers = load_answers(locked)
        cleaned, errors = validate_step(editable, data, answers)
        if not reason:
            errors["reason"] = "Escribe brevemente por qué corriges esta respuesta."
        if errors:
            return locked, errors
        editable_ids = {q.id for q in editable.questions}
        merged = dict(answers)
        for question_id, value in cleaned.items():
            if value is None:
                merged.pop(question_id, None)
            else:
                merged[question_id] = value
        by_id = {q.id: q for q in all_questions(locked.version)}
        # A follow-up this person cannot read is never written from their form. If the corrected
        # answer hides it, it goes as on any other save (hidden answers are not kept) and gets a
        # revision below, like any answer the correction hides.
        for child in full.children:
            if child.id not in editable_ids and child.id in answers and not is_visible(child, merged, by_id):
                cleaned[child.id] = None

        def revision_reason(question_id: int, value) -> str:
            hidden = value is None and not is_visible(by_id[question_id], merged, by_id)
            return HIDDEN_BY_CORRECTION if hidden else reason[:200]

        changed = []
        for question_id, value in cleaned.items():
            before = answers.get(question_id)
            before_codes = sorted(before.codes) if before else []
            before_text = before.text if before else ""
            after_codes = sorted(value.codes) if value else []
            after_text = value.text if value else ""
            if before_codes != after_codes or before_text != after_text:
                AnswerRevision.objects.create(
                    interview=locked,
                    question_id=question_id,
                    previous_option_codes=before_codes,
                    previous_text=before_text,
                    reason=revision_reason(question_id, value),
                    changed_by=access.user,
                )
                changed.append(question_id)
        if not changed:
            return locked, {}
        pruned = write_answers(locked, cleaned, actor=access.user, by_participant=False)
        # Answers on other screens that the correction hid (``_prune_hidden``) leave a revision too.
        for question_id, before in pruned.items():
            AnswerRevision.objects.create(
                interview=locked,
                question_id=question_id,
                previous_option_codes=sorted(before.codes),
                previous_text=before.text,
                reason=HIDDEN_BY_CORRECTION,
                changed_by=access.user,
            )
        locked.revision += 1
        locked.correction_count += 1
        locked.last_corrected_at = timezone.now()
        locked.save(update_fields=["revision", "correction_count", "last_corrected_at"])
        # Count only answers the person could read: the count must not reveal that a hidden one existed.
        corrected = len([question_id for question_id in changed if question_id in editable_ids])
        audit.record(
            access.user,
            locked.event,
            "entrevista_corregida",
            f"Corrigió {corrected} {'respuesta' if corrected == 1 else 'respuestas'} de la entrevista del campero #{locked.participant.code}",
            locked,
            questions=corrected,
        )
    return locked, {}


# ---- "Entrevista a solas": the campero answers on the computer (kiosk) ----------------------------


def hand_over_to_participant(request, access, interview: Interview, station: InterviewStation | None) -> ParticipantSession:
    """End the coordinator's login and bind this browser to a single capture session."""
    token = session_claim_token(request.session, interview)
    with transaction.atomic():
        locked = Interview.objects.select_for_update().select_related("participant").get(pk=interview.pk)
        if locked.status != Interview.Status.IN_PROGRESS:
            raise AlreadyCompleted(NOT_IN_PROGRESS_MESSAGE, interview=locked)
        if not locked.lock_matches(token):
            raise ClaimLost(CLAIM_LOST_MESSAGE, interview=locked)
        _close_participant_sessions(locked, ParticipantSession.EndReason.TAKEN_OVER)
        kiosk_token = new_token()
        now = timezone.now()
        lock_token = _set_claim(locked, access.user, minutes=KIOSK_MAX_MINUTES)
        locked.mode = Interview.Mode.SUPERVISED
        locked.station = station or locked.station
        locked.save(update_fields=["lock_token_hash", "lock_holder", "lock_expires_at", "mode", "station"])
        participant_session = ParticipantSession.objects.create(
            interview=locked,
            station=locked.station,
            token_hash=hash_token(kiosk_token),
            lock_token_hash=hash_token(lock_token),
            started_by=access.user,
            expires_at=now + timedelta(minutes=KIOSK_MAX_MINUTES),
        )
        audit.record(
            access.user,
            locked.event,
            "captura_iniciada",
            f"Entregó la computadora al campero #{locked.participant.code}",
            locked,
            station=locked.station.name if locked.station else "",
        )
    stations = request.session.get("stations", {})
    logout(request)  # flushes the session: no coordinator privileges remain in this browser
    request.session[KIOSK_KEY] = {
        "session": str(participant_session.public_id),
        "token": kiosk_token,
        "claim": lock_token,
    }
    request.session["stations"] = stations  # which computer this is (not a secret)
    return participant_session


def switch_to_assisted(access, interview: Interview, *, session) -> Interview:
    """Before any self-capture happened, the coordinator may decide to ask the questions."""
    token = session_claim_token(session, interview)
    with transaction.atomic():
        locked = Interview.objects.select_for_update().get(pk=interview.pk)
        if locked.status != Interview.Status.IN_PROGRESS or not locked.lock_matches(token):
            raise ClaimLost(CLAIM_LOST_MESSAGE, interview=locked)
        if locked.answers.filter(answered_by_participant=True).exists():
            raise InterviewError("El campero ya respondió algunas preguntas a solas, así que la entrevista sigue a solas. Entrégale la computadora.")
        locked.mode = Interview.Mode.ASSISTED
        locked.save(update_fields=["mode"])
    return locked


def kiosk_state(session) -> dict | None:
    return session.get(KIOSK_KEY)


def current_kiosk_session(session, *, touch: bool = False) -> ParticipantSession:
    state = kiosk_state(session)
    if not state:
        raise KioskEnded("No hay una entrevista abierta en esta computadora.")
    participant_session = (
        ParticipantSession.objects.select_related("interview__participant__event", "interview__version", "station", "started_by")
        .filter(public_id=state.get("session"))
        .first()
    )
    if participant_session is None or not participant_session.token_matches(state.get("token")):
        raise KioskEnded(KIOSK_CLOSED_MESSAGE)
    now = timezone.now()
    if participant_session.state == ParticipantSession.State.ACTIVE:
        idle_limit = participant_session.last_activity_at + timedelta(minutes=KIOSK_IDLE_MINUTES)
        if now > participant_session.expires_at or now > idle_limit:
            participant_session.state = ParticipantSession.State.AWAITING_UNLOCK
            participant_session.end_reason = ParticipantSession.EndReason.EXPIRED
            participant_session.save(update_fields=["state", "end_reason"])
        elif touch:
            participant_session.last_activity_at = now
            participant_session.save(update_fields=["last_activity_at"])
    return participant_session


def require_active_kiosk(session) -> ParticipantSession:
    participant_session = current_kiosk_session(session, touch=True)
    if participant_session.state != ParticipantSession.State.ACTIVE:
        raise KioskEnded(KIOSK_CLOSED_MESSAGE)
    if participant_session.interview.status != Interview.Status.IN_PROGRESS:
        raise KioskEnded("Esta entrevista ya terminó.")
    return participant_session


def kiosk_save_step(session, step: Step, data) -> tuple[Interview, dict]:
    participant_session = require_active_kiosk(session)
    return save_step(
        participant_session.interview,
        step,
        data,
        token=kiosk_state(session).get("claim"),
        actor=None,
        by_participant=True,
    )


def kiosk_finish(session) -> ParticipantSession:
    participant_session = require_active_kiosk(session)
    participant_session.state = ParticipantSession.State.AWAITING_UNLOCK
    participant_session.save(update_fields=["state"])
    return participant_session


def kiosk_unlock(request, password: str, *, username: str | None = None):
    """Return (user, interview) on success; raise InterviewError otherwise."""
    from . import throttle

    participant_session = current_kiosk_session(request.session)
    if participant_session.state == ParticipantSession.State.CLOSED:
        request.session.pop(KIOSK_KEY, None)
        raise KioskEnded(KIOSK_CLOSED_MESSAGE)
    wait = throttle.consume(
        f"unlock:{participant_session.public_id}",
        limit=UNLOCK_MAX_ATTEMPTS,
        window_seconds=60 * 60,
        block_seconds=UNLOCK_COOLDOWN_SECONDS,
        max_block_seconds=15 * 60,
    )
    if wait:
        raise InterviewError(throttle.wait_message(wait))
    target = participant_session.started_by
    login_name = username or target.get_username()
    user = authenticate(request, username=login_name, password=password)
    from ..access import resolve_access

    interview = participant_session.interview
    access = resolve_access(user, interview.event) if user else None
    if user is None or access is None or not access.can_interview(interview.participant):
        raise InterviewError(WRONG_PASSWORD_MESSAGE)
    with transaction.atomic():
        locked_session = ParticipantSession.objects.select_for_update().get(pk=participant_session.pk)
        if locked_session.state == ParticipantSession.State.CLOSED:
            raise KioskEnded(KIOSK_CLOSED_MESSAGE)
        locked_session.state = ParticipantSession.State.CLOSED
        locked_session.ended_at = timezone.now()
        locked_session.end_reason = ParticipantSession.EndReason.UNLOCKED
        locked_session.save(update_fields=["state", "ended_at", "end_reason"])
    throttle.reset(f"unlock:{participant_session.public_id}")
    request.session.pop(KIOSK_KEY, None)
    login(request, user)  # rotates the session key (session data such as the station is kept)
    with transaction.atomic():
        locked = Interview.objects.select_for_update().get(pk=interview.pk)
        token = None
        if locked.status == Interview.Status.IN_PROGRESS and locked.lock_token_hash in {"", locked_session.lock_token_hash}:
            # Hand the kiosk's claim back to the coordinator's new login.
            token = _set_claim(locked, user)
            locked.save(update_fields=["lock_token_hash", "lock_holder", "lock_expires_at"])
        audit.record(user, locked.event, "captura_desbloqueada", f"Recuperó la computadora del campero #{interview.participant.code}", locked)
    if token:
        _remember_claim(request.session, locked, token)
    return user, locked


def revoke_participant_session(participant_session: ParticipantSession, *, actor) -> None:
    with transaction.atomic():
        locked = ParticipantSession.objects.select_for_update().select_related("interview").get(pk=participant_session.pk)
        if locked.state == ParticipantSession.State.CLOSED:
            return
        locked.state = ParticipantSession.State.CLOSED
        locked.ended_at = timezone.now()
        locked.end_reason = ParticipantSession.EndReason.REVOKED
        locked.save(update_fields=["state", "ended_at", "end_reason"])
        interview = Interview.objects.select_for_update().get(pk=locked.interview_id)
        if interview.lock_token_hash == locked.lock_token_hash:
            _clear_claim(interview)
            interview.save(update_fields=["lock_token_hash", "lock_holder", "lock_expires_at"])
        audit.record(actor, interview.event, "sesion_revocada", "Cerró la pantalla del campero en una entrevista a solas", interview)


def revoke_claim(interview: Interview, *, actor) -> None:
    """"Liberar": free the interview and stop the computer that had it open from saving.

    That computer's next save, review or hand-over is refused (``ClaimReleased``). Another
    computer opens the interview normally; the same one opens it again from "Mi inicio" or
    takes control. See ``_release_claim``.
    """
    with transaction.atomic():
        locked = Interview.objects.select_for_update().get(pk=interview.pk)
        _close_participant_sessions(locked, ParticipantSession.EndReason.REVOKED)
        _release_claim(locked)
        locked.save(update_fields=["lock_holder", "lock_expires_at"])
        audit.record(actor, locked.event, "sesion_revocada", "Liberó una entrevista abierta en otra computadora", locked)


# ---- safety -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class SafetyReport:
    follow_up: FollowUp
    notified: bool  # notify_safety_report returned True: there were addresses and the backend accepted the message


def report_safety_concern(access, participant: EventParticipant, *, details: str, interview: Interview | None = None) -> SafetyReport:
    """Store the report and send the alert, saying whether the alert went (so the screen never claims it did).

    Called in autocommit (the view), the row is already stored when the alert goes, and its result is known.
    Inside a caller's transaction the alert waits for the commit (a rollback must not alert anyone about a
    report that does not exist) and ``notified`` is False: nothing is known yet.
    """
    from .notifications import notify_safety_report

    follow_up = FollowUp.objects.create(
        event=access.event,
        participant=participant,
        interview=interview,
        kind=FollowUp.Kind.SAFETY,
        reason=FollowUp.Reason.SAFETY,
        details=details.strip()[:4000],
        created_by=access.user,
    )
    audit.record(access.user, access.event, "reporte_riesgo", f"Reportó una situación de riesgo del campero #{participant.code}", follow_up)
    if transaction.get_connection().in_atomic_block:
        transaction.on_commit(lambda: notify_safety_report(follow_up))
        return SafetyReport(follow_up, notified=False)
    return SafetyReport(follow_up, notified=notify_safety_report(follow_up))


SAFETY_REPORT_SAVED = "El reporte quedó guardado."
SAFETY_REPORT_CARE_TEAM = "El equipo de cuidado autorizado lo verá en Emmaus. Avísales también en persona."
SAFETY_REPORT_ADMINISTRATION = "La administración lo verá en Seguimiento."
SAFETY_REPORT_EMAILED = "También les mandamos un aviso por correo, sin nombres ni detalles."
SAFETY_REPORT_NOBODY_CAN_READ = "Ahora mismo nadie en este evento tiene acceso confidencial: avisa a la administración."


def safety_report_result(event: Event, *, notified: bool) -> tuple[str, str | None]:
    """What the person who sent a safety report reads afterwards: only what Emmaus knows to be true.

    The report is always saved. Who will see it depends on who holds confidential access in the event:
    the care team, or, with nobody holding it, only the administration (which sees safety reports in
    "Seguimiento" without the flag, but not what they say). The e-mail is mentioned only when it went:
    ``notified`` (what ``report_safety_concern`` returned) and a backend that delivers
    (``team.email_is_configured``; the console backend "sends" into the server log). The second value is
    a warning, or None.
    """
    from ..models import EventMembership
    from .team import email_is_configured

    readers = EventMembership.objects.filter(event=event, is_active=True, can_view_sensitive=True, user__is_active=True).exists()
    sentences = [SAFETY_REPORT_SAVED, SAFETY_REPORT_CARE_TEAM if readers else SAFETY_REPORT_ADMINISTRATION]
    if notified and email_is_configured():
        sentences.append(SAFETY_REPORT_EMAILED)
    return " ".join(sentences), (None if readers else SAFETY_REPORT_NOBODY_CAN_READ)


# ---- read helpers -------------------------------------------------------------------------------


def review_rows(access, interview: Interview, *, during_capture: bool = False, for_participant: bool = False):
    """Rows for a check-your-answers page, with per-field visibility applied.

    A conditional question is listed only if the viewer may see the answer that
    controls it; otherwise its mere presence would reveal that hidden answer.
    Hidden rows look the same whether or not they were answered.
    """
    answers = load_answers(interview)
    by_id = {q.id: q for q in all_questions(interview.version)}
    rows = []
    hidden: set[int] = set()
    for step in build_steps(interview.version):
        for question in step.questions:
            visible = for_participant or (access is not None and access.can_view_answer(interview, question, during_capture=during_capture))
            if not visible:
                hidden.add(question.id)
            if question.condition_question_id and question.condition_question_id in hidden:
                hidden.add(question.id)
                continue
            if not is_visible(question, answers, by_id):
                continue
            value = answers.get(question.id)
            rows.append(
                {
                    "step": step,
                    "question": question,
                    "visible": visible,
                    "answered": (value is not None and not value.is_empty) if visible else None,
                    "display": describe_answer(question, value) if visible else "",
                }
            )
    return rows


def visible_question_ids(access, interview: Interview, *, during_capture: bool = False) -> set[int]:
    return {q.id for q in all_questions(interview.version) if access.can_view_answer(interview, q, during_capture=during_capture)}
