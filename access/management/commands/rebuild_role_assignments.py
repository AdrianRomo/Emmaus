"""Compare ``access.RoleAssignment`` with its sources and, with ``--apply``, make it equal (decision 0003, rule 5).

The projection is written by the services of its sources; this command is the proof and the repair.
Sources today: ``groups.GroupMembership`` (scope ``group``). It prints counts only, never a name.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from access.models import RoleAssignment
from groups.services import SOURCE, expected_assignments


class Command(BaseCommand):
    help = (
        "Compara los roles por ámbito con las tablas de las que salen (hoy: integrantes de grupos). Sin opciones "
        "solo muestra las diferencias; --apply las corrige; --check termina con error si hay alguna."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Corregir las diferencias.")
        parser.add_argument("--check", action="store_true", help="Terminar con código 1 si hay diferencias.")

    def handle(self, *args, **options):
        with transaction.atomic():
            expected = expected_assignments()
            current = {row.source_id: row for row in RoleAssignment.objects.filter(source_table=SOURCE)}
            create = [pk for pk in expected if pk not in current]
            delete = [pk for pk in current if pk not in expected]
            update = [
                pk for pk, fields in expected.items()
                if pk in current and any(getattr(current[pk], name) != value for name, value in fields.items())
            ]
            if options["apply"]:
                for pk in create:
                    RoleAssignment.objects.create(source_table=SOURCE, source_id=pk, **expected[pk])
                for pk in update:
                    RoleAssignment.objects.filter(pk=current[pk].pk).update(**expected[pk])
                RoleAssignment.objects.filter(source_table=SOURCE, source_id__in=delete).delete()
        unchanged = len(expected) - len(create) - len(update)
        self.stdout.write(f"{SOURCE}: {len(expected)} esperados")
        self.stdout.write(f"  sin cambios: {unchanged}")
        self.stdout.write(f"  por crear: {len(create)}")
        self.stdout.write(f"  por actualizar: {len(update)}")
        self.stdout.write(f"  por borrar: {len(delete)}")
        differences = len(create) + len(update) + len(delete)
        if options["apply"]:
            self.stdout.write(self.style.SUCCESS(f"Listo. Se corrigieron {differences} diferencia(s)."))
        elif differences:
            self.stdout.write(self.style.WARNING(f"Hay {differences} diferencia(s). Para corregirlas: --apply"))
            if options["check"]:
                raise SystemExit(1)
        else:
            self.stdout.write(self.style.SUCCESS("Sin diferencias."))
