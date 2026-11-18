"""«Antes de empezar» (owner decision): the required checks of an interview live here and only here — the identity
(«¿Es la persona correcta?»: «Sí, es {nombre}») and the applicable authorizations; no question of the interview is
required. The server refuses to start without them, in both modes."""

from __future__ import annotations

from django.test import Client
from django.urls import reverse

from events.access import resolve_access
from events.models import AuditEvent, EventMembership, Interview, MembershipRole
from events.services import interviews as svc
from events.templatetags.emmaus import fecha_corta
from people import flags

from .base import PASSWORD, CampFixture


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


class IdentityConfirmationIsKeptTests(CampFixture):
    """«Sí, es {nombre}» is asked once, when staff start a new interview; resuming, taking control on another computer
    or recovering it after the campero answered alone never asks again, and the review and the record keep saying
    who confirmed it and when (the start's audit row)."""

    def setUp(self):
        self.login(self.coord1)
        self.choose_station()

    def started(self, participant, mode="asistida"):
        self.assertEqual(self.start(participant, mode=mode).status_code, 302)
        interview = Interview.objects.get(participant=participant)
        row = AuditEvent.objects.get(action="entrevista_iniciada", object_id=str(interview.public_id))
        self.assertEqual((row.metadata.get("identity_confirmed"), row.actor_id), (True, self.coord1.id))
        return interview, f"Identidad confirmada por {self.coord1.get_full_name()}, {fecha_corta(row.created_at)}."

    def finalize(self, client, interview):
        interview.refresh_from_db()
        return client.post(self.url("interview_review", interview.public_id), {"revision": interview.revision, "follow_up_decision": "no_requerido"})

    def test_resuming_and_taking_control_never_ask_again_and_keep_it_in_both_modes(self):
        interview, said = self.started(self.p2)
        for module in (False, True):
            flags.set_enabled(self.church, "community", module)
            with self.subTest(module=module):
                # The same computer: «Retomar», and opening «Antes de empezar» again, go back into the interview.
                resumed = self.client.get(self.url("interview_resume", interview.public_id))
                self.assertIn("/paso/", resumed["Location"])
                again = self.client.get(self.url("interview_start", self.p2.public_id))
                self.assertRedirects(again, self.url("interview_resume", interview.public_id), fetch_redirect_response=False)
                self.assertContains(self.client.get(self.url("interview_review", interview.public_id)), said)
        # Another computer, with another account that may interview: «Tomar el control aquí», no box to tick.
        EventMembership.objects.create(event=self.event, user=self.admin, role=MembershipRole.ADMIN)
        other = self.login(self.admin, Client())
        self.choose_station(other, number=2)
        taken = other.post(self.url("interview_conflict", interview.public_id))
        self.assertRedirects(taken, self.url("interview_resume", interview.public_id), fetch_redirect_response=False)
        review = other.get(self.url("interview_review", interview.public_id))
        self.assertNotContains(review, 'name="identity"')
        self.assertContains(review, said)  # who confirmed it, not who holds it now
        self.assertRedirects(self.finalize(other, interview), self.url("interview_done", interview.public_id), fetch_redirect_response=False)
        for module in (False, True):
            flags.set_enabled(self.church, "community", module)
            with self.subTest(record=module):
                self.assertContains(other.get(self.url("interview_detail", interview.public_id)), said)

    def test_recovering_the_computer_after_the_campero_keeps_it(self):
        interview, said = self.started(self.p1, mode="supervisada")
        self.client.post(self.url("interview_handoff", interview.public_id))
        self.client.post(reverse("kiosk:finish"))
        recovered = self.client.post(reverse("kiosk:unlock"), {"password": PASSWORD})
        self.assertRedirects(recovered, self.url("interview_review", interview.public_id), fetch_redirect_response=False)
        self.assertContains(self.client.get(self.url("interview_review", interview.public_id)), said)
        self.assertRedirects(self.finalize(self.client, interview), self.url("interview_done", interview.public_id), fetch_redirect_response=False)
        self.assertContains(self.client.get(self.url("interview_detail", interview.public_id)), said)
        self.assertEqual(AuditEvent.objects.filter(action="entrevista_iniciada", object_id=str(interview.public_id)).count(), 1)

    def test_an_interview_started_without_the_box_says_nothing_about_it(self):
        """Started before «Antes de empezar» asked it (or by a caller that did not ask): nothing is claimed."""
        interview = svc.start_interview(
            resolve_access(self.coord1, self.event), self.p2, mode="asistida", station=None,
            confirmed_consents={"consent"}, session=self.client.session,
        )
        self.assertIsNone(svc.identity_confirmation(interview))
        self.assertNotContains(self.client.get(self.url("interview_detail", interview.public_id)), "Identidad confirmada")
