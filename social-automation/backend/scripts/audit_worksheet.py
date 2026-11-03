#!/usr/bin/env python3
"""Generate the 30-Minute Tech Audit Worksheet (fillable).

Adopts the Pipeline Optimization Toolkit audit-call worksheet (Founders
3-11-2026): discovery questions, red-flag checklist, action plan, and the
Single-Fix close script — one page the owner fills during the free audit.

    python scripts/audit_worksheet.py --out audit-worksheet.pdf
    python scripts/audit_worksheet.py --client "Acme SA" --format both
"""

from __future__ import annotations

import argparse
from pathlib import Path

QUESTIONS = (
    ('Question 1 (The Goal):', '"What do you need to get done with your tech setup or marketing right now?"'),
    ('Question 2 (The Pain):', '"What\'s making that difficult? Is it high software bills, slow tools, or lack of time?"'),
    ('Question 3 (The Timeline):', '"When do you absolutely need this setup and running smoothly?"'),
)
RED_FLAGS = (
    "Overpaying for servers/hosting",
    "Clunky manual data entries",
    "Slow loading website",
    "Trapped in agency contract",
    "Unreliable freelancer tech",
    "No clear lead dashboard",
)
CLOSE = (
    '"You said you need to fix (Pain) and get your (Goal) running. I recommend '
    "our Single Fix. We design an easy website, secure hosting, and automatic "
    "marketing in 14 days flat. You own everything we make. The total is ONLY "
    "€800 (50% upfront / 50% done). Should I send you the payment link?\" "
    "(Then PAUSE and listen)"
)


def render_markdown(client: str, date: str) -> str:
    lines = [
        "# 30-MINUTE TECH AUDIT WORKSHEET",
        "",
        f"**Company:** {client or '________________'}   **Contact Person:** ________________",
        f"**Current Stack:** ________________   **Date:** {date}",
        "",
        "## 1. DISCOVERY PHASE (ASK & LISTEN)",
        "",
    ]
    for label, q in QUESTIONS:
        lines += [f"**{label}** {q}", "", "Answer: ", ""]
    lines += [
        "## 2. TECHNICAL RED FLAGS & COST LEAKS (check during call)",
        "",
    ] + [f"- [ ] {f}" for f in RED_FLAGS] + [
        "",
        "## 3. THE ACTION PLAN (quick wins to give away for free)",
        "",
        "Top 2 or 3 quick fixes they can do right now to save money or time:",
        "",
        "1. ",
        "2. ",
        "3. ",
        "",
        "## 4. THE RECOMMENDATION & CLOSE",
        "",
        "Tailored scope: SINGLE FIX (€800)  /  FULL-STACK BUNDLE (€3,600/mo)",
        "",
        f"How to close the loop: {CLOSE}",
        "",
    ]
    return "\n".join(lines)


def render_pdf(client: str, date: str, out: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(out), pagesize=A4)
    width, height = A4
    left, right, y = 18 * mm, width - 18 * mm, height - 22 * mm

    def para(text, font="Helvetica", size=10, gap=5, bold_first=None):
        nonlocal y
        c.setFont(font, size)
        for line in simpleSplit(text, font, size, right - left):
            c.drawString(left, y, line)
            y -= gap * mm * (size / 10)
        y -= 1.5 * mm

    c.setFont("Helvetica-Bold", 14)
    c.drawCentredString(width / 2, y, "30-MINUTE TECH AUDIT WORKSHEET")
    y -= 10 * mm
    para(f"Company: {client or '__________________'}      Contact Person: __________________", size=10)
    para(f"Current Stack: __________________      Date: {date}", size=10)
    y -= 3 * mm

    def head(t):
        nonlocal y
        c.setFont("Helvetica-Bold", 11)
        c.drawString(left, y, t)
        y -= 6 * mm

    head("1. DISCOVERY PHASE (ASK & LISTEN)")
    for label, q in QUESTIONS:
        para(f"{label} {q}", size=10)
        para("Answer: " + "_" * 70, size=10)
    head("2. TECHNICAL RED FLAGS & COST LEAKS (CHECK DURING CALL)")
    c.setFont("Helvetica", 10)
    for i in range(0, len(RED_FLAGS), 2):
        row = "  ☐  ".join(RED_FLAGS[i : i + 2])
        c.drawString(left, y, "☐  " + row)
        y -= 5 * mm
    y -= 4 * mm
    head("3. THE ACTION PLAN (QUICK WINS TO GIVE AWAY FOR FREE)")
    para("Top 2 or 3 quick fixes they can do right now to save money or time:", size=10)
    for i in (1, 2, 3):
        para(f"{i}. " + "_" * 76, size=10)
    head("4. THE RECOMMENDATION & CLOSE")
    para("Tailored scope:   ☐ SINGLE FIX (€800)     ☐ FULL-STACK BUNDLE (€3,600/mo)", size=10)
    para(f"How to close the loop: {CLOSE}", size=9)
    c.showPage()
    c.save()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--client", default="", help="Pre-fill the company name")
    p.add_argument("--date", default="______________")
    p.add_argument("--format", choices=("pdf", "md", "both"), default="pdf")
    p.add_argument("--out", help="Output path (default ./audit-worksheet.<ext>)")
    args = p.parse_args()
    base = Path(args.out) if args.out else Path("audit-worksheet")
    if args.format in ("md", "both"):
        md = base.with_suffix(".md")
        md.write_text(render_markdown(args.client, args.date), encoding="utf-8")
        print(f"wrote {md}")
    if args.format in ("pdf", "both"):
        pdf = base.with_suffix(".pdf")
        render_pdf(args.client, args.date, pdf)
        print(f"wrote {pdf}")


if __name__ == "__main__":
    main()
