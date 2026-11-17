"""Synthetic community data on the demo church, to try "Mi iglesia" by hand and in the browser gate. Never for production.

On ``iglesia-demo`` (created if missing; ``seed_camp_demo`` uses the same church): both modules on, a finished camp
of last year with a few camperos and no answers, three groups, and four accounts that tell the camp-to-group story:

* a former campero with an account and no group yet (she can ask to join one),
* a former campero who already asked to join (the request waits for the guide),
* the guide of a group, who holds nothing else anywhere,
* the church's administrator.

With ``--panorama`` it also adds what the pastor's page counts: a pastor who is not an administrator, people in
two groups, weeks of meetings with their lists (one group stopped recording five weeks ago) and a finished event
with sign-ups and attendance. It is opt-in because the browser gate (``e2e/community-flow.mjs``) counts on the
plain seed.

Idempotent: every object is looked up by a fixed key. The password comes from --password or EMMAUS_DEMO_PASSWORD.
"""

import os
from datetime import date, time, timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

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
PASTOR = ("pastor.iglesia@demo.invalid", "Ernesto", "Salas Mejía")
# --panorama: people without an account, and how many of each group came, oldest meeting first (weeks ago, how many).
PANORAMA_PEOPLE = [
    ("Hugo", "Paredes Lima"), ("Itzel", "Carmona Ruiz"), ("Joaquín", "Zavala Ortiz"), ("Karla", "Montes Ibarra"),
    ("Lorenzo", "Beltrán Soto"), ("Mariana", "Quiroz Peña"), ("Noé", "Gallardo Cruz"),
]
PANORAMA_MEETINGS = {
    "Estudio de Romanos": (PANORAMA_PEOPLE[:4], [(6, 3), (5, 4), (4, 2), (3, 4), (2, 3), (1, 4)]),
    "Recién llegados": (PANORAMA_PEOPLE[4:], [(7, 3), (6, 2), (5, 1)]),
}
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
        parser.add_argument("--panorama", action="store_true", help="Agrega un pastor, reuniones con lista y un evento terminado, para ver Panorama.")

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
            self._agenda_and_notices(church, admin, leader, groups[0])
            if opts["panorama"]:
                self._panorama(church, admin, password)
        self.stdout.write(self.style.SUCCESS(f"Listo: comunidad de demostración en {church.name}."))
        accounts = [("Administración", ADMIN), ("Guía de un grupo", LEADER), ("Excampera sin grupo", MEMBER), ("Excampero con solicitud", ASKED)]
        if opts["panorama"]:
            accounts.append(("Pastor (Panorama)", PASTOR))
        for label, (email, *_rest) in accounts:
            self.stdout.write(f"  {label:<26} {email}")

    def _panorama(self, church, admin, password) -> None:
        """What "Panorama" counts: a pastor, two groups with weeks of lists, a finished event with attendance."""
        from agenda.models import EventPage, EventRegistration
        from groups.models import GroupMeeting

        pastor = self._account(*PASTOR, password)
        assign_role(pastor, church, Role.PASTOR)
        people_services.ensure_own_person(pastor, church)
        today = timezone.localdate()
        everybody = []
        for name, (names, meetings) in PANORAMA_MEETINGS.items():
            group = Group.objects.get(church=church, name=name)
            people = [
                Person.objects.get_or_create(church=church, first_name=first, last_name=last, user=None)[0] for first, last in names
            ]
            everybody += people
            for person in people:
                if not GroupMembership.objects.filter(group=group, person=person, status="activa").exists():
                    group_services.set_membership(group, person, admin)
            for weeks, came in meetings:
                day = group.last_meeting_day(today) - timedelta(weeks=weeks)
                if not GroupMeeting.objects.filter(group=group, date=day).exists():
                    group_services.record_meeting(group, day, [person.pk for person in people[:came]], admin)
        day = today - timedelta(days=23)
        event, _ = Event.objects.get_or_create(
            slug="retiro-de-familias-demo",
            defaults={"church": church, "name": "Retiro de familias", "kind": Event.Kind.OTHER, "location": "Templo principal",
                      "starts_on": day, "ends_on": day, "reports_enabled": False},
        )
        EventPage.objects.get_or_create(event=event, defaults={"published": True, "registration": "abierta", "capacity": 30, "created_by": admin})
        for index, person in enumerate(everybody):
            EventRegistration.objects.get_or_create(event=event, person=person, defaults={"attended": index % 3 != 0})

    def _agenda_and_notices(self, church, admin, leader, group) -> None:
        """An event the church published with its sign-up open, and two notices: one for everybody, one for a group."""
        from agenda import services as agenda
        from notices import services as notices
        from notices.models import Announcement

        day = timezone.localdate() + timedelta(days=12)
        event, _ = Event.objects.get_or_create(
            slug="noche-de-alabanza-demo",
            defaults={"church": church, "name": "Noche de alabanza", "kind": Event.Kind.OTHER, "location": "Templo principal",
                      "starts_on": day, "ends_on": day, "reports_enabled": False},
        )
        Event.objects.filter(pk=event.pk).update(starts_on=day, ends_on=day, status=Event.Status.ACTIVE)
        event.refresh_from_db()
        agenda.save_page(
            event, admin, published=True, registration="abierta", capacity=40, starts_at=time(19, 0),
            description="Una noche para cantar y orar juntos. Trae a alguien de tu familia.",
        )
        from resources import services as resources
        from resources.models import Devotional, MediaItem

        if not Devotional.objects.filter(church=church, date=timezone.localdate()).exists():
            resources.save_devotional(
                church, admin, date=timezone.localdate(), title="Permanecer", passage="Juan 15:1-8", author_name="Alma Cisneros",
                body="Jesús no pide que demos fruto a la fuerza. Pide que permanezcamos en él.\n\nHoy, antes de empezar, quédate un minuto en silencio.",
                publish=True,
            )
        if not MediaItem.objects.filter(church=church).exists():
            resources.save_media(
                church, admin, kind="predicacion", title="La vid y las ramas", speaker="Ernesto Salas", series="Juan",
                date=timezone.localdate() - timedelta(days=2), url="https://www.youtube.com/watch?v=demo-emmaus", description="",
            )
        from care import services as care
        from care.models import FollowUp
        from serving import services as serving
        from serving.models import Team

        member = Person.objects.get(church=church, user__email=MEMBER[0])
        asked = Person.objects.get(church=church, user__email=ASKED[0])
        if not Team.objects.filter(church=church).exists():
            team = serving.save_team(church, admin, name="Bienvenida", description="Recibimos a quien llega a la reunión.")
            serving.set_member(team, leader, admin, role="coordina")
            serving.set_member(team, member, admin, role="sirve")
            sunday = timezone.localdate() + timedelta(days=5)
            service = serving.save_date(team, leader.user, date=sunday, what="Bienvenida en la reunión", starts_at=time(10, 30), place="Entrada principal")
            serving.assign(service, member, leader.user)
        if not FollowUp.objects.filter(church=church).exists():
            care.create(church, admin, person=asked, reason="grupo", owner=leader, due_on=timezone.localdate() + timedelta(days=3))
        if not Announcement.objects.filter(church=church).exists():
            notices.publish(church, admin, title="El domingo empezamos a las 11",
                            body="Por el cambio de horario, la reunión del domingo empieza a las 11 de la mañana.\n\nLlega unos minutos antes.")
            notices.publish(church, leader.user, group=group, title="Esta semana nos vemos en el Salón 3",
                            body="El Salón 2 estará ocupado. Nos vemos a la misma hora en el Salón 3.")

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
