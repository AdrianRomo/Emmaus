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

from events.models import FollowUp, Interview, InterviewStatus
from events.services.metrics import event_scope
from events.services.reports import generate_snapshot
from people import flags
from people.models import Person

from .base import CampFixture
from .test_ux_coordinator import give_interview

LEDE = re.compile(r'<p class="lede"[^>]*>(.*?)</p>', re.DOTALL)


def main_of(response) -> str:
    return response.content.decode().split("<main", 1)[1]


def row_of(html: str, name: str) -> str:
    """The one row of a list that names this person."""
    rows = [row.split("</li>")[0] for row in html.split('<li class="row row--lead')[1:] if f">{name}</a>" in row.split("</li>")[0]]
    assert len(rows) == 1, (name, len(rows))
    return rows[0]


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
        rows = html.split('<li class="row row--lead" id="seguimiento-')[1:]
        self.assertEqual(len(rows), 2)
        by_name = {name: row for row in rows for name in (self.p1.full_name, self.p3.full_name) if f">{name}</a>" in row}
        open_row, closed_row = by_name[self.p1.full_name], by_name[self.p3.full_name]
        for row, coordinator in ((open_row, "Carla Uno"), (closed_row, "César Dos")):
            self.assertIn("<li>Pidió hablar con un líder</li>", row)
            self.assertIn(f"{coordinator}</li>", row)
            self.assertNotIn('<p class="row__meta">', row)
        # An open one this person may assign: the selector says who attends it, once, and acts in place on its own row
        self.assertIn(f'<select class="input input--small" id="asignar-{self.open_follow_up.id}" name="assigned_to" data-inline-focus><option value="">Sin asignar</option>', open_row)
        self.assertIn(f'class="row-assign" data-inline="seguimiento-{self.open_follow_up.id}">', open_row)
        self.assertIn('<input type="hidden" name="siguiente" value="/eventos/campamento-a/seguimiento/?estado=todos">', open_row)
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

    def test_a_campero_row_reads_like_the_home_queue(self):
        """The search capsule and the views as capsules, as on the administration's Camperos; a row is who (circle,
        name), the interview's state with its mark, the number and the cuarto, and the one action."""
        self.client.force_login(self.coord1)
        html = main_of(self.client.get(self.url("my_participants")))
        self.assertIn('<form class="search camperos__buscar" method="get" role="search">', html)
        self.assertIn('<input class="input search__input" id="q" type="search" name="q" value=""', html)
        self.assertIn('<ul class="filters camperos__vistas" aria-label="Mostrar" data-desliza>', html)
        self.assertNotIn("list-search", html)
        row = row_of(html, self.p1.full_name)
        self.assertIn('<span class="mono" aria-hidden="true">ML</span>', row)
        self.assertIn(f'<p class="row__meta row__meta--con-marca"><span class="status status--pending"><span class="status__mark" aria-hidden="true"></span>Sin entrevista</span><span>#{self.p1.code}, Cuarto 1, 15 años</span>', row)
        self.assertIn(">Entrevistar<span", row)
        done = row_of(html, self.p2.full_name)  # completed: the row is the way to the record, no action
        self.assertIn("Completada</span>", done)
        self.assertNotIn("<a class=\"btn", done)

    def test_a_minors_authorization_is_its_own_sign(self):
        """A guardian's authorization is a check with its word, or vela «Falta la autorización»; never the interview's
        mark. An adult's row says nothing about it. «Menores sin autorización» is a capsule with its count."""
        self.client.force_login(self.coord1)
        html = main_of(self.client.get(self.url("my_participants")))
        authorized, missing, adult = (row_of(html, p.full_name) for p in (self.p1, self.p_minor_no_auth, self.p2))
        self.assertIn('<span class="autorizada">', authorized)
        self.assertIn("Autorizada</span>", authorized)
        self.assertNotIn("chip--wait", authorized)
        self.assertIn('<span class="chip chip--wait">Falta la autorización</span>', missing)
        self.assertIn("status status--pending", missing)  # the interview's own state, apart
        self.assertNotIn("autorizada", adult)
        self.assertNotIn("chip--wait", adult)
        capsule = f'<a href="{self.url("my_participants")}?autorizacion=falta">Menores sin autorización <span class="count">1</span></a>'
        self.assertIn(capsule, html)
        only = main_of(self.client.get(self.url("my_participants") + "?autorizacion=falta"))
        self.assertIn(f'<a href="{self.url("my_participants")}?autorizacion=falta" aria-current="page">Menores sin autorización', only)
        self.assertIn(self.p_minor_no_auth.full_name, only)
        self.assertNotIn(self.p1.full_name, only)
        self.assertIn('<input type="hidden" name="autorizacion" value="falta">', only)  # the search keeps the view

    def test_a_search_that_finds_nobody_offers_to_clear_it(self):
        self.client.force_login(self.coord1)
        html = main_of(self.client.get(self.url("my_participants") + "?q=zzzz"))
        self.assertIn('<p class="empty__title">Nadie coincide</p>', html)
        self.assertIn(f'<a class="btn btn--secondary" href="{self.url("my_participants")}">Quitar filtros</a>', html)
        lucia = main_of(self.client.get(self.url("my_participants") + "?q=luc"))
        self.assertIn(self.p_minor_no_auth.full_name, lucia)
        self.assertNotIn(self.p1.full_name, lucia)

    def test_a_situacion_de_riesgo_is_said_in_chile_and_never_softened(self):
        safety = FollowUp.objects.create(
            event=self.event, participant=self.p1, kind=FollowUp.Kind.SAFETY, reason=FollowUp.Reason.SAFETY, created_by=self.coord1,
        )
        self.client.force_login(self.care)  # sensitive access: the one who may see it
        html = main_of(self.client.get(self.url("follow_ups")))
        row = html.split(f'id="seguimiento-{safety.id}"')[1].split('id="seguimiento-')[0]
        self.assertIn('<span class="status status--risk"><span class="status__mark" aria-hidden="true"></span>Situación de riesgo</span>', row)
        self.assertNotIn("Urgente", row)
        detail = main_of(self.client.get(self.url("follow_up_detail", safety.id)))
        self.assertIn("Situación de riesgo: Mariana López Ruiz", detail)
        self.assertIn('<span class="status status--risk"><span class="status__mark" aria-hidden="true"></span>Situación de riesgo</span>', detail)
        self.assertIn("Sigue el procedimiento de protección", detail)
        home = main_of(self.client.get(self.url("pastor_home")))
        self.assertIn('<a class="decision decision--riesgo" href="/eventos/campamento-a/seguimiento/">', home)
        self.assertIn("Situación de riesgo abierta</span>", home)
        self.client.force_login(self.pastor)  # without sensitive access the home does not count it
        self.assertNotIn("decision--riesgo", main_of(self.client.get(self.url("pastor_home"))))

    def test_the_tutor_and_pastor_homes_are_built_as_the_coordinator_s(self):
        """The event and its dates as the title, the role only where the rail is hidden (.solo-sin-riel), what waits
        for a decision as rows with their count, the list and the progress as on the coordinator's "Mi inicio"."""
        for user, name, role in ((self.tutor1, "tutor_home", "tutor"), (self.pastor, "pastor_home", "pastor")):
            self.client.force_login(user)
            with self.subTest(role=role):
                html = main_of(self.client.get(self.url(name)))
                self.assertIn('<h1 class="voice page-head__title">Campamento A</h1>', html)
                self.assertIn(f'<p class="page-head__meta solo-sin-riel">Tu rol: {role}.</p>', html)
                self.assertIn('<h2 id="esperan" class="heading">Esperan tu decisión</h2>', html)
                self.assertIn('class="block tablero__avance"', html)
                self.assertNotIn("report-head", html)
        self.client.force_login(self.tutor1)
        tutor = main_of(self.client.get(self.url("tutor_home")))
        self.assertIn(f'<a href="{self.url("coordinator_detail", self.m_coord1.id)}">Carla Uno</a>', tutor)  # its coordinators
        self.client.force_login(self.pastor)
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


class DecidingInPlaceTests(CampFixture):
    """«Por decidir» on «Entrevistas pendientes» answers in place (form[data-inline]): the decision of the interview's
    record, back to the same list (same site only) with the next row in focus, and a message that names the campero
    and the answer. Opening the record and deciding there goes on to the next record."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        flags.set_enabled(cls.church, "community", True)
        Person.objects.create(church=cls.church, user=cls.coord1, first_name="Carla", last_name="Uno")
        cls.newer = give_interview(cls.p1, InterviewStatus.COMPLETED, decision="por_evaluar", minutes_ago=10)
        cls.older = give_interview(cls.p2, InterviewStatus.COMPLETED, decision="por_evaluar", minutes_ago=30)
        cls.theirs = give_interview(cls.p3, InterviewStatus.COMPLETED, decision="por_evaluar")  # César's campero

    def setUp(self):
        self.client.force_login(self.coord1)
        self.pending = self.url("coord_pending")

    def decide(self, interview, decision="no_requerido", siguiente=None):
        data = {"entrevista": str(interview.public_id), "decision": decision}
        data["siguiente"] = f"{self.pending}?despues={interview.public_id}#por-evaluar" if siguiente is None else siguiente
        return self.client.post(self.pending, data)

    def test_each_row_leads_to_the_answers_before_deciding(self):
        """Whether someone needs follow-up is judged from what they answered: the row's «Decidir» opens the interview at
        its decision (and deciding there goes on to the next one); the list itself decides nothing."""
        html = main_of(self.client.get(self.pending))
        section = html.split('id="por-evaluar">')[1].split("</section>")[0]
        self.assertNotIn('data-inline="por-evaluar"', section)
        self.assertNotIn('name="decision"', section)
        record = self.url("interview_detail", self.newer.public_id)
        self.assertIn(f'href="{record}?siguiente=', section)
        self.assertIn('>Decidir<span class="visually-hidden"> si Mariana López Ruiz necesita seguimiento</span></a>', section)
        self.assertEqual(section.count("btn--primary"), 1)  # the first row's, the next to decide
        self.assertLess(section.index(self.p1.full_name), section.index(self.p2.full_name))  # the newest first
        self.assertNotIn(self.p3.full_name, section)

    def test_a_decision_comes_back_to_the_list_with_the_next_row_in_focus_and_says_who_and_what(self):
        response = self.decide(self.newer)
        self.assertRedirects(response, f"{self.pending}?despues={self.newer.public_id}#por-evaluar", fetch_redirect_response=False)
        self.newer.refresh_from_db()
        self.assertEqual(self.newer.follow_up_decision, "no_requerido")
        page = self.client.get(response["Location"])
        self.assertEqual([m.message for m in page.context["messages"]], ["Mariana López Ruiz no necesita seguimiento."])
        section = main_of(page).split('id="por-evaluar">')[1].split("</section>")[0]
        self.assertNotIn(self.p1.full_name, section)
        self.assertIn(f'#seguimiento" data-inline-focus>{self.p2.full_name}</a>', section)

    def test_yes_says_so_and_opens_the_follow_up(self):
        self.decide(self.older, decision="requerido")
        page = self.client.get(self.pending)
        self.assertEqual([m.message for m in page.context["messages"]], ["Diego Hernández Cruz necesita seguimiento."])
        self.older.refresh_from_db()
        self.assertEqual(self.older.follow_up_decision, "requerido")
        self.assertTrue(FollowUp.objects.filter(interview=self.older, kind=FollowUp.Kind.CARE).exists())

    def test_the_way_back_is_this_site_only(self):
        for foreign in ("https://malo.example/", "//malo.example/x", "/\\malo.example", "javascript:alert(1)"):
            Interview.objects.filter(pk=self.newer.pk).update(follow_up_decision="por_evaluar")
            with self.subTest(siguiente=foreign):
                response = self.decide(self.newer, siguiente=foreign)
                self.assertRedirects(response, f"{self.pending}#por-evaluar", fetch_redirect_response=False)

    def test_another_coordinator_s_interview_cannot_be_decided_from_here(self):
        self.assertEqual(self.decide(self.theirs).status_code, 404)
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.follow_up_decision, "por_evaluar")
        self.assertEqual(self.client.post(self.pending, {"entrevista": "1 OR 1=1", "decision": "no_requerido"}).status_code, 403)
        self.client.force_login(self.tutor1)  # the page is the coordinator's own
        self.assertEqual(self.decide(self.newer).status_code, 403)
        self.newer.refresh_from_db()
        self.assertEqual(self.newer.follow_up_decision, "por_evaluar")

    def test_an_answer_already_given_is_refused_where_it_was_asked(self):
        self.decide(self.newer)
        self.client.get(self.pending)  # its message is read
        response = self.decide(self.newer, decision="requerido")
        page = self.client.get(response["Location"])
        self.assertEqual([m.message for m in page.context["messages"]], ["El seguimiento de esta entrevista ya se decidió."])
        self.newer.refresh_from_db()
        self.assertEqual(self.newer.follow_up_decision, "no_requerido")

    def test_deciding_on_the_record_goes_on_to_the_next_record(self):
        html = main_of(self.client.get(self.pending))
        record = self.url("interview_detail", self.newer.public_id)
        self.assertIn(f'href="{record}?siguiente=%2Feventos%2Fcampamento-a%2Fpendientes%2Fmias%2F%3Fdecidir%3Dsiguiente%26despues%3D{self.newer.public_id}#seguimiento"', html)
        self.client.post(record, {"action": "decision", "decision": "no_requerido"})
        after = self.client.get(self.pending, {"decidir": "siguiente", "despues": str(self.newer.public_id)})
        self.assertEqual(after.status_code, 302)
        self.assertTrue(after["Location"].startswith(self.url("interview_detail", self.older.public_id) + "?siguiente="))
        self.assertTrue(after["Location"].endswith("#seguimiento"))
        self.client.post(self.url("interview_detail", self.older.public_id), {"action": "decision", "decision": "no_requerido"})
        done = self.client.get(self.pending, {"decidir": "siguiente", "despues": str(self.older.public_id)})
        self.assertRedirects(done, f"{self.pending}#por-evaluar", fetch_redirect_response=False)
        # Another coordinator's interview as "despues" names nothing: the list's own first one
        third = give_interview(self.p_minor_no_auth, InterviewStatus.COMPLETED, decision="por_evaluar")
        foreign = self.client.get(self.pending, {"decidir": "siguiente", "despues": str(self.theirs.public_id)})
        self.assertTrue(foreign["Location"].startswith(self.url("interview_detail", third.public_id)))


class ChoosingAComputerTests(CampFixture):
    """«¿En qué computadora estás?»: one question, the computers as capsules, the one in use already chosen, a computer
    where someone else has an interview moving now marked «En uso» (never who)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        flags.set_enabled(cls.church, "community", True)
        Person.objects.create(church=cls.church, user=cls.coord1, first_name="Carla", last_name="Uno")

    def interview_at(self, participant, number, *, minutes_ago=1):
        interview = give_interview(participant, InterviewStatus.IN_PROGRESS, minutes_ago=minutes_ago)
        interview.station = self.event.stations.get(number=number)
        interview.save(update_fields=["station"])
        return interview

    def label(self, html, number):
        station = self.event.stations.get(number=number)
        return html.split(f'value="{station.id}"')[1].split("</label>")[0]

    def test_the_computers_are_capsules_and_someone_else_s_is_marked(self):
        self.interview_at(self.p3, 2)  # César, now
        self.interview_at(self.p4, 4, minutes_ago=90)  # Cora, stalled: not in use
        self.interview_at(self.p1, 3)  # Carla herself
        self.client.force_login(self.coord1)
        html = main_of(self.client.get(self.url("choose_station")))
        self.assertIn('<h1 class="voice page-head__title">¿En qué computadora estás?</h1>', html)
        self.assertIn("En uso</span>", self.label(html, 2))
        for number in (1, 3, 4):
            self.assertNotIn("En uso", self.label(html, number))
        self.assertNotIn("César", html)
        self.assertNotIn(" checked", html)
        self.assertEqual(html.count('class="btn btn--primary"'), 1)

    def test_the_computer_this_browser_uses_is_already_chosen(self):
        self.client.force_login(self.coord1)
        self.choose_station(number=5)
        html = main_of(self.client.get(self.url("choose_station")))
        self.assertIn(" checked><label", self.label(html, 5))
        self.assertEqual(html.count(" checked"), 1)
