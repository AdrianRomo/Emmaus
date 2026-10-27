from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Church, Role
from core.services import assign_role
from events.models import (
    ConsentRecord,
    Event,
    EventMembership,
    EventParticipant,
    InterviewStation,
    MembershipRole,
    TutorAssignment,
)
from events.services.event_questionnaires import use_version
from events.services.questionnaire import DEFAULT_CAMP_QUESTIONNAIRE, build_steps, create_questionnaire_from_definition

PASSWORD = "clave-de-prueba-123"


def make_user(email: str, first: str = "Persona", last: str = "Prueba") -> User:
    return User.objects.create_user(username=email, email=email, password=PASSWORD, first_name=first, last_name=last)


class CampFixture(TestCase):
    """Two events. In event A: admin, pastor, care team, two tutors with two coordinators each."""

    @classmethod
    def setUpTestData(cls):
        cls.church = Church.objects.create(name="Iglesia de prueba", slug="iglesia-prueba")
        cls.version = create_questionnaire_from_definition(cls.church, DEFAULT_CAMP_QUESTIONNAIRE)
        cls.event = cls._event("campamento-a", "Campamento A")
        cls.other_event = cls._event("campamento-b", "Campamento B")

        cls.admin = make_user("admin@prueba.mx", "Ada", "Admin")
        assign_role(cls.admin, cls.church, Role.CHURCH_ADMIN)
        cls.pastor = make_user("pastor@prueba.mx", "Pablo", "Pastor")
        cls.care = make_user("cuidado@prueba.mx", "Celia", "Cuidado")
        cls.tutor1 = make_user("tutor1@prueba.mx", "Teresa", "Uno")
        cls.tutor2 = make_user("tutor2@prueba.mx", "Tomás", "Dos")
        cls.coord1 = make_user("coord1@prueba.mx", "Carla", "Uno")
        cls.coord2 = make_user("coord2@prueba.mx", "César", "Dos")
        cls.coord3 = make_user("coord3@prueba.mx", "Cora", "Tres")
        cls.outsider = make_user("externo@prueba.mx", "Óscar", "Externo")
        cls.other_coord = make_user("otro@prueba.mx", "Olga", "Otro")

        m = lambda user, role, **kw: EventMembership.objects.create(event=cls.event, user=user, role=role, **kw)  # noqa: E731
        cls.m_pastor = m(cls.pastor, MembershipRole.PASTOR)
        cls.m_care = m(cls.care, MembershipRole.PASTOR, can_view_sensitive=True)
        cls.m_tutor1 = m(cls.tutor1, MembershipRole.TUTOR, team_name="Equipo Norte")
        cls.m_tutor2 = m(cls.tutor2, MembershipRole.TUTOR, team_name="Equipo Sur")
        cls.m_coord1 = m(cls.coord1, MembershipRole.COORDINATOR)
        cls.m_coord2 = m(cls.coord2, MembershipRole.COORDINATOR)
        cls.m_coord3 = m(cls.coord3, MembershipRole.COORDINATOR)
        TutorAssignment.objects.create(event=cls.event, tutor=cls.m_tutor1, coordinator=cls.m_coord1)
        TutorAssignment.objects.create(event=cls.event, tutor=cls.m_tutor1, coordinator=cls.m_coord2)
        TutorAssignment.objects.create(event=cls.event, tutor=cls.m_tutor2, coordinator=cls.m_coord3)
        # The outsider is a coordinator in *another* event only.
        cls.m_other = EventMembership.objects.create(event=cls.other_event, user=cls.other_coord, role=MembershipRole.COORDINATOR)
        EventMembership.objects.create(event=cls.other_event, user=cls.outsider, role=MembershipRole.TUTOR)

        cls.p1 = cls._camper("Mariana López Ruiz", 15, cls.m_coord1, guardian=True)
        cls.p2 = cls._camper("Diego Hernández Cruz", 19, cls.m_coord1)
        cls.p3 = cls._camper("Sofía García Pérez", 14, cls.m_coord2, guardian=True)
        cls.p4 = cls._camper("Mateo Ramírez Díaz", 16, cls.m_coord3, guardian=True)
        cls.p_minor_no_auth = cls._camper("Lucía Torres Gómez", 13, cls.m_coord1, guardian=False)
        cls.p_other_event = EventParticipant.objects.create(
            event=cls.other_event, number=1, full_name="Persona Otro Evento", age=20, room="1", church_group="Otra", coordinator=cls.m_other
        )
        cls.steps = build_steps(cls.version)

    @classmethod
    def _event(cls, slug, name):
        event = Event.objects.create(
            church=cls.church,
            name=name,
            slug=slug,
            procedures_approved_at=timezone.now(),
            starts_on=timezone.localdate(),
            ends_on=timezone.localdate(),
        )
        use_version(event, cls.version)
        for n in range(1, 11):
            InterviewStation.objects.create(event=event, number=n, name=f"Computadora {n}")
        return event

    @classmethod
    def _camper(cls, name, age, coordinator, guardian=False):
        number = EventParticipant.objects.filter(event=cls.event).count() + 1
        participant = EventParticipant.objects.create(
            event=cls.event, number=number, full_name=name, age=age, room=str(number), church_group="Iglesia Centro", coordinator=coordinator
        )
        if guardian:
            ConsentRecord.objects.create(participant=participant, kind=ConsentRecord.Kind.GUARDIAN)
        return participant

    # ---- helpers --------------------------------------------------------------------------
    def login(self, user, client=None):
        client = client or self.client
        self.assertTrue(client.login(username=user.username, password=PASSWORD))
        return client

    def url(self, name, *args):
        return reverse(f"events:{name}", args=[self.event.slug, *args])

    def step(self, code):
        for step in self.steps:
            if step.question.code == code:
                return step
        raise AssertionError(code)

    def question(self, code):
        for step in self.steps:
            for q in step.questions:
                if q.code == code:
                    return q
        raise AssertionError(code)

    def choose_station(self, client=None, number=1):
        client = client or self.client
        station = self.event.stations.get(number=number)
        client.post(self.url("choose_station"), {"station": station.id, "siguiente": "/"})
        return station

    def start(self, participant, *, mode="asistida", client=None):
        client = client or self.client
        consents = {"consent_assent": "on", "consent_consent": "on", "consent_guardian": "on"}
        response = client.post(self.url("interview_start", participant.public_id), {"mode": mode, **consents})
        return response

    def answer(self, interview, code, values, client=None, direction="next", url_name="interview_step", extra=None):
        client = client or self.client
        interview.refresh_from_db()
        step = self.step(code)
        data = {"revision": interview.revision, "direction": direction, **(extra or {})}
        data[f"q_{step.question.id}"] = values
        if url_name == "kiosk":
            return client.post(reverse("kiosk:step", args=[step.number]), data)
        return client.post(self.url(url_name, interview.public_id, step.number), data)
