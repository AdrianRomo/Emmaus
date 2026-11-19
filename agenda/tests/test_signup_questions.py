"""Issue #93 (wave 12), on synthetic data: what an event asks at sign-up. Up to five questions of three kinds,
answers read by the event's administration only, no free text where the event protects minors, and answers that
end with the registration. Each test names the defect it would catch."""

from django.urls import reverse

from agenda import questions, services, visitors
from agenda.models import EventQuestion, EventRegistration, RegistrationAnswer
from agenda.tests.test_event_extras import ExtrasFixture
from agenda.tests.test_visitors import ADDRESS, NAME, VisitorFixture, form_url
from people import flags
from people.models import ChurchAuditEvent


class QuestionsFixture(ExtrasFixture):
    def add(self, text="¿Te quedas a comer?", kind="si_no", **fields) -> EventQuestion:
        return questions.save_question(self.retiro, self.admin, text=text, kind=kind, **fields)

    def field(self, question) -> str:
        return f"{questions.PREFIX}{question.pk}"


class BuilderTests(QuestionsFixture):
    def setUp(self):
        self.client.force_login(self.admin)

    def test_the_administration_adds_edits_and_removes_questions(self):
        url = self.url("questions")
        empty = self.client.get(url)
        self.assertContains(empty, "<h1", count=1)
        self.assertContains(empty, "No preguntes por salud, alergias, situaciones familiares ni nada sobre menores de edad")
        self.assertContains(empty, "btn--primary", count=1)
        done = self.client.post(url, {"text": "  ¿Qué   talla de playera? ", "kind": "opcion", "options": "Chica\n\nMediana\nchica\nGrande"}, follow=True)
        self.assertContains(done, "Agregaste la pregunta.")
        item = EventQuestion.objects.get()
        self.assertEqual((item.text, item.kind, item.option_list), ("¿Qué talla de playera?", "opcion", ["Chica", "Mediana", "Grande"]))
        edit = self.url("question_edit", item.pk)
        self.assertNotContains(self.client.get(edit), 'name="kind"')  # the kind is chosen once
        self.client.post(edit, {"text": "¿Qué talla?", "options": "S\nM", "required": "on", "kind": "texto"})
        item.refresh_from_db()
        self.assertEqual((item.text, item.kind, item.option_list, item.required), ("¿Qué talla?", "opcion", ["S", "M"], True))
        audit = ChurchAuditEvent.objects.filter(action="evento_preguntas_inscripcion")
        self.assertEqual(audit.count(), 2)
        for row in audit:  # which question of which event, never its words
            self.assertNotIn("talla", row.summary)
        registration = services.register(self.retiro, self.member, self.member_user)
        questions.save_answers(registration, {str(item.pk): "M"})
        asking = self.client.get(self.url("question_delete", item.pk))
        self.assertContains(asking, "Se borra también la respuesta que ya dio 1 persona.")
        self.client.post(self.url("question_delete", item.pk))
        self.assertFalse(EventQuestion.objects.exists())
        self.assertFalse(RegistrationAnswer.objects.exists())

    def test_at_most_five_and_single_line_and_sane_options(self):
        for number in range(questions.MAX_QUESTIONS):
            self.add(f"Pregunta {number}")
        page = self.client.post(self.url("questions"), {"text": "La sexta", "kind": "si_no"})
        self.assertContains(page, "ya tiene 5 preguntas")
        self.assertEqual(EventQuestion.objects.count(), 5)
        with self.assertRaises(services.AgendaError):
            self.add("La sexta")
        EventQuestion.objects.all().delete()
        self.assertContains(self.client.post(self.url("questions"), {"text": "¿Cuál?", "kind": "opcion", "options": "Una"}),
                            "Escribe de 2 a 8 opciones")
        self.assertContains(self.client.post(self.url("questions"), {"text": "x" * 121, "kind": "si_no"}), "120")
        self.assertEqual(self.add("Dos\nlíneas\r\ny   espacios").text, "Dos líneas y espacios")

    def test_no_free_text_question_where_the_event_protects_minors(self):
        """Defect: a box to type in about somebody who may be a minor."""
        self.require_consent()
        page = self.client.post(self.url("questions"), {"text": "Cuéntanos algo", "kind": "texto"})
        self.assertContains(page, questions.MINORS_NO_TEXT)
        with self.assertRaises(services.AgendaError) as refused:
            self.add("Cuéntanos algo", "texto")
        self.assertEqual(refused.exception.code, "minors_no_text")
        self.assertFalse(EventQuestion.objects.exists())
        self.add("¿Vienes en camión?", "si_no")
        self.assertEqual(EventQuestion.objects.count(), 1)

    def test_turning_the_rule_on_stops_asking_free_text_and_deletes_what_was_typed(self):
        text = self.add("¿De dónde nos visitas?", "texto")
        yes_no = self.add()
        registration = services.register(self.retiro, self.member, self.member_user)
        questions.save_answers(registration, {str(text.pk): "De Toluca", str(yes_no.pk): "Sí"})
        services.save_page(self.retiro, self.admin, requires_guardian_consent=True)
        self.assertEqual(list(RegistrationAnswer.objects.values_list("value", flat=True)), ["Sí"])
        self.assertEqual([row.pk for row in questions.asked(self.retiro)], [yes_no.pk])
        self.assertContains(self.client.get(self.url("questions")), "No se pregunta.")
        self.assertEqual(questions.save_answers(registration, {str(text.pk): "Otra vez"}), 0)

    def test_who_may_not_write_questions(self):
        item = self.add()
        urls = [self.url("questions"), self.url("question_edit", item.pk), self.url("question_delete", item.pk)]
        self.client.logout()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)
        for user, status in ((self.member_user, 403), (self.admin_b, 404)):
            self.client.force_login(user)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, status, url)
                self.assertEqual(self.client.post(url, {"text": "Cambiada", "kind": "si_no"}).status_code, status, url)
        # The administration of the other church with ITS event and this church's question id: a guessed parent.
        guessed = reverse("agenda:question_edit", args=[self.retiro_b.slug, item.pk])
        self.assertEqual(self.client.get(guessed).status_code, 404)
        self.assertEqual(self.client.post(reverse("agenda:question_delete", args=[self.retiro_b.slug, item.pk])).status_code, 404)
        item.refresh_from_db()
        self.assertEqual(item.text, "¿Te quedas a comer?")
        self.client.force_login(self.admin)
        flags.set_enabled(self.church, "community", False)
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404)


class MemberAnswerTests(QuestionsFixture):
    def setUp(self):
        self.client.force_login(self.member_user)

    def test_signing_up_becomes_a_step_that_asks_and_the_answers_are_stored(self):
        food = self.add(required=True)
        size = self.add("¿Qué talla?", "opcion", options="Chica\nGrande")
        note = self.add("¿De dónde vienes?", "texto")
        page = self.client.get(self.url())
        self.assertNotContains(page, 'data-inline="inscripcion"')  # one more step can follow: never in place
        self.assertNotContains(self.client.get(reverse("agenda:list")), f'data-inline="ev-{self.retiro.slug}"')
        step = self.client.post(self.url("register"))
        self.assertEqual(step.status_code, 200)
        self.assertContains(step, "Antes de inscribirte")
        self.assertContains(step, "btn--primary", count=1)
        self.assertFalse(EventRegistration.objects.exists())
        missing = self.client.post(self.url("register"), {"preguntas": "1", self.field(size): "Grande"})
        self.assertContains(missing, "Responde esta pregunta.")
        self.assertFalse(EventRegistration.objects.exists())
        forged = self.client.post(self.url("register"), {"preguntas": "1", self.field(food): "Sí", self.field(size): "Enorme"})
        self.assertContains(forged, "Elige una de las opciones.")
        done = self.client.post(self.url("register"), {"preguntas": "1", self.field(food): "Sí", self.field(size): "Grande",
                                                       self.field(note): "  De   Toluca "}, follow=True)
        self.assertContains(done, "Te inscribiste a Retiro de jóvenes.")
        registration = EventRegistration.objects.get()
        self.assertEqual(dict(registration.answers.values_list("question_id", "value")),
                         {food.pk: "Sí", size.pk: "Grande", note.pk: "De Toluca"})
        # The member never reads answers back, their own included, and no audit row carries one.
        self.assertNotContains(self.client.get(self.url()), "De Toluca")
        for row in ChurchAuditEvent.objects.all():
            self.assertNotIn("Toluca", row.summary + str(row.metadata))

    def test_an_event_without_questions_signs_up_in_one_press_as_before(self):
        self.assertContains(self.client.get(self.url()), 'data-inline="inscripcion"')
        self.assertEqual(self.client.post(self.url("register")).status_code, 302)
        self.assertEqual(EventRegistration.objects.get().status, "inscrita")

    def test_cancelling_deletes_the_answers(self):
        """Defect: what somebody answered outliving the registration it was for."""
        food = self.add()
        self.client.post(self.url("register"), {"preguntas": "1", self.field(food): "No"})
        self.assertEqual(RegistrationAnswer.objects.count(), 1)
        self.client.post(self.url("cancel"))
        self.assertEqual(EventRegistration.objects.get().status, "cancelada")
        self.assertFalse(RegistrationAnswer.objects.exists())

    def test_the_minors_event_asks_the_birth_date_and_the_questions_on_one_page(self):
        self.require_consent()
        food = self.add(required=True)
        step = self.client.post(self.url("register"))
        self.assertContains(step, "Tu fecha de nacimiento")
        self.assertContains(step, "¿Te quedas a comer?")
        self.client.post(self.url("register"), {"preguntas": "1", "birth_date": "", self.field(food): "Sí"})
        registration = EventRegistration.objects.get()
        self.assertEqual((registration.status, registration.pending_reason), ("pendiente", "age"))  # the rule is untouched
        self.assertEqual(registration.answers.get().value, "Sí")

    def test_the_answers_are_read_only_by_the_administration_and_are_escaped(self):
        note = self.add("¿Algo <b>más</b>?", "texto")
        self.client.post(self.url("register"), {"preguntas": "1", self.field(note): "<script>alert(1)</script>"})
        url = self.url("registrations")
        self.assertEqual(self.client.get(url).status_code, 403)  # the member, own answers included
        self.client.force_login(self.leader_user)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin_b)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.admin)
        page = self.client.get(url).content.decode()
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("¿Algo &lt;b&gt;más&lt;/b&gt;?", page)
        step = self.client.get(self.url("questions")).content.decode()
        self.assertNotIn("<b>más</b>", step)


class VisitorAnswerTests(VisitorFixture):
    def test_a_visitor_answers_on_the_form_and_the_answers_arrive_with_the_confirmation(self):
        food = questions.save_question(self.retiro, self.admin, text="¿Te quedas a comer?", kind="si_no", required=True)
        page = self.client.get(form_url(self.retiro))
        self.assertContains(page, "¿Te quedas a comer?")
        missing = self.client.post(form_url(self.retiro), {**NAME, "email": ADDRESS, "adult": "on"})
        self.assertContains(missing, "Responde esta pregunta.")
        self.client.post(form_url(self.retiro), {**NAME, "email": ADDRESS, "adult": "on", f"{questions.PREFIX}{food.pk}": "No"})
        row = self.request_row()
        self.assertEqual(row.answers, {str(food.pk): "No"})
        self.assertFalse(RegistrationAnswer.objects.exists())  # nothing is an answer before the address is proved
        state, registration = visitors.confirm(row)
        self.assertEqual(registration.answers.get().value, "No")

    def test_an_answer_that_no_longer_fits_is_dropped_at_confirmation(self):
        size = questions.save_question(self.retiro, self.admin, text="¿Talla?", kind="opcion", options="Chica\nGrande")
        gone = questions.save_question(self.retiro, self.admin, text="¿Vienes en camión?", kind="si_no")
        row = self.requested(answers={str(size.pk): "Grande", str(gone.pk): "Sí", "999999": "Sí", "x": ["lista"]})
        questions.save_question(self.retiro, self.admin, size, text="¿Talla?", options="Chica\nMediana")
        questions.delete_question(gone, self.admin)
        state, registration = visitors.confirm(row)
        self.assertEqual(state, "ok")
        self.assertFalse(registration.answers.exists())
