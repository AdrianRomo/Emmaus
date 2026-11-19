"""One hand over the seven areas of wave 11 (docs/v2/wave11/CONSISTENCY.md): the rules that the areas now share,
read from the templates and the stylesheet themselves so the next screen cannot drift from them unnoticed."""

import re

from django.conf import settings
from django.test import SimpleTestCase

TEMPLATES = settings.BASE_DIR / "templates"
SHEET = (settings.BASE_DIR / "static" / "css" / "santuario.css").read_text()
SCRIPT = (settings.BASE_DIR / "static" / "js" / "emmaus.js").read_text()


def source(name: str) -> str:
    return (TEMPLATES / name).read_text()


def tags(html: str, css_class: str) -> list[str]:
    """Every opening tag that carries ``css_class``."""
    return [tag for tag in re.findall(r"<(?:a|button)\b[^>]*>", html) if css_class in tag]


class RowEndTests(SimpleTestCase):
    def test_a_state_word_leads_the_meta_line(self):
        # Rule 1: the row's end is for its one action or its chevron, never for its state
        for name in ("oracion/inbox.html", "people/person_list.html"):
            with self.subTest(name):
                self.assertNotIn("row__end", source(name))
                self.assertIn('<p class="row__meta row__meta--con-marca">{% if', source(name))
        swaps = source("serving/team.html")
        swaps = swaps[swaps.index('id="cambios-titulo"'):swaps.index("</section>", swaps.index('id="cambios-titulo"'))]
        self.assertNotIn("row__end", swaps)
        self.assertIn('<p class="row__meta row__meta--con-marca">{% if swap.status == "hecha" %}', swaps)

    def test_a_small_capsule_is_an_answer(self):
        # Rule 2: btn--small is an answer in a row (a pair, or the answer that changes it), the one filled next
        # step of the first row that waits, or the pager's pair. Everything else at a row's end is a text action.
        for name in sorted(path.name for path in (TEMPLATES / "serving").glob("*.html")):
            for tag in tags(source(f"serving/{name}"), "btn--small"):
                with self.subTest(name, tag=tag[:80]):
                    self.assertTrue('name="respuesta"' in tag or 'name="decision"' in tag or "btn--primary" in tag, tag)
        for name in sorted(path.name for path in (TEMPLATES / "espacios").glob("*.html")):
            for tag in tags(source(f"espacios/{name}"), "btn--small"):
                with self.subTest(name, tag=tag[:80]):
                    self.assertTrue(name in {"queue.html", "week.html"}, tag)
        self.assertEqual(len(tags(source("espacios/week.html"), "btn--small")), 2)  # «Anterior», «Siguiente»
        self.assertEqual(len(tags(source("espacios/queue.html"), "btn--small")), 2)  # «Aprobar», «Rechazar»

    def test_the_yes_comes_first_and_only_the_first_row_is_filled(self):
        # Rule 3: one filled action on a screen of several decisions, the affirmative answer of the first row
        queue = source("espacios/queue.html")
        self.assertLess(queue.index(">Aprobar<"), queue.index(">Rechazar<"))
        self.assertEqual(queue.count("btn--primary"), 1)
        self.assertIn('{% if forloop.first %}btn--primary{% else %}btn--secondary{% endif %} btn--small" type="submit">Aprobar', queue)
        team = source("serving/team.html")
        self.assertIn("{% if forloop.first %}btn--primary btn--small{% else %}btn--link{% endif %}", team)
        self.assertIn("{% if forloop.first and not uncovered %}btn--primary{% else %}btn--secondary{% endif %} btn--small", team)
        self.assertIn("{% if uncovered or to_approve %}btn--secondary{% else %}btn--primary{% endif %}", team)  # the head gives its fill up

    def test_a_list_ordered_by_hand_ends_in_its_two_moves(self):
        # Rules 2 and 7: «Subir» / «Bajar» and nothing else; removing lives where the item is changed
        plan = source("serving/plan.html")
        end = plan[plan.index('<form class="row__end"'):]
        end = end[:end.index("</form>")]
        self.assertEqual(re.findall(r'value="(\w+)"', end), ["subir", "bajar"])
        fold = plan[plan.index('<details class="fold">'):plan.index("</details>")]
        self.assertIn('<button class="btn btn--link btn--quieto" type="submit" name="accion" value="quitar" formnovalidate>', fold)
        questions = source("formularios/detail.html")
        end = questions[questions.index('<form class="row__end"'):]
        self.assertEqual(re.findall(r'value="(\w+)"', end[:end.index("</form>")]), ["arriba", "abajo"])


class ConsequenceTests(SimpleTestCase):
    def test_what_can_be_resumed_is_quiet_and_what_cannot_be_undone_is_chile(self):
        # Rule 4
        self.assertIn('<button class="btn btn--link btn--quieto" type="submit">Terminar la serie</button>', source("agenda/series_admin.html"))
        self.assertIn('<a class="btn btn--danger-outline" href="{% url \'formularios:close\' form_row.pk %}">Cerrar el formulario hoy</a>', source("formularios/detail.html"))
        self.assertIn('<button class="btn btn--danger-outline" type="submit">Deshacer: borrar', source("people/import_detail.html"))
        self.assertIn('<a class="btn btn--link btn--quieto" href="{% url \'espacios:cancel\' booking.pk %}">Cancelar', source("espacios/_booking.html"))
        self.assertIn('class="btn btn--link btn--quieto"', source("people/tags.html"))

    def test_a_way_that_stands_alone_is_a_text_action(self):
        # Rule 6
        self.assertIn('<p class="comparar__cambiar"><a class="btn btn--link" href=', source("people/merge_preview.html"))

    def test_dates_in_words_come_from_the_shared_filters(self):
        for name in ("resources/approval_queue.html", "resources/approval_review.html", "resources/_approval_note.html"):
            with self.subTest(name):
                self.assertNotIn(r'date:"j \d\e F"', source(name))


class SheetTests(SimpleTestCase):
    def test_a_state_and_its_facts_wrap_as_one_line_in_every_area(self):
        # Rule 10
        areas = ":is(.serving, .spaces, .oracion, .serie-admin, .personas-lista)"
        self.assertIn(f':root[data-ui="2"] {areas} .row__meta--con-marca {{ flex-wrap: nowrap; align-items: baseline; }}', SHEET)
        self.assertIn(f':root[data-ui="2"] {areas} .row__meta--con-marca > span:not(.status) {{ min-width: 0; }}', SHEET)
        self.assertIn(':root[data-ui="2"] .forms .row__meta--con-marca { flex-wrap: nowrap; align-items: baseline; }', SHEET)

    def test_the_comparison_sits_inside_the_column(self):
        # Rule 8
        self.assertIn(".page--app .table-wrap:has(> .comparar) { margin-inline: 0; }", SHEET)
        self.assertIn(".page--app .comparar :is(th, td):first-child { padding-left: 0; }", SHEET)
        self.assertIn(".page--app .comparar :is(th, td):last-child { padding-right: 0; }", SHEET)

    def test_a_long_rail_scrolls_shows_where_it_is_cut_and_keeps_this_page_in_view(self):
        # Rule 11: the list scrolls (never a hidden link); the cut edges get a hairline; the current place is in view
        nav = re.search(r"\.rail__nav \{[^}]*\}", SHEET).group(0)
        self.assertIn("overflow-y: auto", nav)
        for edge in ('"abajo"', '"arriba"', '"arriba abajo"'):
            self.assertIn(f".rail__nav[data-corte={edge}] {{ box-shadow: inset 0 ", SHEET)
        self.assertIn('nav.querySelector("[aria-current=\'page\']")', SCRIPT)
        self.assertIn('nav.setAttribute("data-corte", edges.join(" "))', SCRIPT)
