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

    def test_partial_last_column_is_marked(self):
        html = columns_chart(self.points, chart_id="c", title="t", partial_last=True)
        self.assertEqual(html.count("col--partial"), 1)
        self.assertIn("(hora en curso)", html)


class RedesignedScreenTests(CampFixture):
    def test_every_screen_of_every_role_is_on_rotulo(self):
        """One design system: no role ever crosses into the old stylesheet, which no longer exists."""
        self.assertFalse((settings.BASE_DIR / "static" / "css" / "emmaus.css").exists())
        pages = {
            self.coord1: ["coord_home", "my_participants", "coord_pending", "coord_progress", "participant_create",
                          ("participant_detail", self.p1.public_id), "follow_ups", "backup_list", "interview_search",
                          ("guardian_authorization", self.p_minor_no_auth.public_id), ("guardian_authorization_withdraw", self.p1.public_id)],
            self.tutor1: ["tutor_home", "coordinators", "interviews", "pending", "reports", "follow_ups"],
            self.pastor: ["pastor_home", "progress", "follow_ups", "reports", "team", "presentation_live"],
            self.admin: ["admin_home", "participants", "participant_import", "team", "assignments", "questionnaires",
                         "settings", "audit", "sessions", "reports", "member_add", "join_code_sign", "sedes", "sede_add"],
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
        self.assertContains(response, "Cambiar de evento")
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
