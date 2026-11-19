"""The camp administration on «Ventanas» (ui_v2): what the redesign promises on these screens, beside the module-off
markup the other suites pin byte for byte.

An event whose last day has passed says «Terminó» without its stored status changing; the half-done interviews on the
administration home are discarded by a quiet action whose page asks first; no preparation step wears the vela dot
(vela only blocks somebody); the forms group their fields and keep one filled button; erasing a sede is drawn as
the irreversible action it is.
"""

from __future__ import annotations

import re
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from events.models import Event, Interview, InterviewStatus, Sede
from people import flags
from people.models import Person

from .base import CampFixture
from .test_camperos_admin import main_of
from .test_ux_coordinator import give_interview


class AdministrationOnVentanasTests(CampFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        flags.set_enabled(cls.church, "community", True)
        Person.objects.create(church=cls.church, user=cls.admin, first_name=cls.admin.first_name, last_name=cls.admin.last_name)

    def setUp(self):
        self.client.force_login(self.admin)

    def test_an_event_whose_days_passed_says_it_ended_without_changing_its_status(self):
        today = timezone.localdate()
        Event.objects.filter(pk=self.event.pk).update(status=Event.Status.ACTIVE, starts_on=today - timedelta(days=5), ends_on=today - timedelta(days=3))
        Event.objects.filter(pk=self.other_event.pk).update(status=Event.Status.ACTIVE, starts_on=today, ends_on=today + timedelta(days=1))
        page = main_of(self.client.get(reverse("events:list") + "?todos=1"))
        self.assertIn("Eventos donde sirves", page)
        rows = {re.search(r">(Campamento [AB])</a>", row).group(1): row for row in page.split('<li class="row row--lead">')[1:]}
        self.assertIn("Terminó", rows["Campamento A"])
        self.assertNotIn("En curso", rows["Campamento A"])
        self.assertIn("En curso", rows["Campamento B"])  # today is still inside its days
        self.assertEqual(Event.objects.get(pk=self.event.pk).status, Event.Status.ACTIVE)  # display only
        home = main_of(self.client.get(self.url("admin_home")))
        self.assertIn("Terminó", home.split("</header>", 1)[0])

    def test_half_done_interviews_are_discarded_quietly_and_steps_carry_no_vela_dot(self):
        interview = give_interview(self.p2, InterviewStatus.IN_PROGRESS)
        Interview.objects.filter(pk=interview.pk).update(last_activity_at=timezone.now() - timedelta(hours=3))
        page = main_of(self.client.get(self.url("admin_home")))
        section = re.search(r'<section class="block" aria-labelledby="entrevistas-interrumpidas">.*?</section>', page, re.DOTALL).group(0)
        discard = self.url("interview_discard", interview.public_id)
        self.assertIn(f'<a class="btn btn--link btn--quieto" href="{discard}">Descartar', section)
        self.assertNotIn("btn--danger", section)
        self.assertIn("Al descartar se borran sus respuestas.", section)  # what the action costs stays said
        self.assertNotIn("row--attention", page)
        self.assertNotIn("Llevamos", page)
        self.assertIn('<span class="avance__n" data-count="llevamos">', page)

    def test_the_forms_group_their_fields_and_keep_one_filled_button(self):
        for url in (reverse("events:create"), self.url("participant_create"), self.url("member_add"), self.url("sede_add")):
            with self.subTest(url=url):
                page = main_of(self.client.get(url))
                self.assertIn('class="form-actions"', page)
                self.assertEqual(page.count("btn--primary"), 1)
        record = main_of(self.client.get(self.url("participant_create")))
        self.assertIn("Ya entregó la autorización firmada de su padre, madre o tutor legal", record)  # the guardian's wording, whole

    def test_erasing_an_unused_sede_is_the_irreversible_action_and_asks_first(self):
        sede = Sede.objects.create(church=self.church, name="Sede Vacía", slug="sede-vacia")
        page = main_of(self.client.get(self.url("sede_edit", sede.id)))
        self.assertRegex(page, r'<button class="btn btn--danger-outline" type="submit" data-confirm="¿Borrar la sede Sede Vacía\? No se puede deshacer\.">Borrar la sede</button>')

    def test_the_page_that_erases_a_campero_keeps_its_words_and_needs_no_dark_panel(self):
        page = main_of(self.client.get(self.url("participant_delete", self.p_minor_no_auth.public_id)))
        self.assertIn("Esto no se puede deshacer", page)
        self.assertIn('class="btn btn--danger" type="submit">Eliminar definitivamente', page)
        self.assertNotIn('class="confirm"', page)

    def test_with_the_module_off_none_of_it_is_served(self):
        flags.set_enabled(self.church, "community", False)
        page = main_of(self.client.get(reverse("events:list") + "?todos=1"))
        self.assertNotIn("Eventos donde sirves", page)
        self.assertNotIn("Terminó", page)
        home = main_of(self.client.get(self.url("admin_home")))
        self.assertIn('class="voice admin-state__title"', home)
        self.assertNotIn("admin-pasos", home)
