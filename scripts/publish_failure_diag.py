#!/usr/bin/env python3
"""Diagnose a failed SocialAuto publish end-to-end.

Given a post_id or publish_queue id, prints the full failure picture:
queue + target DB state, the worker-log exception signature, sidecar
health, and whether the running backend image carries the transport
classification fix (PR #2540). Read-only — repairs stay with
scripts/publish_queue.py (reset/unstick).

Usage:
    python3 scripts/publish_failure_diag.py <post_id|queue_id>
    python3 scripts/publish_failure_diag.py --latest-failed [--platform linkedin]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# docker exec, not compose exec: compose parses .env and a malformed
# *_COOKIES_JSON line breaks every call from the repo root.
PSQL = [
    "docker", "exec", "-i", "social-postgres",
    "psql", "-U", "social_user", "-d", "social_automation", "-t", "-A", "-F", "\t",
]
WORKER = "social-worker-publishing"
SIDECAR_PORT = {"linkedin": 9225}
# Marker added by the transport-classification fix (publishing.py).
FIX_MARKER = "possible partial post"


def sh(cmd: list[str], cwd: Path = ROOT, timeout: int = 60) -> str:
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return res.stdout if res.returncode == 0 else (res.stderr or res.stdout)


def sql(query: str) -> list[list[str]]:
    out = sh([*PSQL, "-c", query])
    return [r.split("\t") for r in out.strip().splitlines() if r.strip()]


def jdump(obj) -> str:
    return json.dumps(obj, indent=2, default=str)


def resolve_ids(arg: str) -> tuple[str, str | None]:
    """Return (queue_id, post_id)."""
    rows = sql(
        f"SELECT id, post_id FROM publish_queue WHERE id = '{arg}' "
        f"OR post_id = '{arg}' LIMIT 5"
    )
    if not rows:
        sys.exit(f"no publish_queue row matches {arg}")
    qid, pid = rows[0][0], rows[0][1]
    return qid, pid


def latest_failed(platform: str | None) -> tuple[str, str]:
    plat = f"AND sa.platform = '{platform}'" if platform else ""
    rows = sql(
        f"""SELECT q.id, q.post_id FROM publish_queue q
            JOIN post_targets t ON t.post_id = q.post_id
            JOIN social_accounts sa ON sa.id = t.social_account_id
            WHERE q.status = 'failed' {plat}
            ORDER BY q.scheduled_at DESC NULLS LAST LIMIT 1"""
    )
    if not rows:
        sys.exit("no failed queue rows" + (f" for {platform}" if platform else ""))
    return rows[0][0], rows[0][1]


def worker_log_signature(qid: str, pid: str) -> dict:
    """Extract exception lines around this item's processing from worker logs."""
    log = sh(["docker", "logs", WORKER, "--since", "6h"], timeout=120)
    lines = log.splitlines()
    # Find lines mentioning the ids, then collect the traceback window.
    hits = [i for i, l in enumerate(lines) if qid[:8] in l or pid[:8] in l or pid in l]
    sig = {"hits": len(hits), "windows": []}
    for i in hits[-3:]:
        window = lines[max(0, i - 2): i + 25]
        sig["windows"].append("\n".join(window))
    text = "\n".join(sig["windows"])
    sig["cancelled"] = "CancelledError" in text or "TimeoutError" in text
    sig["sidecar_timeout"] = bool(
        re.search(r"linkedin_sidecar.*(post_image|post)|TimeoutError", text, re.S)
    )
    sig["sidecar_error"] = "LinkedInSidecarError" in text
    sig["unhandled"] = "Unhandled exception while publishing" in text
    return sig


def sidecar_health(platform: str | None) -> dict | None:
    port = SIDECAR_PORT.get(platform or "")
    if not port:
        return None
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/health", timeout=10) as r:
            return json.loads(r.read())
    except Exception as exc:  # noqa: BLE001 - diagnostic surface
        return {"error": str(exc)}


def fix_deployed(platform: str | None) -> bool | None:
    """Does the running image carry the transport-classification fix?"""
    if platform != "linkedin":
        return None
    out = sh(
        ["docker", "exec", WORKER, "grep", "-c", FIX_MARKER,
         "/app/app/services/publishing.py"],
        timeout=30,
    ).strip()
    try:
        return int(out.splitlines()[-1]) > 0
    except (ValueError, IndexError):
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", help="post_id or publish_queue id")
    ap.add_argument("--latest-failed", action="store_true")
    ap.add_argument("--platform", help="platform filter for --latest-failed")
    args = ap.parse_args()

    if args.latest_failed:
        qid, pid = latest_failed(args.platform)
    elif args.target:
        qid, pid = resolve_ids(args.target)
    else:
        ap.error("give a post_id/queue_id or --latest-failed")

    report: dict = {"queue_id": qid, "post_id": pid,
                    "checked_at": datetime.now(UTC).isoformat()}

    q = sql(
        f"SELECT status, attempts, max_attempts, coalesce(locked_by,''), "
        f"scheduled_at::text FROM publish_queue WHERE id = '{qid}'"
    )
    report["queue"] = dict(zip(
        ["status", "attempts", "max_attempts", "locked_by", "scheduled_at"],
        q[0], strict=False)) if q else None

    t = sql(
        f"""SELECT sa.platform, sa.username, t.status, t.platform_post_id,
                   t.platform_url, left(coalesce(t.error_message,''),200),
                   coalesce(t.error_class,'')
            FROM post_targets t JOIN social_accounts sa ON sa.id = t.social_account_id
            WHERE t.post_id = '{pid}'"""
    )
    report["targets"] = [
        dict(zip(["platform", "username", "status", "platform_post_id",
                  "platform_url", "error_message", "error_class"], r, strict=False))
        for r in t
    ]

    platforms = {r[0] for r in t}
    report["log_signature"] = worker_log_signature(qid, pid)
    report["sidecar_health"] = {
        p: sidecar_health(p) for p in platforms if p in SIDECAR_PORT
    }
    report["fix_deployed_linkedin"] = fix_deployed("linkedin") if "linkedin" in platforms else None

    # Verdict heuristics — cite evidence, never guess.
    sig = report["log_signature"]
    verdicts = []
    if sig["unhandled"] and sig["cancelled"]:
        verdicts.append("UNHANDLED-TIMEOUT: raw CancelledError/TimeoutError escaped "
                        "publish_to_platform — pre-#2540 signature. If fix_deployed "
                        "is false, merge+deploy resolves; else look for a new hang site.")
    if sig["sidecar_error"]:
        verdicts.append("SIDECAR-ERROR: LinkedInSidecarError surfaced — check "
                        "sidecar_health and session/auth state.")
    if report["queue"] and report["queue"]["status"] == "processing":
        age_min = None
        try:
            sched = datetime.fromisoformat(report["queue"]["scheduled_at"].replace(" ", "T"))
            age_min = round((datetime.now(UTC) - sched).total_seconds() / 60, 1)
        except Exception:  # noqa: BLE001
            pass
        verdicts.append(f"IN-FLIGHT: queue still processing (scheduled {age_min}m ago) "
                        "— wait for the worker deadline before touching it.")
    if report["fix_deployed_linkedin"] is False:
        verdicts.append("OLD-IMAGE: running worker predates PR #2540 — timeouts will "
                        "keep burning attempts until the GHCR image redeploys.")
    if not verdicts:
        verdicts.append("No signature matched — read log_signature.windows manually.")
    report["verdicts"] = verdicts

    print(jdump(report))


if __name__ == "__main__":
    main()
