from urllib.parse import urlsplit

from django.core.management.base import BaseCommand, CommandError

from core.scheduler import base_url_for
from notices import email


class Command(BaseCommand):
    help = (
        "Envía los correos de los avisos que ya se pueden leer y todavía no se enviaron: los que se guardaron para "
        "un día que ya llegó, lo que quedó pendiente de un aviso con muchas personas y lo que falló. El correo solo "
        "dice que hay un aviso y dónde leerlo. Nadie recibe dos por la misma versión de un aviso. Sin --apply solo "
        "muestra cuántos correos saldrían. El servicio «scheduler» hace lo mismo en cada pasada (run_scheduled_jobs)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--base-url", default="", help="Dirección pública de Emmaus, por ejemplo https://emmaus.ejemplo.org. Sin ella se usa EMMAUS_PUBLIC_BASE_URL; con --apply hace falta una de las dos.")
        parser.add_argument(
            "--days", type=int, default=email.WINDOW_DAYS,
            help=f"Solo avisos cuyo día llegó, cuya información cambió o que se volvieron urgentes en estos últimos días ({email.WINDOW_DAYS}).",
        )
        parser.add_argument("--apply", action="store_true", help="Enviar de verdad.")

    def handle(self, *args, **options):
        apply, base_url, days = options["apply"], base_url_for(options["base_url"]), options["days"]
        if days < 0:
            raise CommandError("--days no puede ser negativo.")
        if apply:
            parts = urlsplit(base_url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise CommandError(
                    "Con --apply hace falta la dirección pública de Emmaus (https://…): --base-url, o EMMAUS_PUBLIC_BASE_URL en el servidor."
                )
        if not apply:
            waiting = [email.waiting_count(notice) for notice in email.open_rounds(days) if email.is_mailable(notice)]
            self.stdout.write(
                f"[simulación] avisos con correos por enviar: {sum(1 for count in waiting if count)} · correos que saldrían: {sum(waiting)}"
            )
            return
        done = email.send_due(base_url, days=days)
        if done["not_configured"]:
            self.stdout.write("No hay correo configurado: no se envió nada.")
            return
        line = (
            f"avisos revisados: {done['notices']} · correos enviados: {done['sent']} · "
            f"no se pudieron enviar: {done['failed']} · quedan pendientes: {done['left']}"
        )
        self.stdout.write(line if done["failed"] or done["left"] else self.style.SUCCESS(line))
