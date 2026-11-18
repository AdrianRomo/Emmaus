"""The team's camp screens with the redesign on ("Santuario", docs/v2/DESIGN_SYSTEM.md).

Their templates are shared with churches that keep Atrio, so the redesign changes words for both and structure only
inside ``{% if ui_v2 %}``. Every other test of these screens runs with the module off and pins Atrio; these run with
it on, for staff who are people of the church (they wear the shell, the event is a place in the rail), and check:
every screen answers, no paragraph explains a list, and the rows that were rebuilt say the same facts to the same
people as Atrio's.
"""

from __future__ import annotations

import re

from django.urls import reverse
from django.utils import timezone

from events.models import FollowUp, InterviewStatus
from events.services.metrics import event_scope
from events.services.reports import generate_snapshot
from people import flags
from people.models import Person

from .base import CampFixture
from .test_ux_coordinator import give_interview

LEDE = re.compile(r'<p class="lede"[^>]*>(.*?)</p>', re.DOTALL)


def main_of(response) -> str:
    return response.content.decode().split("<main", 1)[1]


class TeamScreensInTheShellTests(CampFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        flags.set_enabled(cls.church, "community", True)
        for user in (cls.admin, cls.pastor, cls.care, cls.tutor1, cls.coord1):
            Person.objects.create(church=cls.church, user=user, first_name=user.first_name, last_name=user.last_name)
        cls.done = give_interview(cls.p2, InterviewStatus.COMPLETED)
        cls.open_follow_up = FollowUp.objects.create(
            event=cls.event, participant=cls.p1, kind=FollowUp.Kind.CARE, reason=FollowUp.Reason.TALK_TO_LEADER, created_by=cls.coord1,
        )
        cls.closed_follow_up = FollowUp.objects.create(
            event=cls.event, participant=cls.p3, kind=FollowUp.Kind.CARE, reason=FollowUp.Reason.TALK_TO_LEADER, created_by=cls.coord2,
            status=FollowUp.Status.DONE, assigned_to=cls.tutor1,
        )
        cls.snapshot = generate_snapshot(cls.event, event_scope(cls.event), trigger="manual", cutoff_at=timezone.now())

    def pages(self):
        u = self.url
        return {
            self.coord1: [
                u("coord_home"), u("my_participants"), u("my_participants") + "?autorizacion=falta", u("coord_pending"), u("coord_progress"),
                u("participant_create"), u("participant_detail", self.p1.public_id), u("participant_edit", self.p1.public_id),
                u("guardian_authorization", self.p_minor_no_auth.public_id), u("guardian_authorization_withdraw", self.p1.public_id),
                u("follow_ups"), u("follow_up_detail", self.open_follow_up.id), u("ayuda"),
            ],
            self.tutor1: [
                u("tutor_home"), u("coordinators"), u("coordinator_detail", self.m_coord1.id), u("interviews"), u("pending"), u("reports"),
                u("follow_ups"), u("ayuda"),
            ],
            self.pastor: [
                u("pastor_home"), u("progress"), u("follow_ups"), u("follow_ups") + "?estado=todos", u("follow_up_detail", self.open_follow_up.id),
                u("team"), u("reports"), u("report_detail", self.snapshot.public_id), u("pending"), u("interviews"), u("coordinators"), u("ayuda"),
            ],
        }

    def test_every_screen_answers_inside_the_shell_with_the_event_as_a_place(self):
        for user, urls in self.pages().items():
            self.client.force_login(user)
            for url in urls:
                with self.subTest(user=user.username, url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, '<header class="rail rail--event')
                    self.assertNotContains(response, '<header class="bar')
                    self.assertEqual(len(re.findall(r"<h1\b", main_of(response))), 1)

    def test_the_station_page_keeps_the_navigation_of_whoever_opens_it(self):
        """Defect: the view passed its own "shell" (a leftover string), which replaced the redesigned shell's data:
        the rail was drawn empty, and a church with the module off got that empty rail instead of Atrio's bar."""
        self.client.force_login(self.coord1)
        page = self.client.get(self.url("choose_station")).content.decode()
        rail = page[page.index('<header class="rail rail--event'):page.index("</header>")]
        self.assertIn('<span class="rail__text">Mis camperos</span>', rail)
        self.assertIn("Tu rol: coordinador.", rail)
        self.assertIn("Carla Uno", rail)
        flags.set_enabled(self.church, "community", False)
        page = self.client.get(self.url("choose_station"))
        self.assertContains(page, '<header class="bar')
        self.assertNotContains(page, 'class="rail')
        self.assertNotContains(page, "page--app")
        self.client.force_login(self.coord2)  # an event role and no person: Atrio too
        self.assertContains(self.client.get(self.url("choose_station")), '<header class="bar')

    def test_the_live_fragments_answer_too(self):
        for user, name in ((self.coord1, "coord_home"), (self.coord1, "coord_progress"), (self.tutor1, "tutor_home"), (self.pastor, "pastor_home"), (self.pastor, "progress")):
            self.client.force_login(user)
            with self.subTest(name=name):
                response = self.client.get(self.url(name), HTTP_X_REQUESTED_WITH="fetch")
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "<html")

    def test_no_screen_explains_itself_under_its_title(self):
        """A sentence under the title stays only where the title cannot say it: who a new campero is assigned to,
        which cutoff this is, and what withdrawing an authorization does (protected wording)."""
        allowed = {
            self.url("participant_create"): "Quedará asignado a ti.",
            self.url("report_detail", self.snapshot.public_id): None,  # the scope and the day of the cutoff
            self.url("guardian_authorization", self.p_minor_no_auth.public_id): None,
            self.url("guardian_authorization_withdraw", self.p1.public_id): None,
        }
        for user, urls in self.pages().items():
            self.client.force_login(user)
            for url in urls:
                with self.subTest(user=user.username, url=url):
                    ledes = [" ".join(text.split()) for text in LEDE.findall(main_of(self.client.get(url)))]
                    if url in allowed:
                        if allowed[url]:
                            self.assertEqual(ledes, [allowed[url]])
                    else:
                        self.assertEqual(ledes, [])

    def test_the_role_is_said_on_each_home_as_well_as_in_the_rail(self):
        for user, name, role in ((self.coord1, "coord_home", "coordinador"), (self.tutor1, "tutor_home", "tutor"), (self.pastor, "pastor_home", "pastor")):
            self.client.force_login(user)
            with self.subTest(role=role):
                html = self.client.get(self.url(name)).content.decode()
                rail, main = html.split("<main", 1)
                self.assertIn(f"Tu rol: {role}.", rail)
                self.assertIn(f"Tu rol: {role}", main)  # a phone has no rail: the page keeps saying it

    def test_a_follow_up_row_says_who_what_since_when_and_who_attends_it(self):
        self.client.force_login(self.pastor)
        html = main_of(self.client.get(self.url("follow_ups") + "?estado=todos"))
        self.assertIn('<section class="results results--wide" aria-label="Seguimientos">', html)
        self.assertLess(html.index('<ul class="filters" aria-label="Estados">'), html.index('class="rows"'))  # the filters come first
        rows = html.split('<li class="row">')[1:]
        self.assertEqual(len(rows), 2)
        by_name = {name: row for row in rows for name in (self.p1.full_name, self.p3.full_name) if f">{name}</a>" in row}
        open_row, closed_row = by_name[self.p1.full_name], by_name[self.p3.full_name]
        for row, coordinator in ((open_row, "Carla Uno"), (closed_row, "César Dos")):
            self.assertIn("<li>Pidió hablar con un líder</li>", row)
            self.assertIn(f"{coordinator}</li>", row)
            self.assertNotIn('<p class="row__meta">', row)
        # An open one this person may assign: the selector says who attends it, once
        self.assertIn(f'<select class="input input--small" id="asignar-{self.open_follow_up.id}" name="assigned_to"><option value="">Sin asignar</option>', open_row)
        self.assertNotIn("Responsable:", open_row)
        self.assertIn("Pendiente", open_row)
        # A closed one has no selector: the fact says it
        self.assertNotIn("<select", closed_row)
        self.assertIn("Responsable: Teresa Uno</li>", closed_row)
        self.assertIn("Atendido", closed_row)

    def test_a_coordinator_reads_the_same_facts_and_never_another_coordinator_s_follow_ups(self):
        self.client.force_login(self.coord1)
        html = main_of(self.client.get(self.url("follow_ups") + "?estado=todos"))
        self.assertIn(self.p1.full_name, html)
        self.assertNotIn(self.p3.full_name, html)  # César's campero

    def test_a_campero_row_is_one_line_of_facts(self):
        self.client.force_login(self.coord1)
        html = main_of(self.client.get(self.url("my_participants")))
        self.assertNotIn('<p class="row__meta">', html)
        row = html[html.index(f">{self.p1.full_name}</a>"):]
        row = row[:row.index("</li>\n")]
        facts = [re.sub(r"<svg.*?</svg>", "", fact, flags=re.DOTALL).strip() for fact in re.findall(r"<li>(.*?)</li>", row, re.DOTALL)]
        self.assertEqual(facts, [f"#{self.p1.code}", "Cuarto 1", "15 años", "Iglesia Centro"])

    def test_the_pastor_home_draws_the_day_and_the_role_as_facts(self):
        self.client.force_login(self.pastor)
        html = main_of(self.client.get(self.url("pastor_home")))
        self.assertIn('<ul class="meta report-head__meta">', html)
        self.assertIn('<li class="own-role">Tu rol: pastor</li>', html)
        self.assertNotIn('<p class="report-head__meta">', html)
        self.assertContains(self.client.get(self.url("reports")), '<ul class="meta report-head__meta">')

    def test_what_is_protected_reads_the_same_in_the_shell(self):
        self.client.force_login(self.coord1)
        page = self.client.get(self.url("guardian_authorization", self.p_minor_no_auth.public_id))
        self.assertContains(page, "Confirmas que la autorización firmada del padre, la madre o el tutor legal de Lucía Torres Gómez está en manos de la iglesia.")
        self.assertContains(page, "Confírmalo solo si viste el formato firmado.")
        withdraw = self.client.get(self.url("guardian_authorization_withdraw", self.p1.public_id))
        self.assertContains(withdraw, "Las entrevistas ya completadas no cambian.")
        home = self.client.get(self.url("coord_home"))
        self.assertContains(home, "Sin la autorización de su padre, madre o tutor legal no se le puede entrevistar.")
        self.client.force_login(self.pastor)
        self.assertContains(self.client.get(self.url("pastor_home")), "Quién lee las respuestas confidenciales:")
        self.assertContains(self.client.get(self.url("report_detail", self.snapshot.public_id)), "Corte guardado: no cambia.")
        self.assertContains(self.client.get(reverse("events:follow_up_detail", args=[self.event.slug, self.open_follow_up.id])), "Seguimiento: Mariana López Ruiz")
