#!/usr/bin/env python3
"""Upload SoundHelix music beds to the SocialAuto media library and
assign one to posts that carry a single video asset.

Runs INSIDE the social-api container (has app deps, ffmpeg, DB env).

    docker cp ~/social-music social-api:/tmp/social-music
    docker exec social-api python /tmp/music_library.py --upload-only
    docker exec social-api python /tmp/music_library.py --assign <post_id> [<post_id>...]
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, "/app")

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.security import create_access_token  # noqa: E402
from app.db.session import async_session_maker  # noqa: E402
from app.models.content import MediaAsset, Post  # noqa: E402

SRC = Path("/tmp/social-music")
CLIPS = SRC / "clips"
API = "http://localhost:8000"
BED_SECONDS = 60


async def _team_user(db) -> tuple[uuid.UUID, uuid.UUID]:
    post = (await db.execute(select(Post).where(Post.music_asset_id.is_(None)).limit(1))).scalar_one()
    return post.team_id, post.user_id or post.team_id


def _trim(src: Path, dst: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(src), "-t", str(BED_SECONDS),
            "-af", "afade=t=in:d=1.5,afade=t=out:st=57:d=3,volume=0.9",
            "-codec:a", "libmp3lame", "-q:a", "4", str(dst),
        ],
        check=True, capture_output=True,
    )


async def upload_all() -> None:
    async with async_session_maker() as db:
        team_id, user_id = await _team_user(db)
    token = create_access_token({"sub": str(user_id)})
    headers = {"Authorization": f"Bearer {token}"}

    for mp3 in sorted(SRC.glob("SoundHelix-Song-*.mp3")):
        n = mp3.stem.rsplit("-", 1)[-1]
        clip = CLIPS / f"bed-{n}.mp3"
        if not clip.exists():
            _trim(mp3, clip)
        data = clip.read_bytes()
        with httpx.Client(timeout=120) as client:
            r = client.post(
                f"{API}/api/v1/media/upload",
                params={"team_id": str(team_id)},
                files={"file": (clip.name, data, "audio/mpeg")},
                data={
                    "alt_text": f"License-free music bed {n} (SoundHelix, CC0)",
                    "tags": f"music,soundhelix,bed-{n}",
                },
                headers=headers,
            )
        if r.status_code != 201:
            print(f"bed-{n}: upload FAILED HTTP {r.status_code} {r.text[:160]}")
            continue
        print(f"bed-{n}: uploaded asset {r.json()['id']}")


async def assign(post_ids: list[str], asset_id: str) -> None:
    async with async_session_maker() as db:
        for pid in post_ids:
            try:
                post = await db.get(Post, uuid.UUID(pid))
            except ValueError:
                post = None
            if post is None:
                print(f"{pid}: NOT FOUND")
                continue
            post.music_asset_id = uuid.UUID(asset_id)
            print(f"{pid}: music_asset_id={asset_id}")
        await db.commit()


async def list_music() -> None:
    async with async_session_maker() as db:
        rows = (await db.execute(
            select(MediaAsset.id, MediaAsset.filename, MediaAsset.tags)
            .where(MediaAsset.mime_type.like("audio/%"))
        )).all()
        for r in rows:
            print(r.id, r.filename, r.tags)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload-only", action="store_true")
    ap.add_argument("--assign", nargs="+", metavar="POST_ID")
    ap.add_argument("--asset", help="media_assets id to assign")
    ap.add_argument("--list-music", action="store_true")
    args = ap.parse_args()

    if args.list_music:
        asyncio.run(list_music())
    elif args.assign:
        if not args.asset:
            raise SystemExit("--assign requires --asset")
        asyncio.run(assign(args.assign, args.asset))
    else:
        asyncio.run(upload_all())


if __name__ == "__main__":
    main()
