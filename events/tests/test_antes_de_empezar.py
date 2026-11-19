"""«Antes de empezar» (owner decision): the required checks of an interview live here and only here — the identity
(«¿Es la persona correcta?»: «Sí, es {nombre}») and the applicable authorizations; no question of the interview is
required. The server refuses to start without them, in both modes."""

from __future__ import annotations

from datetime import timedelta

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from events.access import resolve_access
from events.models import AuditEvent, EventMembership, Interview, MembershipRole
from events.services import audit
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
    """«Sí, es {nombre}» is asked when staff start a new interview. The person who started it is never asked again
    (resuming, another computer, recovering it after the campero answered alone). Anyone else who picks it up to
    continue with the campero ticks the box again, on its own audit row, so «Identidad confirmada por …» stays as it
    was; the review and the record show both people: who confirmed it and «Entrevista a cargo de …» (owner decisions)."""

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

    def other_browser(self, user, number=2):
        EventMembership.objects.get_or_create(event=self.event, user=user, defaults={"role": MembershipRole.ADMIN})
        client = self.login(user, Client())
        self.choose_station(client, number=number)
        return client

    def checks(self, interview):
        return AuditEvent.objects.filter(action=audit.IDENTITY_VERIFIED, object_id=str(interview.public_id))

    def test_the_person_who_started_it_is_never_asked_again_in_both_modes(self):
        interview, said = self.started(self.p2)
        mine = f"{said} Entrevista a cargo de {self.coord1.get_full_name()}."
        for module in (False, True):
            flags.set_enabled(self.church, "community", module)
            with self.subTest(module=module):
                resumed = self.client.get(self.url("interview_resume", interview.public_id))
                self.assertIn("/paso/", resumed["Location"])
                again = self.client.get(self.url("interview_start", self.p2.public_id))
                self.assertRedirects(again, self.url("interview_resume", interview.public_id), fetch_redirect_response=False)
                self.assertContains(self.client.get(self.url("interview_review", interview.public_id)), mine)
        # The same person on another computer: «Continuar aquí» with nothing to tick.
        second = self.login(self.coord1, Client())
        self.choose_station(second, number=2)
        page = second.get(self.url("interview_conflict", interview.public_id))
        self.assertNotContains(page, 'name="identity"')
        taken = second.post(self.url("interview_conflict", interview.public_id))
        self.assertRedirects(taken, self.url("interview_resume", interview.public_id), fetch_redirect_response=False)
        self.assertFalse(self.checks(interview).exists())
        self.assertContains(second.get(self.url("interview_review", interview.public_id)), mine)

    def test_another_person_taking_it_over_checks_the_identity_again_in_both_modes(self):
        interview, said = self.started(self.p2)
        other = self.other_browser(self.admin)
        conflict = self.url("interview_conflict", interview.public_id)
        for module in (False, True):
            flags.set_enabled(self.church, "community", module)
            with self.subTest(module=module):
                page = other.get(conflict)
                self.assertContains(page, f"Sí, es {self.p2.full_name}")
                self.assertContains(page, 'name="identity"')
                refused = other.post(conflict)  # without the box: refused, and nothing is taken
                self.assertEqual(refused.status_code, 200)
                self.assertContains(refused, "Para continuar, confirma que es la persona correcta.")
                interview.refresh_from_db()
                self.assertEqual(interview.lock_holder_id, self.coord1.id)
                self.assertFalse(self.checks(interview).exists())
        taken = other.post(conflict, {"identity": "on"})
        self.assertRedirects(taken, self.url("interview_resume", interview.public_id), fetch_redirect_response=False)
        (check,) = self.checks(interview)
        self.assertEqual((check.actor_id, check.metadata), (self.admin.id, {}))
        started = AuditEvent.objects.get(action="entrevista_iniciada", object_id=str(interview.public_id))
        self.assertEqual((started.actor_id, started.metadata.get("identity_confirmed")), (self.coord1.id, True))  # unchanged
        both = f"{said} Identidad verificada de nuevo por {self.admin.get_full_name()}, {fecha_corta(check.created_at)}. Entrevista a cargo de {self.admin.get_full_name()}."
        self.assertContains(other.get(self.url("interview_review", interview.public_id)), both)
        self.assertNotContains(other.get(conflict), 'name="identity"')  # asked once per person
        self.assertRedirects(self.finalize(other, interview), self.url("interview_done", interview.public_id), fetch_redirect_response=False)
        for module in (False, True):
            flags.set_enabled(self.church, "community", module)
            with self.subTest(record=module):
                self.assertContains(other.get(self.url("interview_detail", interview.public_id)), both)

    def test_another_person_picking_up_a_free_claim_is_asked_before_any_screen(self):
        interview, _ = self.started(self.p2)
        Interview.objects.filter(pk=interview.pk).update(lock_expires_at=timezone.now() - timedelta(minutes=1))  # expired
        other = self.other_browser(self.admin)
        for url in (self.url("interview_resume", interview.public_id), self.url("interview_review", interview.public_id),
                    self.url("interview_step", interview.public_id, 1), self.url("interview_handoff", interview.public_id)):
            response = other.get(url, follow=True)
            self.assertEqual(response.redirect_chain[-1][0], self.url("interview_conflict", interview.public_id), url)
            self.assertContains(response, f"Antes de continuar con {self.p2.first_name}")
        refused = other.post(self.url("interview_conflict", interview.public_id))
        self.assertContains(refused, "Para continuar, confirma que es la persona correcta.")
        other.post(self.url("interview_conflict", interview.public_id), {"identity": "on"})
        self.assertEqual(other.get(self.url("interview_review", interview.public_id)).status_code, 200)
        self.assertEqual(self.checks(interview).count(), 1)

    def test_recovering_the_computer_after_the_campero_keeps_it(self):
        """The coordinator who handed the computer over recovers it: they started it, so nothing is asked."""
        interview, said = self.started(self.p1, mode="supervisada")
        self.client.post(self.url("interview_handoff", interview.public_id))
        self.client.post(reverse("kiosk:finish"))
        recovered = self.client.post(reverse("kiosk:unlock"), {"password": PASSWORD})
        self.assertRedirects(recovered, self.url("interview_review", interview.public_id), fetch_redirect_response=False)
        self.assertContains(self.client.get(self.url("interview_review", interview.public_id)), said)
        self.assertRedirects(self.finalize(self.client, interview), self.url("interview_done", interview.public_id), fetch_redirect_response=False)
        self.assertContains(self.client.get(self.url("interview_detail", interview.public_id)), f"{said} Entrevista a cargo de {self.coord1.get_full_name()}.")
        self.assertFalse(self.checks(interview).exists())

    def test_another_coordinator_recovering_the_computer_checks_it_before_the_review(self):
        """«Soy otro coordinador» on the kiosk: a different person picks it up, so «Sí, es …» before going on."""
        interview, said = self.started(self.p1, mode="supervisada")
        self.client.post(self.url("interview_handoff", interview.public_id))
        self.client.post(reverse("kiosk:finish"))
        EventMembership.objects.create(event=self.event, user=self.admin, role=MembershipRole.ADMIN)
        recovered = self.client.post(reverse("kiosk:unlock"), {"password": PASSWORD, "otra": "1", "username": self.admin.username})
        self.assertRedirects(recovered, self.url("interview_review", interview.public_id), fetch_redirect_response=False)
        review = self.client.get(self.url("interview_review", interview.public_id))
        self.assertRedirects(review, self.url("interview_conflict", interview.public_id), fetch_redirect_response=False)
        page = self.client.get(self.url("interview_conflict", interview.public_id))
        self.assertContains(page, f"Antes de continuar con {self.p1.first_name}")
        self.assertContains(page, f"Sí, es {self.p1.full_name}")
        self.client.post(self.url("interview_conflict", interview.public_id), {"identity": "on"})
        review = self.client.get(self.url("interview_review", interview.public_id))
        self.assertContains(review, said)
        self.assertContains(review, f"Entrevista a cargo de {self.admin.get_full_name()}.")
        self.assertEqual(self.checks(interview).count(), 1)

    def test_an_interview_started_without_the_box_says_nothing_about_it(self):
        """Started before «Antes de empezar» asked it (or by a caller that did not ask): nothing is claimed."""
        interview = svc.start_interview(
            resolve_access(self.coord1, self.event), self.p2, mode="asistida", station=None,
            confirmed_consents={"consent"}, session=self.client.session,
        )
        self.assertIsNone(svc.identity_confirmation(interview))
        self.assertNotContains(self.client.get(self.url("interview_detail", interview.public_id)), "Identidad confirmada")
