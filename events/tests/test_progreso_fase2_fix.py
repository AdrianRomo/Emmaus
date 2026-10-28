"""Q2 fix round: the defects the phase-2 refuter found in "Por cuestionario" and "a medias" on the aggregate screens.

A  "Coordinadores": the sentence and the key under the rows counted camperos who are in no row (no coordinador).
B  "Pendientes … incluye" said "a medias" on some pages and not on others for the same state.
C  Deck slide 2: "Llevamos 120 de 400 con los 2 cuestionarios." wrapped to four lines and pushed the key under the foot.
D  Deck slide 3: from seven rows, or with long titles (two or three lines each), the list ran under the foot.
E  Deck slide 3 appeared with zeros for an event without camperos.
F  PDF "Por cuestionario": a long title, a bare string, ran over the "Completadas" column.
G  "Avance": the line per questionnaire under a coordinator's name inherited nowrap and scrolled the table sideways.

The deck's and the table's measured layout (headless Chromium at 1192, 1500 and 1744 px frames, and a 1366 px window)
is in the round's report; here the cheap parts: the markup, the CSS declarations, the cap and the PDF's wrapped lines.
Each test names the defect it would catch. With one questionnaire nothing changes (the N = 1 guards in
test_progreso_agregados and test_multi_questionnaires_progress, and OneQuestionnaireFixTests below).
"""

from __future__ import annotations

import re
from unittest import mock
from xml.sax.saxutils import escape

from django.test import Client, SimpleTestCase
from django.utils import timezone
from reportlab.pdfbase import pdfmetrics

from events.models import EventParticipant, Interview, QuestionnaireVersion
from events.services import pdf as pdf_module
from events.services.event_questionnaires import refresh_interview_statuses, use_version
from events.services.metrics import build_payload, event_scope, sum_rows
from events.services.presentation import DECK_QUESTIONNAIRE_ROWS, build_deck
from events.services.questionnaire import create_questionnaire_from_definition

from .base import CampFixture
from .test_camp_visual import declarations, stylesheet
from .test_multi_questionnaires import IsolatedCacheMixin, definition
from .test_multi_questionnaires_progress import ProgressFixture
from .test_progreso_agregados import PageHelpers
from .test_ux_pastoral import small_print_px

# 80 and 160 characters: titles a church may well write, wider than the PDF's title column (265.9 pt at 9.5 pt)
LONG = "Cuestionario de salud emocional, hábitos de oración y vida familiar del campero"
MAX = ("Cuestionario de seguimiento pastoral sobre salud emocional, hábitos de oración, vida familiar, amistades "
       "y servicio en la iglesia local del campero y su familia")[:160]
DECK_ROW = '<li class="quests__row"><p class="quests__text"><span class="quests__name">«{}»:</span> {}.</p>'


class FixRoundTests(PageHelpers, ProgressFixture):
    """halfway_and_live(): the event 1 de 5, 1 en curso, 1 a medias, 2 sin iniciar; Carla (coord1) 0 de 3 with 1 en curso
    and 1 a medias. See TwoQuestionnairesTests in test_progreso_agregados."""

    def add_questionnaires(self, count):
        """``count`` more questionnaires after the fixture's two, «Cuestionario C», «Cuestionario D», …"""
        for index in range(count):
            letter = chr(ord("C") + index)
            use_version(self.event, create_questionnaire_from_definition(self.church, definition(f"Extra{letter}", f"Cuestionario {letter}", f"x{letter}")))
        self.event.forget_questionnaire_links()

    # ---- A ---------------------------------------------------------------------------------------------------------------

    def test_coordinators_sentence_and_key_count_only_the_camperos_in_the_rows(self):
        """Defect A: "Coordinadores" used the event's progress, so a campero with no coordinador (in no row) who completed
        «Primer cuestionario» made the key under the rows say "2 sin iniciar y 2 a medias" and the sentence "1 de 6"
        while the rows hold 5 camperos and only one of them is halfway. The pastor's home keeps counting the event."""
        self.halfway_and_live()
        lone = self._camper("Uriel Sin Coordinador", 17, None)
        self.interview(lone, self.first)
        html = self.html("coordinators", user=self.pastor)
        self.assertIn("Entre todos llevan 1 de 5 con los 2 cuestionarios.", html)
        key = re.search(r'<ul class="key crew__key" aria-hidden="true">.*?</ul>', html, re.S).group(0)
        self.assertIn('<li><span class="key__mark"></span>2 sin iniciar y 1 a medias</li>', key)
        rows = re.findall(r'<p class="crew__figures"><strong>(\d+) de (\d+)</strong>', html)
        self.assertEqual(sum(int(total) for _, total in rows), 5)  # the sentence's 5 is the rows' 5
        home = self.html("pastor_home", user=self.pastor)
        self.assertIn("Llevamos 1 de 6 con los 2 cuestionarios.", home)
        self.assertIn('<li><span class="key__mark" aria-hidden="true"></span>2 sin iniciar y 2 a medias</li>', home)
        self.assertIn('<dt>Faltan</dt><dd class="figures__value">5</dd>', home)

    def test_the_tutor_sees_the_same_numbers_as_before(self):
        """Defect A, the other way: the tutor's "Coordinadores" (a team: every campero is in a row) changing its counts."""
        self.halfway_and_live()
        html = self.html("coordinators", user=self.tutor1)
        self.assertIn("Entre todos llevan 0 de 4 con los 2 cuestionarios.", html)
        self.assertIn('<li><span class="key__mark"></span>2 sin iniciar y 1 a medias</li>', html)

    # ---- B ---------------------------------------------------------------------------------------------------------------

    def test_pendientes_names_the_halfway_camperos_on_every_page(self):
        """Defect B: "incluye 1 en curso, 1 a medias" on the pastor's home and "Avance" but "incluye 1 en curso" on
        "Reportes", a saved cutoff, its PDF, a coordinator's page and the coordinator's own "Mi avance", for one state."""
        self.halfway_and_live()
        note = '<dd class="figures__note">incluye 1 en curso, 1 a medias</dd>'
        snapshot = self.snapshot()
        pages = {
            "reports": self.html("reports", user=self.pastor),
            "report_detail": self.html("report_detail", snapshot.public_id, user=self.pastor),
            "coordinator_detail": self.html("coordinator_detail", self.m_coord1.id, user=self.pastor),  # Carla
            "coord_progress": self.html("coord_progress", user=self.coord1),  # Carla's own
            "pastor_home": self.html("pastor_home", user=self.pastor),
            "progress": self.html("progress", user=self.pastor),
        }
        for name, html in pages.items():
            with self.subTest(page=name):
                self.assertIn(note, html)
        paragraphs, _, _ = self.pdf(snapshot)
        self.assertIn("incluye 1 en curso, 1 a medias", paragraphs)

    # ---- C ---------------------------------------------------------------------------------------------------------------

    def test_deck_slide_two_says_the_count_then_the_questionnaires_on_a_line_of_their_own(self):
        """Defect C: "Llevamos 120 de 400 con los 2 cuestionarios." in the 6.25cqw headline took four lines and pushed
        the key under the foot (one questionnaire's "entrevistas." takes three). The headline says the count and a
        .deck-sub line under it says how many questionnaires, at .deck-sub's size (22 px or more from a 1128 px frame)."""
        self.halfway_and_live()
        for html in (self.html("presentation_live", user=self.pastor), self.html("report_presentation", self.snapshot().public_id, user=self.pastor)):
            second = self.slides(html)[1][2]
            self.assertIn('<h2 class="voice deck-headline">Llevamos 1 de 5.</h2>\n'
                          '        <p class="deck-sub deck-sub--several">Con los 2 cuestionarios.</p>\n'
                          '        <p class="deck-sub">', second)
            self.assertNotIn("con los", second)
        css = stylesheet()
        self.assertGreaterEqual(float(declarations(css, ".deck-sub")["font-size"].removesuffix("cqw")) * 1128 / 100, 22)
        self.assertNotIn("font-size", declarations(css, ".deck-sub--several"))  # .deck-sub's size, not a smaller one

    # ---- D ---------------------------------------------------------------------------------------------------------------

    def test_deck_rows_take_one_line_each_and_keep_their_count(self):
        """Defect D: a long title wrapped to two or three lines per row and the list ran under the foot (three titles of
        160 characters at a 1192 px frame). Each row is one line: the title ends in "…", the count after it stays whole,
        like a team's name and figure on slide 2."""
        self.halfway_and_live()
        QuestionnaireVersion.objects.filter(pk=self.first.pk).update(title=MAX)
        self.event.forget_questionnaire_links()
        third = self.slides(self.html("presentation_live", user=self.pastor))[2][2]
        self.assertIn(DECK_ROW.format(MAX, "3 de 5"), third)
        css = stylesheet()
        text = declarations(css, ".deck .quests__text")
        self.assertEqual((text.get("display"), text.get("white-space"), text.get("overflow")), ("flex", "nowrap", "hidden"))
        name = declarations(css, ".deck .quests__name")
        self.assertEqual((name.get("min-width"), name.get("overflow"), name.get("text-overflow")), ("0", "hidden", "ellipsis"))
        self.assertEqual(declarations(css, ".deck-teams__list li > span").get("text-overflow"), "ellipsis")  # the precedent

    def test_deck_from_seven_rows_tightens_at_the_floor(self):
        """Defect D: seven or eight rows (at the four-row tier) ran 7 and 37 px under the foot at a 1192 px frame. From
        seven the rows tighten again, and the line stays at the small print's floor, never under 22 px from 1128 up."""
        self.add_questionnaires(5)
        self.halfway_and_live()
        third = self.slides(self.html("presentation_live", user=self.pastor))[2][2]
        self.assertEqual(third.count('<li class="quests__row">'), 7)
        css = stylesheet()
        tier = ".deck .quests__list:has(li:nth-child(7))"
        self.assertLess(float(declarations(css, tier)["gap"].removesuffix("cqw")), float(declarations(css, ".deck .quests__list:has(li:nth-child(4))")["gap"].removesuffix("cqw")))
        size = declarations(css, f"{tier} .quests__text")["font-size"]
        for frame in (1129, 1192, 1211, 1404, 1500, 1744):
            self.assertGreaterEqual(round(small_print_px(size, frame), 2), 22, frame)
        self.assertGreaterEqual(round(small_print_px(declarations(css, ".deck .quests__more")["font-size"], 1129), 2), 22)

    def test_deck_shows_eight_questionnaires_and_says_how_many_more(self):
        """Defect D: ten rows do not fit even at the floor (at a 1192 px frame they cross the foot and the title), so the
        slide lists the first eight and a last line says "y 2 más"; slide 2 still counts all ten."""
        self.assertEqual(DECK_QUESTIONNAIRE_ROWS, 8)
        self.add_questionnaires(8)
        self.halfway_and_live()
        deck = build_deck(build_payload(event_scope(self.event)), event_name="X", kind_label="Campamento", k=5)
        self.assertEqual((len(deck["progress"]["questionnaires"]), len(deck["questionnaire_rows"]), deck["questionnaires_more"]), (10, 8, 2))
        self.assertEqual(deck["questionnaire_rows"], deck["progress"]["questionnaires"][:8])
        for html in (self.html("presentation_live", user=self.pastor), self.html("report_presentation", self.snapshot().public_id, user=self.pastor)):
            slides = self.slides(html)
            third = slides[2][2]
            self.assertEqual(third.count('<li class="quests__row">'), 8)
            self.assertIn(DECK_ROW.format("Cuestionario H", "0 de 5"), third)
            self.assertNotIn("Cuestionario I", third)
            self.assertIn('</ul>\n    <p class="quests__more">y 2 más</p>\n  </div>', third)
            self.assertIn('<p class="deck-sub deck-sub--several">Con los 10 cuestionarios.</p>', slides[1][2])

    def test_deck_with_eight_questionnaires_has_no_more_line(self):
        """Defect D, the edge: "y 0 más" under eight rows that all fit."""
        self.add_questionnaires(6)
        self.halfway_and_live()
        third = self.slides(self.html("presentation_live", user=self.pastor))[2][2]
        self.assertEqual(third.count('<li class="quests__row">'), 8)
        self.assertNotIn("quests__more", third)
        self.assertNotIn("más", third)

    # ---- E ---------------------------------------------------------------------------------------------------------------

    def test_no_por_cuestionario_slide_without_camperos(self):
        """Defect E: an event with two questionnaires and no camperos yet (setting up) projected a "Por cuestionario"
        slide of "0 de 0." rows after "Todavía no hay camperos registrados.", live and in a saved cutoff."""
        EventParticipant.objects.filter(event=self.event).delete()
        snapshot = self.snapshot()
        for html in (self.html("presentation_live", user=self.pastor), self.html("report_presentation", snapshot.public_id, user=self.pastor)):
            slides = self.slides(html)
            self.assertEqual([(n, total) for n, total, _ in slides], [(str(n), "5") for n in range(1, 6)])
            self.assertIn('<h2 class="voice deck-headline">Todavía no hay camperos registrados.</h2>', slides[1][2])
            self.assertNotIn("Por cuestionario", html)
            self.assertNotIn("quests__", html)
        deck = build_deck(build_payload(event_scope(self.event)), event_name="X", kind_label="Campamento", k=5)
        self.assertEqual((deck["progress"]["questionnaires"], deck["questionnaire_rows"], deck["questionnaires_more"]), ([], [], 0))

    # ---- F ---------------------------------------------------------------------------------------------------------------

    def recorded_pdf(self, snapshot):
        """The cutoff's PDF built with Paragraph and Table recorded: {"paragraphs": [(text, instance, drawn x)], "tables":
        [(data, instance)]}. A drawn x is relative to the table the paragraph sits in (Table.draw translates)."""
        made = {"paragraphs": [], "tables": []}
        base_paragraph, base_table = pdf_module.Paragraph, pdf_module.Table

        class Paragraph(base_paragraph):
            def __init__(self, text, *args, **kwargs):
                super().__init__(text, *args, **kwargs)
                self.drawn_x = []
                made["paragraphs"].append((str(text), self))

            def drawOn(self, canvas, x, y, _sW=0):
                self.drawn_x.append(x)
                return super().drawOn(canvas, x, y, _sW)

        class Table(base_table):
            def __init__(self, data, *args, **kwargs):
                super().__init__(data, *args, **kwargs)
                made["tables"].append((data, self))

        client = self.login(self.pastor, Client())
        with mock.patch.object(pdf_module, "Paragraph", Paragraph), mock.patch.object(pdf_module, "Table", Table):
            response = client.get(self.url("report_pdf", snapshot.public_id))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))
        return made

    def test_pdf_long_titles_wrap_inside_their_column(self):
        """Defect F: the "Por cuestionario" table's first cell was the bare title, so an 80- or 160-character title (wider
        than the 265.9 pt column) was drawn over "Completadas" and the figures. Now each title is a Paragraph wrapped in
        its column: every line it draws ends before the next column, and its figures are still in their cells."""
        self.halfway_and_live()
        QuestionnaireVersion.objects.filter(pk=self.first.pk).update(title=LONG)
        QuestionnaireVersion.objects.filter(pk=self.second.pk).update(title=MAX)
        self.event.forget_questionnaire_links()
        made = self.recorded_pdf(self.snapshot())
        found = [(data, table) for data, table in made["tables"] if data and data[0] == ["Cuestionario", "Completadas", "En curso", "Avance"]]
        self.assertTrue(found)
        data, table = found[0]
        column_end = table._colpositions[1] - 4  # the title column's right edge, less the cell's right padding
        self.assertAlmostEqual(column_end - 4, 265.9, delta=0.1)
        for row, (title, completed, live, pct) in zip(data[1:], ((LONG, 3, 0, "60\u00a0%"), (MAX, 1, 1, "20\u00a0%"))):
            with self.subTest(title=len(title)):
                self.assertGreater(pdfmetrics.stringWidth(title, "Figtree", 9.5), column_end - 4)  # it cannot fit on one line
                cell = row[0]
                self.assertIsInstance(cell, pdf_module.Paragraph)  # not a bare string (the patch is over: the real class)
                self.assertEqual(cell.getPlainText(), title)
                self.assertEqual(row[1:], [completed, live, pct])
                lines = cell.blPara.lines
                self.assertGreaterEqual(len(lines), 2)
                widest = max(cell.width - (line[0] if isinstance(line, tuple) else line.extraSpace) for line in lines)
                self.assertTrue(cell.drawn_x)
                for x in cell.drawn_x:
                    self.assertLessEqual(x + widest, column_end + 0.01)
                self.assertEqual(sum(1 for text, _ in made["paragraphs"] if text == escape(title)), 1)  # printed once

    def test_pdf_escapes_a_title_in_its_cell(self):
        """Defect F: a title with "&" or "<" breaking ReportLab's markup once the cell is a Paragraph."""
        self.halfway_and_live()
        QuestionnaireVersion.objects.filter(pk=self.first.pk).update(title="Salud & <hábitos>")
        self.event.forget_questionnaire_links()
        made = self.recorded_pdf(self.snapshot())
        data = next(data for data, _ in made["tables"] if data and data[0] == ["Cuestionario", "Completadas", "En curso", "Avance"])
        self.assertEqual(data[1][0].text, "Salud &amp; &lt;hábitos&gt;")
        self.assertEqual(data[1][0].getPlainText(), "Salud & <hábitos>")

    # ---- G ---------------------------------------------------------------------------------------------------------------

    def test_table_sub_wraps_inside_a_nowrap_table(self):
        """Defect G: in "Avance" the coordinators table is .table--nowrap, so the line per questionnaire under each name
        inherited nowrap and the table scrolled sideways on a 1366 px desktop (976 px of table in an 816 px box with
        the fixture's two titles). The line wraps, and is never wider than 22rem."""
        self.halfway_and_live()
        html = self.html("progress", user=self.pastor)
        table = re.search(r'aria-label="Avance por coordinador"><table class="table table--nowrap">.*?</table>', html, re.S).group(0)
        self.assertIn('<p class="table__sub">«Primer cuestionario»: 2 de 3, «Segundo cuestionario»: 0 de 3.</p>', table)
        css = stylesheet()
        self.assertEqual(declarations(css, ".table--nowrap td").get("white-space"), "nowrap")
        for width in (390, 1366):
            rule = declarations(css, ".table--nowrap .table__sub", width)
            self.assertEqual((rule.get("white-space"), rule.get("max-width")), ("normal", "22rem"), width)


class OneQuestionnaireFixTests(PageHelpers, IsolatedCacheMixin, CampFixture):
    """One questionnaire: none of the fix round's changes show (p1 completed, p2 in progress, both coord1's)."""

    def setUp(self):
        super().setUp()
        version = self.event.questionnaire_versions()[0]
        for participant, status in ((self.p1, Interview.Status.COMPLETED), (self.p2, Interview.Status.IN_PROGRESS)):
            done = status == Interview.Status.COMPLETED
            Interview.objects.create(event=self.event, participant=participant, version=version, status=status,
                                     completed_at=timezone.now() if done else None)
        refresh_interview_statuses(self.event)

    def test_deck_slide_two_and_pendientes_are_as_they_were(self):
        """Defect C/B guard: the one-questionnaire headline gaining the new line, or a "Pendientes" note gaining
        "0 a medias", on the deck, Mi avance, a coordinator's page, a cutoff and its PDF."""
        second = self.slides(self.html("presentation_live", user=self.pastor))[1][2]
        self.assertIn('<h2 class="voice deck-headline">Llevamos 1 de 5 entrevistas.</h2>\n        <p class="deck-sub">', second)
        self.assertNotIn("deck-sub--several", second)
        snapshot = self.snapshot()
        for html in (self.html("coord_progress", user=self.coord1), self.html("coordinator_detail", self.m_coord1.id, user=self.pastor),
                     self.html("report_detail", snapshot.public_id, user=self.pastor), self.html("reports", user=self.pastor)):
            self.assertIn('<dd class="figures__note">incluye 1 en curso</dd>', html)
        paragraphs, _, _ = self.pdf(snapshot)
        self.assertIn("incluye 1 en curso", paragraphs)

    def test_coordinators_page_is_as_it_was(self):
        """Defect A guard: with every campero in a row, the sentence and key from the rows are the event's."""
        html = self.html("coordinators", user=self.pastor)
        self.assertIn("Entre todos llevan 1 de 5 entrevistas.", html)
        self.assertIn('<li><span class="key__mark"></span>Sin iniciar</li>', html)


class SumRowsTests(SimpleTestCase):
    """events.services.metrics.sum_rows: the counts of a page's rows, in Progress.as_dict()'s shape."""

    @staticmethod
    def row(eligible, completed, in_progress, partial=None, quests=None):
        progress = {"eligible": eligible, "completed": completed, "in_progress": in_progress}
        if partial is not None:
            progress["partial"] = partial
        if quests is not None:
            progress["questionnaires"] = [
                {"id": qid, "title": title, "order": order, "eligible": eligible, "completed": done, "in_progress": live, "percent": None}
                for order, (qid, title, done, live) in enumerate(quests, start=1)
            ]
        return progress

    def test_rows_add_up_with_each_questionnaire_by_id(self):
        """Defect A: a sum that drops "partial", miscounts "sin iniciar", or mixes two questionnaires' counts."""
        rows = [
            self.row(3, 0, 1, 1, [(7, "Primero", 2, 0), (9, "Segundo", 0, 1)]),
            self.row(1, 0, 0, 0, [(7, "Primero", 0, 0), (9, "Segundo", 0, 0)]),
            self.row(1, 1, 0, 0, [(7, "Primero", 1, 0), (9, "Segundo", 1, 0)]),
        ]
        self.assertEqual(
            sum_rows(rows),
            {
                "eligible": 5, "completed": 1, "in_progress": 1, "partial": 1,
                "questionnaires": [
                    {"id": 7, "title": "Primero", "order": 1, "eligible": 5, "completed": 3, "in_progress": 0, "percent": 60},
                    {"id": 9, "title": "Segundo", "order": 2, "eligible": 5, "completed": 1, "in_progress": 1, "percent": 20},
                ],
                "pending": 4, "not_started": 2, "percent": 20,
            },
        )

    def test_no_rows_and_old_rows(self):
        """Defect A: no rows (no coordinadores yet) raising or printing "0 %", or a cutoff's rows saved without
        "partial" and "questionnaires" failing."""
        self.assertEqual(sum_rows([]), {"eligible": 0, "completed": 0, "in_progress": 0, "partial": 0, "questionnaires": [], "pending": 0, "not_started": 0, "percent": None})
        self.assertEqual(sum_rows([self.row(2, 1, 1), self.row(2, 0, 0)])["not_started"], 2)
