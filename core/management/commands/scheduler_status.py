"""Whether the scheduled jobs are running, for cron or a monitor on the host (issue #80).

    python manage.py scheduler_status                # one line per job; exit 1 if one is not running
    python manage.py scheduler_status --max-age 30   # minutes without a good pass that are tolerated (15)

Read-only. A job this server is set not to run (the community switch is off, or the notice e-mails
have no public address or no mail backend) is printed as such and does not fail the command: it is
a setting, not a missed run. A job that never recorded a good pass is not running.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core import scheduler
from core.models import SchedulerHeartbeat

NOT_EXPECTED = {
    scheduler.OFF: "apagado (EMMAUS_SCHEDULED_COMMUNITY_JOBS o EMMAUS_V2_MODULES)",
    scheduler.NO_BASE_URL: "no corre: falta EMMAUS_PUBLIC_BASE_URL",
    scheduler.NO_EMAIL: "no corre: no hay correo configurado (EMAIL_BACKEND)",
}


def minutes_since(moment, now) -> int:
    return max(0, int((now - moment).total_seconds() // 60))


class Command(BaseCommand):
    help = (
        "Dice, por cada trabajo programado, cuándo terminó bien por última vez. Termina con error (código 1) si alguno "
        "que debería correr no ha terminado bien en los últimos --max-age minutos. No escribe nada."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-age", type=int, default=scheduler.STALE_MINUTES,
            help=f"Minutos sin una pasada buena que se toleran ({scheduler.STALE_MINUTES}).",
        )

    def handle(self, *args, **options):
        max_age = options["max_age"]
        if max_age < 1:
            raise CommandError("--max-age tiene que ser al menos 1 minuto.", returncode=2)
        now = timezone.now()
        heartbeats = {row.job: row for row in SchedulerHeartbeat.objects.filter(job__in=scheduler.JOBS)}
        late = 0
        for job in scheduler.JOBS:
            heartbeat = heartbeats.get(job)
            reason = scheduler.skipped(job) or (scheduler.NEVER if heartbeat is None else scheduler.problem(job, max_age, heartbeat))
            if reason in NOT_EXPECTED:
                self.stdout.write(f"{job}: {NOT_EXPECTED[reason]}")
                continue
            if not reason:
                self.stdout.write(f"{job}: bien · terminó hace {minutes_since(heartbeat.last_ok_at, now)} min")
                continue
            late += 1
            if reason == scheduler.NEVER:
                line = f"{job}: SIN CORRER · nunca ha terminado bien"
            else:
                line = f"{job}: SIN CORRER · terminó bien por última vez hace {minutes_since(heartbeat.last_ok_at, now)} min"
            if heartbeat is not None and heartbeat.last_error:
                line += f" · último error: {heartbeat.last_error}"
            self.stdout.write(line)
        if late:
            raise CommandError(
                f"{late} de {len(scheduler.JOBS)} trabajos programados no terminaron bien en los últimos {max_age} minutos. "
                "Revisa el servicio «scheduler».",
                returncode=1,
            )
