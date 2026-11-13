#!/usr/bin/env python3
"""publish_queue_preflight.py — preflight posts that are ABOUT TO publish.

Sibling of publish_preflight.py (which only covers future `scheduled`
posts). This one audits posts with a live publish_queue row in
pending/processing — the rows a queue run is claiming right now — so
failures surface BEFORE the platform API call burns an attempt.

Per post+target it checks:

- post has non-empty content and a publish_queue row with attempts left
- target's social_account is active and its token outlives now
  (refresh-token presence noted, matching preflight semantics)
- every media_id resolves to media_assets whose EFFECTIVE type — derived
  from storage_path extension, same as /api/v1/media/view — is
  platform-compatible, and whose publish URL returns bytes. Publishers
  fetch ``<MEDIA_PUBLIC_BASE_URL>/api/v1/media/view?path=...`` (or the
  Cloudflare tunnel equivalent), NOT the raw R2 ``public_url`` — the R2
  object may serve application/octet-stream while the view endpoint
  re-types by extension, so public_url is only a fallback signal.
- quality_gate cache state — uncached posts will pay minutes of DMR
  calls inside the ~900s Celery soft limit (see publish-ops skill)

    python3 scripts/publish_queue_preflight.py                 # all live rows
    python3 scripts/publish_queue_preflight.py --post <uuid>   # one post
    python3 scripts/publish_queue_preflight.py --json
    python3 scripts/publish_queue_preflight.py --media-base URL

Exit 1 if any post FAILs.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PSQL = [
    "docker", "exec", "-i", "social-postgres",
    "psql", "-U", "social_user", "-d", "social_automation", "-t", "-A", "-F", "\t",
]

MAX_IG_CAROUSEL = 10
JPEG_OK = {"image/jpeg", "image/jpg", "image/pjpeg"}

# Mirrors ext_mime in app/api/media.py view_media — the content type the
# publish URL actually serves, regardless of what R2 stored.
EXT_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".avif": "image/avif", ".bmp": "image/bmp", ".ico": "image/x-icon",
    ".tif": "image/tiff", ".tiff": "image/tiff",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
    ".pdf": "application/pdf",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
    ".ogg": "audio/ogg", ".flac": "audio/flac",
}
DEFAULT_MEDIA_BASE = "https://social.cloudless.gr"


def sql(query: str) -> list[list[str]]:
    res = subprocess.run(
        [*PSQL, "-c", query], capture_output=True, text=True
    )
    if res.returncode != 0:
        raise RuntimeError(f"psql failed: {res.stderr.strip()[:300]}")
    return [
        [c if c != "" else None for c in line.split("\t")]
        for line in res.stdout.strip().splitlines()
        if line
    ]


def head_ok(url: str, timeout: int = 15) -> tuple[bool, str]:
    # /api/v1/media/view is GET-only (HEAD -> 405); range-GET the first
    # bytes instead so we exercise the same path publishers fetch.
    req = urllib.request.Request(
        url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; SocialAuto-Preflight/1.0)",
            "Range": "bytes=0-511",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 400, r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        if e.code == 405:  # HEAD-style rejection — treat as reachable
            return True, ""
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)[:120]


def live_posts(post_id: str | None) -> list[list[str]]:
    where = "pq.status IN ('pending','processing')"
    if post_id:
        where += f" AND pq.post_id = '{post_id}'"
    return sql(
        "SELECT DISTINCT pq.post_id, p.status, "
        "left(regexp_replace(coalesce(p.content_text,''), '[\\r\\n\\t]+', ' ', 'g'),60), "
        "p.media_ids::text, p.platform_specific->'quality_gate'->>'content_hash', "
        "max(pq.attempts), max(pq.max_attempts) "
        "FROM publish_queue pq JOIN posts p ON p.id = pq.post_id "
        f"WHERE {where} GROUP BY pq.post_id, p.status, p.content_text, p.media_ids, "
        "p.platform_specific ORDER BY pq.post_id"
    )


def check_post(row: list[str], media_base: str) -> dict:
    row += [None] * (7 - len(row))
    pid, pstatus, preview, media_txt, gate_hash, attempts, max_attempts = row[:7]
    issues: list[str] = []
    warns: list[str] = []

    if pstatus in ("published", "archived"):
        warns.append(f"post status={pstatus} — queue row may be stale")
    if attempts and max_attempts and int(attempts) >= int(max_attempts):
        issues.append(f"queue attempts exhausted ({attempts}/{max_attempts})")
    if not gate_hash:
        warns.append("no quality_gate cache — run will pay full DMR gate cost")

    targets = sql(
        "SELECT sa.platform, sa.username, sa.status, sa.token_expires_at, "
        "(sa.refresh_token_enc IS NOT NULL)::int, pt.status "
        "FROM post_targets pt JOIN social_accounts sa ON sa.id = pt.social_account_id "
        f"WHERE pt.post_id = '{pid}'"
    )
    if not targets:
        issues.append("no target accounts")
    platforms = []
    now = datetime.now(timezone.utc)
    for platform, username, acct_status, expires, has_refresh, tstatus in targets:
        platforms.append(platform)
        if acct_status != "active":
            # OAuth platforms fail at publish time on dead creds anyway;
            # IG "revoked" is often stale metadata (instagrapi sidecar
            # publishes regardless) — warn, don't fail.
            warns.append(f"{platform}: account status={acct_status}")
        if expires:
            try:
                exp = datetime.fromisoformat(expires)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp < now and has_refresh != "1":
                    issues.append(f"{platform}: token expired, no refresh token")
            except ValueError:
                pass
        if tstatus == "published":
            warns.append(f"{platform}: target already published")
        elif tstatus in ("failed", "skipped"):
            warns.append(f"{platform}: target {tstatus}")

    media_ids = []
    if media_txt and media_txt not in ("{}", "NULL"):
        media_ids = [m for m in media_txt.strip("{}").split(",") if m]
    if not media_ids:
        warns.append("no media attached (text-only post)")
    mimes: dict[str, str] = {}
    for mid in media_ids:
        rows = sql(
            f"SELECT mime_type, public_url, storage_path, is_archived::int "
            f"FROM media_assets WHERE id='{mid}'"
        )
        if not rows:
            issues.append(f"media {mid[:8]}: not found in library")
            continue
        mime, url, storage_path, archived = (rows[0] + [None] * 4)[:4]
        # Effective type = what /api/v1/media/view will serve (extension
        # derived), falling back to the stored mime_type.
        ext = Path(storage_path or "").suffix.lower()
        eff_mime = EXT_MIME.get(ext) or mime or ""
        mimes[mid] = eff_mime
        if archived == "1":
            warns.append(f"media {mid[:8]}: archived")
        if not storage_path:
            issues.append(f"media {mid[:8]}: no storage_path")
            continue
        view_url = (
            f"{media_base.rstrip('/')}/api/v1/media/view?path="
            f"{urllib.parse.quote(storage_path)}"
        )
        ok, ctype = head_ok(view_url)
        if not ok:
            issues.append(f"media {mid[:8]}: view URL unreachable ({ctype})")
        elif ctype and not ctype.startswith(("image/", "video/", "application/pdf")):
            issues.append(f"media {mid[:8]}: view serves {ctype}")
        # public_url is only a fallback signal — octet-stream there is
        # cosmetic when the view endpoint re-types correctly.
        if url and not ok:
            ok2, _ = head_ok(url)
            if not ok2:
                warns.append(f"media {mid[:8]}: public_url also unreachable")

    if "instagram" in platforms and media_ids:
        if len(media_ids) > MAX_IG_CAROUSEL:
            issues.append(f"instagram: {len(media_ids)} slides > {MAX_IG_CAROUSEL}")
        for mid in media_ids:
            mt = mimes.get(mid, "")
            if mt and mt not in JPEG_OK and not mt.startswith("video/"):
                issues.append(f"instagram: media {mid[:8]} is {mt} — needs JPEG")
    if "tiktok" in platforms:
        has_video = any(mt.startswith("video/") for mt in mimes.values())
        if media_ids and not has_video:
            issues.append("tiktok: no video media attached")

    return {
        "post_id": pid,
        "post_status": pstatus,
        "platforms": platforms,
        "preview": preview,
        "media_count": len(media_ids),
        "gate_cached": bool(gate_hash),
        "attempts": attempts,
        "status": "FAIL" if issues else ("WARN" if warns else "OK"),
        "issues": issues,
        "warnings": warns,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--post", help="single post uuid")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--media-base", default=DEFAULT_MEDIA_BASE,
                    help="base URL publishers use for /api/v1/media/view")
    args = ap.parse_args()

    results = [check_post(r, args.media_base) for r in live_posts(args.post)]
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        if not results:
            print("No pending/processing queue rows.")
        for r in results:
            mark = {"OK": "[OK]  ", "WARN": "[WARN]", "FAIL": "[FAIL]"}[r["status"]]
            print(f"{mark} {r['post_id'][:8]}  {','.join(r['platforms']):30} "
                  f"media={r['media_count']} gate={'cached' if r['gate_cached'] else 'MISSING'}  {r['preview']}")
            for i in r["issues"]:
                print(f"         x {i}")
            for w in r["warnings"]:
                print(f"         ! {w}")
    return 1 if any(r["status"] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
