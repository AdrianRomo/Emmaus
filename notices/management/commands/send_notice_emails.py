from urllib.parse import urlsplit

from django.core.management.base import BaseCommand, CommandError

from notices import email


class Command(BaseCommand):
    help = (
        "Envía los correos de los avisos que ya se pueden leer y todavía no se enviaron: los que se guardaron para "
        "un día que ya llegó, lo que quedó pendiente de un aviso con muchas personas y lo que falló. El correo solo "
        "dice que hay un aviso y dónde leerlo. Nadie recibe dos por la misma versión de un aviso. Sin --apply solo "
        "muestra cuántos correos saldrían. No está programado."
    )

    def add_arguments(self, parser):
        parser.add_argument("--base-url", default="", help="Dirección pública de Emmaus, por ejemplo https://emmaus.ejemplo.org. Necesaria con --apply.")
        parser.add_argument(
            "--days", type=int, default=email.WINDOW_DAYS,
            help=f"Solo avisos cuyo día llegó, cuya información cambió o que se volvieron urgentes en estos últimos días ({email.WINDOW_DAYS}).",
        )
        parser.add_argument("--apply", action="store_true", help="Enviar de verdad.")

    def handle(self, *args, **options):
        apply, base_url, days = options["apply"], options["base_url"].strip(), options["days"]
        if days < 0:
            raise CommandError("--days no puede ser negativo.")
        if apply:
            parts = urlsplit(base_url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise CommandError("Con --apply hace falta --base-url con la dirección pública de Emmaus (https://…).")
        notices = [notice for notice in email.open_rounds(days) if email.is_mailable(notice)]
        if not apply:
            waiting = [email.waiting_count(notice) for notice in notices]
            self.stdout.write(
                f"[simulación] avisos con correos por enviar: {sum(1 for count in waiting if count)} · correos que saldrían: {sum(waiting)}"
            )
            return
        sent = failed = left = 0
        for notice in notices:
            while True:
                done = email.deliver(notice.pk, base_url)
                if done.not_configured:
                    self.stdout.write("No hay correo configurado: no se envió nada.")
                    return
                sent, failed = sent + done.sent, failed + done.failed
                # Again only while a full batch went out: what is left then is the part above the cap.
                if not done.left or done.failed or not done.sent:
                    left += done.left
                    break
        line = f"avisos revisados: {len(notices)} · correos enviados: {sent} · no se pudieron enviar: {failed} · quedan pendientes: {left}"
        self.stdout.write(line if failed or left else self.style.SUCCESS(line))
