"""How many of the event's N questionnaires were completed, on the aggregate screens: the pastor's summary and "Avance",
the administration's home, the tutor's home, "Coordinadores" and a coordinator's page, "Reportes" and a saved cutoff
(its history, its PDF) and the presentation, live and saved.

Owner's ask (2026-10-28): "the data clear at every level". The rule, from Q1's coordinator screens: with several
questionnaires a count says "X de Y con los N cuestionarios", the camperos with some questionnaires done and none live
are "a medias", and a "Por cuestionario" block says how far each questionnaire went. With one questionnaire every page,
PDF and slide is as it was (the pinned tests in test_ux_pastoral, test_rotulo_leaders, test_reporting, test_admin_screens
and test_camp_flow say so; OneQuestionnaireTests below checks the words).

Each test names the defect it would catch.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest import mock
from xml.sax.saxutils import escape

from django.test import Client, SimpleTestCase
from django.utils import timezone

from events.models import Interview, QuestionnaireVersion, ReportSnapshot
from events.services import pdf as pdf_module
from events.services.event_questionnaires import refresh_interview_statuses
from events.services.metrics import DEFINITIONS, SEVERAL_QUESTIONNAIRE_DEFINITIONS, build_payload, event_scope
from events.services.presentation import build_deck
from events.services.reports import _run_key, generate_snapshot, group_history

from .base import CampFixture
from .test_camp_visual import declarations, stylesheet
from .test_multi_questionnaires import IsolatedCacheMixin
from .test_multi_questionnaires_progress import ProgressFixture
from .test_ux_pastoral import small_print_px

# The PDF's "Cómo se calcula": DEFINITIONS always, then SEVERAL_QUESTIONNAIRE_DEFINITIONS only with several questionnaires
GLOSSARY_LINES = [f"<b>{escape(name)}:</b> {escape(text)}" for name, text in DEFINITIONS]
SEVERAL_LINES = [f"<b>{escape(name)}:</b> {escape(text)}" for name, text in SEVERAL_QUESTIONNAIRE_DEFINITIONS]
GLOSSARY = set(GLOSSARY_LINES)
QUEST = '<li class="quests__row"><p class="quests__text">{}</p>'
NEW_WORDS = ("Por cuestionario", "a medias", "con los", "table__sub", 'class="quests')


class PageHelpers:
    def html(self, name, *args, user):
        client = self.login(user, Client())
        response = client.get(self.url(name, *args))
        self.assertEqual(response.status_code, 200, name)
        return response.content.decode()

    def snapshot(self):
        return generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=timezone.now().replace(second=0, microsecond=0))

    def pdf(self, snapshot, user=None):
        """Every text handed to ReportLab for the cutoff's PDF: (paragraphs without the DEFINITIONS lines, tables, key
        items). The two lines for several questionnaires stay in, so a test can see them."""
        paragraphs, tables, keys = self.pdf_all(snapshot, user)
        return [text for text in paragraphs if text not in GLOSSARY], tables, keys

    def glossary(self, snapshot):
        """The lines under "Cómo se calcula", the PDF's last section, in order."""
        paragraphs, _, _ = self.pdf_all(snapshot)
        return paragraphs[paragraphs.index("Cómo se calcula") + 1:]

    def pdf_all(self, snapshot, user=None):
        client = self.login(user or self.pastor, Client())
        with mock.patch.object(pdf_module, "Paragraph", wraps=pdf_module.Paragraph) as paragraph, \
                mock.patch.object(pdf_module, "Table", wraps=pdf_module.Table) as table, \
                mock.patch.object(pdf_module, "Key", wraps=pdf_module.Key) as key:
            response = client.get(self.url("report_pdf", snapshot.public_id))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))
        paragraphs = [str(call.args[0]) for call in paragraph.call_args_list]
        self.assertTrue(GLOSSARY <= set(paragraphs))  # the glossary is printed: leaving it out below is not hiding a miss
        tables = [call.args[0] for call in table.call_args_list]
        keys = [text for call in key.call_args_list for _, text in call.args[0]]
        return paragraphs, tables, keys

    @staticmethod
    def slides(html):
        return re.findall(r'<section class="slide[^"]*" aria-label="Diapositiva (\d+) de (\d+)">(.*?)</section>', html, re.S)


class TwoQuestionnairesTests(PageHelpers, ProgressFixture):
    """halfway_and_live(): p1 completed Primero ("a medias"), p2 completed Primero and answers Segundo ("en curso"), p4
    completed both, p3 and p_minor_no_auth have nothing. The event: 1 de 5, 1 en curso, 1 a medias, 2 sin iniciar;
    Primero 3 de 5, Segundo 1 de 5 and 1 en curso. Equipo Norte (Carla: p1, p2, p_minor; César: p3) and Equipo Sur
    (Cora: p4)."""

    def setUp(self):
        super().setUp()
        self.halfway_and_live()

    def assert_event_block(self, html, quests_id="por-cuestionario"):
        self.assertRegex(html, rf'</ul>\s*<section class="quests" aria-labelledby="{quests_id}">')
        self.assertIn(QUEST.format("«Primer cuestionario»: 3 de 5."), html)
        self.assertIn(QUEST.format("«Segundo cuestionario»: 1 de 5, 1 en curso."), html)

    # ---- 1. Headline, key and "Por cuestionario" ------------------------------------------------------------------------

    def test_pastor_home_says_con_los_2_cuestionarios_and_a_medias(self):
        """Defect: the pastor's summary reading "Llevamos 1 de 5 entrevistas." (one questionnaire's words), a key whose
        "sin iniciar" hides the camperos halfway, "Faltan" silent about them, or no line per questionnaire."""
        html = self.html("pastor_home", user=self.pastor)
        self.assertIn('<p class="voice headline headline--sentence">Llevamos 1 de 5 con los 2 cuestionarios.</p>', html)
        self.assertIn('<li><span class="key__mark" aria-hidden="true"></span>2 sin iniciar y 1 a medias</li>', html)
        self.assertIn('<dd class="figures__note">incluye 1 en curso, 1 a medias</dd>', html)
        self.assert_event_block(html, "por-cuestionario-resumen")

    def test_avance_says_it_and_each_coordinator_says_each_questionnaire(self):
        """Defect: "Avance" without the per-questionnaire counts, or its coordinators table without them under the names."""
        html = self.html("progress", user=self.pastor)
        self.assertIn('<p class="voice headline">Llevamos 1 de 5 con los 2 cuestionarios.</p>', html)
        self.assertIn('<li><span class="key__mark" aria-hidden="true"></span>2 sin iniciar y 1 a medias</li>', html)
        self.assertIn('<dd class="figures__note">incluye 1 en curso, 1 a medias</dd>', html)
        self.assert_event_block(html, "por-cuestionario-avance")
        carla = re.search(rf'>{self.m_coord1.display_name}</a>(.*?)</td>', html).group(1)
        self.assertEqual(carla, '<p class="table__sub">«Primer cuestionario»: 2 de 3, «Segundo cuestionario»: 0 de 3.</p>')

    def test_admin_home_says_it(self):
        """Defect: the administration's state counting "entrevistas" while the event has two questionnaires."""
        html = self.html("admin_home", user=self.admin)
        self.assertIn('<h2 id="estado-evento" class="voice admin-state__title">Llevamos 1 de 5 con los 2 cuestionarios.</h2>', html)
        self.assertIn('<li><span class="key__mark" aria-hidden="true"></span>2 sin iniciar y 1 a medias</li>', html)
        self.assert_event_block(html)

    def test_tutor_home_says_it_for_the_team(self):
        """Defect: the tutor's team sentence without the N or the halfway camperos, or a "Por cuestionario" counting the
        whole event instead of the team."""
        html = self.html("tutor_home", user=self.tutor1)
        self.assertIn('<h2 id="avance-equipo" class="voice coord__count">Tu equipo lleva 0 de 4 con los 2 cuestionarios.</h2>', html)
        self.assertIn("0 % de sus camperos, 1 en curso, 1 a medias.", html)
        self.assertRegex(html, r'Cada marca es un campero\.\s*</p>\s*<section class="quests" aria-labelledby="por-cuestionario-equipo">')
        self.assertIn(QUEST.format("«Primer cuestionario»: 2 de 4."), html)
        self.assertIn(QUEST.format("«Segundo cuestionario»: 0 de 4, 1 en curso."), html)

    def test_supervision_pages_say_it(self):
        """Defect: "Coordinadores" and a coordinator's page counting "entrevistas", or silent about the halfway ones."""
        html = self.html("coordinators", user=self.pastor)
        self.assertIn("Entre todos llevan 1 de 5 con los 2 cuestionarios.", html)
        html = self.html("coordinator_detail", self.m_coord1.id, user=self.pastor)
        self.assertIn('<p class="voice coord__count">Carla lleva 0 de 3 con los 2 cuestionarios.</p>', html)
        self.assertIn('<p class="coord__count-note">0 % de sus camperos. 1 en curso. 1 a medias. 1 sin iniciar.</p>', html)
        self.assertIn('<li><span class="key__mark"></span>1 sin iniciar y 1 a medias</li>', html)
        self.assertIn(QUEST.format("«Primer cuestionario»: 2 de 3."), html)

    def test_reports_say_it_in_the_past(self):
        """Defect: "Reportes" and a saved cutoff reading "Llevábamos 1 de 5 entrevistas." for a two-questionnaire event."""
        snapshot = self.snapshot()
        for html in (self.html("reports", user=self.pastor), self.html("report_detail", snapshot.public_id, user=self.pastor)):
            self.assertIn('<p class="voice headline">Llevábamos 1 de 5 con los 2 cuestionarios.</p>', html)
            self.assertIn('<li><span class="key__mark" aria-hidden="true"></span>2 sin iniciar y 1 a medias</li>', html)
            self.assert_event_block(html)

    def test_no_a_medias_in_the_key_while_nobody_is_halfway(self):
        """Defect: "y 0 a medias" in a key (or "0 a medias" under "Faltan") when no campero is halfway."""
        Interview.objects.filter(participant=self.p1).delete()
        Interview.objects.filter(participant=self.p2).delete()
        refresh_interview_statuses(self.event)
        html = self.html("pastor_home", user=self.pastor)
        self.assertIn("Llevamos 1 de 5 con los 2 cuestionarios.", html)
        self.assertIn('<li><span class="key__mark" aria-hidden="true"></span>4 sin iniciar</li>', html)
        self.assertNotIn("a medias", html)
        _, _, keys = self.pdf(self.snapshot())
        self.assertIn("4 sin iniciar", keys)

    # ---- 2. Rows and tables ----------------------------------------------------------------------------------------------

    def test_each_coordinator_row_has_a_line_per_questionnaire(self):
        """Defect: the tutor's and the leaders' coordinator rows saying "0 de 3" with no way to tell that Carla's
        camperos already completed two of the first questionnaire."""
        for user, name in ((self.tutor1, "tutor_home"), (self.pastor, "coordinators")):
            html = self.html(name, user=user)
            row = re.search(rf'<li class="crew__row">(?:(?!</li>).)*>{self.m_coord1.display_name}</a>.*?</li>', html, re.S).group(0)
            self.assertIn('<p class="row__meta">«Primer cuestionario»: 2 de 3, «Segundo cuestionario»: 0 de 3.</p>', row, name)
            # The key under the rows: the scope's camperos (the team, or the event), as on the page's own key
            self.assertIn('<li><span class="key__mark"></span>2 sin iniciar y 1 a medias</li>', html, name)

    def test_the_cutoff_names_each_questionnaire_under_teams_and_coordinators(self):
        """Defect: a saved cutoff's teams and coordinators with one number each, nothing per questionnaire."""
        html = self.html("report_detail", self.snapshot().public_id, user=self.pastor)
        sub = '<span class="table__sub">{}</span>'
        self.assertIn('<span class="teams__name">Equipo Norte' + sub.format("«Primer cuestionario»: 2 de 4, «Segundo cuestionario»: 0 de 4.") + "</span>", html)
        self.assertIn('<span class="teams__name">Equipo Sur' + sub.format("«Primer cuestionario»: 1 de 1, «Segundo cuestionario»: 1 de 1.") + "</span>", html)
        coordinators = html.split('aria-labelledby="t-coord"', 1)[1]
        self.assertIn(f'<span class="teams__name">{self.m_coord1.display_name}<span class="teams__meta">Equipo Norte</span>'
                      + sub.format("«Primer cuestionario»: 2 de 3, «Segundo cuestionario»: 0 de 3.") + "</span>", coordinators)

    def test_the_history_says_each_questionnaire_under_completadas(self):
        """Defect: two cutoffs reading "1" under "Completadas" in the history while one questionnaire moved."""
        self.snapshot()
        html = self.html("reports", user=self.pastor)
        history = html.split('aria-labelledby="historial"', 1)[1]
        self.assertIn('<td class="num">1<p class="table__sub">«Primer cuestionario»: 3, «Segundo cuestionario»: 1.</p></td>', history)

    def test_table_sub_is_small_quiet_and_without_margin(self):
        """Defect (layout): the line under a name as large and dark as the name, or pushed away by a paragraph margin."""
        rule = declarations(stylesheet(), ".table__sub")
        self.assertEqual((rule.get("font-size"), rule.get("color"), rule.get("margin")), ("var(--t-small)", "var(--ink-2)", "0"))
        self.assertEqual(rule.get("display"), "block")  # a <span> inside a report's name sits under it

    # ---- 3. The presentation ---------------------------------------------------------------------------------------------

    def test_the_deck_has_a_por_cuestionario_slide_after_the_progress_one(self):
        """Defect: the projector saying "entrevistas" for two questionnaires, no slide per questionnaire, or slide numbers
        ("3 de 5") that skip or repeat once there are six."""
        for html in (self.html("presentation_live", user=self.pastor), self.html("report_presentation", self.snapshot().public_id, user=self.pastor)):
            slides = self.slides(html)
            self.assertEqual([(n, total) for n, total, _ in slides], [(str(n), "6") for n in range(1, 7)])
            feet = re.findall(r'<div class="slide__foot"><span>(.*?)</span><span>(.*?)</span></div>', html)
            self.assertEqual([number for _, number in feet], [f"{n} de 6" for n in range(1, 7)])
            self.assertIn('data-deck-count aria-live="polite">1 de 6</span>', html)
            self.assertIn('<h2 class="voice deck-headline">Llevamos 1 de 5 con los 2 cuestionarios.</h2>', slides[1][2])
            self.assertIn('<li><span class="key__mark" aria-hidden="true"></span>2 sin iniciar y 1 a medias</li>', slides[1][2])
            third = slides[2][2]
            self.assertIn('<h2 class="voice slide__title">Por cuestionario</h2>', third)
            self.assertIn(QUEST.format("«Primer cuestionario»: 3 de 5."), third)
            self.assertIn(QUEST.format("«Segundo cuestionario»: 1 de 5."), third)
            self.assertEqual(third.count('class="ruler"'), 2)
            privacy = feet[2][0]
            self.assertTrue(privacy.startswith("Solo totales"), privacy)
            self.assertIn("Sin nombres ni respuestas.", privacy)
            self.assertIn("Avance de entrevistas", slides[3][2])  # the chart moved to slide 4

    def test_the_new_slide_names_nobody(self):
        """Defect: a camper's or a leader's name, a "≈" Figtree cannot draw, or "participantes" on the new slide."""
        third = self.slides(self.html("presentation_live", user=self.pastor))[2][2]
        self.assertIn("Por cuestionario", third)
        for forbidden in (self.p1.full_name, self.p2.full_name, self.coord1.get_full_name(), self.tutor1.get_full_name(),
                          "Equipo Norte", "≈", "participantes"):
            self.assertNotIn(forbidden, third, forbidden)

    def test_build_deck_copies_only_the_counts(self):
        """Defect: the deck serializer passing a questionnaire's ids (or anything else) to the projector template, or
        dropping the counts so the slide cannot be drawn."""
        deck = build_deck(build_payload(event_scope(self.event)), event_name="X", kind_label="Campamento", k=5)
        self.assertEqual(deck["progress"]["partial"], 1)
        self.assertEqual(
            deck["progress"]["questionnaires"],
            [
                {"title": "Primer cuestionario", "eligible": 5, "completed": 3, "in_progress": 0, "percent": 60},
                {"title": "Segundo cuestionario", "eligible": 5, "completed": 1, "in_progress": 1, "percent": 20},
            ],
        )

    def test_the_deck_floor_holds_on_the_new_slide(self):
        """Defect (layout): the per-questionnaire lines under the deck's 22 px floor from a 1128 px frame up."""
        css = stylesheet()
        text = declarations(css, ".deck .quests__text")["font-size"]
        self.assertGreaterEqual(float(text.removesuffix("cqw")) * 1128 / 100, 22)
        tight = declarations(css, ".deck .quests__list:has(li:nth-child(4)) .quests__text")["font-size"]
        for frame in (1129, 1211, 1404, 1744):
            self.assertGreaterEqual(round(small_print_px(tight, frame), 2), 22)

    # ---- 4. The PDF ------------------------------------------------------------------------------------------------------

    def test_the_pdf_says_it_and_has_a_table_per_questionnaire(self):
        """Defect: the cutoff's PDF reading "entrevistas", a key without the halfway camperos, no "Por cuestionario"
        table, or team rows without the line per questionnaire."""
        paragraphs, tables, keys = self.pdf(self.snapshot())
        self.assertIn("Llevábamos 1 de 5 con los 2 cuestionarios.", paragraphs)
        self.assertIn("2 sin iniciar y 1 a medias", keys)
        self.assertIn("Por cuestionario", paragraphs)
        self.assertLess(paragraphs.index("Llevábamos 1 de 5 con los 2 cuestionarios."), paragraphs.index("Por cuestionario"))
        self.assertLess(paragraphs.index("Por cuestionario"), paragraphs.index("¿Cuánto faltaba?"))
        quests = next(rows for rows in tables if rows[0] == ["Cuestionario", "Completadas", "En curso", "Avance"])
        self.assertEqual(quests[1:], [["Primer cuestionario", 3, 0, "60 %"], ["Segundo cuestionario", 1, 1, "20 %"]])
        line = "<br/><font size='8.5' color='#5A6872'>{}</font>"
        self.assertIn("Equipo Norte" + line.format("«Primer cuestionario»: 2 de 4, «Segundo cuestionario»: 0 de 4."), paragraphs)
        self.assertIn("Equipo Sur" + line.format("«Primer cuestionario»: 1 de 1, «Segundo cuestionario»: 1 de 1."), paragraphs)

    def test_the_pdf_glossary_defines_a_medias_and_por_cuestionario(self):
        """Defect: a two-questionnaire cutoff's PDF printing "a medias" and a "Por cuestionario" table without saying in
        "Cómo se calcula" what they count."""
        self.assertEqual(self.glossary(self.snapshot()), GLOSSARY_LINES + SEVERAL_LINES)

    def test_the_pdf_escapes_a_title_in_the_two_line_name(self):
        """Defect: a questionnaire title with "&" or "<" breaking ReportLab's markup in the two-line name."""
        QuestionnaireVersion.objects.filter(pk=self.first.pk).update(title="Salud & <hábitos>")
        paragraphs, _, _ = self.pdf(self.snapshot())
        self.assertIn("Equipo Sur<br/><font size='8.5' color='#5A6872'>«Salud &amp; &lt;hábitos&gt;»: 1 de 1, «Segundo cuestionario»: 1 de 1.</font>", paragraphs)

    # ---- 5. A cutoff saved before this change ----------------------------------------------------------------------------

    def old_snapshot(self):
        """A ready cutoff whose payload has neither "partial" nor "questionnaires", like every cutoff saved before Q1. Ready
        cutoffs refuse save(): the update writes the row as the old code did."""
        snapshot = self.snapshot()
        data = snapshot.data
        for row in [data["progress"], *data["teams"], *data["coordinators"]]:
            row.pop("partial")
            row.pop("questionnaires")
        ReportSnapshot.objects.filter(pk=snapshot.pk).update(data=data)
        snapshot.refresh_from_db()
        return snapshot

    def test_an_old_cutoff_renders_as_it_did(self):
        """Defect: a cutoff saved before this change raising on the missing keys, or growing a "Por cuestionario" it never
        counted, on its page, in the history, in its PDF or on its saved slides."""
        snapshot = self.old_snapshot()
        for html in (self.html("report_detail", snapshot.public_id, user=self.pastor), self.html("reports", user=self.pastor)):
            self.assertIn('<p class="voice headline">Llevábamos 1 de 5 entrevistas.</p>', html)
            for word in NEW_WORDS:
                self.assertNotIn(word, html)
        paragraphs, tables, keys = self.pdf(snapshot)
        self.assertIn("Llevábamos 1 de 5 entrevistas.", paragraphs)
        self.assertNotIn("Por cuestionario", paragraphs)
        self.assertFalse([rows for rows in tables if rows and rows[0] and rows[0][0] == "Cuestionario"])
        self.assertFalse([text for text in keys if "a medias" in text])
        self.assertFalse(set(SEVERAL_LINES) & set(paragraphs))  # nothing it prints needs them
        teams = next(rows for rows in tables if rows and rows[0][1:2] == ["Camperos"])
        self.assertEqual([row[0] for row in teams[1:]], ["Equipo Norte", "Equipo Sur"])  # one-line names, as before
        html = self.html("report_presentation", snapshot.public_id, user=self.pastor)
        self.assertEqual(len(self.slides(html)), 5)
        self.assertNotIn("Por cuestionario", html)
        deck = build_deck(snapshot.data, event_name="X", kind_label="Campamento", k=5)
        self.assertEqual((deck["progress"]["partial"], deck["progress"]["questionnaires"]), (0, []))


class OneQuestionnaireTests(PageHelpers, IsolatedCacheMixin, CampFixture):
    """The event's only questionnaire: none of the new words, blocks, lines or slides anywhere."""

    def setUp(self):
        super().setUp()
        version = self.event.questionnaire_versions()[0]
        for participant, status in ((self.p1, Interview.Status.COMPLETED), (self.p2, Interview.Status.IN_PROGRESS)):
            done = status == Interview.Status.COMPLETED
            Interview.objects.create(event=self.event, participant=participant, version=version, status=status,
                                     completed_at=timezone.now() if done else None)
        refresh_interview_statuses(self.event)

    def test_no_page_says_anything_new(self):
        """Defect: a one-questionnaire event gaining "con los 1 cuestionarios", "0 a medias", a "Por cuestionario" block, a
        line under a name or under "Completadas"."""
        snapshot = self.snapshot()
        pages = [
            (self.pastor, "pastor_home"), (self.pastor, "progress"), (self.admin, "admin_home"), (self.tutor1, "tutor_home"),
            (self.tutor1, "coordinators"), (self.pastor, "coordinators"), (self.pastor, ("coordinator_detail", self.m_coord1.id)),
            (self.pastor, "reports"), (self.pastor, ("report_detail", snapshot.public_id)),
            (self.pastor, "presentation_live"), (self.pastor, ("report_presentation", snapshot.public_id)),
        ]
        for user, name in pages:
            args = name if isinstance(name, tuple) else (name,)
            html = self.html(*args, user=user)
            for word in NEW_WORDS:
                with self.subTest(page=args[0], word=word):
                    self.assertNotIn(word.lower(), html.lower())
        self.assertIn("Llevamos 1 de 5 entrevistas.", self.html("pastor_home", user=self.pastor))

    def test_the_deck_keeps_five_slides(self):
        """Defect: a "Por cuestionario" slide (or "de 6") for an event with one questionnaire."""
        html = self.html("presentation_live", user=self.pastor)
        self.assertEqual([(n, total) for n, total, _ in self.slides(html)], [(str(n), "5") for n in range(1, 6)])
        self.assertEqual(build_deck(build_payload(event_scope(self.event)), event_name="X", kind_label="Campamento", k=5)["progress"]["questionnaires"], [])

    def test_the_pdf_says_nothing_new(self):
        """Defect: the PDF of a one-questionnaire event with a "Por cuestionario" table, "a medias" or two-line names."""
        paragraphs, tables, keys = self.pdf(self.snapshot())
        self.assertIn("Llevábamos 1 de 5 entrevistas.", paragraphs)
        for word in ("Por cuestionario", "a medias", "con los", "<br/>"):
            self.assertFalse([text for text in paragraphs + keys if word in text], word)
        self.assertFalse([rows for rows in tables if rows and rows[0] and rows[0][0] == "Cuestionario"])

    def test_the_pdf_glossary_is_the_one_it_always_was(self):
        """Defect: "Cómo se calcula" of a one-questionnaire cutoff gaining "A medias" and "Por cuestionario" (appended to
        DEFINITIONS by the first phase), numbers its PDF never prints: the one-questionnaire PDF must stay as it was."""
        lines = self.glossary(self.snapshot())
        self.assertEqual(lines, GLOSSARY_LINES)
        self.assertFalse(set(SEVERAL_LINES) & set(lines))


# ---- The history's fold ---------------------------------------------------------------------------------------------------


class HistoryFoldPerQuestionnaireTests(SimpleTestCase):
    """events.services.reports._run_key: cutoffs fold only when every questionnaire's count is the same too."""

    @staticmethod
    def snap(per_questionnaire=None, completed=1, pending=4):
        progress = {"completed": completed, "pending": pending}
        if per_questionnaire is not None:
            progress["questionnaires"] = [{"title": f"Q{i}", "completed": n} for i, n in enumerate(per_questionnaire)]
        return SimpleNamespace(status="listo", data={"progress": progress})

    def test_the_key_carries_each_questionnaires_count(self):
        """Defect: the key ignoring the questionnaires, or failing on a cutoff saved without them."""
        self.assertEqual(_run_key(self.snap((3, 1))), (1, 4, (3, 1)))
        self.assertEqual(_run_key(self.snap()), (1, 4, ()))

    def test_equal_counts_fold_and_a_moved_questionnaire_does_not(self):
        """Defect: three cutoffs folded as "Sin cambios" while camperos completed the first questionnaire (completed and
        pending only move when a campero finishes them all)."""
        same = [self.snap((3, 1)) for _ in range(4)]
        self.assertEqual([row["kind"] for row in group_history(same)], ["corte", "run"])
        moved = [self.snap((4, 1)), self.snap((3, 1)), self.snap((2, 1)), self.snap((1, 1))]
        self.assertEqual([row["kind"] for row in group_history(moved)], ["corte"] * 4)
