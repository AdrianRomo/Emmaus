"""Camp flow fixes in the discipleship module: AI drafts are reviewed once, participants read their
church's notice, and every error page speaks Spanish on Rótulo.

This module is also the URLconf of the error-page tests (override_settings(ROOT_URLCONF=__name__)):
the project's routes plus two that fail on purpose, served with the project's error handlers.
"""

import importlib
import re
from unittest.mock import patch

from django.apps import apps as django_apps
from django.contrib.messages import get_messages
from django.core import mail
from django.core.exceptions import SuspiciousOperation
from django.test import Client, override_settings
from django.urls import path, reverse

import emmaus.urls as project_urls

from .forms import AIDraftReviewForm
from .models import (
    AIDraft,
    Church,
    ParticipantProfile,
    ParticipantQuestion,
    QuestionResponse,
)
from .tests import EmmausFixture
from .views import EMERGENCY_NOTICE, LEGACY_ENGLISH_NOTICE, QUESTION_DISCLAIMER_INTRO


class ProvokedFailure(Exception):
    """Any unhandled error in a view."""


def _fails(request):
    raise ProvokedFailure("falla de prueba")


def _suspicious(request):
    raise SuspiciousOperation("petición sospechosa de prueba")


urlpatterns = [path("prueba/falla/", _fails), path("prueba/sospechosa/", _suspicious), *project_urls.urlpatterns]
# The project's handlers; a missing one falls back to Django's default page, which the tests reject.
handler400 = getattr(project_urls, "handler400", None)
handler403 = getattr(project_urls, "handler403", None)
handler404 = getattr(project_urls, "handler404", None)
handler500 = getattr(project_urls, "handler500", None)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AIDraftReviewedOnceTests(EmmausFixture):
    """A3: a reviewed draft cannot be sent again, and its label says whether its text changed."""

    def setUp(self):
        super().setUp()
        self.question = ParticipantQuestion.objects.create(church=self.church, participant=self.participant, question_text="¿Cómo perdono?")
        self.draft = AIDraft.objects.create(church=self.church, related_question=self.question, generated_by=self.pastor, generated_text="Borrador generado")
        self.url = reverse("church_question_detail", args=[self.question.id])
        self.client.force_login(self.pastor)

    def review(self, action, text="Borrador generado"):
        prefix = f"borrador-{self.draft.pk}"
        return self.client.post(self.url, {
            "action": "review_ai", "draft_id": self.draft.pk, f"{prefix}-review_action": action, f"{prefix}-edited_final_text": text,
        })

    def messages(self, response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    def test_approving_twice_sends_one_answer(self):
        self.review("approve")
        second = self.review("approve")
        self.assertRedirects(second, self.url, fetch_redirect_response=False)
        self.assertIn("Este borrador ya se revisó.", self.messages(second))
        self.assertEqual(QuestionResponse.objects.filter(question=self.question).count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, AIDraft.Status.APPROVED)  # unchanged text: "Aprobado"

    def test_a_rejected_draft_cannot_be_approved_afterwards(self):
        self.review("reject")
        response = self.review("approve")
        self.assertIn("Este borrador ya se revisó.", self.messages(response))
        self.assertFalse(QuestionResponse.objects.filter(question=self.question).exists())
        self.assertEqual(len(mail.outbox), 0)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, AIDraft.Status.REJECTED)

    def test_approving_changed_text_is_recorded_as_edited(self):
        self.review("approve", text="Texto que cambió el pastor")
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, AIDraft.Status.EDITED)
        self.assertEqual(self.draft.edited_final_text, "Texto que cambió el pastor")
        response = QuestionResponse.objects.get(question=self.question)
        self.assertEqual(response.response_body, "Texto que cambió el pastor")
        self.assertEqual(len(mail.outbox), 1)

    def test_an_untouched_multiline_draft_sent_from_a_browser_is_approved(self):
        """Browsers send textarea line breaks as CRLF: an untouched draft must not read as edited."""
        generated = "Hola, gracias por escribir.\n\nPerdonar es un proceso.\nOremos juntos."
        AIDraft.objects.filter(pk=self.draft.pk).update(generated_text=generated)
        self.review("approve", text=generated.replace("\n", "\r\n"))
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, AIDraft.Status.APPROVED)
        body = QuestionResponse.objects.get(question=self.question).response_body
        self.assertNotIn("\r", body)
        self.assertEqual(body, generated)
        self.assertNotIn("\r", self.draft.edited_final_text)

    def test_save_changes_without_changes_is_approved(self):
        self.review("edit")
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, AIDraft.Status.APPROVED)
        self.assertEqual(QuestionResponse.objects.get(question=self.question).response_body, "Borrador generado")

    def test_a_reviewed_draft_shows_its_status_instead_of_the_form(self):
        self.review("approve")
        html = self.client.get(self.url).content.decode()
        self.assertNotIn(f'name="borrador-{self.draft.pk}-review_action"', html)
        self.assertNotIn(f'name="draft_id" value="{self.draft.pk}"', html)
        self.assertIn("Este borrador ya se revisó el ", html)
        self.assertIn("Aprobado", html)

    def test_a_review_that_loses_the_race_sends_nothing(self):
        """Two leaders confirm at the same time: the second finds the draft already reviewed."""
        original = AIDraftReviewForm.is_valid

        def reviewed_meanwhile(form):
            AIDraft.objects.filter(pk=form.instance.pk).update(status=AIDraft.Status.APPROVED)
            return original(form)

        with patch.object(AIDraftReviewForm, "is_valid", reviewed_meanwhile):
            response = self.review("approve")
        self.assertIn("Este borrador ya se revisó.", self.messages(response))
        self.assertFalse(QuestionResponse.objects.filter(question=self.question).exists())
        self.assertEqual(len(mail.outbox), 0)


class ParticipantNoticeTests(EmmausFixture):
    """A4: participants read their church's notice (church settings), and always the emergency sentence."""

    def setUp(self):
        super().setUp()
        self.question = ParticipantQuestion.objects.create(church=self.church, participant=self.participant, question_text="¿Por dónde empiezo?")

    def set_notice(self, text):
        Church.objects.filter(pk=self.church.pk).update(basic_disclaimer_text=text)

    def pages(self):
        self.client.force_login(self.participant)
        yield self.client.get(reverse("ask_question"))
        yield self.client.get(reverse("participant_question_detail", args=[self.question.id]))
        self.client.force_login(self.pastor)
        yield self.client.get(reverse("church_question_detail", args=[self.question.id]))

    def test_a_custom_notice_is_what_participants_read(self):
        self.set_notice("Esta iglesia responde en un plazo de tres días. Para una emergencia no esperes la respuesta.")
        for response in self.pages():
            self.assertContains(response, "Esta iglesia responde en un plazo de tres días.")
            self.assertContains(response, EMERGENCY_NOTICE)
            self.assertNotContains(response, QUESTION_DISCLAIMER_INTRO)

    def test_a_blank_notice_shows_the_emmaus_notice(self):
        self.set_notice("   ")
        for response in self.pages():
            self.assertContains(response, QUESTION_DISCLAIMER_INTRO)
            self.assertContains(response, EMERGENCY_NOTICE)

    def test_the_default_notice_keeps_the_911_sentence(self):
        self.set_notice(Church._meta.get_field("basic_disclaimer_text").default)
        for response in self.pages():
            self.assertContains(response, Church._meta.get_field("basic_disclaimer_text").default)
            self.assertContains(response, "llama de inmediato al 911")

    def test_the_legacy_english_notice_reads_as_no_notice(self):
        """Churches created under migration 0001 kept its English default."""
        self.set_notice(LEGACY_ENGLISH_NOTICE)
        for response in self.pages():
            self.assertNotContains(response, "Emmaus supports discipleship")
            self.assertContains(response, QUESTION_DISCLAIMER_INTRO)
            self.assertContains(response, EMERGENCY_NOTICE)

    def test_migration_0006_clears_only_the_legacy_english_notice(self):
        migration = importlib.import_module("core.migrations.0006_clear_legacy_english_notice")
        self.assertEqual(migration.LEGACY_ENGLISH_NOTICE, LEGACY_ENGLISH_NOTICE)
        own = Church.objects.create(name="Iglesia propia", slug="propia", basic_disclaimer_text="Nuestro propio aviso.")
        self.set_notice(LEGACY_ENGLISH_NOTICE)
        migration.forwards(django_apps, None)
        self.assertEqual(Church.objects.get(pk=self.church.pk).basic_disclaimer_text, "")
        self.assertEqual(Church.objects.get(pk=own.pk).basic_disclaimer_text, "Nuestro propio aviso.")

    def test_a_question_shows_the_notice_of_the_church_it_was_asked_in(self):
        """The participant later moved to another church: the question keeps its own church's notice."""
        self.set_notice("AVISO DE LA IGLESIA A")
        other = Church.objects.create(name="Otra", slug="otra", basic_disclaimer_text="AVISO DE LA IGLESIA B")
        ParticipantProfile.objects.filter(pk=self.profile.pk).update(church=other)
        self.client.force_login(self.pastor)
        response = self.client.get(reverse("church_question_detail", args=[self.question.id]))
        self.assertContains(response, "AVISO DE LA IGLESIA A")
        self.assertNotContains(response, "AVISO DE LA IGLESIA B")

    def test_setup_shows_what_participants_will_read(self):
        Church.objects.all().delete()
        response = self.client.get(reverse("initial_setup"))
        self.assertContains(response, Church._meta.get_field("basic_disclaimer_text").default)
        self.assertContains(response, EMERGENCY_NOTICE)


def one_h1(response):
    return len(re.findall(r"<h1\b", response.content.decode())) == 1


@override_settings(ROOT_URLCONF=__name__)
class ErrorPageTests(EmmausFixture):
    """A6: 500, 400 and an expired form get a Spanish page on Rótulo, never Django's own."""

    def test_server_error(self):
        self.client.force_login(self.participant)
        self.client.raise_request_exception = False
        with self.assertLogs("django.request", "ERROR"):  # the failure is still logged
            response = self.client.get("/prueba/falla/")
        self.assertEqual(response.status_code, 500)
        self.assertContains(response, "Emmaus no pudo abrir esta página", status_code=500)
        self.assertContains(response, "Lo que ya guardaste está a salvo. Recarga la página; si sigue igual, avisa a la administración.", status_code=500)
        self.assertContains(response, "css/rotulo.css", status_code=500)
        self.assertContains(response, "<title>Emmaus no pudo abrir esta página · Emmaus</title>", status_code=500)
        self.assertNotContains(response, "Server Error", status_code=500)
        self.assertTrue(one_h1(response))

    def test_bad_request(self):
        self.client.force_login(self.participant)
        with self.assertLogs("django.security", "ERROR"):
            response = self.client.get("/prueba/sospechosa/")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "La dirección no es válida", status_code=400)
        self.assertContains(response, "Revisa la dirección o vuelve a tu inicio.", status_code=400)
        self.assertContains(response, "Volver a mi inicio", status_code=400)
        self.assertNotContains(response, "Bad Request", status_code=400)
        self.assertNotContains(response, "Server Error", status_code=400)
        self.assertTrue(one_h1(response))

    def test_a_host_outside_allowed_hosts_gets_the_same_page(self):
        """Refused before authentication: the page draws without the signed-in user, but in Spanish."""
        with self.assertLogs("django.security.DisallowedHost", "ERROR"):
            response = self.client.get("/", HTTP_HOST="sitio-ajeno.example")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "La dirección no es válida", status_code=400)
        self.assertNotContains(response, "Bad Request", status_code=400)

    def test_a_form_sent_without_its_token(self):
        for signed_in in (False, True):
            client = Client(enforce_csrf_checks=True)
            if signed_in:
                client.force_login(self.participant)
            with self.assertLogs("django.security.csrf", "WARNING"):
                response = client.post(reverse("login"), {"username": "x", "password": "y"})
            self.assertEqual(response.status_code, 403)
            self.assertContains(response, "Esta pantalla ya no está al día", status_code=403)
            self.assertContains(response, "ya no está al día", status_code=403)
            self.assertContains(response, "Regresa, recárgala y repite el paso.", status_code=403)
            self.assertNotContains(response, "CSRF", status_code=403)
            self.assertTrue(one_h1(response))

    def test_forbidden_and_not_found_without_an_eyebrow(self):
        self.client.force_login(self.participant)
        forbidden = self.client.get(reverse("church_dashboard"))
        self.assertContains(forbidden, "pide a la administración del evento que te dé acceso.", status_code=403)
        self.assertNotContains(forbidden, "revise tu rol", status_code=403)
        missing = self.client.get("/no-existe/")
        for response, code in ((forbidden, 403), (missing, 404)):
            self.assertContains(response, "Volver a mi inicio", status_code=code)
            self.assertNotContains(response, "Ir a mi inicio", status_code=code)
            self.assertNotContains(response, f"Error {code}", status_code=code)
