"""Unit tests for app.services.media_dedup — perceptual media dedup."""

import io

import pytest
from PIL import Image

from app.services.media_dedup import (
    DUP_DISTANCE,
    compute_phash,
    phash_distance,
)


def _png(seed=None, noise=False):
    im = Image.new("RGB", (64, 64), (128, 128, 128))
    if noise:
        import random

        rng = random.Random(seed)
        px = im.load()
        for x in range(64):
            for y in range(64):
                px[x, y] = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_phash_deterministic():
    h1 = compute_phash(_png())
    h2 = compute_phash(_png())
    assert h1 == h2
    assert len(h1) == 16  # 64-bit hex


def test_distinct_images_hash_differently():
    h1 = compute_phash(_png(seed=1, noise=True))
    h2 = compute_phash(_png(seed=2, noise=True))
    assert h1 and h2 and h1 != h2
    assert phash_distance(h1, h2) > DUP_DISTANCE


def test_near_identical_images_match():
    import random

    base = _png(seed=7, noise=True)
    im = Image.open(io.BytesIO(base)).copy()
    rng = random.Random(9)
    px = im.load()
    for _ in range(30):  # flip a few pixels
        x, y = rng.randrange(64), rng.randrange(64)
        px[x, y] = (255, 255, 255)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    h1 = compute_phash(base)
    h2 = compute_phash(buf.getvalue())
    assert phash_distance(h1, h2) <= DUP_DISTANCE


def test_non_image_returns_none():
    assert compute_phash(b"not an image") is None
    assert compute_phash(b"") is None


def test_distance_symmetry_and_bounds():
    h1 = compute_phash(_png(seed=3, noise=True))
    h2 = compute_phash(_png(seed=4, noise=True))
    assert phash_distance(h1, h2) == phash_distance(h2, h1)
    assert 0 <= phash_distance(h1, h2) <= 64


@pytest.mark.asyncio
async def test_media_duplicate_reason_no_media():
    from app.services.media_dedup import media_duplicate_reason

    class _Post:
        media_ids = []
        id = "p1"

    reason = await media_duplicate_reason(None, _Post(), "acct")
    assert reason is None
