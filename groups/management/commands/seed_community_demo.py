"""Synthetic community data on the demo church, to try "Mi iglesia" by hand and in the browser gate. Never for production.

On ``iglesia-demo`` (created if missing; ``seed_camp_demo`` uses the same church): both modules on, a finished camp
of last year with a few camperos and no answers, three groups, and four accounts that tell the camp-to-group story:

* a former campero with an account and no group yet (she can ask to join one),
* a former campero who already asked to join (the request waits for the guide),
* the guide of a group, who holds nothing else anywhere,
* the church's administrator.

Idempotent: every object is looked up by a fixed key. The password comes from --password or EMMAUS_DEMO_PASSWORD.
"""

import os
from datetime import date, time

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Church, Role
from core.services import assign_role
from events.models import Event, EventParticipant, Sede
from groups import services as group_services
from groups.models import Group, GroupJoinRequest, GroupMembership
from people import flags
from people import services as people_services
from people.models import EventParticipantLink, Person

CHURCH_SLUG = "iglesia-demo"
EVENT_SLUG = "jovenes-2025"
ADMIN = ("admin.comunidad@demo.invalid", "Alma", "Cisneros Rey")
LEADER = ("guia.grupo@demo.invalid", "Gabriel", "Ortega Luna")
MEMBER = ("valeria.soto@demo.invalid", "Valeria", "Soto Marín")
ASKED = ("emilio.vargas@demo.invalid", "Emilio", "Vargas Peña")
CAMPEROS = [
    ("Valeria Soto Marín", 17), ("Emilio Vargas Peña", 18), ("Renata Aguilar Cruz", 15), ("Santiago Mora Díaz", 16),
    ("Ximena Robles Vega", 14), ("Leonardo Pineda Sol", 19), ("Camila Fuentes Ríos", 16), ("Matías Ochoa Lara", 15),
]
GROUPS = [
    {"name": "Jóvenes Roca", "kind": Group.Kind.SMALL, "audience": "Jóvenes de 15 a 19 años", "meeting_day": 3,
     "meeting_time": time(19, 30), "meeting_place": "Salón 2 de la iglesia",
     "description": "Nos juntamos a platicar de la vida y de la fe, con botana. Puedes llegar aunque no conozcas a nadie."},
    {"name": "Estudio de Romanos", "kind": Group.Kind.STUDY, "audience": "Quien quiera leer la Biblia con calma", "meeting_day": 1,
     "meeting_time": time(20, 0), "meeting_place": "Casa de la familia Ortega", "accepts_requests": False,
     "description": "Leemos un capítulo por semana y lo conversamos. Este ciclo ya empezó; abrimos lugares en enero."},
    {"name": "Recién llegados", "kind": Group.Kind.SMALL, "audience": "Quienes llevan poco en la iglesia", "meeting_day": 6,
     "meeting_time": time(13, 0), "meeting_place": "Cafetería, después de la reunión",
     "description": "Un café después de la reunión del domingo para conocernos y resolver dudas."},
]


class Command(BaseCommand):
    help = "Crea datos sintéticos de comunidad (grupos, personas, un campamento terminado) en la iglesia de demostración (solo con DEBUG=true)."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="", help="Contraseña de las cuentas (o EMMAUS_DEMO_PASSWORD). Nunca se muestra.")

    def handle(self, *args, **opts):
        if not settings.DEBUG:
            raise CommandError("Este comando solo funciona con DEBUG=true. Nunca uses datos de demostración en producción.")
        password = opts["password"] or os.environ.get("EMMAUS_DEMO_PASSWORD", "")
        if not password:
            raise CommandError("Indica la contraseña con --password o con la variable EMMAUS_DEMO_PASSWORD.")
        with transaction.atomic():
            church, _ = Church.objects.get_or_create(slug=CHURCH_SLUG, defaults={"name": "Iglesia de demostración"})
            flags.set_enabled(church, "people", True)
            flags.set_enabled(church, "community", True)
            admin = self._account(*ADMIN, password)
            assign_role(admin, church, Role.CHURCH_ADMIN)
            people_services.ensure_own_person(admin, church)
            event = self._event(church)
            groups = [self._group(church, admin, spec) for spec in GROUPS]
            leader = self._person(church, *LEADER, password)
            member = self._person(church, *MEMBER, password)
            asked = self._person(church, *ASKED, password)
            for person, name in ((member, CAMPEROS[0][0]), (asked, CAMPEROS[1][0])):
                campero = EventParticipant.objects.get(event=event, full_name=name)
                if not EventParticipantLink.objects.filter(participant=campero).exists():
                    people_services.link_participant(person, campero, admin)
            for group in groups[:2]:
                if not GroupMembership.objects.filter(group=group, person=leader, status="activa").exists():
                    group_services.set_membership(group, leader, admin, role=GroupMembership.Role.LEADER)
            if not GroupJoinRequest.objects.filter(group=groups[0], person=asked).exists():
                group_services.request_to_join(groups[0], asked, asked.user)
        self.stdout.write(self.style.SUCCESS(f"Listo: comunidad de demostración en {church.name}."))
        for label, (email, *_rest) in (("Administración", ADMIN), ("Guía de un grupo", LEADER),
                                        ("Excampera sin grupo", MEMBER), ("Excampero con solicitud", ASKED)):
            self.stdout.write(f"  {label:<26} {email}")

    def _account(self, email, first, last, password) -> User:
        user, _ = User.objects.get_or_create(username=email, defaults={"email": email})
        user.first_name, user.last_name, user.email, user.is_active = first, last, email, True
        user.set_password(password)
        user.save()
        return user

    def _person(self, church, email, first, last, password) -> Person:
        user = self._account(email, first, last, password)
        person, _ = Person.objects.get_or_create(
            church=church, user=user, defaults={"first_name": first, "last_name": last, "email": email, "phone": "55 5550 0100"}
        )
        return person

    def _event(self, church) -> Event:
        event, created = Event.objects.get_or_create(
            slug=EVENT_SLUG,
            defaults={
                "church": church, "name": "Campamento de Jóvenes 2025", "location": "Rancho El Encino, Valle de Bravo",
                "starts_on": date(2025, 11, 14), "ends_on": date(2025, 11, 16), "status": Event.Status.CLOSED,
                "reports_enabled": False,
            },
        )
        if created:
            sede = Sede.objects.filter(church=church, active=True).first()
            for number, (name, age) in enumerate(CAMPEROS, start=1):
                EventParticipant.objects.create(
                    event=event, number=number, full_name=name, age=age, room=str(1 + number % 3), church_group="Jóvenes", sede=sede
                )
        return event

    def _group(self, church, admin, spec) -> Group:
        group = Group.objects.filter(church=church, name=spec["name"]).first()
        if group is None:
            group = group_services.save_group(Group(church=church, **spec), admin, created=True)
        return group
