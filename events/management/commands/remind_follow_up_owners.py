from urllib.parse import urlsplit

from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from core.notify import FAILED, NOT_CONFIGURED, SENT, notify
from core.scheduler import base_url_for
from events.models import Event
from events.services import audit
from events.services.follow_ups import owners_to_remind

KIND = "seguimientos_pendientes"


def reminder_link(base_url: str, event: Event) -> str:
    """The owner's own open follow-ups of the event ("Míos" on "Seguimiento")."""
    return f"{base_url.rstrip('/')}{reverse('events:follow_ups', args=[event.slug])}?estado=abiertos&responsable=mio"


class Command(BaseCommand):
    help = (
        "Recuerda por correo, a cada responsable, que tiene seguimientos abiertos en un evento terminado. "
        "El correo no lleva nombres, motivos ni respuestas: solo que hay seguimientos y dónde verlos. "
        "Sin --apply solo muestra cuántos correos saldrían. No está programado: se corre a mano."
    )

    def add_arguments(self, parser):
        parser.add_argument("--event", help="Nombre corto de un evento. Sin él: todos los eventos terminados.")
        parser.add_argument("--base-url", default="", help="Dirección pública de Emmaus, por ejemplo https://emmaus.ejemplo.org. Sin ella se usa EMMAUS_PUBLIC_BASE_URL; con --apply hace falta una de las dos.")
        parser.add_argument("--apply", action="store_true", help="Enviar de verdad.")

    def handle(self, *args, **options):
        apply, base_url = options["apply"], base_url_for(options["base_url"])
        if apply:
            parts = urlsplit(base_url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise CommandError(
                    "Con --apply hace falta la dirección pública de Emmaus (https://…): --base-url, o EMMAUS_PUBLIC_BASE_URL en el servidor."
                )
        events = Event.objects.filter(camp_tools=True).select_related("church")
        if options["event"]:
            events = events.filter(slug=options["event"])
            if not events.exists():
                raise CommandError("No hay un evento con ese nombre corto.")
        else:
            events = events.filter(status=Event.Status.CLOSED)
        if not events.exists():
            self.stdout.write("No hay eventos terminados.")
            return
        for event in events.order_by("id"):
            owners = owners_to_remind(event)
            total = sum(count for _, count in owners)
            if not owners:
                self.stdout.write(f"{event.name}: nadie tiene seguimientos abiertos a su cargo.")
                continue
            if not apply:
                self.stdout.write(f"[simulación] {event.name}: saldrían {len(owners)} correos por {total} seguimientos abiertos.")
                continue
            context = {"church_name": event.church.name, "link": reminder_link(base_url, event)}
            outcomes = {SENT: 0, NOT_CONFIGURED: 0, FAILED: 0}
            for owner, _count in owners:
                outcomes[notify(KIND, owner.email, context)] += 1
            if outcomes[NOT_CONFIGURED]:
                self.stdout.write(f"{event.name}: no hay correo configurado, no se envió nada.")
                continue
            if outcomes[SENT]:
                audit.record(
                    None, event, "recordatorio_seguimiento",
                    f"Se enviaron {outcomes[SENT]} recordatorios de seguimientos abiertos a sus responsables", event, sent=outcomes[SENT],
                )
            line = f"{event.name}: se enviaron {outcomes[SENT]} correos."
            if outcomes[FAILED]:
                line += f" {outcomes[FAILED]} no se pudieron enviar."
            self.stdout.write(self.style.SUCCESS(line) if not outcomes[FAILED] else line)
