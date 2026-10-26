"""Administration after a walk through the guide from an empty database.

Each class is one finding: what an e-mail message promised, what deleting a campero took with it, a legal
attestation recorded in one click, a scheduler nobody could see had stopped, and a retention period the
screens promised but nothing enforced.
"""

import io
import json
import re
from datetime import date, datetime, time, timedelta
from unittest import mock, skipUnless
from zoneinfo import ZoneInfo

from django import forms
from django.contrib.auth.models import User
from django.core import mail
from django.db import connection
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from core.models import Church, Role
from core.services import assign_role
from events.management.commands.purge_expired_event_data import PurgeRefused, purge_event
from events.management.commands.run_report_cutoffs import Command as SchedulerCommand
from events.models import (
    AuditEvent,
    ConsentRecord,
    Event,
    EventMembership,
    EventParticipant,
    FollowUp,
    Interview,
    InterviewStatus,
    MembershipRole,
    ReportSnapshot,
)
from events.services.audit import ACTION_LABELS
from events.services.interviews import consent_requirements
from events.services.participants import guardian_authorized
from events.services.reports import run_due_cutoffs

from .base import CampFixture, make_user
from .test_concurrency import run_in_threads

CONFIDENTIAL_SENTENCE = "Si existieran seguimientos confidenciales, también se borrarían."
CONSOLE = "django.core.mail.backends.console.EmailBackend"
FILEBASED = "django.core.mail.backends.filebased.EmailBackend"
DUMMY = "django.core.mail.backends.dummy.EmailBackend"
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
NOT_CONFIGURED = "No hay correo configurado: comparte este enlace con la persona."
MEXICO = ZoneInfo("America/Mexico_City")


def messages_of(response):
    return [str(m) for m in response.wsgi_request._messages]


# ---- "Persona agregada" says an e-mail went out only when one could -----------------------------


class MemberAddedDeliveryTests(CampFixture):
    def add(self, email="nueva@persona.mx", name="Nadia Nueva"):
        self.login(self.admin)
        return self.client.post(self.url("member_add"), {"full_name": name, "email": email, "role": "tutor", "team_name": "Equipo Este"})

    def test_console_dummy_or_file_backend_is_not_called_sent(self):
        """The console backend (the default) only writes to the server log: the link on screen is the delivery."""
        for backend in (CONSOLE, DUMMY, FILEBASED):
            with self.subTest(backend=backend), override_settings(EMAIL_BACKEND=backend):
                response = self.add(email=f"{backend.rsplit('.', 2)[-2]}@persona.mx")
                self.assertTemplateUsed(response, "events/admin/member_created.html")
                self.assertContains(response, f"Nadia Nueva ya forma parte del equipo. {NOT_CONFIGURED}")
                self.assertNotContains(response, "Le enviamos un correo")
                self.assertContains(response, f'value="{response.context["link"]}"')

    def test_console_backend_never_writes_the_link_to_the_server_log(self):
        with override_settings(EMAIL_BACKEND=CONSOLE), mock.patch("events.views.admin.send_email_safely") as send:
            self.add()
        send.assert_not_called()

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_a_real_backend_still_says_it_sent_the_email(self):
        response = self.add()
        self.assertContains(response, "Nadia Nueva ya forma parte del equipo. Le enviamos un correo para crear su contraseña.")
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_an_existing_account_that_never_set_a_password_gets_the_link_like_a_new_person(self):
        person = User.objects.create_user(username="sin.clave@prueba.mx", email="sin.clave@prueba.mx", first_name="Sina", last_name="Clave")
        person.set_unusable_password()
        person.save()
        response = self.add(email="Sin.Clave@prueba.mx", name="Otro Nombre")
        self.assertTemplateUsed(response, "events/admin/member_created.html")
        self.assertContains(response, "Sina Clave ya forma parte del equipo. Le enviamos un correo para crear su contraseña.")
        self.assertNotContains(response, "contraseña de siempre")
        self.assertEqual([m.to for m in mail.outbox], [["sin.clave@prueba.mx"]])
        self.assertIn(response.context["link"], mail.outbox[0].body)
        self.assertEqual(User.objects.filter(email__iexact="sin.clave@prueba.mx").count(), 1)

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_an_existing_account_without_password_and_no_email_configured_gets_the_link_on_screen(self):
        person = User.objects.create_user(username="sin.clave@prueba.mx", email="sin.clave@prueba.mx", first_name="Sina", last_name="Clave")
        person.set_unusable_password()
        person.save()
        response = self.add(email="sin.clave@prueba.mx")
        self.assertContains(response, f"Sina Clave ya forma parte del equipo. {NOT_CONFIGURED}")

    @override_settings(EMAIL_BACKEND=LOCMEM)
    def test_another_churchs_account_gets_one_message_whether_or_not_it_has_a_password(self):
        """Its link may only go to its owner's e-mail, and this administration may not learn if it has a password."""
        other = Church.objects.create(name="Iglesia Vecina", slug="iglesia-vecina")
        answers = []
        for email, usable in (("sin@otra.mx", False), ("con@otra.mx", True)):
            person = User.objects.create_user(username=email, email=email, first_name="Vera", last_name="Vecina")
            if usable:
                person.set_password("UnaClaveLarga-2026")
            else:
                person.set_unusable_password()
            person.save()
            assign_role(person, other, Role.PASTOR)
            self.client = Client()  # a fresh session: an unread message would carry into the next answer
            response = self.add(email=email)
            self.assertRedirects(response, self.url("team"), fetch_redirect_response=False)
            answers.append(messages_of(response))
        expected = ["Vera Vecina ya forma parte del equipo. Esta cuenta ya existe y la administra otra iglesia o el sistema; "
                    "pide a esa administración que le dé acceso."]
        self.assertEqual(answers, [expected, expected])
        self.assertEqual(len(mail.outbox), 0)


# ---- Deleting a campero says, in counts, what goes with it ------------------------------------------


class ParticipantDeleteTests(CampFixture):
    def follow_up(self, participant, kind, status=FollowUp.Status.PENDING, details=""):
        return FollowUp.objects.create(event=self.event, participant=participant, kind=kind, status=status,
                                       reason=FollowUp.Reason.SAFETY if kind == FollowUp.Kind.SAFETY else FollowUp.Reason.COORDINATOR,
                                       details=details)

    def page(self, participant):
        self.login(self.admin)
        return self.client.get(self.url("participant_delete", participant.public_id))

    def confirm_block(self, response):
        html = response.content.decode()
        match = re.search(r'<section class="confirm"[^>]*>(.*?)</section>', html, re.S)
        self.assertIsNotNone(match, "no .confirm block")
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(1))).strip()

    def test_the_page_counts_open_follow_ups_safety_reports_and_interviews_never_their_content(self):
        self.follow_up(self.p1, FollowUp.Kind.CARE, details="DETALLE-DE-SEGUIMIENTO")
        self.follow_up(self.p1, FollowUp.Kind.SAFETY, details="DETALLE-DEL-RIESGO")
        self.follow_up(self.p1, FollowUp.Kind.SAFETY, status=FollowUp.Status.DONE)  # closed: not "abierto"
        self.follow_up(self.p1, FollowUp.Kind.CARE, status=FollowUp.Status.CANCELLED)
        Interview.objects.create(event=self.event, participant=self.p1, version=self.version)
        response = self.page(self.p1)
        self.assertIn("1 seguimiento abierto, 1 reporte de riesgo abierto, 1 entrevista.", self.confirm_block(response))
        self.assertNotContains(response, "DETALLE-DE-SEGUIMIENTO")
        self.assertNotContains(response, "DETALLE-DEL-RIESGO")
        self.assertContains(response, "Entiendo que se borrará un reporte de riesgo abierto")
        self.assertEqual(len(re.findall(r"<h1\b", response.content.decode())), 1)

    def test_plural_counts_and_zero(self):
        for _ in range(2):
            self.follow_up(self.p2, FollowUp.Kind.CARE)
        self.follow_up(self.p2, FollowUp.Kind.SAFETY, status=FollowUp.Status.IN_PROGRESS)
        self.follow_up(self.p2, FollowUp.Kind.SAFETY)
        self.assertIn("2 seguimientos abiertos, 2 reportes de riesgo abiertos, 0 entrevistas.", self.confirm_block(self.page(self.p2)))
        self.assertContains(self.page(self.p2), "Entiendo que se borrarán 2 reportes de riesgo abiertos")
        response = self.page(self.p3)
        self.assertIn("0 seguimientos abiertos, 0 reportes de riesgo abiertos, 0 entrevistas.", self.confirm_block(response))
        self.assertNotContains(response, 'name="understand_safety"')

    def confidential_referral(self, participant):
        """What finalize creates from a confidential answer: a care follow-up only the care team may see."""
        return FollowUp.objects.create(event=self.event, participant=participant, kind=FollowUp.Kind.CARE, confidential=True,
                                       reason=FollowUp.Reason.SUPPORT_REQUESTED, details="DETALLE-CONFIDENCIAL", created_by=self.coord1)

    def page_html(self, participant, user=None):
        client = self.login(user or self.admin, Client())
        html = client.get(self.url("participant_delete", participant.public_id)).content.decode()
        return re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', 'name="csrfmiddlewaretoken" value=""', html)

    def test_without_confidential_access_a_confidential_referral_changes_nothing_on_the_page(self):
        """The administration sees what "Seguimiento" shows it, and a sentence that is the same for everyone."""
        self.follow_up(self.p1, FollowUp.Kind.CARE)  # an ordinary one, counted for everyone
        before = self.page_html(self.p1)
        self.confidential_referral(self.p1)
        after = self.page_html(self.p1)
        self.assertEqual(before, after)  # byte for byte, apart from the CSRF token
        self.assertIn("1 seguimiento abierto, 0 reportes de riesgo abiertos, 0 entrevistas.", after)
        self.assertIn(CONFIDENTIAL_SENTENCE, after)
        self.assertNotIn("DETALLE-CONFIDENCIAL", after)
        # Another campero with none at all reads the same counts and the same sentence.
        self.follow_up(self.p2, FollowUp.Kind.CARE)
        other = self.page_html(self.p2)
        block = lambda html: re.search(r'<section class="confirm"[^>]*>(.*?)</section>', html, re.S).group(1)  # noqa: E731
        self.assertEqual(block(other), block(after))

    def test_with_confidential_access_the_referral_is_counted_and_the_box_rule_holds(self):
        care_admin = make_user("admin.cuidado@prueba.mx", "Alma", "Cuidado")
        EventMembership.objects.create(event=self.event, user=care_admin, role=MembershipRole.ADMIN, can_view_sensitive=True)
        self.confidential_referral(self.p1)
        self.follow_up(self.p1, FollowUp.Kind.SAFETY)
        html = self.page_html(self.p1, care_admin)
        self.assertIn("1 seguimiento abierto, 1 reporte de riesgo abierto, 0 entrevistas.", html)
        self.assertIn(CONFIDENTIAL_SENTENCE, html)
        self.assertIn("Entiendo que se borrará un reporte de riesgo abierto", html)
        self.assertNotIn("DETALLE-CONFIDENCIAL", html)
        client = self.login(care_admin, Client())
        url = self.url("participant_delete", self.p1.public_id)
        self.assertEqual(client.post(url, {"confirm_name": "Mariana López Ruiz"}).status_code, 200)
        self.assertTrue(EventParticipant.objects.filter(pk=self.p1.pk).exists())
        client.post(url, {"confirm_name": "Mariana López Ruiz", "understand_safety": "on"})
        self.assertFalse(EventParticipant.objects.filter(pk=self.p1.pk).exists())

    def test_an_open_safety_report_needs_the_extra_box(self):
        self.follow_up(self.p1, FollowUp.Kind.SAFETY)
        self.login(self.admin)
        url = self.url("participant_delete", self.p1.public_id)
        response = self.client.post(url, {"confirm_name": "Mariana López Ruiz"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(EventParticipant.objects.filter(pk=self.p1.pk).exists())
        self.assertContains(response, "Hay un problema")
        self.assertContains(response, "Marca esta casilla para borrar sus datos junto con el reporte abierto.")
        response = self.client.post(url, {"confirm_name": "mariana lópez ruiz", "understand_safety": "on"})
        self.assertRedirects(response, self.url("participants"), fetch_redirect_response=False)
        self.assertFalse(EventParticipant.objects.filter(pk=self.p1.pk).exists())
        self.assertFalse(FollowUp.objects.filter(participant_id=self.p1.pk).exists())

    def test_a_report_filed_after_the_page_opened_still_needs_its_box(self):
        self.page(self.p2)  # no report yet: no box on screen
        self.follow_up(self.p2, FollowUp.Kind.SAFETY)
        response = self.client.post(self.url("participant_delete", self.p2.public_id), {"confirm_name": "Diego Hernández Cruz"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(EventParticipant.objects.filter(pk=self.p2.pk).exists())

    def test_without_an_open_report_the_name_is_enough_and_a_wrong_name_is_refused(self):
        self.follow_up(self.p3, FollowUp.Kind.SAFETY, status=FollowUp.Status.DONE)
        self.login(self.admin)
        url = self.url("participant_delete", self.p3.public_id)
        response = self.client.post(url, {"confirm_name": "Sofía García"})
        self.assertContains(response, "Escribe su nombre completo exactamente como aparece.")
        self.assertTrue(EventParticipant.objects.filter(pk=self.p3.pk).exists())
        self.assertRedirects(self.client.post(url, {"confirm_name": " Sofía García Pérez "}), self.url("participants"), fetch_redirect_response=False)
        self.assertFalse(EventParticipant.objects.filter(pk=self.p3.pk).exists())


# ---- "Marcar como recibida" is a confirmed attestation, with an audited undo ----------------------


class GuardianAuthorizationTests(CampFixture):
    def guardian_records(self, participant):
        return list(ConsentRecord.objects.filter(participant=participant, kind=ConsentRecord.Kind.GUARDIAN).order_by("id").values_list("status", "recorded_by", "notes"))

    def test_the_detail_page_links_to_a_confirmation_instead_of_recording_in_one_click(self):
        self.login(self.coord1)
        html = self.client.get(self.url("participant_detail", self.p_minor_no_auth.public_id)).content.decode()
        action = self.url("guardian_authorization", self.p_minor_no_auth.public_id)
        self.assertNotIn(f'action="{action}"', html)
        self.assertIn(f'<a href="{action}">Marcar como recibida</a>', html)

    def test_the_confirmation_says_what_is_attested_and_by_whom_and_records_nothing(self):
        self.login(self.coord1)
        response = self.client.get(self.url("guardian_authorization", self.p_minor_no_auth.public_id))
        self.assertContains(response, "Confirmas que la autorización firmada del padre, la madre o el tutor legal de Lucía Torres Gómez está en manos de la iglesia.")
        self.assertContains(response, "Queda registrado a nombre de")
        self.assertContains(response, "Carla Uno")
        self.assertContains(response, 'name="confirmar" value="si"')
        self.assertEqual(self.guardian_records(self.p_minor_no_auth), [])

    def test_a_post_without_the_confirmation_records_nothing(self):
        """A one-click form left open in another tab lands on the confirmation page."""
        self.login(self.coord1)
        url = self.url("guardian_authorization", self.p_minor_no_auth.public_id)
        response = self.client.post(url)
        self.assertRedirects(response, url, fetch_redirect_response=False)
        self.assertEqual(self.guardian_records(self.p_minor_no_auth), [])
        self.assertFalse(AuditEvent.objects.filter(action__startswith="autorizacion").exists())

    def test_confirming_records_it_once_in_the_coordinators_name_and_audits_it(self):
        self.login(self.coord1)
        url = self.url("guardian_authorization", self.p_minor_no_auth.public_id)
        response = self.client.post(url, {"confirmar": "si"})
        self.assertRedirects(response, self.url("participant_detail", self.p_minor_no_auth.public_id), fetch_redirect_response=False)
        self.client.post(url, {"confirmar": "si"})  # a double click
        self.assertEqual(self.guardian_records(self.p_minor_no_auth), [(ConsentRecord.Status.GRANTED, self.coord1.id, "")])
        audits = list(AuditEvent.objects.filter(action="autorizacion_registrada").values_list("actor", "summary"))
        self.assertEqual(audits, [(self.coord1.id, f"Registró la autorización del padre, madre o tutor legal del campero #{self.p_minor_no_auth.code}")])
        detail = self.client.get(self.url("participant_detail", self.p_minor_no_auth.public_id))
        self.assertContains(detail, "Registrada por Carla Uno")
        self.assertContains(detail, "Quitar la autorización")

    def test_the_service_records_it_once_even_when_two_requests_pass_the_view_check(self):
        """Two tabs confirming at once both see "not yet" in the view; the service, under a row lock, writes one."""
        from events.services.participants import record_guardian_authorization, withdraw_guardian_authorization

        first = record_guardian_authorization(self.p_minor_no_auth, actor=self.coord1)
        second = record_guardian_authorization(self.p_minor_no_auth, actor=self.admin)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(AuditEvent.objects.filter(action="autorizacion_registrada").count(), 1)
        self.assertIsNotNone(withdraw_guardian_authorization(self.p_minor_no_auth, actor=self.coord1, reason="Por error"))
        self.assertIsNone(withdraw_guardian_authorization(self.p_minor_no_auth, actor=self.coord1, reason="Otra vez"))
        self.assertEqual(AuditEvent.objects.filter(action="autorizacion_retirada").count(), 1)

    def test_removing_it_needs_a_reason_and_is_audited_without_the_reason(self):
        self.login(self.coord1)
        url = self.url("guardian_authorization_withdraw", self.p1.public_id)
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(len(re.findall(r"<h1\b", page.content.decode())), 1)
        response = self.client.post(url, {"reason": "   "})
        self.assertContains(response, "Escribe por qué quitas la autorización.")
        self.assertTrue(guardian_authorized(self.p1))
        response = self.client.post(url, {"reason": "Se marcó por error en la lista de autorizaciones"})
        self.assertRedirects(response, self.url("participant_detail", self.p1.public_id), fetch_redirect_response=False)
        self.assertFalse(guardian_authorized(self.p1))
        self.assertEqual(self.guardian_records(self.p1)[-1],
                         (ConsentRecord.Status.REVOKED, self.coord1.id, "Se marcó por error en la lista de autorizaciones"))
        row = AuditEvent.objects.get(action="autorizacion_retirada")
        self.assertEqual(row.summary, f"Quitó la autorización del padre, madre o tutor legal del campero #{self.p1.code}")
        self.assertNotIn("error", row.summary + str(row.metadata))
        detail = self.client.get(self.url("participant_detail", self.p1.public_id))
        self.assertContains(detail, "Marcar como recibida")

    def test_removing_it_leaves_a_completed_interview_alone_and_only_changes_future_checks(self):
        completed_at = timezone.now() - timedelta(hours=2)
        interview = Interview.objects.create(event=self.event, participant=self.p1, version=self.version,
                                             status=Interview.Status.COMPLETED, completed_at=completed_at, conducted_by=self.coord1)
        EventParticipant.objects.filter(pk=self.p1.pk).update(interview_status=InterviewStatus.COMPLETED)
        self.login(self.coord1)
        self.client.post(self.url("guardian_authorization_withdraw", self.p1.public_id), {"reason": "La familia retiró su permiso"})
        self.assertFalse(guardian_authorized(self.p1))
        interview.refresh_from_db()
        self.assertEqual((interview.status, interview.completed_at), (Interview.Status.COMPLETED, completed_at))
        self.assertEqual(EventParticipant.objects.get(pk=self.p1.pk).interview_status, InterviewStatus.COMPLETED)
        # The next interview of a camper whose authorization was removed asks for it again.
        minor = self.p_minor_no_auth
        self.client.post(self.url("guardian_authorization", minor.public_id), {"confirmar": "si"})
        self.client.post(self.url("guardian_authorization_withdraw", minor.public_id), {"reason": "Se marcó por error"})
        guardian = next(r for r in consent_requirements(EventParticipant.objects.get(pk=minor.pk)) if r.key == "guardian")
        self.assertEqual((guardian.satisfied, guardian.confirm_now), (False, True))
        self.choose_station()
        self.client.post(self.url("interview_start", minor.public_id), {"mode": "asistida", "consent_assent": "on"})
        self.assertFalse(Interview.objects.filter(participant=minor).exists())
        self.client.post(self.url("interview_start", minor.public_id), {"mode": "asistida", "consent_assent": "on", "consent_guardian": "on"})
        self.assertTrue(Interview.objects.filter(participant=minor).exists())  # what blocked it was the authorization

    def test_only_who_can_edit_the_campero_can_attest_or_remove(self):
        for user in (self.coord2, self.tutor1, self.pastor):
            client = self.login(user, Client())
            for name, participant in (("guardian_authorization", self.p_minor_no_auth), ("guardian_authorization_withdraw", self.p1)):
                with self.subTest(user=user.email, page=name):  # 404 for a coordinador who cannot see the campero at all
                    self.assertIn(client.get(self.url(name, participant.public_id)).status_code, (403, 404))
                    self.assertIn(client.post(self.url(name, participant.public_id), {"confirmar": "si", "reason": "x"}).status_code, (403, 404))
        self.assertTrue(guardian_authorized(self.p1))
        self.assertFalse(guardian_authorized(self.p_minor_no_auth))
        admin = self.login(self.admin, Client())
        self.assertEqual(admin.get(self.url("guardian_authorization", self.p_minor_no_auth.public_id)).status_code, 200)

    def test_withdrawing_during_an_interview_says_it_can_finish_and_names_it_in_the_log(self):
        """The campero is already talking: the interview is not stopped, but the page and the log say so."""
        self.login(self.coord1)
        self.choose_station()
        minor = self.p_minor_no_auth
        self.client.post(self.url("interview_start", minor.public_id), {"mode": "asistida", "consent_assent": "on", "consent_guardian": "on"})
        interview = Interview.objects.get(participant=minor)
        url = self.url("guardian_authorization_withdraw", minor.public_id)
        self.assertContains(self.client.get(url), "Hay una entrevista en curso; se podrá terminar. Las siguientes necesitarán una autorización nueva.")
        self.client.post(url, {"reason": "La familia retiró su permiso"})
        self.assertFalse(guardian_authorized(minor))
        row = AuditEvent.objects.get(action="autorizacion_retirada")
        self.assertEqual(row.metadata, {"interview": str(interview.public_id)})
        self.assertIn("con una entrevista en curso", row.summary)
        self.assertEqual(FollowUp.objects.filter(participant=minor).count(), 0)
        self.answer(interview, "salvo", "si")  # the coordinator keeps going
        interview.refresh_from_db()
        self.assertEqual(interview.status, Interview.Status.IN_PROGRESS)
        self.assertGreaterEqual(interview.answers.count(), 1)

    def test_without_an_interview_in_progress_the_page_does_not_mention_one(self):
        self.login(self.coord1)
        url = self.url("guardian_authorization_withdraw", self.p1.public_id)
        self.assertNotContains(self.client.get(url), "Hay una entrevista en curso")
        self.client.post(url, {"reason": "Por error"})
        self.assertEqual(AuditEvent.objects.get(action="autorizacion_retirada").metadata, {})

    def test_the_withdrawal_reason_is_in_the_campers_own_data_export(self):
        care_admin = make_user("admin.cuidado@prueba.mx", "Alma", "Cuidado")
        EventMembership.objects.create(event=self.event, user=care_admin, role=MembershipRole.ADMIN, can_view_sensitive=True)
        self.login(self.coord1)
        self.client.post(self.url("guardian_authorization_withdraw", self.p1.public_id), {"reason": "La familia retiró su permiso"})
        data = json.loads(self.login(care_admin, Client()).get(self.url("participant_data_export", self.p1.public_id)).content)
        revoked = [a for a in data["autorizaciones"] if a["estado"] == "Revocado"]
        self.assertEqual([a["notas"] for a in revoked], ["La familia retiró su permiso"])
        # The export itself keeps its rule: confidential access only.
        self.assertEqual(self.login(self.admin, Client()).get(self.url("participant_data_export", self.p1.public_id)).status_code, 403)

    def test_both_actions_have_a_label_in_the_activity_log(self):
        self.assertIn("autorizacion_registrada", ACTION_LABELS)
        self.assertIn("autorizacion_retirada", ACTION_LABELS)
        self.assertEqual(len(set(ACTION_LABELS.values())), len(ACTION_LABELS))  # the filter cannot show two identical labels


# ---- "Cortes automáticos": whether the scheduler is running -----------------------------------------


def local(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=MEXICO)


class SchedulerRowTests(CampFixture):
    DAY = date(2026, 11, 5)

    def setUp(self):
        Event.objects.filter(pk=self.event.pk).update(
            starts_on=self.DAY, ends_on=self.DAY + timedelta(days=2), report_window_start=time(8), report_window_end=time(20),
            report_interval_minutes=120, reports_enabled=True, status=Event.Status.ACTIVE,
            created_at=local(self.DAY, 0) - timedelta(days=3), updated_at=local(self.DAY, 0) - timedelta(days=3),
            procedures_approved_at=local(self.DAY, 0) - timedelta(days=3),
        )
        Event.objects.filter(pk=self.other_event.pk).update(reports_enabled=False)
        self.event.refresh_from_db()

    def save_event_at(self, at, **fields):
        """What the settings form does when its status or its cutoffs change: Event.save(), at that moment."""
        with mock.patch("django.utils.timezone.now", return_value=at):
            event = Event.objects.get(pk=self.event.pk)
            for name, value in fields.items():
                setattr(event, name, value)
            event.save()

    def scheduler(self, start, end):
        """The scheduler service, every 5 minutes from start to end."""
        at = start
        while at <= end:
            with mock.patch("django.utils.timezone.now", return_value=at):
                run_due_cutoffs(at)
            at += timedelta(minutes=5)

    def post_settings(self, at, which, **changes):
        """"Configuración" as a person uses it: the form with its current values, some changed, then save."""
        from events.forms import EventForm, EventSettingsForm

        form_class, prefix = {"basics": (EventForm, "basics"), "settings": (EventSettingsForm, "cfg")}[which]
        with mock.patch("django.utils.timezone.now", return_value=at):
            form = form_class(instance=Event.objects.get(pk=self.event.pk), prefix=prefix)
            data = {"form": which}
            for name, field in form.fields.items():
                value = changes.get(name, form[name].value())
                if isinstance(field, forms.BooleanField):
                    if value:
                        data[f"{prefix}-{name}"] = "on"
                    continue
                if isinstance(value, time):
                    value = value.strftime("%H:%M")
                elif isinstance(value, date):
                    value = value.isoformat()
                data[f"{prefix}-{name}"] = "" if value is None else value
            response = self.login(self.admin, Client()).post(self.url("settings"), data)
        self.assertEqual(response.status_code, 302, f"{which} not saved")

    def row_or_none(self, now):
        with mock.patch("django.utils.timezone.now", return_value=now):
            html = self.login(self.admin, Client()).get(self.url("admin_home")).content.decode()
        return 'id="cortes-automaticos"' in html

    def snapshot(self, at, trigger=ReportSnapshot.Trigger.AUTOMATIC, status=ReportSnapshot.Status.READY):
        return ReportSnapshot.objects.create(
            event=self.event, scope_key="evento", scope_label="Todo el campamento", trigger=trigger, status=status,
            cutoff_at=at, interval_minutes=120, metrics_version="1",
        )

    def row(self, now):
        with mock.patch("django.utils.timezone.now", return_value=now):
            client = self.login(self.admin, Client())
            html = client.get(self.url("admin_home")).content.decode()
            fragment = client.get(self.url("admin_home"), HTTP_X_REQUESTED_WITH="fetch").content.decode()
        match = re.search(r'<li class="row( row--attention)?" id="cortes-automaticos">(.*?)</li>', html, re.S)
        self.assertIsNotNone(match, "no 'Cortes automáticos' row")
        self.assertIn('id="cortes-automaticos"', fragment)  # the live region carries it too
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(2))).strip()
        return bool(match.group(1)), text

    def test_the_newest_automatic_cutoff_is_shown(self):
        self.snapshot(local(self.DAY, 12))
        self.snapshot(local(self.DAY, 14))
        attention, text = self.row(local(self.DAY, 14, 20))
        self.assertFalse(attention)
        self.assertIn("Último corte automático: hoy 14:00.", text)

    def test_a_missed_slot_more_than_15_minutes_ago_marks_the_row(self):
        self.snapshot(local(self.DAY, 12))
        attention, text = self.row(local(self.DAY, 14, 16))
        self.assertTrue(attention)
        self.assertIn("El programador de cortes no ha corrido desde las 12:00. Puedes generar un corte a mano en Reportes.", text)

    def test_within_15_minutes_of_the_slot_it_is_not_late_yet(self):
        self.snapshot(local(self.DAY, 12))
        attention, text = self.row(local(self.DAY, 14, 14))
        self.assertFalse(attention)
        self.assertIn("Último corte automático: hoy 12:00.", text)

    def test_a_manual_cutoff_does_not_hide_a_stopped_scheduler(self):
        self.snapshot(local(self.DAY, 12))
        self.snapshot(local(self.DAY, 14, 5), trigger=ReportSnapshot.Trigger.MANUAL)
        attention, _ = self.row(local(self.DAY, 14, 30))
        self.assertTrue(attention)

    def test_a_failed_automatic_cutoff_still_shows_the_scheduler_ran(self):
        self.snapshot(local(self.DAY, 12))
        self.snapshot(local(self.DAY, 14), status=ReportSnapshot.Status.FAILED)
        attention, text = self.row(local(self.DAY, 14, 40))
        self.assertFalse(attention)
        self.assertIn("Último corte automático: hoy 14:00.", text)

    def test_a_scheduler_that_never_ran_is_noticed_after_the_first_slot(self):
        attention, text = self.row(local(self.DAY, 8, 10))
        self.assertFalse(attention)
        self.assertIn("Todavía no hay cortes automáticos.", text)
        attention, text = self.row(local(self.DAY, 8, 20))
        self.assertTrue(attention)
        self.assertIn("El programador de cortes no ha corrido: el corte de las 08:00 no se generó.", text)

    def test_overnight_the_last_cutoff_of_yesterday_is_not_late(self):
        self.snapshot(local(self.DAY, 20))
        attention, text = self.row(local(self.DAY + timedelta(days=1), 7, 50))
        self.assertFalse(attention)
        self.assertIn("Último corte automático: ayer 20:00.", text)
        attention, text = self.row(local(self.DAY + timedelta(days=1), 8, 30))
        self.assertTrue(attention)
        self.assertIn("no ha corrido desde ayer a las 20:00.", text)

    def test_the_row_is_hidden_when_the_event_is_not_in_course_or_cutoffs_are_off(self):
        """The scheduler has nothing to do then: any sentence about it would be untrue."""
        self.snapshot(local(self.DAY, 12))
        self.assertTrue(self.row_or_none(local(self.DAY, 13)))
        for status in (Event.Status.DRAFT, Event.Status.CLOSED, Event.Status.ARCHIVED):
            Event.objects.filter(pk=self.event.pk).update(status=status)
            self.assertFalse(self.row_or_none(local(self.DAY, 18)), status)
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.ACTIVE, reports_enabled=False)
        self.assertFalse(self.row_or_none(local(self.DAY, 18)))

    def test_an_event_opened_mid_morning_owes_nothing_for_earlier_slots(self):
        """In preparation until 10:40 while the scheduler ran all morning: no alarm about 08:00 or 10:00."""
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.DRAFT)
        self.scheduler(local(self.DAY, 0), local(self.DAY, 10, 35))
        self.save_event_at(local(self.DAY, 10, 40), status=Event.Status.ACTIVE)
        self.scheduler(local(self.DAY, 10, 40), local(self.DAY, 10, 50))
        self.assertFalse(ReportSnapshot.objects.filter(event=self.event).exists())  # 10:00 is past its grace: nothing was due
        attention, text = self.row(local(self.DAY, 10, 52))
        self.assertFalse(attention, text)
        self.assertIn("Todavía no hay cortes automáticos.", text)
        self.scheduler(local(self.DAY, 10, 55), local(self.DAY, 12, 5))
        attention, text = self.row(local(self.DAY, 12, 20))
        self.assertFalse(attention, text)
        self.assertIn("Último corte automático: hoy 12:00.", text)
        # Then the scheduler really stops: 14:00 is missed.
        attention, text = self.row(local(self.DAY, 14, 20))
        self.assertTrue(attention)
        self.assertIn("El programador de cortes no ha corrido desde las 12:00.", text)

    def test_through_the_settings_screen_opening_late_and_pausing_cutoffs_raise_no_alarm(self):
        """The same two flows through "Configuración", which saves the event (and so its updated_at)."""
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.DRAFT)
        self.scheduler(local(self.DAY, 0), local(self.DAY, 10, 35))
        self.post_settings(local(self.DAY, 10, 40), "basics", status=Event.Status.ACTIVE)
        self.scheduler(local(self.DAY, 10, 40), local(self.DAY, 10, 50))
        attention, text = self.row(local(self.DAY, 10, 52))
        self.assertFalse(attention, text)
        self.scheduler(local(self.DAY, 10, 55), local(self.DAY, 12, 10))
        self.post_settings(local(self.DAY, 12, 15), "settings", reports_enabled=False)
        self.scheduler(local(self.DAY, 12, 15), local(self.DAY, 16, 40))
        self.post_settings(local(self.DAY, 16, 45), "settings", reports_enabled=True)
        self.scheduler(local(self.DAY, 16, 45), local(self.DAY, 16, 50))
        attention, text = self.row(local(self.DAY, 16, 52))
        self.assertFalse(attention, text)
        self.assertIn("Último corte automático: hoy 12:00.", text)

    def test_cutoffs_turned_off_and_on_owe_nothing_for_the_gap(self):
        self.scheduler(local(self.DAY, 7, 55), local(self.DAY, 12, 10))  # 08:00, 10:00, 12:00
        self.save_event_at(local(self.DAY, 12, 15), reports_enabled=False)
        self.scheduler(local(self.DAY, 12, 15), local(self.DAY, 16, 40))
        self.assertFalse(self.row_or_none(local(self.DAY, 15)))
        self.save_event_at(local(self.DAY, 16, 45), reports_enabled=True)
        self.scheduler(local(self.DAY, 16, 45), local(self.DAY, 16, 50))
        attention, text = self.row(local(self.DAY, 16, 52))
        self.assertFalse(attention, text)
        self.assertIn("Último corte automático: hoy 12:00.", text)
        # 18:00 is owed: missing it is named as such, not blamed on the hours the cutoffs were off.
        attention, text = self.row(local(self.DAY, 18, 20))
        self.assertTrue(attention)
        self.assertIn("El programador de cortes no ha corrido: el corte de las 18:00 no se generó.", text)


# ---- Retention: the scheduler purges finished events once a day ------------------------------------


class RetentionTickTests(CampFixture):
    DAY = date(2026, 11, 5)

    def finished_event(self, slug, status, ends_on=date(2026, 1, 10), retention_days=30):
        event = Event.objects.create(church=self.church, name=slug, slug=slug, status=status, starts_on=ends_on - timedelta(days=2),
                                     ends_on=ends_on, retention_days=retention_days, reports_enabled=False)
        for n in range(1, 4):
            EventParticipant.objects.create(event=event, number=n, full_name=f"Campero {slug} {n}", age=15, room="1", church_group="Centro")
        return event

    def tick(self, command, at):
        with mock.patch("django.utils.timezone.now", return_value=at):
            command.run_once()

    def test_the_purge_runs_at_most_once_a_day_after_3_and_never_for_an_active_event(self):
        self.finished_event("cerrado", Event.Status.CLOSED)
        self.finished_event("archivado", Event.Status.ARCHIVED)
        active = self.finished_event("sigue-activo", Event.Status.ACTIVE)
        draft = self.finished_event("borrador", Event.Status.DRAFT)
        command = SchedulerCommand(stdout=io.StringIO())
        day2 = self.DAY + timedelta(days=1)
        ticks = [local(self.DAY, 2, 55), local(self.DAY, 3, 1), local(self.DAY, 3, 6), local(self.DAY, 23, 50),
                 local(day2, 0, 5), local(day2, 2, 59), local(day2, 3, 4), local(day2, 9)]
        with mock.patch("events.management.commands.run_report_cutoffs.purge_event", return_value=3) as purge:
            for at in ticks:
                self.tick(command, at)
        purged = [c.args[0].slug for c in purge.call_args_list]
        self.assertEqual(sorted(purged), sorted(["cerrado", "archivado"] * 2))  # day 1 at 03:01 and day 2 at 03:04
        self.assertNotIn(active.slug, purged)
        self.assertNotIn(draft.slug, purged)
        self.assertEqual(sum(1 for line in command.stdout.getvalue().splitlines() if "eventos terminados revisados" in line), 2)

    def test_a_due_finished_event_is_really_purged_and_an_active_one_kept(self):
        closed = self.finished_event("cerrado", Event.Status.CLOSED)
        not_due = self.finished_event("reciente", Event.Status.CLOSED, ends_on=self.DAY - timedelta(days=10))
        active = self.finished_event("sigue-activo", Event.Status.ACTIVE)
        no_end = self.finished_event("sin-fin", Event.Status.CLOSED)
        Event.objects.filter(pk=no_end.pk).update(ends_on=None)
        out = io.StringIO()
        self.tick(SchedulerCommand(stdout=out), local(self.DAY, 3, 5))
        self.assertEqual(EventParticipant.objects.filter(event=closed).count(), 0)
        closed.refresh_from_db()
        self.assertIsNotNone(closed.data_purged_at)
        self.assertTrue(AuditEvent.objects.filter(event=closed, action="campero_eliminado", summary__contains="periodo de conservación").exists())
        for kept in (not_due, active, no_end):
            self.assertEqual(EventParticipant.objects.filter(event=kept).count(), 3, kept.slug)
        self.assertIn("eventos terminados revisados: 2 · eventos con datos vencidos borrados: 1 · camperos borrados: 3", out.getvalue())

    def test_an_open_safety_report_or_confidential_referral_keeps_the_data_until_closed(self):
        report_event = self.finished_event("con-reporte", Event.Status.CLOSED)
        referral_event = self.finished_event("con-confidencial", Event.Status.ARCHIVED)
        closed_report_event = self.finished_event("reporte-atendido", Event.Status.CLOSED)
        report = FollowUp.objects.create(event=report_event, participant=report_event.participants.first(), kind=FollowUp.Kind.SAFETY,
                                         reason=FollowUp.Reason.SAFETY, status=FollowUp.Status.IN_PROGRESS)
        FollowUp.objects.create(event=referral_event, participant=referral_event.participants.first(), kind=FollowUp.Kind.CARE,
                                confidential=True, reason=FollowUp.Reason.SUPPORT_REQUESTED)
        FollowUp.objects.create(event=closed_report_event, participant=closed_report_event.participants.first(), kind=FollowUp.Kind.SAFETY,
                                reason=FollowUp.Reason.SAFETY, status=FollowUp.Status.DONE)
        out = io.StringIO()
        command = SchedulerCommand(stdout=out)
        self.tick(command, local(self.DAY, 3, 5))
        self.assertEqual(EventParticipant.objects.filter(event=report_event).count(), 3)
        self.assertEqual(EventParticipant.objects.filter(event=referral_event).count(), 3)
        self.assertEqual(EventParticipant.objects.filter(event=closed_report_event).count(), 0)
        log = out.getvalue()
        for slug in ("con-reporte", "con-confidencial"):
            self.assertEqual(sum(1 for line in log.splitlines() if f"conservación: {slug} conserva sus datos" in line), 1, slug)
        # Attended the next afternoon: purged on the next day's pass, not before.
        FollowUp.objects.filter(pk=report.pk).update(status=FollowUp.Status.DONE)
        self.tick(command, local(self.DAY, 15))
        self.assertEqual(EventParticipant.objects.filter(event=report_event).count(), 3)
        self.tick(command, local(self.DAY + timedelta(days=1), 3, 5))
        self.assertEqual(EventParticipant.objects.filter(event=report_event).count(), 0)

    def test_a_stale_copy_of_the_event_cannot_purge_or_audit_twice(self):
        """Two runs that loaded the event before either purged it: the second finds it purged under the lock."""
        event = self.finished_event("dos-corridas", Event.Status.CLOSED)
        stale = Event.objects.get(pk=event.pk)
        self.assertEqual(purge_event(event, automatic=True), 3)
        with self.assertRaises(PurgeRefused):
            purge_event(stale, automatic=True)
        self.assertEqual(AuditEvent.objects.filter(event=event, action="campero_eliminado").count(), 1)

    def test_the_automatic_purge_reads_the_status_again_under_the_lock(self):
        event = self.finished_event("reabierto", Event.Status.CLOSED)
        stale = Event.objects.get(pk=event.pk)
        Event.objects.filter(pk=event.pk).update(status=Event.Status.ACTIVE)  # reopened after the run loaded it
        with self.assertRaises(PurgeRefused):
            purge_event(stale, automatic=True)
        FollowUp.objects.create(event=event, participant=event.participants.first(), kind=FollowUp.Kind.SAFETY, reason=FollowUp.Reason.SAFETY)
        Event.objects.filter(pk=event.pk).update(status=Event.Status.CLOSED)
        with self.assertRaises(PurgeRefused):  # a report filed after the check
            purge_event(Event.objects.get(pk=event.pk), automatic=True)
        self.assertEqual(EventParticipant.objects.filter(event=event).count(), 3)
        self.assertFalse(AuditEvent.objects.filter(event=event, action="campero_eliminado").exists())

    def test_the_hour_is_the_events_own(self):
        """03:00 in Tijuana is 05:00 in Mexico City: the purge waits for the event's clock."""
        event = self.finished_event("tijuana", Event.Status.CLOSED)
        Event.objects.filter(pk=event.pk).update(time_zone="America/Tijuana")
        command = SchedulerCommand(stdout=io.StringIO())
        with mock.patch("events.management.commands.run_report_cutoffs.purge_event", return_value=0) as purge:
            self.tick(command, local(self.DAY, 4, 30))  # 02:30 in Tijuana (UTC-8 in November)
            self.assertEqual(purge.call_count, 0)
            self.tick(command, local(self.DAY, 5, 5))
            self.assertEqual(purge.call_count, 1)


@skipUnless(connection.vendor == "postgresql", "Row-level locking needs PostgreSQL")
class RetentionOverlapTests(TransactionTestCase):
    def test_two_runs_purging_the_same_event_at_once_delete_and_audit_once(self):
        church = Church.objects.create(name="Iglesia", slug="iglesia-solapada")
        event = Event.objects.create(church=church, name="Solapado", slug="solapado", status=Event.Status.CLOSED,
                                     ends_on=date(2026, 1, 10), retention_days=30, reports_enabled=False)
        for n in range(1, 4):
            EventParticipant.objects.create(event=event, number=n, full_name=f"Campero {n}", age=15, room="1", church_group="Centro")
        copies = [Event.objects.get(pk=event.pk) for _ in range(4)]
        results, _ = run_in_threads([lambda copy=copy: purge_event(copy, automatic=True) for copy in copies])
        self.assertEqual(sorted(r for r in results if isinstance(r, int)), [3], results)
        self.assertEqual(sum(isinstance(r, PurgeRefused) for r in results), 3, results)
        self.assertEqual(AuditEvent.objects.filter(event=event, action="campero_eliminado").count(), 1)


# ---- New screens are on Rótulo ---------------------------------------------------------------------


class NewScreensTests(CampFixture):
    def test_the_new_screens_use_rotulo_with_one_heading(self):
        self.login(self.coord1)
        for url in (self.url("guardian_authorization", self.p_minor_no_auth.public_id), self.url("guardian_authorization_withdraw", self.p1.public_id)):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertContains(response, "css/rotulo.css", msg_prefix=url)
            self.assertEqual(len(re.findall(r"<h1\b", response.content.decode())), 1, url)
            self.assertEqual(len(re.findall(r'class="sign[ "]', response.content.decode())), 0, url)
