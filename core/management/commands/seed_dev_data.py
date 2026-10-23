import os
import secrets

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import Church, ParticipantProfile, Role
from core.services import assign_role, enroll_participant_in_path


PASSWORD_ENV_VAR = "EMMAUS_DEMO_PASSWORD"


class Command(BaseCommand):
    help = (
        "Crea datos de demostración solo para desarrollo local. "
        "No lo uses para configurar producción."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            help=(
                "Contraseña para todas las cuentas de demostración. También puedes usar la variable de "
                f"entorno {PASSWORD_ENV_VAR}. Si no indicas ninguna, se genera una al azar."
            ),
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "Este comando solo funciona en desarrollo (DEBUG=true). "
                "No crees datos de demostración en un servidor real."
            )
        password = options.get("password") or os.environ.get(PASSWORD_ENV_VAR, "")
        generated = not password
        if generated:
            password = secrets.token_urlsafe(9)

        church, _ = Church.objects.get_or_create(
            slug="emmaus-demo",
            defaults={
                "name": "Iglesia de demostración Emmaus",
                "contact_email": "hello@example.org",
                "public_welcome_text": "Te damos la bienvenida al espacio de demostración de Emmaus.",
            },
        )
        people = {
            "admin@example.org": ("Administración", "Demo", Role.CHURCH_ADMIN),
            "pastor@example.org": ("Pastor", "Demo", Role.PASTOR),
            "mentor@example.org": ("Mentor", "Demo", Role.MENTOR),
            "participant@example.org": ("Participante", "Demo", Role.PARTICIPANT),
        }
        users = {}
        for email, (first_name, last_name, role) in people.items():
            user, _ = User.objects.get_or_create(
                username=email,
                defaults={"email": email, "first_name": first_name, "last_name": last_name},
            )
            user.email = email
            user.first_name = first_name
            user.last_name = last_name
            user.set_password(password)
            user.is_staff = role in [Role.CHURCH_ADMIN, Role.PASTOR]
            user.is_active = True
            user.save()
            assign_role(user, church, role)
            users[role] = user
        call_command("create_starter_path", church_slug=church.slug, stdout=self.stdout)
        profile, _ = ParticipantProfile.objects.get_or_create(
            user=users[Role.PARTICIPANT],
            defaults={
                "church": church,
                "assigned_mentor": users[Role.MENTOR],
                "preferred_contact_method": ParticipantProfile.ContactMethod.EMAIL,
                "consent_accepted_at": timezone.now(),
                "emergency_disclaimer_accepted_at": timezone.now(),
            },
        )
        profile.assigned_mentor = users[Role.MENTOR]
        profile.save()
        enroll_participant_in_path(users[Role.PARTICIPANT], church.paths.filter(is_default=True).first())
        accounts = ", ".join(people)
        if generated:
            self.stdout.write(self.style.SUCCESS(f"Cuentas de demostración listas: {accounts}."))
            self.stdout.write(
                "Contraseña generada para todas las cuentas (solo se muestra esta vez, guárdala ahora): "
                f"{password}"
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Cuentas de demostración listas: {accounts}. Usan la contraseña que indicaste."
                )
            )
