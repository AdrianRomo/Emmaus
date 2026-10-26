"""PDF export of a cutoff, built with ReportLab (pure Python, no system packages).

The PDF is generated from the *viewer-trimmed* payload, so it never shows more than
the person downloading it may see on screen. It reads like the cutoff page ("Rótulo",
docs/design-system.md): Recursive Casual for what a person would say (the title, the
section questions, the headline sentence), Atkinson Hyperlegible Next for everything
else, añil for data, chile only for risk, no yellow (nothing in a PDF is a next action).
"""

from __future__ import annotations

import io
from xml.sax.saxutils import escape
from datetime import datetime
from pathlib import Path

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..charts import _grid_steps, nice_max
from ..templatetags.emmaus import fecha_corta, fecha_larga
from .metrics import DEFINITIONS, delta_sentence, format_duration

# Static TrueType fonts: ReportLab cannot read the variable WOFF2 files in static/fonts. Atkinson-*.ttf are static
# Atkinson Hyperlegible Next subsets that include "≈" (used for rounded totals; the web subset lacks it);
# RecursiveCasual-Bold.ttf is the web Recursive instanced at CASL 1, wght 700 with fontTools (fixed text only: it has
# no "≈"). Licences: OFL-*.txt next to them.
FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
# The Rótulo tokens (static/css/rotulo.css :root).
CARBON = colors.HexColor("#1C1B19")
INK_2 = colors.HexColor("#4D4A44")
INK_3 = colors.HexColor("#68645C")
LINE = colors.HexColor("#DEDBD3")
LINE_STRONG = colors.HexColor("#8B877E")
DONE = colors.HexColor("#1F3C8F")  # añil
PROGRESS = colors.HexColor("#7D93CF")
TRACK = colors.HexColor("#E7E5DF")
CHILE = colors.HexColor("#B8321C")

_registered = False


def _register_fonts() -> None:
    global _registered
    if _registered:
        return
    pdfmetrics.registerFont(TTFont("EmmausSans", str(FONT_DIR / "Atkinson-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("EmmausSans-Bold", str(FONT_DIR / "Atkinson-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("EmmausVoice", str(FONT_DIR / "RecursiveCasual-Bold.ttf")))
    from reportlab.pdfbase.pdfmetrics import registerFontFamily

    registerFontFamily("EmmausSans", normal="EmmausSans", bold="EmmausSans-Bold", italic="EmmausSans", boldItalic="EmmausSans-Bold")
    _registered = True


def _styles():
    return {
        "wordmark": ParagraphStyle("wordmark", fontName="EmmausVoice", fontSize=13, leading=16, textColor=CARBON),
        "title": ParagraphStyle("title", fontName="EmmausVoice", fontSize=26, leading=30, textColor=CARBON, spaceBefore=6, spaceAfter=4),
        "lede": ParagraphStyle("lede", fontName="EmmausSans", fontSize=12, leading=16, textColor=INK_2),
        "meta": ParagraphStyle("meta", fontName="EmmausSans", fontSize=9, leading=12.5, textColor=INK_3, spaceBefore=2),
        "question": ParagraphStyle("question", fontName="EmmausVoice", fontSize=15, leading=19, textColor=CARBON, spaceBefore=4, spaceAfter=6),
        "headline": ParagraphStyle("headline", fontName="EmmausVoice", fontSize=21, leading=25, textColor=CARBON, spaceAfter=3),
        "body": ParagraphStyle("body", fontName="EmmausSans", fontSize=10.5, leading=15, textColor=CARBON, alignment=TA_LEFT),
        "small": ParagraphStyle("small", fontName="EmmausSans", fontSize=8.5, leading=12, textColor=INK_3),
        "figure": ParagraphStyle("figure", fontName="EmmausSans-Bold", fontSize=24, leading=27, textColor=CARBON),
        "figlabel": ParagraphStyle("figlabel", fontName="EmmausSans-Bold", fontSize=9, leading=12, textColor=INK_2),
        "fignote": ParagraphStyle("fignote", fontName="EmmausSans", fontSize=8.5, leading=11, textColor=INK_3),
    }


class Rule(Flowable):
    """The hairline that opens each section, like the top border of .report-section."""

    def __init__(self, width: float, color=LINE, weight: float = 0.6):
        super().__init__()
        self.width, self.height, self.color, self.weight = width, 6, color, weight

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.weight)
        self.canv.line(0, self.height - 1, self.width, self.height - 1)


class Meter(Flowable):
    """The ruler: completed / in progress / remaining, a 2pt paper gap between segments, 2pt corners."""

    def __init__(self, completed: int, in_progress: int, total: int, width: float, height: float = 12):
        super().__init__()
        self.completed, self.in_progress, self.total = completed, in_progress, total
        self.width, self.height = width, height

    def draw(self):
        c = self.canv
        c.setFillColor(TRACK)
        c.roundRect(0, 0, self.width, self.height, 2, stroke=0, fill=1)
        if not self.total:
            return
        done_w = self.width * self.completed / self.total
        prog_w = self.width * self.in_progress / self.total
        if done_w:
            c.setFillColor(DONE)
            c.roundRect(0, 0, done_w, self.height, 2, stroke=0, fill=1)
        if prog_w:
            gap = 2 if done_w else 0
            c.setFillColor(colors.white)
            c.rect(done_w, 0, gap, self.height, stroke=0, fill=1)
            c.setFillColor(PROGRESS)
            c.rect(done_w + gap, 0, max(prog_w - gap, 0), self.height, stroke=0, fill=1)


class Key(Flowable):
    """The legend under the ruler: a mark and its words, never colour alone."""

    def __init__(self, items: list[tuple[object, str]], width: float):
        super().__init__()
        self.items, self.width, self.height = items, width, 14

    def draw(self):
        c = self.canv
        x = 0
        for fill, text in self.items:
            if fill is None:
                c.setStrokeColor(LINE_STRONG)
                c.setLineWidth(0.8)
                c.setFillColor(colors.white)
                c.rect(x + 0.4, 2.4, 8.2, 8.2, stroke=1, fill=1)
            else:
                c.setFillColor(fill)
                c.rect(x, 2, 9, 9, stroke=0, fill=1)
            c.setFillColor(CARBON)
            c.setFont("EmmausSans", 9.5)
            c.drawString(x + 13, 3, text)
            x += 13 + pdfmetrics.stringWidth(text, "EmmausSans", 9.5) + 18


class Timeline(Flowable):
    """Cumulative completions toward the total; one axis, round ticks, a label on the last point only."""

    def __init__(self, points: list[dict], total: int, width: float, height: float = 58 * mm):
        super().__init__()
        self.points, self.total, self.width, self.height = points, total, width, height

    def draw(self):
        c = self.canv
        left, bottom, right, top = 26, 16, 52, 8
        plot_w, plot_h = self.width - left - right, self.height - bottom - top
        peak = nice_max(max([self.total] + [p["cumulative"] for p in self.points] + [1]))
        steps = _grid_steps(peak, (4, 5, 2, 3))
        c.setFont("EmmausSans", 7.5)
        for i in range(steps + 1):
            y = bottom + plot_h * i / steps
            c.setStrokeColor(LINE)
            c.setLineWidth(0.5)
            c.line(left, y, left + plot_w, y)
            c.setFillColor(INK_3)
            c.drawRightString(left - 4, y - 2.5, str(round(peak * i / steps)))
        if self.total:
            y_total = bottom + plot_h * self.total / peak
            c.setStrokeColor(LINE_STRONG)
            c.setLineWidth(0.8)
            c.setDash(3, 2)
            c.line(left, y_total, left + plot_w, y_total)
            c.setDash()
            c.setFillColor(INK_2)
            c.drawString(left + plot_w + 5, y_total - 2.5, f"Total {self.total}")
        n = len(self.points)
        if not n:
            return
        xs = [left + (plot_w * i / (n - 1) if n > 1 else plot_w / 2) for i in range(n)]
        ys = [bottom + plot_h * p["cumulative"] / peak for p in self.points]
        c.setStrokeColor(DONE)
        c.setLineWidth(2)
        c.setLineJoin(1)
        path = c.beginPath()
        path.moveTo(xs[0], ys[0])
        for x, y in zip(xs[1:], ys[1:]):
            path.lineTo(x, y)
        c.drawPath(path, stroke=1, fill=0)
        c.setStrokeColor(colors.white)
        c.setLineWidth(1.5)
        c.setFillColor(DONE)
        c.circle(xs[-1], ys[-1], 3.2, stroke=1, fill=1)
        c.setFillColor(CARBON)
        c.setFont("EmmausSans-Bold", 9)
        last = str(self.points[-1]["cumulative"])
        # Keep the last value clear of the "Total" label when the line ends near it.
        label_y = ys[-1] + 4
        if self.total and abs(label_y - (bottom + plot_h * self.total / peak)) < 9:
            label_y = ys[-1] - 12
        c.drawString(xs[-1] + 6, label_y, last)
        c.setFont("EmmausSans", 7.5)
        c.setFillColor(INK_3)
        every = max(1, -(-n // 7))
        for i, point in enumerate(self.points):
            if i % every == 0 or i == n - 1:
                c.drawCentredString(xs[i], 4, point["label"])


def _as_datetime(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def _day(dt) -> str:
    """'viernes 23 de octubre de 2026': a printed document keeps the year."""
    return f"{fecha_larga(dt).lower()} de {timezone.localtime(dt).year}"


def _pct(value) -> str:
    return "—" if value is None else f"{value} %"


def _section(question: str, width: float, styles) -> list:
    return [Spacer(1, 10), Rule(width), Paragraph(escape(question), styles["question"])]


def _table_style(numeric_from: int = 1) -> list:
    return [
        ("FONT", (0, 0), (-1, 0), "EmmausSans-Bold", 9),
        ("FONT", (0, 1), (-1, -1), "EmmausSans", 9.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK_2),
        ("TEXTCOLOR", (0, 1), (-1, -1), CARBON),
        ("ALIGN", (numeric_from, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 1, LINE_STRONG),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]


def build_snapshot_pdf(snapshot, payload: dict, *, delta: dict | None = None, previous_cutoff=None) -> bytes:
    """``previous_cutoff`` names the time in the change sentence, as the cutoff page does."""
    _register_fonts()
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=f"Corte de seguimiento de las {timezone.localtime(snapshot.cutoff_at):%H:%M}, {snapshot.event.name}",
        author="Emmaus",
    )
    width = doc.width - 12  # the frame pads 6 pt on each side; tables and rules line up with the text
    progress = payload["progress"]
    rate = payload["throughput"]
    hora = timezone.localtime(snapshot.cutoff_at).strftime("%H:%M")
    story = [
        Paragraph("Emmaus", styles["wordmark"]),
        Paragraph(f"Corte de las {hora}", styles["title"]),
        Paragraph(escape(f"{snapshot.event.name}, {payload['scope']['label']}, {_day(snapshot.cutoff_at)}."), styles["lede"]),
        Paragraph(
            escape(
                f"Corte guardado: no cambia. Datos al {fecha_corta(_as_datetime(payload['data_as_of']))}, generado de forma "
                f"{'manual' if snapshot.trigger == 'manual' else 'automática'}, métricas versión {payload['metrics_version']}."
            ),
            styles["meta"],
        ),
    ]

    # ¿Cómo íbamos? The answer as one sentence, then the ruler and its key.
    story += _section("¿Cómo íbamos?", width, styles)
    if progress["eligible"]:
        if not progress["pending"]:
            sub = "Ya se habían completado todas."
        elif rate.get("eta_minutes"):
            sub = f"Al ritmo de ese momento, {rate['per_hour']:g} por hora, faltaban unas {format_duration(rate['eta_minutes'])}."
        else:
            sub = "Todavía no había suficiente ritmo para estimar cuándo terminaríamos."
        story += [
            Paragraph(f"Llevábamos {progress['completed']} de {progress['eligible']} entrevistas.", styles["headline"]),
            Paragraph(escape(sub), styles["lede"]),
            Spacer(1, 8),
            Meter(progress["completed"], progress["in_progress"], progress["eligible"], width),
            Spacer(1, 5),
            Key(
                [
                    (DONE, f"{progress['completed']} completadas ({_pct(progress['percent'])})"),
                    (PROGRESS, f"{progress['in_progress']} en curso"),
                    (None, f"{progress['not_started']} sin iniciar"),
                ],
                width,
            ),
        ]
    else:
        story.append(Paragraph("Todavía no había participantes registrados.", styles["headline"]))
    if delta:
        story += [Spacer(1, 6), Paragraph(escape(delta_sentence(delta, previous_cutoff)), styles["body"])]

    # ¿Cuánto faltaba? Three figures and the cumulative chart.
    story += _section("¿Cuánto faltaba?", width, styles)
    fu = payload["follow_ups"]
    figures = [
        ("Pendientes", str(progress["pending"]), f"incluye {progress['in_progress']} en curso"),
        ("Ritmo", f"{rate['per_hour']:g}", "por hora, últimas 2 horas"),
        ("Seguimientos abiertos", str(fu.get("open_total", fu["pending"] + fu["in_progress"])), ""),
    ]
    fig_table = Table(
        [
            [Paragraph(label, styles["figlabel"]) for label, _, _ in figures],
            [Paragraph(value, styles["figure"]) for _, value, _ in figures],
            [Paragraph(note, styles["fignote"]) for _, _, note in figures],
        ],
        colWidths=[width / 3] * 3,
    )
    fig_table.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    story.append(fig_table)
    points = payload["timeline"]["points"]
    if points:
        story += [Spacer(1, 10), Paragraph("Entrevistas completadas, acumuladas por hora", styles["figlabel"]), Spacer(1, 2), Timeline(points, progress["eligible"], width)]
    else:
        story += [Spacer(1, 6), Paragraph("Todavía no había entrevistas completadas.", styles["body"])]

    rows_source = payload.get("teams") or []
    title = "Por equipo"
    if not rows_source and payload.get("coordinators"):
        rows_source = [{"label": c["name"], **c} for c in payload["coordinators"]]
        title = "Por coordinador"
    if rows_source:
        table_rows = [["", "Participantes", "Completadas", "Pendientes", "Avance"]]
        for row in rows_source:
            table_rows.append([row["label"], row["eligible"], row["completed"], row["pending"], _pct(row["percent"])])
        table = Table(table_rows, colWidths=[width * 0.4] + [width * 0.15] * 4, repeatRows=1)
        table.setStyle(TableStyle(_table_style()))
        story += _section(title, width, styles) + [table]

    fu_rows = [
        ["Seguimientos pendientes", fu["pending"]],
        ["En proceso", fu["in_progress"]],
        ["Atendidos", fu["done"]],
        ["Entrevistas por evaluar", fu["to_evaluate"]],
    ]
    fu_style = [
        ("FONT", (0, 0), (-1, -1), "EmmausSans", 10),
        ("TEXTCOLOR", (0, 0), (-1, -1), CARBON),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    if fu.get("safety_open") is not None:
        fu_rows.append(["Situaciones de riesgo abiertas", fu["safety_open"]])
        if fu["safety_open"]:
            fu_style += [("TEXTCOLOR", (0, -1), (-1, -1), CHILE), ("FONT", (0, -1), (-1, -1), "EmmausSans-Bold", 10)]
    fu_table = Table(fu_rows, colWidths=[width * 0.6, width * 0.2], hAlign="LEFT")
    fu_table.setStyle(TableStyle(fu_style))
    story.append(KeepTogether(_section("Seguimiento", width, styles) + [fu_table]))

    outcomes = payload.get("outcomes") or []
    if outcomes:
        k = payload.get("min_group_size", 5)
        story += _section("Resumen de respuestas", width, styles)
        story.append(
            Paragraph(
                f"Sin datos individuales. Cifras redondeadas a múltiplos de {k}; las cantidades menores a {k} se ocultan para que nadie pueda ser identificado.",
                styles["small"],
            )
        )
        story.append(Spacer(1, 6))
        for outcome in outcomes:
            title = escape(outcome["title"])
            sep = " " if title.rstrip()[-1:] in "?!." else ". "
            block = [Paragraph(f"<b>{title}</b>{sep}Respondieron {escape(outcome['respondents']['display'])}.", styles["body"])]
            if outcome["withheld"]:
                block.append(Paragraph("Aún no hay suficientes respuestas para mostrar este resumen.", styles["small"]))
            else:
                data = [[row["label"], row["display"]] for row in outcome["rows"]]
                t = Table(data, colWidths=[width * 0.6, width * 0.2], hAlign="LEFT")
                t.setStyle(TableStyle([
                    ("FONT", (0, 0), (-1, -1), "EmmausSans", 9.5),
                    ("TEXTCOLOR", (0, 0), (-1, -1), CARBON),
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ]))
                block.append(t)
            block.append(Spacer(1, 8))
            story.append(KeepTogether(block))

    recs = payload.get("recommendations") or []
    if recs:
        story += _section("Próximos pasos", width, styles)
        for index, rec in enumerate(recs, start=1):
            story.append(Paragraph(f"{index}. {escape(rec['text'])} <font color='#68645C'>({escape(rec['evidence'])})</font>", styles["body"]))
            story.append(Spacer(1, 3))

    story += _section("Cómo se calcula", width, styles)
    for name, text in DEFINITIONS:
        story.append(Paragraph(f"<b>{escape(name)}:</b> {escape(text)}", styles["small"]))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        left, right = doc.leftMargin + 6, doc.leftMargin + 6 + width
        canvas.line(left, 13 * mm, right, 13 * mm)
        canvas.setFont("EmmausSans", 8)
        canvas.setFillColor(INK_3)
        canvas.drawString(left, 9 * mm, "Documento confidencial de uso interno. No reenviar.")
        canvas.drawRightString(right, 9 * mm, f"Página {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
