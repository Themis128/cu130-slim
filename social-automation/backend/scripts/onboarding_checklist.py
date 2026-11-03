#!/usr/bin/env python3
"""Generate the 14-Day Client Onboarding & Fulfillment Checklist.

Adopts the Pipeline Optimization Toolkit / Agent Manifest
PRODUCTION_CHECKLIST (Founders 3-11-2026): the ops contract that runs once
a client says YES — Day 1 deal-secure, Days 2-10 build, Days 11-14 QC +
handoff. One fillable page per engagement.

    python scripts/onboarding_checklist.py --client "Acme SA" --out acme-onboarding.pdf
    python scripts/onboarding_checklist.py --client "Acme SA" --format both

Same contract as audit_worksheet.py: Markdown and/or reportlab PDF,
/app/pipeline-assets persistence when run inside social-api.
"""

from __future__ import annotations

import argparse
from pathlib import Path

PHASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Phase 1: Secure the Deal (Day 1)",
        (
            "Send Letter of Agreement (scripts/letter_of_agreement.py) — signed copy back",
            "Collect 50% deposit (€400 + VAT) — kickoff date locked on payment",
            "Create the client asset folder + share the workspace invite links",
            "Confirm by text: copy, logo, brand assets due from client today",
        ),
    ),
    (
        "Phase 2: Build & Configure (Days 2-10)",
        (
            "Assemble the site layout — fast-render first, open-source stack",
            "Provision hosting + wire the SSL certificate",
            "Build the inquiry → dashboard automation (form → lead capture → notify)",
            "Internal QA pass: forms fire, dashboard updates, welcome email sends",
        ),
    ),
    (
        "Phase 3: Quality Control & Handoff (Days 11-14)",
        (
            "Mobile layout + form simulation checks (0ms tracking delay target)",
            "Write the handoff runbook + code blueprint docs",
            "Collect final 50% (€400 + VAT)",
            "Transfer root access + 100% code ownership on cleared invoice",
        ),
    ),
)


def render_markdown(client: str, date: str) -> str:
    lines = [
        "# 14-DAY CLIENT ONBOARDING & FULFILLMENT CHECKLIST",
        "",
        f"**Client:** {client or '________________'}   **Kickoff:** {date}   **Go-live target:** Day 14",
        "**Scope:** Web Design + Hosting + Automation — €800 excl. VAT (50/50)",
        "",
    ]
    for title, tasks in PHASES:
        lines += [f"## {title}", ""] + [f"- [ ] {t}" for t in tasks] + [""]
    lines += [
        "---",
        "On cleared final invoice: hand over root access + full code ownership.",
        "No retainers, no lock-in — the client owns everything.",
    ]
    return "\n".join(lines)


def render_pdf(client: str, date: str, out: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(out), pagesize=A4)
    width, height = A4
    left, right, y = 18 * mm, width - 18 * mm, height - 20 * mm

    def para(text, font="Helvetica", size=10, gap=4.6):
        nonlocal y
        c.setFont(font, size)
        for line in simpleSplit(text, font, size, right - left):
            c.drawString(left, y, line)
            y -= gap * mm * (size / 10)
        y -= 1.2 * mm

    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(width / 2, y, "14-DAY CLIENT ONBOARDING & FULFILLMENT CHECKLIST")
    y -= 9 * mm
    para(f"Client: {client or '________________'}      Kickoff: {date}      Go-live: Day 14")
    para("Scope: Web Design + Hosting + Automation — EUR 800 excl. VAT (50% / 50%)", size=9)
    y -= 2 * mm
    for title, tasks in PHASES:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(left, y, title)
        y -= 6 * mm
        c.setFont("Helvetica", 9.5)
        for t in tasks:
            for line in simpleSplit(f"[ ]  {t}", "Helvetica", 9.5, right - left - 4 * mm):
                c.drawString(left + 2 * mm, y, line)
                y -= 4.6 * mm
        y -= 2 * mm
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(left, y, "On cleared final invoice: root access + 100% code ownership. No retainers.")
    c.showPage()
    c.save()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--client", default="", help="Pre-fill the company name")
    p.add_argument("--date", default="______________")
    p.add_argument("--format", choices=("pdf", "md", "both"), default="pdf")
    p.add_argument("--out", help="Output path (default ./<client>-onboarding.<ext>)")
    args = p.parse_args()
    out_dir = Path("/app/pipeline-assets") if Path("/app/pipeline-assets").is_dir() else Path(".")
    base = Path(args.out) if args.out else out_dir / f"{(args.client or 'client').lower().replace(' ', '-')}-onboarding"
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
