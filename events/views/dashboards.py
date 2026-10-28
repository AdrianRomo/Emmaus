from __future__ import annotations

from datetime import datetime, time, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Case, Count, Exists, F, IntegerField, OuterRef, Q, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.views.decorators.http import require_POST

from ..forms import FollowUpAssignForm, FollowUpForm, follow_up_leaders
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
from ..services import event_questionnaires as eq
from ..services.metrics import build_payload, coordinator_scope, local_tz, scope_for_access, stale_interviews, sum_rows
from ..services.reports import next_slot
from ..templatetags.emmaus import fecha_corta
from .common import ADMIN, LEADERS, PASTOR, TUTOR, event_view, is_fragment, safe_next


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
        .select_related("participant", "conducted_by", "version")
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
    rows = payload["coordinators"]
    # "Entre todos llevan X de Y…" and the key under the rows count the camperos in the rows (metrics.sum_rows): the
    # event's camperos without a coordinador are in none. The event-wide pages (the pastor's home, "Avance") still count
    # them, and the recommendations say how many have no coordinador.
    return render(
        request,
        "events/supervision/coordinators.html",
        {"rows": rows, "progress": sum_rows(rows), "places": _last_station(access)},
    )


@event_view(*LEADERS)
def coordinator_detail(request, event, access, membership_id):
    coordinator = get_object_or_404(access.coordinators(), pk=membership_id)
    payload = build_payload(coordinator_scope(event, coordinator))
    participants = eq.annotate_questionnaire_progress(access.participants().filter(coordinator=coordinator), event).order_by(
        "interview_status", "room", "full_name"
    )
    return render(
        request,
        "events/supervision/coordinator_detail.html",
        {"coordinator": coordinator, "payload": payload, "progress": payload["progress"], "participants": participants},
    )


PERIODS = [("", "Todo el tiempo"), ("2h", "Últimas 2 horas"), ("hoy", "Hoy")]


@event_view(*LEADERS)
def interviews(request, event, access):
    # Annotated before the "Completadas" filter below: that filter joins the interviews too, and a count taken after
    # it would only see the interviews completed in the period.
    participants = eq.annotate_questionnaire_progress(access.participants().select_related("coordinator__user"), event)
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
            "status_choices": eq.status_choices(event),
            "periods": PERIODS,
            "selected": {"coordinador": coordinator_id, "cuarto": room, "grupo": group, "estado": status, "periodo": period},
            "filters_active": filters_active,
            "querystring": request.GET.urlencode(),
        },
    )


@event_view(TUTOR, PASTOR, ADMIN)
def pending(request, event, access):
    participants = access.participants()
    stale = stale_interviews(participants).select_related("participant__coordinator__user", "conducted_by", "station", "version")
    in_progress = access.interviews().filter(status=Interview.Status.IN_PROGRESS).exclude(pk__in=stale.values("pk"))
    to_evaluate = access.interviews().filter(
        status=Interview.Status.COMPLETED, follow_up_decision=Interview.FollowUpDecision.TO_EVALUATE
    ).select_related("participant__coordinator__user", "version")
    follow_ups = access.follow_ups().filter(status__in=FollowUp.OPEN_STATUSES).order_by("kind", "created_at")
    waiting = participants.filter(interview_status=InterviewStatus.PENDING)
    coordinator_fields = ("coordinator__id", "coordinator__user__first_name", "coordinator__user__last_name")
    links = eq.links_for(event)
    several = len(links) > 1
    if several:
        # Several questionnaires: per coordinador, "n" pendientes with none done (sin iniciar) and "partial" the ones
        # who completed some (a medias); the heading says "Sin terminar". One questionnaire: the table it always was.
        done = Interview.objects.filter(
            participant=OuterRef("pk"),
            status=Interview.Status.COMPLETED,
            questionnaire_id__in=[link.questionnaire_id for link in links],
        )
        not_started_by_coordinator = (
            waiting.annotate(has_done=Exists(done))
            .values(*coordinator_fields)
            .annotate(n=Count("id", filter=Q(has_done=False)), partial=Count("id", filter=Q(has_done=True)))
            .order_by("-n", "-partial")
        )
    else:
        not_started_by_coordinator = waiting.values(*coordinator_fields).annotate(n=Count("id")).order_by("-n")
    return render(
        request,
        "events/supervision/pending.html",
        {
            "stale": stale,
            "in_progress": in_progress,
            "to_evaluate": to_evaluate,
            "follow_ups": follow_ups,
            "not_started_by_coordinator": not_started_by_coordinator,
            "several_questionnaires": several,
        },
    )


# ---- Pastor ----------------------------------------------------------------------------------

# Where each matter of "¿Qué hay que coordinar?" is done: (url name, query, fragment). Every target opens for both
# roles of pastor_home (PASTOR, ADMIN). The link is added here, at render time: recommendations() is frozen into
# every cutoff (ReportSnapshot.data) and printed by the cutoff page and the PDF, which must not gain live links.
# "completo" and any kind not listed render as plain text.
AGENDA_TARGETS = {
    "pendientes": ("events:progress", "", ""),
    "equipo": ("events:progress", "", "equipos"),
    "interrumpidas": ("events:pending", "", "interrumpidas"),
    "sin_coordinador": ("events:participants", "coordinador=ninguno", ""),
    "sin_tutor": ("events:team", "", "sin-tutor"),
    "por_evaluar": ("events:pending", "", "por-evaluar"),
    "seguimientos": ("events:follow_ups", "", ""),
    "autorizaciones": ("events:participants", "", ""),
}


def _agenda(event, recommendations):
    """[{text, evidence, url}] for the pastor summary; url is "" when there is nothing to open."""
    agenda = []
    for item in recommendations:
        url = ""
        target = AGENDA_TARGETS.get(item.get("kind"))
        if target:
            name, query, fragment = target
            url = reverse(name, args=[event.slug]) + (f"?{query}" if query else "") + (f"#{fragment}" if fragment else "")
        agenda.append({"text": item["text"], "evidence": item["evidence"], "url": url})
    return agenda


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
        "agenda": _agenda(event, payload["recommendations"]),
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


FOLLOW_UPS_SHOWN = 200  # a display cap, not a count: the page says "Mostrando 200 de N"
RESPONSABLE_FILTERS = ("todos", "ninguno", "mio")


def _follow_up_status_rank():
    """Work in the order it is attended: nobody started it, under way, then the closed ones."""
    return Case(
        When(status=FollowUp.Status.PENDING, then=Value(0)),
        When(status=FollowUp.Status.IN_PROGRESS, then=Value(1)),
        When(status=FollowUp.Status.DONE, then=Value(2)),
        When(status=FollowUp.Status.CANCELLED, then=Value(3)),
        default=Value(4),
        output_field=IntegerField(),
    )


def _can_assign(access, follow_up) -> bool:
    """``access.can_manage_follow_up`` for a row already taken from ``access.follow_ups()``: the same
    rule without its per-row ``exists()``. Only decides whether the row offers the control; the
    assign endpoint still runs the full check."""
    if follow_up.kind == FollowUp.Kind.SAFETY:
        return access.can_view_sensitive or access.is_admin
    return access.is_admin or access.is_pastor or access.is_tutor or access.can_view_sensitive or (
        access.is_coordinator and follow_up.participant.coordinator_id == getattr(access.membership, "id", None)
    )


@event_view(*LEADERS, MembershipRole.COORDINATOR)
def follow_ups(request, event, access):
    status = request.GET.get("estado", "abiertos")
    responsable = request.GET.get("responsable", "todos")
    if responsable not in RESPONSABLE_FILTERS:
        responsable = "todos"
    in_status = access.follow_ups().select_related("participant__coordinator__user", "assigned_to")
    if status == "abiertos":
        in_status = in_status.filter(status__in=FollowUp.OPEN_STATUSES)
    elif status in FollowUp.Status.values:
        in_status = in_status.filter(status=status)
    unassigned = in_status.filter(assigned_to__isnull=True)
    mine = in_status.filter(assigned_to_id=request.user.id)
    qs = {"ninguno": unassigned, "mio": mine}.get(responsable, in_status)
    total = qs.count()
    qs = qs.annotate(
        status_rank=_follow_up_status_rank(),
        sin_responsable=Case(When(assigned_to__isnull=True, then=Value(1)), default=Value(0), output_field=IntegerField()),
    ).order_by("-kind", "status_rank", "-sin_responsable", "-created_at")
    rows = list(qs[:FOLLOW_UPS_SHOWN])
    leaders = list(follow_up_leaders(event))
    leader_ids = {user.id for user in leaders}
    for f in rows:
        f.can_assign = _can_assign(access, f)
        # The form also accepts the campero's own coordinador; offer them on that row only.
        coordinator = f.participant.coordinator
        f.coordinator_option = coordinator.user if coordinator and coordinator.user_id not in leader_ids else None
        # Someone who left the team stays shown and selected, so sending the row unchanged cannot
        # unassign them by accident; the form still refuses them ("Elige a una persona del equipo.").
        eligible = leader_ids | ({coordinator.user_id} if coordinator else set())
        f.former_assignee = f.assigned_to if f.assigned_to_id and f.assigned_to_id not in eligible else None
    counts = dict(access.follow_ups().values_list("status").annotate(n=Count("id")))
    return render(
        request,
        "events/supervision/follow_ups.html",
        {
            "follow_ups": rows,
            "total": total,
            "shown": FOLLOW_UPS_SHOWN,
            "status": status,
            "responsable": responsable,
            "unassigned_count": unassigned.count(),
            "mine_count": mine.count(),
            "counts": counts,
            "status_choices": FollowUp.Status.choices,
            "leaders": leaders,
        },
    )


@event_view(*LEADERS, MembershipRole.COORDINATOR)
def follow_up_detail(request, event, access, follow_up_id):
    follow_up = get_object_or_404(access.follow_ups(), pk=follow_up_id)
    back = safe_next(request, request.POST.get("siguiente") or request.GET.get("siguiente"), reverse("events:follow_ups", args=[event.slug]))
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
            return redirect(back)
    if can_read and (follow_up.details or follow_up.resolution_note):
        audit.record(request.user, event, "consulta_confidencial", f"Consultó un seguimiento confidencial del campero #{follow_up.participant.code}", follow_up)
    return render(
        request,
        "events/supervision/follow_up_detail.html",
        {"follow_up": follow_up, "form": form, "can_read": can_read, "can_manage": can_manage, "back": back},
    )


@require_POST
@event_view(*LEADERS, MembershipRole.COORDINATOR)
def follow_up_assign(request, event, access, follow_up_id):
    """Who attends a follow-up, set from its row in the list. The guards, audit row and message of
    ``follow_up_detail``; it writes only ``assigned_to``, so it cannot undo a status saved meanwhile."""
    item = get_object_or_404(access.follow_ups(), pk=follow_up_id)
    if not access.can_manage_follow_up(item):
        raise PermissionDenied
    back = safe_next(request, request.POST.get("siguiente"), reverse("events:follow_ups", args=[event.slug]))
    form = FollowUpAssignForm(request.POST, instance=item, event=event)
    if not form.is_valid():
        messages.error(request, "Elige a una persona del equipo.")
        return redirect(back)
    item = form.save(commit=False)
    item.save(update_fields=["assigned_to", "updated_at"])
    audit.record(request.user, event, "seguimiento", f"Actualizó un seguimiento del campero #{item.participant.code}", item, status=item.status)
    messages.success(request, "Seguimiento actualizado.")
    return redirect(back)


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


def _preparation_steps(event, *, progress, unassigned, without_tutor, team_state, roles, stations, has_automatic_cutoff,
                       auto_cutoffs, last_snapshot, failed_count) -> list[dict]:
    """The rows of "Preparación" on the administration home, in the order of the work (audit UI-06, UX-11).

    Each step: ``key``; ``id`` (the row's anchor, or None); ``title``; ``meta`` (its lines, one ``.row__meta`` each);
    ``state`` ("listo", "falta", "revisar", "esperando" or "apagados"); ``attention`` (the row is marked: every step
    that is not "listo", except "Cortes"); and its button: ``url``, ``label`` and ``label_hidden`` (what a screen
    reader hears after the label). The conditions are the ones the template used.

    "Cortes" is one row (it was two): the schedule and the last cutoff, then whether the scheduler runs, said only
    while it has work (``auto_cutoffs``, which also gives the row its id), and the cutoffs that failed. Only a late
    scheduler marks the row; a failed cutoff says "Revisar" without marking it, because the scheduler did run.
    """
    slug = event.slug
    settings_url = reverse("events:settings", args=[slug])

    def step(key, title, meta, state, url, label, label_hidden="", *, attention=None, anchor=None):
        return {
            "key": key, "id": anchor, "title": title, "meta": meta if isinstance(meta, list) else [meta], "state": state,
            "attention": state != "listo" if attention is None else attention,
            "url": url, "label": label, "label_hidden": label_hidden,
        }

    steps = []
    approved = event.procedures_approved
    steps.append(step(
        "procedimientos", "Procedimientos de la iglesia",
        f"Confirmados el {fecha_corta(event.procedures_approved_at)}." if approved else "Sin confirmar. Sin eso no se pueden iniciar entrevistas.",
        "listo" if approved else "falta",
        f"{settings_url}#procedimientos", "Ver" if approved else "Confirmar", "los procedimientos",
    ))

    links = eq.links_for(event)  # one line per questionnaire the event uses, in the order the camperos answer them
    questionnaire_meta = [f"{link.version.title}, versión {link.version.number}." for link in links]
    if len(links) > 1 and event.questionnaires_in_order:
        questionnaire_meta.append("Se responden en orden.")
    steps.append(step(
        "cuestionario", "Cuestionario",
        questionnaire_meta or "Sin cuestionario publicado.",
        "listo" if links else "falta",
        reverse("events:questionnaires", args=[slug]), "Ver", "los cuestionarios",
    ))

    eligible = progress["eligible"]
    if unassigned:
        campers_button = (f"{reverse('events:participants', args=[slug])}?coordinador=ninguno", "Asignar", "coordinador a los camperos que no tienen")
    else:
        campers_button = (reverse("events:participant_import", args=[slug]), "Importar", "camperos")
    steps.append(step(
        "camperos", "Camperos",
        f"{eligible} participando{f', {unassigned} sin coordinador' if unassigned else ''}.",
        "falta" if not eligible else "revisar" if unassigned else "listo",
        *campers_button,
    ))

    counts = []  # "1 administrador, 2 pastores, 2 tutores y 3 coordinadores"
    for value, label in MembershipRole.choices:
        n = roles.get(value, 0)
        counts.append(f"{n} {str(label).lower()}{'' if n == 1 else 'es'}")
    if team_state == "falta":
        team_note = "Sin coordinadores nadie puede entrevistar."
    elif without_tutor:
        team_note = f"{without_tutor} coordinador{'' if without_tutor == 1 else 'es'} sin tutor."
    else:
        team_note = "Cada coordinador tiene tutor."
    if team_state == "revisar":
        team_button = (reverse("events:assignments", args=[slug]), "Asignar tutores", "")
    else:
        team_button = (reverse("events:member_add", args=[slug]), "Agregar persona", "al equipo")
    steps.append(step(
        "equipo", "Equipo", f"{', '.join(counts[:-1])} y {counts[-1]}. {team_note}", team_state, *team_button, anchor="preparacion-equipo",
    ))

    steps.append(step(
        "computadoras", "Computadoras",
        f"{stations} disponible{'' if stations == 1 else 's'} para entrevistar." if stations else "Ninguna disponible para entrevistar.",
        "listo" if stations else "falta",
        f"{settings_url}#computadoras", "Configurar", "las computadoras",
    ))

    if event.reports_enabled:
        schedule = f"{event.get_report_interval_minutes_display()}, de {event.report_window_start:%H:%M} a {event.report_window_end:%H:%M}."
    else:
        schedule = "Los cortes automáticos están apagados."
    last = f"Último corte: {fecha_corta(last_snapshot.cutoff_at)}." if last_snapshot else "Aún no hay cortes."
    late = bool(auto_cutoffs and auto_cutoffs["late"])
    health = []  # the second line: whether the scheduler runs, and the cutoffs that failed
    if auto_cutoffs:
        if late:
            health.append(f"{auto_cutoffs['late_text']} Puedes generar un corte a mano en Reportes.")
        elif auto_cutoffs["last"]:
            health.append(f"Último corte automático: {auto_cutoffs['last']}.")
        else:
            health.append("Todavía no hay cortes automáticos.")
    if failed_count:
        failed = "1 corte no se pudo generar." if failed_count == 1 else f"{failed_count} cortes no se pudieron generar."
        health.append(format_html("<strong>{}</strong>", failed))
    meta = [f"{schedule} {last}"]
    if health:
        meta.append(format_html_join(" ", "{}", ((part,) for part in health)))
    if late or failed_count:
        cutoffs_state = "revisar"
    elif not event.reports_enabled:
        cutoffs_state = "apagados"
    else:
        cutoffs_state = "listo" if has_automatic_cutoff else "esperando"
    reports_url = reverse("events:reports", args=[slug])
    cutoffs_button = (reports_url, "Generar un corte", "en Reportes") if late else (reports_url, "Ver reportes", "")
    steps.append(step(
        "cortes", "Cortes", meta, cutoffs_state, *cutoffs_button, attention=late, anchor="cortes-automaticos" if auto_cutoffs else None,
    ))
    return steps


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
    if not event.has_questionnaire:
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
    # "Preparación": "N de M pasos listos", the pending steps first, the ready ones folded (audit UI-06, LANG-24)
    steps = _preparation_steps(
        event, progress=context["progress"], unassigned=unassigned, without_tutor=without_tutor, team_state=team_state,
        roles=roles, stations=context["stations"], has_automatic_cutoff=context["has_automatic_cutoff"],
        auto_cutoffs=context["auto_cutoffs"], last_snapshot=context["last_snapshot"], failed_count=failed_count,
    )
    pending = [s for s in steps if s["state"] != "listo"]
    ready = [s for s in steps if s["state"] == "listo"]
    context["steps"], context["steps_done"], context["steps_total"] = steps, len(ready), len(steps)
    # Both lists in one loop, so the row is written once in the template; an empty list is left out
    context["step_groups"] = [group for group in ({"listos": False, "steps": pending}, {"listos": True, "steps": ready}) if group["steps"]]
    if is_fragment(request):
        return render(request, "events/admin/_home_live.html", context)
    return render(request, "events/admin/home.html", context)

