"""Captura supervisada: screens a campero sees on a shared computer.

The browser holds no login here. Every request is validated against the server-side
ParticipantSession (one interview, one station, expiring). The middleware keeps this
browser inside /estacion/ until the coordinator unlocks it with their password.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..models import Interview, ParticipantSession
from ..services import interviews as svc
from ..services.questionnaire import build_steps, first_unanswered_step, load_answers, neighbor_steps, visible_steps
from .interview import step_context


def _session_or_screen(request):
    try:
        return svc.current_kiosk_session(request.session, touch=True), None
    except svc.KioskEnded:
        request.session.pop(svc.KIOSK_KEY, None)
        return None, render(request, "events/kiosk/ended.html", {}, status=200)


def kiosk_home(request):
    participant_session, screen = _session_or_screen(request)
    if screen:
        return screen
    interview = participant_session.interview
    context = {"ps": participant_session, "interview": interview, "participant": interview.participant}
    if participant_session.state == ParticipantSession.State.CLOSED:
        request.session.pop(svc.KIOSK_KEY, None)
        return render(request, "events/kiosk/ended.html", context)
    if participant_session.state == ParticipantSession.State.AWAITING_UNLOCK or interview.status != Interview.Status.IN_PROGRESS:
        return render(request, "events/kiosk/handback.html", context)
    answers = load_answers(interview)
    started = bool(answers)
    steps = visible_steps(interview.version, answers)
    first = first_unanswered_step(interview.version, answers) or steps[0]
    context.update({"first_step": first, "started": started, "intro": interview.version.intro_text, "total_steps": len(steps)})
    return render(request, "events/kiosk/welcome.html", context)


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
    if request.method == "POST":
        try:
            interview, errors = svc.kiosk_save_step(request.session, step, request.POST)
        except svc.StaleRevision:
            return redirect("kiosk:step", number=number)
        except (svc.ClaimLost, svc.KioskEnded, svc.AlreadyCompleted):
            return redirect("kiosk:home")
        if not errors:
            answers = load_answers(interview)
            previous, following = neighbor_steps(interview.version, answers, step.number)
            direction = request.POST.get("direction", "next")
            if direction == "back" and previous:
                return redirect("kiosk:step", number=previous.number)
            if following is None or direction == "review":
                return redirect("kiosk:review")
            return redirect("kiosk:step", number=following.number)
        context = step_context(interview, step, errors=errors, posted=request.POST)
    else:
        context = step_context(interview, step, answers=answers)
    context.update(
        {
            "participant": interview.participant,
            "form_action": request.path,
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
    return render(request, "events/kiosk/review.html", {"interview": interview, "participant": interview.participant, "rows": rows, "ps": participant_session})


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
