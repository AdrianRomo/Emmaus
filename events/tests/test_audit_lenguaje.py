"""Language and privacy findings of the UX audit (auditoria-ux/lenguaje, LANG-xx; ux, UX-xx).

* LANG-01: the default privacy notice says who reads each level as EventAccess.can_view_answer allows it, and
  events/0010 moves events still holding the previous default to it.
* LANG-03: after a safety report, the screen says only what Emmaus knows (saved; e-mail only if one can go out;
  a warning when nobody can read it).
* LANG-28: the activity log does not tell an administrador without confidential access which camperos have
  confidential information or safety reports.
* LANG-29: the campero's data export has Spanish keys a family can read.
* LANG-05: the questionnaire editor names answers by their labels; internal identifiers are folded away.
* LANG-07: the change since the previous cutoff says "pasó de 51 % a 77 %", never "+26 %".
* LANG-15 / UX-10: a coordinador without camperos is not congratulated; unconfirmed procedures show on "Mi inicio".
* LANG-04, LANG-17: the printed sheet and the event form name the slug "nombre corto".
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from importlib import import_module
from unittest import mock

from django.apps import apps as django_apps
from django.db import migrations
from django.core import mail
from django.test import Client, SimpleTestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import (
    DEFAULT_PRIVACY_NOTICE,
    AuditEvent,
    Event,
    EventMembership,
    EventParticipant,
    FollowUp,
    Interview,
    InterviewStatus,
    MembershipRole,
)
from events.services import audit
from events.services.metrics import compare, delta_sentence

from .base import PASSWORD, CampFixture, make_user

WHO_READS = (
    "Tus respuestas las lee tu coordinador y, las generales, también el equipo del campamento. "
    "Las de temas personales, solo quien te entrevistó, tu coordinador y el equipo de cuidado. "
    "Las marcadas “Confidencial”, solo el equipo de cuidado autorizado."
)
NO_CAMPEROS = "Todavía no tienes camperos. Registra al primero o pide a la administración que te los asigne."
PROCEDURES_PENDING = "Todavía no se pueden iniciar entrevistas: la administración debe confirmar los procedimientos."
SAVED = "El reporte quedó guardado. El equipo de cuidado autorizado lo verá en Emmaus. Avísales también en persona."
SAVED_NO_READERS = "El reporte quedó guardado. La administración lo verá en Seguimiento."
EMAILED = "También les mandamos un aviso por correo, sin nombres ni detalles."
NOBODY_CAN_READ = "Ahora mismo nadie en este evento tiene acceso confidencial: avisa a la administración."
CONSOLE = "django.core.mail.backends.console.EmailBackend"
SMTP = "django.core.mail.backends.smtp.EmailBackend"


# ---- LANG-01: the default privacy notice ------------------------------------------------------------


class DefaultPrivacyNoticeTests(SimpleTestCase):
    def test_it_says_who_reads_each_level(self):
        self.assertIn(WHO_READS, DEFAULT_PRIVACY_NOTICE)
        self.assertNotIn("El equipo del campamento podrá leer", DEFAULT_PRIVACY_NOTICE)

    def test_it_keeps_the_rest_of_the_notice(self):
        for kept in (
            "La iglesia organizadora registra tus respuestas para acompañarte durante y después del campamento.",
            "Puedes elegir “Prefiero no responder” en cualquier pregunta.",
            "No compartimos tus respuestas con nadie fuera de la iglesia, y ninguna inteligencia artificial las analiza.",
        ):
            self.assertIn(kept, DEFAULT_PRIVACY_NOTICE)


class WhoReadsMatchesTheAccessRulesTests(CampFixture):
    """The three sentences, checked against EventAccess.can_view_answer on the default questionnaire."""

    def test_personal_and_confidential_levels(self):
        from events.access import resolve_access

        interview = Interview.objects.create(event=self.event, participant=self.p1, version=self.version, status=Interview.Status.COMPLETED)
        personal = self.question("salvo")  # "sensible"
        confidential = self.question("apoyo")  # "confidencial"
        readers = lambda q: {u.email for u in (self.coord1, self.tutor1, self.pastor, self.admin, self.care)  # noqa: E731
                             if resolve_access(u, self.event).can_view_answer(interview, q)}
        # "Las de temas personales, solo quien te entrevistó, tu coordinador y el equipo de cuidado."
        self.assertEqual(readers(personal), {self.coord1.email, self.care.email})
        # "Las marcadas “Confidencial”, solo el equipo de cuidado autorizado."
        self.assertEqual(readers(confidential), {self.care.email})

    def test_quien_te_entrevisto_is_the_administrador_who_conducted_it(self):
        """access.py lets an administrador who conducted the interview read its "Sensible" answers, without the
        flag and without being the coordinador: the sentence names them ("quien te entrevistó")."""
        from events.access import resolve_access

        admin_access = resolve_access(self.admin, self.event)
        self.assertFalse(admin_access.can_view_sensitive)
        self.assertNotEqual(self.p1.coordinator.user, self.admin)
        interview = Interview.objects.create(
            event=self.event, participant=self.p1, version=self.version, status=Interview.Status.COMPLETED, conducted_by=self.admin
        )
        personal, confidential = self.question("salvo"), self.question("apoyo")
        readers = lambda q: {u.email for u in (self.coord1, self.tutor1, self.pastor, self.admin, self.care)  # noqa: E731
                             if resolve_access(u, self.event).can_view_answer(interview, q)}
        self.assertIn("solo quien te entrevistó, tu coordinador y el equipo de cuidado.", DEFAULT_PRIVACY_NOTICE)
        self.assertEqual(readers(personal), {self.admin.email, self.coord1.email, self.care.email})
        self.assertEqual(readers(confidential), {self.care.email})
        # Interviewing does not open the answers to anyone else: a tutor or pastor who did not conduct it reads none.
        self.assertFalse(resolve_access(self.pastor, self.event).can_view_answer(interview, personal))


class PrivacyNoticeMigrationTests(CampFixture):
    """events/0010 swaps the untouched 0009 default for the new one and leaves edited notices alone."""

    migration = import_module("events.migrations.0010_privacy_notice_who_reads")

    def test_an_event_with_the_0009_default_gets_the_new_one(self):
        previous = import_module("events.migrations.0009_copy_pass_labels").NEW_DEFAULT_PRIVACY_NOTICE
        self.assertEqual(self.migration.OLD_DEFAULT_PRIVACY_NOTICE, previous)
        Event.objects.filter(pk=self.event.pk).update(privacy_notice=previous)
        edited = previous + " Pregunta a Rebeca si tienes dudas."
        Event.objects.filter(pk=self.other_event.pk).update(privacy_notice=edited)
        self.migration.replace_old_default_notice(django_apps, None)
        self.assertEqual(Event.objects.get(pk=self.event.pk).privacy_notice, DEFAULT_PRIVACY_NOTICE)
        self.assertEqual(Event.objects.get(pk=self.other_event.pk).privacy_notice, edited)

    def test_the_frozen_new_text_is_the_current_default(self):
        self.assertEqual(self.migration.NEW_DEFAULT_PRIVACY_NOTICE, DEFAULT_PRIVACY_NOTICE)

    def test_the_step_runs_forward_only(self):
        (step,) = [op for op in self.migration.Migration.operations if isinstance(op, migrations.RunPython)]
        self.assertIs(step.code, self.migration.replace_old_default_notice)
        self.assertIs(step.reverse_code, migrations.RunPython.noop)


# ---- LANG-03: what the reporter reads after a safety report ----------------------------------------


class SafetyReportResultTests(CampFixture):
    """Inside a test transaction the alert waits for the commit, as for any caller in a transaction: nothing is
    known when the message is written, so it never mentions an e-mail. Delivery is tested below, in autocommit."""

    def send(self):
        self.login(self.coord1)
        response = self.client.post(self.url("safety_report", self.p1.public_id), {"details": "Contó algo que preocupa."}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(FollowUp.objects.filter(participant=self.p1, kind=FollowUp.Kind.SAFETY).count(), 1)
        return response, [str(m) for m in response.context["messages"]]

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_without_email_nothing_mentions_an_email(self):
        response, shown = self.send()
        self.assertEqual(shown, [SAVED])
        self.assertNotIn("correo", " ".join(shown))
        self.assertNotContains(response, "llegó al equipo")
        # Nor does the form promise one.
        form = self.client.get(self.url("safety_report", self.p1.public_id))
        self.assertNotContains(form, "por correo")
        self.assertContains(form, "La administración verá que existe un reporte, pero no lo que dice.")

    @override_settings(EMAIL_BACKEND=SMTP)
    def test_an_alert_still_waiting_for_the_commit_is_not_claimed(self):
        with mock.patch("events.services.notifications.send_email_safely", return_value=True) as sent:
            _, shown = self.send()
        self.assertEqual(shown, [SAVED])
        self.assertEqual(sent.call_count, 0)  # deferred to on_commit, which a TestCase never reaches

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_nobody_with_confidential_access_gets_a_coherent_message(self):
        EventMembership.objects.filter(event=self.event).update(can_view_sensitive=False)
        response, shown = self.send()
        self.assertEqual(shown, [SAVED_NO_READERS, NOBODY_CAN_READ])
        self.assertNotIn("equipo de cuidado autorizado lo verá", " ".join(shown))
        self.assertNotIn("Avísales", " ".join(shown))
        self.assertContains(response, 'class="notice notice--warning"')
        # What it promises is true: an administrador without the flag finds the report in "Seguimiento".
        listed = self.login(self.admin, Client()).get(self.url("follow_ups"))
        self.assertContains(listed, self.p1.full_name)

    def test_an_inactive_care_member_does_not_count(self):
        EventMembership.objects.filter(pk=self.m_care.pk).update(is_active=False)
        _, shown = self.send()
        self.assertEqual(shown, [SAVED_NO_READERS, NOBODY_CAN_READ])


class SafetyReportResultSentencesTests(CampFixture):
    """safety_report_result for each combination of readers, delivery and backend."""

    def result(self, notified):
        from events.services.interviews import safety_report_result

        return safety_report_result(self.event, notified=notified)

    @override_settings(EMAIL_BACKEND=SMTP)
    def test_sent_with_a_care_team(self):
        self.assertEqual(self.result(True), (f"{SAVED} {EMAILED}", None))
        self.assertEqual(self.result(False), (SAVED, None))

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_the_console_backend_never_counts_as_sent(self):
        self.assertEqual(self.result(True), (SAVED, None))

    @override_settings(EMAIL_BACKEND=SMTP)
    def test_sent_with_nobody_holding_the_flag(self):
        EventMembership.objects.filter(event=self.event).update(can_view_sensitive=False)
        self.assertEqual(self.result(True), (f"{SAVED_NO_READERS} {EMAILED}", NOBODY_CAN_READ))
        self.assertEqual(self.result(False), (SAVED_NO_READERS, NOBODY_CAN_READ))


class SafetyReportDeliveryTests(TransactionTestCase):
    """Autocommit, as the view runs in production: report_safety_concern sends the alert and says whether it went."""

    def setUp(self):
        from core.models import Church, Role
        from core.services import assign_role
        from events.services.questionnaire import DEFAULT_CAMP_QUESTIONNAIRE, create_questionnaire_from_definition

        church = Church.objects.create(name="Iglesia de prueba", slug="iglesia-prueba")
        version = create_questionnaire_from_definition(church, DEFAULT_CAMP_QUESTIONNAIRE)
        self.event = Event.objects.create(church=church, name="Campamento A", slug="campamento-a", questionnaire_version=version,
                                          procedures_approved_at=timezone.now())
        admin = make_user("admin@prueba.mx", "Ada", "Admin")
        assign_role(admin, church, Role.CHURCH_ADMIN)
        self.care = EventMembership.objects.create(event=self.event, user=make_user("cuidado@prueba.mx", "Celia", "Cuidado"),
                                                   role=MembershipRole.PASTOR, can_view_sensitive=True)
        self.coord = make_user("coord1@prueba.mx", "Carla", "Uno")
        coordinator = EventMembership.objects.create(event=self.event, user=self.coord, role=MembershipRole.COORDINATOR)
        self.camper = EventParticipant.objects.create(event=self.event, number=1, full_name="Mariana López Ruiz", age=19, room="1",
                                                      church_group="Centro", coordinator=coordinator)

    def send(self):
        client = Client()
        self.assertTrue(client.login(username=self.coord.username, password=PASSWORD))
        url = reverse("events:safety_report", args=[self.event.slug, self.camper.public_id])
        response = client.post(url, {"details": "Contó algo que preocupa."}, follow=True)
        self.assertEqual(response.status_code, 200)
        return [str(m) for m in response.context["messages"]]

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_a_delivered_alert_is_mentioned(self):
        self.assertEqual(self.send(), [f"{SAVED} {EMAILED}"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(sorted(mail.outbox[0].to), ["admin@prueba.mx", "cuidado@prueba.mx"])

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_a_failed_send_is_not_mentioned(self):
        with mock.patch("core.services.send_mail", side_effect=OSError("el servidor de correo no responde")), \
                self.assertLogs("core.services", "ERROR"):
            shown = self.send()
        self.assertEqual(shown, [SAVED])
        self.assertEqual(FollowUp.objects.filter(participant=self.camper, kind=FollowUp.Kind.SAFETY).count(), 1)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_nobody_to_write_to_is_not_mentioned(self):
        with mock.patch("events.services.notifications._care_team_emails", return_value=[]):
            self.assertEqual(self.send(), [SAVED])
        self.assertEqual(mail.outbox, [])

    @override_settings(EMAIL_BACKEND=CONSOLE)
    def test_the_console_backend_is_not_mentioned(self):
        self.assertEqual(self.send(), [SAVED])

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_nobody_with_the_flag_but_the_administration_was_written_to(self):
        EventMembership.objects.filter(pk=self.care.pk).update(can_view_sensitive=False)
        self.assertEqual(self.send(), [f"{SAVED_NO_READERS} {EMAILED}", NOBODY_CAN_READ])


# ---- LANG-28: the activity log without confidential access -----------------------------------------


class ConfidentialRowsInTheActivityLogTests(CampFixture):
    """Rows written by the real flows: a confidential referral read and updated by the care team, an
    ordinary follow-up updated by the administration, a safety report filed by a coordinador."""

    def setUp(self):
        self.secret = FollowUp.objects.create(
            event=self.event, participant=self.p1, kind=FollowUp.Kind.CARE, confidential=True,
            reason=FollowUp.Reason.SUPPORT_REQUESTED, details="Pidió hablar de su familia.",
        )
        self.ordinary = FollowUp.objects.create(event=self.event, participant=self.p3, kind=FollowUp.Kind.CARE)
        care = self.login(self.care, Client())
        self.assertEqual(care.get(self.url("follow_up_detail", self.secret.pk)).status_code, 200)
        care.post(self.url("follow_up_detail", self.secret.pk), {"status": "en_proceso", "assigned_to": "", "resolution_note": ""})
        self.login(self.admin, Client()).post(self.url("follow_up_detail", self.ordinary.pk), {"status": "en_proceso", "assigned_to": ""})
        self.login(self.coord1, Client()).post(self.url("safety_report", self.p2.public_id), {"details": "Algo preocupante."})
        self.confidential_rows = [
            f"Consultó un seguimiento confidencial del campero #{self.p1.code}",
            f"Actualizó un seguimiento del campero #{self.p1.code}",
            f"Reportó una situación de riesgo del campero #{self.p2.code}",
        ]
        stored = set(AuditEvent.objects.filter(event=self.event).values_list("summary", flat=True))
        self.assertTrue(set(self.confidential_rows) <= stored, stored)
        self.assertIn(f"Actualizó un seguimiento del campero #{self.p3.code}", stored)

    def test_an_administrador_without_the_flag_never_reads_the_campero(self):
        response = self.login(self.admin).get(self.url("audit"))
        self.assertEqual(response.status_code, 200)
        for summary in self.confidential_rows:
            self.assertNotContains(response, summary)
        self.assertNotContains(response, f"#{self.p1.code}")
        self.assertNotContains(response, f"#{self.p2.code}")
        self.assertNotContains(response, self.p1.full_name)
        self.assertContains(response, "<td>Movimiento confidencial</td>", count=3, html=False)
        # An ordinary follow-up is not confidential: the administration sees it in "Seguimiento" anyway.
        self.assertContains(response, f"Actualizó un seguimiento del campero #{self.p3.code}")
        # Filtering by the action shows the same neutral rows.
        filtered = self.client.get(self.url("audit") + "?accion=consulta_confidencial")
        self.assertNotContains(filtered, f"#{self.p1.code}")
        self.assertContains(filtered, "<td>Movimiento confidencial</td>", count=1, html=False)

    def test_an_administrador_with_the_flag_reads_every_row(self):
        care_admin = make_user("admin.cuidado@prueba.mx", "Alma", "Cuidado")
        EventMembership.objects.create(event=self.event, user=care_admin, role=MembershipRole.ADMIN, can_view_sensitive=True)
        response = self.login(care_admin).get(self.url("audit"))
        for summary in self.confidential_rows:
            self.assertContains(response, summary)
        self.assertNotContains(response, "Movimiento confidencial")

    def test_a_row_about_a_follow_up_that_no_longer_exists_is_neutral(self):
        from events.access import resolve_access

        self.ordinary.delete()
        rows = audit.with_shown_summaries(AuditEvent.objects.filter(event=self.event, action="seguimiento"), resolve_access(self.admin, self.event))
        self.assertEqual({r.shown_summary for r in rows}, {"Movimiento confidencial"})
        # The stored rows are never changed.
        self.assertTrue(AuditEvent.objects.filter(summary=f"Actualizó un seguimiento del campero #{self.p3.code}").exists())

    def test_the_decision_on_an_interview_stays_readable(self):
        """"seguimiento" is also written for the follow-up decision of an interview, which is not confidential."""
        from events.access import resolve_access

        interview = Interview.objects.create(event=self.event, participant=self.p4, version=self.version)
        audit.record(self.coord3, self.event, "seguimiento", f"Decidió el seguimiento del campero #{self.p4.code}", interview)
        rows = audit.with_shown_summaries(AuditEvent.objects.filter(event=self.event, object_type="Interview"), resolve_access(self.admin, self.event))
        self.assertEqual([r.shown_summary for r in rows], [f"Decidió el seguimiento del campero #{self.p4.code}"])


# ---- LANG-29: the family's data export --------------------------------------------------------------

MONTHS = "(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)"
DATE = re.compile(rf"^\d{{1,2}} de {MONTHS} de \d{{4}}, \d{{2}}:\d{{2}}:\d{{2}} \(hora de Ciudad de México\)$")


class FamilyExportTests(CampFixture):
    def setUp(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p1)
        self.interview = Interview.objects.get(participant=self.p1)
        self.answer(self.interview, "salvo", "si")
        self.care_admin = make_user("admin.cuidado@prueba.mx", "Alma", "Cuidado")
        EventMembership.objects.create(event=self.event, user=self.care_admin, role=MembershipRole.ADMIN, can_view_sensitive=True)

    def export(self):
        response = self.login(self.care_admin, Client()).get(self.url("participant_data_export", self.p1.public_id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Disposition"], f'attachment; filename="campero-{self.p1.code}.json"')
        return json.loads(response.content)

    def test_the_keys_are_spanish_words(self):
        data = self.export()
        self.assertEqual(
            list(data),
            ["evento", "número de campero", "nombre", "edad", "cuarto", "iglesia o grupo", "fecha de registro", "entrevista", "autorizaciones"],
        )
        self.assertEqual(list(data["entrevista"]), ["estado", "versión del cuestionario", "fecha en que se completó", "respuestas"])
        self.assertEqual(list(data["entrevista"]["respuestas"][0]), ["pregunta", "respuesta", "fecha"])
        self.assertEqual(list(data["autorizaciones"][0]), ["tipo", "estado", "fecha", "notas"])

    def test_no_identifier_style_key_in_the_top_two_levels(self):
        data = self.export()
        second = [key for value in data.values() if isinstance(value, dict) for key in value]
        second += [key for value in data.values() if isinstance(value, list) for item in value for key in item]
        for key in [*data, *second]:
            self.assertNotIn("_", key)
            self.assertNotRegex(key, r"^[a-z]+(_[a-z]+)+$")
        for old in ("numero", "iglesia_o_grupo", "registro", "version_cuestionario", "completada"):
            self.assertNotIn(old, [*data, *data["entrevista"]])

    def test_the_content_is_all_there_and_dates_are_written_out(self):
        data = self.export()
        self.assertEqual(data["evento"], self.event.name)
        self.assertEqual(data["número de campero"], self.p1.code)
        self.assertEqual((data["nombre"], data["edad"], data["cuarto"], data["iglesia o grupo"]), ("Mariana López Ruiz", 15, "1", "Iglesia Centro"))
        self.assertRegex(data["fecha de registro"], DATE)
        self.assertEqual(data["entrevista"]["estado"], "En curso")
        self.assertEqual(data["entrevista"]["versión del cuestionario"], self.version.number)
        self.assertEqual(data["entrevista"]["fecha en que se completó"], "")
        (answer,) = data["entrevista"]["respuestas"]
        self.assertEqual((answer["pregunta"], answer["respuesta"]), ("¿Te consideras salvo?", "Sí"))
        self.assertRegex(answer["fecha"], DATE)
        self.assertTrue(data["autorizaciones"])
        for item in data["autorizaciones"]:
            self.assertRegex(item["fecha"], DATE)
        self.assertNotRegex(json.dumps(data), r"\d{4}-\d{2}-\d{2}T")  # no ISO stamps left

    def test_dates_keep_the_seconds_in_the_events_time_zone(self):
        from zoneinfo import ZoneInfo

        moment = timezone.make_aware(datetime(2026, 10, 26, 17, 0, 30), ZoneInfo("UTC"))
        EventParticipant.objects.filter(pk=self.p1.pk).update(created_at=moment)
        self.assertEqual(self.export()["fecha de registro"], "26 de octubre de 2026, 11:00:30 (hora de Ciudad de México)")
        Event.objects.filter(pk=self.event.pk).update(time_zone="America/Tijuana")
        self.assertEqual(self.export()["fecha de registro"], "26 de octubre de 2026, 10:00:30 (hora de Tijuana)")
        Event.objects.filter(pk=self.event.pk).update(time_zone="America/Bogota")  # outside the list: named by its key
        self.assertEqual(self.export()["fecha de registro"], "26 de octubre de 2026, 12:00:30 (hora de Bogota)")


# ---- LANG-05: the questionnaire editor --------------------------------------------------------------


class QuestionnaireEditorLabelsTests(CampFixture):
    def test_conditions_name_the_answers_and_identifiers_are_folded(self):
        response = self.login(self.admin).get(self.url("version_detail", self.version.id))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("se muestra solo si en “¿Tienes dudas acerca de tu fe?” se elige “Sí”.", html)
        self.assertIn("se elige “Familia”, “Bienestar emocional”, “Vida personal”, “Relaciones”, “Sexualidad” o “Otra situación”.", html)
        self.assertNotIn("se responde", html)
        self.assertNotIn("Identificador interno: <code>", html)
        self.assertNotIn(", identificador <code>", html)
        # The codes are still there for whoever edits, inside a fold.
        folds = re.findall(r"<details class=\"details\">\s*<summary>Identificador interno \(para quien edita el cuestionario\)</summary>(.*?)</details>", html, re.S)
        self.assertEqual(len(folds), sum(len(step.questions) for step in self.steps))
        self.assertTrue(any("<code>salvo</code>" in fold and "Sí, <code>si</code>" in fold for fold in folds))
        outside = re.sub(r"<details.*?</details>", "", html, flags=re.S)
        self.assertNotIn("<code>", outside)

    def test_a_code_without_an_option_is_shown_not_dropped(self):
        question = self.question("dudas_detalle")
        question.condition_option_codes = ["si", "ya_no_existe"]
        self.assertEqual(question.condition_option_labels, ["Sí", "ya_no_existe"])

    def test_the_question_form_lists_the_identifiers_folded(self):
        from events.services.questionnaire import duplicate_version

        draft = duplicate_version(self.version, self.admin)
        question = draft.sections.first().questions.first()
        response = self.login(self.admin).get(self.url("question_edit", draft.id, question.id))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<summary>Identificador interno (para quien edita el cuestionario)</summary>", html=False)
        # The questions this one may depend on (not itself), each answer with its identifier.
        self.assertContains(response, "<dt>¿Tienes dudas acerca de tu fe?</dt><dd>Sí, <code>si</code>; No, <code>no</code>;", html=False)
        self.assertNotContains(response, "<code>no_seguro</code>")


# ---- LANG-07: the change since the previous cutoff --------------------------------------------------


class DeltaSentenceFromToTests(SimpleTestCase):
    def setUp(self):
        self.six = timezone.make_aware(datetime(2026, 10, 26, 6, 0))

    def test_interviews_and_progress_say_both_ends(self):
        self.assertEqual(
            delta_sentence({"completed": 25, "percent": 26, "percent_before": 51, "percent_now": 77}, self.six),
            "25 entrevistas más que en el corte de las 06:00. El avance pasó de 51 % a 77 %.",
        )

    def test_new_camperos_explain_the_progress_that_went_down(self):
        self.assertEqual(
            delta_sentence({"completed": 0, "percent": -3, "eligible": 5, "percent_before": 51, "percent_now": 48}, self.six),
            "Ninguna entrevista nueva desde el corte de las 06:00; el avance pasó de 51 % a 48 % porque se registraron 5 camperos más.",
        )

    def test_no_sign_before_a_figure(self):
        cases = [
            {"completed": 25, "percent": 26, "percent_before": 51, "percent_now": 77},
            {"completed": -2, "percent": -55, "percent_before": 80, "percent_now": 25},
            {"completed": 1, "percent": None},
            {"completed": 0, "percent": 4, "eligible": -1, "percent_before": 20, "percent_now": 24},
            {"completed": 0, "percent": None, "eligible": 1},
            {"completed": 0, "percent": 3},
        ]
        for delta in cases:
            sentence = delta_sentence(delta, self.six)
            self.assertNotRegex(sentence, "[+−-]\\s?\\d", sentence)

    def test_a_delta_without_both_ends_says_points(self):
        self.assertEqual(
            delta_sentence({"completed": 0, "percent": 3}),
            "Ninguna entrevista nueva desde el corte anterior; el avance subió 3 puntos porcentuales.",
        )
        self.assertEqual(
            delta_sentence({"completed": 4, "percent": -1}),
            "4 entrevistas más que en el corte anterior. El avance bajó 1 punto porcentual.",
        )

    def test_compare_gives_both_ends(self):
        before = {"progress": {"eligible": 100, "completed": 51, "in_progress": 0, "pending": 49, "percent": 51}, "follow_ups": {"open_total": 0}}
        now = {"progress": {"eligible": 100, "completed": 77, "in_progress": 0, "pending": 23, "percent": 77}, "follow_ups": {"open_total": 0}}
        delta = compare(now, before)
        self.assertEqual((delta["percent_before"], delta["percent_now"], delta["percent"]), (51, 77, 26))
        self.assertEqual(delta_sentence(delta, self.six), "26 entrevistas más que en el corte de las 06:00. El avance pasó de 51 % a 77 %.")


# ---- LANG-15 / UX-10: the coordinador's empty states -------------------------------------------------


class CoordinatorEmptyStatesTests(CampFixture):
    def setUp(self):
        self.newcomer = make_user("nuevo@prueba.mx", "Nora", "Nueva")
        EventMembership.objects.create(event=self.event, user=self.newcomer, role=MembershipRole.COORDINATOR)

    def test_without_camperos_nobody_is_congratulated(self):
        self.login(self.newcomer)
        for name in ("coord_pending", "coord_home"):
            response = self.client.get(self.url(name))
            self.assertEqual(response.status_code, 200, name)
            # "Mi inicio": the sign says what to do, the list under it only that it is empty (fix/audit-followup).
            self.assertContains(response, NO_CAMPEROS if name == "coord_pending" else "Sin camperos por entrevistar.", msg_prefix=name)
            self.assertNotContains(response, "Buen trabajo", msg_prefix=name)
        live = self.client.get(self.url("coord_home"), HTTP_X_REQUESTED_WITH="fetch")
        self.assertContains(live, "Sin camperos por entrevistar.")

    def test_with_every_campero_started_the_praise_stays(self):
        EventParticipant.objects.filter(pk=self.p3.pk).update(interview_status=InterviewStatus.COMPLETED)
        self.login(self.coord2)
        response = self.client.get(self.url("coord_pending"))
        self.assertContains(response, "No quedan entrevistas por iniciar. ¡Buen trabajo!")
        self.assertNotContains(response, NO_CAMPEROS)
        self.assertNotContains(self.client.get(self.url("coord_home")), NO_CAMPEROS)

    def test_unconfirmed_procedures_show_on_my_home(self):
        self.login(self.coord1)
        self.assertNotContains(self.client.get(self.url("coord_home")), PROCEDURES_PENDING)
        Event.objects.filter(pk=self.event.pk).update(procedures_approved_at=None)
        response = self.client.get(self.url("coord_home"))
        self.assertContains(response, f'<div class="notice notice--quiet"><p>{PROCEDURES_PENDING}</p></div>', html=False)


# ---- LANG-04, LANG-17: "nombre corto" ---------------------------------------------------------------


class ShortNameTests(CampFixture):
    def test_the_printed_sheet(self):
        self.login(self.admin)
        html = self.client.get(self.url("join_code_sign")).content.decode()
        self.assertIn(f"<li>Escribe el código. También sirve el nombre corto: {self.event.slug}.</li>", html)
        self.assertNotIn("o el nombre del evento,", html)
        Event.objects.filter(pk=self.event.pk).update(join_by_name=False)
        html = self.client.get(self.url("join_code_sign")).content.decode()
        self.assertIn("<li>Escribe el código.</li>", html)
        self.assertNotIn("nombre corto", html)

    def test_the_event_field_is_called_nombre_corto(self):
        self.assertEqual(Event._meta.get_field("slug").verbose_name, "Nombre corto para la dirección")
        form = self.login(self.admin).get(reverse("events:create"))
        self.assertEqual(form.status_code, 200)
        self.assertContains(form, "Nombre corto para la dirección")
        self.assertNotContains(form, "Identificador en la dirección web")


# ---- LANG-07 on the report pages: no signed percentage under the sentence -----------------------------

SIGNED_PERCENT = re.compile(r"[+\u2212-]\d+(?:&nbsp;|\u00a0| )%")


class ReportPagesSayFromToTests(CampFixture):
    """The cutoff had 1 of 4 (25 %); a campero joined since: 1 of 5 (20 %)."""

    def setUp(self):
        from datetime import timedelta

        from events.services.metrics import event_scope
        from events.services.reports import generate_snapshot

        now = timezone.now().replace(microsecond=0)
        EventParticipant.objects.filter(pk=self.p1.pk).update(interview_status=InterviewStatus.COMPLETED)
        EventParticipant.objects.filter(pk=self.p2.pk).update(is_active=False)
        generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=now - timedelta(hours=1))
        EventParticipant.objects.filter(pk=self.p2.pk).update(is_active=True)
        self.latest = generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=now)
        self.login(self.pastor)

    def test_reports_home(self):
        html = self.client.get(self.url("reports")).content.decode()
        self.assertIn("De 25\u00a0% a 20\u00a0% desde el corte anterior", html)
        self.assertEqual(SIGNED_PERCENT.findall(html), [])

    def test_cutoff_detail(self):
        html = self.client.get(self.url("report_detail", self.latest.public_id)).content.decode()
        self.assertIn('<th scope="row">Avance</th><td class="num">25\u00a0%</td><td class="num">20\u00a0%</td><td class="num">de 25\u00a0% a 20\u00a0%</td>', html)
        self.assertEqual(SIGNED_PERCENT.findall(html), [])


# ---- LANG-17 and LANG-05 in events/forms.py ------------------------------------------------------------


class FormWordingTests(CampFixture):
    def test_the_slug_errors_say_nombre_corto(self):
        from events.forms import EventForm

        form = EventForm(data={"name": "Otro", "kind": "campamento", "slug": "", "status": "activo"})
        self.assertEqual(form.errors["slug"], ["Escribe un nombre corto."])
        form = EventForm(data={"name": "Otro", "kind": "campamento", "slug": self.event.slug, "status": "activo"})
        self.assertEqual(form.errors["slug"], ["Ya existe un evento con ese nombre corto. Prueba con otro."])

    def test_the_condition_help_points_at_the_fold(self):
        from events.forms import QuestionForm

        help_text = QuestionForm(version=self.version).fields["condition_codes"].help_text
        self.assertIn("“Identificador interno (para quien edita el cuestionario)”", help_text)
        self.assertNotIn("junto a cada opción", help_text)
