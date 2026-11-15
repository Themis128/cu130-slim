#!/usr/bin/env python3
"""account_truth.py — DB status vs live platform probe, per account.

``social_accounts.status`` drifts: heal lanes flip rows to ``active``
without validating the token (observed 2026-11-15: Instagram/Threads/
WhatsApp showed ``active`` while ``debug_token`` returned 190 "Bad
signature"). ``account_status_reconcile`` probes live every 6h but only
writes the DB — this script REPORTS the diff so drift is visible before
publishes start failing.

Run inside social-api::

    python scripts/account_truth.py              # table, exit 1 on drift
    python scripts/account_truth.py --json       # machine-readable
    python scripts/account_truth.py --platform meta

Exit codes: 0 = every probed account matches its DB status (or is
UNKNOWN/inconclusive), 1 = at least one FALSE-ACTIVE or UNDERSTATED drift.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime

GOOD = {"active", "connected"}
BAD = {"expired", "revoked", "error"}


async def _collect(rows, platform_filter):
    from app.core.security import decrypt_token  # noqa: PLC0415
    from app.worker.tasks.account_status_reconcile import _probe  # noqa: PLC0415

    now = datetime.now(UTC)
    out = []

    async def one(acc):
        md = acc.meta_data or {}
        entry = {
            "id": str(acc.id),
            "platform": acc.platform,
            "account": acc.username or acc.display_name or acc.account_id,
            "db_status": acc.status,
            "token_expires_at": acc.token_expires_at.isoformat() if acc.token_expires_at else None,
            "last_verified_at": md.get("last_verified_at"),
        }
        # Mirror reconcile: a past token_expires_at means expired without
        # a network probe.
        exp = acc.token_expires_at
        if exp and exp.replace(tzinfo=UTC) < now:
            entry["probe"] = "expired-by-clock"
            entry["verdict"] = _verdict(entry["db_status"], "expired")
            return entry
        token = ""
        if acc.access_token_enc:
            try:
                token = decrypt_token(acc.access_token_enc)
            except Exception:
                token = ""
        if not token:
            entry["probe"] = "no-token"
            entry["verdict"] = "UNKNOWN"
            return entry
        probe = await _probe(acc.platform, acc.account_id, token, md)
        entry["probe"] = probe
        entry["verdict"] = _verdict(entry["db_status"], probe)
        return entry

    tasks = [one(a) for a in rows if not platform_filter or a.platform == platform_filter]
    for coro in asyncio.as_completed(tasks):
        out.append(await coro)
    out.sort(key=lambda e: (e["platform"], e["account"]))
    return out


def _verdict(db_status: str, probe: str) -> str:
    if probe in ("inconclusive", "unprobed", "no-token"):
        return "UNKNOWN"
    if probe == "expired" or probe == "expired-by-clock":
        # Clock-expired agrees with an already-bad DB row; an active row
        # with a dead clock is drift reconcile will fix on its next tick.
        return "MATCH" if db_status in BAD else "UNKNOWN"
    if db_status in GOOD and probe == "active":
        return "MATCH"
    if db_status in BAD and probe == "revoked":
        return "MATCH"
    if db_status in GOOD and probe == "revoked":
        return "FALSE-ACTIVE"
    if db_status in BAD and probe == "active":
        return "UNDERSTATED"
    return "UNKNOWN"


async def run(platform: str | None) -> dict:
    from sqlalchemy import select

    from app.models import SocialAccount  # noqa: PLC0415
    from app.worker._async import get_session_factory  # noqa: PLC0415

    async with get_session_factory()() as db:
        rows = (await db.execute(select(SocialAccount))).scalars().all()
    accounts = await _collect(rows, platform)
    counts: dict[str, int] = {}
    for a in accounts:
        counts[a["verdict"]] = counts.get(a["verdict"], 0) + 1
    drift = [a for a in accounts if a["verdict"] in ("FALSE-ACTIVE", "UNDERSTATED")]
    return {"accounts": accounts, "counts": counts, "drift": drift}


def _print_table(report: dict) -> None:
    print(f"{'platform':<11} {'account':<38} {'db':<9} {'probe':<17} verdict")
    print("-" * 95)
    for a in report["accounts"]:
        print(f"{a['platform']:<11} {str(a['account'])[:37]:<38} {str(a['db_status']):<9} {str(a['probe']):<17} {a['verdict']}")
    print("-" * 95)
    print("counts:", json.dumps(report["counts"], sort_keys=True))
    for a in report["drift"]:
        print(f"DRIFT {a['platform']}/{a['account']}: db={a['db_status']} live={a['probe']} — heal lane wrote status without token validation")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--platform", help="probe one platform only")
    args = ap.parse_args()
    rep = asyncio.run(run(args.platform))
    if args.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        _print_table(rep)
    raise SystemExit(1 if rep["drift"] else 0)
