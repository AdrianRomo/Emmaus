"""Cortes: persisted, immutable metric snapshots.

Scheduling: ``python manage.py run_report_cutoffs`` runs every few minutes (cron or the
``scheduler`` compose service). For each active event it finds the most recent slot
(window start + n x interval, within the configured hours) and stores one snapshot per
scope. The unique constraint (event, scope, cutoff, trigger) makes reruns no-ops. If the
server was down, a missed slot is skipped rather than back-filled with later data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import Event, EventMembership, MembershipRole, ReportSnapshot
from . import audit
from .event_questionnaires import links_for
from .metrics import (
    METRICS_VERSION,
    Scope,
    build_payload,
    event_scope,
    keyed_outcomes,
    local_tz,
    present_outcomes,
    scope_from_key,
    team_scope,
)

RELEASED_FOLLOW_UP_KEYS = ("requested_total", "open_total", "done", "to_evaluate")


def release_cells(current, previous, k: int):
    """Release a new value for a cell only if it moved by at least k since the last release.

    Applied cell by cell to nested dicts of counts. Comparing two consecutive releases can
    then never isolate fewer than k people, which is what plain rounding cannot promise.
    """
    if isinstance(current, dict):
        previous = previous if isinstance(previous, dict) else {}
        return {key: release_cells(value, previous.get(key), k) for key, value in current.items()}
    if previous is None or abs(current - previous) >= k:
        return current
    return previous


def _previous_released(event: Event, scope_key: str):
    previous = (
        ReportSnapshot.objects.filter(event=event, scope_key=scope_key, status=ReportSnapshot.Status.READY)
        .order_by("-cutoff_at", "-id")
        .first()
    )
    return (previous.data or {}).get("released") if previous else None

logger = logging.getLogger(__name__)


def slots_for_day(event: Event, day) -> list[datetime]:
    tz = local_tz(event)
    start = datetime.combine(day, event.report_window_start, tzinfo=tz)
    end = datetime.combine(day, event.report_window_end, tzinfo=tz)
    step = timedelta(minutes=event.report_interval_minutes)
    slots = []
    cursor = start
    while cursor <= end:
        slots.append(cursor)
        cursor += step
    return slots


def _event_days_ok(event: Event, day) -> bool:
    if event.starts_on and day < event.starts_on:
        return False
    if event.ends_on and day > event.ends_on:
        return False
    return True


def latest_due_slot(event: Event, now: datetime | None = None) -> datetime | None:
    now = now or timezone.now()
    local_now = now.astimezone(local_tz(event))
    if not _event_days_ok(event, local_now.date()):
        return None
    due = [slot for slot in slots_for_day(event, local_now.date()) if slot <= local_now]
    if not due:
        return None
    slot = due[-1]
    grace = timedelta(minutes=max(10, min(30, event.report_interval_minutes // 2)))
    if local_now - slot > grace:
        return None  # missed slot: do not label later data with an earlier time
    return slot


def next_slot(event: Event, now: datetime | None = None) -> datetime | None:
    if not event.reports_enabled or event.status != Event.Status.ACTIVE:
        return None
    now = now or timezone.now()
    local_now = now.astimezone(local_tz(event))
    for offset in range(0, 8):
        day = local_now.date() + timedelta(days=offset)
        if not _event_days_ok(event, day):
            if event.ends_on and day > event.ends_on:
                return None
            continue
        for slot in slots_for_day(event, day):
            if slot > local_now:
                return slot
    return None


def scopes_for_event(event: Event) -> list[Scope]:
    scopes = [event_scope(event)]
    for tutor in EventMembership.objects.filter(event=event, role=MembershipRole.TUTOR, is_active=True).select_related("user"):
        scopes.append(team_scope(event, tutor))
    return scopes


def generate_snapshot(event: Event, scope: Scope, *, trigger: str, cutoff_at: datetime, user=None, now: datetime | None = None) -> ReportSnapshot:
    existing = ReportSnapshot.objects.filter(event=event, scope_key=scope.key, cutoff_at=cutoff_at, trigger=trigger).first()
    if existing and existing.status == ReportSnapshot.Status.READY:
        return existing
    try:
        payload = build_payload(scope, include_safety=True, include_outcomes=scope.is_event, now=now)
        k = event.min_group_size
        previous = _previous_released(event, scope.key) or {}
        # The counts are keyed by questionnaire; a cutoff saved before that holds them unkeyed. Without this the first
        # cutoff after the change would find no previous cell and release raw counts, breaking the "moved by k" rule.
        previous_outcomes = keyed_outcomes(previous.get("outcomes"), links_for(event)) if previous.get("outcomes") else None
        payload["released"] = {
            "follow_ups": release_cells({key: payload["follow_ups"][key] for key in RELEASED_FOLLOW_UP_KEYS}, previous.get("follow_ups"), k),
            "outcomes": release_cells(payload.pop("outcome_counts", {}), previous_outcomes, k),
        }
    except Exception as exc:  # recorded so administrators see it; never includes answers
        logger.exception("Snapshot generation failed for event %s scope %s", event.pk, scope.key)
        if existing:
            ReportSnapshot.objects.filter(pk=existing.pk).update(error=str(exc)[:500])
            return existing
        return ReportSnapshot.objects.create(
            event=event,
            scope_key=scope.key,
            scope_label=scope.label,
            tutor=scope.tutor,
            trigger=trigger,
            status=ReportSnapshot.Status.FAILED,
            cutoff_at=cutoff_at,
            interval_minutes=event.report_interval_minutes,
            metrics_version=METRICS_VERSION,
            error=str(exc)[:500],
            created_by=user,
        )
    try:
        with transaction.atomic():
            if existing:  # a failed attempt for this slot: replace it with the good one
                ReportSnapshot.objects.filter(pk=existing.pk, status=ReportSnapshot.Status.FAILED).delete()
            snapshot = ReportSnapshot.objects.create(
                event=event,
                scope_key=scope.key,
                scope_label=scope.label,
                tutor=scope.tutor,
                trigger=trigger,
                status=ReportSnapshot.Status.READY,
                cutoff_at=cutoff_at,
                interval_minutes=event.report_interval_minutes,
                metrics_version=METRICS_VERSION,
                data=payload,
                created_by=user,
            )
    except IntegrityError:
        # A concurrent run stored the same cutoff first.
        return ReportSnapshot.objects.get(event=event, scope_key=scope.key, cutoff_at=cutoff_at, trigger=trigger)
    return snapshot


def run_due_cutoffs(now: datetime | None = None) -> list[ReportSnapshot]:
    now = now or timezone.now()
    created = []
    for event in Event.objects.filter(status=Event.Status.ACTIVE, reports_enabled=True):
        slot = latest_due_slot(event, now)
        if slot is None:
            continue
        for scope in scopes_for_event(event):
            created.append(generate_snapshot(event, scope, trigger=ReportSnapshot.Trigger.AUTOMATIC, cutoff_at=slot))
    return created


def manual_cutoff(access, scope_key: str) -> ReportSnapshot:
    allowed = {scope["key"] for scope in access.report_scopes()}
    if scope_key not in allowed:
        raise PermissionError("No puedes generar cortes de ese alcance.")
    scope = scope_from_key(access.event, scope_key)
    if scope is None:
        raise PermissionError("Ese alcance ya no existe.")
    now = timezone.now()
    with transaction.atomic():
        # Serialize manual cutoffs per event so a double click returns the first one.
        Event.objects.select_for_update().filter(pk=access.event.pk).first()
        recent = (
            ReportSnapshot.objects.filter(
                event=access.event, scope_key=scope.key, trigger=ReportSnapshot.Trigger.MANUAL,
                status=ReportSnapshot.Status.READY, created_at__gte=now - timedelta(seconds=60),
            )
            .order_by("-created_at")
            .first()
        )
        if recent:
            return recent
        snapshot = generate_snapshot(
            access.event, scope, trigger=ReportSnapshot.Trigger.MANUAL, cutoff_at=now.replace(microsecond=0), user=access.user
        )
    audit.record(access.user, access.event, "corte_generado", f"Generó un corte manual: {scope.label}", snapshot)
    return snapshot


def previous_snapshot(snapshot: ReportSnapshot) -> ReportSnapshot | None:
    return (
        ReportSnapshot.objects.filter(
            event=snapshot.event,
            scope_key=snapshot.scope_key,
            status=ReportSnapshot.Status.READY,
            cutoff_at__lt=snapshot.cutoff_at,
        )
        .order_by("-cutoff_at", "-id")
        .first()
    )


def visible_payload(payload: dict, access) -> dict:
    """Trim a stored payload to what the *current* viewer may see (never what they saw before)."""
    data = dict(payload)
    released = data.pop("released", None) or {}
    data.pop("outcome_counts", None)
    if not (access.is_admin or access.can_view_sensitive):
        data["follow_ups"] = {**data.get("follow_ups", {}), "safety_open": None}
    if not access.can_view_sensitive:
        data["follow_ups"] = {**data.get("follow_ups", {}), "confidential_open": None}
    data["outcomes"] = present_outcomes(access.event, released.get("outcomes")) if access.can_see_aggregate_outcomes else []
    if access.is_tutor:
        allowed = set(access.coordinators().values_list("id", flat=True))
        before = data.get("coordinators", [])
        data["coordinators"] = [row for row in before if row.get("id") in allowed]
        data["coordinators_hidden"] = len(before) - len(data["coordinators"])
    return data


def released_follow_ups(snapshot: ReportSnapshot | None) -> dict | None:
    if snapshot is None:
        return None
    return (snapshot.data or {}).get("released", {}).get("follow_ups")


def latest_snapshot(event: Event, scope_key: str) -> ReportSnapshot | None:
    return (
        ReportSnapshot.objects.filter(event=event, scope_key=scope_key, status=ReportSnapshot.Status.READY)
        .order_by("-cutoff_at", "-id")
        .first()
    )


# ---- the history list ------------------------------------------------------------------------------------
MIN_RUN = 3  # consecutive unchanged cutoffs before they fold into one row


def _run_key(snapshot):
    """What makes two cutoffs "the same" in the history: both ready, with the same completed and pending counts and,
    with several questionnaires, the same count completed of each (a cutoff saved before has none: an empty tuple)."""
    if snapshot.status != ReportSnapshot.Status.READY:
        return None
    progress = (snapshot.data or {}).get("progress") or {}
    return (progress.get("completed"), progress.get("pending"), tuple(q.get("completed") for q in (progress.get("questionnaires") or [])))


def group_history(snapshots, *, min_run: int = MIN_RUN) -> list[dict]:
    """The history (newest first) with its quiet stretches folded: ``min_run`` or more consecutive ready cutoffs whose
    counts did not change (``_run_key``) become one row, so a night of identical cutoffs does not push the ones
    that matter off the screen. The newest cutoff always stands on its own (it is the current state), and a failed
    cutoff keeps its own row and ends a run. Rows are ``{"kind": "corte", "snapshot": s}`` or
    ``{"kind": "run", "snapshots": [newest, ...], "count": n, "newest": s, "oldest": s}``."""
    items = list(snapshots)
    rows: list[dict] = []
    i = 0
    while i < len(items):
        key = _run_key(items[i])
        j = i
        if key is not None and i > 0:
            while j + 1 < len(items) and _run_key(items[j + 1]) == key:
                j += 1
        if j - i + 1 >= min_run:
            run = items[i : j + 1]
            rows.append({"kind": "run", "snapshots": run, "count": len(run), "newest": run[0], "oldest": run[-1]})
            i = j + 1
        else:
            rows.append({"kind": "corte", "snapshot": items[i]})
            i += 1
    return rows
