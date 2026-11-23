#!/usr/bin/env python3
"""Dry-run a queued LinkedIn newsletter issue — incl. inline media — against
the live sidecar (:9225), WITHOUT touching the publish button.

Why this exists (verified 2026-11-24): the sidecar publish path supports
`media[]` items appended at the end of the article body through the editor's
"Add image or video" dialog (hidden input[type=file] → Next confirm →
ProseMirror node). Queued issues carry the spec in
`newsletter_issues.result["_media"]` as
    [{"type":"image"|"video","asset_id":"<uuid>"}]
    | {"type":..., "base64": "...", "filename": "..."}
    | {"type":..., "url": "https://public/...", "filename": "..."}

`asset_id` entries resolve bytes server-side via media_dedup.load_asset_bytes
(R2/minio/local) — private R2 URLs 403 for anonymous fetch, so never pass the
asset's public_url to the sidecar.

This script runs the payload-build INSIDE social-api (DB + storage access),
then POSTs to the sidecar from wherever it runs — it survives social-api
restarts because only the ~3s build step needs the container.

Usage (host):
  python3 newsletter_media_dryrun.py ISSUE_ID_OR_SLUG [--sidecar http://localhost:9225] [--publish]

  --publish actually publishes (omit for dry-run). A dry-run still creates a
  private draft on LinkedIn — expected, not a bug.

Verified 2026-11-24 on issue 9baa5f26 (black-friday-offers-2026):
  cover-uploaded → title-filled → body-pasted → media-1/2-uploaded → seo-set.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request  # noqa: E402

BUILD_SNIPPET = r'''
import asyncio, base64, json
from sqlalchemy import select, or_
from app.db.session import async_session_maker
from app.models.content import MediaAsset
from app.models.newsletter_issue import NewsletterIssue
from app.services.media_dedup import load_asset_bytes
from app.services.newsletter_cover import render_newsletter_cover

KEY = __KEY__

async def main():
    async with async_session_maker() as db:
        q = select(NewsletterIssue).where(
            or_(NewsletterIssue.id == KEY, NewsletterIssue.slug == KEY))
        issue = (await db.execute(q)).scalar_one()
        spec = (issue.result or {}).get("_media", [])
        media = []
        for item in spec:
            if item.get("base64") or item.get("url"):
                media.append(item)
                continue
            a = (await db.execute(
                select(MediaAsset).where(MediaAsset.id == item["asset_id"]))
            ).scalar_one()
            data = await load_asset_bytes(a)
            media.append({"type": item.get("type", "image"),
                          "base64": base64.b64encode(data).decode(),
                          "filename": a.filename})
    cover = base64.b64encode(render_newsletter_cover(
        issue.title, issue_number=issue.issue_number,
        issue_date=None, category=issue.category)).decode()
    payload = {
        "title": issue.title,
        "html_body": issue.html_body,
        "cover_base64": cover,
        "cover_filename": "cover.png",
        "newsletter_name": issue.newsletter_name,
        "seo_title": issue.seo_title or issue.title,
        "seo_description": (issue.seo_description or "")[:300] or None,
        "post_description": issue.post_description,
        "media": media,
        "dry_run": __DRYRUN__,
    }
    print(json.dumps(payload))

asyncio.run(main())
'''


def build_payload(key: str, dry_run: bool, container: str) -> dict:
    snippet = BUILD_SNIPPET.replace("__KEY__", json.dumps(key)).replace(
        "__DRYRUN__", "True" if dry_run else "False"
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False
    ) as f:
        f.write(snippet)
        host_path = f.name
    try:
        subprocess.run(
            ["docker", "cp", host_path, f"{container}:/tmp/_nl_payload_build.py"],
            check=True, capture_output=True,
        )
        out = subprocess.run(
            ["docker", "exec", container, "python", "/tmp/_nl_payload_build.py"],
            check=True, capture_output=True, text=True,
        )
        # sqlalchemy echo lines precede the JSON — take the last {...} block
        lines = [l for l in out.stdout.splitlines() if l.startswith("{")]
        return json.loads("\n".join(lines))
    finally:
        Path(host_path).unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("issue", help="newsletter_issues id or slug")
    ap.add_argument("--sidecar", default="http://localhost:9225")
    ap.add_argument("--container", default="social-api")
    ap.add_argument("--publish", action="store_true",
                    help="actually publish (default: dry-run)")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    payload = build_payload(args.issue, not args.publish, args.container)
    media = [(m.get("type"), len(m.get("base64") or "")) for m in payload.get("media") or []]
    print(f"payload: {len(json.dumps(payload))} bytes, media={media}")

    r = request(
        "POST", f"{args.sidecar}/newsletter/publish",
        data=payload, timeout=args.timeout,
    )
    print(json.dumps(r, indent=2))
    steps = r.get("steps") or []
    failed = [s for s in steps if "failed" in str(s)]
    return 1 if (failed and not args.publish) else 0


if __name__ == "__main__":
    sys.exit(main())
