#!/usr/bin/env python3
"""Create the three win-back email templates in Mautic (idempotent by name).

Run inside social-api: ``python scripts/mautic_winback_setup.py``

Emails are ``template`` type — sent 1:1 via ``/emails/{id}/contact/{cid}/send``
by the ``mautic-winback`` beat. Copy follows the re-engagement playbook:
specific value-drop first touch, proof second, breakup close third — single
CTA each, real numbers only.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import mautic_client
from app.services.mautic_winback import _EMAIL_NAMES

_SUBJECTS = (
    "The 30-minute audit we offer (still relevant?)",
    "What our own funnel data shows about cold leads",
    "Should I close your file?",
)

# Plain-text copy is the source of truth: it is the text/plain part and is
# rendered through the shared branded layout (funnel_email) for the HTML
# part. Mautic merge tokens ({contactfield=firstname}) contain no HTML
# specials, so they survive escaping and Mautic substitutes them at send.
_TEXTS = (
    """Hi {contactfield=firstname},

A while back you reached out — or downloaded something — and the conversation paused. No pitch, no guilt trip.

The offer stands: a free 30-minute audit of where your digital funnel actually leaks.
You bring the business, we bring the teardown — real numbers, no slides.

Book the audit: https://cloudless.gr/en/consultation

Or just reply to this email and we'll find a slot.

— Cloudless""",
    """Hi {contactfield=firstname},

Quick honesty check: most leads we capture go cold because nobody follows up with something useful. So here's ours.

Our own funnel tracked 1,015 followers → 205 CRM leads → and, until recently, zero opportunities.
The fix wasn't more traffic — it was a scoring + re-engagement loop. That's the same loop we audit for clients.

If that's the kind of leak you suspect in your funnel, the free audit maps it in 30 minutes.

Book the audit: https://cloudless.gr/en/consultation

— Cloudless""",
    """Hi {contactfield=firstname},

Last note from me on this. If the timing wasn't right, no problem — priorities shift.

If you'd rather I stop emailing about the audit, reply "no" and I'll close the file.
If it's still on the table, grab a slot — takes two minutes: https://cloudless.gr/en/consultation

Either way, thanks for the earlier interest.

— Cloudless""",
)

_CTA_LABELS = ("Book the audit", "Book the audit", "Grab a slot")


def build_bodies() -> list[tuple[str, str]]:
    """(plain_text, branded_html) per win-back email, in _EMAIL_NAMES order."""
    from app.services.funnel_email import render_funnel_html

    return [(text, render_funnel_html(text, subject=subject, cta_label=label)) for subject, text, label in zip(_SUBJECTS, _TEXTS, _CTA_LABELS, strict=True)]


async def main() -> None:
    if not mautic_client.configured():
        print("mautic not configured")
        return
    existing = await mautic_client.mautic_get("/emails", params={"limit": 200})
    items = existing.get("emails") or {}
    by_name = {str(e.get("name")): int(e["id"]) for e in (items.values() if isinstance(items, dict) else items)}
    for name, subject, (text, body) in zip(_EMAIL_NAMES, _SUBJECTS, build_bodies(), strict=True):
        if name in by_name:
            print(f"exists: {name} (id {by_name[name]})")
            continue
        r = await mautic_client.mautic_post(
            "/emails/new",
            {
                "name": name,
                "subject": subject,
                "customHtml": body,
                "plainText": text,
                "emailType": "template",
                "isPublished": True,
            },
        )
        eid = (r.get("email") or {}).get("id")
        print(f"created: {name} -> {eid} {r.get('errors') or ''}")


if __name__ == "__main__":
    asyncio.run(main())
