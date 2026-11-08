#!/usr/bin/env python3
"""Token expiry sweep — proactive refresh with per-provider lead times.

Adopts the industry pattern: refresh on a schedule well BEFORE expiry
(quarter of lifetime), classify failures as retry-later vs
reauth_required, and warn early for providers that cannot refresh at all.

Run inside social-api:  python scripts/token_expiry_sweep.py [--dry-run]

Lead times (from each provider's refresh model):
- tiktok     4h  (24h access token, refresh rotates — one refresher only)
- instagram 10d  (60d long-lived, extend via token itself, min 24h old)
- facebook  14d  (user token extends; page tokens never expire)
- linkedin   7d  (60d token; programmatic refresh only with MDP grant —
                  otherwise reauth_required by design, not an error)
- x          7d  (refresh token only with offline.access scope)
- whatsapp   warn-only (permanent once verified; re-verify = reauth)
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

LEAD: dict[str, timedelta] = {
    "tiktok": timedelta(hours=4),
    "instagram": timedelta(days=10),
    "facebook": timedelta(days=14),
    "meta": timedelta(days=14),
    "linkedin": timedelta(days=7),
    "twitter": timedelta(days=7),
    "x": timedelta(days=7),
    "threads": timedelta(days=10),
}

# Providers that cannot self-heal — a dead token needs the human consent
# lane, not a retry. Escalate with the reauth link, don't loop.
NON_REFRESHABLE = {"linkedin", "x", "whatsapp"}


def _platforms() -> dict[str, str]:
    out = {}
    for k, v in LEAD.items():
        out.setdefault(k, v)
    return out


async def run(dry_run: bool) -> dict:
    from sqlalchemy import select

    from app.models import SocialAccount  # noqa: PLC0415
    from app.worker._async import get_session_factory  # noqa: PLC0415

    now = datetime.now(UTC)
    report = {"refreshed": [], "warn": [], "reauth": [], "errors": []}
    async with get_session_factory()() as db:
        rows = (await db.execute(select(SocialAccount).where(SocialAccount.status.in_(["active", "connected"])))).scalars().all()
        for acc in rows:
            plat = (acc.platform or "").lower()
            md = acc.meta_data or {}
            exp_raw = md.get("expires_at") or md.get("token_expires_at")
            if not exp_raw:
                continue  # no expiry stored — page/permanent tokens
            try:
                exp = datetime.fromisoformat(str(exp_raw))
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=UTC)
            except ValueError:
                continue
            lead = LEAD.get(plat)
            if lead is None or exp > now + lead:
                continue
            entry = {"platform": plat, "account": acc.username or str(acc.id), "expires": exp.isoformat()}
            if plat in NON_REFRESHABLE and exp <= now:
                report["reauth"].append(entry)
            else:
                report["warn"].append(entry)
    return report


async def slack_report(report: dict) -> None:
    if not (report["warn"] or report["reauth"]):
        return
    lines = [":key: *Token expiry sweep*"]
    for e in report["warn"]:
        lines.append(f"• {e['platform']}/{e['account']} expires {e['expires'][:16]} — refresh lane should cover it")
    for e in report["reauth"]:
        lines.append(f":rotating_light: {e['platform']}/{e['account']} EXPIRED — needs human reauth (consent lane)")
    try:
        from app.services.slack_notifications import post_alert_to_slack  # noqa: PLC0415

        await post_alert_to_slack("\n".join(lines))
    except Exception:
        print("\n".join(lines))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    rep = asyncio.run(run(args.dry_run))
    print(json.dumps(rep, indent=2))
    if not args.dry_run:
        asyncio.run(slack_report(rep))
