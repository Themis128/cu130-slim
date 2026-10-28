#!/usr/bin/env python3
"""LinkedIn ad-funnel audit — the LinkedIn SMB-ads playbook's outcome chain.

Adoption of the LinkedIn Marketing Solutions case-study brief
"Kakkava Sofia/Linkedin/1792140163392.pdf" — two B2B SMBs whose measurable
outcomes came from a funnel discipline, not from ads alone:

  Dark Horse (-70% CPL, 23% CVR, 97 leads/4mo)
    · Document Ads to show expertise upfront
    · Lead Gen Forms + Message Ads to capture high-intent prospects
    · A/B testing on ad copy — continuous optimization
    · leads flow straight into marketing-automation nurture

  Connectd (2x revenue, -10% cost/conv, 200x ARR)
    · Conversions API + CRM sync — a measurement foundation BEFORE scaling
    · diversified formats: Document / Thought Leader / boosted posts
    · LinkedIn Insights to target industries, titles, regions

Live checks (no heuristics — every number comes from a real surface):

  campaign inventory  — Marketing API adCampaigns + ad_campaign_snapshots
  format diversity    — distinct ad formats the account has run (Connectd:
                        single-format strategy was the failure mode)
  A/B rotation        — creative count per active campaign; the r_ads token
                        cannot enumerate creatives (probed 2026-10-20:
                        /rest/creatives and adLeadForms are unreachable), so
                        this is reported as a CM-manual check, not faked
  credit safety       — account-wide spend + resumable caps vs promo credit
  lead capture        — EspoCRM leads with utmMedium=paid (Dark Horse's
                        "leads flow into nurture" — our loop)
  paid attribution    — web_analytics_events with utm_medium=paid, all-time
  measurement base    — LinkedIn Insight Tag presence on cloudless.gr
                        (Connectd's "measurement foundation"; without it a
                        conversion is unmeasurable to LinkedIn)
  document-ads stock  — branded carousel PDFs in media_assets ready to be
                        sponsored as Document Ads

Run inside social-api (PYTHONPATH=/app):

    python scripts/linkedin_ads_funnel_audit.py          # human report
    python scripts/linkedin_ads_funnel_audit.py --json   # machine output
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.session import async_session_maker  # noqa: E402

_SITE = "https://cloudless.gr/"
# LinkedIn Insight Tag markers in page source — snap.licdn.com is the loader,
# _linkedin_partner_id the configured partner account.
_TAG_RE = re.compile(r"snap\.licdn\.com|_linkedin_partner_id", re.I)

# Marketing API endpoints the paper's surfaces live behind. Probed
# 2026-10-20 with the org r_ads token — several are unreachable with our
# scopes, so reachability itself is the audited fact.
_AD_ACCT = "512642510"
_AD_URN = f"urn:li:sponsoredAccount:{_AD_ACCT}"


async def _campaigns_and_credit() -> dict:
    """Live campaign inventory + credit-safety math via collect_metrics."""
    try:
        from app.services.linkedin_ads_report import collect_metrics
    except ImportError:
        return {"error": "linkedin_ads_report unavailable in this env"}
    async with async_session_maker() as db:
        m = await collect_metrics(db)
    campaigns = [
        {
            "id": c["id"],
            "name": c["name"],
            "status": c["status"],
            "budget_eur": c["budget"],
        }
        for c in (m.raw.get("campaigns") or [])
    ]
    if not campaigns and m.campaign_id:
        campaigns.append({"id": m.campaign_id, "name": m.campaign_name, "status": m.status, "budget_eur": m.budget_eur})
    credit = get_settings().LINKEDIN_ADS_CREDIT_EUR
    # Worst-case exposure: spend so far + remaining cap of every campaign
    # that could still deliver (active/paused/draft) — the truth ladder from
    # linkedin-ads-ops. spend_by_campaign is per-campaign from adAnalytics.
    resumable_cap = sum(c["budget"] or 0.0 for c in (m.raw.get("campaigns") or []) if c["status"] in ("active", "paused", "draft"))
    spent_by_campaign = m.raw.get("spend_by_campaign") or {}
    resumable_spent = sum(spent_by_campaign.get(c["id"], 0.0) for c in (m.raw.get("campaigns") or []) if c["status"] in ("active", "paused", "draft"))
    worst_case = m.account_spend_eur - resumable_spent + resumable_cap
    return {
        "campaigns": campaigns,
        "tracked_status": m.status,
        "account_spend_eur": m.account_spend_eur,
        "credit_eur": credit,
        "worst_case_eur": round(worst_case, 2) if credit else None,
        "api_error": m.raw.get("api_error"),
    }


async def _format_diversity() -> dict:
    """Distinct formats the account has ever run — from snapshot names
    (format is encoded in the campaign name, e.g. '— carousel')."""
    async with async_session_maker() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT campaign_id, campaign_name, status,"
                    "       max(captured_at) AS last_seen"
                    "  FROM ad_campaign_snapshots"
                    " GROUP BY campaign_id, campaign_name, status"
                    " ORDER BY last_seen DESC"
                )
            )
        ).all()
    return {"campaigns_seen": [{"id": r.campaign_id, "name": r.campaign_name, "status": r.status} for r in rows]}


async def _leadgen_and_conversions(token_note: dict) -> dict:
    """Reachability of the paper's capture surfaces — leadForms and
    conversions — with the org r_ads token. Unreachable is a finding,
    not an error."""
    out = {"lead_forms": "unchecked", "conversions": "unchecked"}
    try:
        from app.services.analytics_sync import _linkedin_headers
        from app.services.linkedin_ads_report import _linkedin_org_token
    except ImportError:
        out["lead_forms"] = out["conversions"] = "unchecked — app modules unavailable"
        return out
    async with async_session_maker() as db:
        token = await _linkedin_org_token(db)
    if not token:
        out["lead_forms"] = out["conversions"] = "unchecked — no org token"
        return out
    h = _linkedin_headers(token)
    async with httpx.AsyncClient(timeout=30.0) as client:
        for key, url in (
            ("lead_forms", f"https://api.linkedin.com/rest/leadForms?q=owner&owner=(sponsoredAccount:{_AD_URN})&count=10"),
            ("conversions", f"https://api.linkedin.com/rest/conversions?q=account&accounts=List({_AD_URN})&count=10"),
        ):
            try:
                r = await client.get(url, headers=h)
                if r.status_code == 200:
                    n = len((r.json() or {}).get("elements") or [])
                    out[key] = f"reachable — {n} configured"
                elif r.status_code in (400, 403, 404):
                    out[key] = f"unreachable via r_ads token (HTTP {r.status_code}) — needs leadgen/conversions scope or CM check"
                else:
                    out[key] = f"probe failed (HTTP {r.status_code})"
            except Exception as exc:  # noqa: BLE001
                out[key] = f"probe failed: {type(exc).__name__}"
    return out


async def _espo_paid_leads() -> dict:
    try:
        from app.services import espocrm_client as espo
    except ImportError:
        return {"error": "espocrm_client unavailable"}
    if not espo.configured():
        return {"error": "ESPOCRM_* not configured"}

    async def _count(attr: str, value: str) -> int | None:
        try:
            rows = await espo.list_all(
                "Lead",
                select=["id"],
                where=[{"type": "equals", "attribute": attr, "value": value}],
            )
            return len(rows)
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    return {
        "leads_utm_medium_paid": await _count("utmMedium", "paid"),
        "leads_utm_source_linkedin": await _count("utmSource", "linkedin"),
    }


async def _paid_events() -> int | None:
    async with async_session_maker() as db:
        return await db.scalar(text("SELECT count(*) FROM web_analytics_events WHERE payload->>'utm_medium' = 'paid'"))


async def _insight_tag() -> str:
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(_SITE)
        if r.status_code != 200:
            return f"unchecked — {_SITE} HTTP {r.status_code}"
        return "installed" if _TAG_RE.search(r.text) else "absent — no Insight Tag on cloudless.gr"
    except Exception as exc:  # noqa: BLE001
        return f"unchecked — site fetch failed: {type(exc).__name__}"


async def _document_stock() -> int | None:
    async with async_session_maker() as db:
        return await db.scalar(text("SELECT count(*) FROM media_assets WHERE mime_type='application/pdf'"))


async def audit() -> dict:
    ads, formats, surfaces, espo, paid_events, tag, pdfs = await asyncio.gather(
        _campaigns_and_credit(),
        _format_diversity(),
        _leadgen_and_conversions({}),
        _espo_paid_leads(),
        _paid_events(),
        _insight_tag(),
        _document_stock(),
    )

    # Format diversification: distinct name-suffix hints across all campaigns
    # ever seen (API inventory + snapshot history).
    names = [c["name"] for c in ads.get("campaigns", [])] + [c["name"] for c in formats["campaigns_seen"]]
    formats_seen = {(n.rsplit("—", 1)[-1].strip().lower() if "—" in n else "unspecified") for n in names}

    gaps = []
    if ads.get("api_error"):
        gaps.append(f"ads API metrics failed (sidecar fallback): {ads['api_error'][:120]}")
    credit, worst = ads.get("credit_eur"), ads.get("worst_case_eur")
    if credit and worst is not None and worst > credit:
        gaps.append(f"CARD AT RISK — worst-case exposure €{worst:.2f} exceeds promo credit €{credit:.2f}")
    if isinstance(espo.get("leads_utm_medium_paid"), int) and espo["leads_utm_medium_paid"] == 0:
        gaps.append("zero paid leads in EspoCRM — Dark Horse's 'leads flow into nurture' loop has never produced a record")
    if paid_events == 0:
        gaps.append("zero utm_medium=paid site events — paid→site attribution never landed")
    if tag.startswith("absent"):
        gaps.append("no LinkedIn Insight Tag on cloudless.gr — Connectd's measurement foundation is missing; ad→lead conversion is invisible to LinkedIn")
    active = [c for c in ads.get("campaigns", []) if c["status"] in ("active", "paused")]
    if active and len(formats_seen) < 2:
        gaps.append(f"single-format ads ({', '.join(sorted(formats_seen))}) — Connectd's fix was format diversification (Document / Thought Leader / boosted)")

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "campaigns": ads.get("campaigns", []),
        "campaigns_in_history": formats["campaigns_seen"],
        "formats_seen": sorted(formats_seen),
        "credit": {
            "credit_eur": credit,
            "account_spend_eur": ads.get("account_spend_eur"),
            "worst_case_eur": ads.get("worst_case_eur"),
        },
        "capture_surfaces": surfaces,
        "ab_rotation": ("unmeasurable via API — creatives endpoints unreachable with r_ads token; verify ≥2 creatives per active campaign in Campaign Manager"),
        "funnel": {
            "espo_paid_leads": espo.get("leads_utm_medium_paid"),
            "espo_linkedin_leads": espo.get("leads_utm_source_linkedin"),
            "paid_site_events": paid_events,
            "insight_tag": tag,
        },
        "document_ads_pdf_stock": pdfs,
        "gaps": gaps,
    }


def render(r: dict) -> str:
    lines = [
        f"LinkedIn ad-funnel audit — {r['generated_at'][:16]}Z",
        "",
        "Campaigns (live API)",
        "-" * 60,
    ]
    for c in r["campaigns"]:
        lines.append(f"  {c['id']}  {c['status']:<9} €{c['budget_eur'] or 0:.0f}  {c['name']}")
    cr = r["credit"]
    lines += [
        "",
        f"credit €{cr['credit_eur']} · spend €{cr['account_spend_eur']} · worst-case €{cr['worst_case_eur']}",
        f"formats seen : {', '.join(r['formats_seen']) or '—'}",
        f"lead gen     : {r['capture_surfaces']['lead_forms']}",
        f"conversions  : {r['capture_surfaces']['conversions']}",
        f"A/B rotation : {r['ab_rotation'][:80]}…",
        "",
        "Funnel loop",
        "-" * 60,
        f"paid leads (EspoCRM)      : {r['funnel']['espo_paid_leads']}",
        f"linkedin-source leads     : {r['funnel']['espo_linkedin_leads']}",
        f"paid site events          : {r['funnel']['paid_site_events']}",
        f"insight tag               : {r['funnel']['insight_tag']}",
        f"document-ads PDF stock    : {r['document_ads_pdf_stock']}",
        "",
    ]
    if r["gaps"]:
        lines.append("gaps:")
        lines += [f"  • {g}" for g in r["gaps"]]
    else:
        lines.append("gaps: none")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = asyncio.run(audit())
    print(json.dumps(report, indent=2) if args.json else render(report))
    sys.exit(1 if report["gaps"] else 0)


if __name__ == "__main__":
    main()
