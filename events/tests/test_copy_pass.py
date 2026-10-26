"""Behaviour that changed with the copy pass (wording alone is not tested here).

* Digits that ``str.isdigit`` accepts but ``int`` rejects ("²") no longer break a page.
* Every reason interviews cannot start is shown, each with what to do; a finished event is not "todavía no".
* A missing required answer names its question.
* The AI-draft messages never show setting names or the raw error.
* The camper is told, on the screen, who reads the answers.
* "Interrumpida" uses the one constant everywhere; durations and "hace …" are written in words.
* Events that still hold the old default privacy notice (it named only the "equipo pastoral") get the new one.
* Chart days read "25 oct", also for cutoffs saved with "25/10".
"""
from __future__ import annotations

import io
from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest import mock
from urllib import error as urlerror

from django.apps import apps as django_apps
from django.contrib.messages import get_messages
from django.db import migrations
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.services import generate_ai_draft
from events.forms import QuestionForm
from events.models import Event, EventParticipant, Interview, Question
from events.services import interviews as svc
from events.services import questionnaire
from events.services.metrics import event_scope, format_duration, scope_from_key
from events.templatetags.emmaus import chart_day, cumulative_chart, since

from .base import CampFixture

CAMPER_IS_TOLD = "El equipo del campamento podrá leer tus respuestas, menos las marcadas “Confidencial”: esas solo las lee el equipo de cuidado autorizado."

SUPERSCRIPT_TWO = "²"  # "²".isdigit() is True, and int("²") raises ValueError


class UnicodeDigitsTests(CampFixture):
    """A number field that only checked ``isdigit()`` answered "²" with a 500."""

    def test_interview_search_with_a_superscript_digit(self):
        self.login(self.coord1)
        self.choose_station()
        response = self.client.get(self.url("interview_search"), {"q": SUPERSCRIPT_TWO})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["results"], [])

    def test_my_camperos_search_with_a_superscript_digit(self):
        self.login(self.coord1)
        for query in (SUPERSCRIPT_TWO, "#" + SUPERSCRIPT_TWO):
            response = self.client.get(self.url("my_participants"), {"q": query})
            self.assertEqual(response.status_code, 200, query)

    def test_backup_list_with_a_superscript_coordinator(self):
        self.login(self.admin)
        response = self.client.get(self.url("backup_list"), {"coordinador": SUPERSCRIPT_TWO})
        self.assertEqual(response.status_code, 200)

    def test_interviews_filter_with_a_superscript_coordinator(self):
        self.login(self.pastor)
        response = self.client.get(self.url("interviews"), {"coordinador": SUPERSCRIPT_TWO})
        self.assertEqual(response.status_code, 200)

    def test_tutor_assignments_ignore_a_superscript_value(self):
        self.login(self.admin)
        field = f"tutor_{self.m_coord1.id}"
        response = self.client.post(self.url("assignments"), {field: SUPERSCRIPT_TWO})
        self.assertEqual(response.status_code, 302)  # read as "no tutor", like any other value that is not an id

    def test_import_confirmation_skips_a_superscript_line(self):
        upload = io.BytesIO("Nombre,Edad,Cuarto,Iglesia o grupo\nValeria Ruiz Ortega,16,8,Iglesia Centro\n".encode("utf-8"))
        upload.name = "camperos.csv"
        self.login(self.admin)
        self.client.post(self.url("participant_import"), {"file": upload})
        response = self.client.post(self.url("participant_import"), {"action": "confirm", "include": ["2", SUPERSCRIPT_TWO]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual([str(m) for m in get_messages(response.wsgi_request)], ["Se importó 1 campero."])
        self.assertTrue(EventParticipant.objects.filter(event=self.event, full_name="Valeria Ruiz Ortega").exists())

    def test_report_scope_key_with_a_superscript_id(self):
        self.assertIsNone(scope_from_key(self.event, "equipo:" + SUPERSCRIPT_TWO))
        self.assertEqual(scope_from_key(self.event, "evento").key, event_scope(self.event).key)


class BlockersSayWhatToDoTests(CampFixture):
    def test_the_search_lists_every_blocker_not_only_the_first(self):
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.DRAFT, questionnaire_version=None, procedures_approved_at=None)
        self.login(self.coord1)
        self.choose_station()
        response = self.client.get(self.url("interview_search"))
        for blocker in svc.event_blockers(Event.objects.get(pk=self.event.pk)):
            self.assertContains(response, blocker)
        self.assertEqual(len(svc.event_blockers(Event.objects.get(pk=self.event.pk))), 3)

    def test_a_draft_event_is_not_yet_in_progress(self):
        self.event.status = Event.Status.DRAFT
        self.assertEqual(svc.event_blockers(self.event), ["El evento todavía no está en curso. Avisa a la administración."])

    def test_a_finished_event_is_not_told_it_has_not_started(self):
        for status in (Event.Status.CLOSED, Event.Status.ARCHIVED):
            self.event.status = status
            (blocker,) = svc.event_blockers(self.event)
            self.assertIn("ya terminó", blocker, status)
            self.assertNotIn("todavía no", blocker, status)


class MissingRequiredAnswerTests(CampFixture):
    def test_the_error_names_the_question_that_is_missing(self):
        question = self.question("dudas")
        Question.objects.filter(pk=question.pk).update(required=True)  # a published version: only for this test
        # Published structures are cached in-process: rebuild it for this test, and put the cache back after.
        with mock.patch.dict(questionnaire._structure_cache, clear=True):
            self.login(self.coord1)
            self.choose_station()
            self.start(self.p2)
            interview = Interview.objects.get(participant=self.p2)
            response = self.client.post(self.url("interview_review", interview.public_id), {"revision": interview.revision, "follow_up_decision": "no_requerido"})
        self.assertEqual(response.status_code, 200, response.get("Location"))
        self.assertContains(response, f"Falta responder “{question.text}”. Toca “Cambiar” junto a ella.")
        interview.refresh_from_db()
        self.assertEqual(interview.status, Interview.Status.IN_PROGRESS)


class AiDraftMessagesTests(SimpleTestCase):
    question = SimpleNamespace(question_text="¿Cómo oro?", get_sensitivity_level_display=lambda: "General")

    @override_settings(LLM_ENABLED=False)
    def test_off_says_who_can_turn_it_on_without_setting_names(self):
        draft, message = generate_ai_draft(self.question, None)
        self.assertIsNone(draft)
        self.assertNotIn("LLM_", message)
        self.assertIn("quien administra el servidor", message)

    @override_settings(LLM_ENABLED=True, LLM_BASE_URL="", LLM_API_KEY="", LLM_MODEL="")
    def test_half_configured_says_who_finishes_it_without_setting_names(self):
        draft, message = generate_ai_draft(self.question, None)
        self.assertIsNone(draft)
        self.assertNotIn("LLM_", message)
        self.assertIn("quien administra el servidor", message)

    @override_settings(LLM_ENABLED=True, LLM_BASE_URL="http://llm.invalid/v1", LLM_API_KEY="k", LLM_MODEL="m")
    def test_a_failure_goes_to_the_log_not_to_the_screen(self):
        with mock.patch("core.services.request.urlopen", side_effect=urlerror.URLError("connection refused by llm.invalid")):
            with self.assertLogs("core.services", level="ERROR") as logs:
                draft, message = generate_ai_draft(self.question, None)
        self.assertIsNone(draft)
        self.assertEqual(message, "No se pudo generar el borrador. Inténtalo otra vez.")
        self.assertIn("connection refused by llm.invalid", "\n".join(logs.output))


class CamperIsToldWhoReadsTests(CampFixture):
    def test_welcome_and_review_say_who_reads_the_answers(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2, mode="supervisada")
        interview = Interview.objects.get(participant=self.p2)
        self.client.post(self.url("interview_handoff", interview.public_id))
        self.assertContains(self.client.get(reverse("kiosk:home")), CAMPER_IS_TOLD)
        self.assertContains(self.client.get(reverse("kiosk:review")), CAMPER_IS_TOLD)


class StaleMinutesComeFromTheConstantTests(CampFixture):
    def test_the_pending_page_prints_the_constant(self):
        self.login(self.tutor1)
        with mock.patch("events.services.metrics.STALE_MINUTES", 35):
            response = self.client.get(self.url("pending"))
        self.assertContains(response, "Llevan 35 minutos o más sin cambios.")

    def test_the_tutor_sign_and_the_coordinator_rows_print_the_constant(self):
        """A literal 20 in tutor/_home_live.html or rotulo/_coordinator_rows.html would not say 7."""
        self.login(self.coord1)
        self.choose_station(number=4)
        self.start(self.p1)
        Interview.objects.filter(participant=self.p1).update(last_activity_at=timezone.now() - timedelta(minutes=45))
        self.login(self.tutor1)
        with mock.patch("events.services.metrics.STALE_MINUTES", 7):
            response = self.client.get(self.url("tutor_home"))
        self.assertContains(response, "Lleva 7 minutos o más sin cambios en Computadora 4.")  # the sign
        self.assertContains(response, "1 entrevista interrumpida: 7 minutos o más sin cambios")  # Carla's row
        self.assertNotContains(response, "20 minutos")


class ConditionCodesErrorTests(CampFixture):
    def test_unknown_codes_list_the_ones_that_exist(self):
        parent = self.question("dudas")
        form = QuestionForm(
            {"text": "¿Cuáles?", "kind": Question.Kind.LONG_TEXT, "sensitivity": Question.Sensitivity.SENSITIVE,
             "order": 9, "condition_question": parent.pk, "condition_codes": "quizas"},
            version=self.version,
        )
        self.assertFalse(form.is_valid())
        (message,) = form.errors["condition_codes"]
        self.assertIn("quizas", message)
        for code in parent.options.values_list("code", flat=True):
            self.assertIn(code, message)


class WordsNotAbbreviationsTests(SimpleTestCase):
    def test_durations(self):
        self.assertEqual(format_duration(80), "1 hora 20 minutos")
        self.assertEqual(format_duration(61), "1 hora 1 minuto")
        self.assertEqual(format_duration(120), "2 horas")
        self.assertEqual(format_duration(1), "1 minuto")
        self.assertEqual(format_duration(45), "45 minutos")
        self.assertEqual(format_duration(None), "")

    def test_since(self):
        now = timezone.now()
        self.assertEqual(since(now - timezone.timedelta(minutes=1, seconds=5)), "hace 1 minuto")
        self.assertEqual(since(now - timezone.timedelta(minutes=5, seconds=5)), "hace 5 minutos")
        self.assertEqual(since(now - timezone.timedelta(hours=1, minutes=1)), "hace 1 hora")
        self.assertEqual(since(now - timezone.timedelta(hours=3, minutes=1)), "hace 3 horas")
        old = timezone.localtime(now - timezone.timedelta(days=3))
        self.assertRegex(since(old), r"^\d{1,2} [a-z]{3}, \d{2}:\d{2}$")


class OldDefaultPrivacyNoticeMigrationTests(CampFixture):
    """events/0009 swaps the untouched old default notice for the new one, and leaves edited notices alone."""

    migration = import_module("events.migrations.0009_copy_pass_labels")

    def test_an_event_with_the_old_default_gets_the_new_one(self):
        Event.objects.filter(pk=self.event.pk).update(privacy_notice=self.migration.OLD_DEFAULT_PRIVACY_NOTICE)
        edited = self.migration.OLD_DEFAULT_PRIVACY_NOTICE + " Pregunta a Rebeca si tienes dudas."
        Event.objects.filter(pk=self.other_event.pk).update(privacy_notice=edited)
        self.migration.replace_old_default_notice(django_apps, None)
        self.assertEqual(Event.objects.get(pk=self.event.pk).privacy_notice, self.migration.NEW_DEFAULT_PRIVACY_NOTICE)
        self.assertEqual(Event.objects.get(pk=self.other_event.pk).privacy_notice, edited)

    def test_the_frozen_texts_are_the_old_and_the_current_defaults(self):
        from events.models import DEFAULT_PRIVACY_NOTICE

        self.assertEqual(self.migration.NEW_DEFAULT_PRIVACY_NOTICE, DEFAULT_PRIVACY_NOTICE)
        self.assertIn("equipo pastoral", self.migration.OLD_DEFAULT_PRIVACY_NOTICE)
        self.assertIn("terceros", self.migration.OLD_DEFAULT_PRIVACY_NOTICE)

    def test_the_step_runs_forward_only(self):
        (step,) = [op for op in self.migration.Migration.operations if isinstance(op, migrations.RunPython)]
        self.assertIs(step.code, self.migration.replace_old_default_notice)
        self.assertIs(step.reverse_code, migrations.RunPython.noop)


class ChartDayTests(SimpleTestCase):
    def test_new_points_old_points_and_the_presentation(self):
        t = "2026-10-25T18:00:00-06:00"
        self.assertEqual(chart_day({"t": t, "day": "25 oct", "label": "18:00"}), "25 oct")
        self.assertEqual(chart_day({"t": t, "day": "25/10", "label": "18:00"}), "25 oct")  # a cutoff saved before
        self.assertEqual(chart_day({"day": "26/10", "label": "00:00"}), "26 oct")  # presentation timeline: no "t"
        self.assertEqual(chart_day({"t": t, "day": "", "label": "18:00"}), "")  # a timeline within one day

    def test_the_chart_axis_of_a_saved_cutoff_reads_the_new_way(self):
        points = [
            {"t": "2026-10-25T23:00:00-06:00", "label": "23:00", "day": "25/10", "count": 1, "cumulative": 1},
            {"t": "2026-10-26T00:00:00-06:00", "label": "00:00", "day": "26/10", "count": 2, "cumulative": 3},
        ]
        svg = str(cumulative_chart(points, 10, "c", "Entrevistas"))
        self.assertIn(">25 oct 23:00<", svg)
        self.assertNotIn("25/10", svg)
