"""Switch a module on or off for one church (decision 0004). There is no screen for this yet:
turning a module on for a real church is an operator's decision, made here and audited.

    manage.py set_church_module <church-slug> community on
    manage.py set_church_module <church-slug> people off

Turning a module on also runs the people backfill for that church (one person per account that
holds a church role there), so its staff can open the new screens. Nothing is deleted when a
module is turned off: its screens answer 404 and its data stays.
"""

from django.core.management.base import BaseCommand, CommandError

from core.models import Church, UserChurchRole
from people import flags
from people.audit import record_church
from people.services import ensure_own_person


class Command(BaseCommand):
    help = "Enciende o apaga un módulo (people, community) para una iglesia: set_church_module <identificador> <módulo> on|off"

    def add_arguments(self, parser):
        parser.add_argument("church")
        parser.add_argument("module", choices=sorted(flags.MODULES))
        parser.add_argument("state", choices=["on", "off"])

    def handle(self, *args, **options):
        church = Church.objects.filter(slug=options["church"]).first()
        if church is None:
            raise CommandError(f"No hay una iglesia con el identificador {options['church']!r}.")
        module, value = options["module"], options["state"] == "on"
        changed = flags.set_enabled(church, module, value)
        if changed:
            record_church(church, None, "modulo_cambiado", f"Módulo {module}: {'encendido' if value else 'apagado'}",
                          module=module, value=value)
        created = 0
        if value:
            for role in UserChurchRole.objects.filter(church=church).select_related("user"):
                before = role.user.persons.filter(church=church).exists()
                if ensure_own_person(role.user, church) is not None and not before:
                    created += 1
        state = "encendido" if value else "apagado"
        self.stdout.write(self.style.SUCCESS(
            f"{church.slug}: módulo {module} {state}{'' if changed else ' (ya lo estaba)'}. Personas creadas para cuentas con rol: {created}."
        ))
