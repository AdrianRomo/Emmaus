"""Interview workflow on shared stations."""

from datetime import timedelta

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from events.access import resolve_access
from events.models import (
    ConsentRecord,
    EventParticipant,
    FollowUp,
    Interview,
    InterviewStatus,
    ParticipantSession,
)
from events.services import interviews as svc

from .base import PASSWORD, CampFixture


class ParticipantRegistrationTests(CampFixture):
    def test_coordinator_registers_camper_assigned_to_themselves(self):
        self.login(self.coord2)
        response = self.client.post(
            self.url("participant_create"),
            {"full_name": "Valeria Méndez Soto", "age": 16, "room": "7", "church_group": "Iglesia Lomas", "then": "otro"},
        )
        self.assertEqual(response.status_code, 302)
        camper = EventParticipant.objects.get(full_name="Valeria Méndez Soto")
        self.assertEqual(camper.coordinator, self.m_coord2)
        self.assertEqual(camper.created_by, self.coord2)
        self.assertEqual(camper.number, 6)

    def test_coordinator_cannot_choose_another_coordinator(self):
        self.login(self.coord2)
        self.client.post(
            self.url("participant_create"),
            {"full_name": "Iker Ruiz Paz", "age": 15, "room": "2", "church_group": "Centro", "coordinator": self.m_coord1.id},
        )
        self.assertEqual(EventParticipant.objects.get(full_name="Iker Ruiz Paz").coordinator, self.m_coord2)

    def test_possible_duplicate_requires_confirmation_and_is_never_merged(self):
        self.login(self.coord1)
        data = {"full_name": "mariana lopez  ruiz", "age": 15, "room": "4", "church_group": "Centro"}
        response = self.client.post(self.url("participant_create"), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ya hay un campero registrado con este nombre")
        response = self.client.post(self.url("participant_create"), {**data, "not_duplicate": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(EventParticipant.objects.filter(event=self.event, normalized_name="mariana lopez ruiz").count(), 2)

    def test_validation_messages_are_spanish(self):
        self.login(self.coord1)
        response = self.client.post(self.url("participant_create"), {"full_name": "Solo", "age": "", "room": "", "church_group": ""})
        self.assertContains(response, "Hay un problema")
        self.assertContains(response, "Escribe nombre y apellido.")
        self.assertContains(response, "Escribe la edad del campero.")
        self.assertContains(response, "Error: ")


class AssistedInterviewTests(CampFixture):
    def setUp(self):
        self.login(self.coord1)
        self.station = self.choose_station(number=3)

    def test_start_requires_consent_and_records_it(self):
        response = self.client.post(self.url("interview_start", self.p2.public_id), {"mode": "asistida"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirma esta autorización para poder comenzar.")
        self.assertFalse(Interview.objects.filter(participant=self.p2).exists())
        response = self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        self.assertRedirects(response, self.url("interview_step", interview.public_id, 1), fetch_redirect_response=False)
        self.assertTrue(ConsentRecord.objects.filter(participant=self.p2, kind=ConsentRecord.Kind.SELF, interview=interview).exists())
        self.assertEqual(interview.station, self.station)

    def test_minor_without_guardian_authorization_is_blocked_until_confirmed(self):
        response = self.client.post(
            self.url("interview_start", self.p_minor_no_auth.public_id), {"mode": "asistida", "consent_assent": "on"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Interview.objects.filter(participant=self.p_minor_no_auth).exists())

    def test_cannot_start_until_procedures_are_approved(self):
        self.event.procedures_approved_at = None
        self.event.save()
        self.start(self.p2)
        self.assertFalse(Interview.objects.filter(participant=self.p2).exists())

    def test_save_partial_resume_and_complete(self):
        self.start(self.p1)
        interview = Interview.objects.get(participant=self.p1)
        self.assertEqual(EventParticipant.objects.get(pk=self.p1.pk).interview_status, InterviewStatus.IN_PROGRESS)
        response = self.answer(interview, "salvo", "no_seguro", direction="exit")
        self.assertRedirects(response, self.url("coord_home"), fetch_redirect_response=False)
        # Interrupted: a fresh session (another browser) resumes on the next unanswered step.
        other = Client()
        self.login(self.coord1, other)
        interview.lock_expires_at = timezone.now() - timedelta(seconds=1)
        interview.save()
        response = other.get(self.url("interview_resume", interview.public_id))
        self.assertRedirects(response, self.url("interview_step", interview.public_id, self.step("dudas").number), fetch_redirect_response=False)
        for code, value in [("dudas", "no"), ("apoyo", "ninguna"), ("decision", "seguir_aprendiendo")]:
            self.answer(interview, code, value, client=other)
        interview.refresh_from_db()
        response = other.post(self.url("interview_review", interview.public_id), {"revision": interview.revision, "follow_up_decision": "no_requerido"})
        self.assertRedirects(response, self.url("interview_done", interview.public_id), fetch_redirect_response=False)
        interview.refresh_from_db()
        self.assertEqual(interview.status, Interview.Status.COMPLETED)
        self.assertEqual(EventParticipant.objects.get(pk=self.p1.pk).interview_status, InterviewStatus.COMPLETED)
        self.assertEqual(interview.answers.count(), 4)

    def test_duplicate_finalize_is_idempotent(self):
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        self.answer(interview, "salvo", "si")
        interview.refresh_from_db()
        payload = {"revision": interview.revision, "follow_up_decision": "requerido"}
        first = self.client.post(self.url("interview_review", interview.public_id), payload)
        second = self.client.post(self.url("interview_review", interview.public_id), payload)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(Interview.objects.filter(participant=self.p2).count(), 1)
        self.assertEqual(FollowUp.objects.filter(participant=self.p2, kind=FollowUp.Kind.CARE).count(), 1)

    def test_starting_again_after_completion_is_rejected(self):
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        interview.refresh_from_db()
        self.client.post(self.url("interview_review", interview.public_id), {"revision": interview.revision, "follow_up_decision": "no_requerido"})
        response = self.start(self.p2)
        self.assertEqual(Interview.objects.filter(participant=self.p2).count(), 1)
        self.assertEqual(response.status_code, 302)

    def test_second_station_cannot_edit_while_first_holds_the_claim(self):
        self.start(self.p1)
        interview = Interview.objects.get(participant=self.p1)
        other = Client()
        self.login(self.coord1, other)
        response = other.get(self.url("interview_step", interview.public_id, 1))
        self.assertRedirects(response, self.url("interview_conflict", interview.public_id), fetch_redirect_response=False)
        # Forged POST from the second browser is refused, nothing saved.
        step = self.step("salvo")
        response = other.post(self.url("interview_step", interview.public_id, step.number), {"revision": interview.revision, f"q_{step.question.id}": "no"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(interview.answers.exists())

    def test_take_over_invalidates_the_first_station(self):
        self.start(self.p1)
        interview = Interview.objects.get(participant=self.p1)
        other = Client()
        self.login(self.coord1, other)
        other.post(self.url("interview_conflict", interview.public_id))
        self.answer(interview, "salvo", "si")  # first station tries to save
        self.assertFalse(interview.answers.exists())
        self.answer(interview, "salvo", "no", client=other)
        self.assertEqual(interview.answers.get().options.get().code, "no")
        self.assertTrue(self.event.audit_events.filter(action="entrevista_control").exists())

    def test_stale_tab_cannot_overwrite_newer_answers(self):
        self.start(self.p1)
        interview = Interview.objects.get(participant=self.p1)
        old_revision = interview.revision
        self.answer(interview, "salvo", "si")
        step = self.step("salvo")
        response = self.client.post(self.url("interview_step", interview.public_id, step.number), {"revision": old_revision, f"q_{step.question.id}": "no"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(interview.answers.get().options.get().code, "si")

    def test_discard_requires_reason_and_deletes_answers(self):
        self.start(self.p1)
        interview = Interview.objects.get(participant=self.p1)
        self.answer(interview, "salvo", "si")
        response = self.client.post(self.url("interview_discard", interview.public_id), {})
        self.assertEqual(response.status_code, 200)
        self.client.post(self.url("interview_discard", interview.public_id), {"reason": "repetir"})
        interview.refresh_from_db()
        self.assertEqual(interview.status, Interview.Status.DISCARDED)
        self.assertFalse(interview.answers.exists())
        self.assertEqual(EventParticipant.objects.get(pk=self.p1.pk).interview_status, InterviewStatus.PENDING)
        self.start(self.p1)  # a new interview may begin
        self.assertEqual(Interview.objects.filter(participant=self.p1).exclude(status="descartada").count(), 1)

    def test_correction_keeps_history_and_counts_once(self):
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        self.answer(interview, "salvo", "si")
        interview.refresh_from_db()
        self.client.post(self.url("interview_review", interview.public_id), {"revision": interview.revision, "follow_up_decision": "no_requerido"})
        interview.refresh_from_db()
        step = self.step("salvo")
        response = self.client.post(
            self.url("interview_correct", interview.public_id, step.number),
            {"revision": interview.revision, f"q_{step.question.id}": "no", "reason": ""},
        )
        self.assertContains(response, "Escribe brevemente por qué corriges esta respuesta.")
        self.client.post(
            self.url("interview_correct", interview.public_id, step.number),
            {"revision": interview.revision, f"q_{step.question.id}": "no", "reason": "Se marcó por error"},
        )
        interview.refresh_from_db()
        self.assertEqual(interview.correction_count, 1)
        self.assertEqual(interview.answer_revisions.get().previous_option_codes, ["si"])
        self.assertEqual(Interview.objects.filter(participant=self.p2, status="completada").count(), 1)

    def test_tutor_cannot_correct(self):
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        interview.refresh_from_db()
        self.client.post(self.url("interview_review", interview.public_id), {"revision": interview.revision, "follow_up_decision": "no_requerido"})
        self.login(self.tutor1)
        self.assertEqual(self.client.get(self.url("interview_correct", interview.public_id, 1)).status_code, 403)

    def test_pages_are_never_cached(self):
        self.start(self.p1)
        interview = Interview.objects.get(participant=self.p1)
        response = self.client.get(self.url("interview_step", interview.public_id, 1))
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])


class SupervisedCaptureTests(CampFixture):
    def setUp(self):
        self.login(self.coord1)
        self.choose_station(number=5)
        self.start(self.p1, mode="supervisada")
        self.interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", self.interview.public_id))

    def test_hand_over_logs_the_coordinator_out(self):
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertIn(svc.KIOSK_KEY, self.client.session)
        ps = ParticipantSession.objects.get(interview=self.interview)
        self.assertEqual(ps.state, ParticipantSession.State.ACTIVE)
        self.assertEqual(ps.station.number, 5)

    def test_participant_mode_cannot_reach_privileged_pages(self):
        for url in (self.url("coord_home"), self.url("interview_detail", self.interview.public_id), reverse("admin:index"), reverse("dashboard"), reverse("login")):
            response = self.client.get(url)
            self.assertRedirects(response, reverse("kiosk:home"), fetch_redirect_response=False, msg_prefix=url)

    def test_participant_answers_are_saved_and_marked_as_own(self):
        response = self.answer(self.interview, "salvo", "si", url_name="kiosk")
        self.assertEqual(response.status_code, 302)
        answer = self.interview.answers.get()
        self.assertTrue(answer.answered_by_participant)
        self.assertIsNone(answer.answered_by)

    def test_finish_then_unlock_with_password(self):
        self.answer(self.interview, "salvo", "si", url_name="kiosk")
        self.client.post(reverse("kiosk:finish"))
        response = self.client.get(reverse("kiosk:home"))
        self.assertContains(response, "Avisa a tu coordinador")
        self.assertNotContains(response, "Sí")  # no answers on the hand-back screen
        response = self.client.post(reverse("kiosk:unlock"), {"password": "equivocada"})
        self.assertContains(response, "La contraseña no es correcta.")
        response = self.client.post(reverse("kiosk:unlock"), {"password": PASSWORD})
        self.assertRedirects(response, self.url("interview_review", self.interview.public_id), fetch_redirect_response=False)
        self.assertNotIn(svc.KIOSK_KEY, self.client.session)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.coord1.id)
        # The coordinator can finalize; confidential answers stay hidden in supervised mode.
        self.interview.refresh_from_db()
        response = self.client.post(self.url("interview_review", self.interview.public_id), {"revision": self.interview.revision, "follow_up_decision": "no_requerido"})
        self.assertEqual(response.status_code, 302)
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.status, Interview.Status.COMPLETED)
        self.assertEqual(ParticipantSession.objects.get(interview=self.interview).state, ParticipantSession.State.CLOSED)

    def test_unlock_attempts_are_rate_limited(self):
        self.client.post(reverse("kiosk:finish"))
        for _ in range(svc.UNLOCK_MAX_ATTEMPTS):
            self.client.post(reverse("kiosk:unlock"), {"password": "x"})
        response = self.client.post(reverse("kiosk:unlock"), {"password": PASSWORD})
        self.assertContains(response, "Demasiados intentos")

    def test_unrelated_coordinator_cannot_unlock(self):
        self.client.post(reverse("kiosk:finish"))
        response = self.client.post(reverse("kiosk:unlock"), {"otra": "1", "username": self.coord3.username, "password": PASSWORD})
        self.assertContains(response, "La contraseña no es correcta.")
        self.assertIn(svc.KIOSK_KEY, self.client.session)

    def test_expired_session_is_rejected(self):
        ParticipantSession.objects.filter(interview=self.interview).update(last_activity_at=timezone.now() - timedelta(minutes=svc.KIOSK_IDLE_MINUTES + 1))
        response = self.answer(self.interview, "salvo", "si", url_name="kiosk")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.interview.answers.exists())
        self.assertContains(self.client.get(reverse("kiosk:home")), "La pantalla se cerró")

    def test_revoked_session_shows_no_data(self):
        admin = Client()
        self.login(self.admin, admin)
        ps = ParticipantSession.objects.get(interview=self.interview)
        admin.post(self.url("sessions"), {"participant_session": str(ps.public_id)})
        response = self.client.get(reverse("kiosk:home"))
        self.assertContains(response, "No hay una entrevista abierta en esta computadora")
        self.assertNotContains(response, self.p1.first_name)

    def test_next_participant_starts_clean(self):
        self.client.post(reverse("kiosk:finish"))
        self.client.post(reverse("kiosk:unlock"), {"password": PASSWORD})
        self.interview.refresh_from_db()
        self.client.post(self.url("interview_review", self.interview.public_id), {"revision": self.interview.revision, "follow_up_decision": "no_requerido"})
        self.start(self.p2, mode="supervisada")
        second = Interview.objects.get(participant=self.p2)
        self.client.post(self.url("interview_handoff", second.public_id))
        response = self.client.get(reverse("kiosk:home"))
        self.assertContains(response, "Hola, Diego")
        self.assertNotContains(response, "Mariana")
        self.assertEqual(ParticipantSession.objects.filter(state=ParticipantSession.State.ACTIVE).count(), 1)

    def test_kiosk_cannot_save_after_the_interview_is_taken_over_elsewhere(self):
        other = Client()
        self.login(self.coord1, other)
        other.post(self.url("interview_conflict", self.interview.public_id))
        self.answer(self.interview, "salvo", "si", url_name="kiosk")
        self.assertFalse(self.interview.answers.exists())
        self.assertEqual(ParticipantSession.objects.get(interview=self.interview).state, ParticipantSession.State.CLOSED)


class ServiceLevelConcurrencyTests(CampFixture):
    def test_unique_live_interview_constraint(self):
        access = resolve_access(self.coord1, self.event)
        session_a, session_b = {}, {}
        svc.start_interview(access, self.p1, mode="asistida", station=None, confirmed_consents={"assent"}, session=session_a)
        with self.assertRaises(svc.AlreadyInProgress):
            svc.start_interview(access, self.p1, mode="asistida", station=None, confirmed_consents={"assent"}, session=session_b)


class HandoffAndBackupListTests(CampFixture):
    def test_coordinator_can_switch_to_assisted_before_handing_over(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2, mode="supervisada")
        interview = Interview.objects.get(participant=self.p2)
        response = self.client.post(self.url("interview_handoff", interview.public_id), {"action": "asistida"})
        self.assertRedirects(response, self.url("interview_step", interview.public_id, 1), fetch_redirect_response=False)
        interview.refresh_from_db()
        self.assertEqual(interview.mode, Interview.Mode.ASSISTED)
        self.assertIn("_auth_user_id", self.client.session)  # still logged in: nothing was handed over
        self.assertFalse(ParticipantSession.objects.filter(interview=interview).exists())

    def test_backup_list_is_scoped_and_has_no_answers(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        self.answer(interview, "dudas", "si", extra={f"q_{self.question('dudas_detalle').id}": "RESPUESTA-PRIVADA"})
        response = self.client.get(self.url("backup_list"))
        self.assertContains(response, self.p1.full_name)
        self.assertNotContains(response, self.p3.full_name)  # another coordinator's camper
        self.assertNotContains(response, "RESPUESTA-PRIVADA")
        self.login(self.tutor2)
        response = self.client.get(self.url("backup_list"))
        self.assertContains(response, self.p4.full_name)
        self.assertNotContains(response, self.p1.full_name)
