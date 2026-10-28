"""Operational metrics. One payload builder feeds live dashboards *and* saved cutoffs.

Definitions (metrics version 2)
-------------------------------
An event may use several questionnaires (``EventQuestionnaire``); a campero's "interview" is all of them.
The counts of camperos come from ``EventParticipant.interview_status``, which
``services.event_questionnaires`` derives from the campero's interviews.

* Camperos: camperos registered in the scope and still taking part (``is_active``).
* Completadas: camperos who completed the interview, that is every questionnaire of the event.
  Corrections edit the same interview, so nothing is counted twice.
* En curso: camperos with an interview started and not yet finalized (at most one at a time).
* Pendientes: camperos who have not completed every questionnaire yet (includes "en curso").
* A medias (``partial``): with several questionnaires, pendientes without an interview in progress who
  completed at least one of the event's questionnaires. Always 0 with one questionnaire.
* Sin iniciar: pendientes without an interview in progress and not "a medias".
* Por cuestionario (``questionnaires``): with several questionnaires, one row per questionnaire of the event
  in its order: the camperos who completed it and the ones answering it now. Empty with one questionnaire.
* Avance: completadas / camperos. With 0 camperos it is None, shown as "—", never 0 %.
* Ritmo: camperos who complete the interview per hour, averaged over the last 2 hours. A campero
  completes it when their last questionnaire is finalized (``campero_completions``), so the rate and
  the timeline are in the same unit as Pendientes.
* Seguimientos abiertos: care follow-ups with status pendiente or en proceso.
* Por decidir: completed interviews (one per questionnaire) whose follow-up decision is "Por decidir"
  (``por_evaluar``).
* Interrumpidas: interviews en curso without changes for 20 minutes or more.
* Outcomes: counted per questionnaire of the event, keyed by questionnaire id (question codes repeat
  between questionnaires). Cutoffs saved with version 1 hold their only questionnaire's counts unkeyed.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Count, Exists, Max, OuterRef, Q, Subquery
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
from .event_questionnaires import links_for
from .questionnaire import all_questions

METRICS_VERSION = "2"
STALE_MINUTES = 20
THROUGHPUT_WINDOW_HOURS = 2
SHORT_MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def short_day(moment) -> str:
    """'25 oct', like ``fecha_corta``. Charts use the bucket's own wall clock (already in the event's time zone)."""
    return f"{moment.day} {SHORT_MONTHS[moment.month - 1]}"

DEFINITIONS = [
    ("Camperos", "Camperos registrados que siguen participando en el evento."),
    ("Completadas", "Camperos que completaron la entrevista (todos los cuestionarios del evento). Las correcciones no cuentan como una entrevista nueva."),
    ("En curso", "Entrevistas iniciadas que todavía no se finalizan."),
    ("Pendientes", "Camperos que aún no completan todos los cuestionarios, incluidos los que tienen una entrevista en curso."),
    ("Avance", "Completadas entre camperos. Si no hay camperos no se calcula y se muestra “—”, nunca 0\u00a0%."),
    ("Ritmo", "Camperos que completan la entrevista por hora, en promedio, durante las últimas 2 horas."),
    ("Seguimientos abiertos", "Seguimientos en estado pendiente o en proceso."),
    ("Por decidir", "Entrevistas completadas que esperan decidir si necesitan seguimiento."),
    ("Interrumpidas", f"Entrevistas en curso sin cambios desde hace {STALE_MINUTES} minutos o más."),
]
# Printed after DEFINITIONS only where the numbers exist: a cutoff of an event with several questionnaires (its progress
# has "questionnaires" rows). With one questionnaire the glossary stays the one it always was.
SEVERAL_QUESTIONNAIRE_DEFINITIONS = [
    ("A medias", "Camperos que completaron algún cuestionario del evento, pero no todos, y no tienen una entrevista en curso."),
    ("Por cuestionario", "Camperos que completaron cada cuestionario del evento. Un campero cuenta en cada cuestionario que terminó."),
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
    # With several questionnaires (both stay 0 / [] with one): pendientes who completed some questionnaire and have
    # none live ("a medias"), and one row per questionnaire of the event in its order (see _questionnaire_rows).
    partial: int = 0
    questionnaires: list = field(default_factory=list)

    @property
    def pending(self) -> int:
        return max(self.eligible - self.completed, 0)

    @property
    def not_started(self) -> int:
        return max(self.eligible - self.completed - self.in_progress - self.partial, 0)

    @property
    def percent(self) -> int | None:
        return percent(self.completed, self.eligible)

    def as_dict(self) -> dict:
        return {**asdict(self), "pending": self.pending, "not_started": self.not_started, "percent": self.percent}


def _several_links(event: Event | None) -> list:
    """The event's links in order when it uses two or more questionnaires; ``[]`` otherwise (or without an event)."""
    if event is None:
        return []
    links = links_for(event)
    return links if len(links) > 1 else []


def _halfway(participants, linked_ids):
    """Pendientes who completed at least one of ``linked_ids``: "a medias" (some done, none live)."""
    done = Interview.objects.filter(participant=OuterRef("pk"), status=Interview.Status.COMPLETED, questionnaire_id__in=linked_ids)
    return participants.filter(interview_status=InterviewStatus.PENDING).filter(Exists(done))


def _questionnaire_counts(participants, linked_ids, *extra):
    """Distinct camperos per questionnaire and interview status (completed or in progress), grouped also by ``extra``."""
    return (
        Interview.objects.filter(
            participant__in=participants,
            questionnaire_id__in=linked_ids,
            status__in=[Interview.Status.COMPLETED, Interview.Status.IN_PROGRESS],
        )
        .values(*extra, "questionnaire_id", "status")
        .annotate(n=Count("participant_id", distinct=True))
    )


def _questionnaire_rows(links, eligible: int, counts: dict) -> list[dict]:
    """One row per link, in order. ``counts``: ``{(questionnaire_id, status): camperos}``."""
    rows = []
    for link in links:
        completed = counts.get((link.questionnaire_id, Interview.Status.COMPLETED), 0)
        rows.append(
            {
                "id": link.questionnaire_id,
                "title": link.version.title,  # the title every other screen uses for a questionnaire
                "order": link.order,
                "eligible": eligible,
                "completed": completed,
                "in_progress": counts.get((link.questionnaire_id, Interview.Status.IN_PROGRESS), 0),
                "percent": percent(completed, eligible),
            }
        )
    return rows


def progress_for(participants, event: Event | None = None) -> Progress:
    """The scope's counts. With ``event`` and two or more questionnaires, also ``partial`` and ``questionnaires``
    (three more queries: the links, the "a medias" count, the counts per questionnaire)."""
    row = participants.aggregate(
        eligible=Count("id"),
        completed=Count("id", filter=Q(interview_status=InterviewStatus.COMPLETED)),
        in_progress=Count("id", filter=Q(interview_status=InterviewStatus.IN_PROGRESS)),
    )
    progress = Progress(row["eligible"] or 0, row["completed"] or 0, row["in_progress"] or 0)
    links = _several_links(event)
    if links:
        linked_ids = [link.questionnaire_id for link in links]
        progress.partial = _halfway(participants, linked_ids).count()
        counts = {(r["questionnaire_id"], r["status"]): r["n"] for r in _questionnaire_counts(participants, linked_ids)}
        progress.questionnaires = _questionnaire_rows(links, progress.eligible, counts)
    return progress


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
    if not (raw_id.isascii() and raw_id.isdigit()):  # "²".isdigit() is True and int("²") raises
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
    """Completed interview ROWS: one per questionnaire answered. For "who finished", see ``campero_completions``."""
    return Interview.objects.filter(status=Interview.Status.COMPLETED, participant__in=participants)


def campero_completions(participants):
    """The camperos who completed the interview, each with ``finished_at``: when their last completed
    questionnaire was finalized. Counts the same people as ``Progress.completed`` (``interview_status``), so
    the timeline cannot climb above the camperos and the rate is in camperos per hour. With one
    questionnaire per event it is the completed interviews, one per campero."""
    return (
        participants.filter(interview_status=InterviewStatus.COMPLETED)
        .annotate(finished_at=Max("interviews__completed_at", filter=Q(interviews__status=Interview.Status.COMPLETED)))
        .filter(finished_at__isnull=False)
    )


def timeline(event: Event, participants, *, now: datetime | None = None) -> dict:
    """Camperos who completed the interview per bucket and cumulative, in the event's time zone."""
    now = now or timezone.now()
    tz = local_tz(event)
    stamps = sorted(campero_completions(participants).filter(finished_at__lte=now).values_list("finished_at", flat=True))
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
                "day": short_day(bucket) if multi_day else "",  # cutoffs saved before held "25/10": see emmaus.chart_day
                "count": count,
                "cumulative": cumulative,
            }
        )
    return {"bucket_minutes": bucket_minutes, "points": points}


def throughput(participants, *, now: datetime | None = None) -> dict:
    now = now or timezone.now()
    qs = campero_completions(participants)
    last_hour = qs.filter(finished_at__gt=now - timedelta(hours=1), finished_at__lte=now).count()
    window = qs.filter(finished_at__gt=now - timedelta(hours=THROUGHPUT_WINDOW_HOURS), finished_at__lte=now).count()
    rate = window / THROUGHPUT_WINDOW_HOURS
    return {"last_hour": last_hour, "per_hour": round(rate, 1)}


def eta_minutes(pending: int, per_hour: float) -> int | None:
    if pending <= 0 or per_hour <= 0:
        return None
    return int(math.ceil(pending / per_hour * 60))


class GroupProgress(dict):
    """``group_progress``'s result: ``{group: Progress}``. ``of(group)`` is that group's Progress, or for a group without
    camperos an empty one that still has a row per questionnaire when the event uses several."""

    def __init__(self, groups: dict, links: list):
        super().__init__(groups)
        self.links = links

    def of(self, key) -> Progress:
        found = self.get(key)
        if found is not None:
            return found
        return Progress(questionnaires=_questionnaire_rows(self.links, 0, {}))


def group_progress(participants, field: str, event: Event | None = None) -> GroupProgress:
    """``progress_for`` per value of ``field``; with ``event`` and several questionnaires each group also carries
    ``partial`` and ``questionnaires`` (three more queries for every group together)."""
    rows = participants.values(field).annotate(
        eligible=Count("id"),
        completed=Count("id", filter=Q(interview_status=InterviewStatus.COMPLETED)),
        in_progress=Count("id", filter=Q(interview_status=InterviewStatus.IN_PROGRESS)),
    )
    groups = {row[field]: Progress(row["eligible"], row["completed"], row["in_progress"]) for row in rows}
    links = _several_links(event)
    if links:
        linked_ids = [link.questionnaire_id for link in links]
        partial = dict(_halfway(participants, linked_ids).values_list(field).annotate(n=Count("id")))
        counts: dict = {}
        for row in _questionnaire_counts(participants, linked_ids, f"participant__{field}"):
            counts.setdefault(row[f"participant__{field}"], {})[(row["questionnaire_id"], row["status"])] = row["n"]
        for key, progress in groups.items():
            progress.partial = partial.get(key, 0)
            progress.questionnaires = _questionnaire_rows(links, progress.eligible, counts.get(key, {}))
    return GroupProgress(groups, links)


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
    by_tutor = group_progress(scope.participants(), "coordinator__tutor_assignment__tutor", scope.event)
    tutor_rows = EventMembership.objects.filter(event=scope.event, role=MembershipRole.TUTOR).select_related("user")
    coordinator_counts = dict(
        EventMembership.objects.filter(event=scope.event, role=MembershipRole.COORDINATOR, is_active=True)
        .values_list("tutor_assignment__tutor")
        .annotate(n=Count("id"))
    )
    result = []
    for tutor in tutor_rows:
        progress = by_tutor.of(tutor.id)
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
    by_coordinator = group_progress(participants, "coordinator", scope.event)
    stale = dict(stale_interviews(participants, now=now).values_list("participant__coordinator").annotate(n=Count("id")))
    last_completed = dict(
        completed_interviews(participants).values_list("participant__coordinator").annotate(last=Max("completed_at"))
    )
    rows = []
    for coordinator in scope.coordinators():
        progress = by_coordinator.of(coordinator.id)
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
    care = FollowUp.objects.filter(event=scope.event, kind=FollowUp.Kind.CARE, participant__in=participants, confidential=False)
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
    summary["confidential_open"] = (
        FollowUp.objects.filter(
            event=scope.event, kind=FollowUp.Kind.CARE, confidential=True, participant__in=participants, status__in=FollowUp.OPEN_STATUSES
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


def suppress(value: int | None, k: int, *, hide_zero: bool = False) -> dict:
    """Small counts are hidden; larger ones are rounded to a multiple of k.

    With ``hide_zero`` an empty cell looks the same as a small one, so a column of
    zeros cannot reveal that everyone gave the same answer.
    """
    if value is None:
        return {"value": None, "display": "—", "suppressed": True}
    if value <= 0 and not hide_zero:
        return {"value": 0, "display": "0", "suppressed": False}
    if value < k:
        return {"value": None, "display": f"Menos de {k}", "suppressed": True}
    rounded = int(k * round(value / k))
    return {"value": rounded, "display": f"unas {rounded}", "suppressed": False}  # words: Figtree has no "≈"


def outcome_counts(event: Event) -> dict:
    """Raw counts per choice question of each questionnaire the event uses (completed interviews only):
    ``{str(questionnaire_id): {question.code: {"respondents": n, "options": {option.code: n}}}}``. Keyed by
    questionnaire because two questionnaires may share question codes. No questionnaire: ``{}``.

    Never rendered directly: snapshots store a *released* copy (see reports.release_cells)
    and templates show it through ``present_outcomes``.
    """
    result = {}
    for link in links_for(event):
        completed = Interview.objects.filter(
            event=event, status=Interview.Status.COMPLETED, participant__is_active=True, questionnaire_id=link.questionnaire_id
        )
        counts = {}
        for question in all_questions(link.version):
            if not question.is_choice or question.sensitivity == Question.Sensitivity.GENERAL:
                continue
            answers = Answer.objects.filter(interview__in=completed, question=question)
            option_counts = dict(answers.values_list("options__code").annotate(n=Count("id", distinct=True)))
            counts[question.code] = {
                "respondents": answers.count(),
                "options": {option.code: option_counts.get(option.code, 0) for option in question.options.all()},
            }
        result[str(link.questionnaire_id)] = counts
    return result


def _is_flat_outcomes(released: dict) -> bool:
    """A cutoff saved before events had several questionnaires: ``{question.code: {"respondents": n, ...}}``.
    In the keyed shape the value under a questionnaire id is a dict of questions, never a count."""
    return any(isinstance(value, dict) and isinstance(value.get("respondents"), int) for value in released.values())


def _flat_outcomes_link(released: dict, links):
    """The link an unkeyed cutoff counted. It was saved while the event had one questionnaire, whose link the
    migration created: the oldest (lowest id), never the first in order, which "Subir" changes. Among the links
    whose questions include every code of the cutoff, the oldest; if none has them all, the oldest link."""
    if not links:
        return None
    codes = set(released)
    having = [link for link in links if codes <= {question.code for question in all_questions(link.version)}]
    return min(having or links, key=lambda link: link.id)


def keyed_outcomes(released: dict | None, links) -> dict:
    """Released outcome counts keyed by questionnaire id, whatever shape they were saved in. The unkeyed shape
    of older cutoffs counted the event's only questionnaire (see ``_flat_outcomes_link``)."""
    if not released:
        return {}
    if _is_flat_outcomes(released):
        link = _flat_outcomes_link(released, links)
        return {str(link.questionnaire_id): released} if link else {}
    return released


def present_outcomes(event: Event, released: dict | None, *, k: int | None = None) -> list[dict]:
    """Suppressed, rounded rows for display, from released counts: one flat list, the event's questionnaires in
    order. Each row says its questionnaire (``questionnaire_id``, ``questionnaire``: the version's title) and
    ``show_questionnaire`` (the event uses more than one, so the screens put a heading over each)."""
    k = k or event.min_group_size
    if not released:
        return []
    links = links_for(event)
    by_questionnaire = keyed_outcomes(released, links)
    several = len(links) > 1
    results = []
    for link in links:
        counts_by_code = by_questionnaire.get(str(link.questionnaire_id))
        if not counts_by_code:
            continue
        for question in all_questions(link.version):
            counts = counts_by_code.get(question.code)
            if counts is None:
                continue
            respondents = counts.get("respondents", 0)
            rows = [
                {"label": option.label, "code": option.code, "decline": option.is_decline,
                 **suppress(counts["options"].get(option.code, 0), k, hide_zero=True)}
                for option in question.options.all()
            ]
            enough = respondents >= 2 * k
            results.append(
                {
                    "code": question.code,
                    "title": question.text,
                    "kind": question.kind,
                    "sensitivity": question.sensitivity,
                    "respondents": suppress(respondents, k, hide_zero=True),
                    "rows": rows if enough else [],
                    "withheld": not enough,
                    "questionnaire_id": link.questionnaire_id,
                    "questionnaire": link.version.title,
                    "show_questionnaire": several,
                }
            )
    return results


# ---- recommendations (deterministic) -------------------------------------------------------------


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def format_duration(minutes: int | None) -> str:
    """'1 hora 20 minutos', '2 horas', '45 minutos': words, never 'h' or 'min'.

    Callers write "alrededor de" before it: "unas" would not agree with "1 hora" or "45 minutos".
    """
    if minutes is None:
        return ""
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{_count(hours, 'hora', 'horas')} {_count(mins, 'minuto', 'minutos')}"
    if hours:
        return _count(hours, "hora", "horas")
    return _count(mins, "minuto", "minutos")


def recommendations(payload: dict, *, include_names: bool = True) -> list[dict]:
    items = []
    progress = payload["progress"]
    rate = payload["throughput"]["per_hour"]
    if progress["eligible"] and progress["pending"]:
        pending = progress["pending"]
        text = f"Falta 1 entrevista de {progress['eligible']}." if pending == 1 else f"Faltan {pending} entrevistas de {progress['eligible']}."
        evidence = f"{progress['completed']} completadas, {progress['in_progress']} en curso."
        eta = eta_minutes(progress["pending"], rate)
        if eta:
            text += f" Al ritmo actual, {rate:g} por hora, terminarían en alrededor de {format_duration(eta)}."
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
                    "text": f"{team['label']} lleva {team['percent']}\u00a0% y el promedio es {overall}\u00a0%. Podría necesitar otra computadora o más apoyo.",
                    "evidence": f"{team['pending']} entrevistas pendientes en el equipo.",
                }
            )
    att = payload["attention"]
    if att["stale_interviews"]:
        n = att["stale_interviews"]
        text = (
            f"1 entrevista está interrumpida: lleva más de {STALE_MINUTES} minutos sin cambios. Hay que retomarla o descartarla."
            if n == 1
            else f"{n} entrevistas están interrumpidas: llevan más de {STALE_MINUTES} minutos sin cambios. Hay que retomarlas o descartarlas."
        )
        items.append({"kind": "interrumpidas", "text": text, "evidence": "Entrevistas en curso sin cambios recientes."})
    if att["unassigned_participants"]:
        n = att["unassigned_participants"]
        items.append({"kind": "sin_coordinador", "text": f"{n} {'campero no tiene' if n == 1 else 'camperos no tienen'} coordinador asignado.", "evidence": "Sin coordinador nadie los puede entrevistar."})
    if att["coordinators_without_tutor"]:
        n = att["coordinators_without_tutor"]
        items.append({"kind": "sin_tutor", "text": f"{n} {'coordinador no tiene' if n == 1 else 'coordinadores no tienen'} tutor.", "evidence": "Nadie revisa su avance."})
    if att["missing_guardian"]:
        n = att["missing_guardian"]
        items.append({"kind": "autorizaciones", "text": f"{n} {'campero menor no tiene' if n == 1 else 'camperos menores no tienen'} autorización registrada. Su coordinador deberá confirmar el formato firmado antes de la entrevista.", "evidence": "Autorización de padre, madre o tutor legal."})
    fu = payload["follow_ups"]
    if fu["to_evaluate"]:
        n = fu["to_evaluate"]
        items.append({"kind": "por_evaluar", "text": f"{n} {'entrevista espera' if n == 1 else 'entrevistas esperan'} que se decida si necesita{'' if n == 1 else 'n'} seguimiento.", "evidence": "Marcadas como “Por decidir”."})
    if fu["open_total"]:
        n = fu["open_total"]
        items.append({"kind": "seguimientos", "text": f"{n} {'seguimiento está abierto' if n == 1 else 'seguimientos están abiertos'}. Revisa que cada uno tenga responsable.", "evidence": f"{fu['pending']} pendientes y {fu['in_progress']} en proceso."})
    return items


# ---- the payload --------------------------------------------------------------------------------


def build_payload(scope: Scope, *, include_safety: bool = False, include_outcomes: bool = False, now: datetime | None = None) -> dict:
    now = now or timezone.now()
    participants = scope.participants()
    progress = progress_for(participants, scope.event)
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
        "outcome_counts": outcome_counts(scope.event) if include_outcomes and scope.is_event else {},
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
    # Both ends, so the sentence says "pasó de 51 % a 77 %": a bare "+26 %" reads as 26 % more.
    delta["percent_before"], delta["percent_now"] = prev_pct, cur_pct
    delta["follow_ups_open"] = current["follow_ups"]["open_total"] - previous["follow_ups"].get("open_total", 0)
    prev_teams = {t["key"]: t for t in previous.get("teams", [])}
    delta["teams"] = {}
    for team in current.get("teams", []):
        before = prev_teams.get(team["key"])
        if before and team["percent"] is not None and before.get("percent") is not None:
            delta["teams"][team["key"]] = team["percent"] - before["percent"]
    delta["previous_as_of"] = previous.get("data_as_of")
    return delta


def _avance_moved(delta: dict) -> str:
    """'el avance pasó de 51 % a 77 %'. The difference is percentage points, so it is never said alone
    as "+26 %"; a delta without both ends (written by hand, not by ``compare``) says the points."""
    before, now = delta.get("percent_before"), delta.get("percent_now")
    if before is not None and now is not None:
        return f"el avance pasó de {before}\u00a0% a {now}\u00a0%"
    points = delta.get("percent") or 0
    unit = "punto porcentual" if abs(points) == 1 else "puntos porcentuales"
    return f"el avance {'subió' if points > 0 else 'bajó'} {abs(points)} {unit}"


def _camperos_moved(camperos: int) -> str:
    if camperos > 0:
        return "se registró 1 campero más" if camperos == 1 else f"se registraron {camperos} camperos más"
    return "hay 1 campero menos" if camperos == -1 else f"hay {-camperos} camperos menos"


def delta_sentence(delta: dict, since: datetime | None = None) -> str:
    """The change since the previous cutoff, said the same way on every screen and in the PDF.

    ``delta`` is what ``compare`` returns. Counts are said as words ("25 entrevistas más", never "+25") and
    the progress as both ends ("pasó de 51 % a 77 %"), never as a signed percentage. When nobody finished
    an interview, camperos registered (or removed) and the percentage they move are still said, never
    "Sin cambios". With ``since`` (the previous cutoff) the sentence names its time; without it, it says
    "el corte anterior".
    """
    anchor = f"el corte de las {timezone.localtime(since):%H:%M}" if since else "el corte anterior"
    completed = delta.get("completed") or 0
    percent = delta.get("percent") or 0
    camperos = delta.get("eligible") or 0
    if completed:
        noun = "entrevista" if abs(completed) == 1 else "entrevistas"
        sentence = f"{abs(completed)} {noun} {'más' if completed > 0 else 'menos'} que en {anchor}."
        if percent:
            moved = _avance_moved(delta)
            sentence += f" {moved[0].upper()}{moved[1:]}."
        return sentence
    if percent and camperos:
        return f"Ninguna entrevista nueva desde {anchor}; {_avance_moved(delta)} porque {_camperos_moved(camperos)}."
    if percent:
        return f"Ninguna entrevista nueva desde {anchor}; {_avance_moved(delta)}."
    if camperos:
        return f"Ninguna entrevista nueva desde {anchor}, pero {_camperos_moved(camperos)}."
    return f"Sin cambios desde {anchor}."
