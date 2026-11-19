"""«Retomar» after a skipped question (browser QA, owner decision): every question is optional, so a question left
blank on purpose stays skipped. An interview continues at the first visible screen after the furthest one with an
answer (a «Prefiero no responder» is one); with nothing after it, at the review, which lists the skipped one as «Sin
responder». The coordinator's card («Pregunta N de M») reads the same place: one function for both
(``questionnaire.resume_step``)."""

from __future__ import annotations

from django.test import Client

from events.models import Interview
from events.services.questionnaire import DECLINED_VALUE, AnswerValue, decline_option, resume_step, visible_steps
from people import flags

from .base import CampFixture


class ResumeStepTests(CampFixture):
    """The rule itself, on the camp questionnaire (five screens: salvo, dudas, apoyo, decision, impedimento)."""

    def number(self, answers):
        step = resume_step(self.version, answers)
        return step.number if step else None

    def test_after_the_furthest_answered_screen_never_back_to_a_skipped_one(self):
        salvo, dudas, apoyo, decision, impedimento = (self.question(code) for code in ("salvo", "dudas", "apoyo", "decision", "impedimento"))
        declined = AnswerValue(declined=True)
        self.assertEqual(self.number({}), self.step("salvo").number)  # nothing answered: the first screen
        self.assertEqual(self.number({salvo.id: declined}), self.step("dudas").number)  # a decline is an answer
        # salvo declined, dudas skipped, apoyo answered: the fourth screen, not the skipped second one
        self.assertEqual(self.number({salvo.id: declined, apoyo.id: AnswerValue(codes=["ninguna"])}), self.step("decision").number)
        self.assertEqual(self.number({dudas.id: AnswerValue(codes=["no"])}), self.step("apoyo").number)  # the first one skipped
        # The last screen answered: the review, whatever was skipped before it
        self.assertIsNone(self.number({salvo.id: declined, impedimento.id: AnswerValue(text="Nada")}))
        self.assertIsNone(self.number({decision.id: AnswerValue(codes=["ninguna"]), impedimento.id: AnswerValue(declined=True)}))

    def test_only_visible_screens_count(self):
        # orientacion shows under decision=recibir_cristo only (a child on decision's screen): the next screen is impedimento
        decision = self.question("decision")
        answers = {decision.id: AnswerValue(codes=["recibir_cristo"])}
        self.assertEqual(self.number(answers), self.step("impedimento").number)
        self.assertIn(self.step("impedimento").number, [s.number for s in visible_steps(self.version, answers)])


class ResumeAfterSkippingTests(CampFixture):
    """The QA scenario: Q1 «Prefiero no responder», Q2 skipped, Q3 answered, «Guardar y salir»."""

    def setUp(self):
        flags.set_enabled(self.church, "community", True)  # the card says «Pregunta N de M» in the redesign
        self.login(self.coord1)
        self.station = self.choose_station(number=4)
        self.start(self.p2)
        self.interview = Interview.objects.get(participant=self.p2)
        salvo = self.question("salvo")
        own = decline_option(salvo)
        self.answer(self.interview, "salvo", own.code if own else DECLINED_VALUE)
        self.answer(self.interview, "dudas", "")  # skipped: nothing chosen
        response = self.answer(self.interview, "apoyo", "ninguna", direction="exit")
        self.assertRedirects(response, self.url("coord_home"), fetch_redirect_response=False)
        self.assertEqual(self.interview.answers.count(), 2)

    def test_retomar_opens_the_screen_after_the_last_answer(self):
        resumed = self.client.get(self.url("interview_resume", self.interview.public_id))
        self.assertRedirects(resumed, self.url("interview_step", self.interview.public_id, self.step("decision").number), fetch_redirect_response=False)

    def test_the_card_says_the_same_place(self):
        page = self.client.get(self.url("coord_home")).content.decode().split("<main", 1)[1]
        total = len(visible_steps(self.version, {}))
        self.assertIn(f"Pregunta 4 de {total}, en {self.station.name}", page)
        self.assertNotIn(f"Pregunta 2 de {total}", page)

    def test_the_card_on_another_computer_says_it_too(self):
        other = self.login(self.coord1, Client())
        self.choose_station(other, number=5)
        page = other.get(self.url("coord_home")).content.decode().split("<main", 1)[1]
        self.assertIn(f"Pregunta 4 de {len(visible_steps(self.version, {}))}. Abierta en {self.station.name}", page)

    def test_with_the_last_screen_answered_retomar_opens_the_review_and_the_skipped_one_stays_blank(self):
        self.answer(self.interview, "impedimento", "Nada", direction="exit")
        resumed = self.client.get(self.url("interview_resume", self.interview.public_id))
        self.assertRedirects(resumed, self.url("interview_review", self.interview.public_id), fetch_redirect_response=False)
        review = self.client.get(self.url("interview_review", self.interview.public_id)).content.decode()
        dudas = review[review.index(self.question("dudas").text):]
        self.assertIn("Sin responder", dudas[: dudas.index("</div>")])
