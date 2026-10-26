"""The "Rótulo" design foundation: scoping, progress marks, chart projection and the redesigned screens."""

import math
import re
from unittest import mock

from django.conf import settings
from django.template import Context, Template
from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse

from events.charts import _label_indexes, columns_chart, cumulative_chart
from events.models import Interview
from events.views import dashboards

from .base import CampFixture


def render(source, **context):
    return Template("{% load emmaus %}" + source).render(Context(context))


def submit_directions(html):
    """The `direction` values of the step's submit buttons, in tree order (the first one is what Enter sends)."""
    buttons = re.findall(r"<button\b[^>]*>", html)
    return [re.search(r'value="(\w+)"', b).group(1) for b in buttons if 'name="direction"' in b]


class ProgressMarkTests(SimpleTestCase):
    def test_tally_draws_one_mark_per_person(self):
        html = render("{% tally 3 1 6 %}")
        self.assertEqual(html.count('class="tally__mark'), 6)
        self.assertEqual(html.count("tally__mark--done"), 3)
        self.assertEqual(html.count("tally__mark--progress"), 1)
        self.assertIn('aria-label="3 de 6 completadas, 1 en curso, 2 sin iniciar"', html)

    def test_tally_falls_back_to_a_ruler_for_large_groups(self):
        html = render("{% tally 50 10 120 %}")
        self.assertNotIn("tally__mark", html)
        self.assertIn('class="ruler"', html)

    def test_ruler_marker_label_is_escaped(self):
        html = render("{% ruler 1 0 4 50 label %}", label='"><script>')
        self.assertNotIn("<script>", html)
        self.assertIn("left:50.00%", html)

    def test_status_never_relies_on_colour_alone(self):
        html = render('{% status "en_curso" %}')
        self.assertIn("status--progress", html)
        self.assertIn("En curso", html)


class StylesheetTests(SimpleTestCase):
    def test_rotulo_css_blocks_are_balanced(self):
        """An unclosed block swallows every rule after it (a merge once dropped three closing braces)."""
        css = (settings.BASE_DIR / "static" / "css" / "rotulo.css").read_text()
        body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        depth = 0
        for line_number, line in enumerate(body.split("\n"), 1):
            depth += line.count("{") - line.count("}")
            self.assertGreaterEqual(depth, 0, f"extra closing brace near line {line_number}")
        self.assertEqual(depth, 0, "a block is never closed")


class ChartTests(SimpleTestCase):
    points = [{"label": f"{h:02d}:00", "day": "", "count": 10, "cumulative": 10 * (h + 1)} for h in range(10)]

    def test_axis_labels_always_include_the_last_point_without_crowding(self):
        for max_labels in (4, 7):
            for count in range(1, 60):
                every = math.ceil(count / max_labels)
                indexes = sorted(_label_indexes(count, max_labels))
                self.assertEqual((indexes[0], indexes[-1]), (0, count - 1))
                gaps = [b - a for a, b in zip(indexes, indexes[1:])]
                # No two labels closer than three quarters of the regular spacing (the old "09:0010:00" collision)
                self.assertTrue(all(g >= every * 0.75 for g in gaps), (count, indexes))
                self.assertLessEqual(len(indexes), max_labels + 1)

    def test_gridlines_land_on_round_numbers(self):
        from events.charts import _round_step

        for total in (3, 7, 12, 31, 96, 180, 253, 400, 1200):
            html = cumulative_chart(self.points, total, chart_id="c", title="t")
            ticks = [int(t) for t in re.findall(r'text-anchor="end" font-size="13">(\d+)</text>', html)]
            gaps = {b - a for a, b in zip(ticks, ticks[1:])}
            self.assertEqual(len(gaps), 1, (total, ticks))
            self.assertTrue(_round_step(gaps.pop()), (total, ticks))

    def test_last_value_never_sits_on_the_total_label(self):
        points = [{"label": f"{h:02d}:00", "day": "", "count": 2, "cumulative": 2 * (h + 1) + 6} for h in range(10)]  # ends at 26
        html = cumulative_chart(points, 31, chart_id="c", title="t")
        value_y = float(re.search(r'class="label-strong" x="[\d.]+" y="([\d.]+)"', html).group(1))
        total_y = float(re.search(r'y="([\d.]+)" font-size="13">Total 31<', html).group(1))
        self.assertGreater(abs(value_y - total_y), 13 * 1.2)

    def test_projection_runs_to_the_total_only_when_asked(self):
        plain = cumulative_chart(self.points, 200, chart_id="c", title="t")
        self.assertNotIn("projection", plain)
        projected = cumulative_chart(self.points, 200, chart_id="c", title="t", eta_minutes=180, projection_label="Hacia las 16:00")
        self.assertIn('class="projection"', projected)
        self.assertIn("Hacia las 16:00", projected)

    def test_no_projection_once_the_total_is_reached(self):
        done = cumulative_chart(self.points, 100, chart_id="c", title="t", eta_minutes=30)
        self.assertNotIn('class="projection"', done)

    def test_a_day_on_the_first_time_label_never_runs_into_the_next(self):
        """"25/10 18:00" is twice as wide as "20:00": spaced as if it were not, it ran into the next label
        ("25/10 1820:00") on the pastor's chart, whose axis also makes room for the projection."""
        from events.charts import CHAR_EM

        points = [{"label": f"{(18 + h) % 24:02d}:00", "day": "25/10" if h == 0 else "", "count": 24, "cumulative": 24 * (h + 1)} for h in range(10)]
        for kwargs, size in (({}, 13), ({"compact": True}, 12), ({"deck": True}, 27)):
            for eta in (None, 420):
                html = cumulative_chart(points, 400, chart_id="c", title="t", eta_minutes=eta, **kwargs)
                labels = re.findall(r'<text x="([\d.]+)" y="\d+" text-anchor="(\w+)" font-size="\d+">([^<]*\d\d:\d\d)</text>', html)
                self.assertTrue(labels[0][2].startswith("25/10 "), labels)
                self.assertTrue(labels[-1][2].endswith("03:00"), labels)  # the last point keeps its label
                boxes = []
                for x, anchor, text in labels:
                    width = len(text) * size * CHAR_EM
                    x0 = float(x) - width if anchor == "end" else float(x) - width / 2 if anchor == "middle" else float(x)
                    boxes.append((x0, x0 + width, text))
                for (a0, a1, a), (b0, b1, b) in zip(boxes, boxes[1:]):
                    self.assertLessEqual(a1 + size * 0.5, b0, (kwargs, eta, a, b))

    def test_few_points_and_a_long_projection_never_overlap_their_labels(self):
        """With 2 to 4 hourly points and a far finish (ETA 420 or 900 min) the first, dated label and the last one
        sat 23 to 31 px over each other: both were kept. The last label stays; the first loses its day, or goes.
        Boxes come from the label strings themselves, so the copy branch's wider "25 oct" is covered too."""
        from events.charts import CHAR_EM

        for day in ("25/10", "25 oct"):
            for n in (2, 3, 4):
                points = [{"label": f"{18 + h:02d}:00", "day": day if h == 0 else "", "count": 3, "cumulative": 3 * (h + 1)} for h in range(n)]
                for kwargs, size in (({}, 13), ({"compact": True}, 12), ({"deck": True}, 27)):
                    for eta in (None, 420, 900):
                        html = cumulative_chart(points, 400, chart_id="c", title="t", eta_minutes=eta, **kwargs)
                        labels = re.findall(r'<text x="([\d.]+)" y="\d+" text-anchor="(\w+)" font-size="\d+">([^<]*\d\d:\d\d)</text>', html)
                        case = (day, n, kwargs, eta, labels)
                        self.assertEqual(labels[-1][2], points[-1]["label"], case)  # the latest hour always keeps its label
                        if len(labels) > 1:
                            self.assertIn(labels[0][2], (f"{day} 18:00", "18:00"), case)
                        boxes = []
                        for x, anchor, text in labels:
                            width = len(text) * size * CHAR_EM
                            x0 = float(x) - width if anchor == "end" else float(x) - width / 2 if anchor == "middle" else float(x)
                            boxes.append((x0, x0 + width))
                        for (_, a1), (b0, _) in zip(boxes, boxes[1:]):
                            self.assertLessEqual(a1 + size * 0.5, b0, case)

    def test_partial_last_column_is_marked(self):
        html = columns_chart(self.points, chart_id="c", title="t", partial_last=True)
        self.assertEqual(html.count("col--partial"), 1)
        self.assertIn("(hora en curso)", html)


class RedesignedScreenTests(CampFixture):
    def test_every_screen_of_every_role_is_on_rotulo(self):
        """One design system: no role ever crosses into the old stylesheet, which no longer exists."""
        self.assertFalse((settings.BASE_DIR / "static" / "css" / "emmaus.css").exists())
        pages = {
            self.coord1: ["coord_home", "my_participants", "coord_pending", "coord_progress", "participant_create", "ayuda",
                          ("participant_detail", self.p1.public_id), "follow_ups", "backup_list", "interview_search",
                          ("guardian_authorization", self.p_minor_no_auth.public_id), ("guardian_authorization_withdraw", self.p1.public_id)],
            self.tutor1: ["tutor_home", "coordinators", "interviews", "pending", "reports", "follow_ups", "ayuda"],
            self.pastor: ["pastor_home", "progress", "follow_ups", "reports", "team", "presentation_live", "ayuda"],
            self.admin: ["admin_home", "participants", "participant_import", "team", "assignments", "questionnaires",
                         "settings", "audit", "sessions", "reports", "member_add", "join_code_sign", "sedes", "sede_add", "ayuda"],
        }
        for user, names in pages.items():
            client = Client()
            self.login(user, client)
            self.choose_station(client)
            for name in names:
                url = self.url(*name) if isinstance(name, tuple) else self.url(name)
                response = client.get(url)
                self.assertEqual(response.status_code, 200, url)
                self.assertContains(response, "css/rotulo.css", msg_prefix=url)
                self.assertNotContains(response, "emmaus.css", msg_prefix=url)
        for url in (reverse("login"), reverse("landing"), reverse("password_reset"), reverse("events:list"),
                    reverse("join"), reverse("event_entry", args=[self.event.join_code])):
            response = (self.client if url != reverse("events:list") else self.login(self.admin, Client())).get(url)
            self.assertContains(response, "css/rotulo.css", msg_prefix=url)

    @override_settings(ROOT_URLCONF="core.test_camp_flow")  # the project's routes plus two that fail on purpose
    def test_error_pages_are_on_rotulo(self):
        """400, 403, 404, 500 and an expired form: Rótulo, one heading, never Django's own page."""
        client = Client(raise_request_exception=False)
        self.login(self.coord1, client)
        expired = Client(enforce_csrf_checks=True)
        self.login(self.coord1, expired)
        with self.assertLogs("django", "WARNING"):  # each failure is still logged
            responses = {
                400: client.get("/prueba/sospechosa/"),
                403: client.get(self.url("admin_home")),
                404: client.get("/no-existe/"),
                500: client.get("/prueba/falla/"),
                "csrf": expired.post(reverse("logout")),
            }
        for name, response in responses.items():
            status = 403 if name == "csrf" else name
            self.assertEqual(response.status_code, status, name)
            html = response.content.decode()
            self.assertIn("css/rotulo.css", html, name)
            self.assertNotIn("emmaus.css", html, name)
            self.assertEqual(len(re.findall(r"<h1\b", html)), 1, name)
            self.assertIn('href="#contenido"', html, name)

    def test_coordinator_sign_switches_to_the_open_interview(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p1)
        response = self.client.get(self.url("coord_home"))
        self.assertContains(response, "Termina la entrevista de Mariana López Ruiz")
        interview = Interview.objects.get(participant=self.p1)
        self.assertContains(response, self.url("interview_resume", interview.public_id))
        self.assertContains(response, 'class="tally"')

    def test_question_screen_has_trail_and_one_heading(self):
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p1)
        interview = Interview.objects.get(participant=self.p1)
        response = self.client.get(self.url("interview_step", interview.public_id, 1))
        html = response.content.decode()
        self.assertEqual(len(re.findall(r"<h1\b", html)), 1)
        self.assertIn('class="trail__blazes"', html)
        self.assertIn("is-here", html)
        self.assertIn("Computadora 1", html)

    def test_enter_on_a_question_moves_forward(self):
        """Enter submits the first submit button of the form in tree order: it must be "next", never "Atrás" or "Guardar y salir"."""
        self.login(self.coord1)
        self.choose_station()
        self.start(self.p1)
        interview = Interview.objects.get(participant=self.p1)
        self.answer(interview, "salvo", "si")
        directions = submit_directions(self.client.get(self.url("interview_step", interview.public_id, 2)).content.decode())
        self.assertEqual(directions[0], "next")
        self.assertIn("back", directions)
        self.assertIn("exit", directions)

    def test_enter_on_a_campero_question_moves_forward(self):
        self.login(self.coord1)
        self.choose_station(number=5)
        self.start(self.p1, mode="supervisada")
        interview = Interview.objects.get(participant=self.p1)
        self.client.post(self.url("interview_handoff", interview.public_id))
        self.answer(interview, "salvo", "no_seguro", url_name="kiosk")
        directions = submit_directions(self.client.get(reverse("kiosk:step", args=[2])).content.decode())
        self.assertEqual(directions[0], "next")
        self.assertIn("back", directions)

    def test_projection_only_when_the_finish_is_within_reach(self):
        real = dashboards.build_payload

        def paced(eta):
            def build(*args, **kwargs):
                payload = real(*args, **kwargs)
                payload["throughput"].update(eta_minutes=eta, per_hour=0.5)
                payload["timeline"]["points"] = [{"t": "", "label": f"{h:02d}:00", "day": "", "count": 1, "cumulative": h + 1} for h in range(4)]
                return payload
            return build

        self.login(self.pastor)
        with mock.patch.object(dashboards, "build_payload", paced(23000)):
            response = self.client.get(self.url("pastor_home"))
        self.assertNotContains(response, 'class="projection"')
        self.assertContains(response, "faltan más de 12 horas")
        with mock.patch.object(dashboards, "build_payload", paced(120)):
            response = self.client.get(self.url("pastor_home"))
        self.assertContains(response, 'class="projection"')
        self.assertContains(response, "Hacia las ")

    def test_admin_search_uses_the_leader_shell(self):
        self.login(self.admin)
        self.choose_station()
        response = self.client.get(self.url("interview_search"), {"q": "mar rui"})
        self.assertContains(response, "css/rotulo.css")
        self.assertContains(response, f'<a href="{reverse("events:list")}?todos=1">Campamentos</a>')
        self.assertContains(response, "Mariana López Ruiz")

    def test_my_participants_search_matches_words_in_any_order(self):
        self.login(self.coord1)
        response = self.client.get(self.url("my_participants"), {"q": "rui mar"})
        self.assertContains(response, "Mariana López Ruiz")
        self.assertNotContains(response, "Diego Hernández Cruz")

    def test_search_matches_the_first_letters_of_each_name(self):
        self.login(self.coord1)
        self.choose_station()
        response = self.client.get(self.url("interview_search"), {"q": "mar rui"})
        self.assertContains(response, "Mariana López Ruiz")
        self.assertNotContains(response, "Diego Hernández Cruz")
        response = self.client.get(self.url("interview_search"), {"q": "rui mar"})
        self.assertContains(response, "Mariana López Ruiz")

    def test_pastor_report_reads_as_questions(self):
        self.login(self.pastor)
        response = self.client.get(self.url("pastor_home"))
        for question in ("¿Cómo va el campamento?", "¿Cuánto falta?", "¿Qué equipos necesitan apoyo?", "¿Qué necesidades hay?", "¿Qué hay que coordinar?"):
            self.assertContains(response, question)
        self.assertContains(response, "Llevamos 0 de 5 entrevistas.")


def stylesheet_text():
    return (settings.BASE_DIR / "static" / "css" / "rotulo.css").read_text()


def root_tokens(css, selector=":root"):
    """Custom properties declared in the first block of `selector` (comments dropped)."""
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    block = re.search(re.escape(selector) + r"\s*\{(.*?)\}", body, re.S).group(1)
    return {name: value.strip() for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block)}


def contrast(a, b):
    def luminance(hex_colour):
        channels = [int(hex_colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class AtrioFoundationTests(SimpleTestCase):
    """The "Atrio" skin: one family (Figtree), one action colour (petróleo), chile for risk, no yellow."""

    RETIRED = (
        "#f4c20d", "#fdf3cc", "#1f3c8f", "#16306f", "#e8ecf7", "#7d93cf", "#1c1b19", "#f3f2ee", "#4d4a44", "#68645c",
        "#dedbd3", "#8b877e", "#e7e5df", "#eceae4", "#8fa6e8", "#4b63a8", "#6b74a0", "#f4f3ef", "#222b4d",
    )

    def test_figtree_is_the_only_family(self):
        css = stylesheet_text()
        faces = re.findall(r"@font-face\s*\{(.*?)\}", css, re.S)
        self.assertEqual(len(faces), 1)
        self.assertIn('font-family: "Figtree"', faces[0])
        self.assertIn('url("../fonts/figtree-latin.woff2")', faces[0])
        self.assertIn("font-weight: 400 800", faces[0])
        self.assertIn("font-display: swap", faces[0])
        self.assertTrue(root_tokens(css)["--font-ui"].startswith('"Figtree"'))
        for retired in ("Recursive", "Atkinson", "CASL", "--font-voice"):
            self.assertNotIn(retired, css)
        fonts = settings.BASE_DIR / "static" / "fonts"
        self.assertEqual(sorted(p.name for p in fonts.iterdir()), ["OFL-Figtree.txt", "figtree-latin.woff2"])
        pdf_fonts = settings.BASE_DIR / "events" / "fonts"
        self.assertEqual(sorted(p.name for p in pdf_fonts.iterdir()),
                         ["Figtree-Bold.ttf", "Figtree-ExtraBold.ttf", "Figtree-Regular.ttf", "OFL-Figtree.txt"])

    def test_the_unicode_range_is_what_the_font_contains(self):
        """The range claimed 19 of 128 Latin Extended-A and 27 of 112 General Punctuation characters the file lacks, so
        the browser waited for Figtree and then fell back anyway. The web file is the subset of these requested blocks
        (fonts-atrio MANIFEST) out of the same font as the PDF's TTF, so the expected set is the TTF's cmap within them."""
        from reportlab.pdfbase.ttfonts import TTFontFile

        requested = set(range(0x20, 0x7F)) | set(range(0xA0, 0x180)) | set(range(0x2000, 0x2070)) | set(range(0x20A0, 0x20D0)) | {0x2212}
        font = TTFontFile(str(settings.BASE_DIR / "events" / "fonts" / "Figtree-Regular.ttf"))
        expected = set(font.charToGlyph) & requested
        declared = re.search(r"unicode-range:\s*([^;]+);", stylesheet_text()).group(1)
        claimed = set()
        for item in declared.split(","):
            first, _, last = item.strip().removeprefix("U+").partition("-")
            claimed.update(range(int(first, 16), int(last or first, 16) + 1))
        self.assertEqual(len(expected), 317)
        self.assertEqual(claimed, expected)
        self.assertNotIn(0x2248, claimed)  # "≈": the system font draws it

    def test_the_skeleton_preloads_the_one_font_file(self):
        skeleton = (settings.BASE_DIR / "templates" / "layout" / "skeleton.html").read_text()
        preloads = re.findall(r'<link rel="preload" href="\{% static \'([^\']+)\' %\}" as="font"', skeleton)
        self.assertEqual(preloads, ["fonts/figtree-latin.woff2"])
        self.assertTrue((settings.BASE_DIR / "static" / preloads[0]).is_file())

    def test_the_tokens_are_atrio(self):
        tokens = root_tokens(stylesheet_text())
        expected = {
            "--page": "#ffffff", "--surface": "#ffffff", "--hundido": "#f3f5f6", "--ink": "#16222b", "--ink-2": "#44525d",
            "--ink-3": "#5a6872", "--line": "#dce1e4", "--line-strong": "#7b8a94", "--petroleo": "#0b6270",
            "--petroleo-hover": "#084e5a", "--petroleo-tint": "#e4f1f3", "--mark": "var(--petroleo)", "--chile": "#b8321c",
            "--chile-tint": "#fbe6e1", "--data-done": "#0b6270", "--data-progress": "#4c95a2", "--data-track": "#ffffff",
            "--r-control": "10px", "--b-control": "1.5px",
            "--dur-1": "120ms", "--dur-2": "200ms", "--dur-3": "320ms", "--ease": "cubic-bezier(.2,.7,.2,1)", "--rise": "6px",
        }
        for name, value in expected.items():
            self.assertEqual(tokens.get(name), value, name)
        for retired in ("--maiz", "--maiz-tint", "--anil", "--carbon", "--cal", "--cal-2", "--b-sign"):
            self.assertNotIn(retired, tokens)

    def test_the_old_palette_is_gone_from_the_skin(self):
        """No yellow, no añil, no carbón: not in the sheet, the charts, the PDF, the deck or the favicon."""
        files = [
            settings.BASE_DIR / "static" / "css" / "rotulo.css",
            settings.BASE_DIR / "events" / "charts.py",
            settings.BASE_DIR / "events" / "services" / "pdf.py",
            settings.BASE_DIR / "events" / "services" / "presentation.py",
            settings.BASE_DIR / "templates" / "layout" / "skeleton.html",
            settings.BASE_DIR / "static" / "img" / "emmaus-mark.svg",  # the favicon
            *sorted((settings.BASE_DIR / "templates" / "events" / "presentation").glob("*.html")),
        ]
        for path in files:
            text = path.read_text().lower()
            for colour in self.RETIRED:
                self.assertNotIn(colour, text, f"{path.name}: {colour}")
            self.assertNotRegex(text, r"var\(--(maiz|anil|carbon|cal|cal-2)\)", path.name)

    def test_the_church_colours_land_on_declared_tokens(self):
        """The church's own palette (layout/skeleton.html) must name tokens the sheet uses, or it silently does nothing."""
        skeleton = (settings.BASE_DIR / "templates" / "layout" / "skeleton.html").read_text()
        hooked = re.findall(r"(--[\w-]+):\{\{", skeleton)
        self.assertEqual(hooked[:3], ["--petroleo", "--petroleo-hover", "--mark"])
        declared = root_tokens(stylesheet_text())
        for name in hooked:
            self.assertIn(name, declared)

    def test_text_and_marks_meet_contrast_on_their_backgrounds(self):
        """Computed from the tokens, not eyeballed: text 4.5:1, marks and control edges 3:1 (WCAG 1.4.3, 1.4.11)."""
        css = stylesheet_text()
        cal, noche = root_tokens(css), root_tokens(css, ".deck--noche")
        white = "#ffffff"
        text = [(fg, bg) for fg in ("--ink", "--ink-2", "--ink-3", "--petroleo", "--chile") for bg in ("--page", "--hundido", "--petroleo-tint")]
        for fg, bg in text:
            self.assertGreaterEqual(contrast(cal[fg], cal[bg]), 4.5, (fg, bg))
        self.assertGreaterEqual(contrast(cal["--chile"], cal["--chile-tint"]), 4.5)
        self.assertGreaterEqual(contrast(cal["--ink"], cal["--chile-tint"]), 4.5)
        for fill in ("--petroleo", "--petroleo-hover", "--chile"):  # white words on filled buttons and the confirmation
            self.assertGreaterEqual(contrast(white, cal[fill]), 4.5, fill)
        for mark in ("--data-done", "--data-progress", "--line-strong", "--chile"):
            self.assertGreaterEqual(contrast(cal[mark], white), 3, mark)
        self.assertGreaterEqual(contrast(cal["--line-strong"], cal["--petroleo-tint"]), 3)  # the search field on the panel
        for fg in ("--ink", "--ink-2", "--ink-3"):
            self.assertGreaterEqual(contrast(noche[fg], noche["--page"]), 4.5, ("noche", fg))
        for mark in ("--data-done", "--data-progress", "--line-strong"):
            self.assertGreaterEqual(contrast(noche[mark], noche["--page"]), 3, ("noche", mark))

    def test_rounded_counts_are_words(self):
        """Figtree has no "≈": a rounded count reads "unas 85" (people)."""
        from events.services.metrics import suppress

        self.assertEqual(suppress(87, 5)["display"], "unas 85")
        self.assertEqual(suppress(87, 5)["value"], 85)
        self.assertEqual(suppress(3, 5)["display"], "Menos de 5")


class MotionTests(SimpleTestCase):
    """Eight short motion rules; prefers-reduced-motion must switch off every one of them."""

    def reduced_block(self, css):
        body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        start = body.index("@media (prefers-reduced-motion: reduce)")
        depth, i = 0, body.index("{", start)
        for j in range(i, len(body)):
            depth += (body[j] == "{") - (body[j] == "}")
            if depth == 0:
                return body[i + 1:j], body[:start] + body[j + 1:]
        self.fail("the reduced-motion block is never closed")

    def test_reduced_motion_turns_every_animation_and_transition_off(self):
        reduced, rest = self.reduced_block(stylesheet_text())
        compact = re.sub(r"\s+", " ", reduced)
        self.assertIn("@view-transition { navigation: none; }", compact)
        self.assertRegex(compact, r"\*, \*::before, \*::after, ::file-selector-button \{ animation: none !important; transition: none !important; \}")
        self.assertRegex(compact, r"::view-transition-group\(\*\), ::view-transition-old\(\*\), ::view-transition-new\(\*\) \{ animation: none !important; \}")
        # Every animated or transitioned selector is an element, ::before/::after, ::file-selector-button or a view
        # transition pseudo-element: all of them are covered above. A new pseudo-element would not be.
        covered = {"::before", "::after", "::file-selector-button", "::view-transition-old", "::view-transition-new", "::view-transition-group"}
        moving = 0
        for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", rest):
            if not re.search(r"(^|;)\s*(animation|transition)(-[\w-]+)?\s*:", declarations):
                continue
            moving += 1
            for pseudo in re.findall(r"::[\w-]+", selectors):
                self.assertIn(pseudo, covered, selectors.strip())
        self.assertGreater(moving, 10)

    def test_every_animation_names_a_defined_keyframes(self):
        css = stylesheet_text()
        defined = set(re.findall(r"@keyframes ([\w-]+)", css))
        used = set()
        for value in re.findall(r"animation\s*:\s*([^;}]+)", css):
            used.update(word for word in re.findall(r"[\w-]+", value) if word.startswith("atrio-"))
        self.assertTrue(used)
        self.assertLessEqual(used, defined)

    def test_motion_uses_the_tokens(self):
        """No literal durations: every transition and animation is timed by --dur-1/2/3 (or is switched off)."""
        _, rest = self.reduced_block(stylesheet_text())
        for prop, value in re.findall(r"\b(animation|transition)\s*:\s*([^;}]+)", rest):
            if value.strip() == "none":
                continue
            literal = re.findall(r"(?<![\w.-])(\d*\.?\d+)(ms|s)\b", value)
            self.assertTrue(all(float(number) == 0 for number, _ in literal), value)  # only "0s" (visibility) may be literal
            self.assertIn("var(--dur-", value, value)

    def test_a_live_region_replays_no_arrival_motion(self):
        """Marks fill in and the panel rises on the first load only, never on each refresh of a live region."""
        js = (settings.BASE_DIR / "static" / "js" / "emmaus.js").read_text()
        self.assertLess(js.index('region.setAttribute("data-live-settled", "")'), js.index("region.innerHTML = html"))
        self.assertIn('region.setAttribute("data-live-busy", "")', js)
        self.assertIn('region.removeAttribute("data-live-busy")', js)
        css = re.sub(r"\s+", " ", stylesheet_text())
        settled = re.search(r"((?:\[data-live-settled\][^{,]+, )+\[data-live-settled\][^{,]+)\{ animation: none; \}", css)
        self.assertIsNotNone(settled)
        stopped = {s.strip().replace("[data-live-settled] ", "") for s in settled.group(1).split(",")}
        for animated in (".sign", ".page-head", ".notice", ".tally__mark--done", ".ruler__seg--done", ".fold[open] > :not(summary)"):
            self.assertIn(animated, stopped)
        self.assertIn("[data-live-url][data-live-busy] { opacity: 0.72;", css)

    def test_only_the_main_question_is_named_for_the_page_transition(self):
        """A view-transition name must be unique on the page: follow-up questions are .question too, inside .reveal."""
        css = re.sub(r"\s+", " ", re.sub(r"/\*.*?\*/", "", stylesheet_text(), flags=re.S))
        self.assertIn("@view-transition { navigation: auto; }", css)
        self.assertEqual(re.findall(r"([^{}]+)\{ view-transition-name: pregunta; \}", css), [" .step > .question "])
        # The whole page crossfades in --dur-1 (it must never feel slow); only the question moves, in --dur-2
        self.assertIn("::view-transition-old(root), ::view-transition-new(root) { animation-duration: var(--dur-1);", css)
        self.assertIn("::view-transition-group(pregunta) { animation-duration: var(--dur-2);", css)
        form = (settings.BASE_DIR / "templates" / "events" / "interview" / "_step_form.html").read_text()
        step = form.split('class="step"', 1)[1]
        self.assertEqual(len(re.findall(r'^  \{% include "events/interview/_question.html"', step, re.M)), 1)
