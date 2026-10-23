"""Leader screens on "Rótulo": tutor home, coordinators, interviews, pending, team and the event's progress."""

import re
from datetime import timedelta

from django.test import Client
from django.utils import timezone

from events.models import FollowUp, Interview

from .base import CampFixture


class LeaderScreensTests(CampFixture):
    def pages(self):
        return {
            self.tutor1: ["tutor_home", "coordinators", ("coordinator_detail", self.m_coord1.id), "interviews", "pending"],
            self.pastor: ["progress", "team", "coordinators", ("coordinator_detail", self.m_coord1.id), "interviews", "pending"],
            self.admin: ["team", "progress", "coordinators", "interviews", "pending"],
        }

    def test_leader_screens_use_rotulo_with_one_heading(self):
        for user, names in self.pages().items():
            client = Client()
            self.login(user, client)
            for name in names:
                url = self.url(*name) if isinstance(name, tuple) else self.url(name)
                response = client.get(url)
                html = response.content.decode()
                self.assertContains(response, "css/rotulo.css", msg_prefix=url)
                self.assertNotContains(response, "css/emmaus.css", msg_prefix=url)
                self.assertEqual(len(re.findall(r"<h1\b", html)), 1, url)

    def stall(self, participant, station_number=1, minutes=30):
        self.login(self.coord1)
        self.choose_station(number=station_number)
        self.start(participant)
        Interview.objects.filter(participant=participant).update(last_activity_at=timezone.now() - timedelta(minutes=minutes))

    def test_tutor_sign_says_whose_interviews_stalled_and_where(self):
        self.stall(self.p1, station_number=4)
        self.login(self.tutor1)
        response = self.client.get(self.url("tutor_home"))
        html = response.content.decode()
        self.assertEqual(len(re.findall(r'class="sign[ "]', html)), 1)  # one sign, never two
        self.assertContains(response, "Carla Uno lleva 1 entrevista sin movimiento")
        self.assertContains(response, "sin actividad en Computadora 4")
        self.assertContains(response, self.url("pending") + "#interrumpidas")
        # The row for Carla says where she is and that one interview stalled, in words (not in red)
        self.assertContains(response, "En Computadora 4")
        self.assertContains(response, "1 entrevista sin actividad desde hace 20 minutos o más")
        self.assertNotContains(response, "chip--alert")

    def test_tutor_sign_names_the_computer_of_the_stalled_interview(self):
        """Not where the coordinator went next: the stalled interview was left at Computadora 8."""
        self.stall(self.p1, station_number=8)
        self.login(self.coord1)
        self.choose_station(number=3)
        self.start(self.p2)  # a newer, active interview at another computer
        self.login(self.tutor1)
        response = self.client.get(self.url("tutor_home"))
        self.assertContains(response, "sin actividad en Computadora 8")
        self.assertNotContains(response, "sin actividad en Computadora 3")

    def test_tutor_sign_counts_every_stalled_coordinator(self):
        self.stall(self.p1)
        self.login(self.coord2)
        self.choose_station(number=2)
        self.start(self.p3)
        Interview.objects.filter(participant=self.p3).update(last_activity_at=timezone.now() - timedelta(minutes=45))
        self.login(self.tutor1)
        response = self.client.get(self.url("tutor_home"))
        self.assertContains(response, "2 entrevistas sin movimiento")
        self.assertContains(response, "Carla Uno lleva 1 y César Dos lleva 1.")

    def test_tutor_home_has_no_sign_while_nothing_is_stalled(self):
        self.login(self.tutor1)
        response = self.client.get(self.url("tutor_home"))
        self.assertNotRegex(response.content.decode(), r'class="sign[ "]')
        self.assertContains(response, "Equipo Norte, con Teresa.")
        self.assertContains(response, "Tu equipo lleva 0 de 4.")
        self.assertContains(response, "Sin computadora registrada")
        # The live region keeps its hooks and the status sits outside it
        html = response.content.decode()
        self.assertIn('data-live-status="estado-actualizacion"', html)
        self.assertLess(html.index('id="estado-actualizacion"'), html.index('id="equipo-vivo"'))

    def test_tutor_home_lists_team_follow_ups_but_never_confidential_ones(self):
        FollowUp.objects.create(event=self.event, participant=self.p2, reason=FollowUp.Reason.TALK_TO_LEADER)
        FollowUp.objects.create(event=self.event, participant=self.p3, confidential=True, created_by=self.care)
        FollowUp.objects.create(event=self.event, participant=self.p_minor_no_auth, kind=FollowUp.Kind.SAFETY)
        self.login(self.tutor1)
        response = self.client.get(self.url("tutor_home"))
        self.assertContains(response, "Diego Hernández Cruz")
        self.assertContains(response, "Pidió hablar con un líder")
        self.assertNotContains(response, "Sofía García Pérez")  # confidential: care team only
        self.assertNotContains(response, "Lucía Torres Gómez")  # risk reports are not on the tutor's home

    def test_coordinators_page_draws_one_mark_per_campero(self):
        self.login(self.tutor1)
        response = self.client.get(self.url("coordinators"))
        html = response.content.decode()
        self.assertEqual(html.count('class="tally"'), 2)
        self.assertContains(response, self.url("coordinator_detail", self.m_coord1.id))
        self.assertNotContains(response, "Cora Tres")  # another tutor's coordinator

    def test_tutor_without_coordinators_sees_an_empty_line(self):
        from events.models import EventMembership, MembershipRole

        from .base import make_user

        lone = make_user("tutor3@prueba.mx", "Tania", "Tres")
        EventMembership.objects.create(event=self.event, user=lone, role=MembershipRole.TUTOR)
        self.login(lone)
        response = self.client.get(self.url("tutor_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hola, Tania.")  # no team name yet: no "Equipo de Tania, con Tania."
        self.assertContains(response, "Tu equipo todavía no tiene camperos.")
        self.assertContains(response, "Aún no tienes coordinadores asignados")

    def test_coordinator_without_tutor_and_team_page_permissions(self):
        from events.models import TutorAssignment

        TutorAssignment.objects.filter(coordinator=self.m_coord3).delete()
        self.login(self.pastor)
        self.assertContains(self.client.get(self.url("coordinator_detail", self.m_coord3.id)), "Sin tutor")
        team = self.client.get(self.url("team"))
        self.assertContains(team, "Coordinadores sin tutor")
        # Only the administration manages the team
        self.assertNotContains(team, "Agregar persona")
        self.assertNotContains(team, "Asignar tutor")
        self.login(self.admin)
        team = self.client.get(self.url("team"))
        self.assertContains(team, "Agregar persona")
        self.assertContains(team, self.url("member_edit", self.m_coord3.id))
