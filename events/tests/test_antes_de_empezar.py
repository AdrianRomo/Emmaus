"""«Antes de empezar» (owner decision): the required checks of an interview live here and only here — the identity
(«¿Es la persona correcta?»: «Sí, es {nombre}») and the applicable authorizations; no question of the interview is
required. The server refuses to start without them, in both modes."""

from __future__ import annotations

from events.models import Interview
from people import flags

from .base import CampFixture


class IdentityIsRequiredBeforeStartingTests(CampFixture):
    def setUp(self):
        self.login(self.coord1)
        self.choose_station(number=2)

    def start_with(self, participant, data):
        return self.client.post(self.url("interview_start", participant.public_id), data)

    def test_no_interview_starts_without_confirming_who_it_is(self):
        for module in (False, True):
            flags.set_enabled(self.church, "community", module)
            with self.subTest(module=module):
                response = self.start_with(self.p2, {"mode": "asistida", "consent_consent": "on", "consent_assent": "on", "consent_guardian": "on"})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Para comenzar, confirma que es la persona correcta.")
                self.assertFalse(Interview.objects.filter(participant=self.p2).exists())
        response = self.start_with(self.p2, {"identity": "on", "mode": "asistida", "consent_consent": "on", "consent_assent": "on", "consent_guardian": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Interview.objects.filter(participant=self.p2).exists())

    def test_the_confirmation_names_the_campero_in_the_identity_step(self):
        for module in (False, True):
            flags.set_enabled(self.church, "community", module)
            with self.subTest(module=module):
                page = self.client.get(self.url("interview_start", self.p2.public_id)).content.decode()
                step = page[page.index("¿Es la persona correcta?"):page.index("Autorización</h2>")]
                self.assertIn(f"Sí, es {self.p2.full_name}", step)
                self.assertIn('name="identity"', step)

    def test_the_authorizations_stay_required_besides_the_identity(self):
        response = self.start_with(self.p_minor_no_auth, {"identity": "on", "mode": "asistida", "consent_assent": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Para comenzar, confirma esta autorización.")
        self.assertFalse(Interview.objects.filter(participant=self.p_minor_no_auth).exists())
