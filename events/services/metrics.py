"""Operational metrics. One payload builder feeds live dashboards *and* saved cutoffs.

Definitions (metrics version 1)
-------------------------------
* Participantes: camperos registered in the scope and still taking part (``is_active``).
* Completadas: participantes whose interview is completed. A campero has at most one live
  interview (database constraint) and corrections edit that same interview, so nothing is
  counted twice.
* En curso: participantes whose interview was started and not yet finalized.
* Pendientes: participantes without a completed interview (includes "en curso").
* Sin iniciar: pendientes that have not started.
* Avance: completadas / participantes. With 0 participantes it is "sin datos", never 0 %.
* Ritmo: interviews completed per hour, averaged over the last 2 hours.
* Seguimientos abiertos: care follow-ups with status pendiente or en proceso.
* Por evaluar: completed interviews marked "Seguimiento pendiente de evaluación".
* Interrumpidas: interviews en curso without activity for 20 minutes or more.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Count, Max, OuterRef, Q, Subquery
from django.utils import timezone

from ..models import (
    Answer,
    ConsentRecord,
    Event,
    EventMembership,
    EventParticipant,
    FollowUp,
    Interview,
    InterviewStatus,
    MembershipRole,
    Question,
)
from .questionnaire import all_questions

METRICS_VERSION = "1"
STALE_MINUTES = 20
THROUGHPUT_WINDOW_HOURS = 2

DEFINITIONS = [
    ("Participantes", "Camperos registrados que siguen participando en el evento."),
    ("Completadas", "Camperos con entrevista finalizada. Las correcciones no cuentan como una entrevista nueva."),
    ("En curso", "Entrevistas iniciadas que todavía no se finalizan."),
    ("Pendientes", "Camperos sin entrevista completada (incluye las que están en curso)."),
    ("Avance", "Completadas entre participantes. Si no hay participantes se muestra “sin datos”."),
    ("Ritmo", "Entrevistas completadas por hora, en promedio, durante las últimas 2 horas."),
    ("Seguimientos abiertos", "Seguimientos en estado pendiente o en proceso."),
    ("Por evaluar", "Entrevistas completadas que esperan decidir si necesitan seguimiento."),
    ("Interrumpidas", f"Entrevistas en curso sin actividad desde hace {STALE_MINUTES} minutos o más."),
]


def percent(part: int, whole: int) -> int | None:
    if not whole:
        return None
    return int(round(100 * part / whole))


@dataclass
class Progress:
    eligible: int = 0
    completed: int = 0
    in_progress: int = 0

    @property
    def pending(self) -> int:
        return max(self.eligible - self.completed, 0)

    @property
    def not_started(self) -> int:
        return max(self.eligible - self.completed - self.in_progress, 0)

    @property
    def percent(self) -> int | None:
        return percent(self.completed, self.eligible)

    def as_dict(self) -> dict:
        return {**asdict(self), "pending": self.pending, "not_started": self.not_started, "percent": self.percent}


def progress_for(participants) -> Progress:
    row = participants.aggregate(
        eligible=Count("id"),
        completed=Count("id", filter=Q(interview_status=InterviewStatus.COMPLETED)),
        in_progress=Count("id", filter=Q(interview_status=InterviewStatus.IN_PROGRESS)),
    )
    return Progress(row["eligible"] or 0, row["completed"] or 0, row["in_progress"] or 0)


# ---- scopes (user-independent, so the scheduler can use them) ------------------------------------


@dataclass
class Scope:
    key: str
    label: str
    event: Event
    tutor: EventMembership | None = None
    coordinator: EventMembership | None = None

    def participants(self):
        qs = EventParticipant.objects.filter(event=self.event, is_active=True)
        if self.coordinator is not None:
            return qs.filter(coordinator=self.coordinator)
        if self.tutor is not None:
            return qs.filter(coordinator__tutor_assignment__tutor=self.tutor)
        return qs

    def coordinators(self):
        qs = EventMembership.objects.filter(event=self.event, role=MembershipRole.COORDINATOR).select_related(
            "user", "tutor_assignment__tutor__user"
        )
        if self.coordinator is not None:
            return qs.filter(pk=self.coordinator.pk)
        if self.tutor is not None:
            return qs.filter(tutor_assignment__tutor=self.tutor)
        return qs

    @property
    def is_event(self) -> bool:
        return self.tutor is None and self.coordinator is None


def event_scope(event: Event) -> Scope:
    return Scope("evento", event.all_of_kind, event)


def team_scope(event: Event, tutor: EventMembership) -> Scope:
    return Scope(f"equipo:{tutor.id}", tutor.team_label, event, tutor=tutor)


def coordinator_scope(event: Event, coordinator: EventMembership) -> Scope:
    return Scope(f"coordinador:{coordinator.id}", coordinator.display_name, event, coordinator=coordinator)


def scope_from_key(event: Event, key: str) -> Scope | None:
    if key == "evento":
        return event_scope(event)
    kind, _, raw_id = key.partition(":")
    if not raw_id.isdigit():
        return None
    membership = EventMembership.objects.filter(event=event, pk=int(raw_id)).select_related("user").first()
    if membership is None:
        return None
    if kind == "equipo" and membership.role == MembershipRole.TUTOR:
        return team_scope(event, membership)
    if kind == "coordinador" and membership.role == MembershipRole.COORDINATOR:
        return coordinator_scope(event, membership)
    return None


def scope_for_access(access) -> Scope:
    if access.sees_whole_event:
        return event_scope(access.event)
    if access.is_tutor:
        return team_scope(access.event, access.membership)
    return coordinator_scope(access.event, access.membership)


# ---- building blocks --------------------------------------------------------------------------------


def local_tz(event: Event) -> ZoneInfo:
    try:
        return ZoneInfo(event.time_zone)
    except Exception:  # pragma: no cover - invalid zone saved through admin
        return ZoneInfo("America/Mexico_City")


def completed_interviews(participants):
    return Interview.objects.filter(status=Interview.Status.COMPLETED, participant__in=participants)


def timeline(event: Event, participants, *, now: datetime | None = None) -> dict:
    """Completed interviews per bucket and cumulative, in the event's time zone."""
    now = now or timezone.now()
    tz = local_tz(event)
    stamps = sorted(completed_interviews(participants).filter(completed_at__lte=now).values_list("completed_at", flat=True))
    if not stamps:
        return {"bucket_minutes": 60, "points": []}
    first = stamps[0].astimezone(tz)
    last_local = now.astimezone(tz)
    span_hours = max((last_local - first).total_seconds() / 3600, 1)
    bucket_minutes = 60 if span_hours <= 30 else 120 if span_hours <= 60 else 240
    start = first.replace(minute=0, second=0, microsecond=0)
    if bucket_minutes > 60:
        start = start.replace(hour=start.hour - start.hour % (bucket_minutes // 60))
    buckets = []
    cursor = start
    while cursor <= last_local and len(buckets) < 96:
        buckets.append(cursor)
        cursor += timedelta(minutes=bucket_minutes)
    counts = [0] * len(buckets)
    for stamp in stamps:
        index = int((stamp.astimezone(tz) - start).total_seconds() // (bucket_minutes * 60))
        if 0 <= index < len(counts):
            counts[index] += 1
    cumulative = 0
    points = []
    multi_day = buckets[0].date() != buckets[-1].date()
    for bucket, count in zip(buckets, counts):
        cumulative += count
        label = bucket.strftime("%H:%M")
        points.append(
            {
                "t": bucket.isoformat(),
                "label": label,
                "day": bucket.strftime("%d/%m") if multi_day else "",
                "count": count,
                "cumulative": cumulative,
            }
        )
    return {"bucket_minutes": bucket_minutes, "points": points}


def throughput(participants, *, now: datetime | None = None) -> dict:
    now = now or timezone.now()
    qs = completed_interviews(participants)
    last_hour = qs.filter(completed_at__gt=now - timedelta(hours=1), completed_at__lte=now).count()
    window = qs.filter(completed_at__gt=now - timedelta(hours=THROUGHPUT_WINDOW_HOURS), completed_at__lte=now).count()
    rate = window / THROUGHPUT_WINDOW_HOURS
    return {"last_hour": last_hour, "per_hour": round(rate, 1)}


def eta_minutes(pending: int, per_hour: float) -> int | None:
    if pending <= 0 or per_hour <= 0:
        return None
    return int(math.ceil(pending / per_hour * 60))


def group_progress(participants, field: str) -> dict:
    rows = participants.values(field).annotate(
        eligible=Count("id"),
        completed=Count("id", filter=Q(interview_status=InterviewStatus.COMPLETED)),
        in_progress=Count("id", filter=Q(interview_status=InterviewStatus.IN_PROGRESS)),
    )
    return {row[field]: Progress(row["eligible"], row["completed"], row["in_progress"]) for row in rows}


def stale_interviews(participants, *, now: datetime | None = None):
    now = now or timezone.now()
    return Interview.objects.filter(
        status=Interview.Status.IN_PROGRESS,
        participant__in=participants,
        last_activity_at__lt=now - timedelta(minutes=STALE_MINUTES),
    )


def teams(scope: Scope) -> list[dict]:
    if not scope.is_event:
        return []
    by_tutor = group_progress(scope.participants(), "coordinator__tutor_assignment__tutor")
    tutor_rows = EventMembership.objects.filter(event=scope.event, role=MembershipRole.TUTOR).select_related("user")
    coordinator_counts = dict(
        EventMembership.objects.filter(event=scope.event, role=MembershipRole.COORDINATOR, is_active=True)
        .values_list("tutor_assignment__tutor")
        .annotate(n=Count("id"))
    )
    result = []
    for tutor in tutor_rows:
        progress = by_tutor.get(tutor.id, Progress())
        result.append(
            {
                "key": f"equipo:{tutor.id}",
                "label": tutor.team_label,
                "tutor": tutor.display_name,
                "coordinators": coordinator_counts.get(tutor.id, 0),
                **progress.as_dict(),
            }
        )
    result.sort(key=lambda row: (row["percent"] is None, row["percent"] if row["percent"] is not None else 0, row["label"]))
    unassigned_team = by_tutor.get(None)
    if unassigned_team and unassigned_team.eligible:
        # Camperos whose coordinador has no tutor, or who have no coordinador at all.
        result.append({"key": "sin_equipo", "label": "Sin equipo asignado", "tutor": "", "coordinators": coordinator_counts.get(None, 0), **unassigned_team.as_dict()})
    return result


def coordinators(scope: Scope, *, now: datetime | None = None) -> list[dict]:
    now = now or timezone.now()
    participants = scope.participants()
    by_coordinator = group_progress(participants, "coordinator")
    stale = dict(stale_interviews(participants, now=now).values_list("participant__coordinator").annotate(n=Count("id")))
    last_completed = dict(
        completed_interviews(participants).values_list("participant__coordinator").annotate(last=Max("completed_at"))
    )
    rows = []
    for coordinator in scope.coordinators():
        progress = by_coordinator.get(coordinator.id, Progress())
        assignment = getattr(coordinator, "tutor_assignment", None)
        last = last_completed.get(coordinator.id)
        rows.append(
            {
                "id": coordinator.id,
                "name": coordinator.display_name,
                "team": assignment.tutor.team_label if assignment else "Sin tutor",
                "active": coordinator.is_active,
                "stale": stale.get(coordinator.id, 0),
                "last_completed": last.isoformat() if last else None,
                **progress.as_dict(),
            }
        )
    rows.sort(key=lambda r: (r["percent"] is None, r["percent"] if r["percent"] is not None else 0, r["name"]))
    return rows


def follow_up_summary(scope: Scope, *, include_safety: bool) -> dict:
    participants = EventParticipant.objects.filter(pk__in=scope.participants().values("pk"))
    care = FollowUp.objects.filter(event=scope.event, kind=FollowUp.Kind.CARE, participant__in=participants)
    counts = dict(care.values_list("status").annotate(n=Count("id")))
    to_evaluate = completed_interviews(participants).filter(follow_up_decision=Interview.FollowUpDecision.TO_EVALUATE).count()
    summary = {
        "pending": counts.get(FollowUp.Status.PENDING, 0),
        "in_progress": counts.get(FollowUp.Status.IN_PROGRESS, 0),
        "done": counts.get(FollowUp.Status.DONE, 0),
        "to_evaluate": to_evaluate,
    }
    summary["open_total"] = summary["pending"] + summary["in_progress"]
    summary["requested_total"] = summary["open_total"] + summary["done"]
    summary["safety_open"] = (
        FollowUp.objects.filter(
            event=scope.event, kind=FollowUp.Kind.SAFETY, participant__in=participants, status__in=FollowUp.OPEN_STATUSES
        ).count()
        if include_safety
        else None
    )
    return summary


def missing_guardian_count(scope: Scope) -> int:
    event = scope.event
    if not event.requires_guardian_authorization:
        return 0
    latest_status = Subquery(
        ConsentRecord.objects.filter(participant=OuterRef("pk"), kind=ConsentRecord.Kind.GUARDIAN)
        .order_by("-recorded_at", "-id")
        .values("status")[:1]
    )
    return (
        scope.participants()
        .filter(age__lt=event.adult_age)
        .exclude(interview_status=InterviewStatus.COMPLETED)
        .annotate(guardian_status=latest_status)
        .exclude(guardian_status=ConsentRecord.Status.GRANTED)
        .count()
    )


def attention(scope: Scope, *, now=None) -> dict:
    participants = scope.participants()
    return {
        "stale_interviews": stale_interviews(participants, now=now).count(),
        "unassigned_participants": participants.filter(coordinator__isnull=True).count() if scope.is_event else 0,
        "coordinators_without_tutor": (
            EventMembership.objects.filter(
                event=scope.event, role=MembershipRole.COORDINATOR, is_active=True, tutor_assignment__isnull=True
            ).count()
            if scope.is_event
            else 0
        ),
        "missing_guardian": missing_guardian_count(scope),
    }


# ---- privacy-preserving outcome aggregates -----------------------------------------------------------


def suppress(value: int, k: int) -> dict:
    """Small counts are hidden; larger ones are rounded to a multiple of k.

    Rounding stops someone from comparing two cutoffs (or a total and its parts) to
    work out the answer of one specific person.
    """
    if value <= 0:
        return {"value": 0, "display": "0", "suppressed": False}
    if value < k:
        return {"value": None, "display": f"Menos de {k}", "suppressed": True}
    rounded = int(k * round(value / k))
    return {"value": rounded, "display": f"≈ {rounded}", "suppressed": False}


def outcome_aggregates(event: Event, *, k: int | None = None) -> list[dict]:
    """Event-level counts for choice questions, from completed interviews only."""
    k = k or event.min_group_size
    version = event.questionnaire_version
    if version is None:
        return []
    completed = Interview.objects.filter(event=event, status=Interview.Status.COMPLETED, participant__is_active=True)
    results = []
    for question in all_questions(version):
        if not question.is_choice or question.sensitivity == Question.Sensitivity.GENERAL:
            continue
        answers = Answer.objects.filter(interview__in=completed, question=question)
        respondents = answers.count()
        option_counts = dict(answers.values_list("options__code").annotate(n=Count("id", distinct=True)))
        rows = []
        for option in question.options.all():
            cell = suppress(option_counts.get(option.code, 0), k)
            rows.append({"label": option.label, "code": option.code, "decline": option.is_decline, **cell})
        enough = respondents >= 2 * k
        results.append(
            {
                "code": question.code,
                "title": question.text,
                "kind": question.kind,
                "sensitivity": question.sensitivity,
                "respondents": suppress(respondents, k),
                "rows": rows if enough else [],
                "withheld": not enough,
            }
        )
    return results


# ---- recommendations (deterministic) -------------------------------------------------------------


def format_duration(minutes: int | None) -> str:
    if minutes is None:
        return ""
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours} h {mins} min"
    if hours:
        return f"{hours} h"
    return f"{mins} min"


def recommendations(payload: dict, *, include_names: bool = True) -> list[dict]:
    items = []
    progress = payload["progress"]
    rate = payload["throughput"]["per_hour"]
    if progress["eligible"] and progress["pending"]:
        text = f"Faltan {progress['pending']} entrevistas de {progress['eligible']}."
        evidence = f"{progress['completed']} completadas, {progress['in_progress']} en curso."
        eta = eta_minutes(progress["pending"], rate)
        if eta:
            text += f" Al ritmo actual ({rate:g} por hora) terminarían en unas {format_duration(eta)}."
        items.append({"kind": "pendientes", "text": text, "evidence": evidence})
    elif progress["eligible"]:
        items.append({"kind": "completo", "text": "Todas las entrevistas están completas.", "evidence": f"{progress['completed']} de {progress['eligible']}."})
    overall = progress["percent"]
    if overall is not None:
        behind = [
            team for team in payload.get("teams", [])
            if team["key"] != "sin_equipo" and team["percent"] is not None and team["percent"] <= overall - 15 and team["pending"] >= 5
        ]
        for team in behind[:3]:
            items.append(
                {
                    "kind": "equipo",
                    "text": f"{team['label']} lleva {team['percent']} % (el promedio es {overall} %). Podría necesitar otra computadora o más apoyo.",
                    "evidence": f"{team['pending']} entrevistas pendientes en el equipo.",
                }
            )
    att = payload["attention"]
    if att["stale_interviews"]:
        n = att["stale_interviews"]
        items.append({"kind": "interrumpidas", "text": f"{n} {'entrevista lleva' if n == 1 else 'entrevistas llevan'} más de {STALE_MINUTES} minutos sin actividad. Hay que retomarlas o descartarlas.", "evidence": "Entrevistas en curso sin cambios recientes."})
    if att["unassigned_participants"]:
        n = att["unassigned_participants"]
        items.append({"kind": "sin_coordinador", "text": f"{n} {'campero no tiene' if n == 1 else 'camperos no tienen'} coordinador asignado.", "evidence": "Sin coordinador nadie los puede entrevistar."})
    if att["coordinators_without_tutor"]:
        n = att["coordinators_without_tutor"]
        items.append({"kind": "sin_tutor", "text": f"{n} {'coordinador no tiene' if n == 1 else 'coordinadores no tienen'} tutor.", "evidence": "Nadie revisa su avance."})
    if att["missing_guardian"]:
        n = att["missing_guardian"]
        items.append({"kind": "autorizaciones", "text": f"{n} {'campero menor no tiene' if n == 1 else 'camperos menores no tienen'} autorización registrada y no {'podrá' if n == 1 else 'podrán'} entrevistarse hasta tenerla.", "evidence": "Autorización de padre, madre o tutor legal."})
    fu = payload["follow_ups"]
    if fu["to_evaluate"]:
        n = fu["to_evaluate"]
        items.append({"kind": "por_evaluar", "text": f"{n} {'entrevista espera' if n == 1 else 'entrevistas esperan'} que se decida si necesita{'' if n == 1 else 'n'} seguimiento.", "evidence": "Marcadas como “pendiente de evaluación”."})
    if fu["open_total"]:
        n = fu["open_total"]
        items.append({"kind": "seguimientos", "text": f"{n} {'seguimiento está abierto' if n == 1 else 'seguimientos están abiertos'}. Asigna a un líder para cada uno.", "evidence": f"{fu['pending']} pendientes y {fu['in_progress']} en proceso."})
    return items


# ---- the payload --------------------------------------------------------------------------------


def build_payload(scope: Scope, *, include_safety: bool = False, include_outcomes: bool = False, now: datetime | None = None) -> dict:
    now = now or timezone.now()
    participants = scope.participants()
    progress = progress_for(participants)
    rate = throughput(participants, now=now)
    payload = {
        "metrics_version": METRICS_VERSION,
        "data_as_of": now.isoformat(),
        "scope": {"key": scope.key, "label": scope.label},
        "event": {"name": scope.event.name, "kind": scope.event.get_kind_display()},
        "progress": progress.as_dict(),
        "throughput": {**rate, "eta_minutes": eta_minutes(progress.pending, rate["per_hour"])},
        "timeline": timeline(scope.event, participants, now=now),
        "teams": teams(scope),
        "coordinators": coordinators(scope, now=now) if not scope.coordinator else [],
        "follow_ups": follow_up_summary(scope, include_safety=include_safety),
        "attention": attention(scope, now=now),
        "outcomes": outcome_aggregates(scope.event) if include_outcomes and scope.is_event else [],
        "min_group_size": scope.event.min_group_size,
    }
    payload["recommendations"] = recommendations(payload)
    return payload


def compare(current: dict, previous: dict | None) -> dict:
    if not previous:
        return {}
    keys = ("eligible", "completed", "in_progress", "pending")
    delta = {key: current["progress"][key] - previous["progress"].get(key, 0) for key in keys}
    cur_pct, prev_pct = current["progress"]["percent"], previous["progress"].get("percent")
    delta["percent"] = None if cur_pct is None or prev_pct is None else cur_pct - prev_pct
    delta["follow_ups_open"] = current["follow_ups"]["open_total"] - previous["follow_ups"].get("open_total", 0)
    prev_teams = {t["key"]: t for t in previous.get("teams", [])}
    delta["teams"] = {}
    for team in current.get("teams", []):
        before = prev_teams.get(team["key"])
        if before and team["percent"] is not None and before.get("percent") is not None:
            delta["teams"][team["key"]] = team["percent"] - before["percent"]
    delta["previous_as_of"] = previous.get("data_as_of")
    return delta
