"""«Pedir que borren mis datos»: one task for the administration, a fixed choice, nothing deleted."""

from django.test import Client
from django.urls import reverse

from cuenta_datos import services
from cuenta_datos.models import RemovalRequest
from formularios import queue
from groups import services as groups
from oracion.models import PrayerRequest
from people.models import ChurchAuditEvent, Person
from resources.models import ReadingPlan, ReadingPlanProgress

from .base import DataFixture, url

OPEN, DONE, WITHDRAWN = RemovalRequest.Status.OPEN, RemovalRequest.Status.DONE, RemovalRequest.Status.WITHDRAWN


class AskTests(DataFixture):
    def setUp(self):
        self.client.force_login(self.member_user)

    def ask(self, what="todo", client=None):
        return (client or self.client).post(url("ask_removal"), {"ask": what})

    def test_asking_creates_one_task_and_deletes_nothing(self):
        people, accounts = Person.objects.count(), type(self.member_user).objects.count()
        page = self.client.get(url("ask_removal"))
        self.assertContains(page, "Emmaus no borra nada por sí solo")
        self.assertContains(page, "se conservan por regla de la iglesia")
        self.assertRedirects(self.ask("contacto"), url("home"))
        row = RemovalRequest.objects.get()
        self.assertEqual((row.church, row.user, row.ask, row.status), (self.church, self.member_user, "contacto", OPEN))
        self.assertEqual((Person.objects.count(), type(self.member_user).objects.count()), (people, accounts))
        self.member.refresh_from_db()
        self.assertEqual(self.member.phone, "55 1111 2222")  # asked, not done
        audit = ChurchAuditEvent.objects.get(action="mis_datos_borrado_pedido")
        self.assertEqual(audit.metadata, {"person_id": self.member.pk, "value": "contacto"})
        home = self.client.get(url("home"))
        self.assertContains(home, "Pediste que borren: mis datos de contacto")
        self.assertContains(home, "Retirar solicitud")

    def test_only_the_three_fixed_choices_and_never_a_text(self):
        for bad in ("", "otra cosa", "<script>", "todo y además escríbeme a mi casa"):
            response = self.ask(bad)
            self.assertEqual(response.status_code, 200, bad)
        self.assertFalse(RemovalRequest.objects.exists())
        self.client.post(url("ask_removal"), {"ask": "cuenta", "nota": "TEXTO-LIBRE", "reason": "TEXTO-LIBRE"})
        row = RemovalRequest.objects.get()
        self.assertEqual({field.name for field in RemovalRequest._meta.concrete_fields},
                         {"id", "church", "user", "ask", "status", "created_at", "closed_at", "closed_by"})
        self.assertNotIn("TEXTO-LIBRE", str(ChurchAuditEvent.objects.filter(object_id=str(row.pk)).values()))

    def test_asking_twice_keeps_one_open_request(self):
        self.ask("cuenta")
        self.ask("todo")
        self.assertEqual(list(RemovalRequest.objects.values_list("ask", "status")), [("cuenta", OPEN)])
        again, created = services.ask_removal(self.church, self.member, self.member_user, "todo")
        self.assertFalse(created)
        self.assertEqual(again.ask, "cuenta")
        self.assertContains(self.client.get(url("ask_removal")), "Ya pediste que borren")

    def test_withdrawing_is_only_for_ones_own_open_request(self):
        self.ask()
        row = RemovalRequest.objects.get()
        other = Client()
        other.force_login(self.asked_user)
        self.assertEqual(other.post(url("withdraw", row.pk)).status_code, 404)
        self.assertEqual(self.client.get(url("withdraw", row.pk)).status_code, 405)
        self.assertRedirects(self.client.post(url("withdraw", row.pk)), url("home"))
        row.refresh_from_db()
        self.assertEqual((row.status, row.closed_by), (WITHDRAWN, None))
        self.assertTrue(ChurchAuditEvent.objects.filter(action="mis_datos_borrado_retirado").exists())
        self.assertRedirects(self.ask("cuenta"), url("home"))  # and may ask again
        self.assertEqual(RemovalRequest.objects.filter(status=OPEN).count(), 1)

    def test_a_request_of_one_church_is_not_seen_from_another(self):
        self.ask()
        row = RemovalRequest.objects.get()
        in_b = Person.objects.create(church=self.church_b, user=self.member_user, first_name="Diego", last_name="H")
        self.assertEqual(services.open_request(self.church_b, self.member_user), None)
        self.client.post(reverse("switch_church"), {"church": self.church_b.pk})
        home = self.client.get(url("home"))  # (the toast of the ask is still waiting: look at the row, not the words)
        self.assertNotContains(home, "Retirar solicitud")
        self.assertIsNone(home.context["open_request"])
        self.assertEqual(self.client.post(url("withdraw", row.pk)).status_code, 404)
        row.refresh_from_db()
        self.assertEqual(row.status, OPEN)
        self.assertIsNotNone(in_b.pk)


class TaskTests(DataFixture):
    """The administration's side: «Pendientes», the list, the task and «Marcar como atendida»."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        groups.set_membership(cls.group, cls.member, cls.admin)
        plan = ReadingPlan.objects.create(church=cls.church, title="Plan de Juan", created_by=cls.admin)
        ReadingPlanProgress.objects.create(plan=plan, person=cls.member)
        from datetime import timedelta

        from django.utils import timezone

        PrayerRequest.objects.create(church=cls.church, person=cls.member, author=cls.member_user, body="PETICION-PRIVADA",
                                     expires_at=timezone.now() + timedelta(days=20))
        cls.row, _ = services.ask_removal(cls.church, cls.member, cls.member_user, "todo")

    def setUp(self):
        self.client.force_login(self.admin)

    def test_pendientes_has_one_neutral_row_for_the_administration_only(self):
        rows = queue.rows(self.admin, self.church, self.admin_person, is_church_admin=True)
        mine = [row for row in rows if row.kind == "Datos personales"]
        self.assertEqual([(row.title, row.url, row.count) for row in mine], [("1 solicitud sobre datos personales", url("requests"), 1)])
        self.assertNotIn("Diego", mine[0].title)
        # The count of the shell's badge is the sum of the rows, with this source too.
        self.assertEqual(queue.count(self.admin, self.church, self.admin_person, is_church_admin=True), sum(row.count for row in rows))
        for user, person in ((self.pastor_user, self.pastor_person), (self.leader_user, self.leader), (self.member_user, self.member)):
            theirs = queue.rows(user, self.church, person, is_church_admin=False)
            self.assertEqual([row for row in theirs if row.kind == "Datos personales"], [], user.email)
            self.assertEqual(queue.count(user, self.church, person, is_church_admin=False), sum(row.count for row in theirs))
        self.assertContains(self.client.get(reverse("formularios:queue")), "1 solicitud sobre datos personales")

    def test_the_task_counts_what_the_administration_may_see_and_only_names_what_is_private(self):
        self.assertContains(self.client.get(url("requests")), "Diego Hernández Cruz")
        response = self.client.get(url("request", self.row.pk))
        self.assertEqual(response.status_code, 200)
        counts = {category.key: count for category, count in response.context["counts"]}
        self.assertEqual(counts["grupos"], 1)
        self.assertEqual(counts["perfil"], 1)
        for private in ("lectura", "oracion", "asistencia", "avisos"):
            self.assertIsNone(counts[private], private)
        self.assertContains(response, "Es privado de la persona")
        self.assertContains(response, "Todo lo que se pueda")
        self.assertContains(response, reverse("people:detail", args=[self.member.pk]))
        self.assertContains(response, "Emmaus todavía no borra cuentas")
        self.assertNotContains(response, "PETICION-PRIVADA")
        self.assertNotContains(response, "Plan de Juan")

    def test_marking_it_done_closes_it_once_and_deletes_nothing(self):
        people = Person.objects.count()
        self.assertEqual(self.client.get(url("request_done", self.row.pk)).status_code, 405)
        self.assertRedirects(self.client.post(url("request_done", self.row.pk)), url("requests"))
        self.row.refresh_from_db()
        self.assertEqual((self.row.status, self.row.closed_by), (DONE, self.admin))
        self.assertEqual(Person.objects.count(), people)
        audit = ChurchAuditEvent.objects.get(action="mis_datos_borrado_atendido")
        self.assertEqual(audit.metadata, {"person_id": self.member.pk})
        closed_at = self.row.closed_at
        # A second press, or the person withdrawing after, changes nothing.
        self.client.post(url("request_done", self.row.pk))
        self.assertIsNone(services.withdraw(self.church, self.member_user, self.row.pk))
        self.row.refresh_from_db()
        self.assertEqual((self.row.status, self.row.closed_at), (DONE, closed_at))
        self.assertEqual(ChurchAuditEvent.objects.filter(action__in=["mis_datos_borrado_atendido", "mis_datos_borrado_retirado"]).count(), 1)
        self.assertEqual(queue.count(self.admin, self.church, self.admin_person, is_church_admin=True),
                         sum(row.count for row in queue.rows(self.admin, self.church, self.admin_person, is_church_admin=True)))

    def test_a_stale_copy_never_reopens_or_recloses_a_request(self):
        """The person withdraws while the administration has the task open: the later «atendida» is refused, and
        the row keeps who really closed it."""
        stale = RemovalRequest.objects.get(pk=self.row.pk)
        self.assertIsNotNone(services.withdraw(self.church, self.member_user, self.row.pk))
        self.assertTrue(stale.is_open)  # the copy in memory still says open
        self.assertIsNone(services.mark_done(self.church, stale.pk, self.admin))
        self.row.refresh_from_db()
        self.assertEqual((self.row.status, self.row.closed_by), (WITHDRAWN, None))

    def test_denials(self):
        paths = [url("requests"), url("request", self.row.pk)]
        for user, expected in ((self.pastor_user, 403), (self.leader_user, 403), (self.member_user, 403), (self.coord2, 404)):
            self.client.force_login(user)
            for path in paths:
                self.assertEqual(self.client.get(path).status_code, expected, (user.email, path))
            self.assertEqual(self.client.post(url("request_done", self.row.pk)).status_code, expected, user.email)
        self.client.logout()
        for path in paths:
            self.assertEqual(self.client.get(path).status_code, 302)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, OPEN)

    def test_the_administration_of_another_church_gets_404_for_a_guessed_id(self):
        self.client.force_login(self.admin_b)
        self.assertEqual(self.client.get(url("request", self.row.pk)).status_code, 404)
        self.assertEqual(self.client.post(url("request_done", self.row.pk)).status_code, 404)
        self.assertNotContains(self.client.get(url("requests")), "Diego")
        self.assertEqual(queue.count(self.admin_b, self.church_b, self.admin_b_person, is_church_admin=True), 0)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, OPEN)

    def test_csrf_and_escaping(self):
        Person.objects.filter(pk=self.member.pk).update(first_name="<img src=x>")
        self.assertNotContains(self.client.get(url("requests")), "<img src=x>")
        self.assertNotContains(self.client.get(url("request", self.row.pk)), "<img src=x>")
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(url("request_done", self.row.pk)).status_code, 403)
        client.force_login(self.member_user)
        self.assertEqual(client.post(url("withdraw", self.row.pk)).status_code, 403)

    def test_a_request_of_an_account_whose_record_was_joined_away_still_opens(self):
        Person.objects.filter(pk=self.member.pk).update(merged_into=self.leader.pk)
        response = self.client.get(url("request", self.row.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ya no tiene un registro de persona")
        self.assertRedirects(self.client.post(url("request_done", self.row.pk)), url("requests"))
