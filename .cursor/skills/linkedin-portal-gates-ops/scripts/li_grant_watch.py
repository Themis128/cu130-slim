#!/usr/bin/env python3
"""li_grant_watch.py — detect LinkedIn product-grant transitions and act on them.

Ground truth is the API, not portal labels: probes the gated endpoints with
the stored tokens (inside social-api) and diffs the verdicts against
~/.local/state/li-grant-watch/state.json. On a transition to GRANTED it
TG-alerts #ops and dispatches the matching Celery sync task so the lane
starts producing data without waiting for the next beat slot.

    li_grant_watch.py            # probe + diff + alert + dispatch
    li_grant_watch.py --json     # machine-readable report, no side effects
    li_grant_watch.py --status   # show last recorded state

Probes (run inside social-api via docker exec):
  dmp_segments      GET /rest/dmpSegments?q=account&account=<ad account>
                    -> Matched Audiences / rw_dmp_segments
  member_snapshot   GET /rest/memberSnapshotData
                    -> Member DP / DMA portability (member token)
  member_changelog  GET /rest/memberChangeLogs
                    -> same product, changelog surface
  token_scopes      linkedin_tool.py token-inspect per account
                    -> OAuth scope set (needs re-consent to grow)

Portal product states (member_dp_probe --json) are recorded for context but
the API verdict is authoritative — a portal "Review in progress" label can
lag the actual grant.

Self-gating: portal probe is skipped when CDP is unreachable (cooldown) —
the API probes still run since they don't touch the browser session.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path("/home/tbaltzakis/cu130-slim")
STATE_DIR = Path.home() / ".local/state/li-grant-watch"
STATE_FILE = STATE_DIR / "state.json"
TG = REPO / ".devin/skills/telegram-alert-ops/scripts/tg_alert.py"
PROBE_DIR = Path(__file__).resolve().parent
ORG_UUID = "9c4451bb-e820-489f-8676-76ddbc788ffe"
MEMBER_UUID = "8f406a9d-7bb7-4cc7-82e2-af9970528f4a"
AD_ACCOUNT_URN = "urn:li:sponsoredAccount:512642510"

# In-container probe: returns {probe_name: {status, denied, granted, detail}}
CONTAINER_PROBE = r'''
import asyncio, json
import httpx
from urllib.parse import quote
from sqlalchemy import select
from app.core.security import decrypt_token
from app.models.social_account import SocialAccount
from app.worker._async import task_session

REST = "https://api.linkedin.com/rest"
HDRS = {"Linkedin-Version": "202608", "X-Restli-Protocol-Version": "2.0.0"}
AD_URN = "urn:li:sponsoredAccount:512642510"

DENY_MARKERS = ("ACCESS_DENIED", "Not enough permissions",
                "INSUFFICIENT_PERMISSIONS", "Product not authorized",
                "UNAUTHORIZED", "ACCESS_DENIED")

async def token_for(db, account_type):
    acc = (await db.execute(select(SocialAccount).where(
        SocialAccount.platform == "linkedin",
        SocialAccount.account_type == account_type))).scalars().first()
    if not acc or not acc.access_token_enc:
        return None
    return decrypt_token(bytes(acc.access_token_enc))

async def probe(client, token, method, url, extra_headers=None, **kw):
    if not token:
        return {"granted": False, "detail": "no-token"}
    hdrs = {**HDRS, **(extra_headers or {}),
            "Authorization": f"Bearer {token}"}
    try:
        r = await client.request(method, url, headers=hdrs,
                                 timeout=25, **kw)
        body = r.text[:300]
        denied = (r.status_code in (401, 403)
                  or any(m in body for m in DENY_MARKERS))
        return {"status": r.status_code, "denied": denied,
                "granted": r.status_code in (200, 201, 204),
                "detail": body[:160]}
    except Exception as e:
        return {"granted": False, "detail": f"error:{e}"[:160]}

async def main():
    out = {}
    async with task_session() as db:
        org = await token_for(db, "organization")
        member = await token_for(db, "person")
    async with httpx.AsyncClient() as c:
        out["dmp_segments"] = await probe(
            c, org, "GET",
            f"{REST}/dmpSegments?q=account&account={quote(AD_URN, safe='')}")
        # Member DP endpoints are pinned to Linkedin-Version 202312 and need
        # q=criteria — a bare GET 404s on version/params, not on authz.
        out["member_snapshot"] = await probe(
            c, member, "GET", f"{REST}/memberSnapshotData",
            params={"q": "criteria", "start": 0, "count": 1},
            extra_headers={"Linkedin-Version": "202312"})
        out["member_changelog"] = await probe(
            c, member, "GET", f"{REST}/memberChangeLogs",
            params={"q": "criteria", "start": 0, "count": 1},
            extra_headers={"Linkedin-Version": "202312"})
    print(json.dumps(out))

asyncio.run(main())
'''


def sh(cmd, timeout=120, **kw):
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, **kw)


def dc(script: str, timeout=120) -> str:
    r = sh(["docker", "exec", "-i", "social-api", "python", "-"],
           timeout=timeout, input=script)
    if r.returncode != 0:
        return ""
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""


def token_scopes() -> dict:
    """Parse granted scopes per LinkedIn account via linkedin_tool."""
    out = {}
    for label, uuid in (("org", ORG_UUID), ("member", MEMBER_UUID)):
        r = sh(["docker", "exec", "social-api", "python",
                "/app/scripts/linkedin_tool.py", "token-inspect",
                "--account", uuid], timeout=60)
        text = r.stdout + r.stderr
        scopes = set()
        for line in text.splitlines():
            if "scope" in line.lower():
                # scopes may be comma/space separated on the line
                for tok in line.replace(",", " ").split():
                    if tok.startswith(("r_", "w_", "rw_")):
                        scopes.add(tok)
        out[label] = {"scopes": sorted(scopes),
                      "ok": "active" in text.lower() or bool(scopes)}
    return out


def portal_state() -> dict:
    """Best-effort portal product-state probe (skips cleanly on cooldown)."""
    script = PROBE_DIR / "member_dp_probe.py"
    if not script.exists():
        return {"status": "missing-probe"}
    try:
        r = sh([sys.executable, str(script), "--json"], timeout=90)
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as e:
        return {"status": "probe-error", "detail": str(e)[:120]}


def dispatch_task(task: str) -> str:
    code = ("from app.worker.celery_app import celery_app\n"
            f"r = celery_app.send_task('{task}')\nprint(r.id)\n")
    return dc(code, timeout=60)


def tg_alert(text: str):
    sh([sys.executable, str(TG), "ops", text], timeout=30)


def collect() -> dict:
    state = {"ts": int(time.time())}
    raw = dc(CONTAINER_PROBE, timeout=90)
    try:
        state["api"] = json.loads(raw) if raw else {"error": "no-output"}
    except json.JSONDecodeError:
        state["api"] = {"error": "unparseable", "raw": raw[:200]}
    state["scopes"] = token_scopes()
    state["portal"] = portal_state()
    return state


def granted_map(state: dict) -> dict:
    """Flatten to {lane: granted?} for diffing."""
    api = state.get("api") or {}
    scopes = state.get("scopes") or {}
    org_scopes = set((scopes.get("org") or {}).get("scopes") or [])
    member_scopes = set((scopes.get("member") or {}).get("scopes") or [])
    return {
        "matched_audiences": bool(
            (api.get("dmp_segments") or {}).get("granted")
            or "rw_dmp_segments" in org_scopes),
        # memberSnapshotData is the canonical Member DP probe; changelog is
        # recorded for context but 404s independently of the grant.
        "member_dp": bool(
            (api.get("member_snapshot") or {}).get("granted")),
        "member_analytics_scopes": {
            "postAnalytics": "r_member_postAnalytics" in member_scopes
            or "r_member_postAnalytics" in org_scopes,
            "profileAnalytics": "r_member_profileAnalytics" in member_scopes
            or "r_member_profileAnalytics" in org_scopes,
        },
    }


def diff_alert(old: dict, new: dict) -> list[str]:
    alerts = []
    o = granted_map(old) if old else {}
    n = granted_map(new)
    for lane in ("matched_audiences", "member_dp"):
        if n.get(lane) and not o.get(lane):
            alerts.append(f"LinkedIn grant LIVE: {lane} — API probes pass, "
                          f"dispatching sync")
    for scope, was in (n.get("member_analytics_scopes") or {}).items():
        if was and not (o.get("member_analytics_scopes") or {}).get(scope):
            alerts.append(f"LinkedIn scope live: {scope}")
    # scope-present-but-api-denied = granted product, token needs re-consent
    api = new.get("api") or {}
    org_scopes = set((new.get("scopes") or {}).get("org", {}).get("scopes") or [])
    if "rw_dmp_segments" in org_scopes and \
            (api.get("dmp_segments") or {}).get("denied"):
        alerts.append("rw_dmp_segments in token but DMP API still denies — "
                      "product grant pending or token stale; re-auth may help")
    return alerts


def auto_dispatch(new: dict):
    g = granted_map(new)
    if g.get("matched_audiences"):
        tid = dispatch_task(
            "app.worker.tasks.linkedin_dmp_sync.sync_linkedin_dmp_audiences")
        print(f"  dispatched dmp sync: {tid}")
    if g.get("member_dp"):
        tid = dispatch_task(
            "app.worker.tasks.linkedin_portability_sync.sync_linkedin_portability")
        print(f"  dispatched portability sync: {tid}")


def main():
    args = set(sys.argv[1:])
    if "--status" in args:
        if STATE_FILE.exists():
            print(json.dumps(json.loads(STATE_FILE.read_text()), indent=1)[:3000])
        else:
            print("no state yet")
        return 0
    state = collect()
    g = granted_map(state)
    report = {"grants": g, "api": state.get("api"),
              "portal": (state.get("portal") or {}).get("status")}
    if "--json" in args:
        print(json.dumps(report, indent=1))
        return 0

    old = {}
    if STATE_FILE.exists():
        try:
            old = json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            old = {}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))

    alerts = diff_alert(old, state)
    for a in alerts:
        print("ALERT:", a)
        tg_alert(a)
    if alerts and any("grant LIVE" in a for a in alerts):
        auto_dispatch(state)
    if not alerts:
        print("no transitions — state recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
