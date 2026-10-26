import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from events.models import ParticipantSession
from events.services import throttle
from events.services.interviews import KIOSK_IDLE_MINUTES, release_stale_claims
from events.services.join import purge_decided_requests
from events.services.reports import run_due_cutoffs

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Genera los cortes de seguimiento que tocan ahora y limpia sesiones vencidas. Se puede repetir sin duplicar."

    def add_arguments(self, parser):
        parser.add_argument("--loop", type=int, default=0, help="Repetir cada N segundos (para el servicio 'scheduler').")

    def run_once(self):
        now = timezone.now()
        released = release_stale_claims()
        expired = ParticipantSession.objects.filter(
            state=ParticipantSession.State.ACTIVE, last_activity_at__lt=now - timezone.timedelta(minutes=KIOSK_IDLE_MINUTES)
        ).update(state=ParticipantSession.State.AWAITING_UNLOCK, end_reason=ParticipantSession.EndReason.EXPIRED)
        # A computer left waiting for its coordinator stops accepting unlock attempts after 3 hours;
        # the interview stays in progress and is resumed after a normal login.
        expired += ParticipantSession.objects.filter(
            created_at__lt=now - timezone.timedelta(hours=3)
        ).exclude(state=ParticipantSession.State.CLOSED).update(
            state=ParticipantSession.State.CLOSED, ended_at=now, end_reason=ParticipantSession.EndReason.EXPIRED
        )
        snapshots = run_due_cutoffs(now)
        from importlib import import_module

        from django.conf import settings

        import_module(settings.SESSION_ENGINE).SessionStore.clear_expired()
        # Anonymous searches and access requests leave one attempt counter per browser session, and decided
        # requests keep a stranger's name and e-mail: neither may grow forever.
        counters = throttle.purge_expired(now)
        old_requests = purge_decided_requests(now)
        ready = sum(1 for s in snapshots if s.status == "listo")
        failed = len(snapshots) - ready
        self.stdout.write(
            f"{timezone.localtime(now):%Y-%m-%d %H:%M} · cortes: {ready} listos, {failed} con error · entrevistas liberadas: {released} · "
            f"capturas vencidas: {expired} · contadores borrados: {counters} · solicitudes antiguas borradas: {old_requests}"
        )

    def handle(self, *args, **options):
        interval = options["loop"]
        if not interval:
            self.run_once()
            return
        self.stdout.write(f"Revisando cortes cada {interval} segundos.")
        while True:
            close_old_connections()
            try:
                self.run_once()
            except Exception:  # keep the scheduler alive; the next pass retries
                logger.exception("run_report_cutoffs falló")
            time.sleep(interval)
