#!/usr/bin/env python3
"""Render a playbook Markdown file to a clean PDF (reportlab only).

Supports the small Markdown subset the playbooks use: `#`/`##` headings,
paragraphs, `- ` bullets, `- [ ]` checklist items, `1. ` numbered lists,
pipe tables, and bare https:// URLs (rendered as links).

Usage:
    python scripts/build_playbook_pdf.py \
        docs/playbooks/cloud-migration-playbook.md \
        social-automation/frontend/public/playbooks/cloud-migration-playbook.pdf
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ACCENT = colors.HexColor("#0891b2")  # cloudless cyan
INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#4b5563")

_URL_RE = re.compile(r"(https?://[^\s)]+)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


class _CheckBox(Flowable):
    """Hollow square drawn as vector graphics (no font glyph dependency)."""

    def __init__(self, size: float = 3.2 * mm):
        super().__init__()
        self.size = size
        self.width = self.height = size

    def draw(self):
        self.canv.setStrokeColor(ACCENT)
        self.canv.setLineWidth(0.9)
        self.canv.rect(0, -0.6 * mm, self.size, self.size, stroke=1, fill=0)


def _inline(text: str) -> str:
    out = escape(text)
    out = _BOLD_RE.sub(r"<b>\1</b>", out)
    return _URL_RE.sub(
        lambda m: f'<link href="{m.group(1)}" color="#0891b2"><u>{m.group(1)}</u></link>', out
    )


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    body = ParagraphStyle(
        "Body", parent=base["BodyText"], fontName="Helvetica", fontSize=10.5,
        leading=15, textColor=INK, alignment=TA_LEFT, spaceAfter=6,
    )
    return {
        "title": ParagraphStyle("Title", parent=body, fontName="Helvetica-Bold",
                                fontSize=26, leading=31, textColor=INK, spaceAfter=10),
        "lede": ParagraphStyle("Lede", parent=body, fontSize=13, leading=18,
                               textColor=MUTED, spaceAfter=14),
        "byline": ParagraphStyle("Byline", parent=body, textColor=ACCENT,
                                 fontName="Helvetica-Bold", spaceAfter=18),
        "h2": ParagraphStyle("H2", parent=body, fontName="Helvetica-Bold", fontSize=15,
                             leading=19, textColor=ACCENT, spaceBefore=12, spaceAfter=6),
        "body": body,
        "callout": ParagraphStyle(
            "Callout", parent=body, leftIndent=8, textColor=INK,
            borderColor=ACCENT, borderWidth=0, spaceBefore=4, spaceAfter=8,
        ),
        "cell": ParagraphStyle("Cell", parent=body, fontSize=9, leading=12, spaceAfter=0),
        "cellh": ParagraphStyle("CellH", parent=body, fontSize=9, leading=12,
                                spaceAfter=0, fontName="Helvetica-Bold", textColor=colors.white),
    }


def _flush_list(items, kind, st, story):
    if not items:
        return
    if kind == "check":
        t = Table(
            [[_CheckBox(), Paragraph(_inline(x), st["body"])] for x in items],
            colWidths=[7 * mm, None],
            hAlign="LEFT",
        )
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (0, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(KeepTogether([t]))
        return
    lf = ListFlowable(
        [ListItem(Paragraph(_inline(t), st["body"]), leftIndent=14) for t in items],
        bulletType="1" if kind == "num" else "bullet",
        start="1" if kind == "num" else None,
        bulletFontName="Helvetica", bulletFontSize=9 if kind == "bullet" else 10,
        bulletColor=ACCENT, leftIndent=14,
    )
    story.append(lf)


def _table(rows, st, width):
    data = []
    for i, r in enumerate(rows):
        style = st["cellh"] if i == 0 else st["cell"]
        data.append([Paragraph(_inline(c) or "&nbsp;", style) for c in r])
    ncol = len(rows[0])
    t = Table(data, colWidths=[width / ncol] * ncol, repeatRows=1,
              rowHeights=[None] + [11 * mm] * (len(rows) - 1))
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def render(md_path: Path, pdf_path: Path) -> None:
    st = _styles()
    lines = md_path.read_text(encoding="utf-8").splitlines()
    story: list = []
    items: list[str] = []
    kind: str | None = None
    table_rows: list[list[str]] = []
    frame_width = A4[0] - 40 * mm
    seen_h2 = False
    after_title = 0

    def flush():
        nonlocal items, kind
        _flush_list(items, kind, st, story)
        items, kind = [], None

    for raw in lines + [""]:
        line = raw.rstrip()
        if line.startswith("|"):
            flush()
            cells = [c.strip() for c in line.strip("|").split("|")]
            is_sep = any(cells) and all(re.fullmatch(r":?-+:?", c) for c in cells if c)
            if not is_sep:
                table_rows.append(cells)
            continue
        if table_rows:
            story.append(_table(table_rows, st, frame_width))
            story.append(Spacer(1, 6))
            table_rows = []
        if line.startswith("> "):
            flush()
            t = Table([[Paragraph(_inline(line[2:]), st["callout"])]], colWidths=[frame_width])
            t.setStyle(TableStyle([
                ("LINEBEFORE", (0, 0), (0, -1), 2.5, ACCENT),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f0f9fb")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]))
            story.append(KeepTogether([t, Spacer(1, 6)]))
            continue
        m_check = re.match(r"^- \[ \] (.*)", line)
        m_bullet = re.match(r"^- (.*)", line)
        m_num = re.match(r"^\d+\. (.*)", line)
        if m_check or m_bullet or m_num:
            k = "check" if m_check else ("bullet" if m_bullet else "num")
            if kind and kind != k:
                flush()
            kind = k
            items.append((m_check or m_bullet or m_num).group(1))
            continue
        flush()
        if not line:
            continue
        if line.startswith("# "):
            story.append(Spacer(1, 30 * mm))
            story.append(Paragraph(_inline(line[2:]), st["title"]))
            after_title = 1
        elif line.startswith("## "):
            title = line[3:]
            if seen_h2 and (title.startswith("Step ") or title.startswith("Appendix")):
                story.append(PageBreak())
            if not seen_h2:
                story.append(PageBreak())
            seen_h2 = True
            story.append(Paragraph(_inline(title), st["h2"]))
        elif after_title == 1:
            story.append(Paragraph(_inline(line), st["lede"]))
            after_title = 2
        elif after_title == 2:
            story.append(Paragraph(_inline(line), st["byline"]))
            after_title = 0
        else:
            story.append(Paragraph(_inline(line), st["body"]))

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(
            20 * mm, 12 * mm,
            "The Cloud Migration Playbook \u00b7 cloudless.gr \u00b7 free audit: cloudless.gr/contact")
        # Link-annotate just the URL portion of the footer string.
        w = canvas.stringWidth(
            "The Cloud Migration Playbook \u00b7 cloudless.gr \u00b7 free audit: ", "Helvetica", 8)
        uw = canvas.stringWidth("cloudless.gr/contact", "Helvetica", 8)
        canvas.linkURL("https://cloudless.gr/contact",
                       (20 * mm + w, 11 * mm, 20 * mm + w + uw, 14 * mm), relative=0)
        canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, str(doc.page))
        canvas.restoreState()

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=20 * mm, bottomMargin=22 * mm,
        title="The Cloud Migration Playbook", author="cloudless.gr",
        subject="Cloud migration framework for small teams",
        invariant=1,  # reproducible output for identical input
    )
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    render(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"wrote {sys.argv[2]}")
