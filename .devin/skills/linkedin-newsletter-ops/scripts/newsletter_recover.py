#!/usr/bin/env python3
"""Recover a stuck LinkedIn newsletter issue — classify the failure, heal the
sidecar lane, requeue, dispatch, and watch to a verdict.

Built 2026-11-24 after issue #3 (black-friday-offers-2026) died `failed`:
the sidecar's redirect-loop circuit tripped on a LIVE session and the issue
burned all publish attempts on fast circuit-open rejects.

What it does, in order:

  1. Reads the issue row (status/attempts/last_error) via social-postgres.
  2. Probes the sidecar /health over the docker network (no navigation —
     never touches LinkedIn, so it can't deepen a real restriction).
  3. Circuit handling:
     - `until` already passed  -> POST /session/clear-rate-limit
     - reason ~ session_dead   -> the stored li_at is dead; only /login
       recovers it (opt-in via --relogin — LinkedIn pushes a 2FA
       approval to the owner's phone, warn before using).
     - otherwise               -> respect the cooldown; report the wait.
  4. Requeue: status=queued + scheduled_for=now (same as the API
     /issues/{id}/retry endpoint; done via SQL so it needs no auth).
  5. Dispatch: celery call dispatch_newsletter_issues on the publishing
     worker (the beat claims the row on its next sweep anyway).
  6. --watch N polls the row until published/failed/timeout.

Usage (host):
    python3 newsletter_recover.py black-friday-offers-2026
    python3 newsletter_recover.py ISSUE_SLUG --watch 900
    python3 newsletter_recover.py ISSUE_SLUG --relogin   # dead session
    python3 newsletter_recover.py ISSUE_SLUG --status    # probe only

Exit codes: 0 published/dispatched ok · 1 terminal failure · 2 blocked
(circuit open / needs relogin / not a queued-or-failed row).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

PSQL = [
    "docker", "exec", "social-postgres",
    "psql", "-U", "social_user", "-d", "social_automation", "-tA",
]
CELERY_DISPATCH = [
    "docker", "exec", "social-worker-publishing",
    "celery", "-A", "app.worker.celery_app:celery_app", "call",
    "app.worker.tasks.newsletter_issues.dispatch_newsletter_issues",
]
CIRCUIT_RE = re.compile(r"circuit open until (\S+)")


def sh(cmd: list[str], timeout: int = 60) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return (out.stdout + out.stderr).strip()


def sql(q: str) -> str:
    return sh(PSQL + ["-c", q])


def sidecar_url() -> str:
    ip = sh(["docker", "inspect", "linkedin-browser-sidecar",
             "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"])
    if not ip:
        sys.exit("sidecar container not found")
    return f"http://{ip}:9225"


def http(method: str, url: str, timeout: int = 20, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, method=method,
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as r:
            return {"ok": True, "status": r.status,
                    "json": json.loads(r.read() or b"{}")}
    except HTTPError as e:
        return {"ok": False, "status": e.code,
                "json": _try_json(e.read())}
    except (URLError, TimeoutError) as e:
        return {"ok": False, "status": 0, "json": {"error": str(e)}}


def _try_json(raw: bytes) -> dict:
    try:
        return json.loads(raw or b"{}")
    except Exception:
        return {"raw": raw[:300].decode(errors="replace")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("issue", help="issue uuid or slug")
    ap.add_argument("--status", action="store_true",
                    help="probe only — no requeue/dispatch")
    ap.add_argument("--watch", type=int, default=0, metavar="SECS",
                    help="poll issue until terminal state (default: report+exit)")
    ap.add_argument("--relogin", action="store_true",
                    help="dead session -> POST /login with LINKEDIN_* creds "
                         "(pushes a 2FA approval to the owner's phone)")
    ap.add_argument("--clear-circuit", action="store_true",
                    help="force-clear the circuit even before `until` — "
                         "only when you KNOW the session is alive; a real "
                         "restriction re-trips on the next navigation")
    args = ap.parse_args()

    # ── 1. issue state ────────────────────────────────────────────────────
    row = sql(
        "SELECT status, attempts, coalesce(last_error,''), "
        "       coalesce(article_url,''), scheduled_for "
        "FROM newsletter_issues "
        f"WHERE slug='{args.issue}' OR id::text='{args.issue}' LIMIT 1"
    )
    if not row:
        sys.exit(f"issue not found: {args.issue}")
    status, attempts, last_error, article_url, scheduled_for = row.split("|", 4)
    print(f"issue: {args.issue}  status={status}  attempts={attempts}")
    print(f"  scheduled_for={scheduled_for}  article_url={article_url or '-'}")
    if last_error:
        print(f"  last_error: {last_error[:220]}")
    if status == "published":
        print("already published — nothing to do")
        return 0

    # ── 2. sidecar health (no navigation) ─────────────────────────────────
    base = sidecar_url()
    health = http("GET", f"{base}/health")
    if not health["ok"]:
        print(f"sidecar /health failed: {health['json']} — fix the container first")
        return 2
    h = health["json"]
    print(f"sidecar: session={h.get('has_session')} "
          f"rate_limited={h.get('rate_limited')} reason={h.get('rate_limit_reason')} "
          f"until={h.get('rate_limit_until')}")

    # ── 3. circuit handling ───────────────────────────────────────────────
    blocked = False
    if h.get("rate_limited"):
        until = h.get("rate_limit_until") or 0
        reason = h.get("rate_limit_reason") or ""
        expired = until and (time.time() * 1000 > until)
        if expired or args.clear_circuit:
            r = http("POST", f"{base}/session/clear-rate-limit")
            print(f"circuit cleared ({'expired' if expired else 'forced'}): "
                  f"{r['status']} {str(r['json'])[:120]}")
            h["rate_limited"] = False
        elif "session_dead" in reason:
            print("circuit reason=session_dead — stored li_at is dead.")
            if not args.relogin:
                print("  rerun with --relogin to drive credential login "
                      "(owner must approve the 2FA push)")
                blocked = True
            else:
                print("attempting credential /login — check the phone for "
                      "the LinkedIn approval…")
                lr = http("POST", f"{base}/login", timeout=180, body={
                    "username": _env("LINKEDIN_EMAIL"),
                    "password": _env("LINKEDIN_PASSWORD"),
                })
                print(f"  /login -> {lr['status']} {str(lr['json'])[:200]}")
                if not lr["json"].get("logged_in"):
                    blocked = True
        else:
            wait_s = max(0, int(until / 1000 - time.time()))
            print(f"circuit open ({reason}) — reopens "
                  f"{datetime.fromtimestamp(until / 1000, tz=UTC).isoformat()} "
                  f"({wait_s // 60}min). The issue queue self-schedules past "
                  f"it on the next failed attempt.")
            blocked = True

    if blocked and not args.status:
        return 2

    if args.status:
        _diagnose(last_error)
        return 0 if not blocked else 2

    # ── 4. requeue ────────────────────────────────────────────────────────
    if status not in ("queued", "failed", "cancelled", "publishing"):
        print(f"status={status} — not requeueable from here")
        return 2
    out = sql(
        "UPDATE newsletter_issues SET status='queued', scheduled_for=now(), "
        f"last_error='' WHERE slug='{args.issue}' OR id::text='{args.issue}' "
        "RETURNING status"
    )
    print(f"requeued: {out}")

    # ── 5. dispatch ───────────────────────────────────────────────────────
    d = sh(CELERY_DISPATCH, timeout=120)
    print(f"dispatch fired: {d.splitlines()[-1] if d else '?'}")

    # ── 6. optional watch ─────────────────────────────────────────────────
    if not args.watch:
        print("dispatched — re-run with --status in ~10min, or --watch")
        return 0
    deadline = time.time() + args.watch
    while time.time() < deadline:
        time.sleep(30)
        s = sql("SELECT status FROM newsletter_issues WHERE slug='{0}' "
                "OR id::text='{0}'".format(args.issue))
        print(f"  {datetime.now(UTC):%H:%M}Z status={s}", flush=True)
        if s in ("published", "failed", "cancelled"):
            if s == "published":
                print(sql("SELECT coalesce(article_url,'') FROM newsletter_issues "
                          f"WHERE slug='{args.issue}' OR id::text='{args.issue}'"))
                return 0
            print(sql("SELECT left(coalesce(last_error,''),300) FROM newsletter_issues "
                      f"WHERE slug='{args.issue}' OR id::text='{args.issue}'"))
            return 1
    print("watch timed out — still in-flight")
    return 0


def _env(key: str) -> str:
    for line in sh(["grep", f"^{key}=", "/home/tbaltzakis/cu130-slim/.env"]).splitlines():
        return line.split("=", 1)[1].strip()
    return ""


def _diagnose(last_error: str) -> None:
    if "circuit open until" in last_error:
        print("dx: sidecar circuit was open — issue self-reschedules past `until` "
              "(record_publish_failure) since the 2026-11-24 fix")
    elif "dismiss-confirmation" in last_error or "intercepts pointer events" in last_error:
        print("dx: media-editor confirm modal blocked the publish click — "
              "fixed by dismissMediaEditorBlockers in the sidecar (>=2026-11-24)")
    elif "Browser busy" in last_error:
        print("dx: shared browser-novnc bridge was busy — transient, retry")
    elif "redirect loop" in last_error.lower():
        print("dx: transient redirect loop — retried automatically; "
              "check `docker exec linkedin-browser-sidecar cat /data/rate-limit.json`")


if __name__ == "__main__":
    sys.exit(main())
