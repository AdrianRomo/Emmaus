"""PDF export of a cutoff, built with ReportLab (pure Python, no system packages).

The PDF is generated from the *viewer-trimmed* payload, so it never shows more than
the person downloading it may see on screen. It reads like the cutoff page ("Atrio",
docs/design-system.md): Figtree ExtraBold for what a person would say (the title, the
section questions, the headline sentence), Regular for reading, Bold for emphasis; petróleo
for data, chile only for risk. The header is the church's PNG logo when it has one (core.logos),
otherwise the wordmark.
"""

from __future__ import annotations

import functools
import io
import logging
import os
from xml.sax.saxutils import escape
from datetime import datetime
from pathlib import Path

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch, mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from core.logos import logo_meta

from ..charts import _grid_steps, nice_max
from ..templatetags.emmaus import fecha_corta, fecha_larga
from .metrics import DEFINITIONS, delta_sentence, format_duration

# Static TrueType fonts: ReportLab cannot read the variable WOFF2 in static/fonts. Figtree-{Regular,Bold,ExtraBold}.ttf
# are the 400, 700 and 800 instances of Figtree 2.002 (OFL, OFL-Figtree.txt next to them), full character set. Figtree
# has no "≈": rounded counts are written in words ("unas 85"), see _about.
FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
# The Atrio tokens (static/css/rotulo.css :root).
INK = colors.HexColor("#16222B")
INK_2 = colors.HexColor("#44525D")
INK_3 = colors.HexColor("#5A6872")
HUNDIDO = colors.HexColor("#F3F5F6")
LINE = colors.HexColor("#DCE1E4")
LINE_STRONG = colors.HexColor("#7B8A94")
DONE = colors.HexColor("#0B6270")  # petróleo
PROGRESS = colors.HexColor("#4C95A2")
TRACK = colors.white  # "sin iniciar": white with a LINE_STRONG outline, as on screen
CHILE = colors.HexColor("#B8321C")

# The church's logo in the header: within 2.2 x 0.72 in (the proportions of the sign-in slot), never stretched.
LOGO_MAX_WIDTH = 2.2 * inch
LOGO_MAX_HEIGHT = 0.72 * inch
# Embedded at most this size: 300 dpi at 2.2 x 0.73 in. A 2000 px upload is not decoded into every PDF.
LOGO_EMBED_MAX_PX = (660, 220)

logger = logging.getLogger(__name__)
_registered = False


def _register_fonts() -> None:
    global _registered
    if _registered:
        return
    pdfmetrics.registerFont(TTFont("Figtree", str(FONT_DIR / "Figtree-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("Figtree-Bold", str(FONT_DIR / "Figtree-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("Figtree-ExtraBold", str(FONT_DIR / "Figtree-ExtraBold.ttf")))
    # No italic file: italic maps to upright (ReportLab draws no synthetic slant)
    pdfmetrics.registerFontFamily("Figtree", normal="Figtree", bold="Figtree-Bold", italic="Figtree", boldItalic="Figtree-Bold")
    _registered = True


def _styles():
    return {
        "wordmark": ParagraphStyle("wordmark", fontName="Figtree-Bold", fontSize=13, leading=16, textColor=INK),
        "title": ParagraphStyle("title", fontName="Figtree-ExtraBold", fontSize=26, leading=30, textColor=INK, spaceBefore=6, spaceAfter=4),
        "lede": ParagraphStyle("lede", fontName="Figtree", fontSize=12, leading=16, textColor=INK_2),
        "meta": ParagraphStyle("meta", fontName="Figtree", fontSize=9, leading=12.5, textColor=INK_3, spaceBefore=2),
        "question": ParagraphStyle("question", fontName="Figtree-ExtraBold", fontSize=15, leading=19, textColor=INK, spaceBefore=4, spaceAfter=6),
        "headline": ParagraphStyle("headline", fontName="Figtree-ExtraBold", fontSize=21, leading=25, textColor=INK, spaceAfter=3),
        "body": ParagraphStyle("body", fontName="Figtree", fontSize=10.5, leading=15, textColor=INK, alignment=TA_LEFT),
        "small": ParagraphStyle("small", fontName="Figtree", fontSize=8.5, leading=12, textColor=INK_3),
        "figure": ParagraphStyle("figure", fontName="Figtree-ExtraBold", fontSize=24, leading=27, textColor=INK),
        "figlabel": ParagraphStyle("figlabel", fontName="Figtree-Bold", fontSize=9, leading=12, textColor=INK_2),
        "fignote": ParagraphStyle("fignote", fontName="Figtree", fontSize=8.5, leading=11, textColor=INK_3),
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
    """The ruler: completed / in progress / remaining (white, outlined), a 2pt paper gap between segments, 3pt corners."""

    def __init__(self, completed: int, in_progress: int, total: int, width: float, height: float = 12):
        super().__init__()
        self.completed, self.in_progress, self.total = completed, in_progress, total
        self.width, self.height = width, height

    def draw(self):
        c = self.canv
        c.setFillColor(TRACK)
        c.setStrokeColor(LINE_STRONG)
        c.setLineWidth(0.8)
        c.roundRect(0.4, 0.4, self.width - 0.8, self.height - 0.8, 3, stroke=1, fill=1)
        if not self.total:
            return
        done_w = self.width * self.completed / self.total
        prog_w = self.width * self.in_progress / self.total
        if done_w:
            c.setFillColor(DONE)
            c.roundRect(0, 0, done_w, self.height, 3, stroke=0, fill=1)
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
            c.setFillColor(INK)
            c.setFont("Figtree", 9.5)
            c.drawString(x + 13, 3, text)
            x += 13 + pdfmetrics.stringWidth(text, "Figtree", 9.5) + 18


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
        c.setFont("Figtree", 7.5)
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
            c.setFillColor(INK_3)  # the goal's label, as on screen
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
        c.setFillColor(INK)
        c.setFont("Figtree-Bold", 9)
        last = str(self.points[-1]["cumulative"])
        # Keep the last value clear of the "Total" label when the line ends near it.
        label_y = ys[-1] + 4
        if self.total and abs(label_y - (bottom + plot_h * self.total / peak)) < 9:
            label_y = ys[-1] - 12
        c.drawString(xs[-1] + 6, label_y, last)
        c.setFont("Figtree", 7.5)
        c.setFillColor(INK_3)
        for i in _spaced_labels(xs, [p["label"] for p in self.points], "Figtree", 7.5):
            c.drawCentredString(xs[i], 4, self.points[i]["label"])


def _spaced_labels(xs: list[float], texts: list[str], font: str, size: float, gap: float = 4) -> list[int]:
    """The time labels drawn under the PDF chart: about seven, always the first and the last, never two closer than
    ``gap`` points, measured with the font's own widths. A label that would touch the one before it is left out; when
    that is the last one, the label before it goes instead (at 22 points they were 20.0 pt apart and 20.5 pt wide)."""
    n = len(xs)
    every = max(1, -(-n // 7))

    def box(i: int) -> tuple[float, float]:
        half = pdfmetrics.stringWidth(texts[i], font, size) / 2
        return xs[i] - half, xs[i] + half

    kept: list[int] = []
    for i in (i for i in range(n) if i % every == 0 or i == n - 1):
        while kept and box(kept[-1])[1] + gap > box(i)[0]:
            if i != n - 1:
                break
            kept.pop()
        else:
            kept.append(i)
    return kept


@functools.lru_cache(maxsize=8)
def _embeddable_logo(path: str, size: int, mtime_ns: int) -> bytes:
    """The stored PNG, at most LOGO_EMBED_MAX_PX, as a small RGBA PNG; decoded once per process and file version.

    One-channel images become L (LA with a transparent palette entry) before resizing, never RGBA: a 2000 x 2000
    1-bit or palette logo then costs one 4 MB grey copy, not 16 MB of RGBA. 16-bit grey (I;16) is scaled into
    L (convert("L") would clip it at 255, turning the logo white). Only the resized image becomes RGBA.
    """
    from PIL import Image as PILImage

    image = PILImage.open(path)
    image.load()
    if image.mode == "P":
        image = image.convert("LA" if "transparency" in image.info else "L")
    elif image.mode == "1":
        image = image.convert("L")
    elif image.mode.startswith("I"):
        image = image.point(lambda value: value / 256).convert("L")
    elif image.mode not in ("L", "LA", "RGB", "RGBA"):
        image = image.convert("RGBA")
    max_width, max_height = LOGO_EMBED_MAX_PX
    scale = min(1, max_width / image.width, max_height / image.height)
    if scale < 1:
        target = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(target, PILImage.LANCZOS, reducing_gap=2.0)
    out = io.BytesIO()
    image.convert("RGBA").save(out, "PNG")
    return out.getvalue()


def _brand(church, styles):
    """The church's PNG logo, or the painted "Emmaus" (no logo, or a stored file that cannot be read)."""
    wordmark = Paragraph("Emmaus", styles["wordmark"])
    meta = logo_meta(church.logo) if church is not None else None
    if meta is None:
        return wordmark
    try:
        scale = min(LOGO_MAX_WIDTH / meta.width, LOGO_MAX_HEIGHT / meta.height)
        path = church.logo.path
        stat = os.stat(path)
        data = _embeddable_logo(path, stat.st_size, stat.st_mtime_ns)
        ImageReader(io.BytesIO(data)).getSize()  # ReportLab reads it now, not halfway through building the PDF
    except Exception:  # whatever the stored file does, the cutoff PDF is still delivered (with the wordmark)
        logger.warning("The church logo %s could not be drawn in a PDF; drawing the wordmark.", church.logo.name, exc_info=True)
        return wordmark
    return Image(io.BytesIO(data), width=meta.width * scale, height=meta.height * scale, hAlign="LEFT")


def _as_datetime(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def _day(dt) -> str:
    """'viernes 23 de octubre de 2026': a printed document keeps the year."""
    return f"{fecha_larga(dt).lower()} de {timezone.localtime(dt).year}"


def _pct(value) -> str:
    return "—" if value is None else f"{value} %"


def _about(cell: dict, noun: str = "") -> str:
    """A rounded count in words, "unas 85 personas": Figtree has no "≈" (ReportLab would draw a box). Read from the
    cell's value, not its display, so a cutoff saved when the display was still "≈ 85" prints the same words. A hidden
    or empty count keeps its own words ("menos de 5", "0", "—")."""
    if cell.get("suppressed") or not cell.get("value"):
        words = cell.get("display", "—")
        words = words[:1].lower() + words[1:]
    elif cell["value"] == 1:  # only with a minimum group size of 1: "1 persona", never "unas 1"
        return f"1 {noun[:-1]}" if noun else "1"
    else:
        words = f"unas {cell['value']}"
    return f"{words} {noun}" if noun and words not in ("—", "0") else words


def _section(question: str, width: float, styles) -> list:
    return [Spacer(1, 10), Rule(width), Paragraph(escape(question), styles["question"])]


def _table_style(numeric_from: int = 1) -> list:
    return [
        ("FONT", (0, 0), (-1, 0), "Figtree-Bold", 9),
        ("FONT", (0, 1), (-1, -1), "Figtree", 9.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK_2),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("BACKGROUND", (0, 0), (-1, 0), HUNDIDO),  # a sunken header row and hairline rules, as on screen
        ("ALIGN", (numeric_from, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
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
        title=f"Corte de las {timezone.localtime(snapshot.cutoff_at):%H:%M}, {snapshot.event.name}",
        author="Emmaus",
    )
    width = doc.width - 12  # the frame pads 6 pt on each side; tables and rules line up with the text
    progress = payload["progress"]
    rate = payload["throughput"]
    hora = timezone.localtime(snapshot.cutoff_at).strftime("%H:%M")
    story = [
        _brand(snapshot.event.church, styles),
        Paragraph(f"Corte de las {hora}", styles["title"]),
        Paragraph(escape(f"{snapshot.event.name}, {payload['scope']['label']}, {_day(snapshot.cutoff_at)}."), styles["lede"]),
        Paragraph(
            escape(
                f"Corte guardado: no cambia. Datos al {fecha_corta(_as_datetime(payload['data_as_of']))}, generado de forma "
                f"{'manual' if snapshot.trigger == 'manual' else 'automática'}."
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
            sub = f"Al ritmo de ese momento, {rate['per_hour']:g} por hora, faltaban alrededor de {format_duration(rate['eta_minutes'])}."
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
        story.append(Paragraph("Todavía no había camperos registrados.", styles["headline"]))
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
        table_rows = [["", "Camperos", "Completadas", "Pendientes", "Avance"]]
        for row in rows_source:
            table_rows.append([row["label"], row["eligible"], row["completed"], row["pending"], _pct(row["percent"])])
        table = Table(table_rows, colWidths=[width * 0.4] + [width * 0.15] * 4, repeatRows=1)
        table.setStyle(TableStyle(_table_style()))
        story += _section(title, width, styles) + [table]

    fu_rows = [
        ["Seguimientos pendientes", fu["pending"]],
        ["En proceso", fu["in_progress"]],
        ["Atendidos", fu["done"]],
        ["Entrevistas por decidir", fu["to_evaluate"]],
    ]
    fu_style = [
        ("FONT", (0, 0), (-1, -1), "Figtree", 10),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
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
            fu_style += [("TEXTCOLOR", (0, -1), (-1, -1), CHILE), ("FONT", (0, -1), (-1, -1), "Figtree-Bold", 10)]
    fu_table = Table(fu_rows, colWidths=[width * 0.6, width * 0.2], hAlign="LEFT")
    fu_table.setStyle(TableStyle(fu_style))
    story.append(KeepTogether(_section("Seguimiento", width, styles) + [fu_table]))

    outcomes = payload.get("outcomes") or []
    if outcomes:
        k = payload.get("min_group_size", 5)
        story += _section("Resumen de respuestas", width, styles)
        story.append(
            Paragraph(
                f"Sin datos individuales. Los totales se redondean de {k} en {k} y las cantidades menores a {k} no se muestran, para que nadie pueda ser identificado.",
                styles["small"],
            )
        )
        story.append(Spacer(1, 6))
        for outcome in outcomes:
            title = escape(outcome["title"])
            sep = " " if title.rstrip()[-1:] in "?!." else ". "
            block = [Paragraph(f"<b>{title}</b>{sep}Respondieron {escape(_about(outcome['respondents'], 'personas'))}.", styles["body"])]
            if outcome["withheld"]:
                block.append(Paragraph("Aún no hay suficientes respuestas para mostrar este resumen.", styles["small"]))
            else:
                data = [[row["label"], _about(row)] for row in outcome["rows"]]
                t = Table(data, colWidths=[width * 0.6, width * 0.2], hAlign="LEFT")
                t.setStyle(TableStyle([
                    ("FONT", (0, 0), (-1, -1), "Figtree", 9.5),
                    ("TEXTCOLOR", (0, 0), (-1, -1), INK),
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
            story.append(Paragraph(f"{index}. {escape(rec['text'])} <font color='#5A6872'>({escape(rec['evidence'])})</font>", styles["body"]))
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
        canvas.setFont("Figtree", 8)
        canvas.setFillColor(INK_3)
        canvas.drawString(left, 9 * mm, "Documento confidencial de uso interno. No reenviar.")
        canvas.drawRightString(right, 9 * mm, f"Página {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
