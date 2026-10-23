"""Server-rendered SVG charts.

Rules (see docs/design-system.md): one axis per chart, thin marks, hairline solid grid,
a single series colour, direct label on the last point only, every chart has a text
alternative (<title>/<desc>) plus a data table in the template.
"""

from __future__ import annotations

import math
from html import escape

from django.utils.safestring import mark_safe


def nice_max(value: int) -> int:
    if value <= 4:
        return 4
    magnitude = 10 ** int(math.floor(math.log10(value)))
    for step in (1, 2, 2.5, 5, 10):
        candidate = step * magnitude
        if candidate >= value:
            return int(math.ceil(candidate))
    return int(10 * magnitude)


def _label_every(count: int, max_labels: int = 7) -> int:
    return max(1, math.ceil(count / max_labels))


def _rounded_top_rect(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """Bar path: rounded data end (top), square at the baseline."""
    if h <= 0:
        return ""
    r = min(r, w / 2, h)
    return (
        f"M{x:.1f},{y + h:.1f} L{x:.1f},{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
        f"L{x + w - r:.1f},{y:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} L{x + w:.1f},{y + h:.1f} Z"
    )


def cumulative_chart(points: list[dict], total: int, *, chart_id: str, title: str, deck: bool = False) -> str:
    """Line: cumulative completed interviews over time, with the participant total as reference."""
    if not points:
        return ""
    width, height = (1200, 420) if deck else (640, 260)
    font = 22 if deck else 13
    left, right, top, bottom = (70, 120, 24, 50) if deck else (40, 72, 16, 34)
    plot_w, plot_h = width - left - right, height - top - bottom
    y_max = nice_max(max(total, points[-1]["cumulative"], 1))
    n = len(points)
    x_for = (lambda i: left + plot_w / 2) if n == 1 else (lambda i: left + plot_w * i / (n - 1))
    y_for = lambda v: top + plot_h - plot_h * v / y_max  # noqa: E731

    parts = [
        f'<svg class="{"deck-chart" if deck else "chart__svg"}" viewBox="0 0 {width} {height}" role="img" '
        f'aria-labelledby="{chart_id}-t {chart_id}-d" preserveAspectRatio="xMidYMid meet">',
        f'<title id="{chart_id}-t">{escape(title)}</title>',
        f'<desc id="{chart_id}-d">De {escape(points[0]["label"])} a {escape(points[-1]["label"])} se completaron '
        f'{points[-1]["cumulative"]} entrevistas de {total} participantes.</desc>',
    ]
    for i in range(5):
        value = y_max * i / 4
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
    parts.append(f'<circle class="dot" cx="{lx:.1f}" cy="{ly:.1f}" r="{7 if deck else 5}"/>')
    parts.append(
        f'<text class="label-strong" x="{lx + 12:.1f}" y="{ly - 10:.1f}" font-size="{font + 2}">{points[-1]["cumulative"]}</text>'
    )
    every = _label_every(n)
    for i, point in enumerate(points):
        if i % every and i != n - 1:
            continue
        label = escape(point["label"] if not point.get("day") or i else f'{point["day"]} {point["label"]}')
        anchor = "start" if (i == 0 and n > 1) else "end" if i == n - 1 and n > 1 else "middle"
        parts.append(f'<text x="{x_for(i):.1f}" y="{height - (14 if deck else 10)}" text-anchor="{anchor}" font-size="{font}">{label}</text>')
    parts.append(f'<line class="axis" x1="{left}" x2="{left + plot_w}" y1="{top + plot_h}" y2="{top + plot_h}"/>')
    parts.append("</svg>")
    return mark_safe("".join(parts))


def columns_chart(points: list[dict], *, chart_id: str, title: str, unit_label: str = "por hora", deck: bool = False) -> str:
    """Columns: interviews completed per time bucket."""
    if not points:
        return ""
    width, height = (1200, 380) if deck else (640, 220)
    font = 22 if deck else 13
    left, right, top, bottom = (60, 20, 30, 50) if deck else (36, 8, 22, 34)
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
    for i in range(3):
        value = y_max * i / 2
        y = top + plot_h - plot_h * value / y_max
        parts.append(f'<line class="grid" x1="{left}" x2="{left + plot_w}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text x="{left - 8}" y="{y + font / 3:.1f}" text-anchor="end" font-size="{font}">{int(value)}</text>')
    peak_index = max(range(n), key=lambda i: points[i]["count"])
    every = _label_every(n)
    for i, point in enumerate(points):
        h = plot_h * point["count"] / y_max
        x = left + band * i + (band - bar_w) / 2
        y = top + plot_h - h
        if point["count"]:
            parts.append(
                f'<path class="col" d="{_rounded_top_rect(x, y, bar_w, h)}"><title>{escape(point["label"])}: '
                f'{point["count"]} entrevistas</title></path>'
            )
        if i == peak_index and point["count"]:
            parts.append(f'<text class="label-strong" x="{x + bar_w / 2:.1f}" y="{y - 6:.1f}" text-anchor="middle" font-size="{font}">{point["count"]}</text>')
        if i % every == 0 or i == n - 1:
            parts.append(f'<text x="{x + bar_w / 2:.1f}" y="{height - (14 if deck else 10)}" text-anchor="middle" font-size="{font}">{escape(point["label"])}</text>')
    parts.append(f'<line class="axis" x1="{left}" x2="{left + plot_w}" y1="{top + plot_h}" y2="{top + plot_h}"/>')
    parts.append("</svg>")
    return mark_safe("".join(parts))
