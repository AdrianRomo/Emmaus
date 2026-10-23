from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Case, IntegerField, Q, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from ..forms import DiscardForm, InterviewStartForm, SafetyReportForm
from ..models import EventParticipant, FollowUp, Interview, InterviewStatus, Question
from ..services import audit
from ..services import interviews as svc
from ..services.questionnaire import (
    build_steps,
    load_answers,
    neighbor_steps,
    visible_steps,
)
from .common import ADMIN, COORDINATOR, current_station, event_view


# ---- view models ----------------------------------------------------------------------------


def question_context(question: Question, value, error: str | None, posted=None, *, readonly: bool = False) -> dict:
    if posted is not None:
        field = f"q_{question.id}"
        codes = posted.getlist(field) if question.kind == Question.Kind.MULTIPLE else ([posted.get(field)] if posted.get(field) else [])
        text = posted.get(field, "") if question.is_text else ""
    else:
        codes = value.codes if value else []
        text = value.text if value else ""
    main, quiet = [], []
    for option in question.options.all():
        entry = {
            "option": option,
            "id": f"q{question.id}-{option.code}",
            "checked": option.code in codes,
        }
        (quiet if (option.is_decline or option.is_exclusive) else main).append(entry)
    return {
        "question": question,
        "name": f"q_{question.id}",
        "is_multi": question.kind == Question.Kind.MULTIPLE,
        "is_long": question.kind == Question.Kind.LONG_TEXT,
        "options": main,
        "quiet_options": quiet,
        "text": text,
        "error": error,
        "readonly": readonly,
        "reveal_for": f"q_{question.condition_question_id}" if question.condition_question_id else "",
        "reveal_codes": ",".join(question.condition_option_codes or []),
    }


def step_context(interview: Interview, step, *, errors=None, posted=None, answers=None) -> dict:
    answers = answers if answers is not None else load_answers(interview)
    errors = errors or {}
    visible = visible_steps(interview.version, answers)
    numbers = [s.number for s in visible]
    position = numbers.index(step.number) + 1 if step.number in numbers else 1
    previous, following = neighbor_steps(interview.version, answers, step.number)
    questions = [question_context(step.question, answers.get(step.question.id), errors.get(step.question.id), posted)]
    children = [question_context(child, answers.get(child.id), errors.get(child.id), posted) for child in step.children]
    total_sections = len({s.section.id for s in build_steps(interview.version)})
    return {
        "interview": interview,
        "step": step,
        "main": questions[0],
        "children": children,
        "errors": errors,
        "error_list": [(f"q{qid}", message) for qid, message in errors.items()],
        "position": position,
        "total": len(visible),
        "visible_numbers": numbers,
        "previous": previous,
        "following": following,
        "total_sections": total_sections,
    }


def _scoped_interview(access, public_id) -> Interview:
    interview = get_object_or_404(
        Interview.objects.select_related("participant__coordinator__user", "version", "station", "conducted_by"),
        public_id=public_id,
        event=access.event,
    )
    if not access.can_view_participant(interview.participant):
        raise Http404
    return interview


def _step_or_404(interview: Interview, number: int):
    for step in build_steps(interview.version):
        if step.number == number:
            return step
    raise Http404


def _claim_or_conflict(request, access, interview: Interview):
    """Ensure this browser is the editor; returns a response when it is not."""
    try:
        svc.claim(access, interview, request.session, station=current_station(request, access.event))
    except svc.ClaimConflict:
        return redirect("events:interview_conflict", slug=access.event.slug, public_id=interview.public_id)
    except svc.AlreadyCompleted:
        return redirect("events:interview_detail", slug=access.event.slug, public_id=interview.public_id)
    except svc.NotAllowed as exc:
        messages.error(request, exc.message)
        return redirect("events:coord_home" if access.is_coordinator else "events:home", slug=access.event.slug)
    return None


# ---- search and start -------------------------------------------------------------------------


@event_view(COORDINATOR, ADMIN)
def interview_search(request, event, access):
    station = current_station(request, event)
    if station is None and event.stations.filter(is_active=True).exists():
        return redirect(f"{reverse('events:choose_station', args=[event.slug])}?siguiente={request.path}")
    query = (request.GET.get("q") or "").strip()
    participants = access.participants().annotate(
        rank=Case(
            When(interview_status=InterviewStatus.IN_PROGRESS, then=Value(0)),
            When(interview_status=InterviewStatus.PENDING, then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        )
    )
    if query:
        from ..models import normalize_name

        normalized = normalize_name(query)
        filters = Q(normalized_name__contains=normalized)
        digits = query.lstrip("#").strip()
        if digits.isdigit():
            filters |= Q(number=int(digits))
        participants = participants.filter(filters)
    results = list(participants.order_by("rank", "room", "full_name")[: 30 if query else 12])
    return render(
        request,
        "events/interview/search.html",
        {
            "query": query,
            "results": results,
            "station": station,
            "blockers": svc.event_blockers(event),
        },
    )


@event_view(COORDINATOR, ADMIN)
def interview_start(request, event, access, public_id):
    participant = get_object_or_404(access.participants(), public_id=public_id)
    if not access.can_interview(participant):
        raise PermissionDenied
    existing = participant.interviews.exclude(status=Interview.Status.DISCARDED).first()
    if existing and existing.status == Interview.Status.IN_PROGRESS:
        return redirect("events:interview_resume", slug=event.slug, public_id=existing.public_id)
    if existing and existing.status == Interview.Status.COMPLETED:
        messages.info(request, f"{participant.full_name} ya tiene su entrevista completada.")
        return redirect("events:interview_detail", slug=event.slug, public_id=existing.public_id)
    requirements = svc.consent_requirements(participant)
    form = InterviewStartForm(request.POST or None, requirements=requirements)
    blockers = svc.event_blockers(event)
    station = current_station(request, event)
    if request.method == "POST" and form.is_valid() and not blockers:
        try:
            interview = svc.start_interview(
                access,
                participant,
                mode=form.cleaned_data["mode"],
                station=station,
                confirmed_consents=form.confirmed(),
                session=request.session,
            )
        except (svc.AlreadyInProgress, svc.AlreadyCompleted) as exc:
            messages.warning(request, exc.message)
            if exc.interview:
                return redirect("events:interview_resume", slug=event.slug, public_id=exc.interview.public_id)
            return redirect("events:interview_search", slug=event.slug)
        except svc.InterviewError as exc:
            messages.error(request, exc.message)
        else:
            if interview.mode == Interview.Mode.SUPERVISED:
                return redirect("events:interview_handoff", slug=event.slug, public_id=interview.public_id)
            first = build_steps(interview.version)[0]
            return redirect("events:interview_step", slug=event.slug, public_id=interview.public_id, number=first.number)
    return render(
        request,
        "events/interview/start.html",
        {
            "participant": participant,
            "form": form,
            "requirements": requirements,
            "blockers": blockers,
            "station": station,
            "privacy_notice": event.privacy_notice,
        },
    )


@event_view(COORDINATOR, ADMIN)
def interview_resume(request, event, access, public_id):
    interview = _scoped_interview(access, public_id)
    if interview.status != Interview.Status.IN_PROGRESS:
        return redirect("events:interview_detail", slug=event.slug, public_id=interview.public_id)
    response = _claim_or_conflict(request, access, interview)
    if response:
        return response
    interview.refresh_from_db()
    answers = load_answers(interview)
    for step in visible_steps(interview.version, answers):
        if step.question.id not in answers:
            return redirect("events:interview_step", slug=event.slug, public_id=interview.public_id, number=step.number)
    return redirect("events:interview_review", slug=event.slug, public_id=interview.public_id)


@event_view(COORDINATOR, ADMIN)
def interview_conflict(request, event, access, public_id):
    interview = _scoped_interview(access, public_id)
    if not access.can_interview(interview.participant):
        raise PermissionDenied
    if request.method == "POST":
        try:
            svc.claim(access, interview, request.session, take_over=True, station=current_station(request, event))
        except svc.InterviewError as exc:
            messages.error(request, exc.message)
            return redirect("events:coord_home" if access.is_coordinator else "events:admin_home", slug=event.slug)
        messages.success(request, "Ahora esta computadora tiene el control de la entrevista.")
        return redirect("events:interview_resume", slug=event.slug, public_id=interview.public_id)
    return render(request, "events/interview/conflict.html", {"interview": interview, "participant": interview.participant})


# ---- steps --------------------------------------------------------------------------------------


@event_view(COORDINATOR, ADMIN)
def interview_step(request, event, access, public_id, number):
    interview = _scoped_interview(access, public_id)
    if interview.status != Interview.Status.IN_PROGRESS:
        return redirect("events:interview_detail", slug=event.slug, public_id=interview.public_id)
    if not access.can_interview(interview.participant):
        raise PermissionDenied
    token = svc.session_claim_token(request.session, interview)
    if not interview.lock_matches(token):
        response = _claim_or_conflict(request, access, interview)
        if response:
            return response
        interview.refresh_from_db()
        token = svc.session_claim_token(request.session, interview)
    step = _step_or_404(interview, number)
    answers = load_answers(interview)
    if step.number not in [s.number for s in visible_steps(interview.version, answers)]:
        _, following = neighbor_steps(interview.version, answers, step.number)
        if following:
            return redirect("events:interview_step", slug=event.slug, public_id=public_id, number=following.number)
        return redirect("events:interview_review", slug=event.slug, public_id=public_id)
    for question in step.questions:
        if not access.can_view_answer(interview, question, during_capture=True):
            # Supervised capture already happened for this question; coordinators may not see it.
            messages.info(request, "Esa pregunta es confidencial y la respondió el campero.")
            return redirect("events:interview_review", slug=event.slug, public_id=public_id)
    if request.method == "POST":
        try:
            interview, errors = svc.save_step(interview, step, request.POST, token=token, actor=request.user, by_participant=False)
        except svc.StaleRevision as exc:
            messages.warning(request, exc.message)
            return redirect("events:interview_step", slug=event.slug, public_id=public_id, number=number)
        except svc.ClaimLost as exc:
            messages.error(request, exc.message)
            return redirect("events:interview_conflict", slug=event.slug, public_id=public_id)
        except svc.AlreadyCompleted:
            return redirect("events:interview_detail", slug=event.slug, public_id=public_id)
        if not errors:
            answers = load_answers(interview)
            direction = request.POST.get("direction", "next")
            previous, following = neighbor_steps(interview.version, answers, step.number)
            if direction == "exit":
                messages.success(request, f"Guardamos las respuestas de {interview.participant.first_name}. Puedes continuar después.")
                return redirect("events:coord_home" if access.is_coordinator else "events:admin_home", slug=event.slug)
            if direction == "back" and previous:
                return redirect("events:interview_step", slug=event.slug, public_id=public_id, number=previous.number)
            if direction == "review" or following is None:
                return redirect("events:interview_review", slug=event.slug, public_id=public_id)
            return redirect("events:interview_step", slug=event.slug, public_id=public_id, number=following.number)
        context = step_context(interview, step, errors=errors, posted=request.POST)
    else:
        context = step_context(interview, step, answers=answers)
    context.update(
        {
            "participant": interview.participant,
            "form_action": request.path,
            "mode": "coordinator",
            "exit_url": reverse("events:coord_home" if access.is_coordinator else "events:admin_home", args=[event.slug]),
            "review_url": reverse("events:interview_review", args=[event.slug, public_id]),
        }
    )
    return render(request, "events/interview/step.html", context)


@event_view(COORDINATOR, ADMIN)
def interview_review(request, event, access, public_id):
    interview = _scoped_interview(access, public_id)
    if interview.status == Interview.Status.COMPLETED:
        return redirect("events:interview_done", slug=event.slug, public_id=public_id)
    if interview.status != Interview.Status.IN_PROGRESS:
        raise Http404
    if not access.can_interview(interview.participant):
        raise PermissionDenied
    token = svc.session_claim_token(request.session, interview)
    if not interview.lock_matches(token):
        response = _claim_or_conflict(request, access, interview)
        if response:
            return response
        interview.refresh_from_db()
        token = svc.session_claim_token(request.session, interview)
    error = None
    if request.method == "POST":
        decision = request.POST.get("follow_up_decision", "")
        try:
            interview = svc.finalize(
                interview, token=token, revision=request.POST.get("revision"), decision=decision, actor=request.user, session=request.session
            )
        except svc.StaleRevision as exc:
            messages.warning(request, exc.message)
            return redirect("events:interview_review", slug=event.slug, public_id=public_id)
        except svc.ClaimLost as exc:
            messages.error(request, exc.message)
            return redirect("events:interview_conflict", slug=event.slug, public_id=public_id)
        except svc.InterviewError as exc:
            error = exc.message
        else:
            return redirect("events:interview_done", slug=event.slug, public_id=public_id)
    answers = load_answers(interview)
    suggestions = svc.follow_up_suggestions(interview, answers)
    rows = svc.review_rows(access, interview, during_capture=True)
    return render(
        request,
        "events/interview/review.html",
        {
            "interview": interview,
            "participant": interview.participant,
            "rows": rows,
            "suggestions": suggestions,
            "decision_choices": Interview.FollowUpDecision.choices,
            "selected_decision": request.POST.get("follow_up_decision") or (Interview.FollowUpDecision.REQUIRED if suggestions else ""),
            "error": error,
            "hidden_count": sum(1 for r in rows if not r["visible"]),
        },
    )


@event_view(COORDINATOR, ADMIN)
def interview_done(request, event, access, public_id):
    interview = _scoped_interview(access, public_id)
    if interview.status != Interview.Status.COMPLETED:
        return redirect("events:interview_resume", slug=event.slug, public_id=public_id)
    follow_up = interview.follow_ups.filter(kind=FollowUp.Kind.CARE).exclude(status=FollowUp.Status.CANCELLED).first()
    remaining = access.participants().exclude(interview_status=InterviewStatus.COMPLETED).count()
    return render(
        request,
        "events/interview/done.html",
        {"interview": interview, "participant": interview.participant, "follow_up": follow_up, "remaining": remaining},
    )


@event_view(COORDINATOR, ADMIN)
def interview_handoff(request, event, access, public_id):
    interview = _scoped_interview(access, public_id)
    if interview.status != Interview.Status.IN_PROGRESS or not access.can_interview(interview.participant):
        raise Http404
    response = _claim_or_conflict(request, access, interview) if request.method == "GET" else None
    if response:
        return response
    if request.method == "POST":
        try:
            svc.hand_over_to_participant(request, access, interview, current_station(request, event))
        except svc.InterviewError as exc:
            messages.error(request, exc.message)
            return redirect("events:interview_resume", slug=event.slug, public_id=public_id)
        return redirect("kiosk:home")
    return render(request, "events/interview/handoff.html", {"interview": interview, "participant": interview.participant})


@event_view(COORDINATOR, ADMIN)
def interview_discard(request, event, access, public_id):
    interview = _scoped_interview(access, public_id)
    if interview.status != Interview.Status.IN_PROGRESS or not access.can_interview(interview.participant):
        raise Http404
    form = DiscardForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        svc.discard(interview, actor=request.user, reason=form.cleaned_data["reason"], session=request.session)
        messages.success(request, f"Se descartó la entrevista de {interview.participant.full_name}. Sus respuestas se borraron.")
        return redirect("events:coord_home" if access.is_coordinator else "events:admin_home", slug=event.slug)
    return render(request, "events/interview/discard.html", {"interview": interview, "participant": interview.participant, "form": form})


# ---- reading and correcting --------------------------------------------------------------------


@event_view()
def interview_detail(request, event, access, public_id):
    interview = _scoped_interview(access, public_id)
    if request.method == "POST" and request.POST.get("action") == "decision":
        try:
            svc.update_follow_up_decision(access, interview, request.POST.get("decision", ""))
        except svc.InterviewError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, "Decisión de seguimiento guardada.")
        return redirect("events:interview_detail", slug=event.slug, public_id=public_id)
    rows = svc.review_rows(access, interview)
    confidential_shown = [r for r in rows if r["visible"] and r["question"].sensitivity == Question.Sensitivity.CONFIDENTIAL and r["answered"]]
    if confidential_shown:
        audit.record(
            request.user, event, "consulta_confidencial",
            f"Consultó respuestas confidenciales del campero #{interview.participant.code}", interview,
        )
    return render(
        request,
        "events/interview/detail.html",
        {
            "interview": interview,
            "participant": interview.participant,
            "rows": rows,
            "can_correct": access.can_correct(interview),
            "can_continue": interview.status == Interview.Status.IN_PROGRESS and access.can_interview(interview.participant),
            "revisions": interview.answer_revisions.select_related("question", "changed_by")[:20] if access.can_view_sensitive or access.is_admin else [],
            "hidden_count": sum(1 for r in rows if not r["visible"]),
        },
    )


@event_view(COORDINATOR, ADMIN)
def interview_correct(request, event, access, public_id, number):
    interview = _scoped_interview(access, public_id)
    if not access.can_correct(interview):
        raise PermissionDenied
    step = _step_or_404(interview, number)
    for question in step.questions:
        if not access.can_view_answer(interview, question):
            raise PermissionDenied
    errors = {}
    if request.method == "POST":
        try:
            interview, errors = svc.correct_step(access, interview, step, request.POST, reason=request.POST.get("reason", ""))
        except svc.StaleRevision as exc:
            messages.warning(request, exc.message)
            return redirect(request.path)
        if not errors:
            messages.success(request, "Corrección guardada. El cambio quedó registrado.")
            return redirect("events:interview_detail", slug=event.slug, public_id=public_id)
    context = step_context(interview, step, errors={k: v for k, v in errors.items() if k != "reason"}, posted=request.POST if errors else None)
    context.update(
        {
            "participant": interview.participant,
            "form_action": request.path,
            "mode": "correction",
            "reason_error": errors.get("reason"),
            "reason_value": request.POST.get("reason", ""),
            "exit_url": reverse("events:interview_detail", args=[event.slug, public_id]),
        }
    )
    return render(request, "events/interview/step.html", context)


@event_view()
def safety_report(request, event, access, participant_id):
    participant = get_object_or_404(access.participants(include_inactive=True), public_id=participant_id)
    interview = participant.interviews.exclude(status=Interview.Status.DISCARDED).first()
    form = SafetyReportForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        svc.report_safety_concern(access, participant, details=form.cleaned_data["details"], interview=interview)
        messages.success(request, "El reporte se envió al equipo responsable. Sigue las indicaciones de protección de tu iglesia.")
        if interview and interview.status == Interview.Status.IN_PROGRESS and access.can_interview(participant):
            return redirect("events:interview_review", slug=event.slug, public_id=interview.public_id)
        return redirect("events:participant_detail", slug=event.slug, public_id=participant.public_id)
    return render(
        request,
        "events/interview/safety.html",
        {"participant": participant, "form": form, "instructions": event.safeguarding_instructions, "interview": interview},
    )
