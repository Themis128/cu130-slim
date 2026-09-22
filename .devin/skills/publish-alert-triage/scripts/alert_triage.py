#!/usr/bin/env python3
"""SocialAuto publish/alert triage.

Classifies recent publishing failures and analytics-sync errors from the
SocialAuto Postgres DB into actionable buckets, so Slack-digest alerts can be
triaged in seconds instead of read one by one.

Stdlib-only; runs on the host and shells out to `docker exec social-postgres
psql`. No secrets printed.

Usage:
    python3 alert_triage.py [--days N] [--json] [--section NAME]

Sections: failures, analytics, accounts, sessions, queue, env
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from typing import Any

DB_CONTAINER = os.environ.get("SOCIAL_DB_CONTAINER", "social-postgres")
DB_USER = os.environ.get("SOCIAL_DB_USER", "social_user")
DB_NAME = os.environ.get("SOCIAL_DB_NAME", "social_automation")

SIDECARS = {
    # name: (url, session_key or None for health-only)
    "browser-bridge": ("http://localhost:9223/session/status", "logged_in"),
    "tiktok": ("http://localhost:9224/session", "logged_in"),
    "linkedin": ("http://localhost:9225/session", "logged_in"),
    "facebook": ("http://localhost:9226/session/validate", "logged_in"),
    "messenger": ("http://localhost:9230/health", None),
}

# ---------------------------------------------------------------------------
# Signature → classification table
# class: platform-limit (don't retry), app-bug (fix code), session (reconnect),
#        config (env/console), transient (retry covered)
# ---------------------------------------------------------------------------

SIGNATURES: list[tuple[str, str, str, str]] = [
    # (regex, class, label, action)
    (r"url_ownership_unverified", "config", "TikTok PULL_FROM_URL domain not verified",
     "Verify cloudless.gr in TikTok console (tiktok-console-ops → domain-verify.sh) or switch to FILE_UPLOAD"),
    (r"unaudited_client_can_only_post_to_private_accounts", "config", "TikTok app unaudited for public Direct Post",
     "Submit Content Posting API audit / App Review; until then MEDIA_UPLOAD or SELF_ONLY on private account"),
    (r"MEDIA_PUBLIC_BASE_URL", "config", "No public media URL base for TikTok",
     "Set MEDIA_PUBLIC_BASE_URL to a public https URL (Cloudflare tunnel) reachable by TikTok"),
    (r"\(#200\).*publish_to_groups|posting to a group|Groups API is deprecated", "platform-limit",
     "Facebook Groups API deprecated",
     "Meta removed Groups API (publish_to_groups) in v19+ — retarget to a Page; do not retry/reconnect"),
    (r"Instagram requires at least one image", "platform-limit", "IG text-only post",
     "Instagram API has no text-only posts — attach media or drop the IG target for this post"),
    (r"Error validating access token: Session has expired", "session", "Token expired",
     "Reconnect the account in SocialAuto → Accounts (OAuth refresh); check token_expires_at"),
    (r"REVOKED_ACCESS_TOKEN|401.*Unauthorized", "session", "OAuth token revoked/expired",
     "Verify refresh_expiring_tokens rotated it; if refresh token also dead, reconnect account"),
    (r"\(#10\) Application does not have permission", "config", "Meta app permission missing",
     "App lacks instagram_content_publish/pages scope or is unreviewed — see meta-app-review skill"),
    (r"Media ID is not available", "app-bug", "IG media container not ready",
     "media_publish called before container FINISHED — needs status polling before publish"),
    (r"metric\[\d+\] must be one of", "app-bug", "Invalid Meta insights metric",
     "Metric name invalid for this API version — adaptive dropper should handle; check call path"),
    (r"\(#100\).*valid insights metric", "app-bug", "FB insights metric invalid",
     "Metric name wrong for Graph API version — see docs for valid post insights"),
    (r"does not exist, cannot be loaded due to missing permissions", "session",
     "Threads/Graph object unreachable",
     "Media id stale or token lacks scope — media may be deleted or owned by another user"),
    (r"free tier monthly write quota|1,500 tweets", "platform-limit", "X write quota exhausted",
     "Free tier 1,500 posts/month — waits for billing reset; browser fallback covers real posts"),
    (r"stats HTTP 402|HTTP 402", "platform-limit", "X API paid-tier metric",
     "non_public_metrics needs paid tier — expected on free plan"),
    (r"Post button disabled|Could not find the tweet composer|Continue button not found", "app-bug",
     "X web UI selector drift",
     "Browser fallback selectors stale — update browser bridge X composer/login selectors"),
    (r"Browser session not logged in", "session", "Bridge session dead",
     "Re-login via noVNC or transplant cookies (session-transplant skill)"),
    (r"Session already active for|Browser busy", "transient", "Bridge serialization",
     "Expected — shared bridge serializes platform sessions; queue retries handle it"),
    (r"Navigation failed: Page.goto|Target page.*closed|No active browser session", "transient",
     "Bridge browser crash/race",
     "Browser restarted mid-op — retry usually recovers; check bridge health if persistent"),
    (r"publish_cancelled", "session", "TikTok MEDIA_UPLOAD draft cancelled",
     "Inbox draft was dismissed/cancelled on the phone app — republish or switch to DIRECT_POST after audit"),
    (r"upload timeout", "transient", "TikTok upload timeout",
     "Large media or slow path — retry; check sidecar session if repeated"),
    (r"All connection attempts failed", "transient", "Network/API unreachable",
     "Transient — retry covered"),
    (r"personal account.*Graph API insights", "platform-limit", "IG personal has no insights API",
     "Business/Creator accounts only — scrape path fills the gap"),
    (r"Cannot parse access token|Invalid OAuth access token", "session",
     "OAuth token unparseable", "Token corrupted/rotated — reconnect the account"),
    (r"Application does not have permission", "session", "Meta app permission/token",
     "Token lacks scope or app unreviewed — reconnect; see meta-app-review skill"),
    (r"stats HTTP 401", "session", "Token expired during sync",
     "Refresh/reconnect the account — refresh_expiring_tokens should rotate it"),
    (r"stats HTTP 5|unknown error has occurred", "transient", "Platform API 5xx",
     "Transient platform error — retried automatically"),
    (r"stats HTTP 404|video_not_found|_not_found", "info", "Media deleted/missing on platform",
     "Post removed on platform — informational only"),
    (r"member_stats_not_implemented|organization_lifetime", "info", "Informational sync note",
     "No action — informational marker"),
    (r"stats HTTP 4", "app-bug", "Stats call rejected (4xx)",
     "Inspect the raw note — likely invalid metric/params for this API version"),
]

SOFT_NOTES = ("activityids", "stats_unavailable")  # mirror slack_digest skips


def _psql(sql: str) -> list[list[str]]:
    r = subprocess.run(
        ["docker", "exec", DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME,
         "-tAc", sql],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    rows = []
    for line in r.stdout.splitlines():
        if line.strip():
            rows.append(line.split("|"))
    return rows


def _http(url: str, timeout: int = 20) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)[:120]}


def _classify(msg: str) -> tuple[str, str, str]:
    for pat, cls, label, action in SIGNATURES:
        if re.search(pat, msg, re.I):
            return cls, label, action
    return "unknown", "Unclassified", "Investigate — no signature match"


def section_failures(days: int) -> list[str]:
    rows = _psql(
        "SELECT sa.platform, pt.error_message, pt.published_at::timestamp(0), "
        "pt.post_id FROM post_targets pt "
        "JOIN social_accounts sa ON sa.id = pt.social_account_id "
        f"WHERE pt.status = 'failed' AND pt.published_at > now() - interval '{days} days' "
        "ORDER BY pt.published_at DESC"
    )
    if not rows:
        return ["  no failed targets in window"]
    groups: dict[tuple, dict] = {}
    for platform, err, ts, post_id in rows:
        cls, label, action = _classify(err or "")
        key = (cls, label, platform)
        g = groups.setdefault(key, {"n": 0, "latest": ts, "action": action,
                                    "post_ids": set()})
        g["n"] += 1
        g["post_ids"].add(post_id)
        if ts > g["latest"]:
            g["latest"] = ts
    out = []
    order = {"app-bug": 0, "config": 1, "session": 2, "platform-limit": 3,
             "transient": 4, "unknown": 5, "info": 6}
    for (cls, label, platform), g in sorted(
            groups.items(), key=lambda kv: (order.get(kv[0][0], 9), -kv[1]["n"])):
        out.append(
            f"  [{cls:>14}] {platform:<10} ×{g['n']:<3} latest {g['latest']}\n"
            f"                   {label}\n"
            f"                   → {g['action']}"
        )
    return out


def section_analytics(days: int) -> list[str]:
    rows = _psql(
        "SELECT sa.platform, s.notes, s.captured_at::timestamp(0) "
        "FROM post_analytics_snapshots s "
        "LEFT JOIN social_accounts sa ON sa.id = s.social_account_id "
        f"WHERE s.notes IS NOT NULL AND s.captured_at > now() - interval '{days} days' "
        "ORDER BY s.captured_at DESC LIMIT 200"
    )
    groups: dict[tuple, dict] = {}
    for platform, note, ts in rows:
        n = (note or "").lower()
        if any(k in n for k in SOFT_NOTES):
            continue
        cls, label, action = _classify(note or "")
        key = (cls, label, platform or "?")
        g = groups.setdefault(key, {"n": 0, "latest": ts, "action": action})
        g["n"] += 1
        if ts > g["latest"]:
            g["latest"] = ts
    if not groups:
        return ["  no error notes in window"]
    out = []
    for (cls, label, platform), g in sorted(groups.items(), key=lambda kv: -kv[1]["n"]):
        out.append(
            f"  [{cls:>14}] {platform:<10} ×{g['n']:<3} latest {g['latest']}\n"
            f"                   {label}\n"
            f"                   → {g['action']}"
        )
    return out


def section_accounts() -> list[str]:
    rows = _psql(
        "SELECT platform, username, status, "
        "COALESCE(token_expires_at::timestamp(0)::text,'-'), "
        "CASE WHEN refresh_token_enc IS NULL THEN 'no' ELSE 'yes' END "
        "FROM social_accounts ORDER BY platform, username"
    )
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    out = []
    for platform, user, status, exp, refresh in rows:
        flag = ""
        if status != "active":
            flag = "  ⚠ status"
        elif exp != "-" and exp < today:
            flag = "  ⚠ expired"
        elif refresh == "no":
            flag = "  (no refresh token)"
        out.append(f"  {platform:<10} {user or '?':<24} {status:<10} "
                   f"exp={exp} refresh={refresh}{flag}")
    return out


def section_sessions() -> list[str]:
    out = []
    for name, (url, key) in SIDECARS.items():
        d = _http(url)
        if "error" in d:
            out.append(f"  {name:<15} unreachable ({d['error'][:60]})")
            continue
        if key is None:  # health-only service
            ok = d.get("status") in ("ok", "healthy") or d.get("healthy")
            out.append(f"  {name:<15} {'up' if ok else 'degraded'}")
            continue
        if "platform" in d and "status" in d:  # bridge schema
            cookies = len(d.get("cookies_found") or [])
            msg = (d.get("message") or "")[:60]
            out.append(f"  {name:<15} {d['status']}@{d['platform']} "
                       f"({cookies} cookies) {msg}")
            continue
        logged = d.get(key, d.get("has_session", d.get("authenticated")))
        extra = ""
        if d.get("code") == "RATE_LIMITED" or d.get("rate_limited"):
            extra = "  ⚠ rate-limited (clear via /session/clear-rate-limit)"
        out.append(f"  {name:<15} session={'yes' if logged else 'NO'}{extra}")
    return out


def section_queue() -> list[str]:
    rows = _psql(
        "SELECT status, count(*) FROM publish_queue GROUP BY 1 ORDER BY 2 DESC"
    )
    stuck = _psql(
        "SELECT count(*) FROM publish_queue WHERE status='pending' "
        "AND scheduled_at < now() - interval '1 hour'"
    )
    out = [f"  {s}: {c}" for s, c in rows]
    if stuck and stuck[0] and int(stuck[0][0]) > 0:
        out.append(f"  ⚠ {stuck[0][0]} pending items overdue >1h")
    return out


def section_env() -> list[str]:
    out = []
    env_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".env")
    keys = {"MEDIA_PUBLIC_BASE_URL": "TikTok PULL_FROM_URL base",
            "SOCIAL_TOTP_SECRET": "n8n workflow login",
            "N8N_API_KEY": "n8n API access"}
    try:
        with open(env_path, encoding="utf-8") as f:
            text = f.read()
        for k, desc in keys.items():
            set_ = re.search(rf"^{k}=\S+", text, re.M) is not None
            out.append(f"  {k:<28} {'set' if set_ else 'MISSING'}  ({desc})")
    except OSError:
        out.append("  .env unreadable")
    return out


def main() -> None:
    days = 7
    only: set[str] = set()
    as_json = False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--days":
            days = int(args[i + 1]); i += 2
        elif args[i] == "--section":
            only = set(args[i + 1].split(",")); i += 2
        elif args[i] == "--json":
            as_json = True; i += 1
        else:
            i += 1

    sections = [
        ("failures", f"Failed publish targets (last {days}d)", section_failures, days),
        ("analytics", f"Analytics sync errors (last {days}d)", section_analytics, days),
        ("accounts", "Social accounts", section_accounts, None),
        ("sessions", "Browser/sidecar sessions", section_sessions, None),
        ("queue", "Publish queue", section_queue, None),
        ("env", "Config checks", section_env, None),
    ]
    report: dict[str, list[str]] = {}
    for key, title, fn, arg in sections:
        if only and key not in only:
            continue
        try:
            report[title] = fn(arg) if arg is not None else fn()
        except Exception as e:
            report[title] = [f"  error: {e}"]

    if as_json:
        print(json.dumps(report, indent=2))
        return
    print("=" * 72)
    print("SocialAuto alert triage")
    print("=" * 72)
    for title, lines in report.items():
        print(f"\n## {title}\n")
        print("\n".join(lines) if lines else "  (none)")
    print()


if __name__ == "__main__":
    main()
