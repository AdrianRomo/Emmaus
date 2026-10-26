"""Server-rendered SVG charts.

Rules (see docs/design-system.md): one axis per chart, thin marks, hairline solid grid,
a single series colour, direct label on the last point only, every chart has a text
alternative (<title>/<desc>) plus a data table in the template.

No colour is written here: the marks carry classes and rotulo.css paints them with the data
tokens (petróleo #0B6270 for completed, #4C95A2 for the hour in progress, the goal and its
projection label in ink-3 #5A6872), so the screen, the deck in Cal and in Noche share one drawing.
"""

from __future__ import annotations

import math
from html import escape

from django.utils.safestring import mark_safe

# The projection label ("Hacia las 16:43") ends at the projection's end point, its baseline this far above the goal line.
PROJECTION_RISE = 9
# Width of a character for label collisions, in ems: Figtree's digits measure 0.56 (400) to 0.58 (800), its labels about
# 0.47; 0.6 keeps a margin.
CHAR_EM = 0.6


def _text_box(x: float, baseline: float, text: str, size: float, anchor: str) -> tuple[float, float, float, float]:
    """Approximate box (x0, x1, y0, y1) of one line of SVG text."""
    width = len(text) * size * CHAR_EM
    x0 = x - width if anchor == "end" else x - width / 2 if anchor == "middle" else x
    return x0, x0 + width, baseline - size, baseline + size * 0.25


def _boxes_overlap(a: tuple, b: tuple, gap: float = 4) -> bool:
    return a[0] < b[1] + gap and b[0] < a[1] + gap and a[2] < b[3] + gap and b[2] < a[3] + gap


def nice_max(value: int) -> int:
    if value <= 4:
        return 4
    magnitude = 10 ** int(math.floor(math.log10(value)))
    for step in (1, 2, 2.5, 4, 5, 10):  # 4: a goal of 400 tops out at 400, not 500
        candidate = step * magnitude
        if candidate >= value:
            return int(math.ceil(candidate))
    return int(10 * magnitude)


def _round_step(step: float) -> bool:
    """1, 2 or 5 times a power of ten."""
    if step < 1 or step != int(step):
        return False
    step = int(step)
    while step % 10 == 0:
        step //= 10
    return step in (1, 2, 5)


def _grid_steps(y_max: int, choices: tuple[int, ...]) -> int:
    """Gridline intervals with round ticks: 0, 100, 200 rather than 0, 125, 250 or 0, 12, 25, 37."""
    for steps in choices:
        if _round_step(y_max / steps):
            return steps
    for steps in choices:
        if y_max % steps == 0:
            return steps
    return 1


def _label_every(count: int, max_labels: int = 7) -> int:
    return max(1, math.ceil(count / max_labels))


def _label_indexes(count: int, max_labels: int = 7) -> set[int]:
    """Evenly spaced axis labels that always include the last point, without two labels colliding."""
    every = _label_every(count, max_labels)
    indexes = list(range(0, count, every))
    if count - 1 not in indexes:
        if (count - 1) - indexes[-1] < every * 0.75 and len(indexes) > 1:
            indexes[-1] = count - 1
        else:
            indexes.append(count - 1)
    return set(indexes)


def _rounded_top_rect(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """Bar path: rounded data end (top), square at the baseline."""
    if h <= 0:
        return ""
    r = min(r, w / 2, h)
    return (
        f"M{x:.1f},{y + h:.1f} L{x:.1f},{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
        f"L{x + w - r:.1f},{y:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} L{x + w:.1f},{y + h:.1f} Z"
    )


def cumulative_chart(
    points: list[dict],
    total: int,
    *,
    chart_id: str,
    title: str,
    deck: bool = False,
    compact: bool = False,
    eta_minutes: int | None = None,
    bucket_minutes: int = 60,
    projection_label: str = "",
) -> str:
    """Line: cumulative completed interviews over time, with the participant total as reference.

    With ``eta_minutes`` the x axis extends to the estimated finish and a dashed projection runs
    from the last point to the total (only drawn when the line has at least two points).
    """
    if not points:
        return ""
    # ``top`` leaves room above the goal line for the projection label, drawn PROJECTION_RISE above it:
    # with less, "Hacia las 04:34" was cut off at the top edge whenever the goal was the top gridline.
    # The deck canvas is as wide as the slide's chart area (about 3.6:1 at any size), so the chart fills
    # the slide's width; its text is set in rotulo.css at 27 (label) and 32 (value) viewBox units.
    if deck:
        width, height, font, (left, right, top, bottom) = 1600, 420, 22, (70, 150, 27 + PROJECTION_RISE, 50)
    elif compact:  # drawn near 1:1 on a phone, so the text stays readable
        width, height, font, (left, right, top, bottom) = 360, 240, 12, (32, 64, 12 + PROJECTION_RISE, 30)
    else:
        width, height, font, (left, right, top, bottom) = 640, 260, 13, (40, 72, 13 + PROJECTION_RISE, 34)
    plot_w, plot_h = width - left - right, height - top - bottom
    y_max = nice_max(max(total, points[-1]["cumulative"], 1))
    n = len(points)
    project = bool(eta_minutes and total and n > 1 and points[-1]["cumulative"] < total)
    extra = eta_minutes / bucket_minutes if project else 0
    slots = (n - 1) + extra
    x_for = (lambda i: left + plot_w / 2) if n == 1 else (lambda i: left + plot_w * i / slots)
    y_for = lambda v: top + plot_h - plot_h * v / y_max  # noqa: E731

    parts = [
        f'<svg class="{"deck-chart" if deck else "chart__svg"}" viewBox="0 0 {width} {height}" role="img" '
        f'aria-labelledby="{chart_id}-t {chart_id}-d" preserveAspectRatio="xMidYMid meet">',
        f'<title id="{chart_id}-t">{escape(title)}</title>',
        f'<desc id="{chart_id}-d">De {escape(points[0]["label"])} a {escape(points[-1]["label"])} se completaron '
        f'{points[-1]["cumulative"]} entrevistas de {total} camperos.</desc>',
    ]
    steps = _grid_steps(y_max, (4, 5))
    for i in range(steps + 1):
        value = y_max * i / steps
        y = y_for(value)
        parts.append(f'<line class="grid" x1="{left}" x2="{left + plot_w}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text x="{left - 10}" y="{y + font / 3:.1f}" text-anchor="end" font-size="{font}">{int(value)}</text>')
    if total:
        y_total = y_for(total)
        parts.append(f'<line class="axis" x1="{left}" x2="{left + plot_w}" y1="{y_total:.1f}" y2="{y_total:.1f}" stroke-width="{2 if deck else 1}"/>')
        parts.append(
            f'<text x="{left + plot_w + 8}" y="{y_total + font / 3:.1f}" font-size="{font}">Total {total}</text>'
        )
    coords = [(x_for(i), y_for(p["cumulative"])) for i, p in enumerate(points)]
    if n > 1:
        line = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(coords))
        area = line + f" L{coords[-1][0]:.1f},{top + plot_h:.1f} L{coords[0][0]:.1f},{top + plot_h:.1f} Z"
        if not deck:
            parts.append(f'<path class="area" d="{area}"/>')
        parts.append(f'<path class="line" d="{line}"/>')
    lx, ly = coords[-1]
    label_x, anchor_last = (lx - 10, "end") if project else (lx + 12, "start")
    label_y = ly - 10
    # Near the goal the value would sit on top of the "Total" label (same right edge): put it under the point.
    if total and not project and abs(label_y - (y_for(total) + font / 3)) < font * 1.4:
        label_x, anchor_last, label_y = lx - 10, "end", ly + font + 8
    # Sizes as drawn: the deck's stylesheet sets its text at 27 and the value at 32 viewBox units (rotulo.css).
    label_size, value_size = (27, 32) if deck else (font, font + 2)
    if project:
        px, py = x_for(slots), y_for(total)
        parts.append(f'<path class="projection" d="M{lx:.1f},{ly:.1f} L{px:.1f},{py:.1f}"/>')
        parts.append(f'<circle class="projection-end" cx="{px:.1f}" cy="{py:.1f}" r="{6 if deck else 4}"/>')
        if projection_label:
            # Beside the projection's end, unless the last value sits there too (close to the goal, a short ETA):
            # then at the goal line's other end. Nothing else is drawn above that line while there is a
            # projection, since every point is below the goal.
            value_box = _text_box(label_x, label_y, str(points[-1]["cumulative"]), value_size, anchor_last)

            def clear(x, anchor):
                return not _boxes_overlap(_text_box(x, py - PROJECTION_RISE, projection_label, label_size, anchor), value_box)

            places = [(px - 4, "end"), (left + 4, "start")]
            goal_x, goal_anchor = next((place for place in places if clear(*place)), places[0])
            parts.append(
                f'<text class="label-quiet" x="{goal_x:.1f}" y="{py - PROJECTION_RISE:.1f}" text-anchor="{goal_anchor}" font-size="{font}">{escape(projection_label)}</text>'
            )
    parts.append(f'<circle class="dot" cx="{lx:.1f}" cy="{ly:.1f}" r="{7 if deck else 5}"/>')
    parts.append(
        f'<text class="label-strong" x="{label_x:.1f}" y="{label_y:.1f}" text-anchor="{anchor_last}" font-size="{font + 2}">{points[-1]["cumulative"]}</text>'
    )
    # Time labels. The first one may carry the day ("25/10 18:00"), about twice as wide as the spacing
    # _label_indexes assumes, and a far finish squeezes the points together. Every box is measured from its own
    # string at the size drawn (the deck's stylesheet sets its text at 27 units). A label that would touch the one
    # before it gives way; the last one never does: the labels before it give way instead, the first by dropping its
    # day before it is dropped.
    axis_size = label_size
    placed: list[tuple[int, str, str, tuple]] = []

    def placed_label(i: int, text: str, anchor: str) -> tuple[int, str, str, tuple]:
        return i, text, anchor, _text_box(x_for(i), 0, text, axis_size, anchor)

    for i in sorted(_label_indexes(n, 4 if compact else 7)):
        point = points[i]
        text = point["label"] if not point.get("day") or i else f'{point["day"]} {point["label"]}'
        anchor = "start" if (i == 0 and n > 1) else "end" if i == n - 1 and n > 1 and not project else "middle"
        label = placed_label(i, text, anchor)
        while placed and _boxes_overlap(placed[-1][3], label[3], gap=axis_size * 0.5):
            if i != n - 1:
                break
            j, previous_text, previous_anchor, _ = placed[-1]
            if j == 0 and previous_text != points[0]["label"]:
                placed[-1] = placed_label(0, points[0]["label"], previous_anchor)  # "18:00" without its day
            else:
                placed.pop()
        else:
            placed.append(label)
    for i, text, anchor, _ in placed:
        parts.append(f'<text x="{x_for(i):.1f}" y="{height - (14 if deck else 10)}" text-anchor="{anchor}" font-size="{font}">{escape(text)}</text>')
    parts.append(f'<line class="axis" x1="{left}" x2="{left + plot_w}" y1="{top + plot_h}" y2="{top + plot_h}"/>')
    parts.append("</svg>")
    return mark_safe("".join(parts))


def columns_chart(
    points: list[dict],
    *,
    chart_id: str,
    title: str,
    unit_label: str = "por hora",
    deck: bool = False,
    compact: bool = False,
    partial_last: bool = False,
) -> str:
    """Columns: interviews completed per time bucket. ``partial_last`` marks the bucket that contains now."""
    if not points:
        return ""
    if deck:
        width, height, font, (left, right, top, bottom) = 1200, 380, 22, (60, 20, 30, 50)
    elif compact:
        width, height, font, (left, right, top, bottom) = 360, 200, 12, (28, 6, 20, 30)
    else:
        width, height, font, (left, right, top, bottom) = 640, 220, 13, (36, 8, 22, 34)
    plot_w, plot_h = width - left - right, height - top - bottom
    peak = max(p["count"] for p in points)
    y_max = nice_max(max(peak, 1))
    n = len(points)
    band = plot_w / n
    bar_w = min(40 if deck else 24, band * 0.7)
    parts = [
        f'<svg class="{"deck-chart" if deck else "chart__svg"}" viewBox="0 0 {width} {height}" role="img" '
        f'aria-labelledby="{chart_id}-t {chart_id}-d" preserveAspectRatio="xMidYMid meet">',
        f'<title id="{chart_id}-t">{escape(title)}</title>',
        f'<desc id="{chart_id}-d">Máximo: {peak} entrevistas {escape(unit_label)}.</desc>',
    ]
    steps = _grid_steps(y_max, (2, 5, 4))
    for i in range(steps + 1):
        value = y_max * i / steps
        y = top + plot_h - plot_h * value / y_max
        parts.append(f'<line class="grid" x1="{left}" x2="{left + plot_w}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text x="{left - 8}" y="{y + font / 3:.1f}" text-anchor="end" font-size="{font}">{int(value)}</text>')
    peak_index = max(range(n), key=lambda i: points[i]["count"])
    labelled = _label_indexes(n, 4 if compact else 7)
    for i, point in enumerate(points):
        h = plot_h * point["count"] / y_max
        x = left + band * i + (band - bar_w) / 2
        y = top + plot_h - h
        if point["count"]:
            partial = partial_last and i == n - 1
            parts.append(
                f'<path class="col{" col--partial" if partial else ""}" d="{_rounded_top_rect(x, y, bar_w, h)}">'
                f'<title>{escape(point["label"])}: {point["count"]} entrevistas{" (hora en curso)" if partial else ""}</title></path>'
            )
        if i == peak_index and point["count"]:
            parts.append(f'<text class="label-strong" x="{x + bar_w / 2:.1f}" y="{y - 6:.1f}" text-anchor="middle" font-size="{font}">{point["count"]}</text>')
        if i in labelled:
            parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{height - (14 if deck else 10)}" text-anchor="middle" font-size="{font}">{escape(point["label"])}</text>')
    parts.append(f'<line class="axis" x1="{left}" x2="{left + plot_w}" y1="{top + plot_h}" y2="{top + plot_h}"/>')
    parts.append("</svg>")
    return mark_safe("".join(parts))
