from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from ..forms import ParticipantForm
from ..models import FollowUp, Interview, InterviewStatus, normalize_name
from ..services import participants as participant_svc
from ..services.metrics import build_payload, coordinator_scope, progress_for, stale_interviews
from .common import ADMIN, COORDINATOR, current_station, event_view, is_fragment


def _my_context(event, access):
    participants = access.participants()
    progress = progress_for(participants)
    mine = access.interviews()
    in_progress = list(mine.filter(status=Interview.Status.IN_PROGRESS).order_by("-last_activity_at")[:5])
    recent = list(mine.filter(status=Interview.Status.COMPLETED).order_by("-completed_at")[:5])
    next_up = list(participants.filter(interview_status=InterviewStatus.PENDING).order_by("room", "full_name")[:6])
    follow_ups = list(
        access.follow_ups().filter(kind=FollowUp.Kind.CARE, status__in=FollowUp.OPEN_STATUSES).order_by("-created_at")[:5]
    )
    to_evaluate = mine.filter(status=Interview.Status.COMPLETED, follow_up_decision=Interview.FollowUpDecision.TO_EVALUATE).count()
    return {
        "progress": progress,
        "in_progress": in_progress,
        "recent": recent,
        "next_up": next_up,
        "follow_ups": follow_ups,
        "to_evaluate": to_evaluate,
        "stale": stale_interviews(participants).count(),
    }


@event_view(COORDINATOR)
def coord_home(request, event, access):
    context = _my_context(event, access)
    context["station"] = current_station(request, event)
    if is_fragment(request):
        return render(request, "events/coordinator/_home_live.html", context)
    return render(request, "events/coordinator/home.html", context)


@event_view(COORDINATOR, ADMIN)
def my_participants(request, event, access):
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("estado", "")
    participants = access.participants()
    if query:
        normalized = normalize_name(query)
        filters = Q(normalized_name__contains=normalized) | Q(room__iexact=query)
        if query.lstrip("#").isdigit():
            filters |= Q(number=int(query.lstrip("#")))
        participants = participants.filter(filters)
    if status in InterviewStatus.values:
        participants = participants.filter(interview_status=status)
    participants = participants.order_by("room", "full_name")
    counts = dict(access.participants().values_list("interview_status").annotate(n=Count("id")))
    tabs = [{"value": "", "label": "Todos", "count": sum(counts.values())}] + [
        {"value": value, "label": label, "count": counts.get(value, 0)} for value, label in InterviewStatus.choices
    ]
    return render(
        request,
        "events/coordinator/participants.html",
        {"participants": participants, "query": query, "status": status, "tabs": tabs, "total": access.participants().count()},
    )


@event_view()
def backup_list(request, event, access):
    """Printable paper fallback: registry data only, never answers."""
    participants = access.participants().select_related("coordinator__user").order_by(
        "coordinator__user__first_name", "coordinator__user__last_name", "room", "full_name"
    )
    coordinator_id = request.GET.get("coordinador", "")
    if coordinator_id.isdigit():
        participants = participants.filter(coordinator_id=int(coordinator_id))
    groups = {}
    for p in participants:
        key = p.coordinator.display_name if p.coordinator else "Sin coordinador"
        groups.setdefault(key, []).append(p)
    return render(request, "events/backup_list.html", {"groups": groups})


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
        )
        messages.success(request, f"{participant.full_name} quedó registrado con el número {participant.code}.")
        if request.POST.get("then") == "entrevistar" and access.can_interview(participant):
            return redirect("events:interview_start", slug=event.slug, public_id=participant.public_id)
        if request.POST.get("then") == "otro":
            return redirect("events:participant_create", slug=event.slug)
        return redirect("events:my_participants" if access.is_coordinator else "events:participants", slug=event.slug)
    groups = (
        event.participants.exclude(church_group="").values_list("church_group", flat=True).distinct().order_by("church_group")[:60]
    )
    return render(request, "events/participant_form.html", {"form": form, "groups": groups, "creating": True})


@event_view(COORDINATOR)
def coord_pending(request, event, access):
    participants = access.participants()
    in_progress = access.interviews().filter(status=Interview.Status.IN_PROGRESS).order_by("last_activity_at")
    not_started = participants.filter(interview_status=InterviewStatus.PENDING).order_by("room", "full_name")
    to_evaluate = access.interviews().filter(
        status=Interview.Status.COMPLETED, follow_up_decision=Interview.FollowUpDecision.TO_EVALUATE
    )
    return render(
        request,
        "events/coordinator/pending.html",
        {"in_progress": in_progress, "not_started": not_started, "to_evaluate": to_evaluate},
    )


@event_view(COORDINATOR)
def coord_progress(request, event, access):
    payload = build_payload(coordinator_scope(event, access.membership))
    context = {"payload": payload, "progress": payload["progress"]}
    if is_fragment(request):
        return render(request, "events/coordinator/_progress_live.html", context)
    return render(request, "events/coordinator/progress.html", context)


@event_view()
def participant_detail(request, event, access, public_id):
    participant = get_object_or_404(access.participants(include_inactive=True), public_id=public_id)
    interview = participant.interviews.exclude(status=Interview.Status.DISCARDED).first()
    follow_ups = access.follow_ups().filter(participant=participant)
    from ..services.participants import guardian_authorized

    return render(
        request,
        "events/participant_detail.html",
        {
            "participant": participant,
            "interview": interview,
            "follow_ups": follow_ups,
            "can_edit": access.can_edit_participant(participant),
            "can_interview": access.can_interview(participant),
            "guardian_ok": guardian_authorized(participant),
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


@event_view(COORDINATOR, ADMIN)
def guardian_authorization(request, event, access, public_id):
    participant = get_object_or_404(access.participants(include_inactive=True), public_id=public_id)
    if not access.can_edit_participant(participant) or request.method != "POST":
        raise PermissionDenied
    participant_svc.record_guardian_authorization(participant, actor=request.user, granted=True)
    messages.success(request, "Autorización registrada.")
    next_url = request.POST.get("siguiente", "")
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect("events:participant_detail", slug=event.slug, public_id=participant.public_id)
