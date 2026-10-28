"""How many of the event's N questionnaires each campero (and each scope) completed, on every campero- and team-level
screen: the shared label rule, Progress.partial / Progress.questionnaires, the lists' status words, Mi inicio, Mi avance,
Entrevistas pendientes, Pendientes, the campero's record, the start, done and kiosk screens, the exports and Cuestionarios.

Owner's ask (2026-10-28): "show how many of the N questionnaires are filled, so the data is clear for everyone at every
level." With one questionnaire nothing changes (the pinned tests in test_multi_questionnaires_flow/_admin say so).

Each test names the defect it would catch.
"""

from __future__ import annotations

import csv
import io
import json
import re

from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import Client, SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from events.models import EventParticipant, Interview
from events.services.event_questionnaires import refresh_interview_statuses, stop_using, use_version
from events.services.metrics import (
    DEFINITIONS,
    SEVERAL_QUESTIONNAIRE_DEFINITIONS,
    Progress,
    build_payload,
    coordinator_scope,
    event_scope,
    group_progress,
    progress_for,
)
from events.services.questionnaire import create_questionnaire_from_definition
from events.templatetags.emmaus import campero_progress_label

from .base import CampFixture
from .test_camp_visual import css_rules, stylesheet
from .test_multi_questionnaires import IsolatedCacheMixin, ServiceHelpers, TwoQuestionnairesFixture, definition

PILL = '<span class="status status--{}"><span class="status__mark" aria-hidden="true"></span>{}</span>'
START_ADULT = {"mode": "asistida", "consent_consent": "on"}


class ProgressFixture(TwoQuestionnairesFixture):
    """Event A uses «Primer cuestionario» (q1, version ``first``) then «Segundo cuestionario» (q2, ``second``); rows carry the version's title. Camperos: coord1 has p1, p2 and
    p_minor_no_auth; coord2 has p3; coord3 has p4. Tutor1's team is coord1 + coord2, tutor2's is coord3."""

    def interview(self, participant, version, status=Interview.Status.COMPLETED):
        """An interview written straight to the database, then every campero's status derived from the interviews."""
        done = status == Interview.Status.COMPLETED
        item = Interview.objects.create(
            event=self.event, participant=participant, version=version, status=status, completed_at=timezone.now() if done else None
        )
        refresh_interview_statuses(self.event)
        return item

    def halfway_and_live(self):
        """p1 completed Primero ("a medias"); p2 completed Primero and is answering Segundo ("en curso"); p4 completed
        both ("completada"); p3 and p_minor_no_auth have nothing."""
        self.interview(self.p1, self.first)
        self.interview(self.p2, self.first)
        self.interview(self.p2, self.second, Interview.Status.IN_PROGRESS)
        self.interview(self.p4, self.first)
        self.interview(self.p4, self.second)

    def page(self, name, *args, user=None, **params):
        client = self.login(user or self.coord1, Client())
        response = client.get(self.url(name, *args), params)
        self.assertEqual(response.status_code, 200, name)
        return response.content.decode()


# ---- 1. The label rule ------------------------------------------------------------------------------------------------


class LabelRuleTests(SimpleTestCase):
    def test_every_state_says_k_of_n_with_several_and_nothing_with_one(self):
        """Defect: "Sin entrevista" for a campero of a two-questionnaire event (the N hidden until the first one is done),
        "Completada" with no N, or a label invented for a one-questionnaire event."""
        self.assertEqual(campero_progress_label("pendiente", 0, 2), "0 de 2 cuestionarios")
        self.assertEqual(campero_progress_label("pendiente", 1, 2), "1 de 2 cuestionarios")
        self.assertEqual(campero_progress_label("en_curso", 1, 2), "En curso, 1 de 2")
        self.assertEqual(campero_progress_label("completada", 2, 2), "2 de 2 cuestionarios")
        for status in ("pendiente", "en_curso", "completada"):
            with self.subTest(status=status):
                self.assertIsNone(campero_progress_label(status, 0, 1))
                self.assertIsNone(campero_progress_label(status, 1, 1))
                self.assertIsNone(campero_progress_label(status, 0, 0))
        self.assertIsNone(campero_progress_label("pendiente", None, None))  # an annotation that is missing


class OneRuleTests(ProgressFixture):
    def cells(self):
        """{name: (CSV cell, printed list cell, pill words on the admin list)} for every active campero."""
        client = self.login(self.admin, Client())
        exported = client.get(self.url("participant_export")).content.decode("utf-8-sig")
        csv_cells = {row[1]: row[7] for row in csv.reader(io.StringIO(exported))}
        printed = client.get(self.url("backup_list")).content.decode()
        listed = client.get(self.url("participants")).content.decode()
        result = {}
        for p in EventParticipant.objects.filter(event=self.event, is_active=True):
            row = re.search(rf"<tr><td class=\"num\">{p.code}</td><td>{p.full_name}</td>(.*?)</tr>", printed).group(1)
            printed_cell = re.findall(r"<td[^>]*>(.*?)</td>", row)[3]
            item = next(li for li in re.findall(r'<li class="row">(.*?)</li>', listed, re.S) if f">{p.full_name}</a>" in li)
            pill = re.search(r'status__mark" aria-hidden="true"></span>([^<]*)</span>', item).group(1)
            result[p.full_name] = (csv_cells[p.full_name], printed_cell, pill)
        return result

    def test_the_csv_the_printed_list_and_the_pill_say_the_same(self):
        """Defect: three private copies of the rule (CSV, paper list, pill) drifting apart, e.g. the paper list saying
        "Sin entrevista" where the screen says "0 de 2 cuestionarios"."""
        self.halfway_and_live()
        expected = {
            self.p1.full_name: "1 de 2 cuestionarios",
            self.p2.full_name: "En curso, 1 de 2",
            self.p3.full_name: "0 de 2 cuestionarios",
            self.p4.full_name: "2 de 2 cuestionarios",
            self.p_minor_no_auth.full_name: "0 de 2 cuestionarios",
        }
        self.assertEqual(self.cells(), {name: (label, label, label) for name, label in expected.items()})

    def test_the_pill_keeps_the_mark_of_its_state(self):
        """Defect: the new words carried by the wrong mark (a completed campero with the pending outline)."""
        self.halfway_and_live()
        html = self.page("participants", user=self.admin)
        self.assertIn(PILL.format("done", "2 de 2 cuestionarios"), html)
        self.assertIn(PILL.format("progress", "En curso, 1 de 2"), html)
        self.assertIn(PILL.format("pending", "1 de 2 cuestionarios"), html)
        self.assertIn(PILL.format("pending", "0 de 2 cuestionarios"), html)


class OneQuestionnaireRuleTests(IsolatedCacheMixin, CampFixture):
    def test_the_csv_and_the_printed_list_keep_the_status_words(self):
        """Defect: a one-questionnaire event's CSV or paper list saying "0 de 1 cuestionarios"."""
        client = self.login(self.admin, Client())
        exported = client.get(self.url("participant_export")).content.decode("utf-8-sig")
        self.assertEqual({row[7] for row in list(csv.reader(io.StringIO(exported)))[1:]}, {"Sin entrevista"})
        printed = client.get(self.url("backup_list")).content.decode()
        self.assertIn("<td>Sin entrevista</td>", printed)
        self.assertNotIn("cuestionarios", printed.split('id="cuestionario"')[0])


# ---- 2. Progress: partial, not_started, questionnaires --------------------------------------------------------------


class ProgressForTests(ProgressFixture):
    def scope_participants(self):
        return EventParticipant.objects.filter(event=self.event, is_active=True)

    def test_partial_and_per_questionnaire_rows(self):
        """Defect: a campero who answered one of two questionnaires counted as "sin iniciar", or no way to tell how many
        camperos completed each questionnaire."""
        self.halfway_and_live()
        progress = progress_for(self.scope_participants(), self.event)
        self.assertEqual(
            (progress.eligible, progress.completed, progress.in_progress, progress.partial, progress.not_started, progress.pending),
            (5, 1, 1, 1, 2, 4),
        )
        self.assertEqual(
            progress.questionnaires,
            [
                {"id": self.q1.id, "title": "Primer cuestionario", "order": 1, "eligible": 5, "completed": 3, "in_progress": 0, "percent": 60},
                {"id": self.q2.id, "title": "Segundo cuestionario", "order": 2, "eligible": 5, "completed": 1, "in_progress": 1, "percent": 20},
            ],
        )

    def test_rows_follow_the_events_order(self):
        """Defect: the rows in creation order after "Subir" moved the second questionnaire first."""
        from events.services.event_questionnaires import move

        move(self.event, self.q2, "subir")
        rows = progress_for(self.scope_participants(), self.event).questionnaires
        self.assertEqual([(row["id"], row["order"]) for row in rows], [(self.q2.id, 1), (self.q1.id, 2)])

    def test_only_this_scope_active_camperos_and_kept_interviews_count(self):
        """Defect: a withdrawn campero, a discarded interview, or another coordinador's campero counted in a scope."""
        self.interview(self.p1, self.first)
        self.interview(self.p3, self.first)  # coord2's
        discarded = self.interview(self.p_minor_no_auth, self.first)
        Interview.objects.filter(pk=discarded.pk).update(status=Interview.Status.DISCARDED)
        refresh_interview_statuses(self.event)
        EventParticipant.objects.filter(pk=self.p1.pk).update(is_active=False)
        scope = coordinator_scope(self.event, self.m_coord1)
        progress = progress_for(scope.participants(), self.event)
        self.assertEqual((progress.eligible, progress.partial, progress.not_started), (2, 0, 2))
        self.assertEqual([row["completed"] for row in progress.questionnaires], [0, 0])

    def test_a_questionnaire_the_event_stopped_using_makes_no_one_halfway(self):
        """Defect: "a medias" or a row counting answers to a questionnaire the event no longer uses."""
        third = create_questionnaire_from_definition(self.church, definition("Tercero", "Tercer cuestionario", "tres"))
        use_version(self.event, third)
        self.interview(self.p3, third)
        stop_using(self.event, third.questionnaire)  # the event keeps Primero and Segundo
        progress = progress_for(self.scope_participants(), self.event)
        self.assertEqual(self.fresh(self.p3).interview_status, "pendiente")
        self.assertEqual(progress.partial, 0)
        self.assertEqual([(row["id"], row["completed"]) for row in progress.questionnaires], [(self.q1.id, 0), (self.q2.id, 0)])

    def test_one_questionnaire_or_no_event_gives_zero_and_no_rows(self):
        """Defect: a one-questionnaire event growing a "Por cuestionario" block, or partial counted without the event."""
        self.interview(self.p1, self.first)
        self.assertEqual((progress_for(self.scope_participants()).partial, progress_for(self.scope_participants()).questionnaires), (0, []))
        self.event.questionnaire_links.filter(questionnaire=self.q2).delete()
        refresh_interview_statuses(self.event)
        progress = progress_for(self.scope_participants(), self.event)
        self.assertEqual((progress.partial, progress.questionnaires, progress.completed), (0, [], 1))

    def test_three_queries_more_at_most(self):
        """Defect: a query per questionnaire or per campero, which grows with the event."""
        self.halfway_and_live()
        participants = self.scope_participants()
        with self.assertNumQueries(1):
            progress_for(participants)
        with self.assertNumQueries(4):
            progress_for(participants, self.event)

    def test_as_dict_carries_both_keys_and_old_ones_unchanged(self):
        """Defect: a payload without the new keys (the report has nothing to show), or the old keys reordered/renamed."""
        self.assertEqual(
            list(Progress().as_dict()),
            ["eligible", "completed", "in_progress", "partial", "questionnaires", "pending", "not_started", "percent"],
        )
        self.assertEqual(Progress(10, 2, 3, 4).as_dict()["not_started"], 1)
        self.assertEqual(Progress(10, 2, 3, 9).not_started, 0)  # never negative

    def test_group_progress_carries_both_per_coordinator(self):
        """Defect: the team and coordinator rows of a report without "a medias" or the per-questionnaire counts."""
        self.halfway_and_live()
        groups = group_progress(self.scope_participants(), "coordinator", self.event)
        coord1, coord2, coord3 = (groups[m.id] for m in (self.m_coord1, self.m_coord2, self.m_coord3))
        self.assertEqual((coord1.eligible, coord1.in_progress, coord1.partial, coord1.not_started), (3, 1, 1, 1))
        self.assertEqual([(r["completed"], r["in_progress"]) for r in coord1.questionnaires], [(2, 0), (0, 1)])
        self.assertEqual((coord2.partial, coord2.not_started, [r["completed"] for r in coord2.questionnaires]), (0, 1, [0, 0]))
        self.assertEqual((coord3.completed, [r["completed"] for r in coord3.questionnaires]), (1, [1, 1]))
        self.assertEqual([r["eligible"] for r in coord1.questionnaires], [3, 3])
        self.assertEqual(groups.of(-1).questionnaires[0]["eligible"], 0)  # a group without camperos still has its rows

    def test_the_payload_carries_them_for_the_event_its_teams_and_coordinators(self):
        """Defect: progress_for/group_progress called without the event by build_payload, so the saved cutoffs and
        the reports never see the per-questionnaire counts."""
        self.halfway_and_live()
        payload = build_payload(event_scope(self.event))
        self.assertEqual((payload["progress"]["partial"], payload["progress"]["not_started"]), (1, 2))
        self.assertEqual([r["completed"] for r in payload["progress"]["questionnaires"]], [3, 1])
        teams = {team["key"]: team for team in payload["teams"]}
        north = teams[f"equipo:{self.m_tutor1.id}"]
        self.assertEqual((north["eligible"], north["partial"], [r["completed"] for r in north["questionnaires"]]), (4, 1, [2, 0]))
        south = teams[f"equipo:{self.m_tutor2.id}"]
        self.assertEqual((south["partial"], [r["completed"] for r in south["questionnaires"]]), (0, [1, 1]))
        coordinators = {row["id"]: row for row in payload["coordinators"]}
        self.assertEqual(coordinators[self.m_coord1.id]["partial"], 1)
        self.assertEqual(len(coordinators[self.m_coord3.id]["questionnaires"]), 2)
        json.dumps(payload)  # a cutoff stores it as JSON

    def test_the_glossary_defines_both(self):
        """Defect: the PDF's "Cómo se calcula" printing numbers it never defines. They live apart from DEFINITIONS so a
        one-questionnaire PDF keeps its glossary (test_progreso_agregados checks both PDFs)."""
        self.assertEqual([name for name, _ in SEVERAL_QUESTIONNAIRE_DEFINITIONS], ["A medias", "Por cuestionario"])
        self.assertFalse({"A medias", "Por cuestionario"} & {name for name, _ in DEFINITIONS})


# ---- 3. The partial "Por cuestionario" -----------------------------------------------------------------------------


class QuestionnaireProgressPartialTests(SimpleTestCase):
    ROWS = [
        {"id": 1, "title": "Primero", "order": 1, "eligible": 4, "completed": 3, "in_progress": 0, "percent": 75},
        {"id": 2, "title": "Segundo", "order": 2, "eligible": 4, "completed": 1, "in_progress": 2, "percent": 25},
    ]

    def render(self, progress, **extra):
        return render_to_string("events/rotulo/_questionnaire_progress.html", {"progress": progress, **extra})

    def test_an_object_and_a_payload_dict_render_the_same(self):
        """Defect: the partial working with the live Progress object but not with a saved cutoff's dict (or the reverse)."""
        progress = Progress(4, 1, 2, 1, self.ROWS)
        html = self.render(progress)
        self.assertEqual(html, self.render(progress.as_dict()))
        self.assertIn('<p class="quests__text">«Primero»: 3 de 4.</p>', html)
        self.assertIn('<p class="quests__text">«Segundo»: 1 de 4, 2 en curso.</p>', html)
        self.assertEqual(html.count('class="ruler"'), 2)
        self.assertIn('<h3 id="por-cuestionario" class="heading quests__title">Por cuestionario</h3>', html)

    def test_an_old_cutoff_without_the_key_renders_no_row(self):
        """Defect: a cutoff saved before this change (no "questionnaires") raising or printing junk."""
        html = self.render({"eligible": 4, "completed": 1})
        self.assertNotIn("quests__row", html)

    def test_a_second_copy_takes_its_own_id(self):
        """Defect: two "Por cuestionario" blocks on one page sharing an id."""
        html = self.render(Progress(4, 1, 0, 0, self.ROWS), quests_id="por-cuestionario-equipo")
        self.assertIn('aria-labelledby="por-cuestionario-equipo"', html)
        self.assertIn('<h3 id="por-cuestionario-equipo"', html)

    def test_the_row_wraps_on_a_phone(self):
        """Defect (layout): a long questionnaire title on one unbreakable line widening a phone's page. The deck's rows are
        one line on purpose (Q2 fix, D): inside the slide, whose frame hides what overflows, a long title ends in "…"."""
        rules = [(media, prelude, body) for media, prelude, body in css_rules(stylesheet()) if ".quests" in prelude]
        page = [body for _, prelude, body in rules if not prelude.startswith(".deck ")]
        self.assertTrue(page)
        self.assertFalse(any("nowrap" in body for body in page))
        for _, prelude, body in rules:
            if "nowrap" in body:
                self.assertIn("overflow: hidden", body, prelude)


class ProgressMarksPartialTests(SimpleTestCase):
    def render(self, source, **context):
        return Template("{% load emmaus %}" + source).render(Context(context))

    def test_the_label_says_a_medias_apart_from_sin_iniciar(self):
        """Defect (Q1 refuter D3): the marks' aria-label counting the camperos "a medias" as "sin iniciar" (total minus
        completed minus in progress), so a screen reader hears "10 sin iniciar" under a key that says 7 and 3 a medias."""
        label = 'aria-label="1 de 12 completadas, 1 en curso, 3 a medias, 7 sin iniciar"'
        tally = self.render("{% tally 1 1 12 partial=3 %}")
        self.assertIn(label, tally)
        # still three kinds of mark: a campero halfway keeps the outline one (the key says "sin iniciar y a medias")
        self.assertEqual(tally.count('class="tally__mark"'), 10)
        self.assertEqual(tally.count("tally__mark--done"), 1)
        self.assertEqual(tally.count("tally__mark--progress"), 1)
        self.assertIn(label, self.render("{% ruler 1 1 12 partial=3 %}"))
        self.assertIn(label, self.render('{% ruler 1 1 12 50 "Promedio" partial=3 %}'))
        # above the limit the tally hands the words to its ruler
        self.assertIn('aria-label="1 de 120 completadas, 1 en curso, 3 a medias, 115 sin iniciar"', self.render("{% tally 1 1 120 partial=3 %}"))
        # never a negative "sin iniciar"
        self.assertIn('aria-label="1 de 4 completadas, 1 en curso, 9 a medias, 0 sin iniciar"', self.render("{% tally 1 1 4 partial=9 %}"))

    def test_partial_zero_or_missing_renders_byte_identical(self):
        """Defect: the keyword changing a one-questionnaire page (partial is 0 there) or an old cutoff's row without the
        key (it resolves to ""): either must render exactly what the tag rendered without it."""
        for source in ("{% tally 3 1 6 %}", "{% tally 50 10 120 %}", "{% tally 0 0 0 %}", "{% ruler 3 1 6 %}", '{% ruler 3 1 6 50 "Promedio" %}'):
            plain = self.render(source)
            with self.subTest(source):
                self.assertEqual(self.render(source.replace(" %}", " partial=0 %}")), plain)
                self.assertEqual(self.render(source.replace(" %}", " partial=row.partial %}"), row={}), plain)
        self.assertIn('aria-label="3 de 6 completadas, 1 en curso, 2 sin iniciar"', self.render("{% tally 3 1 6 partial=0 %}"))


# ---- 4. Lists: "Sin terminar" -------------------------------------------------------------------------------------------


class StatusChoicesTests(ProgressFixture):
    def test_tabs_and_selects_say_sin_terminar_with_several(self):
        """Defect: a tab or filter called "Sin entrevista" that holds camperos reading "1 de 2 cuestionarios"."""
        tabs = self.page("my_participants")
        self.assertRegex(tabs, r'<a href="\?estado=pendiente"[^>]*>Sin terminar <span class="count">3</span></a>')
        self.assertNotIn(">Sin entrevista <", tabs)
        admin = self.page("participants", user=self.admin)
        self.assertIn('<option value="pendiente">Sin terminar</option>', admin)
        supervision = self.page("interviews", user=self.tutor1)
        self.assertIn('<option value="pendiente">Sin terminar</option>', supervision)
        self.assertNotIn('<option value="pendiente">Sin entrevista</option>', admin + supervision)


class OneQuestionnaireStatusChoicesTests(IsolatedCacheMixin, CampFixture):
    def test_tabs_and_selects_keep_sin_entrevista(self):
        """Defect: a one-questionnaire event's lists renamed."""
        client = self.login(self.admin, Client())
        self.assertContains(client.get(self.url("participants")), '<option value="pendiente">Sin entrevista</option>')
        self.assertContains(client.get(self.url("interviews")), '<option value="pendiente">Sin entrevista</option>')
        client = self.login(self.coord1, Client())
        self.assertRegex(client.get(self.url("my_participants")).content.decode(), r">Sin entrevista <span class=\"count\">3</span>")


# ---- 5. Coordinator: Mi inicio, Mi avance, Entrevistas pendientes ---------------------------------------------------------


class CoordinatorScreensTests(ProgressFixture):
    def test_mi_inicio_shows_each_questionnaire_after_the_key(self):
        """Defect: Mi inicio saying only "con todo", with no way to see how far each questionnaire went."""
        self.halfway_and_live()
        html = self.page("coord_home")
        self.assertIn('<h2 id="mi-avance" class="voice coord__count">Llevas 0 de 3 con los 2 cuestionarios.</h2>', html)
        self.assertIn('<p class="coord__count-note">1 en curso, 1 a medias y 1 sin iniciar. <a href="', html)
        self.assertRegex(html, r'</ul>\s*<section class="quests" aria-labelledby="por-cuestionario">')
        self.assertIn('<p class="quests__text">«Primer cuestionario»: 2 de 3.</p>', html)
        self.assertIn('<p class="quests__text">«Segundo cuestionario»: 0 de 3, 1 en curso.</p>', html)

    def test_the_queue_shows_the_pill_also_with_nothing_done(self):
        """Defect: "Por entrevistar" showing the pill only once a questionnaire is done, so the N stays hidden."""
        html = self.page("coord_home")
        queue = re.search(r'<section class="coord__queue".*?</section>', html, re.S).group(0)
        self.assertIn(PILL.format("pending", "0 de 2 cuestionarios"), queue)

    def test_mi_avance_says_a_medias_and_the_key_counts_them(self):
        """Defect: Mi avance reading "N en curso." and a key "Sin iniciar" while some camperos are halfway."""
        self.halfway_and_live()
        html = self.page("coord_progress")
        self.assertIn('<h2 id="cuantas" class="voice coord__count">Llevas 0 de 3 con los 2 cuestionarios.</h2>', html)
        self.assertIn('<p class="coord__count-note">0 % de tus camperos. 1 en curso, 1 a medias.</p>', html)
        self.assertIn('<li><span class="key__mark"></span>1 sin iniciar y 1 a medias</li>', html)
        self.assertIn('<p class="quests__text">«Primer cuestionario»: 2 de 3.</p>', html)

    def test_mi_avance_with_halfway_camperos_and_none_live(self):
        """Defect: "a medias" left out of the sentence when no interview is in progress."""
        self.interview(self.p1, self.first)
        html = self.page("coord_progress")
        self.assertIn('<p class="coord__count-note">0 % de tus camperos. 1 a medias.</p>', html)

    def test_pendientes_splits_sin_iniciar_from_a_medias(self):
        """Defect: a campero with questionnaire 1 done listed under "Sin iniciar" as if nothing was done."""
        self.interview(self.p2, self.first)
        html = self.page("coord_pending")
        not_started = re.search(r'aria-labelledby="sin-iniciar">(.*?)</section>', html, re.S).group(1)
        halfway = re.search(r'aria-labelledby="a-medias">(.*?)</section>', html, re.S).group(1)
        self.assertIn('Sin iniciar <span class="count">2</span>', not_started)
        self.assertNotIn(self.p2.full_name, not_started)
        self.assertIn(PILL.format("pending", "0 de 2 cuestionarios"), not_started)
        self.assertIn('<h2 id="a-medias" class="heading">A medias <span class="count">1</span></h2>', halfway)
        self.assertIn(self.p2.full_name, halfway)
        self.assertIn(PILL.format("pending", "1 de 2 cuestionarios"), halfway)

    def test_pendientes_does_not_celebrate_above_a_medias(self):
        """Defect (Q1 refuter D2): "No quedan entrevistas por iniciar. ¡Buen trabajo!" directly above a non-empty
        "A medias" list, when the coordinator's only campero answered one of the two questionnaires."""
        self.interview(self.p3, self.first)  # coord2's only campero: Primero done, Segundo never started
        html = self.page("coord_pending", user=self.coord2)
        not_started = re.search(r'aria-labelledby="sin-iniciar">(.*?)</section>', html, re.S).group(1)
        self.assertIn('Sin iniciar <span class="count">0</span>', not_started)
        self.assertIn('<p class="empty-line">Nadie sin iniciar.</p>', not_started)
        self.assertNotIn("¡Buen trabajo!", html)
        self.assertIn('<h2 id="a-medias" class="heading">A medias <span class="count">1</span></h2>', html)

    def test_pendientes_celebrates_once_nobody_is_left(self):
        """Defect: the celebration lost with several questionnaires when nobody is sin iniciar nor a medias."""
        self.interview(self.p3, self.first)
        self.interview(self.p3, self.second)
        html = self.page("coord_pending", user=self.coord2)
        self.assertIn('<p class="empty-line">No quedan entrevistas por iniciar. ¡Buen trabajo!</p>', html)
        self.assertNotIn("Nadie sin iniciar.", html)
        self.assertNotIn('aria-labelledby="a-medias"', html)

    def more_camperos(self, n):
        """``n`` more adult camperos for coord1, with nothing done."""
        return [self._camper(f"Campero Extra {i}", 20, self.m_coord1) for i in range(1, n + 1)]

    def test_the_marks_say_what_the_sentence_and_the_key_say(self):
        """Defect (Q1 refuter D3): Mi inicio and Mi avance printing "3 a medias y 7 sin iniciar" while the marks' label,
        the only thing a screen reader hears of them, says "10 sin iniciar"."""
        extra = self.more_camperos(9)  # coord1: p1, p2, p_minor_no_auth and these 9, 12 in all
        self.interview(self.p1, self.first)
        self.interview(self.p1, self.second)  # 1 completada
        self.interview(self.p2, self.first)
        self.interview(self.p2, self.second, Interview.Status.IN_PROGRESS)  # 1 en curso
        for p in (self.p_minor_no_auth, *extra[:2]):
            self.interview(p, self.first)  # 3 a medias; the other 7 sin iniciar
        label = 'aria-label="1 de 12 completadas, 1 en curso, 3 a medias, 7 sin iniciar"'
        home = self.page("coord_home")
        self.assertIn('<p class="coord__count-note">1 en curso, 3 a medias y 7 sin iniciar. <a href="', home)
        self.assertIn(label, home)
        progress = self.page("coord_progress")
        self.assertIn('<li><span class="key__mark"></span>7 sin iniciar y 3 a medias</li>', progress)
        self.assertIn(label, progress)

    def test_mi_inicio_links_to_the_sin_iniciar_and_the_a_medias(self):
        """Defect (Q1 refuter D4, a mutation survivor): "Ver los …" counting only the camperos sin iniciar, so with 6 of 7
        pending in the queue the halfway one behind them has no way in."""
        self.more_camperos(4)  # coord1: 7 pending, the queue shows 6
        self.interview(self.p1, self.first)  # 1 a medias, 6 sin iniciar
        html = self.page("coord_home")
        self.assertIn(f'<p class="more"><a href="{self.url("coord_pending")}">Ver los 6 sin iniciar y 1 a medias</a></p>', html)

    def test_mi_inicio_links_to_the_a_medias_when_none_is_sin_iniciar(self):
        """Defect (Q1 refuter D4): with every pending campero halfway the link vanishing (it counted 0 sin iniciar) or
        saying "Ver los 0 sin iniciar"."""
        for p in (self.p1, self.p2, self.p_minor_no_auth, *self.more_camperos(4)):
            self.interview(p, self.first)  # 7 a medias, 0 sin iniciar
        html = self.page("coord_home")
        self.assertIn(f'<p class="more"><a href="{self.url("coord_pending")}">Ver los 7 a medias</a></p>', html)
        self.assertNotIn("sin iniciar y", html)


class OneQuestionnaireCoordinatorTests(IsolatedCacheMixin, CampFixture):
    def test_no_per_questionnaire_block_nor_a_medias_list(self):
        """Defect: a one-questionnaire event's Mi inicio, Mi avance or Pendientes gaining blocks it never had."""
        client = self.login(self.coord1, Client())
        for name in ("coord_home", "coord_progress", "coord_pending"):
            html = client.get(self.url(name)).content.decode()
            with self.subTest(name):
                self.assertNotIn('class="quests"', html)
                self.assertNotIn("a medias", html.lower())
                self.assertNotIn("cuestionarios", html)
        self.assertContains(client.get(self.url("coord_progress")), '<li><span class="key__mark"></span>Sin iniciar</li>')
        self.assertContains(client.get(self.url("coord_pending")), 'Sin iniciar <span class="count">3</span>')

    def test_the_marks_label_is_as_before(self):
        """Defect (Q1 refuter D3 fix): a one-questionnaire Mi inicio or Mi avance whose marks' label changed."""
        client = self.login(self.coord1, Client())
        for name in ("coord_home", "coord_progress"):
            with self.subTest(name):
                self.assertContains(client.get(self.url(name)), 'aria-label="0 de 3 completadas, 0 en curso, 3 sin iniciar"')


# ---- 6. Supervision: Pendientes ---------------------------------------------------------------------------------------


class SupervisionPendingTests(ProgressFixture):
    def table(self, html):
        return re.search(r'aria-labelledby="t-sin">(.*?)</section>', html, re.S).group(1)

    def test_each_coordinator_says_sin_iniciar_and_a_medias(self):
        """Defect: "Sin iniciar, por coordinador" counting the halfway camperos as never interviewed."""
        self.interview(self.p2, self.first)
        section = self.table(self.page("pending", user=self.tutor1))
        self.assertIn('<h2 id="t-sin" class="heading">Sin terminar, por coordinador</h2>', section)
        rows = dict(re.findall(r">([^<>]+)</a></td><td class=\"num\">([^<]+)</td>", section))
        self.assertEqual(rows, {self.m_coord1.display_name: "2 sin iniciar y 1 a medias", self.m_coord2.display_name: "1 sin iniciar y 0 a medias"})

    def test_nobody_left_says_so(self):
        """Defect: "Todas las entrevistas ya se iniciaron." with several questionnaires, where it reads as "all done"."""
        EventParticipant.objects.filter(event=self.event).update(interview_status="completada")
        section = self.table(self.page("pending", user=self.tutor1))
        self.assertIn('<p class="empty-line">Ningún campero está sin iniciar ni a medias.</p>', section)

    def test_only_halfway_still_shows_the_table(self):
        """Defect: the empty sentence shown while some camperos are still halfway."""
        EventParticipant.objects.filter(event=self.event).update(interview_status="completada")
        EventParticipant.objects.filter(pk=self.p3.pk).update(interview_status="pendiente")
        Interview.objects.create(event=self.event, participant=self.p3, version=self.first, status=Interview.Status.COMPLETED)
        section = self.table(self.page("pending", user=self.tutor1))
        self.assertIn('<td class="num">0 sin iniciar y 1 a medias</td>', section)
        self.assertNotIn("Ningún campero", section)


class OneQuestionnaireSupervisionPendingTests(IsolatedCacheMixin, CampFixture):
    def test_the_table_and_its_empty_sentence_are_as_before(self):
        """Defect: a one-questionnaire event's Pendientes renamed or its cells turned into sentences."""
        client = self.login(self.tutor1, Client())
        html = client.get(self.url("pending")).content.decode()
        self.assertIn('<h2 id="t-sin" class="heading">Sin iniciar, por coordinador</h2>', html)
        self.assertIn('aria-label="Entrevistas sin iniciar por coordinador"', html)
        self.assertRegex(html, r'</a></td><td class="num">\d+</td>')
        EventParticipant.objects.filter(event=self.event).update(interview_status="completada")
        self.assertContains(client.get(self.url("pending")), '<p class="empty-line">Todas las entrevistas ya se iniciaron.</p>')


# ---- 7. The campero: record, start, done, kiosk -------------------------------------------------------------------------


class CamperoScreensTests(ProgressFixture):
    def test_the_record_says_how_many_above_the_rows(self):
        """Defect: the record listing each questionnaire without saying how many of them the campero completed."""
        self.interview(self.p2, self.first)
        html = self.page("participant_detail", self.p2.public_id)
        block = re.search(r'<section class="block" aria-labelledby="entrevista">(.*?)</section>', html, re.S).group(1)
        self.assertRegex(block, r'<p>Lleva 1 de 2 cuestionarios\.</p>\s*<ul class="rows">')

    def test_the_start_page_says_how_many_before_the_questionnaire(self):
        """Defect: "Antes de empezar" naming the questionnaire without saying where the campero stands."""
        html = self.page("interview_start", self.p2.public_id)  # both open: the radio
        self.assertIn(f"<p>{self.p2.full_name} lleva 0 de 2 cuestionarios.</p>", html)
        self.interview(self.p2, self.first)
        html = self.page("interview_start", self.p2.public_id)  # one left: named, no radio
        sentence = f"<p>{self.p2.full_name} lleva 1 de 2 cuestionarios.</p>"
        self.assertIn(sentence, html)
        self.assertLess(html.index(sentence), html.index("<p>Cuestionario: <strong>Segundo cuestionario</strong>.</p>"))

    def test_the_done_page_counts_camperos_missing_a_questionnaire(self):
        """Defect: "Te faltan 3 entrevistas." with several questionnaires, where a halfway campero has no interview left
        to start but still a questionnaire to answer."""
        first = self.complete(self.begin(questionnaire=self.q1))
        html = self.page("interview_done", first.public_id)
        self.assertIn("A 3 camperos les falta algún cuestionario.", html)
        self.assertNotIn("Te falta", html)
        self.complete(self.begin(questionnaire=self.q2))
        self.interview(self.p1, self.first)
        self.interview(self.p1, self.second)
        html = self.page("interview_done", first.public_id)
        self.assertIn("A 1 campero le falta algún cuestionario.", html)

    def kiosk(self, questionnaire):
        client = self.login(self.coord1, Client())
        self.choose_station(client, number=5)
        client.post(self.url("interview_start", self.p2.public_id), {**START_ADULT, "mode": "supervisada", "questionnaire": questionnaire.id})
        interview = Interview.objects.get(participant=self.p2, questionnaire=questionnaire)
        client.post(self.url("interview_handoff", interview.public_id))
        return client

    def test_the_kiosk_welcome_and_handback_say_which_of_how_many(self):
        """Defect: a campero alone at the computer not told this is the second of two questionnaires, or the welcome
        gaining a fourth promise."""
        client = self.kiosk(self.q2)
        html = client.get(reverse("kiosk:home")).content.decode()
        promises = re.search(r'<ul class="promises">(.*?)</ul>', html, re.S).group(1)
        self.assertEqual(promises.count("<li>"), 3)
        self.assertRegex(promises, r"<li>Son \d+ preguntas?, una por pantalla\. Tómate tu tiempo\.<p>Es el cuestionario 2 de 2\.</p></li>")
        client.post(reverse("kiosk:finish"))
        self.assertContains(client.get(reverse("kiosk:home")), "Ya terminaste el cuestionario 2 de 2. Avisa a tu coordinador")


class OneQuestionnaireCamperoScreensTests(IsolatedCacheMixin, ServiceHelpers, CampFixture):
    def setUp(self):
        super().setUp()
        self.session = {}

    def test_start_done_and_kiosk_say_nothing_new(self):
        """Defect: a one-questionnaire event's start, done or kiosk page naming questionnaires."""
        client = self.login(self.coord1, Client())
        html = client.get(self.url("interview_start", self.p2.public_id)).content.decode()
        self.assertNotIn(" lleva ", html)
        interview = self.begin()
        Interview.objects.filter(pk=interview.pk).update(status=Interview.Status.COMPLETED, completed_at=timezone.now())
        EventParticipant.objects.filter(pk=self.p2.pk).update(interview_status="completada")
        html = client.get(self.url("interview_done", interview.public_id)).content.decode()
        self.assertIn("Te faltan 2 entrevistas.", html)
        self.assertNotIn("algún cuestionario", html)

    def test_the_kiosk_welcome_has_no_position(self):
        """Defect: "Es el cuestionario 1 de 1." in an event with one questionnaire."""
        client = self.login(self.coord1, Client())
        self.choose_station(client, number=5)
        self.start(self.p2, mode="supervisada", client=client)
        interview = Interview.objects.get(participant=self.p2)
        client.post(self.url("interview_handoff", interview.public_id))
        html = client.get(reverse("kiosk:home")).content.decode()
        self.assertNotIn("Es el cuestionario", html)
        self.assertRegex(html, r"<li>Son \d+ preguntas?, una por pantalla\. Tómate tu tiempo\.</li>")


# ---- 8. Administración: the person's export, Cuestionarios ---------------------------------------------------------------


class AdminTests(ProgressFixture):
    def export(self):
        response = self.login(self.care_admin, Client()).get(self.url("participant_data_export", self.p1.public_id))
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content)

    def setUp(self):
        super().setUp()
        from events.models import EventMembership, MembershipRole

        from .base import make_user

        self.care_admin = make_user("admin.cuidado@prueba.mx", "Alma", "Cuidado")
        EventMembership.objects.create(event=self.event, user=self.care_admin, role=MembershipRole.ADMIN, can_view_sensitive=True)

    def test_the_export_says_how_many_next_to_the_interview(self):
        """Defect: a family's export of a two-questionnaire event not saying how many questionnaires were answered."""
        self.interview(self.p1, self.first)
        data = self.export()
        self.assertEqual(data["cuestionarios"], {"total": 2, "completados": 1})
        keys = list(data)
        self.assertEqual(keys[keys.index("entrevista") + 1], "cuestionarios")

    def test_cuestionarios_says_how_many_active_camperos_completed_each(self):
        """Defect: "Cuestionarios" counting interviews (in progress, or of withdrawn camperos) instead of saying how many
        of the event's camperos completed each questionnaire."""
        self.interview(self.p1, self.first)
        self.interview(self.p3, self.first)
        self.interview(self.p2, self.second, Interview.Status.IN_PROGRESS)
        EventParticipant.objects.filter(pk=self.p3.pk).update(is_active=False)
        html = self.page("questionnaires", user=self.admin)
        section = re.search(r'<section class="admin-narrow" aria-labelledby="en-este-evento">(.*?)</section>', html, re.S).group(1)
        self.assertIn('<p class="row__meta">Primero, versión 1, 1 de 4 camperos.</p>', section)
        self.assertIn('<p class="row__meta">Segundo, versión 1, 0 de 4 camperos.</p>', section)

    def test_the_export_counts_only_the_questionnaires_the_event_uses(self):
        """Defect (Q1 refuter D4, a mutation survivor): a completed interview of a questionnaire the event stopped using
        counted among "completados", so the family reads 2 of 2 for a person who answered one of the two in use."""
        third = create_questionnaire_from_definition(self.church, definition("Tercero", "Tercer cuestionario", "tres"))
        use_version(self.event, third)
        self.interview(self.p1, third)
        self.interview(self.p1, self.first)
        stop_using(self.event, third.questionnaire)  # the event keeps Primero and Segundo
        self.assertEqual(self.export()["cuestionarios"], {"total": 2, "completados": 1})

    def test_cuestionarios_says_campero_for_one(self):
        """Defect (Q1 refuter D5): "1 de 1 camperos." on a Cuestionarios row when the event has one active campero."""
        self.interview(self.p1, self.first)
        EventParticipant.objects.filter(event=self.event).exclude(pk=self.p1.pk).update(is_active=False)
        html = self.page("questionnaires", user=self.admin)
        self.assertIn('<p class="row__meta">Primero, versión 1, 1 de 1 campero.</p>', html)
        self.assertIn('<p class="row__meta">Segundo, versión 1, 0 de 1 campero.</p>', html)
        EventParticipant.objects.filter(pk=self.p2.pk).update(is_active=True)
        html = self.page("questionnaires", user=self.admin)
        self.assertIn('<p class="row__meta">Primero, versión 1, 1 de 2 camperos.</p>', html)


class OneQuestionnaireAdminTests(IsolatedCacheMixin, CampFixture):
    def test_the_export_has_no_cuestionarios_key(self):
        """Defect: a one-questionnaire export changing shape."""
        from events.models import EventMembership, MembershipRole

        from .base import make_user

        care_admin = make_user("admin.cuidado@prueba.mx", "Alma", "Cuidado")
        EventMembership.objects.create(event=self.event, user=care_admin, role=MembershipRole.ADMIN, can_view_sensitive=True)
        data = json.loads(self.login(care_admin, Client()).get(self.url("participant_data_export", self.p1.public_id)).content)
        self.assertNotIn("cuestionarios", data)
