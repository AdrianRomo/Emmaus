"""Compare ``access.RoleAssignment`` with its sources and, with ``--apply``, make it equal (decision 0003, rule 5).

The projection is written by the services of its sources; this command is the proof and the repair.
Sources: ``groups.GroupMembership`` (scope ``group``) and ``serving.TeamMembership`` (scope
``ministry``, decision 0016). It prints counts only, never a name.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from access.models import RoleAssignment
from groups import services as groups
from serving import services as serving

SOURCES = (
    (groups.SOURCE, groups.expected_assignments),
    (serving.SOURCE, serving.expected_assignments),
)


class Command(BaseCommand):
    help = (
        "Compara los roles por ámbito con las tablas de las que salen (integrantes de grupos y de equipos de "
        "servicio). Sin opciones solo muestra las diferencias; --apply las corrige; --check termina con error si hay alguna."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Corregir las diferencias.")
        parser.add_argument("--check", action="store_true", help="Terminar con código 1 si hay diferencias.")

    def handle(self, *args, **options):
        differences = 0
        with transaction.atomic():
            for source, expected_assignments in SOURCES:
                differences += self.compare(source, expected_assignments(), options["apply"])
            # A row of a source nobody declares answers nothing and has nothing to be compared with.
            unknown = RoleAssignment.objects.exclude(source_table__in=[source for source, _ in SOURCES])
            orphans = unknown.count()
            if orphans:
                self.stdout.write(f"de una fuente desconocida, por borrar: {orphans}")
                differences += orphans
                if options["apply"]:
                    unknown.delete()
        if options["apply"]:
            self.stdout.write(self.style.SUCCESS(f"Listo. Se corrigieron {differences} diferencia(s)."))
        elif differences:
            self.stdout.write(self.style.WARNING(f"Hay {differences} diferencia(s). Para corregirlas: --apply"))
            if options["check"]:
                raise SystemExit(1)
        else:
            self.stdout.write(self.style.SUCCESS("Sin diferencias."))

    def compare(self, source: str, expected: dict, apply: bool) -> int:
        current = {row.source_id: row for row in RoleAssignment.objects.filter(source_table=source)}
        create = [pk for pk in expected if pk not in current]
        delete = [pk for pk in current if pk not in expected]
        update = [
            pk for pk, fields in expected.items()
            if pk in current and any(getattr(current[pk], name) != value for name, value in fields.items())
        ]
        if apply:
            for pk in create:
                RoleAssignment.objects.create(source_table=source, source_id=pk, **expected[pk])
            for pk in update:
                RoleAssignment.objects.filter(pk=current[pk].pk).update(**expected[pk])
            RoleAssignment.objects.filter(source_table=source, source_id__in=delete).delete()
        self.stdout.write(f"{source}: {len(expected)} esperados")
        self.stdout.write(f"  sin cambios: {len(expected) - len(create) - len(update)}")
        self.stdout.write(f"  por crear: {len(create)}")
        self.stdout.write(f"  por actualizar: {len(update)}")
        self.stdout.write(f"  por borrar: {len(delete)}")
        return len(create) + len(update) + len(delete)
