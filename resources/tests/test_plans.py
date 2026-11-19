"""Planes de lectura: the church writes and confirms, each person reads at their pace, and what a person
started or read is only theirs."""

import re
from io import StringIO

from django.contrib import admin as django_admin
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from core.models import Church
from groups.tests.base import CommunityFixture
from people import flags
from people.models import ChurchAuditEvent
from resources import plans
from resources.models import ContentApproval, ReadingPlan, ReadingPlanDay, ReadingPlanDayRead, ReadingPlanProgress
from resources.tests.approving import approve, church_pastor

LIST = reverse("resources:plan_list")
ADMIN = reverse("resources:plan_admin")
CREATE = reverse("resources:plan_create")
HOME = reverse("community:home")
PANORAMA = reverse("overview:panorama")
REFLECTION = "Una rama no se esfuerza por dar fruto.\n\nLo da porque está unida a la vid."


def url(name, plan, *more):
    return reverse(f"resources:plan_{name}", args=[plan.pk, *more])


def main(response) -> str:
    """The page without the shell around it."""
    html = response.content.decode()
    return html[html.index("<main"):html.index("</main>")]


class PlansFixture(CommunityFixture):
    def plan(self, *, church=None, actor=None, days=3, publish=True, title="Tres días en Juan"):
        church, actor = church or self.church, actor or self.admin
        plan = plans.save_plan(
            church, actor, title=title, description="Lecturas cortas para empezar el día.",
            author_line="Preparado por el equipo pastoral",
        )
        for number in range(1, days + 1):
            plans.save_day(
                plan, actor, reference=f"Juan 15:{number}", title=f"Título del día {number}", reflection=REFLECTION,
                question="¿Qué te llevas hoy?",
            )
        if publish:
            plan = self.publish(plan)
        return plan

    @staticmethod
    def publish(plan):
        """The administration asks, a pastor of the church approves: that is what publishes a plan."""
        plans.request_publication(plan, plan.created_by)
        return approve(plan.church, "plan", plan)

    def day(self, plan, position):
        return ReadingPlanDay.objects.select_related("plan").get(plan=plan, position=position)

    def read(self, plan, person, *positions):
        plans.start(plan, person)
        for position in positions:
            plans.mark_day_read(self.day(plan, position), person)


class ReadingTests(PlansFixture):
    def test_a_member_finds_starts_reads_and_finishes_a_plan(self):
        plan = self.plan()
        self.client.force_login(self.member_user)
        page = self.client.get(LIST)
        self.assertEqual(page.content.decode().count("<h1"), 1)
        self.assertContains(page, "Tres días en Juan")
        self.assertContains(page, "3 días. Preparado por el equipo pastoral.")
        self.assertNotContains(page, "En curso")
        self.assertNotContains(page, "Administrar los planes de lectura")

        page = self.client.get(url("detail", plan))
        self.assertContains(page, "Empezar este plan</button>")
        self.assertContains(page, "Día 1: Título del día 1")
        self.assertContains(page, "Lectura: Juan 15:2.")
        self.assertEqual(page.content.decode().count("btn--primary"), 1)
        self.assertNotContains(page, "Dejar este plan")

        # A day can be read before starting; marking it needs the plan started.
        page = self.client.get(url("day", plan, 1))
        self.assertContains(page, "Empezar este plan</button>")
        self.assertNotContains(page, "Ya lo leí</button>")
        self.assertEqual(self.client.post(url("day_read", plan, 1)).status_code, 404)
        self.assertEqual(self.client.get(url("start", plan)).status_code, 405)

        self.assertRedirects(self.client.post(url("start", plan)), url("day", plan, 1))
        page = self.client.get(url("day", plan, 1))
        html = page.content.decode()
        self.assertEqual(html.count("<h1"), 1)
        self.assertContains(page, '<h1 class="voice page-head__title">Título del día 1</h1>', html=True)
        self.assertContains(page, "Tres días en Juan, día 1 de 3.")
        self.assertContains(page, "Lectura: Juan 15:1. Léela en tu Biblia: aquí va solo la cita.")
        self.assertContains(page, "<p>Lo da porque está unida a la vid.</p>")
        self.assertContains(page, "Para pensar")
        self.assertContains(page, "Ya lo leí</button>")
        self.assertEqual(html.count("btn--primary"), 1)
        self.assertNotContains(page, "Día anterior")
        self.assertContains(page, "Día siguiente")
        for forbidden in ("<textarea", '<input type="text"'):  # the question is for thinking: nothing to answer
            self.assertNotIn(forbidden, main(page))

        for _ in range(2):  # twice: the second one changes nothing
            response = self.client.post(url("day_read", plan, 1), follow=True)
            self.assertRedirects(response, url("day", plan, 1))
            self.assertContains(response, "Ya lo leíste.")
            self.assertNotContains(response, "Ya lo leí</button>")
        self.assertEqual(ReadingPlanDayRead.objects.count(), 1)
        self.assertEqual(self.client.get(url("day_read", plan, 1)).status_code, 405)

        page = self.client.get(LIST)
        self.assertContains(page, "En curso")
        self.assertContains(page, "Continuar en el día 2")
        self.assertContains(self.client.get(url("detail", plan)), "Continuar en el día 2")

        # Days are not locked: the third before the second. Resuming goes to the first one without a mark.
        self.assertRedirects(self.client.post(url("day_read", plan, 3)), url("day", plan, 3))
        self.assertContains(self.client.get(url("detail", plan)), "Continuar en el día 2")
        page = self.client.get(url("day", plan, 3))
        self.assertContains(page, "Día anterior")
        self.assertNotContains(page, "Día siguiente")

        # The last one that was missing: the plan's page says it, quietly.
        response = self.client.post(url("day_read", plan, 2), follow=True)
        self.assertRedirects(response, url("detail", plan))
        self.assertContains(response, "Terminaste este plan.")
        self.assertContains(response, "notice notice--success")
        self.assertNotContains(response, "Continuar en el día")
        self.assertNotContains(response, "Empezar este plan")
        html = main(response)
        self.assertEqual(html.count("status--done"), 3)
        for loud in ("%", "racha", "Felicidades", "puntos", "confeti", "personas"):
            self.assertNotIn(loud, html)
        page = self.client.get(LIST)
        self.assertContains(page, "Terminado")
        self.assertNotContains(page, "Continuar en el día")
        self.assertRedirects(self.client.post(url("start", plan)), url("detail", plan))  # starting again changes nothing
        self.assertEqual(ReadingPlanDayRead.objects.count(), 3)
        self.assertEqual(self.client.get(url("day", plan, 4)).status_code, 404)

    def test_marks_are_idempotent_and_need_the_plan_started(self):
        plan = self.plan()
        with self.assertRaises(plans.ResourceError):
            plans.mark_day_read(self.day(plan, 1), self.member)
        first = plans.start(plan, self.member)
        self.assertEqual(plans.start(plan, self.member), first)
        self.assertTrue(plans.mark_day_read(self.day(plan, 1), self.member))
        self.assertFalse(plans.mark_day_read(self.day(plan, 1), self.member))
        with self.assertRaises(plans.ResourceError):
            plans.start(plan, self.member_b)  # a person of another church
        with self.assertRaises(plans.ResourceError):
            plans.mark_day_read(self.day(plan, 1), self.member_b)
        with self.assertRaises(plans.ResourceError):
            plans.start(self.plan(publish=False, title="Borrador"), self.member)
        self.assertEqual((ReadingPlanProgress.objects.count(), ReadingPlanDayRead.objects.count()), (1, 1))

    def test_leaving_deletes_the_marks_and_the_plan_can_be_started_again(self):
        plan = self.plan()
        self.read(plan, self.member, 1, 2)
        self.read(plan, self.leader, 1)
        self.client.force_login(self.member_user)
        page = self.client.get(url("leave", plan))
        self.assertContains(page, "¿Dejar el plan «Tres días en Juan»?")
        self.assertContains(page, "Puedes empezarlo otra vez cuando quieras.")
        self.assertEqual(ReadingPlanDayRead.objects.count(), 3)  # asking deletes nothing
        response = self.client.post(url("leave", plan), follow=True)
        self.assertRedirects(response, url("detail", plan))
        self.assertContains(response, "Dejaste el plan")
        self.assertContains(response, "Empezar este plan</button>")
        self.assertFalse(ReadingPlanProgress.objects.filter(person=self.member).exists())
        self.assertEqual(ReadingPlanDayRead.objects.count(), 1)  # the other reader's mark stays
        self.assertRedirects(self.client.get(url("leave", plan)), url("detail", plan))  # nothing to leave

        self.assertRedirects(self.client.post(url("start", plan)), url("day", plan, 1))
        self.assertContains(self.client.get(url("day", plan, 1)), "Ya lo leí</button>")  # from the beginning

    def test_mi_iglesia_says_where_to_continue_and_only_to_the_reader(self):
        plan = self.plan()
        self.client.force_login(self.member_user)
        self.assertNotContains(self.client.get(HOME), "Continuar:")
        self.read(plan, self.member, 1)
        page = self.client.get(HOME)
        self.assertContains(
            page, f'<p class="more"><a href="{url("day", plan, 2)}">Continuar: Tres días en Juan, día 2</a></p>', html=True
        )
        self.assertEqual(plans.current_for(self.church, self.member), (plan, 2))
        self.assertIsNone(plans.current_for(self.church, self.leader))
        self.client.force_login(self.leader_user)
        self.assertNotContains(self.client.get(HOME), "Continuar:")
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(HOME), "Continuar:")

        self.read(plan, self.member, 2, 3)  # finished: nothing to continue
        self.client.force_login(self.member_user)
        self.assertNotContains(self.client.get(HOME), "Continuar:")
        self.assertIsNone(plans.current_for(self.church, self.member))

    def test_the_churchs_words_are_escaped(self):
        plan = self.plan(days=0, publish=False)
        plans.save_day(plan, self.admin, reference="Juan 1:1", reflection="<script>alert('x')</script> <b>negritas</b>", question="<i>¿sí?</i>")
        self.publish(plan)
        self.client.force_login(self.member_user)
        page = self.client.get(url("day", plan, 1))
        self.assertContains(page, '<h1 class="voice page-head__title">Día 1</h1>', html=True)
        self.assertNotContains(page, "<script>alert")
        self.assertNotContains(page, "<b>negritas</b>")
        self.assertNotContains(page, "<i>¿sí?</i>")
        self.assertContains(page, "&lt;script&gt;")


class VisibilityTests(PlansFixture):
    def test_a_draft_is_invisible_to_a_member_by_list_and_by_address(self):
        draft = self.plan(publish=False, title="Plan en borrador")
        self.client.force_login(self.member_user)
        page = self.client.get(LIST)
        self.assertNotContains(page, "Plan en borrador")
        self.assertContains(page, "Tu iglesia todavía no publica planes de lectura.")
        for address in (url("detail", draft), url("day", draft, 1), url("leave", draft)):
            self.assertEqual(self.client.get(address).status_code, 404, address)
        for address in (url("start", draft), url("day_read", draft, 1)):
            self.assertEqual(self.client.post(address).status_code, 404, address)
        self.assertEqual(ReadingPlanProgress.objects.count(), 0)

        # The administration looks at it, and cannot start or mark it.
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(LIST), "Plan en borrador")
        page = self.client.get(url("detail", draft))
        self.assertContains(page, "Este plan es un borrador")
        self.assertNotContains(page, "Empezar este plan")
        page = self.client.get(url("day", draft, 1))
        self.assertContains(page, "Este plan es un borrador")
        self.assertNotIn("</button>", main(page))
        self.assertEqual(self.client.post(url("start", draft)).status_code, 404)
        self.assertEqual(self.client.post(url("day_read", draft, 1)).status_code, 404)

    def test_another_churchs_plan_day_and_progress_are_a_404(self):
        ours = self.plan()
        theirs = self.plan(church=self.church_b, actor=self.admin_b, title="Plan de la iglesia B")
        self.read(theirs, self.member_b, 1)
        their_day = self.day(theirs, 1)
        for user in (self.member_user, self.admin):
            self.client.force_login(user)
            self.assertNotContains(self.client.get(LIST), "Plan de la iglesia B")
            for name, more in (("detail", ()), ("day", (1,)), ("leave", ()), ("edit", ()), ("days", ()), ("day_create", ()),
                               ("day_edit", (their_day.pk,)), ("day_delete", (their_day.pk,)), ("publish", ()), ("retire", ())):
                status = self.client.get(url(name, theirs, *more)).status_code
                self.assertIn(status, (403, 404) if user == self.member_user else (404,), (name, user))
            for name, more in (("start", ()), ("day_read", (1,)), ("leave", ()), ("retire", ()), ("publish", ())):
                status = self.client.post(url(name, theirs, *more), {"confirm": "on"}).status_code
                self.assertIn(status, (403, 404) if user == self.member_user else (404,), (name, user))
        # A day of another church's plan under our plan's address is a 404 too.
        self.assertEqual(self.client.get(url("day_edit", ours, their_day.pk)).status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, "publicado")
        self.assertEqual(list(ReadingPlanProgress.objects.values_list("person", flat=True)), [self.member_b.pk])
        self.assertIsNone(plans.progress_of(theirs, self.member))
        self.assertIsNone(plans.current_for(self.church, self.member_b))

        self.client.force_login(self.member_b_user)
        page = self.client.get(LIST)
        self.assertContains(page, "Plan de la iglesia B")
        self.assertNotContains(page, "Tres días en Juan")
        self.assertEqual(self.client.get(url("detail", ours)).status_code, 404)

    def test_retiring_hides_it_from_who_never_started_and_keeps_it_for_who_is_reading(self):
        plan = self.plan()
        self.read(plan, self.member, 1)
        plans.retire_plan(plan, self.admin)

        # Never started: gone by list and by address.
        self.client.force_login(self.leader_user)
        self.assertNotContains(self.client.get(LIST), "Tres días en Juan")
        self.assertEqual(self.client.get(url("detail", plan)).status_code, 404)
        self.assertEqual(self.client.get(url("day", plan, 1)).status_code, 404)
        self.assertEqual(self.client.post(url("start", plan)).status_code, 404)
        self.assertEqual(self.client.post(url("day_read", plan, 1)).status_code, 404)
        with self.assertRaises(plans.ResourceError):
            plans.start(plan, self.leader)

        # In progress: listed, readable, markable to the end, and Mi iglesia still says where to continue.
        self.client.force_login(self.member_user)
        page = self.client.get(LIST)
        self.assertContains(page, "Tres días en Juan")
        self.assertContains(page, "Tu iglesia lo retiró; tú puedes terminarlo.")
        self.assertContains(self.client.get(url("detail", plan)), "Tu iglesia retiró este plan.")
        self.assertContains(self.client.get(HOME), "Continuar: Tres días en Juan, día 2")
        self.assertRedirects(self.client.post(url("day_read", plan, 2)), url("day", plan, 2))
        response = self.client.post(url("day_read", plan, 3), follow=True)
        self.assertContains(response, "Terminaste este plan.")
        self.assertContains(self.client.get(LIST), "Terminado")  # finished and retired: still theirs to read again

        # Leaving a retired plan is final, and the question says so.
        self.assertContains(self.client.get(url("leave", plan)), "no vas a poder abrirlo otra vez")
        self.assertRedirects(self.client.post(url("leave", plan)), LIST)
        self.assertEqual(self.client.get(url("detail", plan)).status_code, 404)
        self.assertEqual(self.client.post(url("start", plan)).status_code, 404)
        self.assertEqual(ReadingPlanProgress.objects.count(), 0)

    def test_with_the_module_off_every_address_is_a_404_and_the_link_is_gone(self):
        plan = self.plan()
        day = self.day(plan, 1)
        self.read(plan, self.admin_person, 1)
        addresses = [LIST, ADMIN, CREATE] + [url(name, plan) for name in ("detail", "leave", "edit", "days", "day_create", "publish", "retire")]
        addresses += [url("day", plan, 1), url("day_edit", plan, day.pk), url("day_delete", plan, day.pk)]
        posts = [url("start", plan), url("day_read", plan, 1)]
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(HOME), "Planes de lectura")

        def all_404():
            for address in addresses:
                self.assertEqual(self.client.get(address).status_code, 404, address)
            for address in posts:
                self.assertEqual(self.client.post(address).status_code, 404, address)

        flags.set_enabled(self.church, "community", False)
        all_404()
        self.assertNotContains(self.client.get(reverse("events:list") + "?todos=1"), "Planes de lectura")
        flags.set_enabled(self.church, "community", True)
        with override_settings(EMMAUS_V2_MODULES=False):
            all_404()
        self.assertEqual(ReadingPlanDayRead.objects.count(), 1)

    def test_somebody_without_an_account_is_sent_to_sign_in(self):
        plan = self.plan()
        for address in (LIST, url("detail", plan), url("day", plan, 1), ADMIN):
            self.assertEqual(self.client.get(address).status_code, 302, address)


class WritingTests(PlansFixture):
    def data(self, **changes):
        return {"reference": "Juan 15:1-8", "title": "La vid", "reflection": REFLECTION, "question": "¿Qué te llevas hoy?", **changes}

    def test_only_the_administration_writes(self):
        plan = self.plan()
        day = self.day(plan, 1)
        for user in (self.member_user, self.leader_user):
            self.client.force_login(user)
            for address in (ADMIN, CREATE, url("edit", plan), url("days", plan), url("day_create", plan),
                            url("day_edit", plan, day.pk), url("publish", plan), url("retire", plan)):
                self.assertEqual(self.client.get(address).status_code, 403, address)
            self.assertEqual(self.client.post(CREATE, {"title": "Intento"}).status_code, 403)
            self.assertEqual(self.client.post(url("retire", plan)).status_code, 403)
            self.assertEqual(self.client.post(url("day_edit", plan, day.pk), self.data(reference="Otra")).status_code, 403)
        plan.refresh_from_db()
        self.assertEqual((plan.status, ReadingPlan.objects.count(), self.day(plan, 1).reference), ("publicado", 1, "Juan 15:1"))

    def test_the_administration_writes_a_plan_and_asks_and_a_pastor_publishes_it(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(ADMIN), "Todavía no hay planes de lectura.")
        response = self.client.post(CREATE, {"title": "Plan nuevo", "description": "De qué trata.", "author_line": "Preparado por el equipo pastoral."})
        plan = ReadingPlan.objects.get()
        self.assertRedirects(response, url("days", plan))
        self.assertEqual((plan.status, plan.church, plan.created_by, plan.author_line), ("borrador", self.church, self.admin, "Preparado por el equipo pastoral"))
        self.assertIsNone(plan.rights_confirmed_by)

        # No day yet: there is nothing to ask a pastor about.
        page = self.client.get(url("publish", plan))
        self.assertContains(page, "Este plan todavía no tiene días.")
        self.assertNotIn("</button>", main(page))
        response = self.client.post(url("publish", plan))
        self.assertContains(response, "Agrega por lo menos uno antes de pedir su aprobación.")
        with self.assertRaises(plans.ResourceError):
            plans.request_publication(plan, self.admin)
        self.assertFalse(ContentApproval.objects.exists())

        page = self.client.get(url("day_create", plan))
        self.assertContains(page, "Agregar el día 1")
        self.assertContains(page, "Escribe la cita; el texto bíblico lo lee cada quien en su Biblia. No pegues texto de una traducción con derechos reservados.")
        self.assertEqual(page.content.decode().count("btn--primary"), 1)
        self.assertContains(self.client.post(url("day_create", plan), self.data(reference="")), "Escribe la cita del día")
        self.assertContains(self.client.post(url("day_create", plan), self.data(reflection="a" * 1501)), "hasta 1500 caracteres y esta tiene 1501")
        self.assertEqual(plan.days.count(), 0)
        self.assertRedirects(self.client.post(url("day_create", plan), self.data()), url("days", plan))
        self.assertRedirects(self.client.post(url("day_create", plan), self.data(title="", reflection="", question="")), url("days", plan))
        self.assertEqual(list(plan.days.values_list("position", "title")), [(1, "La vid"), (2, "")])

        # Days: the administration asks for the approval. Asking publishes nothing, whatever the form carries.
        page = self.client.get(url("publish", plan))
        self.assertContains(page, "Pedir la aprobación pastoral</button>")
        self.assertNotContains(page, "Confirmo que")
        self.assertEqual(page.content.decode().count("btn--primary"), 1)
        response = self.client.post(url("publish", plan), {"confirm": "on", "status": "publicado"}, follow=True)
        self.assertRedirects(response, url("days", plan))
        # No pastor account yet in this church: the message says that, not that somebody was asked.
        self.assertContains(response, "Guardamos tu solicitud para publicar el plan. Queda esperando, pero hoy en tu iglesia no hay ninguna cuenta activa con el rol de pastor")
        self.assertNotContains(response, "Emmaus no le avisa a nadie: lo verán")
        self.assertContains(response, "notice--warning")
        self.assertContains(response, "la administración no puede publicarlo por su cuenta")
        plan.refresh_from_db()
        self.assertEqual((plan.status, plan.rights_confirmed_by, plan.rights_confirmed_at), ("borrador", None, None))
        self.assertContains(self.client.get(ADMIN), "Por aprobar")
        self.client.post(url("publish", plan))  # asking twice changes nothing
        asked = ChurchAuditEvent.objects.get(action="contenido_aprobacion_pedida")
        self.assertEqual((asked.actor, asked.object_type, asked.object_id, asked.metadata), (self.admin, "ReadingPlan", str(plan.pk), {}))
        self.assertEqual(asked.summary, "Pidió la aprobación pastoral de un plan de lectura")
        self.client.force_login(self.member_user)
        self.assertNotContains(self.client.get(LIST), "Plan nuevo")
        self.assertEqual(self.client.get(url("detail", plan)).status_code, 404)

        # A pastor of the church approves: that publishes it, under the pastor's name.
        pastor = church_pastor(self.church)
        approve(self.church, "plan", plan)
        self.assertEqual((plan.status, plan.rights_confirmed_by), ("publicado", pastor))
        self.assertIsNotNone(plan.rights_confirmed_at)
        row = ChurchAuditEvent.objects.get(action="contenido_aprobado")
        self.assertEqual(
            (row.church, row.actor, row.object_type, row.object_id, row.metadata),
            (self.church, pastor, "ReadingPlan", str(plan.pk), {"permitted_use": "propia"}),
        )
        self.assertEqual(row.summary, "Aprobó y publicó un plan de lectura (obra propia de la iglesia)")
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.get(url("publish", plan)), url("days", plan))  # already published
        self.client.force_login(self.member_user)
        self.assertContains(self.client.get(LIST), "Plan nuevo")

    def test_correcting_a_published_plan_takes_it_out_of_publication_and_keeps_its_days_and_marks(self):
        plan = self.plan(days=2, publish=False)
        first, second = self.day(plan, 1), self.day(plan, 2)
        self.client.force_login(self.admin)
        # A draft: a day can be taken out and the next ones move up.
        extra = plans.save_day(plan, self.admin, reference="Juan 15:3")
        self.assertContains(self.client.get(url("days", plan)), "Quitar")
        self.assertContains(self.client.get(url("day_delete", plan, first.pk)), "¿Quitar el día 1 del plan?")
        self.assertRedirects(self.client.post(url("day_delete", plan, first.pk)), url("days", plan))
        self.assertEqual(list(plan.days.values_list("pk", "position")), [(second.pk, 1), (extra.pk, 2)])

        self.publish(plan)
        self.read(plan, self.member, 1)

        def days_are_kept():
            self.assertNotContains(self.client.get(url("days", plan)), "Quitar")
            self.assertEqual(self.client.get(url("day_delete", plan, second.pk)).status_code, 404)
            self.assertEqual(self.client.post(url("day_delete", plan, second.pk)).status_code, 404)
            with self.assertRaises(plans.ResourceError):
                plans.delete_day(second, self.admin)

        days_are_kept()
        # The screen says, before saving, that the plan will leave publication.
        self.assertContains(self.client.get(url("day_edit", plan, second.pk)), "el plan deja de verse en cuanto guardes")
        self.assertContains(self.client.get(url("edit", plan)), "deja de verse en cuanto guardes")
        self.assertContains(self.client.get(url("days", plan)), "el plan deja de verse en cuanto guardes")
        # Saving the same words is not a change.
        same = self.day(plan, 1)
        self.client.post(url("day_edit", plan, second.pk), {
            "reference": same.reference, "title": same.title, "reflection": same.reflection, "question": same.question,
        })
        plan.refresh_from_db()
        self.assertEqual(plan.status, "publicado")
        self.assertRedirects(self.client.post(url("day_edit", plan, second.pk), self.data(title="Sin la errata")), url("days", plan))
        plan.refresh_from_db()
        self.assertEqual(plan.status, "borrador")
        self.assertEqual(ContentApproval.objects.get(kind="plan", object_id=plan.pk).state, "cambio")
        # A draft again, but one that was published: its days stay, and so does what people marked.
        days_are_kept()
        page = self.client.get(url("days", plan))
        self.assertContains(page, "Fuera de publicación: nadie lo ve, tampoco quien ya lo había empezado (sus marcas se conservan).")
        self.assertContains(page, "cambió después de publicarse y esos cambios no los ha aprobado un pastor")
        self.assertNotContains(page, "Retirar el plan")
        self.assertRedirects(self.client.post(url("edit", plan), {"title": "Otro título", "description": "", "author_line": ""}), url("days", plan))
        second.refresh_from_db()
        plan.refresh_from_db()
        self.assertEqual((second.title, plan.title, plan.status), ("Sin la errata", "Otro título", "borrador"))
        self.assertEqual(ReadingPlanDayRead.objects.count(), 1)
        # The reader does not see words no pastor read; nor does retiring the plan hand them over.
        self.assertFalse(plans.may_read(plan, plans.progress_of(plan, self.member)))
        with self.assertRaises(plans.ResourceError):
            plans.retire_plan(plan, self.admin)
        self.assertContains(self.client.post(url("retire", plan), follow=True), "Este plan cambió después de publicarse")
        plan.refresh_from_db()
        self.assertEqual(plan.status, "borrador")
        # Approved again, the reader is where they were.
        self.publish(plan)
        self.assertTrue(plans.may_read(plan, plans.progress_of(plan, self.member)))
        self.assertEqual(ReadingPlanDayRead.objects.count(), 1)

        rows = list(ChurchAuditEvent.objects.filter(action__startswith="plan_").order_by("id"))
        self.assertEqual([row.summary for row in rows], [
            "Empezó un borrador de plan de lectura",
            "Agregó el día 1 a un borrador de plan de lectura",
            "Agregó el día 2 a un borrador de plan de lectura",
            "Agregó el día 3 a un borrador de plan de lectura",
            "Quitó el día 1 de un borrador de plan de lectura",
            "Modificó el día 1 de un plan de lectura publicado",
            "Modificó el día 1 de un plan de lectura publicado",
            "Modificó los datos de un borrador de plan de lectura",
        ])
        out = ChurchAuditEvent.objects.get(action="contenido_fuera_de_publicacion")
        self.assertEqual((out.actor, out.object_type, out.object_id, out.metadata), (self.admin, "ReadingPlan", str(plan.pk), {}))
        rows.append(out)
        for row in rows:  # which plan and which day, never the words
            stored = f"{row.summary} {row.metadata}"
            for secret in ("Tres días", "Otro título", "Juan 15", "errata", "Una rama", "Diego"):
                self.assertNotIn(secret, stored)
            self.assertEqual((row.church, row.actor, row.object_type, row.object_id), (self.church, self.admin, "ReadingPlan", str(plan.pk)))

    def test_retiring_is_audited_and_publishing_again_needs_a_new_approval(self):
        plan = self.plan()
        self.client.force_login(self.admin)
        page = self.client.get(url("retire", plan))
        self.assertContains(page, "¿Retirar el plan «Tres días en Juan»?")
        self.assertContains(page, "Quien ya lo empezó lo puede seguir leyendo hasta terminarlo.")
        for _ in range(2):
            self.assertRedirects(self.client.post(url("retire", plan)), url("days", plan))
        plan.refresh_from_db()
        self.assertEqual(plan.status, "retirado")
        self.assertEqual(ChurchAuditEvent.objects.filter(action="plan_retirado", summary="Retiró un plan de lectura").count(), 1)
        self.assertRedirects(self.client.get(url("retire", plan)), url("days", plan))
        self.assertContains(self.client.get(ADMIN), "Retirado")
        self.assertContains(self.client.get(url("detail", plan)), "Este plan está retirado")

        confirmed_at = plan.rights_confirmed_at
        self.assertRedirects(self.client.post(url("publish", plan)), url("days", plan))
        plan.refresh_from_db()
        self.assertEqual(plan.status, "retirado")  # asked, not published
        self.assertContains(self.client.get(ADMIN), "sigue retirado mientras tanto")
        approve(self.church, "plan", plan)
        self.assertEqual(plan.status, "publicado")
        self.assertGreater(plan.rights_confirmed_at, confirmed_at)
        self.assertEqual(ChurchAuditEvent.objects.filter(action="contenido_aprobado").count(), 2)


class PrivacyTests(PlansFixture):
    def test_progress_is_the_readers_and_nothing_the_administration_sees_comes_from_it(self):
        plan = self.plan(title="Plan sin cifras")
        other = self.plan(title="Otro plan sin cifras")
        day = self.day(plan, 1)
        pages = [LIST, ADMIN, CREATE, PANORAMA, HOME, url("day", plan, 1), url("day_edit", plan, day.pk)] + [
            url(name, item) for item in (plan, other) for name in ("detail", "days", "edit", "day_create", "retire")
        ]

        def seen_by_the_administration():
            self.client.force_login(self.admin)
            found = []
            for address in pages:
                response = self.client.get(address)
                self.assertEqual(response.status_code, 200, address)
                html = re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', "", main(response))
                found.append(re.sub(r"\b\d\d:\d\d(:\d\d)?\b", "HH:MM", html))
            return found

        before = seen_by_the_administration()
        audit_before = ChurchAuditEvent.objects.count()
        self.read(plan, self.member, 1, 2, 3)  # finished
        self.read(plan, self.leader, 1)  # in progress
        self.read(plan, self.asked)  # started, nothing read
        self.read(other, self.member, 2)
        # Three people started, one finished: every page of the administration, and Panorama, is to the letter what it was.
        self.assertEqual(seen_by_the_administration(), before)
        for address, html in zip(pages, before):
            for person in (self.member, self.leader, self.asked):
                self.assertNotIn(person.last_name, html, address)
            for words in ("En curso", "Terminado", "Leído", "empezaron", "terminaron", "lectores", "Ya lo leíste", "racha", "%"):
                self.assertNotIn(words, html, address)
        # Reading leaves no audit row, and the retire question does not say whether anybody is reading.
        self.assertEqual(ChurchAuditEvent.objects.count(), audit_before)
        self.assertFalse(ChurchAuditEvent.objects.filter(object_type__in=["ReadingPlanProgress", "ReadingPlanDayRead"]).exists())

        # Each reader sees their own place and nobody else's.
        self.client.force_login(self.leader_user)
        html = main(self.client.get(LIST))
        self.assertEqual((html.count("En curso"), html.count("Terminado")), (1, 0))
        self.assertIn("Continuar en el día 2", html)
        self.assertEqual(main(self.client.get(url("detail", plan))).count("status--done"), 1)
        self.client.force_login(self.coord1)  # started nothing
        html = main(self.client.get(LIST))
        self.assertEqual((html.count("En curso"), html.count("Terminado")), (0, 0))
        self.assertNotIn("status--done", main(self.client.get(url("detail", plan))))

    def test_the_django_admin_does_not_know_the_tables(self):
        registered = {model.__name__ for model in django_admin.site._registry}
        self.assertFalse(registered & {"ReadingPlanProgress", "ReadingPlanDayRead", "ReadingPlan", "ReadingPlanDay"})

    def test_no_bible_text_and_no_answers_are_stored(self):
        columns = {
            model.__name__: sorted(field.name for field in model._meta.concrete_fields)
            for model in (ReadingPlanDay, ReadingPlanProgress, ReadingPlanDayRead)
        }
        self.assertEqual(columns, {
            "ReadingPlanDay": ["id", "plan", "position", "question", "reference", "reflection", "title"],
            "ReadingPlanProgress": ["id", "person", "plan", "started_at"],
            "ReadingPlanDayRead": ["day", "id", "progress", "read_at"],
        })


class SeedTests(PlansFixture):
    def test_the_plan_of_the_demo_comes_only_with_eventos(self):
        with override_settings(DEBUG=True):
            call_command("seed_community_demo", password="x", stdout=StringIO())
            self.assertFalse(ReadingPlan.objects.exists())
            for _ in range(2):  # twice: one plan
                call_command("seed_community_demo", "--eventos", password="x", stdout=StringIO())
        plan = ReadingPlan.objects.get()
        self.assertEqual((plan.church, plan.status, plan.days.count()), (Church.objects.get(slug="iglesia-demo"), "publicado", 3))
        self.assertIsNotNone(plan.rights_confirmed_at)
        self.assertFalse(ReadingPlanProgress.objects.exists())
