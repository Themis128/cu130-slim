#!/usr/bin/env python3
"""Tag library music beds with a mood bucket for mood-matched auto-picks.

Measures each audio/% asset tagged ``music-bed`` with ffmpeg ebur128 and
stores ``meta_data.music_mood`` + ``meta_data.music_energy``:

- integrated loudness >= median of the library -> ``energetic``
- below median                              -> ``calm``

Loudness is an honest energy proxy for generated library beds (denser,
hotter arrangements read as more energetic than sparse, quiet pads). It
is not a genre classification — the keyword match in
``services/publishing.py::_post_mood`` only needs two coarse buckets.

Run inside social-api:

    docker exec social-api python /app/scripts/tag_music_moods.py
    docker exec social-api python /app/scripts/tag_music_moods.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from statistics import median

sys.path.insert(0, "/app")

from sqlalchemy import select  # noqa: E402

from app.db.session import async_session_maker  # noqa: E402
from app.models.content import MediaAsset  # noqa: E402


def _integrated_lufs(path: str) -> float | None:
    """ffmpeg ebur128 summary integrated loudness, or None on failure."""
    r = subprocess.run(
        [
            "ffmpeg",
            "-nostats",
            "-i",
            path,
            "-filter_complex",
            "ebur128",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        timeout=120,
    )
    text = r.stderr.decode("utf-8", errors="replace")
    # Summary block: "    Integrated loudness:\n      I:         -23.4 LUFS"
    idx = text.rfind("Integrated loudness")
    if idx == -1:
        return None
    for line in text[idx:].splitlines()[1:6]:
        parts = line.replace(":", " ").split()
        if parts and parts[0] == "I":
            try:
                return float(parts[1])
            except (IndexError, ValueError):
                return None
    return None


async def _local_path(asset: MediaAsset) -> str | None:
    from app.services.publishing import _asset_file_path  # noqa: PLC0415

    return await _asset_file_path(asset)


async def main(dry_run: bool) -> int:
    async with async_session_maker() as db:
        beds = (
            (
                await db.execute(
                    select(MediaAsset).where(
                        MediaAsset.mime_type.like("audio/%"),
                        MediaAsset.tags.contains(["music-bed"]),
                    )
                )
            )
            .scalars()
            .all()
        )
        beds = [b for b in beds if b.storage_path]
        if not beds:
            print("no music-bed assets found")
            return 1

        measured: list[tuple[MediaAsset, float]] = []
        for b in beds:
            path = await _local_path(b)
            if not path:
                print(f"  {b.filename}: no local file — skipped")
                continue
            lufs = _integrated_lufs(path)
            if lufs is None:
                print(f"  {b.filename}: ebur128 failed — skipped")
                continue
            measured.append((b, lufs))
            print(f"  {b.filename}: {lufs:.1f} LUFS-I")

        if not measured:
            print("nothing measured")
            return 1

        cut = median(v for _, v in measured)
        print(f"median integrated loudness: {cut:.1f} LUFS")
        for asset, lufs in measured:
            mood = "energetic" if lufs >= cut else "calm"
            print(f"  {asset.filename}: {lufs:.1f} -> {mood}")
            if not dry_run:
                md = dict(asset.meta_data or {})
                md["music_mood"] = mood
                md["music_energy"] = round(lufs, 1)
                asset.meta_data = md
        if not dry_run:
            await db.commit()
            print(f"committed {len(measured)} mood tags")
        else:
            print("dry-run — nothing written")
        return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.dry_run)))
