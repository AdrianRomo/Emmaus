"""Settle again every registration that holds a place in an event that asks for a guardian's
authorization and has not ended (decision 0015): for rows confirmed by older code, or after the
words of the authorization changed (``people.policy.CONSENT_POLICY_VERSION``).

    python manage.py resettle_registrations           # dry run: counts only, nothing written
    python manage.py resettle_registrations --apply   # writes, with one audit row per changed registration

It prints counts and never a name. A second ``--apply`` changes nothing.
"""

from django.core.management.base import BaseCommand

from agenda import services


class Command(BaseCommand):
    help = "Revisa otra vez las inscripciones de los eventos que piden autorización para menores (simulación sin --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Escribe los cambios. Sin esto solo cuenta.")

    def handle(self, *args, **options):
        apply = options["apply"]
        totals = services.resettle_all(apply=apply)
        changed = totals["inscrita"] + totals[services.AGE] + totals[services.CONSENT]
        self.stdout.write(f"{'Aplicado' if apply else 'Simulación (usa --apply para escribir)'}:")
        self.stdout.write(f"  eventos revisados: {totals['events']}")
        self.stdout.write(f"  inscripciones revisadas: {totals['reviewed']}")
        self.stdout.write(f"  {'cambiaron' if apply else 'cambiarían'}: {changed}")
        self.stdout.write(f"    a pendiente de confirmar la edad: {totals[services.AGE]}")
        self.stdout.write(f"    a pendiente de autorización: {totals[services.CONSENT]}")
        self.stdout.write(f"    a confirmada: {totals['inscrita']}")
