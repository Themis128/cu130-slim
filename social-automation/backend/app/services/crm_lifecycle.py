"""CRM lifecycle — score every live lead, convert hot ones.

Closes the two dead stages in the capture→clean→score→route→lifecycle loop:

- **Score**: ``scoreLead`` (cloudless.gr ``lead-scoring.ts``) only fires on the
  contact-form path — playbook/Messenger/manual leads enter unscored.
  ``score_lead`` mirrors the same deterministic model (0-100, hot >= 65,
  warm >= 35, explainable reasons) against the fields an EspoCRM Lead row
  actually carries. The ``Lead score:`` note is the dedupe marker; a lead is
  re-scored when a newer engagement note (reply) post-dates its last score.
- **Convert**: ``hot`` leads auto-convert to Contact + Opportunity (Account
  when a company name exists) via ``POST /Lead/action/convert`` — the same
  "hot → opportunity" rule the contact path already applies. Espo's convert
  endpoint takes ``records`` keyed by entity type (the ``convertEntity``
  array people try does nothing). Synthetic/test/self leads are never
  converted; conversion is gated by ``CRM_CONVERT_ENABLED`` (default on).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.core.config import get_settings
from app.services import espocrm_client as espo

logger = logging.getLogger(__name__)

SCORE_MARKER = "Lead score:"
REPLY_MARKER = "Reply received"
DRIP_MARKER = "Email drip"

HOT_THRESHOLD = 65  # same band cut-offs as cloudless.gr lead-scoring.ts
WARM_THRESHOLD = 35

_FREE_MAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "yahoo.gr",
    "hotmail.com",
    "outlook.com",
    "icloud.com",
    "proton.me",
    "protonmail.com",
    "aol.com",
    "live.com",
    "msn.com",
    "mail.com",
    "gmx.com",
    "yandex.com",
}
_SYNTHETIC_DOMAINS = {"messenger.local", "cloudless.gr", "example.com", "example.invalid"}
_TEST_MARKERS = ("ci-test+", "test@test", "@example.invalid", "@example.com")

_PROJECT_SIGNAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(budget|invest|spend)\b", re.I), "mentions budget"),
    (
        re.compile(r"\b(timeline|deadline|asap|urgent)\b", re.I),
        "mentions timeline",
    ),
    (re.compile(r"\b(migrate|migration|launch|rebuild|redesign|scale|grow)\b", re.I), "concrete project verb"),
    (re.compile(r"\b(quote|proposal|estimate|pricing|price)\b", re.I), "asks for proposal/pricing"),
)
_PAID_MEDIUMS = {"cpc", "ppc", "paid", "paid_social", "paidsocial"}

_SELECT = [
    "id",
    "name",
    "firstName",
    "lastName",
    "emailAddress",
    "accountName",
    "status",
    "source",
    "website",
    "title",
    "description",
    "assignedUserId",
    "createdAt",
]


def _email(lead: dict[str, Any]) -> str:
    return str(lead.get("emailAddress") or "").strip().lower()


def is_test_lead(lead: dict[str, Any]) -> bool:
    email = _email(lead)
    name = str(lead.get("name") or "").lower()
    return any(m in email or m in name for m in _TEST_MARKERS) or email.rsplit("@", 1)[-1] in {"cloudless.gr", "example.com", "example.invalid"}


def _is_synthetic(lead: dict[str, Any]) -> bool:
    """Synthetic or unverifiable addresses — score but never convert."""
    email = _email(lead)
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    return not email or domain in _SYNTHETIC_DOMAINS or is_test_lead(lead)


def _utm(description: str, key: str) -> str:
    m = re.search(rf"(?im)^{re.escape(key)}:\s*(.+)$", description)
    return m.group(1).strip() if m else ""


def score_lead(lead: dict[str, Any], notes: list[dict[str, Any]]) -> tuple[int, str, list[str]]:
    """Deterministic 0-100 score mirroring cloudless.gr ``lead-scoring.ts``.

    Same cut-offs and the same explainable-reasons contract, adapted to the
    fields EspoCRM exposes on the Lead entity:

      Company identity   up to 15  (accountName provided)
      Website declared   up to 10
      Description        up to 25  (length + project-signal patterns)
      Attribution        up to 20  (paid medium / campaign / referrer)
      Business email     up to 10  (non-free-mail domain)
      Engagement         up to 25  (reply >> drip-touch)
    """
    reasons: list[str] = []
    pts = 0
    email = _email(lead)
    desc = str(lead.get("description") or "")
    note_posts = [str(n.get("post") or "") for n in notes]

    if lead.get("accountName"):
        pts += 15
        reasons.append("company provided (+15)")

    if lead.get("website"):
        pts += 10
        reasons.append("business website declared (+10)")

    dpts = 0
    length = len(desc.strip())
    if length >= 200:
        dpts += 10
        reasons.append("detailed description (+10)")
    elif length >= 60:
        dpts += 5
        reasons.append("substantive description (+5)")
    for pattern, label in _PROJECT_SIGNAL_PATTERNS:
        if pattern.search(desc):
            dpts += 5
            reasons.append(f"{label} (+5)")
    pts += min(dpts, 25)

    apts = 0
    if _utm(desc, "utm_medium").lower() in _PAID_MEDIUMS:
        apts += 10
        reasons.append("arrived via paid campaign (+10)")
    if campaign := _utm(desc, "utm_campaign") or _utm(desc, "campaign"):
        apts += 5
        reasons.append(f"campaign: {campaign} (+5)")
    source = str(lead.get("source") or "")
    if apts == 0 and source and source not in ("Direct", "Other"):
        apts += 5
        reasons.append(f"arrived via {source} (+5)")
    pts += min(apts, 20)

    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    if domain and domain not in _FREE_MAIL_DOMAINS and domain not in _SYNTHETIC_DOMAINS:
        pts += 10
        reasons.append(f"business email domain: {domain} (+10)")

    if any(REPLY_MARKER in p for p in note_posts):
        pts += 25
        reasons.append("replied to outreach (+25)")
    elif sum(1 for p in note_posts if DRIP_MARKER in p) >= 2:
        pts += 5
        reasons.append("nurture sequence engaged (+5)")

    score = max(0, min(100, pts))
    band = "hot" if score >= HOT_THRESHOLD else "warm" if score >= WARM_THRESHOLD else "cold"
    return score, band, reasons


async def _lead_notes(lead_id: str) -> list[dict[str, Any]]:
    return await espo.list_all(
        "Note",
        select=["id", "post", "createdAt"],
        where=[
            {"type": "equals", "attribute": "parentType", "value": "Lead"},
            {"type": "equals", "attribute": "parentId", "value": lead_id},
        ],
        max_items=100,
    )


def _needs_score(notes: list[dict[str, Any]]) -> bool:
    """Score once — re-score only when a reply landed after the last score."""
    scored = [n for n in notes if SCORE_MARKER in (n.get("post") or "")]
    if not scored:
        return True
    last_score = max(str(n.get("createdAt") or "") for n in scored)
    replies = [n for n in notes if REPLY_MARKER in (n.get("post") or "")]
    return any(str(n.get("createdAt") or "") > last_score for n in replies)


def _score_note(lead: dict[str, Any], score: int, band: str, reasons: list[str]) -> str:
    head = f"{SCORE_MARKER} {score}/100 ({band})"
    detail = " · ".join(reasons) if reasons else "no signals"
    return f"{head}\n{detail}"


async def _convert(lead: dict[str, Any]) -> dict[str, Any]:
    """Run Espo's own convert action — Contact + Opportunity (+Account).

    ``getConvertAttributes`` returns the values the UI pre-fills; the convert
    payload only creates entity types present in ``records``.
    """
    lid = str(lead["id"])
    attrs = await espo.espo_post("/Lead/action/getConvertAttributes", {"id": lid})
    records: dict[str, Any] = {}
    if isinstance(attrs.get("Contact"), dict):
        records["Contact"] = attrs["Contact"]
    else:
        records["Contact"] = {
            "firstName": lead.get("firstName") or "",
            "lastName": lead.get("lastName") or lead.get("name") or "Lead",
            "emailAddress": _email(lead),
            "title": lead.get("title") or "",
        }
    if lead.get("accountName"):
        records["Account"] = (
            attrs.get("Account") if isinstance(attrs.get("Account"), dict) else {"name": lead["accountName"], "website": lead.get("website") or ""}
        )
    records["Opportunity"] = (
        attrs.get("Opportunity")
        if isinstance(attrs.get("Opportunity"), dict)
        else {
            "name": f"{lead.get('accountName') or lead.get('name') or 'Lead'} — opportunity",
            "stage": "Prospecting",
        }
    )
    return await espo.espo_post("/Lead/action/convert", {"id": lid, "records": records})


def _live_lead(lead: dict[str, Any]) -> bool:
    return lead.get("status") not in ("Dead", "Converted")


async def run_crm_lifecycle_sweep(*, dry_run: bool = False) -> dict[str, Any]:
    """Score unscored live leads; convert hot, real-email leads."""
    settings = get_settings()
    leads = [lead for lead in await espo.list_all("Lead", select=_SELECT) if _live_lead(lead)]
    stats: dict[str, Any] = {
        "total": len(leads),
        "scored": 0,
        "rescored": 0,
        "converted": 0,
        "errors": 0,
        "dry_run": dry_run,
        "details": [],
    }
    convert_on = getattr(settings, "CRM_CONVERT_ENABLED", True)

    for lead in leads:
        lid = str(lead["id"])
        try:
            notes = await _lead_notes(lid)
            if not _needs_score(notes):
                continue
            score, band, reasons = score_lead(lead, notes)
            first_score = not any(SCORE_MARKER in (n.get("post") or "") for n in notes)
            entry = {
                "lead_id": lid,
                "name": lead.get("name"),
                "score": score,
                "band": band,
                "converted": False,
            }
            if not dry_run:
                await espo.add_lead_note(lid, _score_note(lead, score, band, reasons))
            stats["scored" if first_score else "rescored"] += 1

            convertible = band == "hot" and convert_on and not _is_synthetic(lead) and lead.get("assignedUserId")
            if convertible:
                if dry_run:
                    entry["converted"] = "would-convert"
                else:
                    result = await _convert(lead)
                    contact = result.get("createdContactId")
                    opp = result.get("createdOpportunityId")
                    entry["converted"] = True
                    stats["converted"] += 1
                    await espo.add_lead_note(
                        lid,
                        f"Converted by lifecycle sweep → Contact {contact or '?'}, Opportunity {opp or '?'} (score {score}/{band})",
                    )
            stats["details"].append(entry)
        except Exception:  # noqa: BLE001 — one bad lead must not stall the sweep
            logger.exception("crm lifecycle: lead %s failed", lid)
            stats["errors"] += 1

    await _slack_report(stats)
    return stats


async def _slack_report(stats: dict[str, Any]) -> None:
    try:
        from app.services.slack_notifications import post_digest_text_to_slack
    except Exception:  # noqa: BLE001
        return
    hot = [d for d in stats["details"] if d.get("band") == "hot"]
    lines = [
        "*CRM lifecycle sweep* — "
        f"{stats['scored']} scored, {stats['rescored']} rescored, "
        f"{stats['converted']} converted, {stats['errors']} errors" + (" (dry run)" if stats["dry_run"] else "")
    ]
    for d in (hot or stats["details"])[:5]:
        conv = " → converted" if d["converted"] is True else ""
        lines.append(f"• {d['name']}: {d['score']}/100 {d['band']}{conv}")
    try:
        await post_digest_text_to_slack("\n".join(lines))
    except Exception:  # noqa: BLE001
        logger.exception("crm lifecycle: slack report failed")
