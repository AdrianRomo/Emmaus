from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from events.models import Event, FollowUp
from events.services import audit

FINISHED = (Event.Status.CLOSED, Event.Status.ARCHIVED)


class PurgeRefused(Exception):
    """Nothing was deleted: the event changed between the check and the lock. The message says how."""


def is_expired(event: Event, today) -> bool:
    """The retention period (``retention_days`` after ``ends_on``) is over. An event without an end date never is."""
    return event.ends_on is not None and event.data_purged_at is None and (today - event.ends_on).days > event.retention_days


def has_open_care(event: Event) -> bool:
    """An open safety report or an open confidential referral: someone still has to act on it."""
    return FollowUp.objects.filter(event=event, status__in=FollowUp.OPEN_STATUSES).filter(
        Q(kind=FollowUp.Kind.SAFETY) | Q(kind=FollowUp.Kind.CARE, confidential=True)
    ).exists()


def purge_event(event: Event, *, automatic: bool = False) -> int:
    """Delete every campero of the event (with interviews, answers, consents, follow-ups) and mark it purged.

    Cutoffs, the team, the questionnaire and the activity log stay. Returns how many camperos were deleted.
    The event row is locked and read again first, so two overlapping runs cannot both delete and audit:
    the second raises ``PurgeRefused``. ``automatic`` (the scheduler) also re-checks, under the lock, that the
    event is still finished and has no open safety report or confidential referral.
    """
    with transaction.atomic():
        locked = Event.objects.select_for_update().filter(pk=event.pk, data_purged_at__isnull=True).first()
        if locked is None:
            raise PurgeRefused("ya se habían borrado sus datos")
        if automatic and locked.status not in FINISHED:
            raise PurgeRefused("ya no está terminado ni archivado")
        if automatic and has_open_care(locked):
            raise PurgeRefused("tiene reportes de riesgo o seguimientos confidenciales abiertos")
        count = locked.participants.count()
        locked.participants.all().delete()
        locked.data_purged_at = timezone.now()
        locked.save(update_fields=["data_purged_at"])
        audit.record(None, locked, "campero_eliminado", f"Se borraron {count} registros por fin del periodo de conservación", locked)
    event.data_purged_at = locked.data_purged_at
    return count


class Command(BaseCommand):
    help = (
        "Borra los datos individuales de eventos cuyo periodo de conservación terminó. "
        "Los cortes (solo totales) se conservan. Sin --apply solo muestra lo que se borraría."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Borrar de verdad.")

    def handle(self, *args, **options):
        today = timezone.localdate()
        due = [event for event in Event.objects.filter(data_purged_at__isnull=True, ends_on__isnull=False) if is_expired(event, today)]
        if not due:
            self.stdout.write("No hay eventos con datos vencidos.")
            return
        for event in due:
            if not options["apply"]:
                count = event.participants.count()
                self.stdout.write(f"[simulación] {event.name}: se borrarían {count} camperos con sus respuestas.")
                continue
            try:
                count = purge_event(event)
            except PurgeRefused as refused:
                self.stdout.write(f"{event.name}: no se borró nada, {refused}.")
                continue
            self.stdout.write(self.style.SUCCESS(f"{event.name}: se borraron {count} camperos."))
