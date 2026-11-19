"""Tasks, «Agregar a personas», the export, the retention and «Pendientes»."""

import csv
import io
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.utils import timezone

from care import services as care_services
from care.models import FollowUp
from espacios.models import Booking, Space
from formularios import queue, services
from formularios.models import Form, FormTask, FormVersion, Question, Submission
from people import flags
from people.models import ChurchAuditEvent, Person
from resources.models import ContentApproval

from .base import OTHER_ANSWER, FormFixture, url

BOOKING_TITLE = "Consejería de la familia Ruiz"


def task_url(form, submission, name="task"):
    return url(name, form.pk, submission.pk)


class TaskTests(FormFixture):
    def setUp(self):
        self.form = self.member_form(reader=True, creates_task=True)
        self.submission, _ = self.answer(self.form)
        self.task = self.submission.task

    def test_a_reader_notes_and_closes_and_the_administration_assigns(self):
        self.client.force_login(self.admin)
        page = self.client.get(task_url(self.form, self.submission, "task_assign"))
        self.assertContains(page, "Gala Guía")
        self.assertContains(page, "Ada Admin")
        self.assertNotContains(page, "Diego Hernández Cruz")  # reads nothing of this form: cannot be given its task
        self.assertNotContains(page, "Pablo Pastor")
        due = self.today + timedelta(days=2)
        self.client.post(task_url(self.form, self.submission, "task_assign"), {"owner": self.leader_user.pk, "due_on": due.isoformat()})
        self.task.refresh_from_db()
        self.assertEqual((self.task.owner, self.task.due_on), (self.leader_user, due))
        response = self.client.post(task_url(self.form, self.submission, "task_assign"), {"owner": self.member_user.pk, "due_on": ""})
        self.assertEqual(response.status_code, 200)  # not one of the choices
        with self.assertRaisesMessage(services.FormError, "no lee las respuestas"):
            services.assign_task(self.task, self.admin, self.member_user, None)
        with self.assertRaisesMessage(services.FormError, "no lee las respuestas"):
            services.assign_task(self.task, self.admin, self.admin_b, None)
        with self.assertRaisesMessage(services.FormError, "Esa fecha ya pasó"):
            services.assign_task(self.task, self.admin, self.leader_user, self.today - timedelta(days=1))

        self.client.force_login(self.leader_user)
        page = self.client.get(task_url(self.form, self.submission, "submission"))
        self.assertContains(page, "Dar seguimiento")
        self.assertContains(page, "No copies aquí lo que la persona respondió")
        self.client.post(task_url(self.form, self.submission), {"accion": "nota", "note": "Le llamé  el\nmartes <b>"})
        self.task.refresh_from_db()
        self.assertEqual(self.task.note, "")  # angle brackets are refused: plain text only
        self.client.post(task_url(self.form, self.submission), {"accion": "nota", "note": "Le llamé  el\nmartes"})
        self.client.post(task_url(self.form, self.submission), {"accion": "hecho"})
        self.task.refresh_from_db()
        self.assertEqual((self.task.note, self.task.status, self.task.closed_by), ("Le llamé el martes", "hecha", self.leader_user))
        self.assertIsNotNone(self.task.closed_at)
        self.client.post(task_url(self.form, self.submission), {"accion": "abrir"})
        self.task.refresh_from_db()
        self.assertEqual((self.task.status, self.task.closed_at, self.task.closed_by), ("abierta", None, None))
        self.assertEqual(self.client.post(task_url(self.form, self.submission), {"accion": "borrar"}).status_code, 404)
        for row in ChurchAuditEvent.objects.filter(action="formulario_tarea"):
            self.assertNotIn("martes", row.summary + str(row.metadata))  # the note is free text: never in the log

    def test_the_task_never_holds_the_answers(self):
        self.assertEqual(self.task.note, "")
        self.assertEqual(FormTask.objects.filter(note__contains=OTHER_ANSWER).count(), 0)
        fields = {field.name for field in FormTask._meta.get_fields()}
        self.assertEqual(fields, {"id", "submission", "form", "owner", "due_on", "status", "note", "created_at", "updated_at", "closed_at", "closed_by"})

    def test_a_stale_copy_does_not_undo_what_somebody_else_did(self):
        mine, theirs = FormTask.objects.get(pk=self.task.pk), FormTask.objects.get(pk=self.task.pk)
        services.set_task_status(theirs, self.leader_user, "hecha")
        services.set_task_note(mine, self.admin, "Nota escrita con una copia vieja")  # ``mine`` still says "abierta"
        self.task.refresh_from_db()
        self.assertEqual((self.task.status, self.task.note, self.task.closed_by), ("hecha", "Nota escrita con una copia vieja", self.leader_user))
        with self.assertRaisesMessage(services.FormError, "ya está hecho"):  # the stale copy thinks it is open; the row decides
            services.assign_task(mine, self.admin, self.leader_user, None)
        services.set_task_status(mine, self.admin, "hecha")  # twice is once
        self.assertEqual(ChurchAuditEvent.objects.filter(action="formulario_tarea", summary__startswith="Marcó").count(), 1)

    def test_a_form_without_the_rule_leaves_no_task(self):
        form = self.member_form(title="Sin pendientes")
        submission, _ = self.answer(form, person=self.leader)
        self.assertFalse(FormTask.objects.filter(submission=submission).exists())
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(url("submission", form.pk, submission.pk)), "Marcar como hecho")
        self.assertEqual(self.client.post(task_url(form, submission), {"accion": "hecho"}).status_code, 404)


class AddPersonTests(FormFixture):
    def setUp(self):
        self.form = self.public_form()
        self.submission, _ = self.answer(self.form, self.public_data(self.form, email="vera@demo.invalid", phone="55 9999 0000"))
        self.address = url("add_person", self.form.pk, self.submission.pk)

    def test_the_administration_adds_who_answered_and_nothing_else_happens(self):
        people = Person.objects.count()
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url("submission", self.form.pk, self.submission.pk)), "Agregar a personas de la iglesia")
        page = self.client.get(self.address)
        self.assertContains(page, "Registrarla como persona de la iglesia")
        self.assertContains(page, "no la suma a ningún grupo y no le envía nada")
        self.assertEqual(Person.objects.count(), people)  # looking adds nobody
        response = self.client.post(self.address, follow=True)
        person = Person.objects.get(email="vera@demo.invalid")
        self.assertContains(response, "Vera Visita está en las personas de la iglesia.")
        self.assertEqual((person.church, person.first_name, person.last_name, person.phone, person.user, person.created_from),
                         (self.church, "Vera", "Visita", "55 9999 0000", None, "manual"))
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.person, person)
        actions = set(ChurchAuditEvent.objects.filter(metadata__person_id=person.pk).values_list("action", flat=True))
        self.assertEqual(actions, {"persona_creada", "formulario_persona_agregada"})  # the people service wrote its own row
        self.assertFalse(person.group_memberships.exists())
        self.client.post(self.address)  # a second time adds nobody
        self.assertEqual(Person.objects.count(), people + 1)
        with self.assertRaisesMessage(services.FormError, "ya es de una persona"):
            services.add_to_people(self.submission, self.admin)
        self.assertNotContains(self.client.get(url("submission", self.form.pk, self.submission.pk)), "Agregar a personas de la iglesia</a>")

    def test_somebody_with_the_same_contact_is_offered_and_never_duplicated(self):
        Person.objects.filter(pk=self.no_account.pk).update(email="VERA@demo.invalid")
        same_phone = Person.objects.create(church=self.church, first_name="Fona", phone="+52 (55) 9999-0000")
        Person.objects.create(church=self.church_b, first_name="Otra iglesia", email="vera@demo.invalid", phone="5599990000")
        merged = Person.objects.create(church=self.church, first_name="Unida", phone="5599990000", merged_into=self.member)
        self.assertEqual(services.similar_people(self.submission), [same_phone, self.no_account])
        self.client.force_login(self.admin)
        page = self.client.get(self.address)
        self.assertContains(page, "Nora Sin Cuenta")
        self.assertNotContains(page, "Otra iglesia")
        people = Person.objects.count()
        response = self.client.post(self.address, follow=True)  # "alguien nuevo" with an e-mail somebody has: refused
        self.assertContains(response, "Otra persona de la iglesia ya tiene este correo.")
        for foreign in (Person.objects.get(first_name="Otra iglesia"), merged):
            self.assertEqual(self.client.post(self.address, {"persona": foreign.pk}).status_code, 404)
        self.submission.refresh_from_db()
        self.assertEqual((Person.objects.count(), self.submission.person), (people, None))
        self.client.post(self.address, {"persona": self.no_account.pk})
        self.submission.refresh_from_db()
        self.assertEqual((Person.objects.count(), self.submission.person), (people, self.no_account))
        self.no_account.refresh_from_db()
        self.assertEqual(self.no_account.first_name, "Nora")  # recognizing changes nothing of the record

    def test_only_with_the_people_module_and_only_public_answers_with_a_contact(self):
        self.client.force_login(self.admin)
        flags.set_enabled(self.church, "people", False)
        self.assertEqual(self.client.get(self.address).status_code, 404)
        self.assertEqual(self.client.post(self.address).status_code, 404)
        self.assertNotContains(self.client.get(url("submission", self.form.pk, self.submission.pk)), "Agregar a personas")
        with self.assertRaises(services.FormError):
            services.add_to_people(self.submission, self.admin)
        flags.set_enabled(self.church, "people", True)
        member_form = self.member_form(title="De la iglesia")
        theirs, _ = self.answer(member_form)
        self.assertEqual(self.client.post(url("add_person", member_form.pk, theirs.pk)).status_code, 404)
        self.assertEqual(Person.objects.filter(email="vera@demo.invalid").count(), 0)


class ExportTests(FormFixture):
    def test_the_administration_downloads_one_form_with_formulas_neutralised(self):
        form = self.public_form()
        self.answer(form, self.public_data(form, email="vera@demo.invalid", text="=HYPERLINK(\"http://malo.invalid\")",
                                            **{f"{self.keys(form)[0]}_nombre": "+52Vera", f"{self.keys(form)[0]}_apellidos": "@cmd"}))
        self.answer(form, self.public_data(form, email="otra@demo.invalid", text="-1+1"))
        other = self.member_form(title="Otro")
        self.answer(other)
        self.client.force_login(self.admin)
        response = self.client.post(url("export", form.pk))
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn(f"formulario-{form.pk}-respuestas.csv", response["Content-Disposition"])
        text = response.content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0], ["Recibida", "Versión", "Nombre", "Apellidos", "Correo", "Teléfono", "¿Qué necesitas?"])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][2:], ["'+52Vera", "'@cmd", "vera@demo.invalid", "", "'=HYPERLINK(\"http://malo.invalid\")"])
        self.assertEqual(rows[2][-1], "'-1+1")
        self.assertNotIn(OTHER_ANSWER, text)  # the other form's answers are not in it
        for row in rows[1:]:
            for cell in row:
                self.assertNotIn(cell[:1], ("=", "+", "-", "@"))
        audit = ChurchAuditEvent.objects.get(action="formulario_exportado")
        self.assertEqual((audit.actor, audit.metadata), (self.admin, {"form_id": form.pk, "value": 2}))
        self.assertNotIn("vera", audit.summary.lower())

    def test_a_reader_and_another_church_do_not_download(self):
        form = self.member_form(reader=True)
        self.answer(form)
        for user, status in ((self.leader_user, 403), (self.pastor_user, 404), (self.member_user, 404), (self.admin_b, 404)):
            self.client.force_login(user)
            self.assertEqual(self.client.post(url("export", form.pk)).status_code, status, user.email)
        self.client.force_login(self.leader_user)
        self.assertNotContains(self.client.get(url("submissions", form.pk)), "Descargar las respuestas")
        self.assertFalse(ChurchAuditEvent.objects.filter(action="formulario_exportado").exists())

    def test_columns_follow_the_versions(self):
        form = self.member_form()
        self.answer(form)
        services.delete_question(form, self.admin, form.questions.get(kind="corto").pk)
        services.save_question(form, self.admin, kind="fecha", label="¿Qué día?")
        form = self.publish(form)
        self.answer(form, {"version": form.current_version_id, self.keys(form)[0]: "No", self.keys(form)[2]: "2026-03-14"}, person=self.leader)
        header, rows = services.export_rows(form)
        self.assertEqual(header, ["Recibida", "Versión", "Persona", "¿Vas a venir?", "¿Qué traes?", "¿Algo más?", "¿Qué día?"])
        self.assertEqual([row[1:] for row in rows], [["1", "Diego Hernández Cruz", "Sí", "Postre", OTHER_ANSWER, ""],
                                                      ["2", "Gala Guía", "No", "", "", "14/03/2026"]])


class RetentionTests(FormFixture):
    def aged(self, days_after_close, **changes):
        """A form with one answer and its task, as they are ``days_after_close`` days after the form's last day:
        the form's days and the day the answer was told it would be kept until, moved back together."""
        form = self.member_form(creates_task=True, **changes)
        self.answer(form)
        last = self.today - timedelta(days=days_after_close)
        Form.objects.filter(pk=form.pk).update(opens_on=last - timedelta(days=3), closes_on=last)
        form.refresh_from_db()
        Submission.objects.filter(form=form).update(delete_after=last + timedelta(days=form.retention_days))
        return form

    def test_answers_are_deleted_when_the_form_s_own_time_ended(self):
        kept = self.aged(89, title="Un día antes del último")
        gone = self.aged(90, title="En el día que se le dijo")
        short = self.aged(31, title="Treinta", retention_days=30)
        long = self.aged(200, title="Un año", retention_days=365)
        open_form = self.member_form(title="Abierto")
        self.answer(open_form, person=self.leader)
        self.assertEqual(services.kept_until(gone.submissions.get()), self.today)  # "a más tardar" that day: it goes that day
        self.assertEqual(services.prune(apply=False), {"forms": 2, "submissions": 2})
        self.assertEqual(Submission.objects.count(), 5)  # the rehearsal writes nothing
        self.assertFalse(ChurchAuditEvent.objects.filter(action="formulario_respuestas_borradas").exists())
        self.assertEqual(services.prune(apply=True), {"forms": 2, "submissions": 2})
        self.assertEqual(set(Submission.objects.values_list("form_id", flat=True)), {kept.pk, long.pk, open_form.pk})
        self.assertEqual(FormTask.objects.count(), 2)  # the tasks went with their answers
        self.assertEqual((Form.objects.count(), FormVersion.objects.filter(form=gone).count(), Question.objects.filter(form=gone).count()), (5, 1, 3))
        rows = ChurchAuditEvent.objects.filter(action="formulario_respuestas_borradas")
        self.assertEqual(sorted((row.metadata["form_id"], row.metadata["value"], row.actor_label) for row in rows),
                         sorted([(gone.pk, 1, "Sistema"), (short.pk, 1, "Sistema")]))
        self.assertEqual(services.prune(apply=True), {"forms": 0, "submissions": 0})  # again: nothing left

    def test_a_form_closed_by_hand_counts_from_that_day(self):
        form = self.member_form(retention_days=30)
        self.answer(form)
        answer = form.submissions.get()
        self.assertEqual(answer.delete_after, form.closes_on + timedelta(days=30))
        services.close_form(form, self.admin)
        answer.refresh_from_db()
        self.assertEqual(answer.delete_after, self.today + timedelta(days=30))  # earlier than it was told: allowed
        self.assertEqual(services.prune(apply=True, now=timezone.now() + timedelta(days=29))["submissions"], 0)
        self.assertEqual(services.prune(apply=True, now=timezone.now() + timedelta(days=31))["submissions"], 1)

    def test_extending_or_reopening_a_form_never_keeps_an_answer_longer_than_it_was_told(self):
        """Review finding: ``closes_on`` stays editable, and the day of deletion followed it."""
        form = self.member_form(retention_days=30)
        self.client.force_login(self.member_user)
        page = self.client.get(url("fill", form.pk))
        told = form.closes_on + timedelta(days=30)
        self.assertEqual(page.context["delete_after"], told)
        self.assertContains(page, f'name="hasta" value="{told.isoformat()}"')
        self.client.post(url("fill", form.pk), {**self.member_data(form), "hasta": told.isoformat()})
        first = Submission.objects.get()
        self.assertEqual(first.delete_after, told)
        values = {"title": form.title, "purpose": form.purpose, "audience": form.audience, "retention_days": 30, "opens_on": form.opens_on,
                  "creates_task": False, "task_owner": None, "task_due_days": 7}
        form = services.update_form(form, self.admin, closes_on=form.closes_on + timedelta(days=300), **values)  # extended
        first.refresh_from_db()
        self.assertEqual(first.delete_after, told)  # what its person read, not what the form says now
        second, _ = self.answer(form, person=self.leader)
        self.assertEqual(second.delete_after, form.closes_on + timedelta(days=30))  # the extension counts for new answers
        # a page drawn before the extension and sent after it keeps the day that page printed
        third, _ = self.answer(form, person=self.asked, shown_until=told)
        self.assertEqual(third.delete_after, told)
        fourth, _ = self.answer(form, person=self.coord_person, shown_until=told + timedelta(days=5000))  # a forged later day: ignored
        self.assertEqual(fourth.delete_after, form.closes_on + timedelta(days=30))
        # bringing the last day or the retention in brings every answer in; pushing them out again brings none back
        form = services.update_form(form, self.admin, closes_on=self.today + timedelta(days=2), **values)
        early = self.today + timedelta(days=32)
        self.assertEqual(set(Submission.objects.values_list("delete_after", flat=True)), {early})
        form = services.update_form(form, self.admin, closes_on=self.today + timedelta(days=200), **values)
        self.assertEqual(set(Submission.objects.values_list("delete_after", flat=True)), {early})
        # a finished form that is reopened: its old answers go on their day
        Form.objects.filter(pk=form.pk).update(opens_on=self.today - timedelta(days=60), closes_on=self.today - timedelta(days=40))
        Submission.objects.update(delete_after=self.today - timedelta(days=10))
        form.refresh_from_db()
        services.update_form(form, self.admin, closes_on=self.today + timedelta(days=30), **values)  # reopened
        self.assertEqual(services.prune(apply=True)["submissions"], 4)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url("detail", form.pk)), "las que ya llegaron conservan su fecha")

    def test_it_deletes_whatever_the_module_flags_say_and_only_counts_out_loud(self):
        gone = self.aged(120)
        flags.set_enabled(self.church, "community", False)
        out = StringIO()
        call_command("prune_form_submissions", stdout=out)
        self.assertIn("ensayo", out.getvalue())
        self.assertEqual(Submission.objects.count(), 1)
        out = StringIO()
        call_command("prune_form_submissions", "--apply", stdout=out)
        self.assertEqual(out.getvalue().strip(), "Se hizo: formularios con respuestas vencidas: 1; respuestas borradas: 1")
        self.assertNotIn(gone.title, out.getvalue())
        self.assertEqual(Submission.objects.count(), 0)

    def test_the_scheduler_runs_it(self):
        from core import scheduler
        from core.management.commands.run_scheduled_jobs import Command

        self.aged(120)
        self.assertIn(scheduler.FORM_ANSWERS, scheduler.JOBS)
        ok, line = Command(stdout=StringIO()).form_answers_summary()
        self.assertEqual((ok, line), (True, "formularios: 1 respuestas vencidas borradas"))
        self.assertEqual(Submission.objects.count(), 0)
        self.assertEqual(scheduler.problem(scheduler.FORM_ANSWERS), "")


class QueueTests(FormFixture):
    """For each source: who may see it gets a row and a count, and who may not gets neither."""

    def rows(self, user, person):
        from formularios import access

        is_admin = access.is_admin(user, self.church)
        found = queue.rows(user, self.church, person, is_church_admin=is_admin)
        total = queue.count(user, self.church, person, is_church_admin=is_admin)
        self.assertEqual(total, sum(row.count for row in found), user.email)  # the badge never says more than the list
        return found

    def kinds(self, user, person):
        return sorted(row.kind for row in self.rows(user, person))

    def everybody(self):
        return {
            "admin": (self.admin, self.admin_person), "leader": (self.leader_user, self.leader), "member": (self.member_user, self.member),
            "pastor": (self.pastor_user, self.pastor_person), "asked": (self.asked_user, self.asked),
        }

    def assert_only(self, kind, *names):
        """``kind`` is in the list of each of ``names`` and of nobody else."""
        for name, (user, person) in self.everybody().items():
            self.assertEqual(kind in self.kinds(user, person), name in names, f"{kind}: {name}")

    def setUp(self):
        # The fixture leaves one request to join the leader's group: take it away so each test adds its own source.
        type(self.pending).objects.filter(pk=self.pending.pk).update(status="declinada")
        for user, person in self.everybody().values():
            self.assertEqual(self.rows(user, person), [], user.email)

    def test_a_follow_up_of_a_person_is_its_owner_s(self):
        follow_up = care_services.create(self.church, self.admin, person=self.member, reason="visita", owner=self.leader,
                                         due_on=self.today + timedelta(days=1))
        self.assert_only("Acompañar", "leader")
        row = self.rows(self.leader_user, self.leader)[0]
        self.assertEqual((row.url, row.due_on, row.title), (f"/mi-iglesia/acompanar/{follow_up.pk}/", follow_up.due_on, "Buscar a una persona de la iglesia"))
        self.client.force_login(self.leader_user)
        page = self.client.get(url("queue"))
        self.assertContains(page, "Buscar a una persona de la iglesia")
        for private in ("Diego", "Vino por primera vez", "visita"):
            self.assertNotContains(page, private)  # who it is about and why stay on the follow-up's own screen
        care_services.set_status(follow_up, self.leader_user, "atendido")
        self.assert_only("Acompañar")

    def test_follow_ups_without_an_owner_are_the_administration_s(self):
        care_services.create(self.church, self.admin, person=self.member, reason="visita")
        care_services.create(self.church_b, self.admin_b, person=self.member_b, reason="visita")
        self.assert_only("Acompañar", "admin")
        row = self.rows(self.admin, self.admin_person)[0]
        self.assertEqual((row.title, row.count, row.url), ("1 seguimiento sin responsable", 1, "/mi-iglesia/acompanar/todos/"))
        FollowUp.objects.create(church=self.church, person=self.admin_person, reason="visita")  # about the administrator: not counted
        self.assertEqual(self.rows(self.admin, self.admin_person)[0].count, 1)

    def test_requests_to_join_a_group_are_its_guide_s(self):
        type(self.pending).objects.filter(pk=self.pending.pk).update(status="pendiente")
        self.assert_only("Grupos", "leader")
        row = self.rows(self.leader_user, self.leader)[0]
        self.assertEqual((row.title, row.url), ("1 solicitud para unirse a Jóvenes Roca", f"/mi-iglesia/grupos/{self.group.pk}/personas/"))
        self.client.force_login(self.leader_user)
        self.assertNotContains(self.client.get(url("queue")), "Pía")
        type(self.group).objects.filter(pk=self.group.pk).update(status="archivado")
        self.assert_only("Grupos")

    def test_requests_of_a_space_are_the_administration_s(self):
        start = timezone.now() + timedelta(days=2)
        space = Space.objects.create(church=self.church, name="Salón 1")
        Booking.objects.create(space=space, title=BOOKING_TITLE, starts_at=start, ends_at=start + timedelta(hours=1), requested_by=self.leader)
        other = Space.objects.create(church=self.church_b, name="Salón B")
        Booking.objects.create(space=other, title="De la B", starts_at=start, ends_at=start + timedelta(hours=1), requested_by=self.member_b)
        self.assert_only("Espacios", "admin")
        row = self.rows(self.admin, self.admin_person)[0]
        self.assertEqual((row.title, row.url), ("1 solicitud de reserva por decidir", "/mi-iglesia/espacios/solicitudes/"))
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(url("queue")), BOOKING_TITLE)  # what a booking is for is not copied here

    def test_content_to_approve_is_the_pastor_s(self):
        ContentApproval.objects.create(church=self.church, kind=ContentApproval.Kind.values[0], object_id=1, state="pendiente")
        ContentApproval.objects.create(church=self.church_b, kind=ContentApproval.Kind.values[0], object_id=2, state="pendiente")
        self.assert_only("Aprobación pastoral", "pastor")
        self.assertEqual(self.rows(self.pastor_user, self.pastor_person)[0].title, "1 contenido por aprobar")

    def test_a_task_of_a_form_is_its_owner_s_while_they_read_the_form(self):
        form = self.member_form(reader=True, creates_task=True)
        services.update_form(form, self.admin, title=form.title, purpose=form.purpose, audience=form.audience, retention_days=90,
                             opens_on=form.opens_on, closes_on=form.closes_on, creates_task=True, task_owner=self.leader_user, task_due_days=7)
        submission, _ = self.answer(form)
        self.assert_only("Formularios", "leader")
        row = self.rows(self.leader_user, self.leader)[0]
        self.assertEqual((row.title, row.url, row.due_on),
                         ("Dar seguimiento a una respuesta de «Convivio de aniversario»", url("submission", form.pk, submission.pk), submission.task.due_on))
        self.client.force_login(self.leader_user)
        page = self.client.get(url("queue"))
        self.assertContains(page, "Dar seguimiento a una respuesta de «Convivio de aniversario»")
        for private in (OTHER_ANSWER, "Diego", "Postre"):
            self.assertNotContains(page, private)  # no answer and nobody's name in the row
        # the role that let the owner read the form ends: the row goes with it, and the task waits for the administration
        type(self.group).objects.filter(pk=self.group.pk).update(status="archivado")
        self.assert_only("Formularios", "admin")
        admin_row = self.rows(self.admin, self.admin_person)[0]
        self.assertEqual((admin_row.title, admin_row.url), ("1 pendiente de formulario sin responsable", url("loose_tasks")))
        self.client.force_login(self.admin)
        page = self.client.get(url("loose_tasks"))
        self.assertContains(page, "Quien lo tenía ya no lee este formulario.")
        self.assertNotContains(page, OTHER_ANSWER)
        type(self.group).objects.filter(pk=self.group.pk).update(status="activo")
        services.set_task_status(submission.task, self.leader_user, "hecha")
        self.assert_only("Formularios")

    def test_overdue_first_and_nothing_of_another_church(self):
        form = self.member_form(creates_task=True)
        late, _ = self.answer(form)
        soon, _ = self.answer(form, person=self.leader)
        FormTask.objects.filter(pk=late.task.pk).update(owner=self.admin, due_on=self.today - timedelta(days=1))
        FormTask.objects.filter(pk=soon.task.pk).update(owner=self.admin, due_on=self.today + timedelta(days=3))
        care_services.create(self.church, self.admin, person=self.member, reason="visita")
        rows = self.rows(self.admin, self.admin_person)
        self.assertEqual([(row.overdue, row.due_on is None) for row in rows], [(True, False), (False, False), (False, True)])
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url("queue")), "Ya pasó la fecha.")
        # the administration of church B, signed in to its own church, sees none of it
        self.assertEqual(queue.rows(self.admin_b, self.church_b, self.admin_b_person, is_church_admin=True), [])
        self.assertEqual(queue.count(self.admin_b, self.church_b, self.admin_b_person, is_church_admin=True), 0)
        self.client.force_login(self.admin_b)
        self.assertContains(self.client.get(url("queue")), "No hay nada esperando por ti.")

    # ---- Servir: swaps of dates (wave 10) ----------------------------------------------------------
    def swap(self, needs_approval=False):
        """``member`` asks ``asked`` to take a date of a team ``leader`` coordinates."""
        from serving import rotas
        from serving import services as serving

        team = serving.save_team(self.church, self.admin, name="Equipo de Sonido")
        serving.set_member(team, self.leader, self.admin, role="coordina")
        serving.set_member(team, self.member, self.admin)
        serving.set_member(team, self.asked, self.admin)
        if needs_approval:
            rotas.save_settings(team, self.leader_user, swap_needs_approval=True, reminder_days=0)
        day = serving.save_date(team, self.admin, date=self.today + timedelta(days=7), what="Domingo de prueba")
        assignment = serving.assign(day, self.member, self.leader_user)
        return team, day, rotas.request_swap(assignment, self.asked, self.member_user)

    def test_a_swap_somebody_asked_is_a_row_of_who_was_asked_and_of_nobody_else(self):
        from serving import rotas

        team, day, swap = self.swap()
        self.assert_only("Servir", "asked")
        row = self.rows(self.asked_user, self.asked)[0]
        self.assertEqual((row.title, row.url, row.due_on, row.count), ("Te pidieron un cambio de fecha", "/mi-iglesia/servir/#cambios", day.date, 1))
        self.client.force_login(self.asked_user)
        page = self.client.get(url("queue"))
        self.assertContains(page, "Te pidieron un cambio de fecha")
        for private in (self.member.first_name, self.member.last_name, team.name, "Domingo de prueba"):
            self.assertNotContains(page, private)  # who asked, in which team and what the date is stay in «Servir»
        self.assertContains(self.client.get(row.url), "Domingo de prueba")  # the screen the row leads to has it
        rotas.answer_swap(swap, False, self.asked_user)
        self.assert_only("Servir")

    def test_a_swap_that_waits_for_the_approval_is_of_who_coordinates_the_team_and_of_the_administration(self):
        from serving import rotas
        from serving.models import SwapRequest

        team, _day, swap = self.swap(needs_approval=True)
        self.assert_only("Servir", "asked")  # until the teammate answers there is nothing to approve
        swap = rotas.answer_swap(swap, True, self.asked_user)
        self.assertEqual(swap.status, SwapRequest.Status.TO_APPROVE)
        self.assert_only("Servir", "leader", "admin")
        row = self.rows(self.leader_user, self.leader)[0]
        self.assertEqual((row.title, row.url, row.count),
                         ("1 cambio de fecha espera tu visto bueno en Equipo de Sonido", f"/mi-iglesia/servir/equipos/{team.pk}/", 1))
        self.client.force_login(self.leader_user)
        page = self.client.get(url("queue"))
        self.assertContains(page, "1 cambio de fecha espera tu visto bueno en Equipo de Sonido")
        for private in (self.member.last_name, self.asked.last_name, "Domingo de prueba"):
            self.assertNotContains(page, private)
        self.assertEqual(self.client.get(row.url).status_code, 200)  # the same permission as the screen it leads to
        # the administration of church B sees nothing of it
        self.assertEqual(queue.rows(self.admin_b, self.church_b, self.admin_b_person, is_church_admin=True), [])
        self.assertEqual(queue.count(self.admin_b, self.church_b, self.admin_b_person, is_church_admin=True), 0)
        rotas.decide_swap(swap, True, self.leader_user)
        self.assert_only("Servir")

    # ---- Oración: never here ----------------------------------------------------------------------
    def test_a_prayer_request_never_produces_a_row_or_a_count(self):
        """Its builder's decision (docs/v2/wave10/oracion.md): nothing of a request, not even that one exists, is
        shown outside its readers' own screens. The reader has their own door in the menu; «Pendientes» has none."""
        from oracion import access as prayer_access
        from oracion import services as prayers
        from oracion.models import PrayerRequest

        body = "Texto inventado de una petición que nadie más debe leer"
        prayers.switch_on(self.church, self.pastor_user, accepted=True, token=prayers.readers_token(prayer_access.readers(self.church)),
                          emergency_line="", retention_days=prayers.policy.PRAYER_RETENTION_DAYS)
        readers = prayer_access.readers(self.church, apart_from=self.member_user)
        prayers.create(self.church, self.member, body=body, token=prayers.readers_token(readers, prayers.terms(self.church)))
        self.assertEqual(PrayerRequest.objects.filter(church=self.church, read_at__isnull=True).count(), 1)
        self.assertEqual(prayers.unread_count(self.pastor_user, self.church), 1)  # it does wait for the pastor, on their screen
        for name, (user, person) in self.everybody().items():
            self.assertEqual(self.rows(user, person), [], name)  # no row, and (self.rows) a count of zero
            self.assertEqual(queue.count(user, self.church, person, is_church_admin=name == "admin"), 0, name)
        self.client.force_login(self.pastor_user)
        page = self.client.get(url("queue"))
        self.assertContains(page, "No hay nada esperando por ti.")
        self.assertNotContains(page, ">Pendientes</span>")  # nothing sends the pastor to this list for it
        self.assertNotContains(page, body)
        self.client.force_login(self.admin)
        page = self.client.get(url("queue"))
        self.assertContains(page, "No hay nada esperando por ti.")
        for private in (body, "Peticiones de oración", "sin leer"):
            self.assertNotContains(page, private)  # the administration is not a reader: neither the text nor a number

    def test_the_shell_shows_the_same_number_and_only_to_who_has_something(self):
        form = self.member_form(reader=True, creates_task=True)
        submission, _ = self.answer(form)
        FormTask.objects.filter(pk=submission.task.pk).update(owner=self.leader_user)
        care_services.create(self.church, self.admin, person=self.member, reason="visita", owner=self.leader)
        self.client.force_login(self.leader_user)
        page = self.client.get(url("queue"))
        self.assertContains(page, '<span class="badge">2<span class="visually-hidden"> pendientes</span></span>')
        self.assertContains(page, "Respuestas de formularios")
        self.client.force_login(self.member_user)
        page = self.client.get(url("queue"))
        self.assertNotContains(page, ">Pendientes</span>")  # the page answers, but nothing sends them there
        self.assertContains(page, "No hay nada esperando por ti.")
        self.assertContains(page, ">Formularios</span>")  # a form of the church is open for them
        self.assertNotContains(page, "Formularios de la iglesia")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url("queue")), "Formularios de la iglesia")
