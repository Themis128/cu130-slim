"""Unit tests for app.services.media_dedup — perceptual media dedup."""

import io
import uuid
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

import app.services.media_dedup as md
from app.models.content import StorageBackend
from app.services.media_dedup import (
    DUP_DISTANCE,
    compute_phash,
    ensure_asset_phash,
    load_asset_bytes,
    media_duplicate_reason,
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


# ── load_asset_bytes / ensure_asset_phash / media_duplicate_reason ───


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


class _DB:
    def __init__(self, results):
        self._q = deque(results)

    async def execute(self, q):
        return self._q.popleft()


def _asset(**kw):
    base = dict(
        id=uuid.uuid4(), storage_backend=StorageBackend.local,
        storage_path="a.png", mime_type="image/png", meta_data={},
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_load_asset_bytes_backends(monkeypatch):
    from app.services import minio_storage, r2_storage

    monkeypatch.setattr(r2_storage, "get_object", AsyncMock(return_value=b"r2"))
    asset = _asset(storage_backend=StorageBackend.r2)
    assert await load_asset_bytes(asset) == b"r2"

    monkeypatch.setattr(minio_storage, "get_object", AsyncMock(return_value=b"minio"))
    asset = _asset(storage_backend=StorageBackend.minio)
    assert await load_asset_bytes(asset) == b"minio"

    import app.core.path_utils as pu
    monkeypatch.setattr(pu, "safe_resolve", lambda b, p: SimpleNamespace(read_bytes=lambda: b"loc"))
    asset = _asset()
    assert await load_asset_bytes(asset) == b"loc"


@pytest.mark.asyncio
async def test_ensure_asset_phash(monkeypatch):
    db = _DB([])
    # existing phash returned without work
    a = _asset(meta_data={"phash": "abc123"})
    assert await ensure_asset_phash(db, a) == "abc123"

    # non-image -> None
    a2 = _asset(mime_type="video/mp4")
    assert await ensure_asset_phash(db, a2) is None

    # load failure -> None
    a3 = _asset()
    monkeypatch.setattr(md, "load_asset_bytes", AsyncMock(side_effect=RuntimeError("x")))
    assert await ensure_asset_phash(db, a3) is None

    # computes + persists
    a4 = _asset()
    monkeypatch.setattr(md, "load_asset_bytes", AsyncMock(return_value=b"img"))
    monkeypatch.setattr(md, "compute_phash", lambda b: "feed00")
    assert await ensure_asset_phash(db, a4) == "feed00"
    assert a4.meta_data["phash"] == "feed00"

    # compute returns falsy -> meta untouched
    a5 = _asset()
    monkeypatch.setattr(md, "compute_phash", lambda b: None)
    assert await ensure_asset_phash(db, a5) is None
    assert "phash" not in a5.meta_data


@pytest.mark.asyncio
async def test_media_duplicate_reason_empty_media_ids():
    post = SimpleNamespace(media_ids=None, id=uuid.uuid4())
    assert await media_duplicate_reason(_DB([]), post, uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_media_duplicate_reason_no_draft_hashes(monkeypatch):
    aid = uuid.uuid4()
    post = SimpleNamespace(media_ids=[aid, aid, uuid.uuid4()], id=uuid.uuid4())
    asset = _asset(id=aid)  # non-image loads -> no hash
    monkeypatch.setattr(md, "ensure_asset_phash", AsyncMock(return_value=None))
    db = _DB([_Res([asset])])
    assert await media_duplicate_reason(db, post, uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_media_duplicate_reason_no_prior(monkeypatch):
    aid = uuid.uuid4()
    post = SimpleNamespace(media_ids=[aid], id=uuid.uuid4())
    monkeypatch.setattr(md, "ensure_asset_phash", AsyncMock(return_value="aa"))
    db = _DB([_Res([_asset(id=aid)]), _Res([])])  # assets, prior posts media_ids
    assert await media_duplicate_reason(db, post, uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_media_duplicate_reason_prior_no_phashes(monkeypatch):
    aid, pid = uuid.uuid4(), uuid.uuid4()
    post = SimpleNamespace(media_ids=[aid], id=uuid.uuid4())
    monkeypatch.setattr(md, "ensure_asset_phash", AsyncMock(return_value="aa"))
    prior_asset = _asset(id=pid, meta_data={})  # no phash stored
    db = _DB([
        _Res([_asset(id=aid)]),
        _Res([[pid]]),          # prior media_ids
        _Res([prior_asset]),    # prior assets
    ])
    assert await media_duplicate_reason(db, post, uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_media_duplicate_reason_match_and_mismatch(monkeypatch):
    aid, pid = uuid.uuid4(), uuid.uuid4()
    post = SimpleNamespace(media_ids=[aid], id=uuid.uuid4())
    monkeypatch.setattr(md, "ensure_asset_phash", AsyncMock(return_value="aa"))
    prior = _asset(id=pid, meta_data={"phash": "aa"})

    # count mismatch: prior post had 2 phashed images, draft has 1
    pid2 = uuid.uuid4()
    prior2 = _asset(id=pid2, meta_data={"phash": "bb"})
    db = _DB([_Res([_asset(id=aid)]), _Res([[pid, pid2]]), _Res([prior, prior2])])
    assert await media_duplicate_reason(db, post, uuid.uuid4()) is None

    # same count + near hash -> duplicate
    db2 = _DB([_Res([_asset(id=aid)]), _Res([[pid]]), _Res([prior])])
    out = await media_duplicate_reason(db2, post, uuid.uuid4())
    assert "identical media" in out

    # distant hash -> no match (0xaa ^ 0xff00 = 12-bit distance > 8)
    prior_far = _asset(id=pid, meta_data={"phash": "ff00"})
    db3 = _DB([_Res([_asset(id=aid)]), _Res([[pid]]), _Res([prior_far])])
    assert await media_duplicate_reason(db3, post, uuid.uuid4()) is None

    # empty prior ids row skipped
    db4 = _DB([_Res([_asset(id=aid)]), _Res([None, [pid]]), _Res([prior])])
    out4 = await media_duplicate_reason(db4, post, uuid.uuid4())
    assert "identical media" in out4
