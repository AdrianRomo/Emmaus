"""PDF export of a cutoff, built with ReportLab (pure Python, no system packages).

The PDF is generated from the *viewer-trimmed* payload, so it never shows more than
the person downloading it may see on screen.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

from django.utils import formats, timezone
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

from .metrics import DEFINITIONS, format_duration

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
INK = colors.HexColor("#1c2420")
INK_2 = colors.HexColor("#48524c")
INK_3 = colors.HexColor("#5d645e")
LINE = colors.HexColor("#ddd3c1")
DONE = colors.HexColor("#1e7a50")
PROGRESS = colors.HexColor("#c98321")
TRACK = colors.HexColor("#e7decd")
ACCENT = colors.HexColor("#a3461e")

_registered = False


def _register_fonts() -> None:
    global _registered
    if _registered:
        return
    pdfmetrics.registerFont(TTFont("EmmausSans", str(FONT_DIR / "Atkinson-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("EmmausSans-Bold", str(FONT_DIR / "Atkinson-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("EmmausSerif", str(FONT_DIR / "Literata-Regular.ttf")))
    pdfmetrics.registerFont(TTFont("EmmausSerif-Semibold", str(FONT_DIR / "Literata-Semibold.ttf")))
    from reportlab.pdfbase.pdfmetrics import registerFontFamily

    registerFontFamily("EmmausSans", normal="EmmausSans", bold="EmmausSans-Bold", italic="EmmausSans", boldItalic="EmmausSans-Bold")
    _registered = True


def _styles():
    return {
        "title": ParagraphStyle("title", fontName="EmmausSerif-Semibold", fontSize=22, leading=26, textColor=INK, spaceAfter=4),
        "folio": ParagraphStyle("folio", fontName="EmmausSans", fontSize=9.5, leading=13, textColor=INK_3),
        "h2": ParagraphStyle("h2", fontName="EmmausSerif-Semibold", fontSize=14, leading=18, textColor=INK, spaceBefore=14, spaceAfter=6),
        "body": ParagraphStyle("body", fontName="EmmausSans", fontSize=10.5, leading=15, textColor=INK, alignment=TA_LEFT),
        "small": ParagraphStyle("small", fontName="EmmausSans", fontSize=8.5, leading=12, textColor=INK_3),
        "figure": ParagraphStyle("figure", fontName="EmmausSans-Bold", fontSize=26, leading=28, textColor=INK),
        "figlabel": ParagraphStyle("figlabel", fontName="EmmausSans", fontSize=9.5, leading=12, textColor=INK_2),
    }


class Meter(Flowable):
    """Completed / in progress / remaining bar, with a 2pt paper gap between segments."""

    def __init__(self, completed: int, in_progress: int, total: int, width: float, height: float = 9):
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
            c.setFillColor(PROGRESS)
            c.rect(done_w + (1.5 if done_w else 0), 0, max(prog_w - 1.5, 0), self.height, stroke=0, fill=1)


class Timeline(Flowable):
    """Cumulative completions line; one axis; label at the last point only."""

    def __init__(self, points: list[dict], total: int, width: float, height: float = 52 * mm):
        super().__init__()
        self.points, self.total, self.width, self.height = points, total, width, height

    def draw(self):
        c = self.canv
        left, bottom, right, top = 24, 16, 46, 6
        plot_w, plot_h = self.width - left - right, self.height - bottom - top
        peak = max([self.total] + [p["cumulative"] for p in self.points] + [1])
        c.setFont("EmmausSans", 7.5)
        for i in range(5):
            y = bottom + plot_h * i / 4
            c.setStrokeColor(LINE)
            c.setLineWidth(0.5)
            c.line(left, y, left + plot_w, y)
            c.setFillColor(INK_3)
            c.drawRightString(left - 4, y - 2.5, str(int(round(peak * i / 4))))
        if self.total:
            y_total = bottom + plot_h * self.total / peak
            c.setStrokeColor(INK_3)
            c.line(left, y_total, left + plot_w, y_total)
            c.drawString(left + plot_w + 4, y_total - 2.5, f"Total {self.total}")
        n = len(self.points)
        if not n:
            return
        xs = [left + (plot_w * i / (n - 1) if n > 1 else plot_w / 2) for i in range(n)]
        ys = [bottom + plot_h * p["cumulative"] / peak for p in self.points]
        c.setStrokeColor(DONE)
        c.setLineWidth(1.6)
        path = c.beginPath()
        path.moveTo(xs[0], ys[0])
        for x, y in zip(xs[1:], ys[1:]):
            path.lineTo(x, y)
        c.drawPath(path, stroke=1, fill=0)
        c.setFillColor(DONE)
        c.circle(xs[-1], ys[-1], 2.6, stroke=0, fill=1)
        c.setFillColor(INK)
        c.setFont("EmmausSans-Bold", 8.5)
        c.drawString(xs[-1] + 5, ys[-1] + 3, str(self.points[-1]["cumulative"]))
        c.setFont("EmmausSans", 7.5)
        c.setFillColor(INK_3)
        every = max(1, -(-n // 7))
        for i, point in enumerate(self.points):
            if i % every == 0 or i == n - 1:
                c.drawCentredString(xs[i], 4, point["label"])


def _fmt(dt) -> str:
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return formats.date_format(timezone.localtime(dt), "l j \\d\\e F \\d\\e Y, H:i")


def build_snapshot_pdf(snapshot, payload: dict, *, delta: dict | None = None) -> bytes:
    _register_fonts()
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Corte de seguimiento - {snapshot.event.name}",
        author="Emmaus",
    )
    width = doc.width
    progress = payload["progress"]
    story = [
        Paragraph(f"{snapshot.event.name} · {payload['scope']['label']}", styles["folio"]),
        Paragraph(f"Corte de seguimiento de las {timezone.localtime(snapshot.cutoff_at).strftime('%H:%M')}", styles["title"]),
        Paragraph(
            f"Datos al {_fmt(payload['data_as_of'])}. Generado de forma {snapshot.get_trigger_display().lower()}. "
            f"Definiciones de métricas, versión {payload['metrics_version']}.",
            styles["folio"],
        ),
        Spacer(1, 10),
    ]
    pct = "sin datos" if progress["percent"] is None else f"{progress['percent']} %"
    figures = [
        ("Participantes", str(progress["eligible"])),
        ("Completadas", str(progress["completed"])),
        ("Pendientes", str(progress["pending"])),
        ("Avance", pct),
    ]
    fig_table = Table(
        [[Paragraph(value, styles["figure"]) for _, value in figures], [Paragraph(label, styles["figlabel"]) for label, _ in figures]],
        colWidths=[width / 4] * 4,
    )
    fig_table.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, 0), 2)]))
    story += [fig_table, Spacer(1, 8), Meter(progress["completed"], progress["in_progress"], progress["eligible"], width)]
    legend = f"Completadas {progress['completed']} · En curso {progress['in_progress']} · Sin iniciar {progress['not_started']}"
    story += [Spacer(1, 4), Paragraph(legend, styles["small"])]
    if delta:
        sign = lambda v: f"+{v}" if v and v > 0 else str(v)  # noqa: E731
        story.append(
            Paragraph(
                f"Desde el corte anterior: {sign(delta['completed'])} completadas, avance {sign(delta['percent']) if delta['percent'] is not None else '—'} puntos.",
                styles["small"],
            )
        )
    rate = payload["throughput"]
    rate_text = f"Ritmo: {rate['per_hour']:g} entrevistas por hora (promedio de las últimas 2 horas)."
    if rate.get("eta_minutes"):
        rate_text += f" Al ritmo actual faltan unas {format_duration(rate['eta_minutes'])}."
    story += [Spacer(1, 6), Paragraph(rate_text, styles["body"])]

    points = payload["timeline"]["points"]
    story.append(Paragraph("Avance de entrevistas", styles["h2"]))
    if points:
        story.append(Paragraph("Entrevistas completadas acumuladas", styles["small"]))
        story.append(Timeline(points, progress["eligible"], width))
    else:
        story.append(Paragraph("Todavía no hay entrevistas completadas.", styles["body"]))

    rows_source = payload.get("teams") or []
    title = "Avance por equipo"
    if not rows_source and payload.get("coordinators"):
        rows_source = [{"label": c["name"], **c} for c in payload["coordinators"]]
        title = "Avance por coordinador"
    if rows_source:
        table_rows = [["", "Participantes", "Completadas", "Pendientes", "Avance"]]
        for row in rows_source:
            table_rows.append(
                [row["label"], row["eligible"], row["completed"], row["pending"], "—" if row["percent"] is None else f"{row['percent']} %"]
            )
        table = Table(table_rows, colWidths=[width * 0.4] + [width * 0.15] * 4, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("FONT", (0, 0), (-1, 0), "EmmausSans-Bold", 9),
                    ("FONT", (0, 1), (-1, -1), "EmmausSans", 9.5),
                    ("TEXTCOLOR", (0, 0), (-1, 0), INK_2),
                    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK_3),
                    ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        story += [Paragraph(title, styles["h2"]), table]

    fu = payload["follow_ups"]
    fu_rows = [
        ["Seguimientos pendientes", fu["pending"]],
        ["En proceso", fu["in_progress"]],
        ["Atendidos", fu["done"]],
        ["Entrevistas por evaluar", fu["to_evaluate"]],
    ]
    if fu.get("safety_open") is not None:
        fu_rows.append(["Situaciones de riesgo abiertas", fu["safety_open"]])
    fu_table = Table(fu_rows, colWidths=[width * 0.6, width * 0.2])
    fu_table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "EmmausSans", 10),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
    ]))
    story += [KeepTogether([Paragraph("Seguimiento", styles["h2"]), fu_table])]

    outcomes = payload.get("outcomes") or []
    if outcomes:
        k = payload.get("min_group_size", 5)
        story.append(Paragraph("Resumen de respuestas (sin datos individuales)", styles["h2"]))
        story.append(
            Paragraph(
                f"Cifras redondeadas a múltiplos de {k}. Las cantidades menores a {k} se ocultan para que nadie pueda ser identificado.",
                styles["small"],
            )
        )
        for outcome in outcomes:
            block = [Paragraph(f"<b>{outcome['title']}</b> — respondieron {outcome['respondents']['display']}", styles["body"])]
            if outcome["withheld"]:
                block.append(Paragraph("Aún no hay suficientes respuestas para mostrar este resumen.", styles["small"]))
            else:
                data = [[row["label"], row["display"]] for row in outcome["rows"]]
                t = Table(data, colWidths=[width * 0.6, width * 0.2])
                t.setStyle(TableStyle([
                    ("FONT", (0, 0), (-1, -1), "EmmausSans", 9.5),
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ]))
                block.append(t)
            block.append(Spacer(1, 6))
            story.append(KeepTogether(block))

    recs = payload.get("recommendations") or []
    if recs:
        story.append(Paragraph("Próximos pasos", styles["h2"]))
        for index, rec in enumerate(recs, start=1):
            story.append(Paragraph(f"{index}. {rec['text']} <font color='#5d645e'>({rec['evidence']})</font>", styles["body"]))
            story.append(Spacer(1, 3))

    story.append(Paragraph("Cómo se calcula", styles["h2"]))
    for name, text in DEFINITIONS:
        story.append(Paragraph(f"<b>{name}:</b> {text}", styles["small"]))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("EmmausSans", 8)
        canvas.setFillColor(INK_3)
        canvas.drawString(doc.leftMargin, 10 * mm, "Emmaus · Documento confidencial de uso interno. No reenviar.")
        canvas.drawRightString(doc.leftMargin + doc.width, 10 * mm, f"Página {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
