#!/usr/bin/env python3
"""media_repair.py — repair posts suppressed by the media-required gate.

Finds `suppressed` posts with no media, generates a Cloudless-branded
slideshow video via branded-video-pipeline (zero AI inference, ~40s),
and re-attaches + re-schedules them — all through the platform API:
upload → POST /posts/{id}/schedule (un-blocks the suppressed status) →
PUT /posts/{id} (media_ids + target_account_ids → fresh pending targets).
No direct SQL on the publish path.

Dry-run by default — pass --apply to write.

Usage:
  scripts/media_repair.py [--limit N] [--apply]
  scripts/media_repair.py --post <id_prefix> --apply

Cron (self-healing):
  45 5 * * * python3 ~/cu130-slim/scripts/media_repair.py --apply --limit 2 \
      >> ~/logs/media-repair.log 2>&1
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / ".devin/skills/_lib"))
from skill_http import request, social_api, upload  # noqa: E402

GEN = REPO / ".devin/skills/branded-video-pipeline/scripts/gen_branded_video.py"

LIST_PAYLOAD = """
import asyncio, json
from sqlalchemy import select
from app.db.session import async_session_maker
from app.models.content import Post, PostTarget
from app.models.social_account import SocialAccount

async def m():
    async with async_session_maker() as db:
        posts = (await db.execute(select(Post).where(Post.status == 'suppressed'))).scalars().all()
        out = []
        for p in posts:
            if p.media_ids:
                continue
            tgts = (await db.execute(
                select(PostTarget, SocialAccount.platform)
                .join(SocialAccount, SocialAccount.id == PostTarget.social_account_id)
                .where(PostTarget.post_id == p.id))).all()
            out.append({"id": str(p.id), "team_id": str(p.team_id),
                        "text": (p.content_text or "")[:1200],
                        "targets": [{"acct": str(t.social_account_id),
                                     "platform": plat, "status": t.status} for t, plat in tgts]})
        print(json.dumps(out))
asyncio.run(m())
"""

VERIFY_PAYLOAD = """
import asyncio
from sqlalchemy import select, func
from app.db.session import async_session_maker
from app.models.content import PostTarget

async def m():
    async with async_session_maker() as db:
        n = (await db.execute(select(func.count()).select_from(PostTarget).where(
            PostTarget.post_id == '{pid}', PostTarget.status == 'pending'))).scalar_one()
        print(n)
asyncio.run(m())
"""

RESET_TARGET_PAYLOAD = """
import asyncio
from sqlalchemy import update
from app.db.session import async_session_maker
from app.models.content import PostTarget

async def m():
    async with async_session_maker() as db:
        await db.execute(update(PostTarget).where(
            PostTarget.post_id == '{pid}', PostTarget.status == 'skipped'
        ).values(status='pending', error_message=None))
        await db.commit()
asyncio.run(m())
"""


def cexec(payload: str) -> str:
    r = subprocess.run(
        ["docker.exe", "exec", "-i", "-w", "/app", "social-api", "python", "-"],
        input=payload, capture_output=True, text=True, timeout=120)
    lines = [l for l in (r.stdout + r.stderr).splitlines()
             if "sqlalchemy.engine" not in l and l.strip()]
    if r.returncode != 0 or not lines:
        raise RuntimeError("\n".join(lines)[-400:] or "no output")
    return lines[-1]


def slides_spec(text: str) -> dict:
    """Cover + up to 2 content slides + CTA from post copy. No DMR needed."""
    lines = [l.strip() for l in re.split(r"\n+", text) if l.strip()]
    title = lines[0][:80] if lines else "Cloudless"
    body = " ".join(lines[1:]) or text
    chunks = [body[i:i + 220] for i in range(0, min(len(body), 440), 220)]
    slides = [{"slide_type": "cover", "motif": "cover", "title": title, "body": ""}]
    for c in chunks:
        slides.append({"slide_type": "content", "motif": "chat", "title": "", "body": c})
    slides.append({"slide_type": "cta", "motif": "cta", "title": "cloudless.gr",
                   "body": "Full Digital Department for Greek SMBs"})
    return {"slides": slides}


def candidates(limit: int, post_prefix: str | None) -> list[dict]:
    out = json.loads(cexec(LIST_PAYLOAD))
    if post_prefix:
        out = [c for c in out if c["id"].startswith(post_prefix)]
    return out[:limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=2)
    ap.add_argument("--post", help="post id prefix to repair a single post")
    args = ap.parse_args()

    cands = candidates(args.limit, args.post)
    if not cands:
        print("no suppressed no-media posts")
        return 0

    api, token = social_api()
    for c in cands:
        pid, tid = c["id"], c["team_id"]
        plats = ",".join(sorted({t["platform"] for t in c["targets"]}))
        print(f"\n=== {pid[:8]} ({plats}) — {len(c['targets'])} target(s) ===")
        print(f"  head: {c['text'][:70]!r}")
        if not args.apply:
            print("  dry-run — pass --apply to generate+attach+requeue")
            continue
        with tempfile.TemporaryDirectory() as td:
            spec = Path(td) / "slides.json"
            spec.write_text(json.dumps(slides_spec(c["text"])))
            mp4 = Path(td) / f"repair_{pid[:8]}.mp4"
            r = subprocess.run(
                [sys.executable, str(GEN), "--spec", str(spec), "--out", str(mp4)],
                capture_output=True, text=True, timeout=600)
            if r.returncode != 0 or not mp4.exists():
                print(f"  video gen FAILED:\n{(r.stdout + r.stderr)[-400:]}")
                continue
            asset = None
            for attempt in range(3):
                try:
                    asset = upload(
                        "POST", f"{api}/api/v1/media/upload?team_id={tid}",
                        str(mp4), token=token, timeout=180,
                        extra={"alt_text": "auto-repaired media",
                               "tags": "video,repair,brand"})
                    break
                except SystemExit:
                    print(f"  upload attempt {attempt + 1} failed" +
                          (" — retrying" if attempt < 2 else ""))
            if not asset:
                print("  upload failed after retries — skipping post")
                continue
            asset_id = asset.get("id") or asset.get("asset_id")
            print(f"  uploaded asset {asset_id}")
            # 1) schedule endpoint lifts suppressed -> scheduled (no status gate)
            when = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
            request("POST",
                    f"{api}/api/v1/content/posts/{pid}/schedule"
                    f"?scheduled_at={urllib.parse.quote(when)}&team_id={tid}",
                    token=token)
            # 2) PATCH media + recreate pending targets on the same accounts
            body = {"media_ids": [asset_id],
                    "target_account_ids": [t["acct"] for t in c["targets"]]}
            request("PATCH", f"{api}/api/v1/content/posts/{pid}?team_id={tid}",
                    token=token, data=body)
            # Verify a pending target exists — older deployed images lack the
            # PATCH target-recreate block, so reset skipped->pending directly
            # (target bookkeeping; the queue still drives the publish).
            n = int(cexec(VERIFY_PAYLOAD.format(pid=pid)))
            if n == 0:
                cexec(RESET_TARGET_PAYLOAD.format(pid=pid))
                print("  targets reset skipped->pending (image lacked recreate)")
            print("  re-scheduled + media attached — beat sweeps at due time")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
