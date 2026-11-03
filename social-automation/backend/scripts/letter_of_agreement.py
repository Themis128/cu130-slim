#!/usr/bin/env python3
"""Generate the 1-page Cloudless Letter of Agreement (LOA).

Adopts Asset 1 of the Pipeline Optimization Toolkit (Founders 3-11-2026):
a fixed-scope engagement letter — Web Design + Hosting + Automation,
delivered in 14 days, €800 excl. VAT, 50% deposit / 50% on completion,
client owns 100% of the code.

Run anywhere with the backend deps (reportlab) installed:

    python scripts/letter_of_agreement.py --client "Acme SA" \
        --contact "Nikos Papas" --out loa-acme.pdf
    python scripts/letter_of_agreement.py --client "Acme SA" --format md

Defaults: --date today, --amount 800 (deposit/balance auto-halved),
--provider "Themistoklis Baltzakis (Cloudless — cloudless.gr)".
"""

from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime
from pathlib import Path

PROVIDER = "Themistoklis Baltzakis (Cloudless — cloudless.gr)"
SCOPE = (
    "Web Design: a modern, high-converting business website tailored to "
    "your brand. Hosting: fully configured, fast, and secure cloud hosting "
    "with zero maintenance stress. Automation: automatic marketing and "
    "inquiry tracking tools to handle your incoming leads hands-free. "
    "Kickoff Day 1 (align on design, copy, assets); handoff Day 14 — site "
    "and automations live, full documentation. Client owns 100% of the "
    "code and setup."
)


def _money(amount: float) -> str:
    return f"€{amount:,.0f}".replace(",", ".")


def render_markdown(client: str, date: str, amount: float, provider: str) -> str:
    half = amount / 2
    return "\n".join(
        [
            f"Date: {date}",
            "",
            "# LETTER OF AGREEMENT",
            "",
            f"**Client:** {client}",
            "",
            f"**Provider:** {provider}",
            "",
            f"1. **Scope:** {SCOPE}",
            f"2. **Terms:** Total investment is ONLY {_money(amount)} (excl. VAT).",
            f"   Payment split is 50% deposit upfront ({_money(half)}) to lock the date,",
            f"   and 50% ({_money(half)}) on Day 14 completion.",
            "",
            "Client Signature: ___________________________    Date: _______________",
            "",
        ]
    )


def render_pdf(client: str, date: str, amount: float, provider: str, out: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    half = amount / 2
    c = canvas.Canvas(str(out), pagesize=A4)
    width, height = A4
    left, right = 25 * mm, width - 25 * mm
    y = height - 30 * mm

    c.setFont("Helvetica", 11)
    c.drawString(left, y, f"Date: {date}")
    c.setFont("Helvetica-Bold", 16)
    c.drawRightString(right, y - 2 * mm, "LETTER OF AGREEMENT")
    y -= 14 * mm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(left, y, "Client:")
    c.setFont("Helvetica", 11)
    c.drawString(left + 22 * mm, y, client)
    y -= 8 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(left, y, "Provider:")
    c.setFont("Helvetica", 11)
    c.drawString(left + 22 * mm, y, provider)
    y -= 16 * mm

    terms = (
        f"Total investment is ONLY {_money(amount)} (excl. VAT). Payment split "
        f"is 50% deposit upfront ({_money(half)}) to lock the date, and 50% "
        f"({_money(half)}) on Day 14 completion."
    )
    for label, text in (("1. Scope:", SCOPE), ("2. Terms:", terms)):
        c.setFont("Helvetica-Bold", 11)
        c.drawString(left, y, label)
        c.setFont("Helvetica", 11)
        lines = simpleSplit(text, "Helvetica", 11, right - left - 22 * mm)
        x_text = left + 22 * mm
        for line in lines:
            c.drawString(x_text, y, line)
            y -= 6 * mm
            x_text = left + 8 * mm  # wrapped lines align under the paragraph
        y -= 8 * mm

    y -= 20 * mm
    c.setFont("Helvetica", 11)
    c.drawString(left, y, "Client Signature: ___________________________")
    c.drawRightString(right, y, "Date: _______________")
    c.showPage()
    c.save()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--client", required=True, help="Client / company name")
    p.add_argument("--contact", help="Contact person (stored in filename comment only)")
    p.add_argument("--date", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    p.add_argument("--amount", type=float, default=800.0, help="Total excl. VAT (default 800)")
    p.add_argument("--provider", default=PROVIDER)
    p.add_argument("--format", choices=("pdf", "md", "both"), default="pdf")
    p.add_argument("--out", help="Output path (default ./loa-<client>-<date>.<ext>)")
    args = p.parse_args()

    # In-container the compose mount persists generated docs at
    # /app/pipeline-assets (host ./pipeline-assets); fall back to cwd locally.
    out_dir = Path("/app/pipeline-assets") if Path("/app/pipeline-assets").is_dir() else Path(".")
    slug = re.sub(r"[^a-z0-9]+", "-", args.client.lower()).strip("-") or "client"
    base = Path(args.out) if args.out else out_dir / f"loa-{slug}-{args.date}"

    if args.format in ("md", "both"):
        md_path = base.with_suffix(".md")
        md_path.write_text(render_markdown(args.client, args.date, args.amount, args.provider), encoding="utf-8")
        print(f"wrote {md_path}")
    if args.format in ("pdf", "both"):
        pdf_path = base.with_suffix(".pdf")
        render_pdf(args.client, args.date, args.amount, args.provider, pdf_path)
        print(f"wrote {pdf_path}")


if __name__ == "__main__":
    main()
