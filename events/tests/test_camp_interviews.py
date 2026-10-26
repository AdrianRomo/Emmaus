"""Findings from the user-guide walkthrough of the interview flow.

W1  "Corregir" is decided per answer, not per screen.
W2  "Cancelar" on the safety report goes back to where the person came from.
W4  "Liberar" in "Sesiones abiertas" stops the old computer from saving.
Plus the refuter's corrections: the safety report follows only the review it came from, discard
and "done" are claim-bound, the released conflict page tells the truth, and answers hidden on
another screen by a correction leave a revision.
"""

import re
from datetime import timedelta
from html import unescape
from unittest import mock
from urllib.parse import quote

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from events.models import AnswerRevision, EventMembership, Interview, MembershipRole, ParticipantSession
from events.services import interviews as svc
from events.services import questionnaire
from events.services.questionnaire import build_steps, create_questionnaire_from_definition

from .base import CampFixture

CANCEL_RE = re.compile(r'<a class="btn btn--link" href="([^"]+)">Cancelar</a>')
SAFETY_LINK_RE = re.compile(r'<a class="row__link" href="([^"]+)">Reportar una situación de riesgo</a>')
RELEASED_HEADING = "La administración liberó esta entrevista"
RELEASED_BODY = (
    "Esta computadora ya no puede guardar. Para seguir aquí, toca “Tomar el control aquí”; "
    "para seguir en otra computadora, ábrela desde “Mi inicio”."
)
HIDDEN_BY_CORRECTION = "ocultada por corrección"


def claim_state(interview: Interview) -> tuple:
    interview.refresh_from_db()
    return interview.lock_token_hash, interview.lock_holder_id, interview.lock_expires_at, interview.station_id


class CorrectionPerAnswerTests(CampFixture):
    """W1: a question that shares its screen with a confidential follow-up can be corrected."""

    def setUp(self):
        self.login(self.coord1)
        self.choose_station(number=2)
        self.start(self.p2)
        self.interview = Interview.objects.get(participant=self.p2)
        self.dudas = self.question("dudas")
        self.detalle = self.question("dudas_detalle")  # confidential, on the same screen as "dudas"
        self.apoyo = self.question("apoyo")  # confidential, its own screen
        self.answer(self.interview, "dudas", "si", extra={f"q_{self.detalle.id}": "Sobre la oración"})
        self.answer(self.interview, "apoyo", "familia")
        self.interview.refresh_from_db()
        self.client.post(
            self.url("interview_review", self.interview.public_id),
            {"revision": self.interview.revision, "follow_up_decision": "no_requerido"},
        )
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.COMPLETED)
        self.shared_url = self.url("interview_correct", self.interview.public_id, self.step("dudas").number)
        self.apoyo_url = self.url("interview_correct", self.interview.public_id, self.step("apoyo").number)

    def value(self, question):
        answer = self.interview.answers.filter(question=question).first()
        if answer is None:
            return None
        return answer.text_value if question.is_text else sorted(answer.options.values_list("code", flat=True))

    def test_coordinator_corrects_the_non_confidential_answer_on_a_shared_screen(self):
        page = self.client.get(self.shared_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'name="q_{self.dudas.id}"')
        # The confidential follow-up is not on the coordinator's correction screen at all.
        self.assertNotContains(page, f'name="q_{self.detalle.id}"')
        self.assertNotContains(page, self.detalle.text)
        self.assertNotContains(page, "Sobre la oración")

        missing_reason = self.client.post(self.shared_url, {"revision": self.interview.revision, f"q_{self.dudas.id}": "no", "reason": ""})
        self.assertContains(missing_reason, "Escribe brevemente por qué corriges esta respuesta.")
        self.assertEqual(self.value(self.dudas), ["si"])

        response = self.client.post(
            self.shared_url, {"revision": self.interview.revision, f"q_{self.dudas.id}": "no", "reason": "Se marcó por error"}
        )
        self.assertRedirects(response, self.url("interview_detail", self.interview.public_id), fetch_redirect_response=False)
        self.interview.refresh_from_db()
        self.assertEqual(self.value(self.dudas), ["no"])
        revision = AnswerRevision.objects.get(interview=self.interview, question=self.dudas)
        self.assertEqual(revision.previous_option_codes, ["si"])
        self.assertEqual(revision.reason, "Se marcó por error")
        self.assertEqual(revision.changed_by, self.coord1)
        self.assertEqual(self.interview.correction_count, 1)
        # "No" hides the follow-up, so its answer goes, as on any save; the care team keeps the old text.
        self.assertIsNone(self.value(self.detalle))
        hidden = AnswerRevision.objects.get(interview=self.interview, question=self.detalle)
        self.assertEqual((hidden.previous_text, hidden.reason, hidden.changed_by), ("Sobre la oración", HIDDEN_BY_CORRECTION, self.coord1))
        # The audit row counts only what the coordinator could see, so it does not reveal the hidden answer.
        audit = self.event.audit_events.get(action="entrevista_corregida")
        self.assertEqual(audit.metadata["questions"], 1)

    def test_shared_screen_ignores_a_forged_confidential_field(self):
        response = self.client.post(
            self.shared_url,
            {"revision": self.interview.revision, f"q_{self.dudas.id}": "si", f"q_{self.detalle.id}": "Texto inventado", "reason": "Prueba"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.value(self.detalle), "Sobre la oración")
        self.assertFalse(AnswerRevision.objects.filter(interview=self.interview).exists())

    def test_no_corregir_link_for_confidential_answers(self):
        html = self.client.get(self.url("interview_detail", self.interview.public_id)).content.decode()
        self.assertIn(f'href="{self.shared_url}">Corregir<span class="visually-hidden"> “{self.dudas.text}”', html)
        for question in (self.detalle, self.apoyo):
            self.assertNotIn(f"Corregir<span class=\"visually-hidden\"> “{question.text}”", html)
        self.assertNotIn(self.apoyo_url, html)

    def test_confidential_answer_url_stays_forbidden(self):
        self.assertEqual(self.client.get(self.apoyo_url).status_code, 403)
        response = self.client.post(
            self.apoyo_url, {"revision": self.interview.revision, f"q_{self.apoyo.id}": "ninguna", "reason": "Por error"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.value(self.apoyo), ["familia"])
        self.assertFalse(AnswerRevision.objects.filter(interview=self.interview).exists())

    def test_care_team_member_can_correct_the_confidential_answer(self):
        EventMembership.objects.create(event=self.event, user=self.admin, role=MembershipRole.ADMIN, can_view_sensitive=True)
        care = self.login(self.admin, Client())
        page = care.get(self.shared_url)
        self.assertContains(page, f'name="q_{self.detalle.id}"')
        self.assertContains(page, "Sobre la oración")
        response = care.post(
            self.shared_url,
            {
                "revision": self.interview.revision,
                f"q_{self.dudas.id}": "si",
                f"q_{self.detalle.id}": "Sobre la oración en familia",
                "reason": "Faltaba una parte",
            },
        )
        self.assertRedirects(response, self.url("interview_detail", self.interview.public_id), fetch_redirect_response=False)
        self.assertEqual(self.value(self.detalle), "Sobre la oración en familia")
        revision = AnswerRevision.objects.get(interview=self.interview, question=self.detalle)
        self.assertEqual((revision.previous_text, revision.reason, revision.changed_by), ("Sobre la oración", "Faltaba una parte", self.admin))
        self.assertEqual(care.get(self.apoyo_url).status_code, 200)


class SafetyReportCancelTests(CampFixture):
    """W2: "Cancelar" returns to the camper record, or to the review for whoever holds the interview."""

    def setUp(self):
        self.station = self.login(self.coord1, Client())
        self.choose_station(self.station, number=4)
        self.start(self.p1, client=self.station)
        self.interview = Interview.objects.get(participant=self.p1)
        self.answer(self.interview, "salvo", "si", client=self.station)
        self.report_url = self.url("safety_report", self.p1.public_id)
        self.record_url = self.url("participant_detail", self.p1.public_id)
        self.review_url = self.url("interview_review", self.interview.public_id)

    def takeovers(self) -> int:
        return self.event.audit_events.filter(action__in=["entrevista_retomada", "entrevista_control"]).count()

    def cancel_href(self, client, url) -> str:
        response = client.get(url)
        self.assertEqual(response.status_code, 200)
        match = CANCEL_RE.search(response.content.decode())
        self.assertIsNotNone(match, "no Cancelar link")
        return unescape(match.group(1))

    def assert_cancel_goes_to(self, client, open_url, expected):
        before, takeovers = claim_state(self.interview), self.takeovers()
        href = self.cancel_href(client, open_url)
        self.assertEqual(href, expected)
        self.assertEqual(client.get(href).status_code, 200)
        self.assertEqual(claim_state(self.interview), before)
        self.assertEqual(self.takeovers(), takeovers)

    def test_tutor_cancel_returns_to_the_camper_record(self):
        self.assert_cancel_goes_to(self.login(self.tutor1, Client()), self.report_url, self.record_url)

    def test_pastor_cancel_returns_to_the_camper_record(self):
        self.assert_cancel_goes_to(self.login(self.pastor, Client()), self.report_url, self.record_url)

    def test_admin_cancel_returns_to_the_camper_record_and_takes_no_claim(self):
        self.assert_cancel_goes_to(self.login(self.admin, Client()), self.report_url, self.record_url)
        # Even with the coordinator's claim expired, the admin's way back takes nothing.
        Interview.objects.filter(pk=self.interview.pk).update(lock_expires_at=timezone.now() - timedelta(minutes=1))
        self.assert_cancel_goes_to(self.login(self.admin, Client()), self.report_url, self.record_url)

    def test_coordinator_holding_the_claim_cancels_back_to_the_review(self):
        review = self.station.get(self.review_url)
        match = SAFETY_LINK_RE.search(review.content.decode())
        self.assertIsNotNone(match, "no safety link on the review")
        link = unescape(match.group(1))
        self.assertEqual(link, f"{self.report_url}?siguiente={quote(self.review_url)}")
        self.assert_cancel_goes_to(self.station, link, self.review_url)

    def test_coordinator_without_the_claim_cancels_back_to_the_camper_record(self):
        other_computer = self.login(self.coord1, Client())
        self.assert_cancel_goes_to(other_computer, self.report_url, self.record_url)

    def test_foreign_siguiente_falls_back_to_the_camper_record(self):
        tutor = self.login(self.tutor1, Client())
        for unsafe in ("https://evil.example/", "//evil.example/", "/\\evil.example/"):
            self.assertEqual(self.cancel_href(tutor, f"{self.report_url}?siguiente={quote(unsafe)}"), self.record_url, unsafe)

    def review_siguiente(self, review_url=None) -> str:
        return f"{self.report_url}?siguiente={quote(review_url or self.review_url)}"

    def test_tutor_with_a_review_siguiente_returns_to_the_camper_record(self):
        self.assert_cancel_goes_to(self.login(self.tutor1, Client()), self.review_siguiente(), self.record_url)

    def test_admin_forwarding_a_review_siguiente_does_not_take_the_interview(self):
        Interview.objects.filter(pk=self.interview.pk).update(lock_expires_at=timezone.now() - timedelta(minutes=1))
        admin = self.login(self.admin, Client())
        self.assert_cancel_goes_to(admin, self.review_siguiente(), self.record_url)
        before, takeovers = claim_state(self.interview), self.takeovers()
        response = admin.post(self.review_siguiente(), {"details": "Reporte."})
        self.assertRedirects(response, self.record_url, fetch_redirect_response=False)
        self.assertEqual(admin.get(response["Location"]).status_code, 200)
        self.assertEqual(claim_state(self.interview), before)
        self.assertEqual(self.takeovers(), takeovers)

    def test_coordinator_whose_claim_expired_returns_to_the_camper_record(self):
        Interview.objects.filter(pk=self.interview.pk).update(lock_expires_at=timezone.now() - timedelta(minutes=1))
        self.assert_cancel_goes_to(self.station, self.review_siguiente(), self.record_url)

    def test_only_this_interviews_review_is_followed(self):
        self.start(self.p2, client=self.station)  # the same browser also holds another camper's interview
        other_review = self.url("interview_review", Interview.objects.get(participant=self.p2).public_id)
        for candidate in (other_review, self.url("coord_home"), self.record_url + "?x=1", self.review_url + "?x=1"):
            self.assertEqual(self.cancel_href(self.station, self.review_siguiente(candidate)), self.record_url, candidate)

    def test_sending_the_report_also_returns_where_the_person_came_from(self):
        Interview.objects.filter(pk=self.interview.pk).update(lock_expires_at=timezone.now() - timedelta(minutes=1))
        before, takeovers = claim_state(self.interview), self.takeovers()
        admin = self.login(self.admin, Client())
        response = admin.post(self.report_url, {"details": "Contó algo que lo pone en riesgo."})
        self.assertRedirects(response, self.record_url, fetch_redirect_response=False)
        self.assertEqual(claim_state(self.interview), before)
        self.assertEqual(self.takeovers(), takeovers)
        # The coordinator who reported from the review goes back to the review.
        Interview.objects.filter(pk=self.interview.pk).update(lock_expires_at=timezone.now() + timedelta(minutes=10))
        response = self.station.post(f"{self.report_url}?siguiente={quote(self.review_url)}", {"details": "Otro reporte."})
        self.assertRedirects(response, self.review_url, fetch_redirect_response=False)


class ReleaseStopsTheOldComputerTests(CampFixture):
    """W4: after "Liberar", the computer that had the interview open cannot save until it opens it again."""

    def setUp(self):
        self.a = self.login(self.coord1, Client())
        self.choose_station(self.a, number=5)
        self.start(self.p1, client=self.a)
        self.interview = Interview.objects.get(participant=self.p1)
        self.answer(self.interview, "salvo", "si", client=self.a)
        self.assertEqual(self.interview.answers.count(), 1)
        self.admin_client = self.login(self.admin, Client())
        self.conflict_url = self.url("interview_conflict", self.interview.public_id)
        self.resume_url = self.url("interview_resume", self.interview.public_id)

    def release(self):
        """The admin's "Sesiones abiertas" → "Liberar" (events.views.admin.sessions → svc.revoke_claim)."""
        response = self.admin_client.post(self.url("sessions"), {"interview": str(self.interview.public_id)})
        self.assertRedirects(response, self.url("sessions"), fetch_redirect_response=False)
        self.interview.refresh_from_db()
        self.assertFalse(self.interview.lock_is_active())

    def step_url(self, code):
        return self.url("interview_step", self.interview.public_id, self.step(code).number)

    def test_old_computer_cannot_save_after_liberar(self):
        self.release()
        response = self.answer(self.interview, "dudas", "no", client=self.a)
        self.assertRedirects(response, self.conflict_url, fetch_redirect_response=False)
        self.assertEqual(self.interview.answers.count(), 1)
        self.assertContains(self.a.get(self.conflict_url), RELEASED_HEADING)
        # A second try does not sneak back in, and neither does finalizing nor reopening the step.
        response = self.answer(self.interview, "dudas", "no", client=self.a)
        self.assertRedirects(response, self.conflict_url, fetch_redirect_response=False)
        self.interview.refresh_from_db()
        response = self.a.post(
            self.url("interview_review", self.interview.public_id), {"revision": self.interview.revision, "follow_up_decision": "no_requerido"}
        )
        self.assertRedirects(response, self.conflict_url, fetch_redirect_response=False)
        self.assertRedirects(self.a.get(self.step_url("dudas")), self.conflict_url, fetch_redirect_response=False)
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.IN_PROGRESS)
        self.assertEqual(self.interview.answers.count(), 1)
        self.assertFalse(self.interview.lock_is_active())

    def test_another_computer_continues_after_liberar(self):
        self.release()
        b = self.login(self.coord1, Client())
        b_station = self.choose_station(b, number=6)
        self.assertRedirects(b.get(self.resume_url), self.step_url("dudas"), fetch_redirect_response=False)
        response = self.answer(self.interview, "dudas", "no", client=b)
        self.assertRedirects(response, self.step_url("apoyo"), fetch_redirect_response=False)
        self.assertEqual(self.interview.answers.count(), 2)
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.station, b_station)
        # The old computer is still refused, now because the other one holds the interview.
        response = self.answer(self.interview, "apoyo", "ninguna", client=self.a)
        self.assertRedirects(response, self.conflict_url, fetch_redirect_response=False)
        self.assertEqual(self.interview.answers.count(), 2)

    def test_old_computer_reopens_from_mi_inicio_with_a_fresh_claim(self):
        self.release()
        released_hash = self.interview.lock_token_hash
        self.assertContains(self.a.get(self.url("coord_home")), f'href="{self.resume_url}">Retomar entrevista</a>')
        self.assertRedirects(self.a.get(self.resume_url), self.step_url("dudas"), fetch_redirect_response=False)
        self.interview.refresh_from_db()
        self.assertTrue(self.interview.lock_is_active())
        self.assertNotEqual(self.interview.lock_token_hash, released_hash)
        self.assertEqual(self.interview.lock_holder, self.coord1)
        response = self.answer(self.interview, "dudas", "no", client=self.a)
        self.assertRedirects(response, self.step_url("apoyo"), fetch_redirect_response=False)
        self.assertEqual(self.interview.answers.count(), 2)
        self.assertTrue(self.event.audit_events.filter(action="entrevista_retomada").exists())

    def test_tomar_el_control_still_works_after_liberar(self):
        self.release()
        response = self.a.post(self.conflict_url)
        self.assertRedirects(response, self.resume_url, fetch_redirect_response=False)
        response = self.answer(self.interview, "dudas", "no", client=self.a)
        self.assertRedirects(response, self.step_url("apoyo"), fetch_redirect_response=False)
        self.assertEqual(self.interview.answers.count(), 2)

    def test_old_computer_cannot_hand_over_after_liberar(self):
        self.start(self.p2, mode="supervisada", client=self.a)
        interview = Interview.objects.get(participant=self.p2)
        handoff_url = self.url("interview_handoff", interview.public_id)
        self.assertEqual(self.a.get(handoff_url).status_code, 200)
        svc.revoke_claim(interview, actor=self.admin)
        response = self.a.post(handoff_url, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], self.url("interview_conflict", interview.public_id))
        self.assertFalse(ParticipantSession.objects.filter(interview=interview).exists())
        self.assertEqual(self.a.get(self.url("coord_home")).status_code, 200)  # still logged in: no kiosk

    def test_hand_over_losing_the_claim_mid_request_does_not_reopen(self):
        """"Liberar" landing between the view's check and the service's row lock must not end in a re-open."""
        self.start(self.p2, mode="supervisada", client=self.a)
        interview = Interview.objects.get(participant=self.p2)
        handoff_url = self.url("interview_handoff", interview.public_id)

        def released_meanwhile(request, access, locked, station):
            svc.revoke_claim(locked, actor=self.admin)
            raise svc.ClaimLost("Esta pantalla ya no tiene el control de la entrevista.", interview=locked)

        with mock.patch.object(svc, "hand_over_to_participant", side_effect=released_meanwhile):
            response = self.a.post(handoff_url)
        self.assertRedirects(response, handoff_url, fetch_redirect_response=False)
        self.assertRedirects(self.a.get(handoff_url), self.url("interview_conflict", interview.public_id), fetch_redirect_response=False)
        interview.refresh_from_db()
        self.assertFalse(interview.lock_is_active())

    def test_an_expired_claim_on_the_hand_over_is_taken_back_like_a_step(self):
        self.start(self.p2, mode="supervisada", client=self.a)
        interview = Interview.objects.get(participant=self.p2)
        Interview.objects.filter(pk=interview.pk).update(lock_expires_at=timezone.now() - timedelta(seconds=1))
        response = self.a.post(self.url("interview_handoff", interview.public_id))
        self.assertRedirects(response, reverse("kiosk:home"), fetch_redirect_response=False)
        self.assertTrue(ParticipantSession.objects.filter(interview=interview, state=ParticipantSession.State.ACTIVE).exists())

    def test_released_conflict_page_says_what_happened(self):
        self.a.get(self.url("coord_home"))  # show setUp's pending "quedó registrada como Computadora 5" flash first
        self.release()
        self.assertRedirects(self.answer(self.interview, "dudas", "no", client=self.a), self.conflict_url, fetch_redirect_response=False)
        page = self.a.get(self.conflict_url)
        html = page.content.decode()
        self.assertContains(page, f'<h1 class="voice page-head__title">{RELEASED_HEADING}</h1>', html=False)
        self.assertEqual(html.count(RELEASED_BODY), 1)
        self.assertEqual(html.count("ya no puede guardar"), 1)  # no flash repeating it
        for misleading in ("abierta en otra computadora", "Otro coordinador", "Computadora 5", "<dt>Computadora</dt>", "La otra computadora"):
            self.assertNotIn(misleading, html)
        self.assertContains(page, "Tomar el control aquí")

    def test_real_conflict_page_is_unchanged(self):
        b = self.login(self.coord1, Client())
        self.assertRedirects(b.get(self.step_url("dudas")), self.conflict_url, fetch_redirect_response=False)
        page = b.get(self.conflict_url)
        self.assertContains(page, "Esta entrevista está abierta en otra computadora")
        self.assertContains(page, "Computadora 5")
        self.assertContains(page, self.coord1.get_full_name())
        self.assertNotContains(page, RELEASED_HEADING)

    def test_released_computer_cannot_discard(self):
        self.release()
        discard_url = self.url("interview_discard", self.interview.public_id)
        self.assertRedirects(self.a.get(discard_url), self.conflict_url, fetch_redirect_response=False)
        self.assertRedirects(self.a.post(discard_url, {"reason": "repetir"}), self.conflict_url, fetch_redirect_response=False)
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.IN_PROGRESS)
        self.assertEqual(self.interview.answers.count(), 1)
        self.assertFalse(self.interview.lock_is_active())

    def test_another_computer_cannot_discard_while_one_holds_it(self):
        b = self.login(self.coord1, Client())
        discard_url = self.url("interview_discard", self.interview.public_id)
        self.assertRedirects(b.get(discard_url), self.conflict_url, fetch_redirect_response=False)
        self.assertRedirects(b.post(discard_url, {"reason": "repetir"}), self.conflict_url, fetch_redirect_response=False)
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.IN_PROGRESS)
        self.assertEqual(self.interview.answers.count(), 1)

    def test_discard_service_refuses_without_the_claim(self):
        with self.assertRaises(svc.ClaimLost):
            svc.discard(self.interview, actor=self.coord1, reason="repetir", session={}, check_claim=True)
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.IN_PROGRESS)
        self.assertEqual(self.interview.answers.count(), 1)

    def test_tomar_el_control_then_discard_works(self):
        self.release()
        self.a.post(self.conflict_url)
        response = self.a.post(self.url("interview_discard", self.interview.public_id), {"reason": "repetir"})
        self.assertRedirects(response, self.url("coord_home"), fetch_redirect_response=False)
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.DISCARDED)
        self.assertFalse(self.interview.answers.exists())

    def test_discard_after_an_expired_claim_works_like_a_step(self):
        Interview.objects.filter(pk=self.interview.pk).update(lock_expires_at=timezone.now() - timedelta(seconds=1))
        response = self.a.post(self.url("interview_discard", self.interview.public_id), {"reason": "repetir"})
        self.assertRedirects(response, self.url("coord_home"), fetch_redirect_response=False)
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.DISCARDED)

    def test_done_url_does_not_reopen_a_released_claim(self):
        self.release()
        released_hash = self.interview.lock_token_hash
        response = self.a.get(self.url("interview_done", self.interview.public_id))
        self.assertRedirects(response, self.conflict_url, fetch_redirect_response=False)
        self.interview.refresh_from_db()
        self.assertFalse(self.interview.lock_is_active())
        self.assertEqual(self.interview.lock_token_hash, released_hash)
        self.assertFalse(self.event.audit_events.filter(action__in=["entrevista_retomada", "entrevista_control"]).exists())

    def test_done_url_without_a_token_still_claims_normally(self):
        Interview.objects.filter(pk=self.interview.pk).update(lock_expires_at=timezone.now() - timedelta(seconds=1))
        b = self.login(self.coord1, Client())
        response = b.get(self.url("interview_done", self.interview.public_id))
        self.assertRedirects(response, self.step_url("dudas"), fetch_redirect_response=False)
        self.interview.refresh_from_db()
        self.assertTrue(self.interview.lock_is_active())
        self.assertTrue(self.interview.lock_matches(svc.session_claim_token(b.session, self.interview)))

    def test_correction_url_of_an_interview_in_progress_is_forbidden(self):
        self.assertEqual(self.a.get(self.url("interview_correct", self.interview.public_id, self.step("salvo").number)).status_code, 403)

    def test_an_expired_claim_is_still_taken_back_without_a_prompt(self):
        Interview.objects.filter(pk=self.interview.pk).update(lock_expires_at=timezone.now() - timedelta(seconds=1))
        response = self.answer(self.interview, "dudas", "no", client=self.a)
        self.assertRedirects(response, self.step_url("apoyo"), fetch_redirect_response=False)
        self.assertEqual(self.interview.answers.count(), 2)

    def test_release_service_frees_the_interview_for_the_sessions_list(self):
        svc.revoke_claim(self.interview, actor=self.admin)
        self.interview.refresh_from_db()
        self.assertIsNone(self.interview.lock_holder)
        self.assertIsNone(self.interview.lock_expires_at)
        page = self.admin_client.get(self.url("sessions"))
        self.assertContains(page, '<span class="muted">Libre</span>')
        self.assertNotContains(page, "¿Liberar esta entrevista?")
        self.assertEqual(svc.release_stale_claims(self.event), 0)


class CorrectionHidesAnotherScreenTests(CampFixture):
    """A correction that hides a question on another screen records what it removed."""

    DEFINITION = {
        "name": "Prueba de pantallas",
        "title": "Prueba",
        "sections": [
            {
                "title": "Situación espiritual",
                "questions": [
                    {
                        "code": "salvo",
                        "text": "¿Te consideras salvo?",
                        "kind": "unica",
                        "sensitivity": "sensible",
                        "options": [{"code": "si", "label": "Sí"}, {"code": "no", "label": "No"}],
                    }
                ],
            },
            {
                "title": "Privado",
                "questions": [
                    {
                        "code": "privado",
                        "text": "¿Hay algo que te impida avanzar?",
                        "kind": "texto_largo",
                        "sensitivity": "confidencial",
                        "condition": ("salvo", ["si"]),
                    }
                ],
            },
        ],
    }

    def setUp(self):
        version = create_questionnaire_from_definition(self.church, self.DEFINITION)
        # build_steps caches published structures by version id, and SQLite reuses ids after a test's
        # rollback: drop any stale entry for this id now and ours afterwards, or other tests read it.
        questionnaire._structure_cache.pop(version.id, None)
        self.addCleanup(questionnaire._structure_cache.pop, version.id, None)
        type(self.event).objects.filter(pk=self.event.pk).update(questionnaire_version=version)
        self.event.refresh_from_db()
        salvo_step, private_step = build_steps(version)
        self.assertNotEqual(salvo_step.number, private_step.number)  # really on another screen
        self.salvo, self.private = salvo_step.question, private_step.question
        self.login(self.coord1)
        self.choose_station(number=7)
        self.start(self.p2)
        self.interview = Interview.objects.get(participant=self.p2)
        self.post_step(salvo_step, {f"q_{self.salvo.id}": "si"})
        self.post_step(private_step, {f"q_{self.private.id}": "Algo muy privado"})
        self.interview.refresh_from_db()
        self.client.post(
            self.url("interview_review", self.interview.public_id),
            {"revision": self.interview.revision, "follow_up_decision": "no_requerido"},
        )
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.COMPLETED)
        self.assertTrue(self.interview.answers.filter(question=self.private).exists())
        self.salvo_step = salvo_step

    def post_step(self, step, data):
        self.interview.refresh_from_db()
        response = self.client.post(
            self.url("interview_step", self.interview.public_id, step.number), {"revision": self.interview.revision, "direction": "next", **data}
        )
        self.assertEqual(response.status_code, 302)

    def test_correction_records_the_answer_it_hid_on_another_screen(self):
        response = self.client.post(
            self.url("interview_correct", self.interview.public_id, self.salvo_step.number),
            {"revision": self.interview.revision, f"q_{self.salvo.id}": "no", "reason": "Error"},
        )
        self.assertRedirects(response, self.url("interview_detail", self.interview.public_id), fetch_redirect_response=False)
        self.assertFalse(self.interview.answers.filter(question=self.private).exists())
        hidden = AnswerRevision.objects.get(interview=self.interview, question=self.private)
        self.assertEqual((hidden.previous_text, hidden.reason, hidden.changed_by), ("Algo muy privado", HIDDEN_BY_CORRECTION, self.coord1))
        corrected = AnswerRevision.objects.get(interview=self.interview, question=self.salvo)
        self.assertEqual((corrected.previous_option_codes, corrected.reason), (["si"], "Error"))
        self.assertEqual(self.event.audit_events.get(action="entrevista_corregida").metadata["questions"], 1)
