"""Camp screens, visual fixes: the answers list, 44px targets, the goal label, the leader bar, the file control,
participants on a phone and the deck's chart and slide number.

Most of these are CSS. The stylesheet tests read rotulo.css with a small parser (rules one @media deep, in source
order) and, for the answers list, replay the cascade for one element, because the defect was a cascade defect: a
more specific rule elsewhere in the sheet silently replaced the answers layout.
"""

import ast
import operator
import re

from django.conf import settings
from django.test import SimpleTestCase

from events.charts import cumulative_chart

from .base import CampFixture

FUNCTIONAL = re.compile(r":(?:not|has|is|where)\(([^()]*)\)")


def stylesheet():
    return (settings.BASE_DIR / "static" / "css" / "rotulo.css").read_text()


def css_rules(css):
    """[(media prelude or None, selector list, declarations)] in source order; @font-face and the like are skipped."""
    text = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules, stack, start = [], [], 0
    for i, ch in enumerate(text):
        if ch == "{":
            stack.append((text[start:i].split(";")[-1].strip(), i + 1))
            start = i + 1
        elif ch == "}":
            prelude, body_start = stack.pop()
            if not prelude.startswith("@"):
                media = next((p for p, _ in reversed(stack) if p.startswith("@media")), None)
                rules.append((media, prelude, text[body_start:i]))
            start = i + 1
    return rules


def media_applies(media, width):
    """Screen width queries only; print, motion and forced-colours blocks never apply here."""
    if media is None:
        return True
    features = re.findall(r"\(([^()]*)\)", media)
    if not features or re.sub(r"\([^()]*\)|@media|and|\s", "", media):
        return False
    for feature in features:
        match = re.fullmatch(r"\s*(max|min)-width:\s*(\d+)px\s*", feature)
        if not match:
            return False
        limit = int(match.group(2))
        if (match.group(1) == "max" and width > limit) or (match.group(1) == "min" and width < limit):
            return False
    return True


def specificity(selector):
    a = b = c = 0
    for argument in FUNCTIONAL.findall(selector):  # :not(), :has(), :is() count as their most specific argument
        sa, sb, sc = max(specificity(s.strip()) for s in argument.split(","))
        a, b, c = a + sa, b + sb, c + sc
    rest = FUNCTIONAL.sub(" ", selector)
    c += len(re.findall(r"::[\w-]+", rest))
    rest = re.sub(r"::[\w-]+", " ", rest)
    a += len(re.findall(r"#[\w-]+", rest))
    b += len(re.findall(r"\.[\w-]+|\[[^\]]*\]|:[\w-]+", rest))
    rest = re.sub(r"#[\w-]+|\.[\w-]+|\[[^\]]*\]|:[\w-]+", " ", rest)
    c += len(re.findall(r"[a-zA-Z][\w-]*", rest))
    return a, b, c


def compound_matches_list(compound, list_classes, has_action):
    """Does one compound selector match the <dl> of a summary list with these classes?"""
    rest = compound
    for kind, argument in re.findall(r":(not|has)\(([^()]*)\)", compound):
        classes = set(re.findall(r"\.([\w-]+)", argument))
        if kind == "not" and classes <= list_classes:
            return False
        if kind == "has" and not (classes == {"summary-list__action"} and has_action):
            return False
    rest = re.sub(r":(?:not|has)\([^()]*\)", "", rest)
    if re.sub(r"\.[\w-]+|^dl", "", rest):
        return False  # anything this model does not know (ids, attributes, other pseudo-classes) is no match
    return set(re.findall(r"\.([\w-]+)", rest)) <= list_classes


def matches_summary_row(selector, list_classes, has_action):
    """A summary-list row, directly inside its <dl> (the rest of the page is not modelled)."""
    compounds = [c for c in re.split(r"\s*>\s*|\s+", selector.strip()) if c]
    if compounds[-1] != ".summary-list__row":
        return False
    return all(compound_matches_list(c, list_classes, has_action) for c in compounds[:-1])


def winning_columns(css, list_classes, width, has_action=True):
    """The grid-template-columns declaration that wins the cascade for a row of that list at that width."""
    winner = None
    for order, (media, selectors, body) in enumerate(css_rules(css)):
        declared = re.search(r"grid-template-columns\s*:\s*([^;]+)", body)
        if not declared or not media_applies(media, width):
            continue
        for selector in selectors.split(","):
            if matches_summary_row(selector, set(list_classes), has_action):
                key = (specificity(selector.strip()), order)
                if winner is None or key > winner[0]:
                    winner = (key, selector.strip(), declared.group(1).strip())
    return winner


def declarations(css, selector, width=1440):
    """Every declaration made for exactly this selector (as one item of a selector list) at that width."""
    found = {}
    for media, selectors, body in css_rules(css):
        if media_applies(media, width) and selector in [s.strip() for s in selectors.split(",")]:
            for prop, value in re.findall(r"([\w-]+)\s*:\s*([^;]+)", body):
                found[prop] = value.strip()
    return found


SPACING = {"--s-1": 4, "--s-2": 8, "--s-3": 12, "--s-5": 20}
ARITHMETIC = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.USub: operator.neg}


def css_length(value, lh, custom=None):
    """A length or calc() in px, with 1rem = 16px, 1lh = the given line height and the sheet's spacing steps."""
    custom = {**SPACING, **(custom or {})}
    text = re.sub(r"var\((--[\w-]+)\)", lambda m: f"({css_length(str(custom[m.group(1)]), lh, custom)})", value)
    text = text.replace("calc(", "(")
    text = re.sub(r"([\d.]+)rem", lambda m: str(float(m.group(1)) * 16), text)
    text = re.sub(r"([\d.]+)lh", lambda m: str(float(m.group(1)) * lh), text)
    text = re.sub(r"([\d.]+)px", r"\1", text)

    def evaluate(node):
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.BinOp):
            return ARITHMETIC[type(node.op)](evaluate(node.left), evaluate(node.right))
        if isinstance(node, ast.UnaryOp):
            return ARITHMETIC[type(node.op)](evaluate(node.operand))
        raise ValueError(value)

    return evaluate(ast.parse(text, mode="eval"))


def split_top_level(value):
    parts, depth, current = [], 0, ""
    for ch in value:
        depth += (ch == "(") - (ch == ")")
        if ch == " " and depth == 0:
            if current:
                parts.append(current)
            current = ""
        else:
            current += ch
    return parts + ([current] if current else [])


def block_edges(declared, prefix):
    """(top, bottom) of padding or margin from the longhand -block, or else the shorthand."""
    if f"{prefix}-block" in declared:
        values = split_top_level(declared[f"{prefix}-block"])
        return values[0], values[-1]
    if prefix in declared:
        values = split_top_level(declared[prefix])
        return values[0], values[2] if len(values) > 2 else values[0]
    return "0", "0"


def target_heights(css, selector, lines, lh):
    """(border box, margin box) of a target holding `lines` lines of text of height `lh`, from the sheet."""
    declared = declarations(css, selector, 390)
    custom = {k: v for k, v in declared.items() if k.startswith("--")}
    length = lambda v: css_length(v, lh, custom)  # noqa: E731
    pad = sum(length(v) for v in block_edges(declared, "padding"))
    margin = sum(length(v) for v in block_edges(declared, "margin"))
    box = max(length(declared.get("min-height", "0")), lines * lh + pad)
    return box, box + margin


class CascadeHelperTests(SimpleTestCase):
    def test_specificity_counts_functional_pseudo_classes_by_their_argument(self):
        self.assertEqual(specificity(".summary-list:has(.summary-list__action) .summary-list__row"), (0, 3, 0))
        self.assertEqual(specificity(".summary-list--answers .summary-list__row"), (0, 2, 0))
        self.assertEqual(specificity(".field input:not([type=\"checkbox\"]):not([type=\"radio\"])"), (0, 3, 1))
        self.assertEqual(specificity("a::before"), (0, 0, 2))

    def test_parser_keeps_media_and_order(self):
        rules = css_rules("a{x:1}\n@media (max-width: 600px){ b{y:2} }\n@media print{ c{z:3} }")
        self.assertEqual([(m, s) for m, s, _ in rules], [(None, "a"), ("@media (max-width: 600px)", "b"), ("@media print", "c")])
        self.assertTrue(media_applies("@media (max-width: 600px)", 390))
        self.assertFalse(media_applies("@media (max-width: 600px)", 1440))
        self.assertFalse(media_applies("@media print", 390))


class AnswersListTests(SimpleTestCase):
    """B1. The review list (interview/review.html, kiosk/review.html, interview/detail.html): question above,
    answer below, "Cambiar" at the right edge. `.summary-list:has(.summary-list__action) .summary-list__row`
    (0,3,0) out-ranked the answers rule (0,2,0) and the phone override, so the rows kept three columns
    (60/90/160px at 390px): the question one word per line and "Cambiar" in the middle of the row."""

    answers = ("summary-list", "summary-list--answers")

    def test_answers_rows_keep_their_two_columns_at_every_width(self):
        css = stylesheet()
        for width in (1440, 768, 390):
            _, selector, columns = winning_columns(css, self.answers, width)
            self.assertIn("summary-list--answers", selector, f"{width}px: {selector} wins")
            self.assertEqual(columns, "minmax(0, 1fr) auto", width)

    def test_the_has_action_rule_no_longer_reaches_the_answers_list(self):
        for media, selectors, body in css_rules(stylesheet()):
            for selector in selectors.split(","):
                if ":has(.summary-list__action)" in selector:
                    self.assertIn(":not(.summary-list--answers)", selector)

    def test_a_plain_list_with_an_action_still_folds_on_a_phone(self):
        """The same rule also out-ranked the phone override of lists like the camper's record (participant_detail)."""
        css = stylesheet()
        self.assertEqual(winning_columns(css, ("summary-list",), 1440)[2], "minmax(0, 2fr) minmax(0, 3fr) 10rem")
        self.assertEqual(winning_columns(css, ("summary-list",), 390)[2], "1fr auto")


class TouchTargetTests(SimpleTestCase):
    """B2. 44px to tap (design-system §6). Measured at 390px they were 27-35px: the back link, the "Atrás" of the
    questionnaire (same rule), the data-table summary, the sign's link, the forgotten-password link and the
    history table's Ver / PDF / Presentación."""

    # selector: (padding top + bottom, margin top + bottom) before the change, which the layout must keep
    TARGETS = {
        ".sign__link": (0, 0), ".auth-card__link": (0, 0), ".table__actions a": (0, 0), ".details summary": (0, 0),
        ".back-link": (8, 20), ".trail__back": (8, 12),
    }

    def test_targets_are_44px_and_move_nothing_whatever_the_number_of_lines(self):
        """The first fix used min-height with margins sized for one line: a link that wrapped to two lines took 14px
        out of the layout (the sign on the search page shrank from 60 to 53px). Padding and margins that cancel
        keep the margin box at the text's own height for any number of lines."""
        css = stylesheet()
        for selector, (old_pad, old_margin) in self.TARGETS.items():
            for lh in (21, 24, 27, 30):
                box, _ = target_heights(css, selector, 1, lh)
                self.assertGreaterEqual(round(box, 3), 44, (selector, lh))
                for lines in (1, 2, 3):
                    _, margin_box = target_heights(css, selector, lines, lh)
                    self.assertAlmostEqual(margin_box, lines * lh + old_pad + old_margin, msg=(selector, lh, lines))

    def test_the_three_inline_links_use_the_cancelling_pair(self):
        css = stylesheet()
        for selector in (".sign__link", ".auth-card__link", ".table__actions a"):
            declared = declarations(css, selector, 390)
            self.assertEqual(declared.get("padding-block"), "calc((2.75rem - 1lh) / 2)", selector)
            self.assertEqual(declared.get("margin-block"), "calc((1lh - 2.75rem) / 2)", selector)
            self.assertNotIn("min-height", declared, selector)

    def test_history_rows_are_as_tall_as_their_targets(self):
        """Rows were 41px apart while their links were 44px tall: the bottom of row 1's "Ver" opened row 2's cutoff."""
        css = stylesheet()
        cell = declarations(css, ".table tr:has(.table__actions) > td", 390)
        self.assertEqual(cell.get("padding-block"), "calc((2.75rem - 1lh) / 2)")
        for lh in (21, 24, 27):
            row_inner = lh + 2 * css_length(cell["padding-block"], lh)
            link_box, _ = target_heights(css, ".table__actions a", 1, lh)
            self.assertGreaterEqual(round(row_inner, 3), 44)
            self.assertLessEqual(round(link_box, 3), round(row_inner, 3))
        self.assertEqual(declarations(css, ".table__actions a", 390).get("min-width"), "2.75rem")  # "Ver" was 40px wide

    def test_history_focus_stays_inside_the_row(self):
        """The usual ring (3px outside a 2px white gap) would paint 5px over the row lines of a 44px target: here the
        ring is drawn inside the box, at least as far in as it is wide, and the outward white gap is dropped."""
        css = stylesheet()
        ring = declarations(css, ":focus-visible", 390)
        self.assertEqual(ring.get("outline"), "3px solid var(--ink)")
        focus = declarations(css, ".table__actions a:focus-visible", 390)
        self.assertLessEqual(css_length(focus["outline-offset"], 24), -3)
        self.assertEqual(focus.get("box-shadow"), "none")

    def test_standalone_inline_links_are_24px_tall(self):
        """C7. Figtree's inline box is 1.2em (Atkinson's was taller): "Ver todos", "Cambiar" and "Ver mi avance" fell
        from 24 to 22px at 18px. Vertical padding on an inline box widens the target and moves no line."""
        from reportlab.pdfbase.ttfonts import TTFontFile

        figtree = TTFontFile(str(settings.BASE_DIR / "events" / "fonts" / "Figtree-Regular.ttf"))
        self.assertEqual((figtree.ascent - figtree.descent) / 1000, 1.2)  # the 1.2em in the rule is this font's box
        css = stylesheet()
        for selector in (".more a", ".summary-list__action a", ".coord__count-note a"):
            for width in (390, 1440):
                declared = declarations(css, selector, width)
                self.assertEqual(declared.get("padding-block"), "max(0px, calc((1.5rem - 1.2em) / 2))", selector)
                self.assertNotIn("display", declared, selector)  # still inline: the padding changes no line box
                for size in (16, 18, 20, 24):
                    self.assertGreaterEqual(round(1.2 * size + 2 * max(0, (24 - 1.2 * size) / 2), 3), 24, (selector, size))

    def test_the_forgotten_password_link_has_the_class(self):
        login = (settings.BASE_DIR / "templates" / "registration" / "login.html").read_text()
        self.assertRegex(login, r'<a class="auth-card__link" href="\{% url \'password_reset\' %\}">')


class GoalLabelTests(SimpleTestCase):
    """B3. "Hacia las 04:34" was drawn 9 units above the goal line with no room above it: when the goal is the top
    gridline (a goal of 400 tops out at 400) the label's baseline sat at y=7 and the text was cut off."""

    points = [{"label": f"{h:02d}:00", "day": "", "count": 10, "cumulative": 10 * (h + 1)} for h in range(10)]

    def label(self, **kwargs):
        html = cumulative_chart(self.points, 400, chart_id="c", title="t", eta_minutes=240, projection_label="Hacia las 04:34", **kwargs)
        match = re.search(r'<text class="label-quiet" x="[\d.]+" y="([\d.-]+)" text-anchor="end" font-size="(\d+)">Hacia las 04:34</text>', html)
        self.assertIsNotNone(match, html)
        return float(match.group(1)), int(match.group(2))

    def test_label_fits_when_the_goal_is_the_top_gridline(self):
        for mode in ({}, {"compact": True}, {"deck": True}):
            y, font = self.label(**mode)
            self.assertGreaterEqual(y, font, mode)

    def test_deck_label_fits_at_the_size_the_stylesheet_sets(self):
        size = int(re.search(r"\.deck-chart text \{[^}]*font-size:\s*(\d+)px", stylesheet()).group(1))
        y, _ = self.label(deck=True)
        self.assertGreaterEqual(y, size)


class GoalAndValueLabelTests(SimpleTestCase):
    """A last value close to the goal with a short ETA put "Hacia las 04:34" on top of "390", in all three sizes."""

    points = [{"label": f"{h:02d}:00", "day": "", "count": 10, "cumulative": 300 + 10 * h} for h in range(10)]  # ends at 390

    def boxes(self, html, deck):
        sizes = (27, 32) if deck else (None, None)

        def box(match, size):
            x, y, anchor, attr_size, text = match.groups()
            size = size or int(attr_size)
            width = len(text) * size * 0.6
            x0 = float(x) - width if anchor == "end" else float(x)
            return x0, x0 + width, float(y) - size, float(y) + size * 0.25

        goal = re.search(r'<text class="label-quiet" x="([\d.]+)" y="([\d.]+)" text-anchor="(\w+)" font-size="(\d+)">(Hacia las 04:34)</text>', html)
        value = re.search(r'<text class="label-strong" x="([\d.]+)" y="([\d.]+)" text-anchor="(\w+)" font-size="(\d+)">(390)</text>', html)
        return box(goal, sizes[0]), box(value, sizes[1])

    def test_goal_label_never_covers_the_last_value(self):
        for mode in ({}, {"compact": True}, {"deck": True}):
            html = cumulative_chart(self.points, 400, chart_id="c", title="t", eta_minutes=30, projection_label="Hacia las 04:34", **mode)
            goal, value = self.boxes(html, mode.get("deck", False))
            apart_in_x = goal[1] <= value[0] or value[1] <= goal[0]
            apart_in_y = goal[3] <= value[2] or value[3] <= goal[2]
            self.assertTrue(apart_in_x or apart_in_y, (mode, goal, value))

    def test_goal_label_stays_by_the_projection_when_there_is_room(self):
        far = [{"label": f"{h:02d}:00", "day": "", "count": 10, "cumulative": 10 * (h + 1)} for h in range(10)]  # ends at 100
        html = cumulative_chart(far, 400, chart_id="c", title="t", eta_minutes=240, projection_label="Hacia las 04:34")
        self.assertIn('text-anchor="end" font-size="13">Hacia las 04:34</text>', html)


class DeckTests(SimpleTestCase):
    """B7. The chart on slide 3 filled about 80% of the width (1295 of 1613px at 1920x1080: a 1200x420 canvas in a
    3.6:1 area). "3 de 5" stays in both places by decision: the foot for the room, the bar for the presenter."""

    def test_deck_chart_canvas_is_as_wide_as_the_slide_area(self):
        html = cumulative_chart(GoalLabelTests.points, 400, chart_id="d", title="t", deck=True)
        width, height = map(int, re.search(r'viewBox="0 0 (\d+) (\d+)"', html).groups())
        self.assertGreaterEqual(width / height, 3.6)
        self.assertEqual(declarations(stylesheet(), ".deck-chart").get("width"), "100%")

    def test_no_rule_hides_the_slide_foot(self):
        """The foot's "3 de 5" stays for the room: the presenter's bar that repeats it fades after 3 s."""
        for media, selectors, body in css_rules(stylesheet()):
            if "slide__foot" in selectors and media_applies(media, 1920):
                self.assertNotRegex(body, r"display\s*:\s*none|visibility\s*:\s*hidden", selectors)


class DeckFootTests(CampFixture):
    def test_every_slide_foot_has_the_privacy_line_and_the_slide_number(self):
        self.login(self.pastor)
        html = self.client.get(self.url("presentation_live")).content.decode()
        feet = re.findall(r'<div class="slide__foot"><span>(.*?)</span><span>(.*?)</span></div>', html)
        self.assertEqual([number for _, number in feet], [f"{n} de 5" for n in range(1, 6)])
        for privacy, _ in feet:
            self.assertTrue(privacy.startswith("Solo totales"), privacy)
            self.assertIn("Sin nombres ni respuestas.", privacy)
        self.assertIn('data-deck-count aria-live="polite">1 de 5</span>', html)  # the presenter's bar keeps its copy


class LeaderBarAndScreensTests(CampFixture):
    def test_event_name_and_change_link_are_apart(self):
        """B4. "Campamento de Jóvenes 2026Campamentos": the name and the link to the event list ran together."""
        self.login(self.admin)
        html = self.client.get(self.url("admin_home")).content.decode()
        self.assertRegex(html, r'<p class="bar__event">Campamento A\s+<a href="[^"]*">Campamentos</a></p>')
        self.assertEqual(declarations(stylesheet(), ".bar--leader .bar__event", 1440).get("column-gap"), "var(--s-2)")

    def test_import_page_says_in_spanish_what_the_file_button_does(self):
        """B5. The native button reads "Choose File" on an English system: the page says it, the button looks like ours."""
        self.login(self.admin)
        response = self.client.get(self.url("participant_import"))
        self.assertContains(response, '<p class="lede">Elige el archivo.</p>', html=True)
        self.assertContains(response, 'class="admin-upload"')
        self.assertContains(response, "Cómo preparar el archivo")
        button = declarations(stylesheet(), '.admin-upload input[type="file"]::file-selector-button')
        self.assertEqual(button.get("border"), "var(--b-control) solid var(--line-strong)")  # as .btn--secondary
        self.assertEqual(button.get("min-height"), "var(--control)")

    def test_participants_render_as_rows_with_the_data_as_a_sentence(self):
        """B6. On a phone the table wrapped names onto three lines and scrolled sideways."""
        self.login(self.admin)
        html = self.client.get(self.url("participants")).content.decode()
        self.assertIn('class="table-wrap admin-people__table"', html)
        rows = re.search(r'<ul class="rows admin-people__rows">(.*?)</ul>', html, re.S).group(1)
        p = self.p1
        # Two lines of at most three data (UI-09); the interview status is the mark at the end of the row.
        self.assertIn(f'<p class="row__meta">Cuarto {p.room}, {p.age} años</p>', rows)
        self.assertIn(f'<p class="row__meta">#{p.code}, {p.church_group}, con {p.coordinator.display_name}</p>', rows)
        self.assertRegex(rows, r'<div class="row__end">\s*<span class="status status--pending"><span class="status__mark" aria-hidden="true"></span>Sin entrevista</span>')
        self.assertIn(f'href="{self.url("participant_detail", p.public_id)}">{p.full_name}</a>', rows)
        self.assertEqual(rows.count('<li class="row">'), html.count("<tr><td class=\"num\">"))

    def test_only_one_of_the_two_participant_lists_is_shown(self):
        css = stylesheet()
        self.assertEqual(declarations(css, ".admin-people__rows", 1440).get("display"), "none")
        self.assertEqual(declarations(css, ".admin-people__rows", 390).get("display"), "block")
        self.assertEqual(declarations(css, ".admin-people__table", 390).get("display"), "none")
        self.assertNotIn("display", declarations(css, ".admin-people__table", 1440))
