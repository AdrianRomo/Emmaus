"""The scheduled jobs: their names, their heartbeat and whether each one is running (issue #80).

``run_scheduled_jobs`` (the ``scheduler`` service) runs every job on each pass through ``run_job``,
which writes the job's heartbeat (``SchedulerHeartbeat``) and one JSON line on ``emmaus.scheduler``::

    {"job": "avisos_correo", "ok": true, "ms": 12, "notices": 1, "sent": 1, "failed": 0, "first_failed": 0, "left": 0, ...}
    {"job": "avisos_correo", "ok": false, "ms": 3, "exception": "OperationalError"}
    {"job": "avisos_correo", "ok": false, "ms": 40, "exception": "AllSendsFailed", "sent": 0, "failed": 3, ...}

Counts and a class name only: never an address, a name, a title or an error message. The same holds
for what a failing community job writes to the ordinary log: its class and its stack, not its message.
A job is not "ok" when it raised, and also when its own counts say it did nothing useful
(``AllSendsFailed``: every e-mail was refused; ``EventsFailed``: a waiting list could not be moved;
``SeriesFailed``: the dates of a series could not be written; ``AllSendsFailed`` also for the e-mails of
an event's subscribers, ``agenda.subscriptions``).

Who reads the heartbeat: ``manage.py scheduler_status`` (for cron or a monitor on the host) and the
notice screens (``notice_mail_problem``), which must not promise an e-mail on a later day while
nothing is sending them.
"""

from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone

from core.models import SchedulerHeartbeat
from core.request_log import dumps

logger = logging.getLogger("emmaus.scheduler")
log = logging.getLogger(__name__)

CUTOFFS = "cortes"
NOTICE_MAIL = "avisos_correo"
WAITING_OFFERS = "lista_espera"
SERIES = "series_eventos"
PERSON_MERGES = "uniones_personas"
FORM_ANSWERS = "respuestas_formularios"
SUBSCRIPTIONS = "suscripciones_evento"
SERVING_REMINDERS = "recordatorios_servir"
PRAYER_RETENTION = "oracion_retencion"
JOBS = (CUTOFFS, NOTICE_MAIL, WAITING_OFFERS, SERIES, PERSON_MERGES, FORM_ANSWERS, SUBSCRIPTIONS,
        SERVING_REMINDERS, PRAYER_RETENTION)
# The jobs that only DELETE what the product promised to delete (a prayer request past its date, an address
# nobody confirmed, the answers of a form past its time, the contact values of a merge that can no longer be
# undone). They run on every pass whatever ``EMMAUS_SCHEDULED_COMMUNITY_JOBS``, ``EMMAUS_V2_MODULES`` and each
# church's module flags say: a switch stops Emmaus from acting for a church, never from keeping its word.
# ``SUBSCRIPTIONS`` is both: with the switches off only its deletions run.
DELETING_JOBS = (PERSON_MERGES, FORM_ANSWERS, SUBSCRIPTIONS, PRAYER_RETENTION)
# A job that has not ended well for this long is not running: three passes of 300 seconds.
STALE_MINUTES = 15

# Why a job is not expected to run at all.
OFF = "apagado"
NO_BASE_URL = "sin_direccion"
NO_EMAIL = "sin_correo"
# Why a job that should run is not trusted.
NEVER = "nunca"
STALE = "atrasado"


def community_jobs_enabled() -> bool:
    """The switch of the community jobs that ACT (send, generate, move a list) and the kill switch of the
    modules they belong to. The deletions (``DELETING_JOBS``) do not ask."""
    return bool(settings.EMMAUS_SCHEDULED_COMMUNITY_JOBS and settings.EMMAUS_V2_MODULES)


def skipped(job: str) -> str:
    """Why the scheduler does not run ``job`` with the settings of this process, or empty when it does."""
    if job == CUTOFFS or job in DELETING_JOBS:
        return ""
    if not community_jobs_enabled():
        return OFF
    if job in (NOTICE_MAIL, SERVING_REMINDERS):
        from events.services.team import email_is_configured

        if not settings.EMMAUS_PUBLIC_BASE_URL:
            return NO_BASE_URL
        if not email_is_configured():
            return NO_EMAIL
    return ""


def _mark(job: str, **values) -> None:
    if not SchedulerHeartbeat.objects.filter(job=job).update(**values):
        SchedulerHeartbeat.objects.update_or_create(job=job, defaults=values)


def _beat(job: str, **values) -> None:
    """Write the heartbeat, and never let it decide anything: the table may not exist yet (an image
    started before ``migrate``) or any more (after ``migrate core 0011``), and the job still has to run."""
    try:
        _mark(job, **values)
    except Exception as error:  # noqa: BLE001 - the database may be what failed
        log.error("The heartbeat of %s could not be written: %s", job, type(error).__name__)


def stack_of(error: BaseException) -> str:
    """Where ``error`` was raised: files, lines and functions. Not its message, which can quote data
    (a database error repeats the row it refused), and not the errors it was raised from."""
    return "".join(traceback.format_tb(error.__traceback__))


@dataclass
class JobRun:
    """How one job of a pass went. ``counts``: numbers only, None when the job raised. ``error``: empty,
    or a class name (the exception's, or the one the job's ``verdict`` gave its own counts)."""

    counts: dict | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def run_job(job: str, work: Callable[[], dict], *, verdict: Callable[[dict], str] | None = None, full_traceback: bool = False) -> JobRun:
    """Run one job of a pass: heartbeat before and after, one log line, and no exception out of here.

    Only ``work`` decides whether the job ran: a heartbeat that cannot be written is logged and
    ignored. ``work`` returns its counts; ``verdict`` may read them and name a failure that raised
    nothing (every e-mail refused). The log of an exception carries its class and its stack, never
    its message; ``full_traceback`` keeps Django's usual traceback, for the cutoffs pass, which
    logged that way before this module existed."""
    started = time.monotonic()
    _beat(job, last_started_at=timezone.now())
    run = JobRun()
    try:
        run.counts = work()
        run.error = verdict(run.counts) if verdict else ""
    except Exception as error:  # noqa: BLE001 - one job never stops the others, nor the next pass
        run.counts, run.error = None, type(error).__name__[:80]
        if full_traceback:
            log.exception("The scheduled job %s failed.", job)
        else:
            log.error("The scheduled job %s failed: %s\n%s", job, run.error, stack_of(error))
    line = {"job": job, "ok": run.ok, "ms": int((time.monotonic() - started) * 1000)}
    if run.error:
        logger.error(dumps({**line, "exception": run.error, **(run.counts or {})}))
        _beat(job, last_error=run.error)
    else:
        logger.info(dumps({**line, **run.counts}))
        _beat(job, last_ok_at=timezone.now(), last_error="")
    return run


def problem(job: str, max_age_minutes: int = STALE_MINUTES, heartbeat: SchedulerHeartbeat | None = None) -> str:
    """Empty when ``job`` ended well in the last ``max_age_minutes``. Otherwise one word: why it is
    not expected to run (``skipped``), ``nunca`` (no good pass was ever recorded: a fresh deploy
    counts as not running) or ``atrasado``."""
    reason = skipped(job)
    if reason:
        return reason
    heartbeat = heartbeat or SchedulerHeartbeat.objects.filter(job=job).first()
    if heartbeat is None or heartbeat.last_ok_at is None:
        return NEVER
    return STALE if heartbeat.last_ok_at < timezone.now() - timedelta(minutes=max_age_minutes) else ""


def last_error(job: str) -> str:
    """The class name the job's last pass left, or empty (also when the heartbeat cannot be read)."""
    try:
        return SchedulerHeartbeat.objects.filter(job=job).values_list("last_error", flat=True).first() or ""
    except DatabaseError:
        return ""


def notice_mail_problem() -> str:
    """Why a notice kept for a later day will NOT be e-mailed on its day as things are now, or empty
    when the job that sends those e-mails is running and its e-mails are getting through. A heartbeat
    that cannot be read (its table is missing) is a job that is not known to run."""
    try:
        return problem(NOTICE_MAIL)
    except DatabaseError:
        return NEVER


def base_url_for(option: str) -> str:
    """The address a command puts in its e-mails: ``--base-url`` if given, else ``EMMAUS_PUBLIC_BASE_URL``."""
    return (option or "").strip() or settings.EMMAUS_PUBLIC_BASE_URL
