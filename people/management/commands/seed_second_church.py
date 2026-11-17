"""A second synthetic church ("Iglesia B") for trying tenant isolation by hand. Never for production.

Idempotent: every object is looked up by a fixed key and created only when missing; a second run creates nothing
and only resets the three accounts (names, password, church role). The password comes from --password or
EMMAUS_DEMO_PASSWORD and is never printed. ``people/tests/test_tenant_isolation.py`` builds its own church B and does not depend on this command.
"""

import os
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import Church, Role, UserChurchRole
from core.services import assign_role
from events.models import Event, EventMembership, EventParticipant, MembershipRole, QuestionnaireVersion, Sede
from events.services.event_questionnaires import use_version
from events.services.questionnaire import DEFAULT_CAMP_QUESTIONNAIRE, create_questionnaire_from_definition
from people.models import Person

PASSWORD_ENV_VAR = "EMMAUS_DEMO_PASSWORD"
CHURCH_SLUG = "iglesia-b-demo"
CHURCH_NAME = "Iglesia B de demostración"
EVENT_SLUG = "campamento-b-2026"
# (label, email, first name, last name, church role or None)
ACCOUNTS = [
    ("Administración", "admin.iglesia-b@demo.invalid", "Beatriz", "Bravo Solís", Role.CHURCH_ADMIN),
    ("Pastor", "pastor.iglesia-b@demo.invalid", "Benito", "Barrera Luna", Role.PASTOR),
    ("Coordinador", "coordinador.iglesia-b@demo.invalid", "Brenda", "Bueno Paz", None),
]


class Command(BaseCommand):
    help = "Crea una segunda iglesia de demostración con datos sintéticos para probar el aislamiento (solo con DEBUG=true)."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="", help=f"Contraseña de las cuentas (o {PASSWORD_ENV_VAR}). Nunca se muestra.")

    def handle(self, *args, **opts):
        if not settings.DEBUG:
            raise CommandError("Este comando solo funciona con DEBUG=true. Nunca uses datos de demostración en producción.")
        password = opts["password"] or os.environ.get(PASSWORD_ENV_VAR, "")
        if not password:
            raise CommandError(f"Indica la contraseña de las cuentas con --password o con la variable {PASSWORD_ENV_VAR}. "
                               "Este comando no la genera ni la muestra.")
        lines = []
        with transaction.atomic():
            church, created = Church.objects.get_or_create(slug=CHURCH_SLUG, defaults={"name": CHURCH_NAME})
            lines.append(("Iglesia", f"{church.name} ({church.slug})", created))
            users = {}
            for label, email, first, last, role in ACCOUNTS:
                user, created = self._account(church, email, first, last, role, password)
                users[label] = user
                lines.append((f"Cuenta de {label.lower()}", email, created))
            event, created = self._event(church)
            lines.append(("Evento", f"{event.name} ({event.slug})", created))
            version, created = self._version(church, event, users["Administración"])
            lines.append(("Cuestionario publicado", f"{version}", created))
            sede, created = Sede.objects.get_or_create(
                church=church, slug="centro-b", defaults={"name": "Centro B", "city": "Puebla"}
            )
            lines.append(("Sede", sede.name, created))
            membership, created = EventMembership.objects.get_or_create(
                event=event, user=users["Coordinador"], defaults={"role": MembershipRole.COORDINATOR}
            )
            lines.append(("Coordinación en el evento", membership.display_name, created))
            campero, created = EventParticipant.objects.get_or_create(
                event=event, number=1,
                defaults={"full_name": "Bruno Beltrán Ochoa", "age": 21, "room": "1", "church_group": "Iglesia B Centro",
                          "coordinator": membership, "sede": sede},
            )
            lines.append(("Campero", campero.full_name, created))
            person, created = Person.objects.get_or_create(
                church=church, user=None, first_name="Berenice", last_name="Blanco Ruiz",
                defaults={"created_from": Person.CreatedFrom.MANUAL},
            )
            lines.append(("Persona", person.full_name, created))
        for label, value, created in lines:
            self.stdout.write(f"  {'Se creó' if created else 'Ya existía':<11} {label}: {value}")
        self.stdout.write(self.style.SUCCESS(
            f"Listo: {CHURCH_NAME}. Las cuentas usan la contraseña que indicaste; aquí no se muestra."
        ))

    # ---------------------------------------------------------------------------------
    def _account(self, church, email, first, last, role, password):
        user, created = User.objects.get_or_create(username=email, defaults={"email": email})
        foreign = UserChurchRole.objects.filter(user=user).exclude(church=church).exists()
        if user.is_superuser or foreign:
            # Reusing it would join church B to another church through one account: the opposite of the demo.
            raise CommandError(f"La cuenta {email} es de otra iglesia o es superusuario. No la uso para la iglesia B.")
        user.first_name, user.last_name, user.email, user.is_active = first, last, email, True
        user.is_staff = role in (Role.CHURCH_ADMIN, Role.PASTOR)  # as invitations and seed_dev_data do today
        user.set_password(password)
        user.save()
        if role:
            assign_role(user, church, role)
        return user, created

    def _event(self, church):
        today = timezone.localdate()
        event, created = Event.objects.get_or_create(
            slug=EVENT_SLUG,
            defaults={"church": church, "name": "Campamento Iglesia B 2026", "location": "Campamento El Roble",
                      "starts_on": today, "ends_on": today + timedelta(days=1), "procedures_approved_at": timezone.now()},
        )
        if event.church_id != church.id:
            raise CommandError(f"Ya existe el evento {EVENT_SLUG} en otra iglesia. No lo modifico.")
        return event, created

    def _version(self, church, event, admin):
        published = QuestionnaireVersion.objects.filter(
            questionnaire__church=church, questionnaire__name=DEFAULT_CAMP_QUESTIONNAIRE["name"],
            status=QuestionnaireVersion.Status.PUBLISHED,
        )
        version = published.order_by("-number").first()
        created = version is None
        if created:
            version = create_questionnaire_from_definition(church, DEFAULT_CAMP_QUESTIONNAIRE, admin)
        use_version(event, version)  # no change when the event already uses it
        return version, created
