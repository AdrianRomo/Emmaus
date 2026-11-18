"""A camper's record (redesign, ui_v2): what comes first when a minor's guardian authorization is missing.

Registering the authorization is the one filled action; starting the interview stays (its consent step asks to
confirm the signed authorization) but is drawn as a secondary action. Erasing the record has its own section at the
foot, apart from reporting a risk. With the module off nothing changes.
"""

from __future__ import annotations

import re

from events.models import InterviewStatus
from people import flags
from people.models import Person

from .base import CampFixture
from .test_ux_coordinator import give_interview


def main_of(response) -> str:
    return response.content.decode().split("<main", 1)[1]


def filled(page: str) -> list[str]:
    return re.findall(r'<a class="btn btn--primary[^"]*" href="[^"]*">[^<]*</a>', page)


class MissingAuthorizationComesFirstTests(CampFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        flags.set_enabled(cls.church, "community", True)
        Person.objects.create(church=cls.church, user=cls.admin, first_name=cls.admin.first_name, last_name=cls.admin.last_name)

    def record(self, participant):
        self.client.force_login(self.admin)
        return main_of(self.client.get(self.url("participant_detail", participant.public_id)))

    def test_a_blocked_minor_has_one_filled_action_and_it_registers_the_authorization(self):
        page = self.record(self.p_minor_no_auth)
        buttons = filled(page)
        self.assertEqual(len(buttons), 1, buttons)
        self.assertIn(f'href="{self.url("guardian_authorization", self.p_minor_no_auth.public_id)}">Marcar como recibida</a>', buttons[0])
        start = self.url("interview_start", self.p_minor_no_auth.public_id)
        self.assertIn(f'<a class="btn btn--secondary" href="{start}">Iniciar entrevista</a>', page)  # still there, not first

    def test_an_authorized_minor_or_an_adult_starts_the_interview_first(self):
        for participant in (self.p1, self.p2):
            with self.subTest(participant=participant.full_name):
                page = self.record(participant)
                self.assertIn(f'<a class="btn btn--primary" href="{self.url("interview_start", participant.public_id)}">Iniciar entrevista</a>', page)
                self.assertNotIn("Marcar como recibida", page)

    def test_an_interview_in_progress_keeps_resuming_first(self):
        give_interview(self.p_minor_no_auth, InterviewStatus.IN_PROGRESS)
        page = self.record(self.p_minor_no_auth)
        self.assertNotIn('btn--primary btn--small" href="' + self.url("guardian_authorization", self.p_minor_no_auth.public_id), page)

    def test_erasing_has_its_own_section_after_reporting_a_risk(self):
        page = self.record(self.p_minor_no_auth)
        risk = page.index("Reportar una situación de riesgo")
        erase = page.index('<h2 id="eliminar" class="heading">Eliminar sus datos</h2>')
        self.assertLess(risk, erase)
        self.assertIn("No se puede deshacer", page[erase:])
        self.assertEqual(page.count(f'href="{self.url("participant_delete", self.p_minor_no_auth.public_id)}"'), 1)

    def test_with_the_module_off_the_record_is_unchanged(self):
        flags.set_enabled(self.church, "community", False)
        page = self.record(self.p_minor_no_auth)
        self.assertIn(f'<a class="btn btn--primary" href="{self.url("interview_start", self.p_minor_no_auth.public_id)}">Iniciar entrevista</a>', page)
        self.assertNotIn('id="eliminar"', page)
