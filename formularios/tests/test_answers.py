"""Answering: a person of the church with their account, and anybody with the address of a public form."""

from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.contrib.sessions.models import Session
from django.test import Client
from django.urls import reverse

from events.models import AuthThrottle
from formularios import public, services
from formularios.fields import HONEYPOT, AnswerForm
from formularios.models import Form, FormTask, Submission
from people import flags
from people.models import ChurchAuditEvent

from .base import OTHER_ANSWER, PUBLIC, FormFixture, url


def public_url(form, name="fill"):
    return reverse(f"formulario:{name}", args=[form.public_token])


class MemberAnswerTests(FormFixture):
    def test_a_person_of_the_church_answers_and_the_reader_reads_it(self):
        form = self.member_form(reader=True, creates_task=True)
        self.client.force_login(self.member_user)
        page = self.client.get(url("list"))
        self.assertContains(page, "Convivio de aniversario")
        self.assertNotContains(page, "Ver respuestas y formularios")
        page = self.client.get(url("fill", form.pk))
        self.assertContains(page, "Para qué lo pedimos:")
        self.assertContains(page, "Para organizar la comida.")
        self.assertContains(page, "Se borran a más tardar el")
        self.assertContains(page, "Responde solo si en «¿Vas a venir?» elegiste «Sí».")
        self.assertEqual(page.content.decode().count("<h1"), 1)
        response = self.client.post(url("fill", form.pk), self.member_data(form))
        self.assertRedirects(response, url("thanks", form.pk))
        submission = Submission.objects.get()
        goes, brings, more = self.keys(form)
        self.assertEqual(submission.answers, {goes: "Sí", brings: "Postre", more: OTHER_ANSWER})
        self.assertEqual((submission.person, submission.version, submission.purpose_shown), (self.member, form.current_version, "Para organizar la comida."))
        task = FormTask.objects.get()
        self.assertEqual((task.submission, task.owner, task.status, task.due_on, task.note), (submission, None, "abierta", self.today + timedelta(days=7), ""))
        audit = ChurchAuditEvent.objects.filter(action__in=("formulario_respuesta", "formulario_tarea"))
        self.assertEqual(audit.count(), 2)
        for row in audit:
            self.assertNotIn(OTHER_ANSWER, row.summary + str(row.metadata))
            self.assertNotIn("Diego", row.summary + str(row.metadata))
        # whoever answered does not read answers, not even their own, at the staff's addresses
        self.assertEqual(self.client.get(url("submission", form.pk, submission.pk)).status_code, 403)
        self.client.force_login(self.leader_user)
        page = self.client.get(url("submission", form.pk, submission.pk))
        self.assertContains(page, "Diego Hernández Cruz")
        self.assertContains(page, OTHER_ANSWER)
        self.assertContains(page, "«Para organizar la comida.»")
        self.assertNotContains(page, "Agregar a personas")
        self.assertNotContains(page, "Cambiar responsable o fecha")

    def test_a_question_that_was_not_shown_is_neither_demanded_nor_kept(self):
        form = self.member_form()
        goes, brings, more = self.keys(form)
        self.client.force_login(self.member_user)
        self.client.post(url("fill", form.pk), {"version": form.current_version_id, goes: "No", brings: "Postre", more: ""})
        self.assertEqual(Submission.objects.get().answers, {goes: "No"})  # what was sent for the hidden one is dropped
        self.client.force_login(self.leader_user)
        response = self.client.post(url("fill", form.pk), {"version": form.current_version_id, goes: "Sí"})
        self.assertContains(response, "Responde esta pregunta.")  # shown and required: demanded
        self.assertContains(response, "Hay un problema")
        response = self.client.post(url("fill", form.pk), {"version": form.current_version_id, goes: "Sí", brings: "Carne asada"})
        self.assertEqual(response.status_code, 200)  # not one of the options
        response = self.client.post(url("fill", form.pk), {"version": form.current_version_id})
        self.assertContains(response, "Responde esta pregunta.")
        self.assertEqual(Submission.objects.count(), 1)

    def test_every_kind_is_validated_and_kept_as_plain_text(self):
        form = self.draft()
        for values in (
            {"kind": "corto", "label": "Corto"}, {"kind": "largo", "label": "Largo"},
            {"kind": "varias", "label": "Varias", "options": ["a", "b", "c"]}, {"kind": "fecha", "label": "Fecha"},
        ):
            services.save_question(form, self.admin, **values)
        form = self.publish(form)
        short, long, multi, day = self.keys(form)
        bad = AnswerForm({short: "x" * 201, long: "y" * 1001, multi: ["a", "z"], day: "31/02/2026"}, schema=form.current_version.schema)
        self.assertFalse(bad.is_valid())
        self.assertEqual(set(bad.errors), {short, long, multi, day})
        submission, _ = self.answer(form, {short: "  dos   espacios ", long: "uno\r\ndos", multi: ["c", "a"], day: "14/03/2026"})
        self.assertEqual(submission.answers, {short: "dos espacios", long: "uno\ndos", multi: ["a", "c"], day: "2026-03-14"})
        self.assertEqual(services.describe(submission), [("Corto", "dos espacios"), ("Largo", "uno\ndos"), ("Varias", "a, c"), ("Fecha", "14/03/2026")])

    def test_what_a_person_typed_is_escaped_for_the_staff(self):
        form = self.member_form()
        submission, _ = self.answer(form, self.member_data(form, **{self.keys(form)[2]: '<script>alert("x")</script>'}))
        self.client.force_login(self.admin)
        page = self.client.get(url("submission", form.pk, submission.pk))
        self.assertContains(page, "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;")
        self.assertNotContains(page, "<script>alert")
        self.assertNotContains(self.client.get(url("submissions", form.pk)), "<script>alert")

    def test_the_same_person_twice_in_a_day_is_one_answer_and_one_task(self):
        form = self.member_form(creates_task=True)
        self.client.force_login(self.member_user)
        for text in ("Primera", "Segunda"):
            response = self.client.post(url("fill", form.pk), self.member_data(form, **{self.keys(form)[2]: text}))
            self.assertRedirects(response, url("thanks", form.pk))
        submission = Submission.objects.get()
        self.assertEqual(submission.answers[self.keys(form)[2]], "Primera")  # nothing is replaced
        self.assertEqual(FormTask.objects.count(), 1)
        self.assertEqual(ChurchAuditEvent.objects.filter(action="formulario_respuesta").count(), 1)
        self.assertContains(self.client.get(url("fill", form.pk)), "Ya respondiste este formulario hoy.")
        # another person the same day is another answer; the same person a day later too; a new version too
        self.answer(form, person=self.leader)
        self.assertEqual(Submission.objects.count(), 2)
        later = submission.created_at + timedelta(days=1, minutes=1)
        _again, created = self.answer(form, now=later)
        self.assertTrue(created)
        self.assertEqual((Submission.objects.count(), FormTask.objects.count()), (3, 3))

    def test_a_new_version_is_answered_again_and_an_old_page_writes_nothing(self):
        form = self.member_form()
        self.client.force_login(self.member_user)
        old = self.member_data(form)
        self.answer(form)
        services.save_question(form, self.admin, kind="corto", label="Pregunta nueva")
        form = self.publish(form)
        response = self.client.post(url("fill", form.pk), old)  # a page drawn before the new version
        self.assertContains(response, "Este formulario cambió mientras lo llenabas.")
        self.assertContains(response, "Pregunta nueva")
        self.assertEqual(Submission.objects.count(), 1)
        with self.assertRaises(services.FormChanged):  # and under the view, the service holds the same
            services.submit(form, old["version"], answers={}, person=self.member)
        self.client.post(url("fill", form.pk), {**old, "version": form.current_version_id, self.keys(form)[3]: "x"})
        self.assertEqual(sorted(Submission.objects.values_list("version__number", flat=True)), [1, 2])

    def test_a_form_that_is_not_open_is_the_same_404(self):
        closed = self.member_form(title="Cerrado")
        services.close_form(closed, self.admin)
        not_open = [
            self.draft(title="Borrador"), closed,
            self.member_form(title="Mañana", opens_on=self.today + timedelta(days=1)),
            self.public_form(title="Público"),
        ]
        ended = self.member_form(title="Ayer")
        Form.objects.filter(pk=ended.pk).update(opens_on=self.today - timedelta(days=5), closes_on=self.today - timedelta(days=1))
        self.client.force_login(self.member_user)
        for form in [*not_open, ended]:
            self.assertEqual(self.client.get(url("fill", form.pk)).status_code, 404, form.title)
            self.assertEqual(self.client.post(url("fill", form.pk), {"version": form.current_version_id or 0}).status_code, 404, form.title)
            self.assertNotContains(self.client.get(url("list")), form.title)
            if form.current_version_id and not form.is_public:
                with self.assertRaises(services.FormChanged):
                    services.submit(form, form.current_version_id, answers={}, person=self.member)
        self.assertEqual(Submission.objects.count(), 0)

    def test_a_form_of_another_church_does_not_exist(self):
        form = self.member_form()
        self.client.force_login(self.member_b_user)
        self.assertNotContains(self.client.get(url("list")), "Convivio")
        self.assertEqual(self.client.get(url("fill", form.pk)).status_code, 404)
        self.assertEqual(self.client.post(url("fill", form.pk), self.member_data(form)).status_code, 404)
        self.assertEqual(self.client.get(url("thanks", form.pk)).status_code, 404)
        with self.assertRaises(services.FormChanged):
            services.submit(form, form.current_version_id, answers={}, person=self.member_b)
        self.assertEqual(Submission.objects.count(), 0)

    def test_the_last_day_is_a_day_of_the_church_not_of_the_server(self):
        self.church.time_zone = "America/New_York"
        self.church.save(update_fields=["time_zone"])
        form = self.member_form()
        Form.objects.filter(pk=form.pk).update(opens_on="2026-03-01", closes_on="2026-03-07")
        form.refresh_from_db()
        # 03:30 UTC on the 8th is still the 7th in New York; an hour and a half later the clocks there jumped (DST) and it is the 8th
        still = datetime(2026, 3, 8, 3, 30, tzinfo=dt_timezone.utc)
        self.assertEqual(services.church_today(self.church, still).isoformat(), "2026-03-07")
        self.assertTrue(services.is_open(form, services.church_today(self.church, still)))
        self.answer(form, now=still)
        over = datetime(2026, 3, 8, 5, 0, tzinfo=dt_timezone.utc)
        self.assertEqual(services.church_today(self.church, over).isoformat(), "2026-03-08")
        with self.assertRaises(services.FormChanged):
            self.answer(form, person=self.leader, now=over)
        self.assertEqual(Submission.objects.count(), 1)

    def test_a_form_keeps_a_bounded_number_of_answers(self):
        form = self.member_form()
        self.answer(form)
        with mock.patch.object(services, "MAX_SUBMISSIONS", 1):
            self.client.force_login(self.leader_user)
            response = self.client.post(url("fill", form.pk), self.member_data(form))
        self.assertContains(response, "ya no puede recibir más respuestas")
        self.assertEqual(Submission.objects.count(), 1)


class PublicAnswerTests(FormFixture):
    def test_anybody_with_the_address_answers_without_an_account_or_a_session(self):
        form = self.public_form(creates_task=True)
        client = Client(enforce_csrf_checks=True)
        page = client.get(public_url(form))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(set(page.cookies), {"csrftoken"})  # the one cookie: no session, nothing to follow anybody with
        self.assertContains(page, "Para organizar la comida.")
        self.assertContains(page, "Acepto que me contacten.")
        self.assertContains(page, 'name="robots" content="noindex, nofollow"')
        self.assertNotContains(page, "Entrar")
        self.assertEqual(page.content.decode().count("<h1"), 1)
        data = self.public_data(form, phone="55 1234 5678")
        self.assertEqual(client.post(public_url(form), data).status_code, 403)  # no token: Django's CSRF check
        self.assertEqual(Submission.objects.count(), 0)
        response = client.post(public_url(form), {**data, "csrfmiddlewaretoken": page.context["csrf_token"]})
        self.assertRedirects(response, public_url(form, "thanks"), fetch_redirect_response=False)
        self.assertNotIn("sessionid", response.cookies)
        thanks = client.get(public_url(form, "thanks"))
        self.assertContains(thanks, "Recibimos tus respuestas")
        self.assertNotIn("sessionid", thanks.cookies)
        self.assertEqual(Session.objects.count(), 0)
        submission = Submission.objects.get()
        self.assertEqual(
            (submission.person, submission.contact_first_name, submission.contact_last_name, submission.contact_email, submission.contact_phone,
             submission.consent_shown, submission.purpose_shown),
            (None, "Vera", "Visita", "visita@demo.invalid", "55 1234 5678", "Acepto que me contacten.", "Para organizar la comida."),
        )
        self.assertEqual(len(submission.email_key), 64)
        self.assertNotIn("visita", submission.email_key)
        self.assertEqual(FormTask.objects.get().submission, submission)
        audit = ChurchAuditEvent.objects.get(action="formulario_respuesta")
        self.assertEqual((audit.actor, audit.actor_label, audit.metadata), (None, "Sistema", {"form_id": form.pk}))
        self.assertNotIn("Vera", audit.summary)

    def test_everything_that_is_not_an_open_public_form_is_the_same_404(self):
        form = self.public_form()
        self.assertEqual(self.client.get(public_url(form)).status_code, 200)
        unknown = self.client.get(reverse("formulario:fill", args=["no-existe"]))
        self.assertEqual(unknown.status_code, 404)

        def assert_gone(label):
            # the reference is asked for again each time: the plain 404 page itself changes with the modules of the server
            unknown = self.client.get(reverse("formulario:fill", args=["no-existe"]))
            for name in ("fill", "thanks"):
                response = self.client.get(public_url(form, name))
                self.assertEqual((response.status_code, response.content), (404, unknown.content), f"{label} {name}")
            self.assertEqual(self.client.post(public_url(form), self.public_data(form)).status_code, 404, label)

        member_form = self.member_form(title="De la iglesia")
        self.assertEqual(self.client.get(public_url(member_form)).content, unknown.content)
        draft = self.draft(audience=PUBLIC)
        self.assertEqual(self.client.get(public_url(draft)).content, unknown.content)
        flags.set_enabled(self.church, "community", False)
        assert_gone("module off")
        flags.set_enabled(self.church, "community", True)
        type(self.church).objects.filter(pk=self.church.pk).update(active=False)
        assert_gone("church closed")
        type(self.church).objects.filter(pk=self.church.pk).update(active=True)
        Form.objects.filter(pk=form.pk).update(opens_on=self.today + timedelta(days=1))
        assert_gone("not yet")
        Form.objects.filter(pk=form.pk).update(opens_on=self.today - timedelta(days=9), closes_on=self.today - timedelta(days=1))
        assert_gone("over")
        Form.objects.filter(pk=form.pk).update(closes_on=self.today + timedelta(days=1))
        self.assertEqual(self.client.get(public_url(form)).status_code, 200)
        services.close_form(form, self.admin)
        assert_gone("closed")
        self.assertEqual(Submission.objects.count(), 0)
        # signed in or not, the page is the same and never a member's shell
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(public_url(form)).status_code, 404)

    def test_the_same_contact_twice_in_a_day_is_one_answer_and_one_task_and_says_nothing(self):
        form = self.public_form(creates_task=True)
        first = self.client.post(public_url(form), self.public_data(form, email="Vera@Demo.Invalid ", text="Primera"))
        second = self.client.post(public_url(form), self.public_data(form, email="vera@demo.invalid", text="Segunda"))
        self.assertEqual((first.status_code, first["Location"]), (second.status_code, second["Location"]))  # no oracle
        submission = Submission.objects.get()
        self.assertEqual(submission.answers[self.keys(form)[1]], "Primera")
        self.assertEqual(FormTask.objects.count(), 1)
        # the same phone written another way; another contact; the same contact a day later
        self.client.post(public_url(form), self.public_data(form, email="", phone="+52 55 1111 2222"))
        self.client.post(public_url(form), self.public_data(form, email="", phone="5511112222"))
        self.assertEqual((Submission.objects.count(), FormTask.objects.count()), (2, 2))
        self.client.post(public_url(form), self.public_data(form, email="otra@demo.invalid"))
        self.assertEqual(Submission.objects.count(), 3)
        _again, created = self.answer(form, self.public_data(form, email="vera@demo.invalid"), now=submission.created_at + timedelta(days=1, minutes=1))
        self.assertTrue(created)
        # the key is of the version: the same contact answers a new version
        services.save_question(form, self.admin, kind="si_no", label="¿Vienes?")
        form = self.publish(form)
        self.client.post(public_url(form), self.public_data(form, email="otra@demo.invalid"))
        self.assertEqual(Submission.objects.count(), 5)

    def test_the_honeypot_gets_the_same_thanks_and_writes_nothing(self):
        form = self.public_form(creates_task=True)
        page = self.client.get(public_url(form))
        self.assertContains(page, f'name="{HONEYPOT}"')
        self.assertContains(page, 'class="form-trap" aria-hidden="true"')
        response = self.client.post(public_url(form), {**self.public_data(form), HONEYPOT: "https://spam.invalid"})
        self.assertRedirects(response, public_url(form, "thanks"), fetch_redirect_response=False)
        self.assertEqual((Submission.objects.count(), FormTask.objects.count(), AuthThrottle.objects.count()), (0, 0, 0))
        self.assertFalse(ChurchAuditEvent.objects.filter(action="formulario_respuesta").exists())

    def test_the_contact_block_wants_a_name_a_way_to_reach_and_the_consent(self):
        form = self.public_form()
        contact, what = self.keys(form)
        for changed, words in (
            ({f"{contact}_nombre": ""}, "Escribe tu nombre."),
            ({f"{contact}_correo": "", f"{contact}_telefono": ""}, "Escribe tu correo o tu teléfono."),
            ({f"{contact}_acepto": ""}, "Marca la casilla"),
            ({f"{contact}_correo": "no-es-correo"}, "correo electrónico válido"),
            ({f"{contact}_telefono": "llámame"}, "al menos 8 números"),
        ):
            response = self.client.post(public_url(form), self.public_data(form, **changed))
            self.assertContains(response, words)
        self.assertEqual(Submission.objects.count(), 0)
        # an optional block may be left empty, and then nothing about a contact is kept
        optional = self.draft(audience=PUBLIC, title="Opcional")
        services.save_question(optional, self.admin, kind="contacto", label="Tus datos", ask_email=True, consent="Acepto.")
        services.save_question(optional, self.admin, kind="corto", label="Comentario", required=True)
        optional = self.publish(optional)
        contact, what = self.keys(optional)
        self.client.post(public_url(optional), {"version": optional.current_version_id, what: "Hola"})
        kept = Submission.objects.get()
        self.assertEqual((kept.contact_first_name, kept.contact_email, kept.consent_shown, kept.email_key, kept.phone_key), ("", "", "", "", ""))
        response = self.client.post(public_url(optional), {"version": optional.current_version_id, what: "Hola", f"{contact}_correo": "a@demo.invalid"})
        self.assertContains(response, "Escribe tu nombre.")
        self.assertContains(response, "Marca la casilla")

    def test_a_flood_slows_one_form_down_and_keeps_no_address(self):
        form, other = self.public_form(), self.public_form(title="Otro")
        with mock.patch.object(public, "LIMIT", 2):
            for number in range(2):
                self.assertEqual(self.client.post(public_url(form), self.public_data(form, email=f"v{number}@demo.invalid")).status_code, 302)
            response = self.client.post(public_url(form), self.public_data(form, email="v3@demo.invalid", text="Lo que escribí"))
            self.assertEqual(response.status_code, 429)
            self.assertContains(response, "Espera unos minutos", status_code=429)
            self.assertContains(response, "Lo que escribí", status_code=429)  # what was typed is still on the page
            self.assertEqual(self.client.post(public_url(other), self.public_data(other)).status_code, 302)  # the other form is not slowed
        self.assertEqual(Submission.objects.filter(form=form).count(), 2)
        self.assertEqual(sorted(AuthThrottle.objects.values_list("key", flat=True)), [f"formulario:{form.pk}", f"formulario:{other.pk}"])
        # an invalid send is not counted: only what would be written
        AuthThrottle.objects.all().delete()
        self.client.post(public_url(form), {"version": form.current_version_id})
        self.assertEqual(AuthThrottle.objects.count(), 0)

    def test_a_public_answer_is_escaped_and_never_a_person_by_itself(self):
        from people.models import Person

        form = self.public_form()
        people = Person.objects.count()
        self.client.post(public_url(form), self.public_data(form, text="<img src=x onerror=alert(1)>", **{f"{self.keys(form)[0]}_nombre": "<b>Vera</b>"}))
        self.assertEqual(Person.objects.count(), people)
        submission = Submission.objects.get()
        self.client.force_login(self.admin)
        for address in (url("submission", form.pk, submission.pk), url("submissions", form.pk), url("add_person", form.pk, submission.pk)):
            page = self.client.get(address)
            self.assertNotContains(page, "<img src=x")
            self.assertNotContains(page, "<b>Vera</b>")
            self.assertContains(page, "&lt;b&gt;Vera&lt;/b&gt;")
