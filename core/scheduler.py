"""The scheduled jobs: their names, their heartbeat and whether each one is running (issue #80).

``run_scheduled_jobs`` (the ``scheduler`` service) runs every job on each pass through ``run_job``,
which writes the job's heartbeat (``SchedulerHeartbeat``) and one JSON line on ``emmaus.scheduler``::

    {"job": "avisos_correo", "ok": true, "ms": 12, "notices": 1, "sent": 1, "failed": 0, "left": 0}
    {"job": "avisos_correo", "ok": false, "ms": 3, "exception": "OperationalError"}

Counts and a class name only: never an address, a name, a title or an error message.

Who reads the heartbeat: ``manage.py scheduler_status`` (for cron or a monitor on the host) and the
notice screens (``notice_mail_problem``), which must not promise an e-mail on a later day while
nothing is sending them.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Callable

from django.conf import settings
from django.utils import timezone

from core.models import SchedulerHeartbeat
from core.request_log import dumps

logger = logging.getLogger("emmaus.scheduler")

CUTOFFS = "cortes"
NOTICE_MAIL = "avisos_correo"
WAITING_OFFERS = "lista_espera"
JOBS = (CUTOFFS, NOTICE_MAIL, WAITING_OFFERS)
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
    """The switch of the community jobs and the kill switch of the modules they belong to."""
    return bool(settings.EMMAUS_SCHEDULED_COMMUNITY_JOBS and settings.EMMAUS_V2_MODULES)


def skipped(job: str) -> str:
    """Why the scheduler does not run ``job`` with the settings of this process, or empty when it does."""
    if job == CUTOFFS:
        return ""
    if not community_jobs_enabled():
        return OFF
    if job == NOTICE_MAIL:
        from events.services.team import email_is_configured

        if not settings.EMMAUS_PUBLIC_BASE_URL:
            return NO_BASE_URL
        if not email_is_configured():
            return NO_EMAIL
    return ""


def _mark(job: str, **values) -> None:
    if not SchedulerHeartbeat.objects.filter(job=job).update(**values):
        SchedulerHeartbeat.objects.update_or_create(job=job, defaults=values)


def run_job(job: str, work: Callable[[], dict]) -> dict | None:
    """Run one job of a pass: heartbeat before and after, one log line, and no exception out of here.
    ``work`` returns its counts (numbers only). None: the job failed, and the pass goes on."""
    started = time.monotonic()
    try:
        _mark(job, last_started_at=timezone.now())
        counts = work()
        _mark(job, last_ok_at=timezone.now(), last_error="")
    except Exception as error:  # noqa: BLE001 - one job never stops the others, nor the next pass
        name = type(error).__name__[:80]
        logger.error(dumps({"job": job, "ok": False, "ms": int((time.monotonic() - started) * 1000), "exception": name}))
        logging.getLogger(__name__).exception("The scheduled job %s failed.", job)
        try:
            _mark(job, last_error=name)
        except Exception:  # noqa: BLE001 - the database may be what failed
            logging.getLogger(__name__).exception("The heartbeat of %s could not be written.", job)
        return None
    logger.info(dumps({"job": job, "ok": True, "ms": int((time.monotonic() - started) * 1000), **counts}))
    return counts


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


def notice_mail_problem() -> str:
    """Why a notice kept for a later day will NOT be e-mailed on its day as things are now, or empty
    when the job that sends those e-mails is running."""
    return problem(NOTICE_MAIL)


def base_url_for(option: str) -> str:
    """The address a command puts in its e-mails: ``--base-url`` if given, else ``EMMAUS_PUBLIC_BASE_URL``."""
    return (option or "").strip() or settings.EMMAUS_PUBLIC_BASE_URL
