#!/usr/bin/env python3
"""meta_appeal_watch.py — restricted-asset appeal watchdog.

Runs INSIDE the social-api container (needs app.core.security + DB env).
Enumerates every BM's owned_ad_accounts, flags restricted statuses, and
files official Appeals-API review requests (ad_review_requests /
bm_review_requests) with a per-asset cooldown so each asset earns at most
one appeal per APPEAL_COOLDOWN_DAYS. Every filing appends to
/app/uploads/meta-appeals-log.jsonl (via meta_appeals._log) and prints a
one-line JSON verdict the cron wrapper can alert on.

Usage: python scripts/meta_appeal_watch.py [--dry-run] [--force]
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meta_appeals as ma  # noqa: E402

STATE = Path("/app/uploads/meta-appeal-watch.json")
COOLDOWN_DAYS = int(os.environ.get("META_APPEAL_COOLDOWN_DAYS", "7"))
BM_COOLDOWN_DAYS = int(os.environ.get("META_BM_APPEAL_COOLDOWN_DAYS", "30"))
# ad account_status codes meaning restricted/closed (Meta docs:
# 1 active, 2 disabled, 3 unsettled, 7 closed, 9 pending settlement,
# 100 pending closure, 101 closed, 201 grace, 202 pending closure)
BAD_AD_STATUSES = {2, 7, 100, 101, 202}


def _load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save_state(st):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1))


def _due(st, key, days):
    ts = st.get(key, 0)
    return (time.time() - ts) > days * 86400


def _mark(st, key):
    st[key] = time.time()


def _collect(token):
    """Return (bad_ad_ids, bm_ids) across all visible BMs."""
    bms = ma._g("me/businesses", token, params={"fields": "id,name"}).get("data", [])
    bad_ads, bm_ids = [], []
    for bm in bms:
        bm_ids.append(bm["id"])
        for field in ("owned_ad_accounts", "client_ad_accounts"):
            accts = ma._g(f"{bm['id']}/{field}", token,
                          params={"fields": "id,account_id,name,account_status"}
                          ).get("data", [])
            for a in accts:
                try:
                    status = int(a.get("account_status") or 0)
                except (TypeError, ValueError):
                    status = 0
                if status in BAD_AD_STATUSES:
                    bad_ads.append({"id": a.get("account_id") or a.get("id"),
                                    "name": a.get("name"), "status": status,
                                    "bm": bm["id"]})
    # dedupe by account id
    seen, out = set(), []
    for a in bad_ads:
        if a["id"] not in seen:
            seen.add(a["id"])
            out.append(a)
    return out, bm_ids


def main():
    dry = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    token = asyncio.run(ma._fb_token())
    bad_ads, bm_ids = _collect(token)  # ma._g is sync urllib — no loop needed
    st = _load_state()
    filed = []

    ad_ids = [a["id"] for a in bad_ads]
    ad_key = "ad_accounts"
    if ad_ids and (force or _due(st, ad_key, COOLDOWN_DAYS)):
        if dry:
            print(json.dumps({"would_appeal_accounts": ad_ids}))
        else:
            rc = ma._appeal(token, "accounts", ad_ids)
            if rc == 0:
                _mark(st, ad_key)
                filed.append({"kind": "ad_review_requests", "ids": ad_ids})

    # Portfolio-level appeal only when the BM shows restricted ads —
    # proxy for the portfolio-mutation issues escalated 2026-11-22.
    if bad_ads and bm_ids and (force or _due(st, "bms", BM_COOLDOWN_DAYS)):
        if dry:
            print(json.dumps({"would_appeal_bms": bm_ids}))
        else:
            rc = ma._appeal(token, "bm", bm_ids)
            if rc == 0:
                _mark(st, "bms")
                filed.append({"kind": "bm_review_requests", "ids": bm_ids})

    if not dry:
        _save_state(st)
    verdict = {"restricted_ads": bad_ads, "filed": filed,
               "cooldowns": {k: round((time.time() - v) / 86400, 1)
                             for k, v in st.items()}}
    print("META_APPEAL_WATCH " + json.dumps(verdict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
