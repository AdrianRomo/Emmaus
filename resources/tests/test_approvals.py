"""Issue #99 on synthetic data: the administration prepares, a pastor of the same church approves, and
only that approval publishes a devotional or a reading plan. What a person read stays theirs."""

import importlib
import re
from datetime import timedelta

from django.apps import apps as django_apps
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Role
from core.services import assign_role
from events.tests.base import make_user
from groups.tests.base import CommunityFixture
from people import flags
from people.audit import ACTION_LABELS
from people.models import ChurchAuditEvent
from resources import approvals, plans, services
from resources.models import ContentApproval, Devotional, DevotionalRead, ReadingPlan, ReadingPlanDayRead, ReadingPlanProgress
from resources.tests.approving import approve, church_pastor

QUEUE = reverse("resources:approval_queue")
HOME = reverse("community:home")
BODY = "Permanezcan en mí.\n\nLa rama no da fruto por sí misma."
PROGRESS_TABLES = (DevotionalRead._meta.db_table, ReadingPlanProgress._meta.db_table, ReadingPlanDayRead._meta.db_table)
NO_PASTOR = "no hay ninguna cuenta activa con el rol de pastor"


def url(name, kind, obj):
    return reverse(f"resources:approval_{name}", args=[kind, obj.pk])


def main(response) -> str:
    html = response.content.decode()
    return html[html.index("<main"):html.index("</main>")]


class ApprovalFixture(CommunityFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.today = timezone.localdate()
        cls.church_pastor = church_pastor(cls.church)
        cls.pastor_b = church_pastor(cls.church_b)
        cls.mentor = make_user("mentora@prueba.mx", "Mara", "Mentora")
        assign_role(cls.mentor, cls.church, Role.MENTOR)
        cls._person(cls.church, cls.mentor)
        # ``cls.pastor`` (the camp fixture) is "Pastor" of an event, with no church role: a person of the church here.
        cls._person(cls.church, cls.pastor)

    def prepared(self, day=None, *, church=None, actor=None, **changes) -> Devotional:
        """A devotional the administration prepared and asked to publish."""
        values = {"title": "La vid verdadera", "passage": "Juan 15:1-8", "body": BODY, "author_name": "Ana López", "publish": True}
        return services.save_devotional(church or self.church, actor or self.admin, date=day or self.today, **{**values, **changes})

    def plan(self, *, church=None, actor=None, days=2, ask=True, title="Dos días en Juan") -> ReadingPlan:
        church, actor = church or self.church, actor or self.admin
        plan = plans.save_plan(church, actor, title=title, description="Lecturas cortas.", author_line="Equipo pastoral")
        for number in range(1, days + 1):
            plans.save_day(plan, actor, reference=f"Juan 15:{number}", title=f"Día número {number}", reflection="Una rama unida a la vid.")
        return plans.request_publication(plan, actor) if ask else plan

    def row(self, kind, obj) -> ContentApproval:
        return ContentApproval.objects.get(kind=kind, object_id=obj.pk)

    def revision(self, kind, obj) -> str:
        obj.refresh_from_db()
        return approvals.kind_of(kind).revision(obj)

    def decide(self, kind, obj, **data):
        return self.client.post(url("approve", kind, obj), {"revision": self.revision(kind, obj), "permitted_use": "propia", **data}, follow=True)


class PastorApprovesTests(ApprovalFixture):
    def test_a_pastor_reads_the_preview_approves_and_that_publishes_the_devotional(self):
        self.client.force_login(self.admin)
        data = {"date": self.today.isoformat(), "title": "Fruto <b>que</b> permanece", "passage": "Juan 15:16",
                "body": "Primera línea\n\n<script>alert('x')</script>", "author_name": "Ana López", "publish": "on"}
        self.assertRedirects(self.client.post(reverse("resources:devotional_create"), data), reverse("resources:devotional_admin"))
        devotional = Devotional.objects.get()
        self.assertEqual(devotional.status, "borrador")
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(reverse("resources:devotional_detail", args=[devotional.pk])).status_code, 404)
        self.assertContains(self.client.get(reverse("resources:devotional_today")), "Hoy no hay devocional.")

        self.client.force_login(self.church_pastor)
        page = self.client.get(HOME)
        self.assertContains(page, QUEUE)
        self.assertContains(page, '<span class="badge">1<span class="visually-hidden"> contenido por aprobar</span></span>')
        page = self.client.get(QUEUE)
        self.assertContains(page, "<h1", count=1)
        self.assertContains(page, "espera tu aprobación")
        self.assertContains(page, "Fruto &lt;b&gt;que&lt;/b&gt; permanece")
        self.assertNotContains(page, "<b>que</b>")
        self.assertContains(page, "Por aprobar")
        self.assertContains(page, "Todo lo que está publicado tiene la aprobación de un pastor.")

        page = self.client.get(url("review", "devocional", devotional))
        self.assertContains(page, "<h1", count=1)
        self.assertContains(page, "<p>Primera línea</p>")
        self.assertContains(page, "&lt;script&gt;")
        self.assertNotContains(page, "<script>alert")
        self.assertContains(page, "Juan 15:16. Solo la cita")
        self.assertContains(page, f'name="revision" value="{approvals.DEVOTIONAL.revision(devotional)}"')
        for label in ("Obra propia de la iglesia", "Con permiso del autor", "Dominio público o licencia abierta"):
            self.assertContains(page, label)
        self.assertContains(page, "Aprobar y publicar</button>")
        self.assertContains(page, "Devolver sin aprobar</button>")
        self.assertEqual(page.content.decode().count("btn--primary"), 1)
        self.assertNotContains(page, "Ir a editarlo")  # the pastor does not edit: the administration does

        # Approving needs the permitted use.
        response = self.client.post(url("approve", "devocional", devotional), {"revision": self.revision("devocional", devotional)})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Elige con qué permiso se publica.")
        self.assertContains(response, "Error: ")
        self.assertEqual(Devotional.objects.get().status, "borrador")

        response = self.decide("devocional", devotional, permitted_use="permiso", source_note="  Carta <i>firmada</i>   del autor ")
        self.assertRedirects(response, QUEUE)
        self.assertContains(response, "Aprobaste «Fruto &lt;b&gt;que&lt;/b&gt; permanece». Las personas de tu iglesia ya lo pueden leer.")
        self.assertContains(response, "No hay nada esperando tu aprobación.")
        devotional.refresh_from_db()
        self.assertEqual(devotional.status, "publicado")
        row = self.row("devocional", devotional)
        self.assertEqual(
            (row.church, row.state, row.approved_by, row.permitted_use, row.source_note, row.approved_digest, row.requested_by),
            (self.church, "aprobado", self.church_pastor, "permiso", "Carta <i>firmada</i> del autor",
             approvals.DEVOTIONAL.digest(devotional), self.admin),
        )
        self.assertIsNotNone(row.approved_at)
        audit = ChurchAuditEvent.objects.get(action="contenido_aprobado")
        self.assertEqual(
            (audit.church, audit.actor, audit.object_type, audit.object_id, audit.metadata, audit.summary),
            (self.church, self.church_pastor, "Devotional", str(devotional.pk), {"permitted_use": "permiso"},
             "Aprobó y publicó un devocional (con permiso del autor)"),
        )
        self.assertEqual(len(mail.outbox), 0)

        # The record, for the staff: who, when, which use, the note (escaped). The member reads the devotional and no record.
        for user in (self.church_pastor, self.admin):
            self.client.force_login(user)
            page = self.client.get(url("review", "devocional", devotional))
            self.assertContains(page, "Aprobado y publicado.")
            self.assertContains(page, "Paula Pastora")
            self.assertContains(page, "Con permiso del autor")
            self.assertContains(page, "Carta &lt;i&gt;firmada&lt;/i&gt; del autor")
            self.assertNotIn("Aprobar y publicar", main(page))
        self.client.force_login(self.member_user)
        page = self.client.get(reverse("resources:devotional_today"))
        self.assertContains(page, "<p>Primera línea</p>")
        self.assertNotContains(page, "Carta")
        self.assertNotContains(page, "Con permiso del autor")
        self.assertEqual(self.client.get(url("review", "devocional", devotional)).status_code, 403)
        self.assertEqual(self.client.get(QUEUE).status_code, 403)

    def test_a_pastor_approves_a_plan_reading_every_day_and_a_plan_without_days_is_refused(self):
        plan = self.plan()
        self.client.force_login(self.member_user)
        self.assertNotContains(self.client.get(reverse("resources:plan_list")), "Dos días en Juan")
        self.assertEqual(self.client.get(reverse("resources:plan_detail", args=[plan.pk])).status_code, 404)
        self.client.force_login(self.church_pastor)
        page = self.client.get(url("review", "plan", plan))
        for text in ("Dos días en Juan", "Día 1: Día número 1", "Día 2: Día número 2", "Lectura: Juan 15:2.", "Una rama unida a la vid."):
            self.assertContains(page, text)
        self.assertRedirects(self.decide("plan", plan, permitted_use="abierta"), QUEUE)
        plan.refresh_from_db()
        self.assertEqual((plan.status, plan.rights_confirmed_by), ("publicado", self.church_pastor))
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(reverse("resources:plan_list")), "Dos días en Juan")

        empty = self.plan(days=1, title="Plan que se quedó sin días")
        plans.delete_day(empty.days.get(), self.admin)
        self.client.force_login(self.church_pastor)
        page = self.client.get(url("review", "plan", empty))
        self.assertContains(page, "Este plan todavía no tiene días.")
        self.assertNotIn("Aprobar y publicar", main(page))
        response = self.decide("plan", empty)
        self.assertContains(response, "Este plan todavía no tiene días.")
        empty.refresh_from_db()
        self.assertEqual(empty.status, "borrador")

    def test_returning_needs_a_reason_that_only_the_staff_reads_and_nothing_is_mailed(self):
        devotional = self.prepared()
        self.client.force_login(self.church_pastor)
        response = self.client.post(url("return", "devocional", devotional), {"reason": "   "})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Escribe por qué lo devuelves")
        self.assertEqual(self.row("devocional", devotional).state, "pendiente")
        self.assertContains(self.client.post(url("return", "devocional", devotional), {"reason": "a" * 301}), "hasta 300 caracteres")

        response = self.client.post(url("return", "devocional", devotional), {"reason": "Falta decir <b>de quién</b> es   la cita."}, follow=True)
        self.assertRedirects(response, QUEUE)
        self.assertContains(response, "Devolviste «La vid verdadera». No se publicó")
        row = self.row("devocional", devotional)
        self.assertEqual((row.state, row.returned_by, row.return_reason), ("devuelto", self.church_pastor, "Falta decir <b>de quién</b> es la cita."))
        devotional.refresh_from_db()
        self.assertEqual(devotional.status, "borrador")
        audit = ChurchAuditEvent.objects.get(action="contenido_devuelto")
        self.assertEqual((audit.actor, audit.object_type, audit.object_id, audit.metadata), (self.church_pastor, "Devotional", str(devotional.pk), {}))
        self.assertEqual(audit.summary, "Devolvió un devocional sin aprobarlo")
        self.assertEqual(len(mail.outbox), 0)
        # Returned is decided: neither decision can be taken again on it.
        self.assertContains(self.decide("devocional", devotional), "Esto ya no espera aprobación")
        self.assertContains(self.client.post(url("return", "devocional", devotional), {"reason": "Otra vez"}, follow=True), "Esto ya no espera aprobación")
        self.assertEqual(Devotional.objects.get().status, "borrador")

        # The administration reads the reason, escaped, where it works; the member reads nothing of it.
        self.client.force_login(self.admin)
        for address in (QUEUE, reverse("resources:devotional_edit", args=[devotional.pk]), url("review", "devocional", devotional)):
            page = self.client.get(address)
            self.assertContains(page, "Falta decir &lt;b&gt;de quién&lt;/b&gt; es la cita.")
            self.assertNotContains(page, "<b>de quién</b>")
        self.assertContains(self.client.get(reverse("resources:devotional_admin")), "Un pastor lo devolvió sin aprobarlo")
        self.client.force_login(self.member_user)
        self.assertNotContains(self.client.get(HOME), "Falta decir")
        self.assertEqual(self.client.get(reverse("resources:devotional_detail", args=[devotional.pk])).status_code, 404)

        # Corrected and asked again: the old reason is gone and it waits for a pastor again.
        self.prepared(passage="Juan 15:1-8 (cita de la iglesia)", devotional=devotional)
        row.refresh_from_db()
        self.assertEqual((row.state, row.return_reason, row.returned_by), ("pendiente", "", None))

    def test_a_version_that_changed_after_the_preview_is_refused(self):
        devotional = self.prepared()
        self.client.force_login(self.church_pastor)
        page = self.client.get(url("review", "devocional", devotional))
        seen = page.context["revision"]
        self.prepared(body="Otro texto que el pastor no leyó.", devotional=devotional)  # the administration corrects it meanwhile
        for revision in (seen, "", "0" * 64):
            response = self.client.post(url("approve", "devocional", devotional), {"revision": revision, "permitted_use": "propia"}, follow=True)
            self.assertRedirects(response, url("review", "devocional", devotional))
            self.assertContains(response, "El contenido cambió mientras lo revisabas. No se aprobó nada")
            self.assertContains(response, "Otro texto que el pastor no leyó.")
        devotional.refresh_from_db()
        self.assertEqual((devotional.status, self.row("devocional", devotional).state), ("borrador", "pendiente"))
        self.assertFalse(ChurchAuditEvent.objects.filter(action="contenido_aprobado").exists())
        with self.assertRaises(approvals.StaleContent):
            approvals.approve(self.church, "devocional", devotional.pk, self.church_pastor, revision=seen, permitted_use="propia")
        # The preview of what is there now can be approved.
        self.assertRedirects(self.decide("devocional", devotional), QUEUE)
        self.assertEqual(self.row("devocional", devotional).approved_digest, approvals.DEVOTIONAL.digest(Devotional.objects.get()))

    def test_two_decisions_on_the_same_row_the_second_one_finds_it_decided(self):
        devotional = self.prepared()
        revision = self.revision("devocional", devotional)
        approvals.approve(self.church, "devocional", devotional.pk, self.church_pastor, revision=revision, permitted_use="propia")
        # A second pastor's tab, opened before: the same form, sent afterwards.
        second = make_user("otro-pastor@prueba.mx", "Pedro", "Pastor Dos")
        assign_role(second, self.church, Role.PASTOR)
        self._person(self.church, second)
        with self.assertRaises(approvals.ApprovalError):
            approvals.approve(self.church, "devocional", devotional.pk, second, revision=revision, permitted_use="permiso")
        with self.assertRaises(approvals.ApprovalError):
            approvals.send_back(self.church, "devocional", devotional.pk, second, reason="No estoy de acuerdo")
        row = self.row("devocional", devotional)
        self.assertEqual((row.state, row.approved_by, row.permitted_use, row.return_reason), ("aprobado", self.church_pastor, "propia", ""))
        self.assertEqual(Devotional.objects.get().status, "publicado")
        self.assertEqual(ChurchAuditEvent.objects.filter(action__in=("contenido_aprobado", "contenido_devuelto")).count(), 1)
        # The administration withdrew the request while the pastor read: there is nothing to approve.
        other = self.prepared(self.today + timedelta(days=1), title="El de mañana")
        seen = self.revision("devocional", other)
        self.prepared(self.today + timedelta(days=1), title="El de mañana", publish=False, devotional=other)
        self.assertFalse(ContentApproval.objects.filter(kind="devocional", object_id=other.pk).exists())
        with self.assertRaises(approvals.ApprovalError):
            approvals.approve(self.church, "devocional", other.pk, self.church_pastor, revision=seen, permitted_use="propia")
        self.client.force_login(self.church_pastor)
        self.assertEqual(self.client.post(url("approve", "devocional", other), {"revision": seen, "permitted_use": "propia"}).status_code, 404)
        other.refresh_from_db()
        self.assertEqual(other.status, "borrador")


class WhoDecidesTests(ApprovalFixture):
    def test_only_a_pastor_of_the_same_church_approves_or_returns_even_by_guessing_the_post(self):
        devotional, plan = self.prepared(), self.plan()
        targets = [("devocional", devotional), ("plan", plan)]

        def posts(expected, who):
            for kind, obj in targets:
                data = {"revision": self.revision(kind, obj), "permitted_use": "propia", "reason": "Porque sí"}
                for name in ("approve", "return"):
                    self.assertEqual(self.client.post(url(name, kind, obj), data).status_code, expected, f"{who} {name} {kind}")

        # The administration prepared it: it reads the list and the preview, and cannot decide.
        self.client.force_login(self.admin)
        page = self.client.get(QUEUE)
        self.assertContains(page, "solo un pastor aprueba y publica")
        self.assertContains(page, "La vid verdadera")
        page = self.client.get(url("review", "devocional", devotional))
        self.assertContains(page, "Solo un pastor de tu iglesia puede aprobarlo.")
        self.assertContains(page, "Ir a editarlo")
        self.assertNotIn("</form>", main(page))
        posts(403, "administration")
        # Nobody else opens anything: a member, a guide, a mentor, and the "Pastor" of an event (an event role is not a church role).
        for user in (self.member_user, self.leader_user, self.mentor, self.pastor):
            self.client.force_login(user)
            for address in (QUEUE, url("review", "devocional", devotional), url("review", "plan", plan)):
                self.assertEqual(self.client.get(address).status_code, 403, f"{user.email} {address}")
            posts(403, user.email)
            self.assertNotContains(self.client.get(HOME), QUEUE)
        # The pastor and the administration of another church: these objects do not exist for them.
        for user, refused in ((self.pastor_b, 404), (self.admin_b, 403)):  # the administration of B is no pastor either
            self.client.force_login(user)
            self.assertNotContains(self.client.get(QUEUE), "La vid verdadera")
            self.assertEqual(self.client.get(url("review", "devocional", devotional)).status_code, 404)
            posts(refused, user.email)
        # Signed out: to the sign-in.
        self.client.logout()
        for address in (QUEUE, url("review", "devocional", devotional)):
            self.assertEqual(self.client.get(address).status_code, 302)
        posts(302, "signed out")

        devotional.refresh_from_db()
        plan.refresh_from_db()
        self.assertEqual((devotional.status, plan.status), ("borrador", "borrador"))
        self.assertEqual(set(ContentApproval.objects.values_list("state", flat=True)), {"pendiente"})
        self.assertFalse(ChurchAuditEvent.objects.filter(action__in=("contenido_aprobado", "contenido_devuelto")).exists())

    def test_the_rule_is_also_inside_the_service(self):
        devotional = self.prepared()
        revision = self.revision("devocional", devotional)
        inactive = make_user("pastor-inactivo@prueba.mx", "Iván", "Inactivo")
        assign_role(inactive, self.church, Role.PASTOR)
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])
        for user in (self.admin, self.member_user, self.mentor, self.pastor, self.pastor_b, inactive):
            self.assertFalse(approvals.can_approve(user, self.church), user.email)
            with self.assertRaises(PermissionDenied):
                approvals.approve(self.church, "devocional", devotional.pk, user, revision=revision, permitted_use="propia")
            with self.assertRaises(PermissionDenied):
                approvals.send_back(self.church, "devocional", devotional.pk, user, reason="Porque sí")
        # In their own church the pastor of B decides, and this devotional is not there.
        self.assertTrue(approvals.can_approve(self.pastor_b, self.church_b))
        with self.assertRaises(approvals.ApprovalError) as refused:
            approvals.approve(self.church_b, "devocional", devotional.pk, self.pastor_b, revision=revision, permitted_use="propia")
        self.assertEqual(refused.exception.code, "missing")
        with self.assertRaises(approvals.ApprovalError):
            approvals.approve(self.church, "cancion", devotional.pk, self.church_pastor, revision=revision, permitted_use="propia")
        with self.assertRaises(approvals.ApprovalError):
            approvals.approve(self.church, "devocional", devotional.pk, self.church_pastor, revision=revision, permitted_use="porque_si")
        self.assertEqual(Devotional.objects.get().status, "borrador")

    def test_a_post_without_the_csrf_token_decides_nothing(self):
        devotional = self.prepared()
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.church_pastor)
        data = {"revision": self.revision("devocional", devotional), "permitted_use": "propia", "reason": "Porque sí"}
        for name in ("approve", "return"):
            self.assertEqual(strict.post(url(name, "devocional", devotional), data).status_code, 403)
            self.assertEqual(strict.get(url(name, "devocional", devotional)).status_code, 405)
        self.assertEqual((Devotional.objects.get().status, self.row("devocional", devotional).state), ("borrador", "pendiente"))

    def test_with_the_module_off_nothing_answers(self):
        devotional = self.prepared()
        flags.set_enabled(self.church, "community", False)
        self.client.force_login(self.church_pastor)
        for address in (QUEUE, url("review", "devocional", devotional)):
            self.assertEqual(self.client.get(address).status_code, 404)
        for name in ("approve", "return"):
            self.assertEqual(self.client.post(url(name, "devocional", devotional), {"permitted_use": "propia", "reason": "x"}).status_code, 404)
        self.assertEqual(self.row("devocional", devotional).state, "pendiente")

    def test_each_church_has_its_own_list_and_a_guessed_address_is_a_404(self):
        mine = self.prepared(title="El de la iglesia A")
        theirs = self.prepared(church=self.church_b, actor=self.admin_b, title="El de la iglesia B")
        their_plan = self.plan(church=self.church_b, actor=self.admin_b, title="Plan de la iglesia B")
        draft = self.prepared(self.today + timedelta(days=2), title="Borrador que nadie pidió", publish=False)
        self.client.force_login(self.church_pastor)
        page = self.client.get(QUEUE)
        self.assertContains(page, "El de la iglesia A")
        for text in ("El de la iglesia B", "Plan de la iglesia B", "Borrador que nadie pidió"):
            self.assertNotContains(page, text)
        for address in (
            url("review", "devocional", theirs), url("review", "plan", their_plan),
            url("review", "devocional", draft),                          # a draft nobody asked about is not the pastor's to read
            reverse("resources:approval_review", args=["cancion", mine.pk]),  # a kind that does not exist
            reverse("resources:approval_review", args=["plan", mine.pk]),     # the id of a devotional as if it were a plan
            reverse("resources:approval_review", args=["devocional", 999999]),
        ):
            self.assertEqual(self.client.get(address).status_code, 404, address)
        for kind, obj in (("devocional", theirs), ("plan", their_plan), ("devocional", draft)):
            for name in ("approve", "return"):
                data = {"revision": self.revision(kind, obj), "permitted_use": "propia", "reason": "x"}
                self.assertEqual(self.client.post(url(name, kind, obj), data).status_code, 404)
        self.assertEqual(self.row("devocional", theirs).state, "pendiente")
        self.assertEqual(approvals.pending_count(self.church), 1)
        self.assertEqual([entry.obj for entry in approvals.pending(self.church_b)], [theirs, their_plan])
        # A row never crosses: the pastor of B, in B, approves B's and A's stays as it was.
        self.client.force_login(self.pastor_b)
        self.assertRedirects(self.decide("devocional", theirs), QUEUE)
        self.assertEqual(self.row("devocional", mine).state, "pendiente")


class WithoutAPastorTests(ApprovalFixture):
    def test_a_church_with_no_active_pastor_publishes_nothing_and_its_screens_say_why_and_what_to_do(self):
        from core.models import UserChurchRole

        devotional, plan = self.prepared(), self.plan(ask=False)
        pages = [
            QUEUE, reverse("resources:devotional_admin"), reverse("resources:devotional_create"),
            reverse("resources:devotional_edit", args=[devotional.pk]), reverse("resources:plan_admin"),
            reverse("resources:plan_days", args=[plan.pk]), reverse("resources:plan_publish", args=[plan.pk]),
        ]
        self.client.force_login(self.admin)
        for address in pages:  # with a pastor: no warning
            self.assertNotContains(self.client.get(address), NO_PASTOR)

        def no_pastor():
            self.assertFalse(approvals.has_active_pastor(self.church))
            for address in pages:
                page = self.client.get(address)
                self.assertContains(page, NO_PASTOR, msg_prefix=address)
                self.assertContains(page, "La administración no puede aprobar en su lugar.")
                self.assertContains(page, reverse("create_invitation"))
                self.assertContains(page, reverse("user_list"))

        self.church_pastor.is_active = False  # the account exists and cannot sign in
        self.church_pastor.save(update_fields=["is_active"])
        no_pastor()
        UserChurchRole.objects.filter(church=self.church, role=Role.PASTOR).delete()
        no_pastor()
        self.assertTrue(approvals.has_active_pastor(self.church_b))  # another church's pastor does not count

        # Asking still works and waits; the administration is not a fallback, by screen, by address or by service.
        self.assertRedirects(self.client.post(reverse("resources:plan_publish", args=[plan.pk])), reverse("resources:plan_days", args=[plan.pk]))
        for kind, obj in (("devocional", devotional), ("plan", plan)):
            data = {"revision": self.revision(kind, obj), "permitted_use": "propia"}
            self.assertEqual(self.client.post(url("approve", kind, obj), data).status_code, 403)
            with self.assertRaises(PermissionDenied):
                approvals.approve(self.church, kind, obj.pk, self.admin, **data)
            obj.refresh_from_db()
            self.assertEqual(obj.status, "borrador")
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(reverse("resources:devotional_today")), "Hoy no hay devocional.")
        self.assertNotContains(self.client.get(reverse("resources:devotional_today")), NO_PASTOR)  # the member is not told


    def test_the_notice_offers_the_two_links_only_when_they_open_for_this_account_in_this_church(self):
        """ "Invitar a alguien" and "Usuarios" act in the church of the account's first role. An administrator who is
        working in their second church is told what to do in words instead of being sent to a page that is not there."""
        from core.authz import SESSION_KEY
        from core.models import UserChurchRole

        assign_role(self.admin, self.church_b, Role.CHURCH_ADMIN)  # the same account administers both churches
        self._person(self.church_b, self.admin)
        UserChurchRole.objects.filter(role=Role.PASTOR).delete()
        self.client.force_login(self.admin)
        pages = (QUEUE, reverse("resources:devotional_admin"), reverse("resources:plan_admin"))
        for address in pages:  # in the church of the first role: the links open, so they are offered
            page = self.client.get(address)
            self.assertContains(page, NO_PASTOR)
            self.assertContains(page, reverse("create_invitation"))
            self.assertContains(page, reverse("user_list"))
        self.assertEqual(self.client.get(reverse("create_invitation")).status_code, 200)

        session = self.client.session
        session[SESSION_KEY] = self.church_b.pk
        session.save()
        for address in pages:  # working in the second church: no link that would not open, and what to do in words
            page = self.client.get(address)
            self.assertContains(page, NO_PASTOR, msg_prefix=address)
            self.assertNotIn(reverse("create_invitation"), main(page))
            self.assertNotIn(reverse("user_list"), main(page))
            self.assertContains(page, "Desde aquí no puedes invitar ni cambiar roles en esta iglesia")
            self.assertContains(page, "hace falta que una cuenta de esta iglesia tenga el rol «Pastor»")


class AfterApprovalTests(ApprovalFixture):
    def test_a_material_edit_takes_it_out_of_publication_and_other_changes_do_not(self):
        devotional = approve(self.church, "devocional", self.prepared())
        self.client.force_login(self.admin)
        edit = reverse("resources:devotional_edit", args=[devotional.pk])
        data = {"date": self.today.isoformat(), "title": "La vid verdadera", "passage": "Juan 15:1-8", "body": BODY,
                "author_name": "Ana López", "publish": "on"}
        self.assertContains(self.client.get(edit), "deja de verse en cuanto guardes y no vuelve a publicarse hasta que un pastor lo apruebe otra vez")
        # The same words (a browser's line breaks included) and another day: still published, still approved.
        tomorrow = self.today + timedelta(days=1)
        self.client.post(edit, {**data, "body": BODY.replace("\n", "\r\n"), "date": tomorrow.isoformat()})
        devotional.refresh_from_db()
        self.assertEqual((devotional.status, devotional.date, self.row("devocional", devotional).state), ("publicado", tomorrow, "aprobado"))
        self.client.post(edit, data)
        # Each of the four fields is material.
        for field, value in (("title", "Otro título"), ("passage", "Juan 15:9"), ("body", "Otro texto."), ("author_name", "Otra firma")):
            if devotional.status != "publicado":
                approve(self.church, "devocional", devotional)
            response = self.client.post(edit, {**data, field: value}, follow=True)
            self.assertContains(response, "pedimos la aprobación pastoral", msg_prefix=field)
            devotional.refresh_from_db()
            self.assertEqual((devotional.status, self.row("devocional", devotional).state), ("borrador", "pendiente"), field)
            self.client.force_login(self.member_user)
            self.assertEqual(self.client.get(reverse("resources:devotional_detail", args=[devotional.pk])).status_code, 404, field)
            self.assertContains(self.client.get(reverse("resources:devotional_today")), "Hoy no hay devocional.")
            self.client.force_login(self.admin)
            data[field] = value
        self.assertEqual(ChurchAuditEvent.objects.filter(action="contenido_fuera_de_publicacion").count(), 4)
        out = ChurchAuditEvent.objects.filter(action="contenido_fuera_de_publicacion").first()
        self.assertEqual((out.actor, out.object_type, out.object_id, out.metadata), (self.admin, "Devotional", str(devotional.pk), {}))
        for secret in ("Otro título", "Juan 15", "Otro texto", "Otra firma"):
            self.assertNotIn(secret, out.summary)
        # The approval on record never covers the new words.
        row = self.row("devocional", devotional)
        self.assertNotEqual(row.approved_digest, approvals.DEVOTIONAL.digest(devotional))

    def test_taking_it_back_to_a_draft_keeps_the_record_and_publishing_again_goes_through_a_pastor(self):
        devotional = approve(self.church, "devocional", self.prepared())
        self.prepared(publish=False, devotional=devotional)  # the administration unpublishes it, with the same words
        devotional.refresh_from_db()
        row = self.row("devocional", devotional)
        self.assertEqual((devotional.status, row.state, row.approved_by), ("borrador", "aprobado", self.church_pastor))
        self.client.force_login(self.church_pastor)
        self.assertContains(self.client.get(url("review", "devocional", devotional)), "Aprobado. Hoy no está publicado: la administración lo retiró.")
        self.assertNotContains(self.client.get(QUEUE), "La vid verdadera")
        # The same words again: still not published by the administration alone.
        self.prepared(devotional=devotional)
        devotional.refresh_from_db()
        self.assertEqual((devotional.status, self.row("devocional", devotional).state), ("borrador", "pendiente"))
        # Withdrawn again: the earlier approval is the record; with other words it no longer is.
        self.prepared(publish=False, devotional=devotional)
        self.assertEqual(self.row("devocional", devotional).state, "aprobado")
        self.prepared(publish=False, body="Palabras que nadie aprobó.", devotional=devotional)
        self.assertEqual(self.row("devocional", devotional).state, "cambio")

    def test_plans_draft_published_retired_who_reads_what(self):
        plan = self.plan()
        detail = reverse("resources:plan_detail", args=[plan.pk])
        day = reverse("resources:plan_day", args=[plan.pk, 1])

        def reads(user) -> bool:
            self.client.force_login(user)
            answers = {self.client.get(detail).status_code, self.client.get(day).status_code}
            self.assertEqual(len(answers), 1)
            return answers == {200}

        # Asked, not approved: a draft. The administration looks at it; nobody reads it, not even the pastor outside the preview.
        self.assertEqual((reads(self.member_user), reads(self.church_pastor), reads(self.admin)), (False, False, True))
        approve(self.church, "plan", plan)
        self.assertEqual((reads(self.member_user), reads(self.leader_user)), (True, True))
        plans.start(plan, self.member)
        plans.mark_day_read(plan.days.get(position=1), self.member)
        plans.retire_plan(plan, self.admin)
        self.assertEqual((reads(self.member_user), reads(self.leader_user)), (True, False))  # who had started it keeps it
        # The administration corrects a retired plan: words no pastor read are not shown to who had started it.
        self.client.force_login(self.admin)
        edit = reverse("resources:plan_day_edit", args=[plan.pk, plan.days.get(position=1).pk])
        self.assertContains(self.client.get(edit), "está retirado y lo sigue leyendo quien ya lo había empezado")
        self.client.post(edit, {"reference": "Juan 15:1", "title": "Día número 1", "reflection": "Otra reflexión.", "question": ""})
        plan.refresh_from_db()
        self.assertEqual((plan.status, self.row("plan", plan).state), ("borrador", "cambio"))
        self.assertEqual((reads(self.member_user), reads(self.leader_user)), (False, False))
        self.client.force_login(self.member_user)
        self.assertNotContains(self.client.get(HOME), "Continuar:")
        self.assertNotContains(self.client.get(reverse("resources:plan_list")), "Dos días en Juan")
        # Asked and approved again: published, and the reader's marks are where they were.
        plans.request_publication(plan, self.admin)
        approve(self.church, "plan", plan)
        self.assertEqual((reads(self.member_user), reads(self.leader_user)), (True, True))
        self.assertEqual(ReadingPlanDayRead.objects.filter(progress__person=self.member).count(), 1)

    def test_retiring_a_plan_withdraws_its_request(self):
        plan = self.plan()
        plans.retire_plan(plan, self.admin)
        self.assertFalse(ContentApproval.objects.filter(kind="plan", object_id=plan.pk).exists())
        self.assertEqual(ChurchAuditEvent.objects.filter(action="contenido_aprobacion_retirada").count(), 1)
        self.client.force_login(self.church_pastor)
        self.assertNotContains(self.client.get(QUEUE), "Dos días en Juan")
        self.assertEqual(self.client.get(url("review", "plan", plan)).status_code, 404)


class PublishedBeforeTests(ApprovalFixture):
    def published(self, title, day):
        return Devotional.objects.create(church=self.church, date=day, title=title, body=BODY, status="publicado", created_by=self.admin)

    def test_the_migration_marks_what_was_published_and_guesses_nobody(self):
        migration = importlib.import_module("resources.migrations.0004_mark_published_before_approvals")
        old = self.published("Publicado desde antes", self.today)
        Devotional.objects.create(church=self.church, date=self.today + timedelta(days=1), title="Borrador", body=BODY)
        approved = approve(self.church, "devocional", self.prepared(self.today + timedelta(days=2), title="Ya aprobado"))
        theirs = Devotional.objects.create(church=self.church_b, date=self.today, title="De B", body=BODY, status="publicado")
        plan = self.plan(ask=False)
        ReadingPlan.objects.filter(pk=plan.pk).update(status="publicado")
        retired = self.plan(ask=False, title="Retirado desde antes")
        ReadingPlan.objects.filter(pk=retired.pk).update(status="retirado")

        for _ in range(2):  # twice: the same rows
            migration.mark_published_before(django_apps, None)
        marks = ContentApproval.objects.filter(state="anterior")
        self.assertEqual(
            set(marks.values_list("church_id", "kind", "object_id")),
            {(self.church.pk, "devocional", old.pk), (self.church_b.pk, "devocional", theirs.pk), (self.church.pk, "plan", plan.pk)},
        )
        for mark in marks:
            self.assertEqual((mark.approved_by, mark.approved_at, mark.requested_by, mark.permitted_use, mark.approved_digest), (None, None, None, "", ""))
        self.assertEqual(self.row("devocional", approved).state, "aprobado")  # an approval is never rewritten
        old.refresh_from_db()
        self.assertEqual(old.status, "publicado")  # nothing is unpublished
        migration.unmark(django_apps, None)
        self.assertFalse(ContentApproval.objects.filter(state="anterior").exists())
        self.assertEqual(self.row("devocional", approved).state, "aprobado")

    def test_it_stays_published_and_a_pastor_approves_it_item_by_item(self):
        old = self.published("Publicado desde antes", self.today)
        ContentApproval.objects.create(church=self.church, kind="devocional", object_id=old.pk, state="anterior")
        unmarked = self.published("Publicado por otra vía", self.today - timedelta(days=1))
        Devotional.objects.create(church=self.church_b, date=self.today, title="De la iglesia B", body=BODY, status="publicado")
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(reverse("resources:devotional_today")), "Publicado desde antes")

        for user, verb in ((self.admin, "Ver"), (self.church_pastor, "Revisar")):
            self.client.force_login(user)
            page = self.client.get(QUEUE)
            self.assertContains(page, "Pendiente de aprobación pastoral")
            self.assertContains(page, "Esto ya está publicado y se sigue viendo: nadie lo retiró.")
            self.assertContains(page, "Se publicó antes de que existieran las aprobaciones.")
            self.assertContains(page, "Está publicado y no hay una aprobación registrada.")
            self.assertContains(page, f"{verb}<span")
            self.assertNotContains(page, "De la iglesia B")
        page = self.client.get(url("review", "devocional", old))
        self.assertContains(page, "Ya está publicado y se sigue viendo. Se publicó antes de que existieran las aprobaciones.")
        self.assertContains(page, ">Aprobar</button>")
        self.assertNotIn("Devolver sin aprobar</button>", main(page))  # approving is all a pastor does here
        self.assertContains(self.client.post(url("return", "devocional", old), {"reason": "No"}, follow=True), "Esto ya no espera aprobación")
        old.refresh_from_db()
        self.assertEqual(old.status, "publicado")

        self.assertRedirects(self.decide("devocional", old), QUEUE)
        self.assertRedirects(self.decide("devocional", unmarked, permitted_use="abierta"), QUEUE)
        self.assertEqual(
            list(ChurchAuditEvent.objects.filter(action="contenido_aprobado").order_by("id").values_list("summary", flat=True)),
            ["Aprobó un devocional (obra propia de la iglesia)", "Aprobó un devocional (dominio público o licencia abierta)"],
        )
        for item in (old, unmarked):
            item.refresh_from_db()
            self.assertEqual((item.status, self.row("devocional", item).state, self.row("devocional", item).approved_by),
                             ("publicado", "aprobado", self.church_pastor))
        self.assertContains(self.client.get(QUEUE), "Todo lo que está publicado tiene la aprobación de un pastor.")

    def test_editing_what_was_published_before_takes_it_out_like_anything_else(self):
        old = self.published("Publicado desde antes", self.today)
        ContentApproval.objects.create(church=self.church, kind="devocional", object_id=old.pk, state="anterior")
        services.save_devotional(self.church, self.admin, date=self.today, title="Publicado desde antes", body="Otro texto.", devotional=old)
        old.refresh_from_db()
        self.assertEqual((old.status, self.row("devocional", old).state), ("borrador", "cambio"))
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("resources:devotional_admin")), "Fuera de publicación")


class ReadingStaysPrivateTests(ApprovalFixture):
    def test_the_list_the_preview_and_both_decisions_never_ask_a_table_of_reading(self):
        read = approve(self.church, "devocional", self.prepared(self.today - timedelta(days=1), title="El de ayer"))
        started = approve(self.church, "plan", self.plan(title="El plan de siempre"))
        devotional, plan = self.prepared(title="El que espera"), self.plan(title="El plan que espera")
        to_return = self.prepared(self.today + timedelta(days=1), title="El que se devuelve")
        ContentApproval.objects.filter(kind="devocional", object_id=read.pk).update(state="anterior")  # so it is in the list again

        def pages(user):
            self.client.force_login(user)
            found = []
            for address in (QUEUE, url("review", "devocional", devotional), url("review", "plan", plan),
                            url("review", "devocional", read), url("review", "plan", started)):
                response = self.client.get(address)
                self.assertEqual(response.status_code, 200, address)
                found.append(re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", main(response)))
            return found

        before = {user: pages(user) for user in (self.church_pastor, self.admin)}
        services.mark_read(read, self.member)
        services.mark_read(read, self.leader)
        plans.start(started, self.member)
        plans.mark_day_read(started.days.get(position=1), self.member)
        plans.start(started, self.leader)
        self.assertEqual((DevotionalRead.objects.count(), ReadingPlanProgress.objects.count(), ReadingPlanDayRead.objects.count()), (2, 2, 1))

        with CaptureQueriesContext(connection) as queries:
            after = {user: pages(user) for user in (self.church_pastor, self.admin)}
            self.client.force_login(self.church_pastor)
            self.assertRedirects(self.decide("devocional", devotional), QUEUE)
            self.assertRedirects(self.decide("plan", plan), QUEUE)
            self.assertRedirects(self.decide("devocional", read), QUEUE)
            self.assertRedirects(self.client.post(url("return", "devocional", to_return), {"reason": "Falta la cita"}, follow=True), QUEUE)
        self.assertGreater(len(queries), 20)
        for query in queries:
            for table in PROGRESS_TABLES:
                self.assertNotIn(table, query["sql"])
        # Reading changed nothing of what the pastor and the administration see: no count, no name, no mark.
        self.assertEqual(before, after)
        for html in after[self.church_pastor] + after[self.admin]:
            for words in ("Diego", "Gala", "leyeron", "lectores", "Ya lo leíste", "empezaron el plan", "%"):
                self.assertNotIn(words, html)
        # Deciding wrote nothing about readers, and reading left no trace for the staff.
        self.assertEqual((DevotionalRead.objects.count(), ReadingPlanProgress.objects.count(), ReadingPlanDayRead.objects.count()), (2, 2, 1))
        self.assertFalse(ChurchAuditEvent.objects.filter(object_type__in=("DevotionalRead", "ReadingPlanProgress", "ReadingPlanDayRead")).exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_the_approval_row_has_no_reader_and_the_audit_words_exist(self):
        fields = {field.name for field in ContentApproval._meta.get_fields()}
        self.assertEqual(fields & {"person", "reader", "readers", "read_at", "progress"}, set())
        related = {field.related_model.__name__ for field in ContentApproval._meta.get_fields() if field.related_model}
        self.assertEqual(related, {"Church", "User"})
        for action in ("contenido_aprobacion_pedida", "contenido_aprobacion_retirada", "contenido_aprobado", "contenido_devuelto",
                       "contenido_fuera_de_publicacion"):
            self.assertIn(action, ACTION_LABELS)

    def test_a_resource_is_a_link_and_needs_no_approval(self):
        item = services.save_media(self.church, self.admin, kind="predicacion", title="Sermón enlazado", url="https://videos.invalid/x", date=self.today)
        self.assertEqual(item.status, "publicado")
        self.assertFalse(ContentApproval.objects.exists())
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(reverse("resources:media_list")), "Sermón enlazado")
        self.client.force_login(self.church_pastor)
        page = self.client.get(QUEUE)
        self.assertNotContains(page, "Sermón enlazado")
        self.assertContains(page, "Los recursos son enlaces a sitios que la iglesia no hospeda: no pasan por aquí.")
