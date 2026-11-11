#!/usr/bin/env python3
# ruff: noqa: E402
"""Attach only semantically-correct media to republished posts.

Runs inside the social-api container:

    docker exec social-api python3 /app/scripts/fix_republished_media.py           # dry run
    docker exec social-api python3 /app/scripts/fix_republished_media.py --apply   # write

For every post whose ``meta_data.republished_from`` is set:
- Resolves the ORIGINAL post's media assets to local paths.
- Runs ``verify_media_semantics`` (vision QA) against the NEW copy — the same
  judge the publish-time ``media_semantic_gate`` uses.
- Keeps only assets that match; writes the surviving ids to
  ``new_post.media_ids``.
- Posts ending with zero matching assets are listed as NEEDS-MEDIA — they
  must get fresh generated/branded media before publishing (text-only is
  not acceptable per owner directive).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from types import SimpleNamespace

for path in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if path not in sys.path and os.path.isdir(os.path.join(path, "app")):
        sys.path.insert(0, path)

from sqlalchemy import select  # noqa: E402

_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from app.db.session import async_session_maker
    from app.models.content import Post
    from app.services.media_ai import verify_media_semantics
    from app.services.publishing import _resolve_media_paths, _vision_safe_image_bytes

    async with async_session_maker() as db:
        republished = (await db.execute(select(Post).where(Post.meta_data["republished_from"].astext.is_not(None)))).scalars().all()
        print(f"republished posts: {len(republished)}")

        needs_media: list[str] = []
        attached: list[str] = []
        for new_post in republished:
            src_id = new_post.meta_data.get("republished_from")
            src = await db.get(Post, uuid.UUID(str(src_id)))
            if src is None or not src.media_ids:
                needs_media.append(f"{new_post.id} (no source media)")
                continue

            good: list[uuid.UUID] = []
            checked = 0
            for aid in src.media_ids:
                shim = SimpleNamespace(media_ids=[aid])
                paths = await _resolve_media_paths(shim, db)  # type: ignore[arg-type]
                image_paths = [p for p in paths if p.lower().endswith(_IMAGE_EXTS)]
                if not image_paths:
                    continue
                checked += 1
                path = image_paths[0]
                try:
                    image_bytes = _vision_safe_image_bytes(path)
                except Exception:
                    continue
                try:
                    qa = await verify_media_semantics(
                        image_bytes,
                        (new_post.content_text or "")[:400],
                        media_type="image",
                    )
                except Exception as exc:
                    print(f"  ! {new_post.id} asset {aid}: QA error {exc!r}")
                    continue
                if qa.get("match"):
                    good.append(aid)
                    break  # one verified image is enough per post

            if good:
                new_post.media_ids = good
                attached.append(f"{new_post.id} <- {[str(g) for g in good]} (checked {checked})")
            else:
                needs_media.append(f"{new_post.id} (checked {checked} assets, 0 match)")

        if args.apply:
            await db.commit()
        else:
            await db.rollback()

        print(f"\n== RESULT (apply={args.apply}) ==")
        print(f"media attached: {len(attached)}")
        for a in attached:
            print(f"  + {a}")
        print(f"needs fresh media: {len(needs_media)}")
        for n in needs_media:
            print(f"  ! {n}")


if __name__ == "__main__":
    asyncio.run(main())
