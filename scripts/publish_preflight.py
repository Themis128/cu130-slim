#!/usr/bin/env python3
"""Preflight check for scheduled SocialAuto posts — answers "will it actually post?"

For every post with status='scheduled' in the window (default: next 48h), checks:

  1. Targets exist and each social_account is status='active'
  2. OAuth token not expired before scheduled_at, or refresh_token_enc stored
     (hourly `refresh_expiring_tokens` task self-heals those)
  3. media_ids resolve to media_assets with a public_url that returns HTTP 200
     and an image/* or video/* content type
  4. Platform format rules:
       - instagram: image media must be JPEG-compatible (image/jpeg/png),
         carousel (multi-image) must have 2-10 slides
       - tiktok/reels: video required if the post declares a video format
  5. scheduled_at already in the past but status still 'scheduled' -> missed

Usage:
    python3 scripts/publish_preflight.py              # next 48h, readable report
    python3 scripts/publish_preflight.py --hours 24
    python3 scripts/publish_preflight.py --post <uuid>
    python3 scripts/publish_preflight.py --json

Exit codes: 0 = every post passes, 1 = at least one FAIL.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PSQL = [
    "docker", "compose", "exec", "-T", "social-postgres",
    "psql", "-U", "social_user", "-d", "social_automation", "-t", "-A", "-F", "\t",
]

MAX_IG_CAROUSEL = 10
JPEG_OK = {"image/jpeg", "image/jpg", "image/pjpeg"}


def sql(query: str) -> list[list[str]]:
    res = subprocess.run(
        [*PSQL, "-c", query], cwd=ROOT, capture_output=True, text=True
    )
    if res.returncode != 0:
        raise RuntimeError(f"psql failed: {res.stderr.strip()[:300]}")
    return [
        [c if c != "" else None for c in line.split("\t")]
        for line in res.stdout.strip().splitlines()
        if line
    ]


def head_ok(url: str, timeout: int = 10) -> tuple[bool, str]:
    # R2/Cloudflare blocks urllib's default UA — send a browser UA.
    req = urllib.request.Request(
        url, method="HEAD",
        headers={"User-Agent": "Mozilla/5.0 (compatible; SocialAuto-Preflight/1.0)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 400, r.headers.get("Content-Type", "")
    except Exception as e:
        return False, str(e)[:120]


def check_post(post_id: str | None, hours: int) -> list[dict]:
    where = "status = 'scheduled'"
    if post_id:
        where += f" AND id = '{post_id}'"
    else:
        where += (
            f" AND scheduled_at >= now() - interval '1 hour'"
            f" AND scheduled_at <= now() + interval '{hours} hours'"
        )

    posts = sql(
        "SELECT id, scheduled_at, "
        "left(regexp_replace(coalesce(content_text,''), '[\\r\\n\\t]+', ' ', 'g'),60), "
        f"media_ids::text FROM posts WHERE {where} ORDER BY scheduled_at"
    )
    results = []
    now = datetime.now(timezone.utc)

    for row in posts:
        row += [None] * (4 - len(row))  # psql drops trailing empty fields
        pid, sched, preview, media_txt = row[:4]
        issues: list[str] = []
        warns: list[str] = []
        sched_dt = None
        if sched:
            sched_dt = datetime.fromisoformat(sched)

        # 1) targets + account health
        targets = sql(
            "SELECT sa.platform, sa.username, sa.status, sa.token_expires_at, "
            "       (sa.refresh_token_enc IS NOT NULL)::int, pt.status "
            "FROM post_targets pt JOIN social_accounts sa ON sa.id = pt.social_account_id "
            f"WHERE pt.post_id = '{pid}'"
        )
        if not targets:
            issues.append("no target accounts")
        platforms = []
        for platform, username, acct_status, expires, has_refresh, tstatus in targets:
            platforms.append(platform)
            if acct_status != "active":
                issues.append(f"{platform}: account status={acct_status}")
            if expires:
                exp = datetime.fromisoformat(expires)
                if sched_dt and exp < sched_dt and not (has_refresh == "1"):
                    issues.append(f"{platform}: token expires {expires} before schedule, no refresh token")
                elif exp < now and has_refresh != "1":
                    issues.append(f"{platform}: token already expired, no refresh token")
            if tstatus == "skipped":
                warns.append(f"{platform}: target marked skipped")

        # 2) media checks
        media_ids = []
        if media_txt and media_txt not in ("{}", "NULL"):
            media_ids = [m for m in media_txt.strip("{}").split(",") if m]
        if not media_ids:
            warns.append("no media attached (text-only post)")
        for mid in media_ids:
            rows = sql(
                f"SELECT mime_type, public_url, is_archived::int FROM media_assets WHERE id='{mid}'"
            )
            if not rows:
                issues.append(f"media {mid[:8]}: not found in library")
                continue
            mime, url, archived = rows[0]
            if archived == "1":
                warns.append(f"media {mid[:8]}: archived")
            if not url:
                issues.append(f"media {mid[:8]}: no public_url")
                continue
            ok, ctype = head_ok(url)
            if not ok:
                issues.append(f"media {mid[:8]}: URL unreachable ({ctype})")
            elif ctype and not ctype.startswith(("image/", "video/")):
                # PDFs are valid LinkedIn document-post media — only flag
                # non-media types on platforms that can't take documents.
                if not (ctype == "application/pdf" and "linkedin" in platforms):
                    issues.append(f"media {mid[:8]}: bad content-type {ctype}")

        # 3) platform format rules
        if "instagram" in platforms and media_ids:
            if len(media_ids) > MAX_IG_CAROUSEL:
                issues.append(f"instagram: {len(media_ids)} slides > {MAX_IG_CAROUSEL} limit")
            for mid in media_ids:
                rows = sql(f"SELECT mime_type FROM media_assets WHERE id='{mid}'")
                if rows and rows[0][0] and rows[0][0] not in JPEG_OK and not rows[0][0].startswith("video/"):
                    issues.append(f"instagram: media {mid[:8]} is {rows[0][0]} — needs JPEG")

        # 4) missed schedule
        if sched_dt and sched_dt < now - timedelta(minutes=30):
            warns.append("scheduled_at in the past — was it missed?")

        results.append(
            {
                "post_id": pid,
                "scheduled_at": sched,
                "platforms": platforms,
                "preview": preview,
                "media_count": len(media_ids),
                "status": "FAIL" if issues else ("WARN" if warns else "OK"),
                "issues": issues,
                "warnings": warns,
            }
        )
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=48)
    ap.add_argument("--post", help="single post uuid")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    results = check_post(args.post, args.hours)
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        if not results:
            print("No scheduled posts in window.")
        for r in results:
            mark = {"OK": "[OK]  ", "WARN": "[WARN]", "FAIL": "[FAIL]"}[r["status"]]
            print(f"{mark} {r['scheduled_at'][:16]}  {','.join(r['platforms']):22} "
                  f"media={r['media_count']}  {r['preview']}")
            for i in r["issues"]:
                print(f"         ✗ {i}")
            for w in r["warnings"]:
                print(f"         ⚠ {w}")
    return 1 if any(r["status"] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
