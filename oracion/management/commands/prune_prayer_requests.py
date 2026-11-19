"""Delete the prayer requests whose date passed (``oracion.services.prune``). The scheduler runs it
on every pass (``run_scheduled_jobs``); this is the same thing by hand.

    python manage.py prune_prayer_requests            # says how many it would delete
    python manage.py prune_prayer_requests --apply    # deletes them

One number for the whole server: never a church, a person or a word of a request.
"""

from django.core.management.base import BaseCommand

from oracion import services


class Command(BaseCommand):
    help = "Borra las peticiones de oración vencidas, con su respuesta. Sin --apply solo dice cuántas."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Borrar de verdad.")

    def handle(self, *args, **options):
        total = services.prune(apply=options["apply"])
        if options["apply"]:
            self.stdout.write(f"Peticiones de oración vencidas borradas: {total}.")
        else:
            self.stdout.write(f"Peticiones de oración vencidas: {total}. No se borró nada (usa --apply).")
