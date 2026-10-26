from django.core.management.base import BaseCommand

from core.models import ParticipantProfile
from core.services import create_inactivity_follow_up


class Command(BaseCommand):
    help = "Crea solicitudes de seguimiento para participantes sin actividad durante 7 días o más."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="Días sin actividad antes de crear un seguimiento (7 si no lo indicas).",
        )

    def handle(self, *args, **options):
        created = 0
        for profile in ParticipantProfile.objects.select_related("user", "church", "assigned_mentor"):
            follow_up = create_inactivity_follow_up(profile, days=options["days"])
            if follow_up:
                created += 1
        if created == 1:
            message = "Se creó 1 solicitud de seguimiento por inactividad."
        else:
            message = f"Se crearon {created} solicitudes de seguimiento por inactividad."
        self.stdout.write(self.style.SUCCESS(message))
