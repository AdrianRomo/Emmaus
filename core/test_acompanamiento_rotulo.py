"""G5b: the discipleship screens used day to day (participant, mentor, questions, follow-ups) on the Rótulo system."""

import re
from datetime import timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from .models import AIDraft, FollowUpRequest, ParticipantQuestion, QuestionResponse
from .tests import EmmausFixture

# Classes and partials of the old "Cuaderno de campo" system that must not survive the migration
OLD_MARKUP = re.compile(
    r'class="[^"]*\b(ledger\w*|figure-row|chip|chip--\w+|meter\w*|folio|page-head__row|section__head|subhead'
    r"|stat-card\w*|card|empty__\w+|btn--quiet|btn--sm|btn--hero|split--even|crumbs)\b"
)


def main_of(response):
    return re.search(r"<main\b.*?</main>", response.content.decode(), re.S).group(0)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AcompanamientoRotuloTests(EmmausFixture):
    def setUp(self):
        super().setUp()
        self.question = ParticipantQuestion.objects.create(
            church=self.church, participant=self.participant, question_text="¿Cómo empiezo a orar?"
        )
        self.follow_up = FollowUpRequest.objects.create(
            church=self.church, participant=self.participant, created_by=self.participant,
            assigned_to=self.mentor, reason="Quiero platicar en persona.",
        )

    def screens(self):
        return [
            (self.participant, reverse("participant_dashboard")),
            (self.participant, reverse("participant_lesson", args=[self.lesson.id])),
            (self.participant, reverse("participant_questions")),
            (self.participant, reverse("participant_question_detail", args=[self.question.id])),
            (self.participant, reverse("ask_question")),
            (self.participant, reverse("request_follow_up")),
            (self.mentor, reverse("mentor_dashboard")),
            (self.mentor, reverse("question_detail", args=[self.question.id])),
            (self.mentor, reverse("follow_up_list")),
            (self.pastor, reverse("questions_inbox")),
            (self.pastor, reverse("escalations")),
            (self.pastor, reverse("church_question_detail", args=[self.question.id])),
            (self.admin, reverse("church_follow_up_list")),
        ]

    def test_every_screen_is_on_rotulo_with_one_h1_and_no_old_classes(self):
        for user, url in self.screens():
            with self.subTest(url=url):
                self.client.force_login(user)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "css/rotulo.css")
                main = main_of(response)
                self.assertIsNone(OLD_MARKUP.search(main), OLD_MARKUP.search(main) and OLD_MARKUP.search(main).group(0))
                self.assertEqual(len(re.findall(r"<h1\b", main)), 1)

    def test_sensitivity_is_offered_as_choices_that_post_the_same_values(self):
        self.client.force_login(self.participant)
        html = self.client.get(reverse("ask_question")).content.decode()
        radios = re.findall(r'<input class="choice__input" type="radio" id="([^"]+)" name="sensitivity_level" value="([^"]+)"( checked)?>', html)
        self.assertEqual(
            [(radio_id, value) for radio_id, value, _ in radios],
            [("id_sensitivity_level", "normal"), ("id_sensitivity_level_1", "sensitive"), ("id_sensitivity_level_2", "pastoral_care_required")],
        )
        self.assertEqual([checked for _, _, checked in radios], [" checked", "", ""])

        response = self.client.post(reverse("ask_question"), {"question_text": "¿Puedo platicar con alguien?", "sensitivity_level": "sensitive"})

        self.assertRedirects(response, reverse("participant_questions"))
        self.assertEqual(ParticipantQuestion.objects.get(question_text="¿Puedo platicar con alguien?").sensitivity_level, "sensitive")

    def test_a_rejected_question_keeps_the_choice_and_links_the_error_to_the_field(self):
        self.client.force_login(self.participant)

        response = self.client.post(reverse("ask_question"), {"question_text": "", "sensitivity_level": "pastoral_care_required"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<title>Error: Haz una pregunta")
        self.assertContains(response, 'href="#id_question_text"')
        self.assertContains(response, 'id="id_question_text"')
        self.assertContains(response, 'id="id_sensitivity_level_2" name="sensitivity_level" value="pastoral_care_required" checked>')

    def test_the_mentor_sign_points_to_the_oldest_open_question(self):
        now = timezone.now()
        oldest_open = ParticipantQuestion.objects.create(church=self.church, participant=self.participant, question_text="Primera")
        escalated = ParticipantQuestion.objects.create(
            church=self.church, participant=self.participant, question_text="Escalada", status=ParticipantQuestion.Status.ESCALATED
        )
        ParticipantQuestion.objects.filter(pk=oldest_open.pk).update(created_at=now - timedelta(days=3))
        ParticipantQuestion.objects.filter(pk=escalated.pk).update(created_at=now - timedelta(days=9))  # already with a pastor
        self.client.force_login(self.mentor)

        sign = re.search(r'<section class="sign".*?</section>', main_of(self.client.get(reverse("mentor_dashboard"))), re.S)

        self.assertIsNotNone(sign)
        self.assertIn(reverse("question_detail", args=[oldest_open.id]), sign.group(0))
        self.assertIn("Pat Participant espera tu respuesta", sign.group(0))

    def test_no_sign_when_every_question_is_already_with_a_pastor(self):
        ParticipantQuestion.objects.all().delete()
        ParticipantQuestion.objects.create(
            church=self.church, participant=self.participant, question_text="Escalada", status=ParticipantQuestion.Status.ESCALATED
        )
        self.client.force_login(self.mentor)

        main = main_of(self.client.get(reverse("mentor_dashboard")))

        self.assertNotIn('class="sign"', main)
        self.assertIn("Ninguna pregunta espera respuesta", main)
        self.assertIn("Escalada", main)  # still listed with the open questions

    def test_no_sign_for_the_mentor_when_nobody_waits_for_a_reply(self):
        ParticipantQuestion.objects.all().update(status=ParticipantQuestion.Status.ANSWERED)
        self.client.force_login(self.mentor)

        main = main_of(self.client.get(reverse("mentor_dashboard")))

        self.assertNotIn('class="sign"', main)
        self.assertIn("Ninguna pregunta espera respuesta", main)

    def test_escalation_is_care_not_an_error(self):
        ParticipantQuestion.objects.filter(pk=self.question.pk).update(status=ParticipantQuestion.Status.ESCALATED)
        self.client.force_login(self.pastor)

        for url in (reverse("escalations"), reverse("church_question_detail", args=[self.question.id])):
            with self.subTest(url=url):
                main = main_of(self.client.get(url))
                self.assertIn("status--progress", main)
                self.assertIn("Escalada", main)
                self.assertNotIn("status--risk", main)
                self.assertNotIn("btn--danger", main)

    def test_the_participant_sign_is_the_next_lesson_until_the_path_is_done(self):
        self.client.force_login(self.participant)
        lesson_url = reverse("participant_lesson", args=[self.lesson.id])

        sign = re.search(r'<section class="sign".*?</section>', main_of(self.client.get(reverse("participant_dashboard"))), re.S)
        self.assertIn(lesson_url, sign.group(0))

        self.enrollment.lesson_progress.update(completed_at=timezone.now())
        main = main_of(self.client.get(reverse("participant_dashboard")))
        self.assertNotIn('class="sign"', main)
        self.assertIn("Terminaste la lección.", main)
        self.assertIn(lesson_url, main)  # the lesson can still be read again

    def test_the_lesson_keeps_its_reflection_fields_and_both_buttons(self):
        self.client.force_login(self.participant)

        main = main_of(self.client.get(reverse("participant_lesson", args=[self.lesson.id])))

        field = f"question_{self.reflection_question.id}"
        self.assertIn(f'<label class="voice disc-reflect__label" for="{field}">', main)
        self.assertIn(f'id="{field}" name="{field}"', main)
        self.assertIn('name="mark_complete" value="1"', main)
        self.assertIn(">Guardar reflexión</button>", main)

    def test_only_open_follow_ups_offer_to_be_marked_completed(self):
        done = FollowUpRequest.objects.create(
            church=self.church, participant=self.participant, created_by=self.mentor, assigned_to=self.mentor,
            reason="Ya platicamos.", status=FollowUpRequest.Status.COMPLETED,
        )
        self.client.force_login(self.mentor)

        main = main_of(self.client.get(reverse("follow_up_list")))

        self.assertIn(f'action="{reverse("complete_follow_up", args=[self.follow_up.id])}"', main)
        self.assertNotIn(f'action="{reverse("complete_follow_up", args=[done.id])}"', main)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AIDraftReviewTests(EmmausFixture):
    """Several AI drafts on one question: one form each, its own ids, its own errors."""

    def setUp(self):
        super().setUp()
        self.question = ParticipantQuestion.objects.create(church=self.church, participant=self.participant, question_text="¿Cómo perdono?")
        self.first = AIDraft.objects.create(church=self.church, related_question=self.question, generated_by=self.pastor, generated_text="Primer borrador")
        self.second = AIDraft.objects.create(church=self.church, related_question=self.question, generated_by=self.pastor, generated_text="Segundo borrador")
        self.url = reverse("church_question_detail", args=[self.question.id])
        self.client.force_login(self.pastor)

    def test_several_drafts_never_share_element_ids(self):
        html = self.client.get(self.url).content.decode()
        ids = re.findall(r'\bid="([^"]+)"', html)
        self.assertEqual([i for i in set(ids) if ids.count(i) > 1], [])
        for draft in (self.first, self.second):
            self.assertIn(f'name="borrador-{draft.pk}-review_action"', html)
            self.assertIn(f'id="id_borrador-{draft.pk}-edited_final_text"', html)

    def test_reviewing_one_draft_sends_it_and_leaves_the_other(self):
        response = self.client.post(self.url, {
            "action": "review_ai", "draft_id": self.first.pk,
            f"borrador-{self.first.pk}-review_action": "edit", f"borrador-{self.first.pk}-edited_final_text": "Texto revisado por el pastor",
        })
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertTrue(QuestionResponse.objects.filter(question=self.question, response_body="Texto revisado por el pastor").exists())
        self.first.refresh_from_db(); self.second.refresh_from_db()
        self.assertEqual((self.first.status, self.second.status), (AIDraft.Status.EDITED, AIDraft.Status.DRAFT))

    def test_a_review_without_a_choice_shows_the_error_on_that_draft(self):
        response = self.client.post(self.url, {
            "action": "review_ai", "draft_id": self.second.pk, f"borrador-{self.second.pk}-edited_final_text": "Lo que ya escribí",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<title>Error: ", html=False)
        self.assertContains(response, "Hay un problema")
        self.assertContains(response, f'href="#id_borrador-{self.second.pk}-review_action"')
        self.assertContains(response, "Lo que ya escribí")  # what the person wrote is still there
        self.assertFalse(QuestionResponse.objects.filter(question=self.question).exists())

    def test_nothing_is_sent_unless_someone_chooses_to(self):
        """The choice starts empty: pressing "Confirmar revisión" untouched must not send the draft."""
        html = self.client.get(self.url).content.decode()
        select = re.search(rf'<select name="borrador-{self.first.pk}-review_action".*?</select>', html, re.S).group(0)
        self.assertRegex(select, r'<option value="" selected>Elige una opción</option>')
        response = self.client.post(self.url, {
            "action": "review_ai", "draft_id": self.first.pk, f"borrador-{self.first.pk}-review_action": "",
            f"borrador-{self.first.pk}-edited_final_text": "Primer borrador",
        })
        self.assertContains(response, "¿Qué quieres hacer con este borrador? Elige qué hacer con este borrador")  # no "?:"
        self.assertFalse(QuestionResponse.objects.filter(question=self.question).exists())
