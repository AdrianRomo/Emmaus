"""Release the places of an event's waiting list that were offered and not accepted in time, and
offer each one to the next person in line (decision 0011).

    python manage.py expire_waiting_offers                       # dry run: counts only, nothing written
    python manage.py expire_waiting_offers --apply               # writes; no e-mail goes out
    python manage.py expire_waiting_offers --apply --base-url https://emmaus.ejemplo.org   # and tells the next ones by e-mail

The screens do the same whenever somebody signs up, cancels or opens "Inscritos"; this command is for
the events nobody opened. It prints counts and never a name, confirms nobody, and a second run
changes nothing. Without ``--base-url`` the address is ``settings.EMMAUS_PUBLIC_BASE_URL``; with neither
there is no address to put in an e-mail: who gets an offer reads it in Emmaus (Mi iglesia and the
event's page). The ``scheduler`` service does the same as ``--apply`` on every pass (``run_scheduled_jobs``).
"""

from urllib.parse import urlsplit

from django.core.management.base import BaseCommand, CommandError

from agenda import services
from core.scheduler import base_url_for


class Command(BaseCommand):
    help = "Libera los lugares ofrecidos de las listas de espera que ya vencieron y los ofrece a quien sigue (simulación sin --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Escribe los cambios. Sin esto solo cuenta.")
        parser.add_argument("--base-url", default="", help="Dirección pública de Emmaus, por ejemplo https://emmaus.ejemplo.org, para avisar por correo a quien recibe el lugar. Sin ella se usa EMMAUS_PUBLIC_BASE_URL.")

    def handle(self, *args, **options):
        apply, base_url = options["apply"], base_url_for(options["base_url"])
        if base_url:
            parts = urlsplit(base_url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise CommandError("--base-url tiene que ser la dirección pública de Emmaus (https://…).")
        totals = services.expire_offers(apply=apply, base_url=base_url or None)
        self.stdout.write(f"{'Aplicado' if apply else 'Simulación (usa --apply para escribir)'}:")
        self.stdout.write(f"  eventos con lugares ofrecidos vencidos: {totals['events']}")
        self.stdout.write(f"  lugares ofrecidos vencidos: {totals['expired']}")
        if apply:
            self.stdout.write(f"  liberados: {totals['released']}")
            self.stdout.write(f"  ofrecidos a quien seguía en la lista: {totals['offered']}")
            if totals["offered"] and not base_url:
                self.stdout.write("  Sin --base-url ni EMMAUS_PUBLIC_BASE_URL no se envió ningún correo: cada persona ve su lugar ofrecido en Emmaus.")
