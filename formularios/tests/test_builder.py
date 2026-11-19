"""The staff's side of a form: what it may ask, publishing what was read, versions, readers."""

from datetime import timedelta

from django.test import Client

from formularios import access, services
from formularios.models import Form, FormVersion, Question, Submission
from people import flags
from people.models import ChurchAuditEvent

from .base import MEMBERS, PUBLIC, FormFixture, url


class BuilderTests(FormFixture):
    def settings(self, **changes):
        return {
            "title": "Convivio", "purpose": "Para organizar la comida.", "audience": "iglesia", "opens_on": self.today.isoformat(),
            "closes_on": (self.today + timedelta(days=5)).isoformat(), "retention_days": "90", "task_due_days": "7", **changes,
        }

    # ---- the journey ----------------------------------------------------------------------------
    def test_the_administration_builds_publishes_and_changes_a_form(self):
        self.client.force_login(self.admin)
        page = self.client.get(url("create"))
        self.assertContains(page, "No preguntes por salud, vida espiritual, conflictos familiares ni datos de menores de edad")
        self.assertContains(page, "no sirven para inscribir a niñas, niños o adolescentes")
        self.assertEqual(page.content.decode().count("<h1"), 1)
        response = self.client.post(url("create"), self.settings())
        form = Form.objects.get()
        self.assertRedirects(response, url("detail", form.pk))
        self.assertEqual((form.church, form.status, form.retention_days, form.current_version), (self.church, "borrador", 90, None))

        page = self.client.get(url("detail", form.pk))
        self.assertContains(page, "No preguntes por salud")
        self.assertContains(page, "Agrega al menos una pregunta.")
        self.assertNotContains(page, "Datos de contacto")  # only a public form may ask for them
        self.assertContains(self.client.get(url("question_create", form.pk) + "?tipo=opcion"), "Una por renglón")
        self.client.post(url("question_create", form.pk), {"tipo": "opcion", "label": "¿Qué traes?", "options": "Guisado\nPostre\n\nPostre"})
        question = Question.objects.get()
        self.assertEqual((question.kind, question.options, question.position), ("opcion", ["Guisado", "Postre"], 1))
        self.client.post(url("question_create", form.pk), {"tipo": "corto", "label": "¿Cuál postre?", "show_if": f"{question.pk}|Postre"})
        dependent = Question.objects.get(kind="corto")
        self.assertEqual((dependent.show_if_question, dependent.show_if_value), (question, "Postre"))

        preview = self.client.get(url("publish", form.pk))
        self.assertContains(preview, "Así lo verá quien responda.")
        self.assertContains(preview, "Responde solo si en «¿Qué traes?» elegiste «Postre».")
        response = self.client.post(url("publish", form.pk), {"visto": preview.context["token"]}, follow=True)
        self.assertContains(response, "Publicaste el formulario.")
        form.refresh_from_db()
        version = form.current_version
        self.assertEqual((form.status, version.number, version.title, version.purpose), ("publicado", 1, "Convivio", "Para organizar la comida."))
        self.assertEqual([item["label"] for item in version.schema], ["¿Qué traes?", "¿Cuál postre?"])
        self.assertEqual(version.schema[1]["show_if"], {"key": question.key, "value": "Postre"})

        # the same thing published again writes no version; a change of the questions writes the next one
        again = self.client.get(url("publish", form.pk)).context["token"]
        self.client.post(url("publish", form.pk), {"visto": again})
        self.assertEqual(FormVersion.objects.count(), 1)
        self.client.post(url("question_edit", form.pk, dependent.pk), {"tipo": "corto", "label": "¿Qué postre traes?"})
        self.assertContains(self.client.get(url("detail", form.pk)), "Hay cambios sin publicar.")
        form.refresh_from_db()
        self.assertEqual(form.current_version.schema[1]["label"], "¿Cuál postre?")  # people still read what was published
        self.client.post(url("publish", form.pk), {"visto": self.client.get(url("publish", form.pk)).context["token"]})
        form.refresh_from_db()
        self.assertEqual((form.current_version.number, FormVersion.objects.count()), (2, 2))
        actions = list(ChurchAuditEvent.objects.filter(church=self.church, action__startswith="formulario_").values_list("action", flat=True))
        self.assertEqual(actions.count("formulario_publicado"), 2)
        for row in ChurchAuditEvent.objects.filter(action__startswith="formulario_"):
            self.assertNotIn("Convivio", row.summary + str(row.metadata))  # ids and fixed words, never a title
            self.assertEqual(row.metadata["form_id"], form.pk)

    def test_an_answer_keeps_the_version_and_the_purpose_it_was_shown(self):
        form = self.member_form()
        first, _ = self.answer(form)
        services.update_form(form, self.admin, title=form.title, purpose="Para otra cosa.", audience=form.audience, retention_days=90,
                             opens_on=form.opens_on, closes_on=form.closes_on, creates_task=False, task_owner=None, task_due_days=7)
        form = self.publish(form)
        second, _ = self.answer(form, person=self.leader)
        first.refresh_from_db()
        self.assertEqual((first.version.number, first.purpose_shown), (1, "Para organizar la comida."))
        self.assertEqual((second.version.number, second.purpose_shown), (2, "Para otra cosa."))

    # ---- publishing is of what was read ---------------------------------------------------------
    def test_a_draft_that_changed_after_the_preview_is_not_published(self):
        form = self.draft()
        services.save_question(form, self.admin, kind="corto", label="Uno")
        self.client.force_login(self.admin)
        token = self.client.get(url("publish", form.pk)).context["token"]
        services.save_question(form, self.admin, kind="corto", label="Otra que nadie leyó")
        response = self.client.post(url("publish", form.pk), {"visto": token})
        self.assertContains(response, "El formulario cambió después de que abriste esta página")
        self.assertContains(response, "Otra que nadie leyó")
        form.refresh_from_db()
        self.assertEqual((form.status, FormVersion.objects.count()), ("borrador", 0))
        for changed in ({"closes_on": form.closes_on + timedelta(days=1)}, {"retention_days": 30}, {"creates_task": True}, {"purpose": "Otro fin."}):
            token = services.preview_token(form)
            values = {"title": form.title, "purpose": form.purpose, "audience": form.audience, "retention_days": form.retention_days,
                      "opens_on": form.opens_on, "closes_on": form.closes_on, "creates_task": form.creates_task, "task_owner": None,
                      "task_due_days": 7, **changed}
            form = services.update_form(form, self.admin, **values)
            with self.assertRaises(services.StalePreview, msg=changed):
                services.publish(form, self.admin, token)
        with self.assertRaises(services.StalePreview):
            services.publish(form, self.admin, "")

    # ---- what a form may ask --------------------------------------------------------------------
    def test_the_rules_of_the_questions(self):
        form = self.draft()
        save = lambda **values: services.save_question(form, self.admin, **values)  # noqa: E731
        with self.assertRaisesMessage(services.FormError, "solo se piden en un formulario público"):
            save(kind="contacto", label="Datos", ask_email=True, consent="Acepto.")
        with self.assertRaisesMessage(services.FormError, "al menos dos opciones"):
            save(kind="opcion", label="Una", options=["Sola"])
        with self.assertRaisesMessage(services.FormError, "hasta 12 opciones"):
            save(kind="varias", label="Muchas", options=[str(n) for n in range(13)])
        with self.assertRaisesMessage(services.FormError, "No pongas enlaces"):
            save(kind="corto", label="Entra a https://malo.invalid y gana")
        with self.assertRaisesMessage(services.FormError, "No pongas enlaces"):
            save(kind="corto", label="<b>hola</b>")
        with self.assertRaisesMessage(services.FormError, "Elige un tipo"):
            save(kind="archivo", label="Sube tu foto")
        question, _ = save(kind="corto", label="  Dos\nrenglones\tjuntos  ")
        self.assertEqual(question.label, "Dos renglones juntos")
        save(kind="largo", label="Cuéntanos")  # a form of the church may ask a long text

        yes_no, _ = save(kind="si_no", label="¿Vienes?")
        text, _ = save(kind="corto", label="Texto")
        child, _ = save(kind="corto", label="Hija", show_if_question_id=yes_no.pk, show_if_value="Sí")
        with self.assertRaisesMessage(services.FormError, "no puede decidir"):  # not a choice
            save(kind="corto", label="X", show_if_question_id=text.pk, show_if_value="a")
        with self.assertRaisesMessage(services.FormError, "tal como está escrita"):
            save(kind="corto", label="X", show_if_question_id=yes_no.pk, show_if_value="Quizá")
        option, _ = save(kind="opcion", label="Opción hija", options=["a", "b"], show_if_question_id=yes_no.pk, show_if_value="No")
        with self.assertRaisesMessage(services.FormError, "no puede decidir"):  # one level only
            save(kind="corto", label="Nieta", show_if_question_id=option.pk, show_if_value="a")
        with self.assertRaisesMessage(services.FormError, "no puede decidir"):  # a later question
            save(question=text, label="Texto", show_if_question_id=option.pk, show_if_value="a")
        with self.assertRaisesMessage(services.FormError, "Otra pregunta depende de esta"):
            first, _ = save(kind="si_no", label="Primera")
            Question.objects.filter(pk=first.pk).update(position=0)
            save(question=yes_no, label="¿Vienes?", show_if_question_id=first.pk, show_if_value="Sí")

        # moving the parent after its child, or deleting it, drops the condition instead of leaving it dangling
        while services.move_question(form, self.admin, yes_no.pk, up=False) == 0 and Question.objects.get(pk=child.pk).show_if_question_id:
            pass
        self.assertIsNone(Question.objects.get(pk=child.pk).show_if_question_id)
        self.assertEqual(sorted(Question.objects.filter(form=form).values_list("position", flat=True)),
                         list(range(1, Question.objects.filter(form=form).count() + 1)))

    def test_thirty_questions_and_no_more(self):
        form = self.draft()
        for number in range(30):
            services.save_question(form, self.admin, kind="corto", label=f"Pregunta {number}")
        with self.assertRaisesMessage(services.FormError, "hasta 30 preguntas"):
            services.save_question(form, self.admin, kind="corto", label="La 31")
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(url("detail", form.pk)), "Agregar una pregunta")

    def test_a_public_form_takes_long_text_only_after_an_explicit_acknowledgement(self):
        form = self.draft(audience=PUBLIC)
        self.client.force_login(self.admin)
        page = self.client.get(url("question_create", form.pk) + "?tipo=largo")
        self.assertContains(page, "la gente puede escribir cosas delicadas")
        response = self.client.post(url("question_create", form.pk), {"tipo": "largo", "label": "Cuéntanos"})
        self.assertContains(response, "Marca la casilla para confirmar que lo sabes")
        self.assertFalse(Question.objects.exists())
        self.client.post(url("question_create", form.pk), {"tipo": "largo", "label": "Cuéntanos", "acknowledge_long_text": "on"})
        form.refresh_from_db()
        self.assertEqual((Question.objects.get().kind, form.long_text_ack_by), ("largo", self.admin))
        self.assertIsNotNone(form.long_text_ack_at)

    def test_a_form_that_became_public_with_a_long_text_is_not_published_without_it(self):
        form = self.draft()
        services.save_question(form, self.admin, kind="largo", label="Cuéntanos")
        values = {"title": form.title, "purpose": form.purpose, "retention_days": 90, "opens_on": form.opens_on, "closes_on": form.closes_on,
                  "creates_task": False, "task_owner": None, "task_due_days": 7}
        form = services.update_form(form, self.admin, audience=PUBLIC, **values)
        self.assertIn("texto largo", services.publish_problems(form)[0])
        with self.assertRaises(services.FormError):
            services.publish(form, self.admin, services.preview_token(form))
        self.assertEqual(Form.objects.get().status, "borrador")

    def test_after_publishing_the_audience_stays_and_the_retention_only_shrinks(self):
        form = self.member_form()
        values = {"title": form.title, "purpose": form.purpose, "opens_on": form.opens_on, "closes_on": form.closes_on,
                  "creates_task": False, "task_owner": None, "task_due_days": 7}
        with self.assertRaisesMessage(services.FormError, "no cambia de quién lo responde"):
            services.update_form(form, self.admin, audience=PUBLIC, retention_days=90, **values)
        with self.assertRaisesMessage(services.FormError, "se puede acortar, no alargar"):
            services.update_form(form, self.admin, audience=MEMBERS, retention_days=365, **values)
        self.assertEqual(services.update_form(form, self.admin, audience=MEMBERS, retention_days=30, **values).retention_days, 30)

    def test_dates_and_retention_are_checked(self):
        for changed, words in (
            ({"closes_on": self.today - timedelta(days=5)}, "no puede ser antes"),
            ({"closes_on": self.today + timedelta(days=500)}, "hasta un año"),
            ({"retention_days": 45}, "cuánto se guardan"),
            ({"title": "Gana un premio en www.malo.invalid"}, "No pongas enlaces"),
            ({"purpose": ""}, "para qué lo pides"),
        ):
            with self.assertRaisesMessage(services.FormError, words):
                self.draft(**changed)

    def test_closing_is_final(self):
        form = self.member_form()
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url("close", form.pk)), "no se puede volver a abrir")
        self.client.post(url("close", form.pk))
        form.refresh_from_db()
        self.assertEqual((form.status, form.closed_on), ("cerrado", self.today))
        self.assertEqual(self.client.get(url("edit", form.pk)).status_code, 404)
        self.assertEqual(self.client.get(url("publish", form.pk)).status_code, 404)
        with self.assertRaises(services.FormError):
            services.save_question(form, self.admin, kind="corto", label="Tarde")
        self.assertEqual(self.client.get(url("fill", form.pk)).status_code, 404)

    # ---- readers --------------------------------------------------------------------------------
    def test_a_reader_is_a_scope_that_exists_and_ends_with_the_role(self):
        form = self.member_form()
        self.assertFalse(access.can_read(self.leader_user, form))
        self.assertFalse(access.can_read(self.pastor_user, form))  # a pastor reads no form by being a pastor
        self.client.force_login(self.admin)
        page = self.client.get(url("readers", form.pk))
        self.assertContains(page, "Quien guía el grupo Jóvenes Roca")
        self.assertNotContains(page, "Grupo de la iglesia B")
        self.client.post(url("readers", form.pk), {"lector": f"group:{self.group.pk}"})
        self.client.post(url("readers", form.pk), {"lector": f"group:{self.group.pk}"})  # twice is one row
        self.assertEqual(form.readers.count(), 1)
        self.assertTrue(access.can_read(self.leader_user, form))
        self.assertEqual(access.readable_form_ids(self.leader_user, self.church), {form.pk})
        for bad in (f"group:{self.group_b.pk}", "church:1", "event:1", "group:x", ""):
            self.client.post(url("readers", form.pk), {"lector": bad})
        self.assertEqual(form.readers.count(), 1)
        # the member of the group, who does not guide it, reads nothing
        from groups import services as group_services

        group_services.set_membership(self.group, self.member, self.admin)
        self.assertFalse(access.can_read(self.member_user, form))
        # an archived group gives nothing, by the list and by the single question alike
        type(self.group).objects.filter(pk=self.group.pk).update(status="archivado")
        self.assertFalse(access.can_read(self.leader_user, form))
        self.assertEqual(access.readable_form_ids(self.leader_user, self.church), set())

    def test_removing_a_reader_leaves_their_open_tasks_without_an_owner(self):
        form = self.member_form(reader=True, creates_task=True)
        services.update_form(form, self.admin, title=form.title, purpose=form.purpose, audience=form.audience, retention_days=90,
                             opens_on=form.opens_on, closes_on=form.closes_on, creates_task=True, task_owner=self.leader_user, task_due_days=7)
        submission, _ = self.answer(form)
        self.assertEqual(submission.task.owner, self.leader_user)
        self.client.force_login(self.admin)
        self.client.post(url("readers", form.pk), {"quitar": form.readers.get().pk})
        submission.task.refresh_from_db()
        form.refresh_from_db()
        self.assertEqual((submission.task.owner, form.task_owner, form.readers.count()), (None, None, 0))

    # ---- who ------------------------------------------------------------------------------------
    def staff_requests(self, form, submission):
        question = form.questions.first()
        return [
            ("get", url("create"), {}), ("post", url("create"), self.settings()),
            ("get", url("loose_tasks"), {}),
            ("get", url("detail", form.pk), {}), ("get", url("edit", form.pk), {}), ("post", url("edit", form.pk), self.settings()),
            ("get", url("question_create", form.pk) + "?tipo=corto", {}), ("post", url("question_create", form.pk), {"tipo": "corto", "label": "X"}),
            ("post", url("question_edit", form.pk, question.pk), {"label": "X"}),
            ("post", url("question_delete", form.pk, question.pk), {}), ("post", url("question_move", form.pk, question.pk), {"hacia": "abajo"}),
            ("get", url("publish", form.pk), {}), ("post", url("publish", form.pk), {"visto": services.preview_token(form)}),
            ("get", url("close", form.pk), {}), ("post", url("close", form.pk), {}),
            ("get", url("readers", form.pk), {}), ("post", url("readers", form.pk), {"lector": f"group:{self.other_group.pk}"}),
            ("post", url("export", form.pk), {}),
            ("get", url("task_assign", form.pk, submission.pk), {}), ("post", url("task_assign", form.pk, submission.pk), {"owner": ""}),
            ("get", url("add_person", form.pk, submission.pk), {}), ("post", url("add_person", form.pk, submission.pk), {}),
        ]

    def reader_requests(self, form, submission):
        return [
            ("get", url("submissions", form.pk), {}), ("get", url("submission", form.pk, submission.pk), {}),
            ("post", url("task", form.pk, submission.pk), {"accion": "nota", "note": "x"}),
        ]

    def assert_all(self, user, requests, expected):
        if user is not None:
            self.client.force_login(user)
        for method, address, data in requests:
            response = getattr(self.client, method)(address, data)
            self.assertEqual(response.status_code, expected, f"{getattr(user, 'email', 'nadie')} {method} {address}")

    def test_only_the_administration_builds_and_only_readers_read(self):
        form = self.member_form(reader=True, creates_task=True)
        submission, _ = self.answer(form)
        before = (Form.objects.count(), Question.objects.count(), form.readers.count(), FormVersion.objects.count())
        staff, reading = self.staff_requests(form, submission), self.reader_requests(form, submission)
        for user in (self.member_user, self.pastor_user, self.coord1, self.leader_user):  # the reader builds nothing either
            self.assert_all(user, staff, 403)
        for user in (self.member_user, self.pastor_user, self.coord1):
            self.assert_all(user, reading, 403)
        self.assertEqual(self.client.get(url("manage")).status_code, 403)
        # another church: its administration, with its own module on, finds nothing at these addresses
        self.assert_all(self.admin_b, staff[3:] + reading, 404)
        self.assert_all(self.member_b_user, staff[3:] + reading, 404)
        form.refresh_from_db()
        self.assertEqual((Form.objects.count(), Question.objects.count(), form.readers.count(), FormVersion.objects.count()), before)
        self.assertEqual((form.status, submission.task.note), ("publicado", ""))
        # the reader reads this form and only this one
        other = self.member_form(title="Otro formulario")
        other_submission, _ = self.answer(other, person=self.asked)
        self.client.force_login(self.leader_user)
        for method, address, data in reading:
            self.assertIn(getattr(self.client, method)(address, data).status_code, (200, 302), address)
        self.assert_all(self.leader_user, self.reader_requests(other, other_submission), 403)
        page = self.client.get(url("manage"))
        self.assertContains(page, "Convivio de aniversario")
        self.assertNotContains(page, "Otro formulario")
        # a submission is fetched under its own form
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url("submission", other.pk, submission.pk)).status_code, 404)
        self.assertEqual(self.client.post(url("task", other.pk, submission.pk), {"accion": "hecho"}).status_code, 404)

    def test_signed_out_goes_to_sign_in_and_the_module_off_is_a_404(self):
        form = self.member_form(reader=True, creates_task=True)
        submission, _ = self.answer(form)
        everything = self.staff_requests(form, submission) + self.reader_requests(form, submission) + [
            ("get", url("manage"), {}), ("get", url("queue"), {}), ("get", url("list"), {}), ("get", url("fill", form.pk), {}),
            ("post", url("fill", form.pk), self.member_data(form)), ("get", url("thanks", form.pk), {}),
        ]
        for method, address, data in everything:
            response = getattr(self.client, method)(address, data)
            self.assertEqual(response.status_code, 302, address)
            self.assertIn("/cuenta/entrar/", response["Location"])
        flags.set_enabled(self.church, "community", False)
        for user in (self.admin, self.leader_user, self.member_user):
            self.assert_all(user, everything, 404)
        self.assertEqual(Submission.objects.count(), 1)

    def test_every_post_needs_the_csrf_token(self):
        form = self.member_form(reader=True, creates_task=True)
        submission, _ = self.answer(form)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        posts = [request for request in self.staff_requests(form, submission) + self.reader_requests(form, submission) if request[0] == "post"]
        posts.append(("post", url("fill", form.pk), self.member_data(form)))
        for _method, address, data in posts:
            self.assertEqual(client.post(address, data).status_code, 403, address)
        form.refresh_from_db()
        self.assertEqual((form.status, Form.objects.count(), Submission.objects.count()), ("publicado", 1, 1))

    def test_get_changes_nothing(self):
        form = self.member_form()
        submission, _ = self.answer(form)
        self.client.force_login(self.admin)
        question = form.questions.first()
        for name, args in (("question_delete", (form.pk, question.pk)), ("question_move", (form.pk, question.pk)), ("export", (form.pk,)),
                           ("task", (form.pk, submission.pk))):
            self.assertEqual(self.client.get(url(name, *args)).status_code, 405, name)
