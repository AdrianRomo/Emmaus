"""Acompañar: the administration asks one person to reach another; only the two of them see it."""

from datetime import timedelta

from django.db import models
from django.urls import reverse
from django.utils import timezone

from care import services
from care.models import FollowUp, FollowUpStep
from core.models import Role
from core.services import assign_role
from events.tests.base import make_user
from groups.tests.base import CommunityFixture
from people import flags
from people.models import ChurchAuditEvent

MINE = reverse("care:mine")
ALL = reverse("care:admin_list")
CREATE = reverse("care:create")
EMPTY = "No acompañas a nadie por ahora."


def url(name, follow_up):
    return reverse(f"care:{name}", args=[follow_up.pk])


class CareTests(CommunityFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.today = timezone.localdate()
        cls.pastor_user = make_user("pastor-sin-admin@prueba.mx", "Pablo", "Pastor")
        assign_role(cls.pastor_user, cls.church, Role.PASTOR)
        cls.pastor_person = cls._person(cls.church, cls.pastor_user)

    def make(self, **changes) -> FollowUp:
        values = {"person": self.member, "reason": "visita", "owner": self.leader, **changes}
        return services.create(self.church, self.admin, **values)

    def every_request(self, follow_up):
        """Each address of the module that names a follow-up, as (method, url, data)."""
        return [
            ("get", url("detail", follow_up), {}),
            ("get", url("edit", follow_up), {}),
            ("post", url("edit", follow_up), {"owner": "", "due_on": ""}),
            ("post", url("step", follow_up), {"paso": "llamada"}),
            ("post", url("status", follow_up), {"estado": "atendido"}),
            ("get", url("cancel", follow_up), {}),
            ("post", url("cancel", follow_up), {}),
        ]

    def assert_all(self, user, follow_up, expected):
        self.client.force_login(user)
        for method, address, data in self.every_request(follow_up):
            response = getattr(self.client, method)(address, data)
            self.assertEqual(response.status_code, expected, f"{user.email} {method} {address}")

    # ---- the journey ----------------------------------------------------------------------------
    def test_the_administration_creates_one_and_its_owner_works_on_it(self):
        self.client.force_login(self.admin)
        page = self.client.get(CREATE + "?q=diego")
        self.assertContains(page, "Diego Hernández Cruz")
        self.assertContains(page, f"?persona={self.member.pk}")
        page = self.client.get(CREATE + f"?persona={self.member.pk}")
        self.assertContains(page, "Vino por primera vez")
        self.assertContains(page, "Crear el seguimiento")
        self.assertContains(page, "Gala Guía")
        self.assertNotContains(page, "Nora Sin Cuenta")  # no account: cannot be responsible
        self.assertNotContains(page, "<textarea")
        self.assertEqual(page.content.decode().count("<h1"), 1)
        due = self.today + timedelta(days=2)
        response = self.client.post(
            CREATE, {"persona": self.member.pk, "reason": "visita", "owner": self.leader.pk, "due_on": due.isoformat()}, follow=True
        )
        follow_up = FollowUp.objects.get()
        self.assertRedirects(response, url("detail", follow_up))
        self.assertEqual(
            (follow_up.church, follow_up.person, follow_up.owner, follow_up.due_on, follow_up.status, follow_up.created_by),
            (self.church, self.member, self.leader, due, "pendiente", self.admin),
        )
        self.assertContains(response, "Cambiar responsable o fecha")
        self.assertContains(response, "Cancelar el seguimiento")
        page = self.client.get(ALL)
        self.assertContains(page, "Responsable: Gala Guía")

        self.client.force_login(self.leader_user)
        page = self.client.get(MINE)
        self.assertContains(page, "Personas que acompañas")
        self.assertContains(page, "Diego Hernández Cruz")
        self.assertContains(page, "Vino por primera vez")
        self.assertContains(page, "Para el ")
        self.assertContains(page, "Pendiente")
        self.assertNotContains(page, "Crear un seguimiento")
        self.assertEqual(page.content.decode().count("<h1"), 1)
        page = self.client.get(url("detail", follow_up))
        self.assertContains(page, 'href="tel:5511112222"')
        self.assertContains(page, 'href="mailto:diego@prueba.mx"')
        self.assertContains(page, "Lo que has hecho")
        self.assertContains(page, "Guardar lo que hice")
        self.assertContains(page, "Marcar como atendido")
        self.assertNotContains(page, "Cambiar responsable o fecha")
        self.assertNotContains(page, "Cancelar el seguimiento")
        self.assertNotContains(page, "<textarea")
        self.assertEqual(page.content.decode().count("<h1"), 1)

        response = self.client.post(url("step", follow_up), {"paso": "llamada"}, follow=True)
        self.assertContains(response, "Le llamé")
        self.assertContains(response, "En proceso")
        follow_up.refresh_from_db()
        step = follow_up.steps.get()
        self.assertEqual((follow_up.status, step.kind, step.on, step.by), ("en_proceso", "llamada", self.today, self.leader))

        response = self.client.post(url("status", follow_up), {"estado": "atendido"}, follow=True)
        self.assertRedirects(response, MINE)
        self.assertContains(response, EMPTY)
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, "atendido")
        self.assertIsNotNone(follow_up.closed_at)
        self.assertEqual(services.mine(self.leader), [])
        self.assertNotContains(self.client.get(url("detail", follow_up)), "Guardar lo que hice")

    def test_the_administration_reassigns_changes_the_date_and_cancels(self):
        follow_up = self.make(owner=None)
        self.client.force_login(self.admin)
        page = self.client.get(ALL)
        self.assertContains(page, "Sin responsable")
        self.assertContains(page, "row--attention")
        due = self.today + timedelta(days=5)
        page = self.client.get(url("edit", follow_up))
        self.assertContains(page, "Cambiar responsable o fecha")
        self.assertContains(page, "Sin responsable por ahora")
        self.assertNotContains(page, "Vino por primera vez")  # the reason is asked once
        self.assertEqual(page.content.decode().count("<h1"), 1)
        response = self.client.post(url("edit", follow_up), {"owner": self.coord_person.pk, "due_on": due.isoformat()})
        self.assertRedirects(response, url("detail", follow_up))
        follow_up.refresh_from_db()
        self.assertEqual((follow_up.owner, follow_up.due_on), (self.coord_person, due))
        # Somebody without an account, or of another church, is not offered and is refused.
        for person in (self.no_account, self.member_b):
            response = self.client.post(url("edit", follow_up), {"owner": person.pk, "due_on": ""})
            self.assertContains(response, "Elige a una persona de la lista.")
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.owner, self.coord_person)

        self.assertContains(self.client.get(url("cancel", follow_up)), "Sí, cancelar el seguimiento")
        self.assertRedirects(self.client.post(url("cancel", follow_up)), ALL)
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, "cancelado")
        self.assertIsNotNone(follow_up.closed_at)
        self.assertEqual(services.mine(self.coord_person), [])
        self.assertNotContains(self.client.get(ALL), "Diego Hernández Cruz")
        self.assertContains(self.client.get(ALL + "?estado=cancelados"), "Diego Hernández Cruz")
        self.assertEqual(self.client.get(url("edit", follow_up)).status_code, 404)
        self.assertEqual(self.client.get(ALL + "?estado=otra-cosa").status_code, 404)

    def test_the_administration_list_puts_the_ones_without_owner_first_and_is_paged_by_fifty(self):
        owned = self.make(due_on=self.today)
        orphan = self.make(person=self.asked, owner=None)
        self.client.force_login(self.admin)
        page = self.client.get(ALL)
        self.assertEqual([row.pk for row in page.context["follow_ups"]], [orphan.pk, owned.pk])
        self.assertNotContains(page, 'class="pager"')
        FollowUp.objects.bulk_create(
            [FollowUp(church=self.church, person=self.asked, reason="otro") for _ in range(100)]
        )
        page = self.client.get(ALL)
        self.assertEqual(len(page.context["follow_ups"]), 50)
        self.assertContains(page, "Página 1 de 3. Del 1 al 50 de 102.")
        self.assertContains(page, 'rel="next" href="?pagina=2"')
        last = self.client.get(ALL + "?pagina=3")
        self.assertEqual(len(last.context["follow_ups"]), 2)
        self.assertContains(last, "Página 3 de 3. Del 101 al 102 de 102.")
        # The state chosen travels in the links of its own pages
        FollowUp.objects.filter(owner__isnull=True).update(status=FollowUp.Status.CANCELLED)
        cancelled = self.client.get(ALL + "?estado=cancelados")
        self.assertContains(cancelled, 'rel="next" href="?estado=cancelados&amp;pagina=2"')
        self.assertContains(cancelled, "Del 1 al 50 de 101.")

    # ---- who sees it ----------------------------------------------------------------------------
    def test_the_person_it_is_about_never_sees_it(self):
        follow_up = self.make(reason="ausencia")
        services.add_step(follow_up, self.leader_user, kind="llamada", by=self.leader)
        self.client.force_login(self.member_user)
        for name in ("community:home", "community:profile", "care:mine"):
            page = self.client.get(reverse(name))
            self.assertEqual(page.status_code, 200, name)
            text = page.content.decode().lower()
            for word in ("seguimiento", "hace tiempo que no viene", "le llamé", "responsable"):
                self.assertNotIn(word, text, name)
        self.assertContains(self.client.get(MINE), EMPTY)
        self.assertEqual(services.mine(self.member), [])
        self.assertEqual(services.open_count(self.member), 0)
        self.assert_all(self.member_user, follow_up, 403)
        follow_up.refresh_from_db()
        self.assertEqual((follow_up.status, follow_up.steps.count()), ("en_proceso", 1))

    def test_not_even_as_the_administration_does_the_person_see_the_one_about_them(self):
        about_admin = services.create(self.church, self.admin, person=self.admin_person, reason="servir", owner=self.leader)
        self.assert_all(self.admin, about_admin, 403)
        self.assertEqual(len(self.client.get(ALL).context["follow_ups"]), 0)
        self.assertEqual(self.client.get(CREATE + f"?persona={self.admin_person.pk}").status_code, 404)
        with self.assertRaisesMessage(services.CareError, "su propio seguimiento"):
            services.assign(about_admin, self.admin, self.admin_person)

    def test_nobody_else_of_the_church_sees_it(self):
        follow_up = self.make(owner=self.coord_person)
        # another member, the guide of a group the person asked to join, a pastor without the administration role
        for user in (self.asked_user, self.leader_user, self.pastor_user):
            self.assert_all(user, follow_up, 403)
            self.assertContains(self.client.get(MINE), EMPTY)
            self.assertNotContains(self.client.get(MINE), "Diego")
            for address in (ALL, CREATE, CREATE + f"?persona={self.member.pk}"):
                self.assertEqual(self.client.get(address).status_code, 403, address)
            self.assertEqual(self.client.post(CREATE, {"persona": self.member.pk, "reason": "visita"}).status_code, 403)
        follow_up.refresh_from_db()
        self.assertEqual((follow_up.status, follow_up.owner, follow_up.steps.count()), ("pendiente", self.coord_person, 0))
        self.assertEqual(FollowUp.objects.count(), 1)

    def test_the_owner_does_not_administer(self):
        follow_up = self.make()
        self.client.force_login(self.leader_user)
        for method, name in (("get", "edit"), ("post", "edit"), ("get", "cancel"), ("post", "cancel")):
            self.assertEqual(getattr(self.client, method)(url(name, follow_up)).status_code, 403, name)
        self.assertEqual(self.client.post(url("status", follow_up), {"estado": "cancelado"}).status_code, 404)
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, "pendiente")

    def test_another_church_is_a_404_everywhere(self):
        follow_up = self.make()
        for user in (self.admin_b, self.member_b_user):
            self.assert_all(user, follow_up, 404)
        self.client.force_login(self.admin_b)
        self.assertEqual(self.client.get(CREATE + f"?persona={self.member.pk}").status_code, 404)
        response = self.client.post(CREATE, {"persona": self.member.pk, "reason": "visita"})
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(self.client.get(CREATE + "?q=diego"), "Diego")
        self.assertNotContains(self.client.get(ALL), "Diego")
        # and an owner of this church cannot be chosen from church B's form
        mine_b = services.create(self.church_b, self.admin_b, person=self.member_b, reason="visita")
        self.client.post(url("edit", mine_b), {"owner": self.leader.pk, "due_on": ""})
        mine_b.refresh_from_db()
        self.assertIsNone(mine_b.owner)
        follow_up.refresh_from_db()
        self.assertEqual((follow_up.status, follow_up.owner, follow_up.steps.count()), ("pendiente", self.leader, 0))
        self.assertEqual(FollowUp.objects.filter(church=self.church).count(), 1)

    def test_with_the_module_off_everything_is_a_404(self):
        follow_up = self.make()
        flags.set_enabled(self.church, "community", False)
        for user in (self.admin, self.leader_user):
            self.assert_all(user, follow_up, 404)
            for address in (MINE, ALL, CREATE):
                self.assertEqual(self.client.get(address).status_code, 404, address)

    def test_signed_out_nothing_answers(self):
        follow_up = self.make()
        for address in (MINE, ALL, CREATE, url("detail", follow_up)):
            self.assertIn(self.client.get(address).status_code, (302, 404), address)

    # ---- the rules ------------------------------------------------------------------------------
    def test_the_owner_has_an_account_and_is_of_the_same_church(self):
        with self.assertRaisesMessage(services.CareError, "todavía no tiene acceso a Emmaus"):
            self.make(owner=self.no_account)
        with self.assertRaisesMessage(services.CareError, "no es de esta iglesia"):
            self.make(owner=self.member_b)
        with self.assertRaisesMessage(services.CareError, "su propio seguimiento"):
            self.make(owner=self.member)
        with self.assertRaisesMessage(services.CareError, "no es de esta iglesia"):
            self.make(person=self.member_b, owner=None)
        with self.assertRaises(services.CareError):
            self.make(reason="chisme")
        with self.assertRaisesMessage(services.CareError, "Esa fecha ya pasó"):
            self.make(due_on=self.today - timedelta(days=1))
        self.assertFalse(FollowUp.objects.exists())
        follow_up = self.make(owner=None)
        for owner in (self.no_account, self.member_b):
            with self.assertRaises(services.CareError):
                services.assign(follow_up, self.admin, owner)
        follow_up.refresh_from_db()
        self.assertIsNone(follow_up.owner)
        self.assertEqual(ChurchAuditEvent.objects.filter(action__startswith="acompanar_").count(), 1)

    def test_a_closed_one_takes_no_step_and_does_not_change(self):
        follow_up = self.make()
        services.set_status(follow_up, self.leader_user, "atendido")
        with self.assertRaisesMessage(services.CareError, "ya está cerrado"):
            services.add_step(follow_up, self.leader_user, kind="mensaje", by=self.leader)
        for change in (
            lambda: services.assign(follow_up, self.admin, self.coord_person),
            lambda: services.set_due(follow_up, self.admin, self.today),
            lambda: services.set_status(follow_up, self.admin, "cancelado"),
            lambda: services.set_status(follow_up, self.admin, "en_proceso"),
            lambda: services.set_status(follow_up, self.admin, "pendiente"),
        ):
            with self.assertRaises(services.CareError):
                change()
        self.client.force_login(self.leader_user)
        response = self.client.post(url("step", follow_up), {"paso": "mensaje"}, follow=True)
        self.assertContains(response, "ya está cerrado")
        follow_up.refresh_from_db()
        self.assertEqual((follow_up.status, follow_up.owner, follow_up.steps.count()), ("atendido", self.leader, 0))

    def test_a_step_is_one_of_four_and_not_in_the_future(self):
        follow_up = self.make()
        with self.assertRaises(services.CareError):
            services.add_step(follow_up, self.leader_user, kind="platicamos de su familia", by=self.leader)
        with self.assertRaises(services.CareError):
            services.add_step(follow_up, self.leader_user, kind="llamada", by=self.leader, on=self.today + timedelta(days=1))
        self.client.force_login(self.leader_user)
        response = self.client.post(url("step", follow_up), {}, follow=True)
        self.assertContains(response, "Elige qué hiciste.")
        follow_up.refresh_from_db()
        self.assertEqual((follow_up.status, follow_up.steps.count()), ("pendiente", 0))

    def test_the_overdue_come_first_then_by_date_then_the_oldest(self):
        no_date_old = self.make(person=self.asked)
        soon = self.make(person=self.coord_person, due_on=self.today + timedelta(days=1))
        today = self.make(person=self.admin_person, due_on=self.today)
        late = self.make(due_on=self.today)
        very_late = self.make(person=self.pastor_person, due_on=self.today)
        no_date_new = self.make(person=self.no_account)
        FollowUp.objects.filter(pk=late.pk).update(due_on=self.today - timedelta(days=1))
        FollowUp.objects.filter(pk=very_late.pk).update(due_on=self.today - timedelta(days=9))
        rows = services.mine(self.leader)
        self.assertEqual([row.pk for row in rows], [very_late.pk, late.pk, today.pk, soon.pk, no_date_old.pk, no_date_new.pk])
        self.assertEqual([row.is_overdue for row in rows], [True, True, False, False, False, False])
        self.assertEqual([row.pk for row in services.mine(self.leader, limit=2)], [very_late.pk, late.pk])
        self.client.force_login(self.leader_user)
        page = self.client.get(MINE)
        self.assertContains(page, "Ya pasó", count=2)
        self.assertContains(page, "row--attention", count=2)
        self.assertContains(page, "Para el ", count=4)  # the two without a date say nothing

    def test_the_counts(self):
        self.assertEqual((services.open_count(self.leader), services.unassigned_count(self.church)), (0, 0))
        pending = self.make()
        working = self.make(person=self.asked)
        services.add_step(working, self.leader_user, kind="sin_respuesta", by=self.leader)
        done = self.make(person=self.coord_person)
        services.set_status(done, self.leader_user, "atendido")
        orphan = self.make(person=self.asked, owner=None)
        cancelled = self.make(person=self.coord_person, owner=None)
        services.set_status(cancelled, self.admin, "cancelado")
        services.create(self.church_b, self.admin_b, person=self.member_b, reason="visita")
        self.assertEqual(services.open_count(self.leader), 2)
        self.assertEqual({row.pk for row in services.mine(self.leader)}, {pending.pk, working.pk})
        self.assertEqual(services.unassigned_count(self.church), 1)
        self.assertEqual(services.unassigned_count(self.church_b), 1)
        services.assign(orphan, self.admin, self.leader)
        self.assertEqual((services.open_count(self.leader), services.unassigned_count(self.church)), (3, 0))

    # ---- privacy --------------------------------------------------------------------------------
    def test_no_model_of_the_module_has_a_field_for_free_text(self):
        for model in (FollowUp, FollowUpStep):
            for field in model._meta.get_fields():
                if not getattr(field, "concrete", False):
                    continue
                self.assertNotIsInstance(field, (models.TextField, models.JSONField), f"{model.__name__}.{field.name}")
                if isinstance(field, models.CharField):
                    self.assertTrue(field.choices, f"{model.__name__}.{field.name} is free text")
        self.assertEqual([field.name for field in FollowUp._meta.fields if isinstance(field, models.CharField)], ["reason", "status"])
        self.assertEqual([field.name for field in FollowUpStep._meta.fields if isinstance(field, models.CharField)], ["kind"])
        self.assertEqual(
            FollowUp.Status.choices,
            [("pendiente", "Pendiente"), ("en_proceso", "En proceso"), ("atendido", "Atendido"), ("cancelado", "Cancelado")],
        )

    def test_the_audit_says_what_happened_and_not_about_whom_or_why(self):
        due = self.today + timedelta(days=3)
        follow_up = self.make(reason="ausencia", owner=None)
        services.assign(follow_up, self.admin, self.leader)
        services.set_due(follow_up, self.admin, due)
        services.add_step(follow_up, self.leader_user, kind="llamada", by=self.leader)
        services.set_status(follow_up, self.leader_user, "atendido")
        other = self.make(reason="contacto")
        services.set_status(other, self.admin, "cancelado")
        rows = list(ChurchAuditEvent.objects.filter(action__startswith="acompanar_").order_by("id"))
        self.assertEqual(
            [row.action for row in rows],
            ["acompanar_creado", "acompanar_asignado", "acompanar_asignado", "acompanar_paso", "acompanar_estado",
             "acompanar_creado", "acompanar_estado"],
        )
        self.assertEqual(rows[0].summary, "Creó un seguimiento de persona")
        self.assertEqual(rows[1].summary, "Asignó un seguimiento de persona")
        forbidden = [
            "Diego", "Hernández", "Cruz", "Gala", "Guía", "diego@prueba.mx", "guia@prueba.mx", "55 1111 2222", "5511112222",
            *FollowUp.Reason.values, *[label for label in FollowUp.Reason.labels], *[label for label in FollowUpStep.Kind.labels],
        ]
        for row in rows:
            self.assertEqual(row.church, self.church)
            self.assertEqual(row.metadata, {"person_id": self.member.pk})
            self.assertEqual(row.object_type, "FollowUp")
            for word in forbidden:
                self.assertNotIn(word.lower(), row.summary.lower(), row.action)
        # nothing was written about church B, and a refused or repeated action writes nothing
        services.assign(follow_up, self.admin, self.leader)
        self.assertEqual(ChurchAuditEvent.objects.filter(action__startswith="acompanar_").count(), len(rows))
        self.assertFalse(ChurchAuditEvent.objects.filter(church=self.church_b, action__startswith="acompanar_").exists())
