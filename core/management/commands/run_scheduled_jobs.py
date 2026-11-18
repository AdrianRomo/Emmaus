"""Everything the ``scheduler`` service does, one pass every few minutes (issue #80).

    python manage.py run_scheduled_jobs              # one pass (cron)
    python manage.py run_scheduled_jobs --loop 300   # the scheduler service

A pass runs, in this order, each one on its own (``core.scheduler.run_job``: a job that fails is
logged, leaves its error's class name in its heartbeat and never stops the next job or the next pass):

1. ``cortes``: the whole pass of ``run_report_cutoffs`` (cutoffs, expired captures and sessions, and
   the retention purge once a day), unchanged.
2. ``avisos_correo``: the e-mails of the notices whose day came, what a publish left over and what
   failed (``notices.email.send_due``, what ``send_notice_emails --apply`` runs). Only with
   ``EMMAUS_PUBLIC_BASE_URL`` and a mail backend that delivers; for at most ``NOTICE_MAIL_BUDGET_SECONDS``.
3. ``lista_espera``: the offered places that expired go to the next in line
   (``agenda.services.expire_offers``, what ``expire_waiting_offers --apply`` runs).

2 and 3 are the community jobs: ``EMMAUS_SCHEDULED_COMMUNITY_JOBS=false`` (or the modules' kill
switch) leaves only the first, and the line it prints is then the one ``run_report_cutoffs`` prints.
Not here, on purpose: ``remind_follow_up_owners`` (it mails again on every run) and
``prune_attendance`` (it deletes). Both stay manual.

The pass prints the cutoffs' line with the community counts added: numbers only.
"""

import logging
import time
from io import StringIO

from django.conf import settings
from django.core.management.base import BaseCommand, OutputWrapper
from django.db import close_old_connections
from django.utils import timezone

from core import scheduler
from events.management.commands.run_report_cutoffs import Command as CutoffsCommand

logger = logging.getLogger(__name__)

# Seconds one pass may spend sending notice e-mails: a mail server that hangs must not hold the
# cutoffs of the next pass. What is left goes out on the following passes.
NOTICE_MAIL_BUDGET_SECONDS = 120
SKIPPED_WORDS = {
    scheduler.NO_BASE_URL: "sin dirección pública (EMMAUS_PUBLIC_BASE_URL)",
    scheduler.NO_EMAIL: "sin correo configurado",
}


def send_notice_mail() -> dict:
    from notices import email

    return email.send_due(settings.EMMAUS_PUBLIC_BASE_URL, budget_seconds=NOTICE_MAIL_BUDGET_SECONDS)


def expire_waiting_offers() -> dict:
    from agenda import services

    return services.expire_offers(apply=True, base_url=settings.EMMAUS_PUBLIC_BASE_URL or None)


class Command(BaseCommand):
    help = (
        "Una pasada del programador: los cortes y la limpieza de siempre (run_report_cutoffs) y, después, los correos de "
        "los avisos cuyo día llegó y los lugares ofrecidos de las listas de espera que vencieron. Se puede repetir sin duplicar."
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # One instance for the life of the process: it remembers which events had their daily retention check.
        self.cutoffs = CutoffsCommand()

    def add_arguments(self, parser):
        parser.add_argument("--loop", type=int, default=0, help="Repetir cada N segundos (para el servicio 'scheduler').")

    def run_cutoffs(self) -> tuple[bool, list[str]]:
        """The cutoffs pass, with what it prints kept for this pass's line."""
        printed = StringIO()
        self.cutoffs.stdout = OutputWrapper(printed)
        ok = scheduler.run_job(scheduler.CUTOFFS, lambda: self.cutoffs.run_once() or {}) is not None
        return ok, printed.getvalue().splitlines()

    def notice_mail_summary(self) -> str:
        reason = scheduler.skipped(scheduler.NOTICE_MAIL)
        if reason:
            return f"correos de avisos: {SKIPPED_WORDS[reason]}"
        done = scheduler.run_job(scheduler.NOTICE_MAIL, send_notice_mail)
        if done is None:
            return "correos de avisos: error"
        if done["not_configured"]:
            return f"correos de avisos: {SKIPPED_WORDS[scheduler.NO_EMAIL]}"
        return f"correos de avisos: {done['sent']} enviados, {done['failed']} fallidos, {done['left']} pendientes"

    def waiting_offers_summary(self) -> str:
        done = scheduler.run_job(scheduler.WAITING_OFFERS, expire_waiting_offers)
        if done is None:
            return "lista de espera: error"
        return f"lista de espera: {done['released']} lugares liberados, {done['offered']} ofrecidos"

    def run_once(self):
        stamp = f"{timezone.localtime(timezone.now()):%Y-%m-%d %H:%M}"
        ok, lines = self.run_cutoffs()
        head = lines[0] if lines else f"{stamp} · cortes: error"
        if lines and not ok:
            head += " · la pasada de cortes no terminó"
        if scheduler.community_jobs_enabled():
            head += f" · {self.notice_mail_summary()} · {self.waiting_offers_summary()}"
        self.stdout.write(head)
        for line in lines[1:]:
            self.stdout.write(line)

    def handle(self, *args, **options):
        interval = options["loop"]
        if not interval:
            self.run_once()
            return
        self.stdout.write(f"Revisando cortes, correos de avisos y listas de espera cada {interval} segundos.")
        while True:
            close_old_connections()
            try:
                self.run_once()
            except Exception:  # keep the scheduler alive; the next pass retries
                logger.exception("run_scheduled_jobs falló")
            time.sleep(interval)
