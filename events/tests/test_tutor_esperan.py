"""The tutor's home (redesign, ui_v2): what is the tutor's to decide («Esperan tu decisión»: follow-ups without a
responsible person) is never mixed with what waits on their coordinators («Esperan a sus coordinadores»: interviews
whose follow-up each coordinator decides)."""

from __future__ import annotations

from events.models import InterviewStatus
from people import flags

from .base import CampFixture
from .test_ux_coordinator import give_interview


class TutorDecisionsApartTests(CampFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        flags.set_enabled(cls.church, "community", True)

    def test_interviews_waiting_on_coordinators_are_not_the_tutor_s_decision(self):
        give_interview(self.p1, InterviewStatus.COMPLETED, decision="por_evaluar")
        self.client.force_login(self.tutor1)
        html = self.client.get(self.url("tutor_home")).content.decode().split("<main", 1)[1]
        self.assertIn(">Esperan a sus coordinadores</h2>", html)
        waiting = html.split(">Esperan a sus coordinadores</h2>", 1)[1]
        self.assertIn("por decidir</span>", waiting)
        self.assertNotIn("Su coordinador decide", html)
        before = html.split(">Esperan a sus coordinadores</h2>", 1)[0]
        self.assertNotIn("por decidir</span>", before)  # never under «Esperan tu decisión»
