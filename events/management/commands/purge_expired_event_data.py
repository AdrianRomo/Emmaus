from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from events.models import Event
from events.services import audit


class Command(BaseCommand):
    help = (
        "Borra los datos individuales de eventos cuyo periodo de conservación terminó. "
        "Los cortes (solo totales) se conservan. Sin --apply solo muestra lo que se borraría."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Borrar de verdad.")

    def handle(self, *args, **options):
        today = timezone.localdate()
        due = []
        for event in Event.objects.filter(data_purged_at__isnull=True, ends_on__isnull=False):
            if (today - event.ends_on).days > event.retention_days:
                due.append(event)
        if not due:
            self.stdout.write("No hay eventos con datos vencidos.")
            return
        for event in due:
            count = event.participants.count()
            if not options["apply"]:
                self.stdout.write(f"[simulación] {event.name}: se borrarían {count} camperos con sus respuestas.")
                continue
            with transaction.atomic():
                event.participants.all().delete()
                event.data_purged_at = timezone.now()
                event.save(update_fields=["data_purged_at"])
                audit.record(None, event, "campero_eliminado", f"Se borraron {count} registros por fin del periodo de conservación", event)
            self.stdout.write(self.style.SUCCESS(f"{event.name}: se borraron {count} camperos."))
