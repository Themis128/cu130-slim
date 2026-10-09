"""Perceptual-hash media deduplication.

Enforces the no-duplicated-posts rule for media: text dedup in
`duplicate_detector` catches identical copy, but the same rule forbids
repackaging a piece in a different format (e.g. carousel slides re-exported
as a video) or re-posting identical media. We hash each image asset with a
perceptual hash and compare a post's media set against media already
published to the same account.

Meta's PDQ/TMK (`facebook/ThreatExchange`) is the industry-standard
perceptual hash, but its bindings (`pdqhash`, `vpdq`) need a C++ toolchain
that the slim backend image lacks. `imagehash.phash` provides equivalent
64-bit perceptual hashing in pure Python. Hashes persist in
`MediaAsset.meta_data["phash"]` — no migration needed.
"""

from __future__ import annotations

import io
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import MediaAsset, Post, PostTarget

logger = logging.getLogger(__name__)

UPLOAD_DIR = "/app/uploads"
PHASH_META_KEY = "phash"
# Two phashes closer than this are near-identical (out of 64 bits).
DUP_DISTANCE = 8
# Window for "already published with this media" comparisons.
DUP_WINDOW_DAYS = 30


def compute_phash(image_bytes: bytes) -> str | None:
    """64-bit perceptual hash as hex, or None if unreadable/non-image."""
    try:
        import imagehash
        from PIL import Image

        return str(imagehash.phash(Image.open(io.BytesIO(image_bytes))))
    except Exception as exc:  # noqa: BLE001 - non-image assets are expected
        logger.debug("phash skipped: %s", exc)
        return None


def phash_distance(a: str, b: str) -> int:
    """Hamming distance between two phash hex strings."""
    return bin(int(a, 16) ^ int(b, 16)).count("1")


async def load_asset_bytes(asset: MediaAsset) -> bytes:
    """Load bytes from whichever storage backend holds the asset."""
    from app.models.content import StorageBackend
    from app.services import minio_storage, r2_storage

    if asset.storage_backend == StorageBackend.r2:
        return await r2_storage.get_object(asset.storage_path)
    if asset.storage_backend == StorageBackend.minio:
        return await minio_storage.get_object(asset.storage_path)
    from app.core.path_utils import safe_resolve

    return safe_resolve(UPLOAD_DIR, asset.storage_path).read_bytes()


async def ensure_asset_phash(db: AsyncSession, asset: MediaAsset) -> str | None:
    """Return the asset's stored phash, computing and persisting if missing."""
    meta = asset.meta_data or {}
    existing = meta.get(PHASH_META_KEY)
    if existing:
        return existing
    if not (asset.mime_type or "").startswith("image/"):
        return None
    try:
        data = await load_asset_bytes(asset)
    except Exception as exc:  # noqa: BLE001
        logger.warning("media_dedup: cannot load asset %s: %s", asset.id, exc)
        return None
    ph = compute_phash(data)
    if ph:
        asset.meta_data = {**meta, PHASH_META_KEY: ph}
    return ph


async def media_duplicate_reason(
    db: AsyncSession, post: Post, account_id, window_days: int = DUP_WINDOW_DAYS
) -> str | None:
    """Return a skip reason if this post's image set already shipped to the
    account inside `window_days`, else None.

    A hit requires *every* image on the draft to match a single already-
    published post's image set (all near-identical, same count) — partial
    overlap (shared logo, reused thumbnail) does not count.
    """
    media_ids = list(dict.fromkeys(post.media_ids or []))
    if not media_ids:
        return None

    assets = (
        (await db.execute(select(MediaAsset).where(MediaAsset.id.in_(media_ids))))
        .scalars()
        .all()
    )
    draft_hashes: set[str] = set()
    for asset in assets:
        ph = await ensure_asset_phash(db, asset)
        if ph:
            draft_hashes.add(ph)
    if not draft_hashes:
        return None

    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    prior_media_ids = (
        (
            await db.execute(
                select(Post.media_ids)
                .join(PostTarget, PostTarget.post_id == Post.id)
                .where(
                    PostTarget.social_account_id == account_id,
                    PostTarget.status == "published",
                    PostTarget.published_at >= cutoff,
                    Post.id != post.id,
                )
            )
        )
        .scalars()
        .all()
    )

    prior_ids: set = set()
    for ids in prior_media_ids:
        prior_ids.update(ids or [])
    if not prior_ids:
        return None

    prior_assets = (
        (await db.execute(select(MediaAsset).where(MediaAsset.id.in_(prior_ids))))
        .scalars()
        .all()
    )
    prior_phashes: dict[str, str] = {}
    for asset in prior_assets:
        ph = (asset.meta_data or {}).get(PHASH_META_KEY)
        if ph:
            prior_phashes[str(asset.id)] = ph

    if not prior_phashes:
        return None

    # Group prior hashes back per published post to require set-level match.
    for ids in prior_media_ids:
        if not ids:
            continue
        published_hashes = [
            prior_phashes[str(i)] for i in ids if str(i) in prior_phashes
        ]
        if len(published_hashes) != len(draft_hashes):
            continue
        if all(
            any(phash_distance(h, ph) <= DUP_DISTANCE for ph in published_hashes)
            for h in draft_hashes
        ):
            return (
                "Skipped: identical media already published to this account "
                f"within the last {window_days}d"
            )
    return None
