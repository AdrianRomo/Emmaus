from __future__ import annotations

from datetime import datetime, time, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from ..forms import FollowUpForm
from ..models import (
    AccessRequest,
    AuditEvent,
    Event,
    EventMembership,
    EventParticipant,
    FollowUp,
    Interview,
    InterviewStatus,
    MembershipRole,
    ParticipantSession,
    ReportSnapshot,
)
from ..services import audit
from ..services.metrics import build_payload, coordinator_scope, local_tz, scope_for_access, stale_interviews
from ..services.reports import next_slot
from .common import ADMIN, LEADERS, PASTOR, TUTOR, event_view, is_fragment


def _last_snapshot(access):
    """The newest cutoff of the scope this page shows: the whole event for the pastor and the
    administration, the team for a tutor. A team's cutoff is never the baseline of the whole event."""
    scope_key = scope_for_access(access).key
    return (
        access.snapshots()
        .filter(status=ReportSnapshot.Status.READY, scope_key=scope_key)
        .order_by("-cutoff_at", "-id")
        .first()
    )


def _recent_activity(access, limit=8):
    return (
        access.interviews()
        .filter(Q(status=Interview.Status.COMPLETED) | Q(status=Interview.Status.IN_PROGRESS))
        .select_related("participant", "conducted_by")
        .order_by("-last_activity_at")[:limit]
    )


def _last_station(access):
    """Where each coordinator was last seen: the computer of the latest interview they conducted
    with one of their own camperos. `now` is true while that interview is still in progress."""
    places = {}
    rows = (
        access.interviews()
        .filter(station__isnull=False, conducted_by=F("participant__coordinator__user"))
        .order_by("-last_activity_at")
        .values_list("participant__coordinator", "station__name", "status")
    )
    for coordinator_id, station, status in rows:
        places.setdefault(coordinator_id, {"station": station, "now": status == Interview.Status.IN_PROGRESS})
    return places


def _stalled_stations(access):
    """The computer where each coordinator's oldest stalled interview was left: where to go and help."""
    places = {}
    rows = (
        stale_interviews(access.participants())
        .filter(station__isnull=False)
        .order_by("last_activity_at")
        .values_list("participant__coordinator", "station__name")
    )
    for coordinator_id, station in rows:
        places.setdefault(coordinator_id, station)
    return places


# ---- Tutor -----------------------------------------------------------------------------------


@event_view(TUTOR)
def tutor_home(request, event, access):
    payload = build_payload(scope_for_access(access))
    open_care = access.follow_ups().filter(kind=FollowUp.Kind.CARE, status__in=FollowUp.OPEN_STATUSES)
    context = {
        "payload": payload,
        "progress": payload["progress"],
        "coordinators": payload["coordinators"],
        "recent": _recent_activity(access),
        # The sign names whose interviews stalled; the rows say where each coordinator is.
        "stalled": [row for row in payload["coordinators"] if row["stale"]],
        "places": _last_station(access),
        "stalled_places": _stalled_stations(access),
        "follow_ups": open_care.order_by("created_at")[:5],
        "follow_ups_open": open_care.count(),
        "last_snapshot": _last_snapshot(access),
        "next_cutoff": next_slot(event),
    }
    if is_fragment(request):
        return render(request, "events/tutor/_home_live.html", context)
    return render(request, "events/tutor/home.html", context)


@event_view(*LEADERS)
def coordinators(request, event, access):
    payload = build_payload(scope_for_access(access))
    return render(
        request,
        "events/supervision/coordinators.html",
        {"rows": payload["coordinators"], "progress": payload["progress"], "places": _last_station(access)},
    )


@event_view(*LEADERS)
def coordinator_detail(request, event, access, membership_id):
    coordinator = get_object_or_404(access.coordinators(), pk=membership_id)
    payload = build_payload(coordinator_scope(event, coordinator))
    participants = access.participants().filter(coordinator=coordinator).order_by("interview_status", "room", "full_name")
    return render(
        request,
        "events/supervision/coordinator_detail.html",
        {"coordinator": coordinator, "payload": payload, "progress": payload["progress"], "participants": participants},
    )


PERIODS = [("", "Todo el tiempo"), ("2h", "Últimas 2 horas"), ("hoy", "Hoy")]


@event_view(*LEADERS)
def interviews(request, event, access):
    participants = access.participants().select_related("coordinator__user")
    coordinator_id = request.GET.get("coordinador", "")
    room = request.GET.get("cuarto", "")
    group = request.GET.get("grupo", "")
    status = request.GET.get("estado", "")
    period = request.GET.get("periodo", "")
    if coordinator_id.isascii() and coordinator_id.isdigit():  # "²".isdigit() is True and int("²") raises
        participants = participants.filter(coordinator_id=int(coordinator_id))
    if room:
        participants = participants.filter(room=room)
    if group:
        participants = participants.filter(church_group=group)
    if status in InterviewStatus.values:
        participants = participants.filter(interview_status=status)
    if period:
        now = timezone.localtime()
        since = now - timedelta(hours=2) if period == "2h" else now.replace(hour=0, minute=0, second=0, microsecond=0)
        participants = participants.filter(
            interviews__status=Interview.Status.COMPLETED, interviews__completed_at__gte=since
        ).distinct()
    participants = participants.order_by("coordinator__user__first_name", "room", "full_name")
    page = Paginator(participants, 50).get_page(request.GET.get("pagina"))
    scoped = access.participants()
    filters_active = any([coordinator_id, room, group, status, period])
    return render(
        request,
        "events/supervision/interviews.html",
        {
            "page": page,
            "coordinators": access.coordinators(),
            "rooms": scoped.order_by("room").values_list("room", flat=True).distinct(),
            "groups": scoped.order_by("church_group").values_list("church_group", flat=True).distinct(),
            "status_choices": InterviewStatus.choices,
            "periods": PERIODS,
            "selected": {"coordinador": coordinator_id, "cuarto": room, "grupo": group, "estado": status, "periodo": period},
            "filters_active": filters_active,
            "querystring": request.GET.urlencode(),
        },
    )


@event_view(TUTOR, PASTOR, ADMIN)
def pending(request, event, access):
    participants = access.participants()
    stale = stale_interviews(participants).select_related("participant__coordinator__user", "conducted_by", "station")
    in_progress = access.interviews().filter(status=Interview.Status.IN_PROGRESS).exclude(pk__in=stale.values("pk"))
    to_evaluate = access.interviews().filter(status=Interview.Status.COMPLETED, follow_up_decision=Interview.FollowUpDecision.TO_EVALUATE)
    follow_ups = access.follow_ups().filter(status__in=FollowUp.OPEN_STATUSES).order_by("kind", "created_at")
    not_started_by_coordinator = (
        participants.filter(interview_status=InterviewStatus.PENDING)
        .values("coordinator__id", "coordinator__user__first_name", "coordinator__user__last_name")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    return render(
        request,
        "events/supervision/pending.html",
        {
            "stale": stale,
            "in_progress": in_progress,
            "to_evaluate": to_evaluate,
            "follow_ups": follow_ups,
            "not_started_by_coordinator": not_started_by_coordinator,
        },
    )


# ---- Pastor ----------------------------------------------------------------------------------


@event_view(PASTOR, ADMIN)
def pastor_home(request, event, access):
    payload = build_payload(scope_for_access(access), include_safety=access.can_view_sensitive or access.is_admin)
    if not access.can_view_sensitive:
        payload["follow_ups"]["confidential_open"] = None
    last = _last_snapshot(access)
    from ..services.metrics import compare
    from ..services.reports import latest_snapshot, visible_payload

    delta = compare(payload, last.data) if last else {}
    outcome_snapshot = latest_snapshot(event, "evento")
    outcomes = visible_payload(outcome_snapshot.data, access)["outcomes"] if outcome_snapshot else []
    eta = payload["throughput"]["eta_minutes"]
    # Only promise a clock time when it is within reach; beyond 12 h the pace says more than a time.
    finish_at = timezone.localtime() + timedelta(minutes=eta) if eta and eta <= 12 * 60 else None
    context = {
        "finish_at": finish_at,
        "projection_label": f"Hacia las {finish_at:%H:%M}" if finish_at else "",
        "chart_eta": eta if finish_at else None,  # beyond 12 h a projection would squash the real data
        "payload": payload,
        "progress": payload["progress"],
        "teams": payload["teams"],
        "delta": delta,
        "last_snapshot": last,
        "next_cutoff": next_slot(event),
        "outcomes": outcomes,
        "outcomes_snapshot": outcome_snapshot,
    }
    if is_fragment(request):
        return render(request, "events/pastor/_home_live.html", context)
    return render(request, "events/pastor/home.html", context)


@event_view(PASTOR, ADMIN)
def progress(request, event, access):
    payload = build_payload(scope_for_access(access))
    context = {"payload": payload, "progress": payload["progress"], "teams": payload["teams"], "coordinators": payload["coordinators"]}
    if is_fragment(request):
        return render(request, "events/pastor/_progress_live.html", context)
    return render(request, "events/pastor/progress.html", context)


@event_view(*LEADERS, MembershipRole.COORDINATOR)
def follow_ups(request, event, access):
    status = request.GET.get("estado", "abiertos")
    qs = access.follow_ups().select_related("participant__coordinator__user", "assigned_to")
    if status == "abiertos":
        qs = qs.filter(status__in=FollowUp.OPEN_STATUSES)
    elif status in FollowUp.Status.values:
        qs = qs.filter(status=status)
    qs = qs.order_by("-kind", "status", "-created_at")
    counts = dict(access.follow_ups().values_list("status").annotate(n=Count("id")))
    return render(
        request,
        "events/supervision/follow_ups.html",
        {"follow_ups": qs[:200], "status": status, "counts": counts, "status_choices": FollowUp.Status.choices},
    )


@event_view(*LEADERS, MembershipRole.COORDINATOR)
def follow_up_detail(request, event, access, follow_up_id):
    follow_up = get_object_or_404(access.follow_ups(), pk=follow_up_id)
    can_manage = access.can_manage_follow_up(follow_up)
    can_read = access.can_read_follow_up_details(follow_up)
    form = FollowUpForm(request.POST or None, instance=follow_up, event=event, can_write_notes=can_read) if can_manage else None
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied
        if form.is_valid():
            item = form.save(commit=False)
            if item.status == FollowUp.Status.DONE and not item.resolved_at:
                item.resolved_at = timezone.now()
            item.save()
            audit.record(request.user, event, "seguimiento", f"Actualizó un seguimiento del campero #{follow_up.participant.code}", follow_up, status=item.status)
            messages.success(request, "Seguimiento actualizado.")
            return redirect("events:follow_ups", slug=event.slug)
    if can_read and (follow_up.details or follow_up.resolution_note):
        audit.record(request.user, event, "consulta_confidencial", f"Consultó un seguimiento confidencial del campero #{follow_up.participant.code}", follow_up)
    return render(
        request,
        "events/supervision/follow_up_detail.html",
        {"follow_up": follow_up, "form": form, "can_read": can_read, "can_manage": can_manage},
    )


@event_view(PASTOR, ADMIN)
def team(request, event, access):
    tutors = EventMembership.objects.filter(event=event, role=MembershipRole.TUTOR).select_related("user").prefetch_related(
        "supervised_assignments__coordinator__user"
    )
    unassigned = EventMembership.objects.filter(event=event, role=MembershipRole.COORDINATOR, tutor_assignment__isnull=True).select_related("user")
    others = EventMembership.objects.filter(event=event, role__in=[MembershipRole.ADMIN, MembershipRole.PASTOR]).select_related("user")
    counts = dict(
        event.participants.filter(is_active=True).values_list("coordinator").annotate(n=Count("id"))
    )
    context = {"tutors": tutors, "unassigned": unassigned, "others": others, "counts": counts, "can_manage": access.is_admin}
    if access.is_admin:  # requests carry strangers' e-mails and notes: only the people who decide see them
        from ..services.team import describe_account

        event.ensure_join_code()
        access_requests = list(AccessRequest.objects.filter(event=event, status=AccessRequest.Status.PENDING))
        for access_request in access_requests:  # at most 50 (the public form's cap)
            access_request.account = describe_account(event, access_request.email)
        context.update({
            "access_requests": access_requests,
            "role_choices": MembershipRole.choices,
            "join_url": request.build_absolute_uri(reverse("join")),
            "entry_url": request.build_absolute_uri(reverse("event_entry", args=[event.join_code])),
        })
    return render(request, "events/supervision/team.html", context)


# ---- Admin -----------------------------------------------------------------------------------


# The scheduler (run_report_cutoffs --loop 300) stores an automatic cutoff within minutes of each slot.
SCHEDULER_LATE = timedelta(minutes=15)
_MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _when(moment, local_now, tz) -> tuple[str, str]:
    """("hoy" | "ayer" | "23 oct", "14:00") for a moment, in the event's time zone."""
    local = moment.astimezone(tz)
    days = (local_now.date() - local.date()).days
    day = "hoy" if days == 0 else "ayer" if days == 1 else f"{local.day} {_MONTHS[local.month - 1]}"
    return day, f"{local:%H:%M}"


def _since_phrase(moment, local_now, tz) -> str:
    """"las 12:00", "ayer a las 20:00" or "el 23 oct a las 20:00": what follows "desde" or "el corte de"."""
    day, clock = _when(moment, local_now, tz)
    return f"las {clock}" if day == "hoy" else f"ayer a las {clock}" if day == "ayer" else f"el {day} a las {clock}"


def _automatic_cutoffs(event, now) -> dict | None:
    """The "Cortes automáticos" row: when the scheduler last stored a cutoff, and whether it missed one.

    Shown only while the event is in course with automatic cutoffs on: otherwise the scheduler has nothing to do
    and the row would say nothing true. Late means: the first slot strictly after ``since`` passed more than
    15 minutes ago without an automatic cutoff. ``since`` is the latest of the newest automatic cutoff, the
    start of today, and the moments the scheduler could not have been expected to work before: the event's
    last save (the settings form saves it when its status or its cutoffs change, so a camp opened at 10:40
    or cutoffs turned back on at 16:45 owe nothing for 08:00) and the confirmation of its procedures. A save
    for another reason only delays an alarm by one slot. Manual cutoffs prove nothing about the scheduler.
    """
    if not event.reports_enabled or event.status != Event.Status.ACTIVE:
        return None
    tz = local_tz(event)
    local_now = now.astimezone(tz)
    last = (
        ReportSnapshot.objects.filter(event=event, trigger=ReportSnapshot.Trigger.AUTOMATIC)
        .order_by("-cutoff_at", "-id")
        .first()
    )
    changed = max(m for m in (event.updated_at, event.procedures_approved_at) if m)  # nothing was owed before this
    since = max(m for m in (last.cutoff_at if last else None, datetime.combine(local_now.date(), time.min, tzinfo=tz), changed) if m)
    expected = next_slot(event, since)
    late = expected is not None and now - expected > SCHEDULER_LATE
    row = {"late": late, "last": ""}
    if last:
        day, clock = _when(last.cutoff_at, local_now, tz)
        row["last"] = f"{day} {clock}" if day in ("hoy", "ayer") else f"{day}, {clock}"  # "hoy 14:00", "23 oct, 14:00"
    if late and last and last.cutoff_at >= changed:
        row["late_text"] = f"El programador de cortes no ha corrido desde {_since_phrase(last.cutoff_at, local_now, tz)}."
    elif late:  # the event changed after its last cutoff (opened, cutoffs turned back on): name the missed slot
        row["late_text"] = f"El programador de cortes no ha corrido: el corte de {_since_phrase(expected, local_now, tz)} no se generó."
    return row


@event_view(ADMIN)
def admin_home(request, event, access):
    payload = build_payload(scope_for_access(access), include_safety=True)
    if not access.can_view_sensitive:
        payload["follow_ups"]["confidential_open"] = None
    roles = dict(EventMembership.objects.filter(event=event, is_active=True).values_list("role").annotate(n=Count("id")))
    active_sessions = ParticipantSession.objects.filter(
        interview__event=event, state__in=[ParticipantSession.State.ACTIVE, ParticipantSession.State.AWAITING_UNLOCK]
    ).count()
    now = timezone.now()
    open_claims = Interview.objects.filter(event=event, status=Interview.Status.IN_PROGRESS, lock_expires_at__gt=now).count()
    failed = access.snapshots().filter(status=ReportSnapshot.Status.FAILED).order_by("-created_at")[:3]
    failed_count = access.snapshots().filter(status=ReportSnapshot.Status.FAILED).count()
    exports = AuditEvent.objects.filter(event=event, action="exportacion").order_by("-created_at")[:5]
    problems = []
    if not event.procedures_approved:
        problems.append(("Falta confirmar los procedimientos de autorización y protección. Sin eso no se pueden iniciar entrevistas.", "events:settings"))
    if not event.questionnaire_version_id:
        problems.append(("El evento no tiene un cuestionario publicado.", "events:questionnaires"))
    if failed_count:
        text = "1 corte no se pudo generar." if failed_count == 1 else f"{failed_count} cortes no se pudieron generar."
        problems.append((text, "events:reports"))
    unassigned = payload["attention"]["unassigned_participants"]
    if unassigned:
        text = "1 campero no tiene coordinador." if unassigned == 1 else f"{unassigned} camperos no tienen coordinador."
        problems.append((text, "events:participants"))
    without_tutor = payload["attention"]["coordinators_without_tutor"]
    if without_tutor:
        text = "1 coordinador no tiene tutor." if without_tutor == 1 else f"{without_tutor} coordinadores no tienen tutor."
        problems.append((text, "events:assignments"))
    # "Equipo" in Preparación says "Listo" only when someone can interview and someone reviews them (audit UX-11): no
    # coordinator is "falta"; a coordinator without a tutor is "revisar" (every coordinator with a tutor implies a tutor).
    coordinators = roles.get(MembershipRole.COORDINATOR, 0)
    team_state = "falta" if not coordinators else "revisar" if without_tutor else "listo"
    context = {
        "payload": payload,
        "progress": payload["progress"],
        # No camperos yet: the next-action panel asks to import or register them (audit UI-03)
        "no_participants": not EventParticipant.objects.filter(event=event, is_active=True).exists(),
        "team_state": team_state,
        # "Cortes" says "Listo" once the scheduler stored its first automatic cutoff; until then "Esperando" (UX-11)
        "has_automatic_cutoff": ReportSnapshot.objects.filter(event=event, trigger=ReportSnapshot.Trigger.AUTOMATIC).exists(),
        "roles": roles,
        "role_labels": MembershipRole.choices,
        "active_sessions": active_sessions,
        "open_claims": open_claims,
        "problems": problems,
        "failed_reports": failed_count,
        "exports": exports,
        # Without confidential access, rows about confidential follow-ups and safety reports say only
        # "Movimiento confidencial", as on the activity log page: their summaries name the campero.
        "recent": audit.with_shown_summaries(AuditEvent.objects.filter(event=event).select_related("actor")[:8], access),
        "last_snapshot": _last_snapshot(access),
        "next_cutoff": next_slot(event),
        "auto_cutoffs": _automatic_cutoffs(event, now),
        "stations": event.stations.filter(is_active=True).count(),
        "pending_requests": AccessRequest.objects.filter(event=event, status=AccessRequest.Status.PENDING).count(),
    }
    if is_fragment(request):
        return render(request, "events/admin/_home_live.html", context)
    return render(request, "events/admin/home.html", context)

