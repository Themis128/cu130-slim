#!/usr/bin/env python3
"""Lead-generation readiness audit — every check reads live data.

Adoption of Meta's "10 strategies for social media lead generation"
(facebook.com/business/resources/social-media-lead-generation). The guide's
actionable, machine-checkable items mapped onto SocialAuto surfaces:

  strategy 2  profile → lead capture   → per-account DM/bot lane enabled?
  strategy 3  strong CTAs              → tagged inventory + real attributed
                                          site events (utm.py tags at send)
  strategy 7  landing-page readiness   → live HTTP probes on capture surfaces
  strategy 1  lead magnet exists       → cloudless.gr/links checklist live?
  measurement lead volume/CPL          → 30d EspoCRM leads by source

Deliberately NOT checked (no code path exists / paid-only):
  retargeting + Advantage+ (strategies 4/10) — paid Meta ads; out of scope
  under the no-spend rule. Instant lead forms (8/9) — no Meta lead ads run.

Exit 1 when a capture surface is down or an active account has no DM
lead-capture lane — those are structural gaps, not tuning.

    python scripts/leadgen_audit.py          # human report
    python scripts/leadgen_audit.py --json   # machine output
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_maker  # noqa: E402

# Capture surfaces every funnel CTA must ultimately land on (canonical rule:
# /contact → EspoCRM lead, /pricing → Polar, /links → lead magnet).
CAPTURE_SURFACES = {
    "cloudless.gr/contact": "https://cloudless.gr/contact",
    "social.cloudless.gr/pricing": "https://social.cloudless.gr/pricing",
    "cloudless.gr/links (lead magnet)": "https://cloudless.gr/links",
}

# meta_data keys that mean "this account answers DMs automatically" — Meta's
# 'direct messaging' lead signal. Key name varies per platform.
DM_LANE_KEYS = (
    "messenger_auto_reply",
    "personal_messenger_auto_reply",
    "instagram_auto_reply",
    "threads_auto_reply",
    "twitter_auto_reply",
    "linkedin_auto_reply",
    "tiktok_auto_reply",
    "whatsapp_auto_reply",
    "telegram_auto_reply",
)

# meta_data keys that hold a cached profile link/bio — where present we can
# verify the profile actually points at a funnel surface.
PROFILE_LINK_KEYS = ("bio_description", "profile_deep_link", "website")
FUNNEL_DOMAINS = ("cloudless.gr", "social.cloudless.gr")


def _flag_enabled(val) -> bool:
    """meta_data flag values may be dict, JSON string, or repr string."""
    if isinstance(val, dict):
        return bool(val.get("enabled", True))
    if isinstance(val, str):
        v = val.lower()
        return "enabled': true" in v or '"enabled": true' in v or "'enabled': True".lower() in v
    return bool(val)


async def _accounts() -> list[dict]:
    async with async_session_maker() as db:
        rows = (await db.execute(text("SELECT platform, username, meta_data FROM social_accounts WHERE status = 'active' ORDER BY platform"))).all()
        return [{"platform": p, "username": u, "meta": m or {}} for p, u, m in rows]


async def _attribution() -> dict:
    """The post→site→CRM loop, measured where it actually happens.

    Stored post text is NOT tagged — app/services/utm.py tag_owned_links()
    rewrites owned-domain URLs at publish time, so the delivered copy carries
    utm_source=platform while the DB row keeps the original. The auditable
    facts are therefore:
      tagged_inventory  — published posts carrying a cloudless.gr link (the
                          publish path tags all of them at send)
      attributed_events — web_analytics_events with a utm_source payload —
                          real post→site arrivals (AttributionCapture).
    A gap means tagged inventory exists but nothing attributed in 30d.
    """
    async with async_session_maker() as db:
        inventory = (
            await db.execute(
                text(
                    "SELECT count(*) FROM posts WHERE status = 'published' "
                    "AND published_at > now() - interval '30 days' "
                    "AND (content_text ILIKE '%cloudless.gr%' "
                    "     OR link_url ILIKE '%cloudless.gr%')"
                )
            )
        ).scalar() or 0
        events = (
            await db.execute(text("SELECT count(*) FROM web_analytics_events WHERE occurred_at > now() - interval '30 days' AND payload ? 'utm_source'"))
        ).scalar() or 0
        by_source = (
            await db.execute(
                text(
                    "SELECT payload->>'utm_source' AS src, count(*) "
                    "FROM web_analytics_events "
                    "WHERE occurred_at > now() - interval '30 days' "
                    "AND payload ? 'utm_source' GROUP BY 1 ORDER BY 2 DESC"
                )
            )
        ).all()
    return {
        "tagged_inventory_30d": inventory,
        "attributed_events_30d": events,
        "by_source": {s or "(none)": n for s, n in by_source},
    }


async def _probe_surfaces() -> dict:
    out = {}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
        for label, url in CAPTURE_SURFACES.items():
            try:
                r = await c.get(url)
                out[label] = {"status": r.status_code, "ok": r.status_code < 400}
            except Exception as exc:  # noqa: BLE001 — surface the gap
                out[label] = {"status": None, "ok": False, "error": str(exc)[:80]}
    return out


async def _leads_30d() -> dict:
    """Lead volume by source over 30d — Meta's core 'lead volume' metric,
    from EspoCRM not platform vanity numbers."""
    try:
        from app.services import espocrm_client as espo

        leads = await espo.list_all("Lead", select=["id", "source", "createdAt"])
    except Exception as exc:  # noqa: BLE001
        return {"error": f"espo unavailable: {exc}"}
    cutoff = datetime.now(UTC).timestamp() - 30 * 86400
    by_source: dict[str, int] = {}
    for lead in leads:
        raw = lead.get("createdAt") or ""
        try:
            ts = datetime.fromisoformat(raw.replace(" ", "T")).timestamp()
        except ValueError:
            continue
        if ts >= cutoff:
            s = lead.get("source") or "(none)"
            by_source[s] = by_source.get(s, 0) + 1
    return {"total": sum(by_source.values()), "by_source": by_source}


def _check_accounts(accounts: list[dict]) -> list[dict]:
    rows = []
    for a in accounts:
        meta = a["meta"]
        lanes = [k for k in DM_LANE_KEYS if k in meta and _flag_enabled(meta[k])]
        profile = {k: meta[k] for k in PROFILE_LINK_KEYS if meta.get(k)}
        funnel_linked = any(d in str(v) for v in profile.values() for d in FUNNEL_DOMAINS)
        rows.append(
            {
                "platform": a["platform"],
                "username": a["username"],
                "dm_lane": bool(lanes),
                "dm_keys": lanes,
                "profile_link_cached": bool(profile),
                "profile_funnel_linked": funnel_linked if profile else None,
            }
        )
    return rows


async def audit() -> dict:
    accounts, surfaces, cta, leads = await asyncio.gather(_accounts(), _probe_surfaces(), _attribution(), _leads_30d())
    rows = _check_accounts(accounts)
    gaps = []
    for label, s in surfaces.items():
        if not s["ok"]:
            gaps.append(f"capture surface down: {label} ({s['status'] or s.get('error')})")
    for r in rows:
        if not r["dm_lane"]:
            gaps.append(f"no DM lead lane: {r['platform']}/{r['username']}")
    # tagged inventory exists but nothing attributed in 30d → the
    # post→site→CRM loop is silent (dead link, dead site, or zero clicks)
    if cta["tagged_inventory_30d"] and not cta["attributed_events_30d"]:
        gaps.append(f"attribution loop silent: {cta['tagged_inventory_30d']} UTM-tagged posts but zero attributed site events in 30d")
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "capture_surfaces": surfaces,
        "accounts": rows,
        "attribution": cta,
        "leads_30d": leads,
        "gaps": gaps,
    }


def render(r: dict) -> str:
    lines = [f"Lead-gen readiness — {r['generated_at'][:16]}Z", "", "capture surfaces:"]
    for label, s in r["capture_surfaces"].items():
        mark = "ok" if s["ok"] else "DOWN"
        lines.append(f"  [{mark:>4}] {label} — {s['status'] or s.get('error')}")
    lines += ["", "DM lead lanes (auto-reply enabled):"]
    for a in r["accounts"]:
        mark = "ok" if a["dm_lane"] else "MISSING"
        link = "funnel-linked" if a["profile_funnel_linked"] else "no funnel link" if a["profile_funnel_linked"] is False else "bio not cached"
        lines.append(f"  [{mark:>7}] {a['platform']}/{a['username']} — {link}")
    c = r["attribution"]
    src = ", ".join(f"{k}={v}" for k, v in c["by_source"].items()) or "—"
    lines += [
        "",
        f"attribution (30d): {c['tagged_inventory_30d']} posts carry auto-tagged links → {c['attributed_events_30d']} attributed site events ({src})",
    ]
    leads = r["leads_30d"]
    if "error" in leads:
        lines.append(f"leads 30d: {leads['error']}")
    else:
        src = ", ".join(f"{k}={v}" for k, v in sorted(leads["by_source"].items(), key=lambda kv: -kv[1]))
        lines.append(f"leads 30d: {leads['total']} ({src})")
    lines.append("")
    lines.append("gaps: " + ("; ".join(r["gaps"]) if r["gaps"] else "none"))
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
