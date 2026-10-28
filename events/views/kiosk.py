"""Entrevista a solas: screens a campero sees on a shared computer.

The browser holds no login here. Every request is validated against the server-side
ParticipantSession (one interview, one station, expiring). The middleware keeps this
browser inside /estacion/ until the coordinator unlocks it with their password.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..models import Interview, ParticipantSession
from ..services import interviews as svc
from ..services.event_questionnaires import links_for
from ..services.questionnaire import (
    all_questions,
    build_steps,
    is_visible,
    load_answers,
    neighbor_steps,
    visible_steps,
)
from .interview import step_context


def _session_or_screen(request):
    try:
        return svc.current_kiosk_session(request.session, touch=True), None
    except svc.KioskEnded:
        request.session.pop(svc.KIOSK_KEY, None)
        return None, render(request, "events/kiosk/ended.html", {}, status=200)


def _questionnaire_place(interview) -> dict:
    """With several questionnaires: which one this is in the event's order (1-based) and how many there are, for
    "Es el cuestionario 1 de 2." Empty with one questionnaire, or if the event no longer uses this one."""
    links = links_for(interview.event_id)
    if len(links) < 2:
        return {}
    position = next((i for i, link in enumerate(links, start=1) if link.questionnaire_id == interview.questionnaire_id), None)
    return {"questionnaire_position": position, "questionnaire_total": len(links)} if position else {}


def kiosk_home(request):
    participant_session, screen = _session_or_screen(request)
    if screen:
        return screen
    interview = participant_session.interview
    context = {"ps": participant_session, "interview": interview, "participant": interview.participant}
    if participant_session.state == ParticipantSession.State.CLOSED:
        request.session.pop(svc.KIOSK_KEY, None)
        return render(request, "events/kiosk/ended.html", context)
    context.update(_questionnaire_place(interview))
    if participant_session.state == ParticipantSession.State.AWAITING_UNLOCK or interview.status != Interview.Status.IN_PROGRESS:
        return render(request, "events/kiosk/handback.html", context)
    answers = load_answers(interview)
    started = bool(answers)
    steps = visible_steps(interview.version, answers)
    # "Retomar" opens the screen the review's "Seguir contestando" opens: a required follow-up left blank counts.
    still_to_answer = _still_to_answer(interview)
    first = still_to_answer[0][0] if still_to_answer else steps[0]
    context.update({"first_step": first, "started": started, "intro": interview.version.intro_text, "total_steps": len(steps)})
    return render(request, "events/kiosk/welcome.html", context)


# "Cambiar" on the review adds ?volver=revision: after that one question, back to the review.
RETURN_TO_REVIEW = "revision"


def kiosk_step(request, number):
    participant_session, screen = _session_or_screen(request)
    if screen:
        return screen
    if participant_session.state != ParticipantSession.State.ACTIVE:
        return redirect("kiosk:home")
    interview = participant_session.interview
    step = next((s for s in build_steps(interview.version) if s.number == number), None)
    if step is None:
        raise Http404
    answers = load_answers(interview)
    if step.number not in [s.number for s in visible_steps(interview.version, answers)]:
        _, following = neighbor_steps(interview.version, answers, number)
        return redirect("kiosk:step", number=following.number) if following else redirect("kiosk:review")
    return_to_review = request.GET.get("volver") == RETURN_TO_REVIEW
    marker = f"?volver={RETURN_TO_REVIEW}" if return_to_review else ""
    this_step = request.path + marker
    if request.method == "POST":
        direction = request.POST.get("direction", "next")
        try:
            # "Parar por ahora" (direction=pause) saves the screen without requiring anything (svc.save_step).
            interview, errors = svc.kiosk_save_step(request.session, step, request.POST, partial=direction == "pause")
        except svc.StaleRevision:
            return redirect(this_step)
        except (svc.ClaimLost, svc.KioskEnded, svc.AlreadyCompleted):
            return redirect("kiosk:home")
        if not errors:
            answers = load_answers(interview)
            previous, following = neighbor_steps(interview.version, answers, step.number)
            if direction == "back" and previous:
                # From "Cambiar", "Atrás" still ends on the review: the marker goes along.
                return redirect(reverse("kiosk:step", args=[previous.number]) + marker)
            if following is None or direction in ("review", "pause") or return_to_review:
                return redirect("kiosk:review")
            return redirect("kiosk:step", number=following.number)
        context = step_context(interview, step, errors=errors, posted=request.POST)
    else:
        context = step_context(interview, step, answers=answers)
    context.update(
        {
            "participant": interview.participant,
            "form_action": this_step,
            "mode": "participant",
            "ps": participant_session,
        }
    )
    return render(request, "events/kiosk/step.html", context)


def kiosk_review(request):
    participant_session, screen = _session_or_screen(request)
    if screen:
        return screen
    if participant_session.state != ParticipantSession.State.ACTIVE:
        return redirect("kiosk:home")
    interview = participant_session.interview
    rows = svc.review_rows(None, interview, for_participant=True)
    unanswered = _still_to_answer(interview)
    return render(
        request,
        "events/kiosk/review.html",
        {
            "interview": interview,
            "participant": interview.participant,
            "rows": rows,
            "ps": participant_session,
            "next_step": unanswered[0][0] if unanswered else None,
            "pending": sum(missing for _, missing in unanswered),
        },
    )


def _still_to_answer(interview: Interview) -> list[tuple]:
    """(step, questions missing on it) for every screen with something left: after "Parar por ahora",
    "Seguir contestando" opens the first one (as "Retomar" does) and the review says how many are left.

    A screen counts when its question has no answer, or when a follow-up it shows is required and blank
    (stored before the follow-up became required): only the camper can fill a confidential follow-up.
    """
    answers = load_answers(interview)
    by_id = {q.id: q for q in all_questions(interview.version)}
    result = []
    for step in visible_steps(interview.version, answers):
        missing = 0 if step.question.id in answers else 1
        missing += sum(
            1
            for child in step.children
            if child.required and is_visible(child, answers, by_id) and (child.id not in answers or answers[child.id].is_empty)
        )
        if missing:
            result.append((step, missing))
    return result


@require_POST
def kiosk_finish(request):
    try:
        svc.kiosk_finish(request.session)
    except svc.KioskEnded:
        pass
    return redirect("kiosk:home")


def kiosk_unlock(request):
    participant_session, screen = _session_or_screen(request)
    if screen:
        return screen
    error = None
    other = request.GET.get("otra") == "1" or request.POST.get("otra") == "1"
    if request.method == "POST":
        try:
            user, interview = svc.kiosk_unlock(
                request,
                request.POST.get("password", ""),
                username=(request.POST.get("username") or "").strip().lower() if other else None,
            )
        except svc.KioskEnded:
            return redirect("kiosk:home")
        except svc.InterviewError as exc:
            error = exc.message
        else:
            messages.success(request, "Recuperaste la computadora. Revisa y finaliza la entrevista.")
            if interview.status == Interview.Status.IN_PROGRESS:
                return redirect("events:interview_review", slug=interview.event.slug, public_id=interview.public_id)
            return redirect("events:home", slug=interview.event.slug)
    return render(
        request,
        "events/kiosk/unlock.html",
        {"ps": participant_session, "coordinator": participant_session.started_by, "error": error, "other": other},
    )


@require_POST
def kiosk_heartbeat(request):
    try:
        svc.require_active_kiosk(request.session)
    except svc.KioskEnded:
        return HttpResponse(status=410)
    return HttpResponse(status=204)


def kiosk_timeout(request):
    """The browser's idle timer lands here; the server marks the session as waiting."""
    try:
        participant_session = svc.current_kiosk_session(request.session)
    except svc.KioskEnded:
        return redirect("kiosk:home")
    if participant_session.state == ParticipantSession.State.ACTIVE:
        idle = timezone.now() - participant_session.last_activity_at
        if idle.total_seconds() > 60:
            participant_session.state = ParticipantSession.State.AWAITING_UNLOCK
            participant_session.end_reason = ParticipantSession.EndReason.EXPIRED
            participant_session.save(update_fields=["state", "end_reason"])
    return redirect("kiosk:home")
