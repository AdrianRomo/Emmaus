"""Questionnaire structure, conditions, optional sensitive answers, versioning."""

from django.http import QueryDict

from events.models import ImmutableRecordError, Interview, Question, QuestionOption, QuestionnaireVersion
from events.services.event_questionnaires import use_version
from events.services.questionnaire import (
    AnswerValue,
    build_steps,
    duplicate_version,
    is_visible,
    load_answers,
    publish_version,
    validate_step,
    visible_steps,
)

from .base import CampFixture


def qd(**data):
    q = QueryDict(mutable=True)
    for key, value in data.items():
        if isinstance(value, list):
            q.setlist(key, value)
        else:
            q[key] = value
    return q


class StructureTests(CampFixture):
    def test_default_questionnaire_has_the_required_questions(self):
        texts = [q.text for step in self.steps for q in step.questions]
        for expected in [
            "¿Te consideras salvo?",
            "¿Tienes dudas acerca de tu fe?",
            "¿Qué dudas te gustaría compartir?",
            "¿Hay alguna situación en tu vida en la que te gustaría recibir apoyo?",
            "¿Tomaste alguna decisión espiritual durante esta conversación?",
            "¿Hay algo que te impida dar el siguiente paso en tu fe?",
            "Si decidiste recibir a Cristo, ¿recibiste orientación o discipulado?",
        ]:
            self.assertIn(expected, texts)

    def test_spiritual_questions_are_optional_and_offer_decline(self):
        for code in ("salvo", "dudas", "apoyo", "decision"):
            question = self.question(code)
            self.assertFalse(question.required, code)
            self.assertTrue(question.options.filter(is_decline=True).exists(), code)

    def test_intimate_questions_are_confidential(self):
        for code in ("apoyo", "apoyo_detalle", "dudas_detalle", "impedimento"):
            self.assertEqual(self.question(code).sensitivity, Question.Sensitivity.CONFIDENTIAL, code)

    def test_conditional_questions_are_revealed_inline_with_their_parent(self):
        step = self.step("dudas")
        self.assertEqual([q.code for q in step.children], ["dudas_detalle"])
        self.assertEqual([q.code for q in self.step("decision").children], ["orientacion"])


class ConditionalLogicTests(CampFixture):
    def test_child_visible_only_for_matching_answer(self):
        parent, child = self.question("dudas"), self.question("dudas_detalle")
        by_id = {parent.id: parent, child.id: child}
        self.assertFalse(is_visible(child, {}, by_id))
        self.assertFalse(is_visible(child, {parent.id: AnswerValue(codes=["no"])}, by_id))
        self.assertTrue(is_visible(child, {parent.id: AnswerValue(codes=["si"])}, by_id))

    def test_hidden_child_answer_is_discarded(self):
        step = self.step("dudas")
        child = self.question("dudas_detalle")
        cleaned, errors = validate_step(step, qd(**{f"q_{step.question.id}": "no", f"q_{child.id}": "texto que no debe guardarse"}), {})
        self.assertEqual(errors, {})
        self.assertIsNone(cleaned[child.id])

    def test_exclusive_option_cannot_be_combined(self):
        step = self.step("apoyo")
        cleaned, errors = validate_step(step, qd(**{f"q_{step.question.id}": ["familia", "prefiero_no"]}), {})
        self.assertIn("Prefiero no responder", errors[step.question.id])

    def test_unknown_option_is_rejected(self):
        step = self.step("salvo")
        _, errors = validate_step(step, qd(**{f"q_{step.question.id}": "inventada"}), {})
        self.assertIn(step.question.id, errors)

    def test_skipping_optional_question_is_allowed(self):
        step = self.step("salvo")
        cleaned, errors = validate_step(step, qd(), {})
        self.assertEqual(errors, {})
        self.assertIsNone(cleaned[step.question.id])

    def test_changing_parent_answer_prunes_saved_child(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        child = self.question("dudas_detalle")
        self.answer(interview, "dudas", "si", extra={f"q_{child.id}": "Tengo dudas sobre la oración"})
        self.assertIn(child.id, load_answers(interview))
        self.answer(interview, "dudas", "no")
        self.assertNotIn(child.id, load_answers(interview))

    def test_orientation_step_appears_only_after_deciding_to_receive_christ(self):
        decision = self.question("decision")
        orientation = self.question("orientacion")
        steps_without = visible_steps(self.version, {decision.id: AnswerValue(codes=["seguir_aprendiendo"])})
        self.assertNotIn(orientation.id, [q.id for s in steps_without for q in s.questions if is_visible(q, {decision.id: AnswerValue(codes=["seguir_aprendiendo"])}, {decision.id: decision, orientation.id: orientation})])

    def test_structured_multi_select_persistence(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        self.answer(interview, "apoyo", ["familia", "relaciones"])
        value = load_answers(interview)[self.question("apoyo").id]
        self.assertEqual(sorted(value.codes), ["familia", "relaciones"])


class VersioningTests(CampFixture):
    def test_published_version_is_immutable(self):
        question = self.question("salvo")
        question.text = "Otra pregunta"
        with self.assertRaises(ImmutableRecordError):
            question.save()
        with self.assertRaises(ImmutableRecordError):
            QuestionOption.objects.create(question=question, order=9, code="x", label="X")

    def test_new_version_does_not_change_old_interviews(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2)
        interview = Interview.objects.get(participant=self.p2)
        self.answer(interview, "salvo", "si")
        draft = duplicate_version(self.version, self.admin)
        new_salvo = Question.objects.get(section__version=draft, code="salvo")
        new_salvo.text = "¿Cómo describirías tu relación con Dios hoy?"
        new_salvo.save()
        publish_version(draft, self.admin)
        use_version(self.event, draft)
        interview.refresh_from_db()
        self.assertEqual(interview.version, self.version)
        self.assertEqual(interview.answers.get().question.text, "¿Te consideras salvo?")
        # The copy keeps conditions pointing inside the new version.
        child = Question.objects.get(section__version=draft, code="dudas_detalle")
        self.assertEqual(child.condition_question.section.version, draft)
        self.assertEqual(len(build_steps(draft)), len(build_steps(self.version)))

    def test_new_interviews_use_the_event_version(self):
        draft = duplicate_version(self.version, self.admin)
        publish_version(draft, self.admin)
        use_version(self.event, draft)
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p2)
        self.assertEqual(Interview.objects.get(participant=self.p2).version, draft)

    def test_admin_editor_requires_draft(self):
        self.login(self.admin)
        response = self.client.get(self.url("question_edit", self.version.id, self.question("salvo").id))
        self.assertEqual(response.status_code, 403)
        response = self.client.post(self.url("version_detail", self.version.id), {"action": "duplicate"})
        draft = QuestionnaireVersion.objects.exclude(pk=self.version.pk).get(questionnaire=self.version.questionnaire)
        self.assertRedirects(response, self.url("version_detail", draft.id), fetch_redirect_response=False)
        response = self.client.post(
            self.url("question_edit", draft.id, Question.objects.get(section__version=draft, code="salvo").id),
            {"text": "¿Cómo te sientes con Dios hoy?", "help_text": "", "kind": "unica", "sensitivity": "sensible", "order": 1, "condition_codes": ""},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Question.objects.filter(section__version=draft, text="¿Cómo te sientes con Dios hoy?").exists())
