from __future__ import annotations

import uuid

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Case, Count, IntegerField, OuterRef, Q, Subquery, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from ..forms import ParticipantForm
from ..models import ConsentRecord, FollowUp, Interview, InterviewStatus, QuestionnaireVersion, normalize_name
from ..services import event_questionnaires as eq
from ..services import interviews as interview_svc
from ..services import participants as participant_svc
from ..services import welcome as welcome_svc
from ..services.metrics import build_payload, coordinator_scope, mark_stalled, progress_for, stale_interviews
from ..services.questionnaire import build_steps
from .common import ADMIN, COORDINATOR, current_station, event_view, is_fragment, programme_now, safe_next


def _recent_completed(access) -> list:
    """The coordinator's last five completed interviews ("Completadas hace poco", on Mi avance). With several
    questionnaires each row names its own (the version's title)."""
    return list(
        access.interviews().filter(status=Interview.Status.COMPLETED).select_related("participant", "version").order_by("-completed_at")[:5]
    )


def _my_context(event, access):
    participants = access.participants()
    # With several questionnaires ``progress.partial`` counts the camperos "a medias" (some done, none live) apart
    # from ``not_started``, and ``progress.questionnaires`` has a row per questionnaire; one questionnaire: 0 and [].
    progress = progress_for(participants, event)
    mine = access.interviews()
    in_progress = list(mine.filter(status=Interview.Status.IN_PROGRESS).select_related("version").order_by("-last_activity_at")[:5])
    # "Tienes N entrevistas sin terminar" (issue #47): every interview of my camperos idle for STALE_MINUTES or more,
    # the oldest first, each with "Retomar" and "Descartar". "También sin terminar" keeps the ones still moving.
    stalled = mark_stalled(stale_interviews(participants).select_related("participant", "version", "station").order_by("last_activity_at"))
    stalled_ids = {interview.pk for interview in stalled}
    next_up = list(
        eq.annotate_questionnaire_progress(participants.filter(interview_status=InterviewStatus.PENDING), event).order_by("room", "full_name")[:6]
    )
    follow_ups = list(
        access.follow_ups().filter(kind=FollowUp.Kind.CARE, status__in=FollowUp.OPEN_STATUSES).order_by("-created_at")[:5]
    )
    to_evaluate = mine.filter(status=Interview.Status.COMPLETED, follow_up_decision=Interview.FollowUpDecision.TO_EVALUATE).count()
    # Minors whose guardian authorization is not registered cannot be interviewed: "Esperan algo de ti" says how many
    # Blocked from an interview: a minor already interviewed is not "cannot be interviewed" (an authorization can be
    # withdrawn afterwards; the campero's record says so), so the home's count leaves the completed ones out
    minors_pending = participant_svc.minors_without_authorization(participants, event).exclude(interview_status="completada").count() if event.requires_guardian_authorization else 0
    return {
        "progress": progress,
        "in_progress": in_progress,
        "stalled": stalled,
        "also_in_progress": [interview for interview in in_progress[1:] if interview.pk not in stalled_ids],
        "next_up": next_up,
        "follow_ups": follow_ups,
        "to_evaluate": to_evaluate,
        "minors_pending": minors_pending,
    }


@event_view(COORDINATOR)
def coord_home(request, event, access):
    context = _my_context(event, access)
    context["station"] = current_station(request, event)
    context["procedures_pending"] = not event.procedures_approved
    context["now_next"] = programme_now(event)  # "Ahora y después", first in the live fragment
    if is_fragment(request):
        return render(request, "events/coordinator/_home_live.html", context)
    # Outside the live region, so only the full page needs it. Same rule as interview_search: an event
    # without active computers is legitimate and needs no choice.
    context["needs_station"] = context["station"] is None and event.stations.filter(is_active=True).exists()
    # The welcome (first time in this role) is outside the live region: the full page only, never the fragment
    context["welcome"] = welcome_svc.for_coordinator(event, access, station=context["station"])
    return render(request, "events/coordinator/home.html", context)


@event_view(COORDINATOR, ADMIN)
def my_participants(request, event, access):
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("estado", "")
    participants = access.participants()
    if query:
        words = Q()
        for word in normalize_name(query).split():  # same rule as the interview search: every word, in any order
            words &= Q(normalized_name__contains=word)
        filters = words | Q(room__iexact=query)
        digits = query.lstrip("#")
        if digits.isascii() and digits.isdigit():  # "²".isdigit() is True and int("²") raises
            filters |= Q(number=int(digits))
        participants = participants.filter(filters)
    if status in InterviewStatus.values:
        participants = participants.filter(interview_status=status)
    # "?autorizacion=falta": only the minors whose guardian authorization is not registered (the welcome and
    # "Esperan algo de ti" link here); nothing when the event does not ask for it
    authorization_pending = request.GET.get("autorizacion") == "falta" and event.requires_guardian_authorization
    if authorization_pending:
        participants = participant_svc.minors_without_authorization(participants, event)
    participants = eq.annotate_questionnaire_progress(participants.select_related("sede"), event)
    if event.requires_guardian_authorization:
        # With the redesign a minor's row says its guardian authorization, apart from the interview: the latest
        # guardian record of each campero, read as the administration's list reads it (admin.participants)
        participants = participants.annotate(
            guardian=Subquery(
                ConsentRecord.objects.filter(participant=OuterRef("pk"), kind=ConsentRecord.Kind.GUARDIAN).order_by("-recorded_at", "-id").values("status")[:1]
            )
        )
    # What needs action first: an interview left half done, then the ones not started, then the finished ones
    # (the same rank as interview_search); room and name inside each.
    participants = list(
        participants.annotate(
            rank=Case(
                When(interview_status=InterviewStatus.IN_PROGRESS, then=Value(0)),
                When(interview_status=InterviewStatus.PENDING, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        )
        .order_by("rank", "room", "full_name")
    )
    counts = dict(access.participants().values_list("interview_status").annotate(n=Count("id")))
    tabs = [{"value": "", "label": "Todos", "count": sum(counts.values())}] + [
        {"value": value, "label": label, "count": counts.get(value, 0)} for value, label in eq.status_choices(event)
    ]
    total = access.participants().count()
    if total == 0:
        empty_reason = "sin_camperos"
    elif query:
        empty_reason = "busqueda" if not participants else ""
    elif authorization_pending:
        empty_reason = "autorizacion" if not participants else ""
    else:
        empty_reason = "filtro" if not participants else ""
    return render(
        request,
        "events/coordinator/participants.html",
        {
            # Shown apart, marked "De práctica", and in no count of this page (issue #49). A coordinador sees it while
            # it is theirs; the administration reaches it from Preparación, so its list stays the camperos'.
            "practice": access.practice_participant() if access.is_coordinator else None,
            "participants": participants,
            "query": query,
            "status": status,
            "tabs": tabs,
            "total": total,
            "empty_reason": empty_reason,
            "authorization_pending": authorization_pending,
            # The capsule "Menores sin autorización" (the redesign): the same rows "?autorizacion=falta" lists
            "authorization_count": (
                participant_svc.minors_without_authorization(access.participants(), event).count() if event.requires_guardian_authorization else None
            ),
        },
    )


@event_view()
def backup_list(request, event, access):
    """Printable paper fallback: registry data and the questionnaire (UX-05), never answers."""
    participants = access.participants().select_related("coordinator__user").order_by(
        "coordinator__user__first_name", "coordinator__user__last_name", "room", "full_name"
    )
    coordinator_id = request.GET.get("coordinador", "")
    if coordinator_id.isascii() and coordinator_id.isdigit():
        participants = participants.filter(coordinator_id=int(coordinator_id))
    participants = eq.annotate_questionnaire_progress(participants, event)
    groups = {}
    for p in participants:
        key = p.coordinator.display_name if p.coordinator else "Sin coordinador"
        groups.setdefault(key, []).append(p)
    # The questions new interviews ask, screen by screen, one sheet per questionnaire of the event in its order:
    # the structure only, no interview is read. A draft can still change, so it is never handed out on paper.
    paper_sheets = []
    for link in eq.links_for(event):
        version = link.version
        if version.status == QuestionnaireVersion.Status.DRAFT:
            continue
        steps = build_steps(version)
        if not steps:
            continue
        # Options in the order the screen shows them (interview.question_context): "Prefiero no responder" and other
        # exclusive options last. A dict, not attributes: build_steps caches its Question objects for every request.
        options = {}
        for step in steps:
            for question in step.questions:
                listed = list(question.options.all())
                quiet = [o for o in listed if o.is_decline or o.is_exclusive]
                options[question.id] = [o for o in listed if o not in quiet] + quiet
        anchor = "cuestionario" if not paper_sheets else f"cuestionario-{len(paper_sheets) + 1}"
        paper_sheets.append({"version": version, "steps": steps, "options": options, "anchor": anchor})
    return render(
        request,
        "events/backup_list.html",
        {"groups": groups, "printed_at": timezone.now(), "paper_sheets": paper_sheets},
    )


@event_view(COORDINATOR, ADMIN)
def participant_create(request, event, access):
    if not access.can_register_participants:
        raise PermissionDenied
    form = ParticipantForm(request.POST or None, event=event, can_choose_coordinator=access.is_admin)
    if request.method == "POST" and form.is_valid():
        coordinator = access.membership if access.is_coordinator else form.cleaned_data.get("coordinator")
        participant = participant_svc.create_participant(
            event,
            actor=request.user,
            full_name=form.cleaned_data["full_name"],
            age=form.cleaned_data["age"],
            room=form.cleaned_data["room"],
            church_group=form.cleaned_data["church_group"],
            coordinator=coordinator,
            guardian_authorization=form.cleaned_data.get("guardian_authorization", False),
            sede=form.cleaned_data.get("sede"),
        )
        messages.success(request, f"{participant.full_name} quedó registrado con el número {participant.code}.")
        if request.POST.get("then") == "entrevistar" and access.can_interview(participant):
            return redirect("events:interview_start", slug=event.slug, public_id=participant.public_id)
        if request.POST.get("then") == "otro":
            return redirect("events:participant_create", slug=event.slug)
        return redirect("events:my_participants" if access.is_coordinator else "events:participants", slug=event.slug)
    groups = (
        event.participants.real().exclude(church_group="").values_list("church_group", flat=True).distinct().order_by("church_group")[:60]
    )
    return render(request, "events/participant_form.html", {"form": form, "groups": groups, "creating": True})


def _decide_url(event, interview) -> str:
    """The record where an interview's follow-up is decided, coming back here to go on with the next one."""
    after = reverse("events:coord_pending", args=[event.slug]) + "?" + urlencode({"decidir": "siguiente", "despues": interview.public_id})
    return reverse("events:interview_detail", args=[event.slug, interview.public_id]) + "?" + urlencode({"siguiente": after}) + "#seguimiento"


def _following(access, to_evaluate, after):
    """The interview of "Por decidir" that comes after ``after`` (the public id of one just decided, so no longer in the
    list) in the list's order, wrapping to the first; None when nothing is left. Only this person's interviews count."""
    queue = list(to_evaluate.select_related(None).only("pk", "public_id", "started_at"))
    if not queue:
        return None
    decided = access.interviews().select_related(None).filter(public_id=after).only("pk", "started_at").first() if _is_uuid(after) else None
    if decided is None:
        return queue[0]
    return next((iv for iv in queue if (iv.started_at, iv.pk) < (decided.started_at, decided.pk)), queue[0])


def _next_decision(request, event, access, to_evaluate):
    """After a decision taken on the interview's record, opened from "Por decidir" (?decidir=siguiente&despues=<the one
    decided>): the next one's record; with none left, the list where "Por decidir" says so."""
    following = _following(access, to_evaluate, request.GET.get("despues", ""))
    if following is None:
        return redirect(reverse("events:coord_pending", args=[event.slug]) + "#por-evaluar")
    return redirect(_decide_url(event, following))


def _decide_in_place(request, event, access):
    """«Por decidir» answered from its row (the redesign: an action in place, form[data-inline]): the same decision the
    interview's record takes (services.interviews.update_follow_up_decision, with its permission check), then back to
    the list, same site only. The message names the campero and the answer."""
    here = reverse("events:coord_pending", args=[event.slug])
    back = safe_next(request, request.POST.get("siguiente"), here + "#por-evaluar")
    public_id = request.POST.get("entrevista", "")
    if not _is_uuid(public_id):
        raise PermissionDenied
    interview = get_object_or_404(access.interviews().select_related("participant"), public_id=public_id)
    decision = request.POST.get("decision", "")
    try:
        interview_svc.update_follow_up_decision(access, interview, decision)
    except interview_svc.InterviewError as exc:
        messages.error(request, exc.message)
        return redirect(back)
    name = interview.participant.full_name
    if decision == Interview.FollowUpDecision.REQUIRED:
        messages.success(request, f"{name} necesita seguimiento.")
    else:
        messages.success(request, f"{name} no necesita seguimiento.")
    return redirect(back)


def _is_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


@event_view(COORDINATOR)
def coord_pending(request, event, access):
    if request.method == "POST":
        return _decide_in_place(request, event, access)
    participants = access.participants()
    in_progress = access.interviews().filter(status=Interview.Status.IN_PROGRESS).select_related("participant", "version").order_by("last_activity_at")
    not_started = eq.annotate_questionnaire_progress(participants.filter(interview_status=InterviewStatus.PENDING), event).order_by(
        "room", "full_name"
    )
    # With several questionnaires "Sin iniciar" keeps the camperos with none done; the ones who answered some go to
    # "A medias". None with one questionnaire: the page is the one it always was.
    partial = None
    if event.questionnaire_count > 1:
        partial = list(not_started.filter(done_count__gt=0))
        not_started = not_started.filter(done_count=0)
    to_evaluate = access.interviews().filter(
        status=Interview.Status.COMPLETED, follow_up_decision=Interview.FollowUpDecision.TO_EVALUATE
    ).select_related("participant", "version").order_by("-started_at", "-pk")
    if request.GET.get("decidir") == "siguiente":
        return _next_decision(request, event, access, to_evaluate)
    return render(
        request,
        "events/coordinator/pending.html",
        {
            "in_progress": in_progress,
            "not_started": not_started,
            "partial": partial,
            "to_evaluate": to_evaluate,
            "has_participants": participants.exists(),
            # After a decision in place (?despues=<the one decided>) the next row of "Por decidir" takes the focus
            "next_focus": _following(access, to_evaluate, request.GET.get("despues", "")) if request.GET.get("despues") else None,
        },
    )


@event_view(COORDINATOR)
def coord_progress(request, event, access):
    payload = build_payload(coordinator_scope(event, access.membership))
    # "recent" stays out of build_payload: that payload is also stored in report snapshots.
    context = {"payload": payload, "progress": payload["progress"], "recent": _recent_completed(access)}
    if is_fragment(request):
        return render(request, "events/coordinator/_progress_live.html", context)
    return render(request, "events/coordinator/progress.html", context)


@event_view()
def participant_detail(request, event, access, public_id):
    participant = get_object_or_404(access.participants(include_inactive=True, include_practice=True), public_id=public_id)
    # One questionnaire: the "Entrevista" block shows ``interview`` (the newest kept), as it always did. Several: one row
    # per questionnaire of the event (``questionnaire_rows``); the page head offers to resume ``live_interview``.
    interview = participant.interviews.exclude(status=Interview.Status.DISCARDED).first()
    live_interview = participant.interviews.filter(status=Interview.Status.IN_PROGRESS).first()
    questionnaire_rows = eq.progress_for(participant) if event.questionnaire_count > 1 else []
    questionnaires_done = sum(1 for row in questionnaire_rows if row.state == eq.COMPLETED)
    if participant.is_practice:  # its own page (issue #49): the rehearsal only, nothing to correct, authorize or follow up
        return render(
            request,
            "events/practice_detail.html",
            {
                "participant": participant,
                "interview": interview,
                "live_interview": live_interview,
                "questionnaire_rows": questionnaire_rows,
                "questionnaires_done": questionnaires_done,
                "can_interview": access.can_interview(participant),
            },
        )
    follow_ups = access.follow_ups().filter(participant=participant)
    from ..models import ConsentRecord
    from ..services.participants import latest_consent

    guardian = latest_consent(participant, ConsentRecord.Kind.GUARDIAN)
    return render(
        request,
        "events/participant_detail.html",
        {
            "participant": participant,
            "interview": interview,
            "live_interview": live_interview,
            "questionnaire_rows": questionnaire_rows,
            "questionnaires_done": questionnaires_done,
            "follow_ups": follow_ups,
            "can_edit": access.can_edit_participant(participant),
            "can_interview": access.can_interview(participant),
            "guardian_ok": bool(guardian and guardian.status == ConsentRecord.Status.GRANTED),
            "guardian_record": guardian,
            "consents": participant.consents.select_related("recorded_by")[:6],
        },
    )


@event_view(COORDINATOR, ADMIN)
def participant_edit(request, event, access, public_id):
    participant = get_object_or_404(access.participants(include_inactive=True), public_id=public_id)
    if not access.can_edit_participant(participant):
        raise PermissionDenied
    from ..forms import ParticipantAdminForm

    form_class = ParticipantAdminForm if access.is_admin else ParticipantForm
    form = form_class(request.POST or None, instance=participant, event=event, can_choose_coordinator=access.is_admin, show_guardian=False)
    if request.method == "POST":
        if form.is_valid():
            # is_valid() already copied the values onto the instance; reload so the service can log the real diff.
            changes = {name: form.cleaned_data[name] for name in form.Meta.fields if name in form.cleaned_data}
            participant.refresh_from_db()
            participant_svc.update_participant(participant, actor=request.user, **changes)
            messages.success(request, "Datos actualizados.")
            return redirect("events:participant_detail", slug=event.slug, public_id=participant.public_id)
    return render(request, "events/participant_form.html", {"form": form, "participant": participant, "creating": False})


def _guardian_participant(access, public_id):
    participant = get_object_or_404(access.participants(include_inactive=True), public_id=public_id)
    if not access.can_edit_participant(participant):
        raise PermissionDenied
    return participant


@event_view(COORDINATOR, ADMIN)
def guardian_authorization(request, event, access, public_id):
    """"Marcar como recibida": a page that says what is attested and by whom; only its own button records it.

    A POST without ``confirmar`` (a stale one-click form) is sent to that page instead of recording.
    """
    participant = _guardian_participant(access, public_id)
    detail = reverse("events:participant_detail", args=[event.slug, participant.public_id])
    after = safe_next(request, request.POST.get("siguiente") or request.GET.get("siguiente"), detail)
    if request.method == "POST" and request.POST.get("confirmar") == "si":
        if participant_svc.guardian_authorized(participant):
            messages.info(request, "La autorización ya estaba registrada.")
        else:
            participant_svc.record_guardian_authorization(participant, actor=request.user, granted=True)
            messages.success(request, "Autorización registrada.")
        return redirect(after)
    if request.method == "POST":
        page = reverse("events:guardian_authorization", args=[event.slug, participant.public_id])
        return redirect(f"{page}?{urlencode({'siguiente': after})}" if after != detail else page)
    if participant_svc.guardian_authorized(participant):
        messages.info(request, "La autorización ya está registrada.")
        return redirect(detail)
    return render(request, "events/guardian_authorization.html", {"participant": participant, "after": after, "back_url": detail})


class GuardianWithdrawForm(forms.Form):
    reason = forms.CharField(
        label="¿Por qué la quitas?",
        help_text="Por ejemplo: se marcó por error, o la familia retiró su permiso. Queda registrado con tu nombre.",
        max_length=200,
        widget=forms.Textarea(attrs={"rows": 3}),
        error_messages={"required": "Escribe por qué quitas la autorización.",
                        "max_length": "Escribe el motivo en 200 caracteres o menos."},
    )


@event_view(COORDINATOR, ADMIN)
def guardian_authorization_withdraw(request, event, access, public_id):
    """"Quitar la autorización": undoes "Marcar como recibida" with a reason. Completed interviews keep theirs."""
    participant = _guardian_participant(access, public_id)
    detail = reverse("events:participant_detail", args=[event.slug, participant.public_id])
    if not participant_svc.guardian_authorized(participant):
        messages.info(request, "No hay una autorización registrada que quitar.")
        return redirect(detail)
    form = GuardianWithdrawForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        participant_svc.withdraw_guardian_authorization(participant, actor=request.user, reason=form.cleaned_data["reason"].strip())
        messages.success(request, "Se quitó la autorización. Antes de entrevistar, alguien tendrá que confirmarla otra vez.")
        return redirect(detail)
    in_progress = participant.interviews.filter(status=Interview.Status.IN_PROGRESS).exists()
    return render(request, "events/guardian_authorization_withdraw.html",
                  {"participant": participant, "form": form, "back_url": detail, "interview_in_progress": in_progress})
