"""Pastoral: the decision first, and a deck that says when its numbers are from (round "cada rol sabe qué hacer").

The pastor's summary reads cómo va -> qué hay que coordinar -> equipos -> cuánto falta -> necesidades, and every
agenda item opens the page where it is done (the link is added at render time: recommendations() is frozen into
every cutoff). An event with no camperos says so once, with who acts next. The headline's sentence carries one
figure; the rate per hour lives in its tile. On the deck, slide 4 names both clocks it mixes, the presenter's bar
sits in a strip of its own under the slide, and the small print of a slide never goes under 22 px.
"""

import re
from datetime import datetime, timedelta
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from events.models import (
    Event,
    EventMembership,
    Interview,
    InterviewStatus,
    MembershipRole,
)
from events.services.event_questionnaires import use_version
from events.services.metrics import build_payload as real_build_payload
from events.services.metrics import event_scope
from events.services.reports import generate_snapshot

from .base import CampFixture
from .test_camp_visual import declarations, stylesheet

EMPTY = "Aún no hay camperos registrados."
SECTIONS = re.compile(r'<section class="report-section[^"]*" aria-labelledby="([^"]+)"')


def hhmm(moment):
    return f"{timezone.localtime(moment):%H:%M}"


def complete(event, participant):
    now = timezone.now()
    Interview.objects.create(
        event=event, participant=participant, version=event.questionnaire_versions()[0], status=Interview.Status.COMPLETED,
        completed_at=now, started_at=now - timedelta(minutes=10), follow_up_decision="no_requerido",
    )
    participant.interview_status = InterviewStatus.COMPLETED
    participant.save(update_fields=["interview_status"])


def payload_changed(change):
    """pastor_home with the real payload, changed by `change` before the template sees it."""

    def build(*args, **kwargs):
        payload = real_build_payload(*args, **kwargs)
        change(payload)
        return payload

    return patch("events.views.dashboards.build_payload", side_effect=build)


def headline_sub(html):
    match = re.search(r'<p class="headline__sub">(.*?)</p>', html, re.S)
    assert match, "the pastor headline has its sentence"
    return " ".join(match.group(1).split())


class PastorOrderTests(CampFixture):
    """UI-10: the decision comes right after "how is it going", the evidence after it."""

    ORDER = ("como-va", "acciones", "grupos", "faltan", "necesidades")

    def test_sections_read_in_the_order_of_the_meeting(self):
        self.login(self.pastor)
        html = self.client.get(self.url("pastor_home")).content.decode()
        self.assertEqual(SECTIONS.findall(html), list(self.ORDER))

    def test_the_live_fragment_keeps_the_same_order(self):
        self.login(self.pastor)
        html = self.client.get(self.url("pastor_home"), {"_": "1"}, HTTP_X_REQUESTED_WITH="fetch").content.decode()
        self.assertNotIn("<html", html)
        self.assertEqual(SECTIONS.findall(html), list(self.ORDER))

    def test_the_headline_is_a_sentence_between_the_title_and_the_sections(self):
        self.login(self.pastor)
        html = self.client.get(self.url("pastor_home")).content.decode()
        self.assertIn('<p class="voice headline headline--sentence">Llevamos 0 de 5 entrevistas.</p>', html)
        css = stylesheet()
        self.assertEqual(declarations(css, ".headline--sentence").get("font-size"), "clamp(1.75rem, 1.4rem + 1.2vw, 2.25rem)")
        self.assertEqual(declarations(css, ".headline").get("font-size"), "clamp(2rem, 1.4rem + 1.8vw, var(--t-h1))")  # elsewhere unchanged

    def test_the_only_totals_notice_keeps_a_reading_width(self):
        self.assertEqual(declarations(stylesheet(), ".report-section__body .notice").get("max-width"), "65ch")


class PastorEmptyEventTests(CampFixture):
    """UI-11: with no camperos the page says it once, and who acts next; no empty sections below."""

    def setUp(self):
        self.empty = Event.objects.create(
            church=self.church,
            name="Campamento vacío",
            slug="campamento-vacio",
            procedures_approved_at=timezone.now(),
            starts_on=timezone.localdate(),
            ends_on=timezone.localdate(),
        )
        use_version(self.empty, self.version)
        EventMembership.objects.create(event=self.empty, user=self.pastor, role=MembershipRole.PASTOR)

    def test_only_how_is_it_going_with_one_sentence(self):
        self.login(self.pastor)
        html = self.client.get(reverse("events:pastor_home", args=[self.empty.slug])).content.decode()
        self.assertEqual(html.count(EMPTY), 1)
        self.assertIn(f'<p class="voice headline headline--sentence">{EMPTY}</p>', html)
        self.assertEqual(html.count("La administración los registra o importa."), 1)
        self.assertEqual(SECTIONS.findall(html), ["como-va"])
        for absent in ('class="figures"', 'class="teams"', "pastor-acumulado", "Ritmo", 'class="agenda"', "0 de 0"):
            self.assertNotIn(absent, html)


class PastorAgendaLinkTests(CampFixture):
    """UI-05: each matter for the next meeting opens the page where it is done, for the pastor and the administration."""

    KINDS = ("pendientes", "completo", "equipo", "interrumpidas", "sin_coordinador", "sin_tutor", "autorizaciones", "por_evaluar", "seguimientos", "otra_cosa")

    def expected(self):
        return {
            "pendientes": self.url("progress"),
            "completo": None,
            "equipo": self.url("progress") + "#equipos",
            "interrumpidas": self.url("pending") + "#interrumpidas",
            "sin_coordinador": self.url("participants") + "?coordinador=ninguno",
            "sin_tutor": self.url("team") + "#sin-tutor",
            "autorizaciones": self.url("participants"),
            "por_evaluar": self.url("pending") + "#por-evaluar",
            "seguimientos": self.url("follow_ups"),
            "otra_cosa": None,  # a kind this page does not know: plain text
        }

    def items(self, user):
        def every_kind(payload):
            payload["recommendations"] = [{"kind": kind, "text": f"Asunto {kind}.", "evidence": f"Evidencia {kind}."} for kind in self.KINDS]

        self.login(user)
        with payload_changed(every_kind):
            response = self.client.get(self.url("pastor_home"))
        html = response.content.decode()
        items = re.findall(r'<li><p class="agenda__text">(.*?)</p><p class="agenda__evidence">(.*?)</p></li>', html)
        return response, html, items

    def test_every_kind_links_where_it_is_done(self):
        _, html, items = self.items(self.pastor)
        self.assertEqual(len(items), len(self.KINDS))
        self.assertIn(f"{len(self.KINDS)} asuntos para la próxima reunión.", html)
        self.assertIn("Estas acciones salen de reglas fijas", html)
        for kind, (text, evidence) in zip(self.KINDS, items):
            with self.subTest(kind):
                self.assertEqual(evidence, f"Evidencia {kind}.")
                url = self.expected()[kind]
                if url is None:
                    self.assertEqual(text, f"Asunto {kind}.")
                    self.assertNotIn("<a", text)
                else:
                    self.assertEqual(text, f'<a href="{url}">Asunto {kind}.</a>')

    def test_the_frozen_recommendations_gain_no_links(self):
        """recommendations() is stored in every cutoff and printed by the cutoff page and the PDF."""
        response, _, _ = self.items(self.pastor)
        for item in response.context["payload"]["recommendations"]:
            self.assertEqual(set(item), {"kind", "text", "evidence"})

    def test_every_target_opens_for_both_roles_of_the_summary(self):
        for user in (self.pastor, self.admin):
            self.login(user)
            for kind, url in self.expected().items():
                if url is None:
                    continue
                with self.subTest(user=user.email, kind=kind):
                    self.assertEqual(self.client.get(url.split("#")[0]).status_code, 200)

    def test_real_recommendations_link_too(self):
        """Without a patch: the fixture's 5 interviews still to do."""
        self.login(self.pastor)
        html = self.client.get(self.url("pastor_home")).content.decode()
        self.assertIn(f'<p class="agenda__text"><a href="{self.url("progress")}">Faltan 5 entrevistas de 5.</a></p>', html)


class PastorHeadlineSentenceTests(CampFixture):
    """LANG-32: one figure per sentence read aloud. The rate per hour stays in its tile."""

    def assert_sub(self, expected, change=None):
        self.login(self.pastor)
        if change:
            with payload_changed(change):
                html = self.client.get(self.url("pastor_home")).content.decode()
        else:
            html = self.client.get(self.url("pastor_home")).content.decode()
        sub = headline_sub(html)
        self.assertNotIn("por hora", sub)
        if isinstance(expected, re.Pattern):
            self.assertRegex(sub, expected)
        else:
            self.assertEqual(sub, expected)
        return html

    def test_a_clock_time_within_reach(self):
        for participant in (self.p1, self.p2, self.p3):
            complete(self.event, participant)  # 1.5 per hour, 2 pending: 80 minutes
        html = self.assert_sub(re.compile(r"^Al ritmo de las últimas dos horas terminaríamos hacia las \d\d:\d\d\.$"))
        tile = re.search(r"<div><dt>Ritmo</dt>(.*?)</div>", html, re.S)
        self.assertIsNotNone(tile)
        self.assertIn("por hora", tile.group(1))
        self.assertIn("alrededor de 1 hora 20 minutos al ritmo actual", html)  # the answer of ¿Cuánto falta? keeps its words

    def test_beyond_twelve_hours(self):
        def slow(payload):
            payload["throughput"].update(per_hour=0.3, eta_minutes=900)

        self.assert_sub("Al ritmo de las últimas dos horas faltan más de 12 horas.", slow)

    def test_no_pace_yet(self):
        self.assert_sub("Todavía no hay suficiente ritmo para estimar cuándo terminaremos.")

    def test_all_done(self):
        def done(payload):
            payload["progress"]["pending"] = 0

        self.assert_sub("Ya se completaron todas.", done)


class PastorFaltanFigureTests(CampFixture):
    """LANG-19: interviews still to do are "Faltan"; "sin atender" stays for follow-ups."""

    def test_the_interviews_tile_says_faltan(self):
        self.login(self.pastor)
        html = self.client.get(self.url("pastor_home")).content.decode()
        self.assertIn("<dt>Faltan</dt>", html)
        self.assertNotIn("<dt>Pendientes</dt>", html)
        self.assertIn("sin atender", html)


class DeckClocksTests(CampFixture):
    """UI-17: live, slide 4 mixes the last cutoff's follow-ups with live numbers, so it names both clocks, once. A saved
    deck has one clock, its cutoff (cutoff_at), on every slide: the time its payload was computed (data_as_of, when
    the scheduler ran) is never called "corte"."""

    BOTH = re.compile(r"Según el corte de las (\d\d:\d\d); lo demás, datos de las (\d\d:\d\d)\.")

    def setUp(self):
        self.cutoff_at = timezone.now().replace(second=0, microsecond=0) - timedelta(hours=1)
        self.snapshot = generate_snapshot(self.event, event_scope(self.event), trigger="automatico", cutoff_at=self.cutoff_at)
        self.login(self.pastor)

    def slide(self, html, n):
        match = re.search(rf'aria-label="Diapositiva {n} de 5">(.*?)</section>', html, re.S)
        self.assertIsNotNone(match, n)
        return match.group(1)

    def assert_both_clocks(self, html, data_at):
        slide4 = self.slide(html, 4)
        match = self.BOTH.search(slide4)
        self.assertIsNotNone(match, slide4)
        self.assertEqual(match.group(1), hhmm(self.cutoff_at))
        self.assertEqual(match.group(2), data_at)
        self.assertEqual(len(self.BOTH.findall(html)), 1, "named once, on the slide that mixes them")

    def test_live_deck(self):
        html = self.client.get(self.url("presentation_live")).content.decode()
        top = re.search(r"en vivo, datos de las (\d\d:\d\d)", self.slide(html, 4))
        self.assertIsNotNone(top)
        self.assert_both_clocks(html, top.group(1))
        self.assertEqual(html.count("data-deck-live"), 5)

    def test_saved_deck(self):
        cut, computed = hhmm(self.cutoff_at), hhmm(datetime.fromisoformat(self.snapshot.data["data_as_of"]))
        self.assertNotEqual(cut, computed)  # the fixture keeps the two clocks apart
        html = self.client.get(self.url("report_presentation", self.snapshot.public_id)).content.decode()
        self.assertIn(f"Corte de las {cut} del ", self.slide(html, 1))
        for n in (2, 3, 4, 5):
            with self.subTest(slide=n):
                self.assertIn(f", corte de las {cut}</span>", self.slide(html, n))
        self.assertIn(f"Según el corte de las {cut}. Los totales se redondean", self.slide(html, 4))
        self.assertEqual(set(re.findall(r"[Cc]orte de las (\d\d:\d\d)", html)), {cut})
        self.assertIsNone(self.BOTH.search(html))
        self.assertNotIn("lo demás", html)
        self.assertNotIn("data-deck-live", html)


class DeckBeforeTheFirstCutoffTests(CampFixture):
    def test_slide_four_keeps_its_sentence(self):
        """No cutoff yet (cutoffs are immutable, so this is its own fixture): no follow-up clock to name."""
        self.login(self.pastor)
        html = self.client.get(self.url("presentation_live")).content.decode()
        self.assertIn("Estas cifras aparecerán con el primer corte.", html)
        self.assertIsNone(DeckClocksTests.BOTH.search(html))


def frame_width(viewport_w, viewport_h, strip_px):
    """.deck__frame's width: min(100vw - 4vmin, (100vh - 4vmin - var(--deck-controls-h)) * 16 / 9)."""
    four_vmin = 0.04 * min(viewport_w, viewport_h)
    return min(viewport_w - four_vmin, (viewport_h - four_vmin - strip_px) * 16 / 9)


def small_print_px(declaration, frame):
    """font-size: max(A cqw, min(22px, B cqw)) in px for a frame that wide."""
    match = re.fullmatch(r"max\(([\d.]+)cqw, min\(22px, ([\d.]+)cqw\)\)", declaration)
    assert match, declaration
    today, scaled = (float(g) * frame / 100 for g in match.groups())
    return max(today, min(22, scaled))


class DeckControlsStripTests(CampFixture):
    """UI-16: the presenter's bar sits in a strip the slide does not use, so it never covers the foot. The small print
    of a slide (team rows at every count included) has a 22 px floor from 1280 x 720 up; past a 1571 px frame its cqw
    size takes over (1920 x 1080, though the strip makes that frame about 5 % smaller); and a smaller frame shrinks
    the floor with it: a fixed 22 px pushed slide 4 under the foot at 1024 x 768 always, and slide 2 from four teams
    (measured in headless Chrome: +11 px on slide 4; +10.7 px with 4 teams, +7.6 with 5, +35.1 with 8 on slide 2)."""

    SMALL_PRINT = (
        (".slide__foot", 1.4),
        (".slide__top", 1.46),
        (".deck-note", 1.46),
        (".deck-key", 1.56),
        (".deck-teams__list li", 1.77),
        (".deck-teams__list:has(li:nth-child(5)) li", 1.56),  # 5 and 6 teams
        (".deck-teams__list:has(li:nth-child(7)) li", 1.46),  # 7 and 8 teams
    )

    def test_the_frame_leaves_the_strip_to_the_bar(self):
        css = stylesheet()
        self.assertEqual(declarations(css, ".deck").get("--deck-controls-h"), "3.5rem")
        self.assertEqual(declarations(css, ".deck").get("padding-bottom"), "calc(2vmin + var(--deck-controls-h))")
        self.assertEqual(
            declarations(css, ".deck__frame").get("width"), "min(100vw - 4vmin, (100vh - 4vmin - var(--deck-controls-h)) * 16 / 9)"
        )
        bar = declarations(css, ".deck-controls")
        self.assertEqual(bar.get("position"), "fixed")
        self.assertEqual(bar.get("bottom"), "0")
        # at least the strip; a bar that wraps (a phone, or a long status on any screen) grows upward, never off the screen
        self.assertEqual(bar.get("min-height"), "var(--deck-controls-h)")
        # one row of 44px buttons fills the strip exactly: (56 - 44) / 2 - 1.5 px of border on each side
        self.assertEqual(bar.get("padding"), "calc((var(--deck-controls-h) - 2.75rem) / 2 - var(--b-control)) var(--s-2)")
        self.assertEqual(declarations(css, ".deck-controls .btn").get("min-height"), "2.75rem")

    def test_small_print_has_a_px_floor(self):
        css = stylesheet()
        projector, xga = frame_width(1920, 1080, 56), frame_width(1024, 768, 56)
        laptops = [frame_width(w, h, 56) for w, h in ((1280, 720), (1366, 768), (1440, 900))]
        for selector, cqw in self.SMALL_PRINT:
            with self.subTest(selector):
                declaration = declarations(css, selector).get("font-size")
                self.assertEqual(declaration, f"max({cqw}cqw, min(22px, 1.95cqw))")
                for frame in laptops:
                    self.assertGreaterEqual(round(small_print_px(declaration, frame), 2), 22)
                self.assertEqual(round(small_print_px(declaration, laptops[1]), 2), 22)  # 1366 x 768: exactly the floor
                self.assertAlmostEqual(small_print_px(declaration, projector), cqw * projector / 100)  # its cqw size
                self.assertGreater(small_print_px(declaration, xga), cqw * xga / 100)  # still larger than before
                self.assertLess(small_print_px(declaration, xga), 22)

    def test_chart_text_keeps_its_pinned_sizes(self):
        css = stylesheet()
        self.assertEqual(declarations(css, ".deck-chart text").get("font-size"), "27px")
        self.assertEqual(declarations(css, ".deck-chart .label-strong").get("font-size"), "32px")

    def test_the_foot_keeps_its_shape(self):
        self.login(self.pastor)
        html = self.client.get(self.url("presentation_live")).content.decode()
        feet = re.findall(r'<div class="slide__foot"><span>(.*?)</span><span>(.*?)</span></div>', html)
        self.assertEqual([number for _, number in feet], [f"{n} de 5" for n in range(1, 6)])
