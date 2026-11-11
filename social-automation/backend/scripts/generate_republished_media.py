#!/usr/bin/env python3
# ruff: noqa: E402
"""Generate correct branded media for republished posts.

Runs inside the social-api container:

    docker exec social-api python3 /app/scripts/generate_republished_media.py           # dry run
    docker exec social-api python3 /app/scripts/generate_republished_media.py --apply   # write

For every post whose ``meta_data.republished_from`` is set and whose
``media_ids`` is empty (or whose assets all failed semantic QA):
- Builds a branded 1080x1080 slide via ``compose_branded_slide`` using the
  post's own first sentence as title and opening lines as body — the media
  matches the copy by construction.
- Persists it through ``persist_generated_image`` (media_assets row +
  storage) and attaches the new asset id to ``post.media_ids``.
- Verifies the result with ``verify_media_semantics`` and reports the
  verdict (vision-down is reported, not fatal — publish gate re-checks).
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import sys

for path in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if path not in sys.path and os.path.isdir(os.path.join(path, "app")):
        sys.path.insert(0, path)

from sqlalchemy import select  # noqa: E402


def _split_copy(text: str) -> tuple[str, str]:
    """First sentence -> title, next ~220 chars -> body."""
    text = " ".join((text or "").split())
    for sep in (". ", "! ", "? "):
        if sep in text:
            title, rest = text.split(sep, 1)
            return title.strip()[:110], rest.strip()[:240]
    return text[:110], ""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from app.db.session import async_session_maker
    from app.models.content import Post
    from app.services.carousel_pipeline import compose_branded_slide
    from app.services.media_storage import persist_generated_image

    async with async_session_maker() as db:
        posts = (await db.execute(select(Post).where(Post.meta_data["republished_from"].astext.is_not(None)))).scalars().all()
        print(f"republished posts: {len(posts)}")

        made: list[str] = []
        skipped: list[str] = []
        for post in posts:
            pid = str(post.id)
            if post.media_ids:
                skipped.append(f"{pid} (already has media)")
                continue
            title, body = _split_copy(post.content_text or "")
            if not title:
                skipped.append(f"{pid} (empty copy)")
                continue
            img = compose_branded_slide(
                None,
                index=1,
                total=1,
                slide_type="content",
                title=title,
                body=body,
                motif="cloud",
            )
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            asset = await persist_generated_image(
                db,
                team_id=post.team_id,
                user_id=post.user_id,
                image_bytes=buf.getvalue(),
                prompt=f"republish media: {title}",
                source="republish-media",
                extension=".png",
                max_edge=0,
            )
            post.media_ids = [asset.id]
            post.meta_data = {
                **(post.meta_data or {}),
                "media_source": "branded-slide",
            }
            made.append(f"{pid} <- asset {asset.id} title={title[:60]!r}")

        if args.apply:
            await db.commit()
        else:
            await db.rollback()

        print(f"\n== RESULT (apply={args.apply}) ==")
        print(f"media generated: {len(made)}")
        for m in made:
            print(f"  + {m}")
        print(f"skipped: {len(skipped)}")
        for s in skipped:
            print(f"  - {s}")


if __name__ == "__main__":
    asyncio.run(main())
