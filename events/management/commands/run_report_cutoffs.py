import logging
import time
from datetime import date
from datetime import time as clock

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from events.management.commands.purge_expired_event_data import FINISHED, PurgeRefused, is_expired, purge_event
from events.models import Event, ParticipantSession
from events.services import throttle
from events.services.interviews import KIOSK_IDLE_MINUTES, release_stale_claims
from events.services.join import purge_decided_requests
from events.services.metrics import local_tz
from events.services.reports import run_due_cutoffs

logger = logging.getLogger(__name__)

# The retention purge runs on the first pass after this hour, in each event's own time zone.
RETENTION_HOUR = clock(3, 0)


class Command(BaseCommand):
    help = "Genera los cortes que tocan ahora y limpia sesiones vencidas. Se puede repetir sin duplicar."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Event id -> the event-local day its retention was last checked. Kept by the --loop process, so the
        # purge runs once per day there; a one-shot run (cron) checks on every run after 03:00, which repeats
        # nothing: a purged event is marked (data_purged_at) and never selected again.
        self._retention_checked: dict[int, date] = {}

    def add_arguments(self, parser):
        parser.add_argument("--loop", type=int, default=0, help="Repetir cada N segundos (para el servicio 'scheduler').")

    def purge_expired_events(self, now) -> tuple[int, int, int] | None:
        """The retention purge (``purge_expired_event_data --apply``), once per event-local day after 03:00.

        Only events that ended (``ends_on`` set) and are closed or archived: an event left "En curso" is never
        purged automatically, whatever its dates say. An event with an open safety report or an open
        confidential referral keeps its data, with one line naming it, and is checked again the next day.
        Returns (checked, purged, camperos), or None when no event was due for its daily check on this pass.
        """
        stamp = f"{timezone.localtime(now):%Y-%m-%d %H:%M}"
        candidates = Event.objects.filter(status__in=FINISHED, ends_on__isnull=False, data_purged_at__isnull=True)
        checked = purged = camperos = 0
        for event in candidates:
            local_now = now.astimezone(local_tz(event))
            if local_now.time() < RETENTION_HOUR or self._retention_checked.get(event.pk) == local_now.date():
                continue
            self._retention_checked[event.pk] = local_now.date()
            checked += 1
            if not is_expired(event, local_now.date()):
                continue
            try:  # purge_event decides under the row lock: still finished, not purged by another run, nothing open
                camperos += purge_event(event, automatic=True)
            except PurgeRefused as refused:
                self.stdout.write(f"{stamp} · conservación: {event.slug} conserva sus datos: {refused}")
                continue
            purged += 1
        return (checked, purged, camperos) if checked else None

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
        retention = self.purge_expired_events(now)
        if retention is not None:
            checked, purged, camperos = retention
            self.stdout.write(
                f"{timezone.localtime(now):%Y-%m-%d %H:%M} · eventos terminados revisados: {checked} · "
                f"eventos con datos vencidos borrados: {purged} · camperos borrados: {camperos}"
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
