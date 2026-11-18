"""Synthetic community data on the demo church, to try "Mi iglesia" by hand and in the browser gate. Never for production.

On ``iglesia-demo`` (created if missing; ``seed_camp_demo`` uses the same church): both modules on, a finished camp
of last year with a few camperos and no answers, three groups, and four accounts that tell the camp-to-group story:

* a former campero with an account and no group yet (she can ask to join one),
* a former campero who already asked to join (the request waits for the guide),
* the guide of a group, who holds nothing else anywhere,
* the church's administrator.

With ``--panorama`` it also adds what the pastor's page counts: a pastor who is not an administrator, people in
five groups that show every state of the page (a weekly group up to date, a weekly group with two meetings
without a list, one that meets every two weeks, a monthly one where nothing was due, and a group of two people,
under the minimum reporting size) and a finished event with sign-ups and attendance. It is opt-in because the browser gate (``e2e/community-flow.mjs``) counts on the
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
# --panorama: people without an account, and the groups that show every state of the pastor's page. Each row: the
# group (one of GROUPS by name, or a new one with its own fields), which people are in it, how many came to each of
# its last due meetings (oldest first) and how many of the most recent due meetings were left without a list.
PANORAMA_PEOPLE = [
    ("Hugo", "Paredes Lima"), ("Itzel", "Carmona Ruiz"), ("Joaquín", "Zavala Ortiz"), ("Karla", "Montes Ibarra"),
    ("Lorenzo", "Beltrán Soto"), ("Mariana", "Quiroz Peña"), ("Noé", "Gallardo Cruz"), ("Olivia", "Rangel Mota"),
    ("Pablo", "Serrano Gil"), ("Regina", "Tovar Luna"), ("Saúl", "Ibáñez Cano"), ("Tania", "Villegas Rey"),
    ("Ulises", "Corona Paz"), ("Victoria", "Nava Solís"), ("Wendy", "Arriaga Ríos"), ("Yahir", "Bustos León"),
]
PANORAMA_GROUPS = [
    # Weekly and up to date.
    {"name": "Estudio de Romanos", "people": slice(0, 4), "came": [3, 4, 2, 4, 3, 4], "unrecorded": 0},
    # Weekly, with the last two due meetings without a list.
    {"name": "Recién llegados", "people": slice(4, 9), "came": [4, 3, 5, 2], "unrecorded": 2},
    # Every two weeks, up to date.
    {"name": "Matrimonios jóvenes", "people": slice(9, 15), "came": [5, 6, 4], "unrecorded": 0,
     "group": {"kind": Group.Kind.SMALL, "audience": "Matrimonios en sus primeros años", "meeting_day": 4,
               "meeting_frequency": Group.Frequency.BIWEEKLY, "meeting_time": time(20, 0), "meeting_place": "Casa de una de las parejas",
               "description": "Cenamos juntos cada dos semanas y platicamos de la vida en pareja."}},
    # Once a month: between meetings nothing is due, so nothing is missing.
    {"name": "Oración por la ciudad", "people": slice(2, 8), "came": [4, 5], "unrecorded": 0,
     "group": {"kind": Group.Kind.OTHER, "audience": "Toda la iglesia", "meeting_day": 5, "meeting_week": Group.Week.FIRST,
               "meeting_frequency": Group.Frequency.MONTHLY, "meeting_time": time(8, 0), "meeting_place": "Templo principal",
               "description": "El primer sábado de cada mes oramos por nuestra ciudad."}},
    # Two people and no fixed day: under the minimum reporting size, so the page shows no attendance for it.
    {"name": "Lectura en pareja", "people": slice(14, 16), "came": [1, 2], "unrecorded": 0, "days_ago": [16, 6],
     "group": {"kind": Group.Kind.STUDY, "audience": "Quien quiera leer con alguien más", "meeting_frequency": Group.Frequency.VARIABLE,
               "meeting_place": "Donde acuerden", "accepts_requests": False,
               "description": "Dos personas leen un libro de la Biblia y se llaman cuando pueden."}},
]
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
        parser.add_argument("--eventos", action="store_true", help="Agrega un evento sencillo de hoy con programa, preguntas, visitas y menores, un aviso importante y un evento terminado con opiniones.")
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
            if opts["eventos"]:
                self._events_extras(church, admin, member)
        self.stdout.write(self.style.SUCCESS(f"Listo: comunidad de demostración en {church.name}."))
        accounts = [("Administración", ADMIN), ("Guía de un grupo", LEADER), ("Excampera sin grupo", MEMBER), ("Excampero con solicitud", ASKED)]
        if opts["panorama"]:
            accounts.append(("Pastor (Panorama)", PASTOR))
        for label, (email, *_rest) in accounts:
            self.stdout.write(f"  {label:<26} {email}")

    def _events_extras(self, church, admin, member) -> None:
        """A simple event that starts today (programme, questions, a visitor, two minors), an important notice and
        an event that ended with opinions; the member can still give theirs."""
        from agenda import services as agenda
        from agenda.models import EventPage, EventRegistration
        from agenda.simple import create_simple_event
        from feedback.models import EventFeedback
        from notices import services as notices

        today = timezone.localdate()
        event = Event.objects.filter(church=church, name="Convivencia de jóvenes").first()
        if event is None:
            event = create_simple_event(church, admin, name="Convivencia de jóvenes", slug="convivencia-de-jovenes-demo",
                                        kind=Event.Kind.OTHER, location="Salón principal", starts_on=today,
                                        ends_on=today + timedelta(days=1))
            agenda.save_page(event, admin, published=True, registration="abierta", capacity=40, starts_at=time(17, 0),
                             description="Dos tardes para conocernos, comer juntos y cantar.", requires_guardian_consent=True)
            agenda.save_faq(event, admin, question="¿Tiene costo?", answer="No. Si quieres, trae algo para compartir.", position=1)
            agenda.save_faq(event, admin, question="¿Puedo llevar a un amigo?", answer="Sí. Al llegar lo registramos en la entrada.", position=2)
            for day, start, title, place in (
                (today, time(17, 0), "Bienvenida", "Entrada"), (today, time(17, 30), "Juegos", "Patio"),
                (today, time(19, 0), "Cena", "Salón principal"), (today + timedelta(days=1), time(18, 0), "Alabanza y mensaje", "Templo"),
            ):
                agenda.save_schedule_item(event, admin, day=day, starts_at=start, title=title, place=place)
            agenda.register_walkup(event, admin, first_name="Visita", last_name="Adulta Demo", phone="55 5550 0101")
            agenda.register_walkup(event, admin, first_name="Visita", last_name="Menor Pendiente", is_minor=True)
            agenda.register_walkup(event, admin, first_name="Visita", last_name="Menor Autorizada", is_minor=True, consent={
                "guardian_name": "Tutora Demo", "relationship": "madre", "method": "papel"})
            notices.publish(church, admin, title="Cambio de horario", priority="important",
                            body="La reunión de jóvenes comenzará a las 6:00 p. m. en lugar de las 5:00 p. m.")
        day = today - timedelta(days=5)
        ended, created = Event.objects.get_or_create(
            slug="cena-de-matrimonios-demo",
            defaults={"church": church, "name": "Cena de matrimonios", "kind": Event.Kind.OTHER, "location": "Salón principal",
                      "starts_on": day, "ends_on": day, "camp_tools": False, "join_by_name": False},
        )
        if created:
            EventPage.objects.create(event=ended, published=True, registration="abierta", created_by=admin)
            EventRegistration.objects.create(event=ended, person=member, attended=True)
            for rating, recommend, comment in (("muy_bien", "si", ""), ("bien", "si", "Empezar a tiempo."), ("regular", "tal_vez", "Más sillas.")):
                EventFeedback.objects.create(event=ended, rating=rating, recommend=recommend, comment=comment)

    def _panorama(self, church, admin, password) -> None:
        """What "Panorama" counts: a pastor, the groups of PANORAMA_GROUPS with their lists, a finished event with
        attendance."""
        from agenda.models import EventPage, EventRegistration
        from groups.models import GroupMeeting

        pastor = self._account(*PASTOR, password)
        assign_role(pastor, church, Role.PASTOR)
        people_services.ensure_own_person(pastor, church)
        today = timezone.localdate()
        everybody = [
            Person.objects.get_or_create(church=church, first_name=first, last_name=last, user=None)[0] for first, last in PANORAMA_PEOPLE
        ]
        for spec in PANORAMA_GROUPS:
            group = Group.objects.filter(church=church, name=spec["name"]).first()
            if group is None:
                group = Group(church=church, name=spec["name"], **spec["group"])
                if group.meeting_frequency == Group.Frequency.BIWEEKLY:
                    group.meeting_anchor = today - timedelta(days=(today.weekday() - group.meeting_day) % 7)
                group = group_services.save_group(group, admin, created=True)
            people = everybody[spec["people"]]
            for person in people:
                if not GroupMembership.objects.filter(group=group, person=person, status="activa").exists():
                    group_services.set_membership(group, person, admin)
            if "days_ago" in spec:  # no fixed day: the lists are of the days it says
                days = [today - timedelta(days=ago) for ago in spec["days_ago"]]
            else:  # the last due meetings by the group's rhythm, oldest first, without the ones left unrecorded
                days, day = [], group.last_meeting_day(today)
                while len(days) < len(spec["came"]) + spec["unrecorded"]:
                    days.insert(0, day)
                    day = group.last_meeting_day(day - timedelta(days=1))
                days = days[:len(spec["came"])]
            # The group existed before its first list: a new group is never asked for a list from before it was created.
            Group.objects.filter(pk=group.pk, created_at__date__gte=days[0]).update(created_at=timezone.now() - timedelta(days=(today - days[0]).days + 7))
            for day, came in zip(days, spec["came"]):
                if not GroupMeeting.objects.filter(group=group, date=day).exists():
                    group_services.record_meeting(group, day, [person.pk for person in people[:came]], admin)
        day = today - timedelta(days=23)
        event, _ = Event.objects.get_or_create(
            slug="retiro-de-familias-demo",
            defaults={"church": church, "name": "Retiro de familias", "kind": Event.Kind.OTHER, "location": "Templo principal",
                      "starts_on": day, "ends_on": day, "reports_enabled": False},
        )
        EventPage.objects.get_or_create(event=event, defaults={"published": True, "registration": "abierta", "capacity": 30, "created_by": admin})
        for index, person in enumerate(everybody[:12]):
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
