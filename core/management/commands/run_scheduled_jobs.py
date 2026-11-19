"""Everything the ``scheduler`` service does, one pass every few minutes (issue #80).

    python manage.py run_scheduled_jobs              # one pass (cron)
    python manage.py run_scheduled_jobs --loop 300   # the scheduler service

A pass runs, in this order, each one on its own (``core.scheduler.run_job``: a job that fails is
logged, leaves its error's class name in its heartbeat and never stops the next job or the next pass;
neither does a heartbeat that cannot be written). Without ``--loop`` the command ends with code 1
when a job of the pass failed, after running all of them, as ``run_report_cutoffs`` does for cron:

1. ``cortes``: the whole pass of ``run_report_cutoffs`` (cutoffs, expired captures and sessions, and
   the retention purge once a day), unchanged.
2. ``avisos_correo``: the e-mails of the notices whose day came, what a publish left over and what
   failed (``notices.email.send_due``, what ``send_notice_emails --apply`` runs). Only with
   ``EMMAUS_PUBLIC_BASE_URL`` and a mail backend that delivers; for at most ``NOTICE_MAIL_BUDGET_SECONDS``.
   A pass in which no e-mail got through is a failed pass (``notices.email.health``).
3. ``lista_espera``: the offered places that expired go to the next in line
   (``agenda.services.expire_offers``, what ``expire_waiting_offers --apply`` runs), in the churches
   that use the ``community`` module. An event whose list could not be moved makes the pass a failed one.
4. ``series_eventos``: the dates of each series (what the church does every week) are written up to
   the horizon (``agenda.series.generate_due``), in the churches that use the ``community`` module. A date
   is written once however many passes run; a series that could not be written makes the pass a failed one.

5. ``uniones_personas``: a merge of two records of a person keeps, for its undo, the contact values the
   surviving record had before; when the undo ends (30 days) they are emptied (``people.merge.clear_expired``,
   what ``prune_person_merges --apply`` runs), in every church whatever its module flags: it only empties
   those values. No record of a person and no record of a merge is deleted. Doing it twice changes nothing.

6. ``respuestas_formularios``: the answers to a general form are deleted when the time the form said it keeps
   them ended (``formularios.services.prune``, what ``prune_form_submissions --apply`` runs), with their tasks,
   in every church whatever its module flags: it only deletes. The form and its questions stay.

7. ``suscripciones_evento``: the e-mail addresses left on an event's public page to hear about its public
   notices (``agenda.subscriptions.run_pass``; issue #96). First it deletes what is over (unconfirmed requests
   after 48 hours, every address 14 days after its event ended), in every church whatever its flags. Then, only
   with ``EMMAUS_PUBLIC_BASE_URL`` and a mail backend that delivers, and only for events of churches that use
   the ``community`` module whose public page takes subscriptions: the e-mails that ask to confirm an address
   and one e-mail per new public notice to each confirmed address, for at most ``SUBSCRIPTION_BUDGET_SECONDS``.

2 to 7 are the community jobs: ``EMMAUS_SCHEDULED_COMMUNITY_JOBS=false`` (or the modules' kill
switch) leaves only the first, and the line it prints is then the one ``run_report_cutoffs`` prints.
Not here, on purpose: ``remind_follow_up_owners`` (it mails again on every run) and
``prune_attendance`` (it deletes). Both stay manual.

The pass prints the cutoffs' line with the community counts added: numbers only.
"""

import logging
import time
from io import StringIO

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, OutputWrapper
from django.db import close_old_connections
from django.utils import timezone

from core import scheduler
from events.management.commands.run_report_cutoffs import Command as CutoffsCommand

logger = logging.getLogger(__name__)

# Seconds one pass may spend sending notice e-mails: a mail server that hangs must not hold the
# cutoffs of the next pass. What is left goes out on the following passes.
NOTICE_MAIL_BUDGET_SECONDS = 120
# The same bound for the e-mails of an event's subscribers.
SUBSCRIPTION_BUDGET_SECONDS = 60
SKIPPED_WORDS = {
    scheduler.NO_BASE_URL: "sin dirección pública (EMMAUS_PUBLIC_BASE_URL)",
    scheduler.NO_EMAIL: "sin correo configurado",
}


def send_notice_mail() -> dict:
    from notices import email

    return email.send_due(settings.EMMAUS_PUBLIC_BASE_URL, budget_seconds=NOTICE_MAIL_BUDGET_SECONDS)


def notice_mail_verdict(done: dict) -> str:
    from notices import email

    return email.health(done, was_failing=scheduler.last_error(scheduler.NOTICE_MAIL) == email.ALL_SENDS_FAILED)


def waiting_offers_verdict(done: dict) -> str:
    return "EventsFailed" if done["failed"] else ""


def expire_waiting_offers() -> dict:
    from agenda import services

    return services.expire_offers(apply=True, base_url=settings.EMMAUS_PUBLIC_BASE_URL or None)


def series_verdict(done: dict) -> str:
    return "SeriesFailed" if done["failed"] else ""


def write_series_dates() -> dict:
    from agenda import series

    return series.generate_due()


def clear_merge_contact_values() -> dict:
    from people import merge

    return {"cleared": merge.clear_expired(apply=True)}


def prune_form_answers() -> dict:
    from formularios import services

    return services.prune(apply=True)


def event_subscriptions() -> dict:
    from agenda import subscriptions

    return subscriptions.run_pass(budget_seconds=SUBSCRIPTION_BUDGET_SECONDS)


def event_subscriptions_verdict(done: dict) -> str:
    from agenda import subscriptions

    return subscriptions.verdict(done)


class Command(BaseCommand):
    help = (
        "Una pasada del programador: los cortes y la limpieza de siempre (run_report_cutoffs) y, después, los correos de "
        "los avisos cuyo día llegó, los lugares ofrecidos de las listas de espera que vencieron y las fechas que siguen de las series. Se puede repetir sin duplicar."
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
        run = scheduler.run_job(scheduler.CUTOFFS, lambda: self.cutoffs.run_once() or {}, full_traceback=True)
        return run.ok, printed.getvalue().splitlines()

    def notice_mail_summary(self) -> tuple[bool, str]:
        reason = scheduler.skipped(scheduler.NOTICE_MAIL)
        if reason:
            return True, f"correos de avisos: {SKIPPED_WORDS[reason]}"
        run = scheduler.run_job(scheduler.NOTICE_MAIL, send_notice_mail, verdict=notice_mail_verdict)
        done = run.counts
        if done is None:
            return False, "correos de avisos: error"
        if done["not_configured"]:
            return True, f"correos de avisos: {SKIPPED_WORDS[scheduler.NO_EMAIL]}"
        line = f"correos de avisos: {done['sent']} enviados, {done['failed']} fallidos, {done['left']} pendientes"
        return run.ok, line if run.ok else f"{line} (ningún correo está saliendo)"

    def waiting_offers_summary(self) -> tuple[bool, str]:
        run = scheduler.run_job(scheduler.WAITING_OFFERS, expire_waiting_offers, verdict=waiting_offers_verdict)
        done = run.counts
        if done is None:
            return False, "lista de espera: error"
        line = f"lista de espera: {done['released']} lugares liberados, {done['offered']} ofrecidos"
        return run.ok, line if run.ok else f"{line}, {done['failed']} eventos con error"

    def series_summary(self) -> tuple[bool, str]:
        run = scheduler.run_job(scheduler.SERIES, write_series_dates, verdict=series_verdict)
        done = run.counts
        if done is None:
            return False, "series: error"
        line = f"series: {done['created']} fechas nuevas"
        return run.ok, line if run.ok else f"{line}, {done['failed']} series con error"

    def person_merges_summary(self) -> tuple[bool, str]:
        run = scheduler.run_job(scheduler.PERSON_MERGES, clear_merge_contact_values)
        if run.counts is None:
            return False, "uniones de personas: error"
        return True, f"uniones de personas: {run.counts['cleared']} con datos anteriores borrados"

    def form_answers_summary(self) -> tuple[bool, str]:
        run = scheduler.run_job(scheduler.FORM_ANSWERS, prune_form_answers)
        if run.counts is None:
            return False, "formularios: error"
        return True, f"formularios: {run.counts['submissions']} respuestas vencidas borradas"

    def event_subscriptions_summary(self) -> tuple[bool, str]:
        run = scheduler.run_job(scheduler.SUBSCRIPTIONS, event_subscriptions, verdict=event_subscriptions_verdict)
        done = run.counts
        if done is None:
            return False, "suscripciones de eventos: error"
        line = f"suscripciones de eventos: {done['unconfirmed'] + done['expired']} direcciones borradas"
        if done["not_configured"]:
            return True, f"{line}, sin correos (falta la dirección pública o el correo)"
        line += f", {done['confirmations'] + done['sent']} correos enviados, {done['failed']} fallidos, {done['left']} pendientes"
        return run.ok, line if run.ok else f"{line} (ningún correo está saliendo)"

    def run_once(self) -> bool:
        """One pass. False when a job of it failed."""
        stamp = f"{timezone.localtime(timezone.now()):%Y-%m-%d %H:%M}"
        ok, lines = self.run_cutoffs()
        head = lines[0] if lines else f"{stamp} · cortes: error"
        if lines and not ok:
            head += " · la pasada de cortes no terminó"
        if scheduler.community_jobs_enabled():
            for job_ok, summary in (self.notice_mail_summary(), self.waiting_offers_summary(), self.series_summary(),
                                    self.person_merges_summary(), self.form_answers_summary(),
                                    self.event_subscriptions_summary()):
                ok = ok and job_ok
                head += f" · {summary}"
        self.stdout.write(head)
        for line in lines[1:]:
            self.stdout.write(line)
        return ok

    def handle(self, *args, **options):
        interval = options["loop"]
        if not interval:
            if not self.run_once():
                raise CommandError("Un trabajo de la pasada falló: revisa la línea de arriba y el registro.", returncode=1)
            return
        self.stdout.write(f"Revisando cortes, correos de avisos y listas de espera cada {interval} segundos.")
        while True:
            close_old_connections()
            try:
                self.run_once()
            except Exception:  # keep the scheduler alive; the next pass retries
                logger.exception("run_scheduled_jobs falló")
            time.sleep(interval)
