#!/usr/bin/env python3
# ruff: noqa: E402
"""Backfill/audit perceptual hashes on media_assets.

Runs inside the social-api container:

    docker exec social-api python3 /app/scripts/dedup_scan.py            # dry run
    docker exec social-api python3 /app/scripts/dedup_scan.py --write    # persist phashes

Prints a near-duplicate report (pairs within DUP_DISTANCE).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

for path in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if path not in sys.path and os.path.isdir(os.path.join(path, "app")):
        sys.path.insert(0, path)

from sqlalchemy import select  # noqa: E402


async def main(write: bool) -> None:
    from app.db.session import async_session_maker
    from app.models.content import MediaAsset
    from app.services.media_dedup import (
        DUP_DISTANCE,
        PHASH_META_KEY,
        ensure_asset_phash,
        phash_distance,
    )

    async with async_session_maker() as db:
        assets = (
            (await db.execute(select(MediaAsset).where(MediaAsset.is_archived.is_(False))))
            .scalars()
            .all()
        )
        hashed: dict[str, str] = {}
        computed = skipped = 0
        for asset in assets:
            before = (asset.meta_data or {}).get(PHASH_META_KEY)
            ph = await ensure_asset_phash(db, asset)
            if ph is None:
                skipped += 1
                continue
            hashed[str(asset.id)] = ph
            if before != ph:
                computed += 1
        if write:
            await db.commit()
        print(
            f"assets={len(assets)} hashed={len(hashed)} "
            f"newly_computed={computed} skipped={skipped} write={write}"
        )

        ids = list(hashed)
        pairs = []
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                d = phash_distance(hashed[ids[i]], hashed[ids[j]])
                if d <= DUP_DISTANCE:
                    pairs.append((d, ids[i], ids[j]))
        pairs.sort()
        for d, a, b in pairs[:50]:
            print(f"dup? distance={d} {a} <-> {b}")
        if not pairs:
            print("no near-duplicate pairs")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="persist computed phashes")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.write)))
