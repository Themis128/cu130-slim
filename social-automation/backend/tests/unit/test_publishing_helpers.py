"""Tests for app/services/publishing.py helpers + media pre-flight gate.

Covers PublishResult, X quota/capacity classification + retry headers,
FB-group/TikTok skip constructors, validate_media_for_platform (the
no-missing-media owner rule), dedup window, media/music path resolution,
PDF/image helpers, X weighted-length/thread split, and URL builders.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import publishing as P

# ── fakes ─────────────────────────────────────────────────────────────


class _Res:
    def __init__(self, v):
        self._v = v

    def scalar_one_or_none(self):
        return self._v

    def scalars(self):
        return self

    def first(self):
        if isinstance(self._v, list):
            return self._v[0] if self._v else None
        return self._v

    def all(self):
        return self._v if isinstance(self._v, list) else ([] if self._v is None else [self._v])


class _DB:
    def __init__(self, results=()):
        self._q = list(results)
        self.added = []

    async def execute(self, stmt):
        return _Res(self._q.pop(0) if self._q else None)

    def add(self, obj):
        self.added.append(obj)


class _XErr(Exception):
    def __init__(self, status_code=400, response_text="", headers=None):
        super().__init__(f"X error {status_code}")
        self.status_code = status_code
        self.response_text = response_text
        self.headers = headers or {}


def _img(path, w=800, h=800):
    # noise → file stays above the 10KB min-bytes gate even at small dims
    import os as _os

    from PIL import Image

    Image.frombytes("RGB", (w, h), _os.urandom(w * h * 3)).save(path)


# ── X error classification ────────────────────────────────────────────


def test_err_has():
    assert P._err_has("x: UsageCapExceeded", P._X_QUOTA_MARKERS)
    assert not P._err_has(None, P._X_QUOTA_MARKERS)
    assert not P._err_has("fine", P._X_QUOTA_MARKERS)


def test_is_x_quota_error():
    assert P._is_x_quota_error(_XErr(402))
    assert P._is_x_quota_error(_XErr(429))
    assert P._is_x_quota_error(_XErr(400, "monthly write cap hit"))
    assert not P._is_x_quota_error(_XErr(400, "duplicate content"))


def test_x_retry_at_headers():
    now = datetime.now(UTC)
    # 24h limit exhausted → reset honored
    e = _XErr(429, headers={"x-user-limit-24hour-reset": str(now.timestamp() + 3600), "x-user-limit-24hour-remaining": "0"})
    out = P._x_retry_at(e)
    assert out > now + timedelta(minutes=30)
    # retry-after honored when not capped
    e2 = _XErr(429, headers={"retry-after": "120"})
    out2 = P._x_retry_at(e2)
    assert abs((out2 - now).total_seconds() - 150) < 10
    # credits-depleted → no reset
    assert P._x_retry_at(_XErr(402, "credits-depleted", headers={"retry-after": "5"})) is None
    assert P._x_retry_at(None) is None
    # 24h reset ignored when remaining > 0
    e3 = _XErr(429, headers={"x-user-limit-24hour-reset": str(now.timestamp() + 99), "x-user-limit-24hour-remaining": "3"})
    assert P._x_retry_at(e3) is None


def test_x_capacity_backoff_future():
    out = P._x_capacity_backoff()
    assert out > datetime.now(UTC)


def test_skip_result_constructors():
    r = P._facebook_group_skip_result("boom")
    assert r.skipped and "deprecated" in r.error
    r2 = P._x_quota_skip_result()
    assert r2.skipped and "credits" in r2.error
    r3 = P._x_capacity_defer_result(retry_at=datetime.now(UTC))
    assert r3.retry_after and not r3.skipped
    r4 = P._skipped_media_result("nope")
    assert r4.skipped and r4.error == "nope"


def test_tiktok_clarify_error():
    assert "url_ownership_unverified" in P._tiktok_clarify_error("url_ownership_unverified")
    assert "audit" in P._tiktok_clarify_error("unaudited_client_can_only_post_to_private_accounts").lower()
    assert P._tiktok_clarify_error("other") == "other"


def test_fmt_bytes():
    assert P._fmt_bytes(2 * 1024**3) == "2GB"
    assert P._fmt_bytes(5 * 1024**2) == "5MB"


# ── validate_media_for_platform ───────────────────────────────────────


def test_validate_unknown_platform_passes():
    assert P.validate_media_for_platform("myspace", []) is None


def test_validate_incomplete_media_skips(tmp_path):
    f = tmp_path / "a.jpg"
    _img(f)
    out = P.validate_media_for_platform("instagram", [str(f)], expected=2)
    assert out.skipped and "1/2" in out.error


def test_validate_required_no_media_skips():
    out = P.validate_media_for_platform("threads", [])
    assert out.skipped and "no media" in out.error


def test_validate_max_count(tmp_path):
    files = [str(tmp_path / f"{i}.jpg") for i in range(5)]
    for f in files:
        _img(f)
    out = P.validate_media_for_platform("twitter", files)  # max 4
    assert out.skipped and "4" in out.error


def test_validate_pdf_not_allowed(tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 rest")
    out = P.validate_media_for_platform("instagram", [str(f)])
    assert out.skipped and "PDF" in out.error


def test_validate_missing_file():
    out = P.validate_media_for_platform("linkedin", ["/nonexistent/x.png"])
    assert out.skipped and "missing" in out.error


def test_validate_empty_pdf(tmp_path):
    f = tmp_path / "e.pdf"
    f.write_bytes(b"")
    out = P.validate_media_for_platform("linkedin", [str(f)])
    assert out.skipped and "empty" in out.error


def test_validate_bad_pdf_header(tmp_path):
    f = tmp_path / "b.pdf"
    f.write_bytes(b"NOTPDF" + b"x" * 20000)
    out = P.validate_media_for_platform("linkedin", [str(f)])
    assert out.skipped and "not a valid PDF" in out.error


def test_validate_linkedin_pdf_ok(tmp_path):
    f = tmp_path / "ok.pdf"
    f.write_bytes(b"%PDF-" + b"x" * 20000)
    assert P.validate_media_for_platform("linkedin", [str(f)]) is None


def test_validate_tiny_file_skips(tmp_path):
    f = tmp_path / "tiny.jpg"
    f.write_bytes(b"x" * 100)
    out = P.validate_media_for_platform("instagram", [str(f)])
    assert out.skipped and "bytes" in out.error


def test_validate_video_size_cap(tmp_path, monkeypatch):
    # shrink threads' 1GB video cap so the oversized branch is testable
    rules = dict(P._PLATFORM_MEDIA_RULES["threads"], max_video_bytes=15_000)
    monkeypatch.setitem(P._PLATFORM_MEDIA_RULES, "threads", rules)
    f = tmp_path / "big.mp4"
    f.write_bytes(b"x" * 20_000)  # > MIN_IMAGE_BYTES so it reaches the video branch
    out = P.validate_media_for_platform("threads", [str(f)])
    assert out.skipped and "video" in out.error.lower() and "limit" in out.error.lower()


def test_validate_video_bad_format(tmp_path):
    f = tmp_path / "clip.webm"  # bluesky accepts mp4 only
    f.write_bytes(b"x" * 20_000)
    out = P.validate_media_for_platform("bluesky", [str(f)])
    assert out.skipped and "format" in out.error


def test_validate_corrupt_image(tmp_path):
    f = tmp_path / "bad.png"
    f.write_bytes(b"not a png but long enough to pass min size" + b"x" * 20000)
    out = P.validate_media_for_platform("instagram", [str(f)])
    assert out.skipped and "decodable" in out.error


def test_validate_image_dims(tmp_path):
    small = tmp_path / "s.jpg"
    _img(small, 140, 140)  # >10KB of noise, width < instagram's 320px min
    out = P.validate_media_for_platform("instagram", [str(small)])  # min 320 width
    assert out.skipped and "below" in out.error

    wide = tmp_path / "w.jpg"
    _img(wide, 800, 100)  # ratio 8:1 > 1.91
    out2 = P.validate_media_for_platform("instagram", [str(wide)])
    assert out2.skipped and "aspect ratio" in out2.error


def test_validate_image_format_gate(tmp_path):
    f = tmp_path / "pic.gif"  # instagram doesn't accept gif
    _img(f, 800, 800)
    out = P.validate_media_for_platform("instagram", [str(f)])
    assert out.skipped and "format" in out.error


def test_validate_image_ok(tmp_path):
    f = tmp_path / "ok.jpg"
    _img(f, 800, 800)
    assert P.validate_media_for_platform("instagram", [str(f)]) is None
    assert P.validate_media_for_platform("threads", [str(f)]) is None


# ── dedup / media resolution ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_duplicate_by_media_ids(monkeypatch):
    shared = uuid.uuid4()
    post = SimpleNamespace(id=uuid.uuid4(), media_ids=[shared], content_text="a")
    other = SimpleNamespace(id=uuid.uuid4(), media_ids=[shared], content_text="b")
    acc = SimpleNamespace(id=uuid.uuid4(), platform="x")
    monkeypatch.setattr(P, "is_duplicate", lambda a, b, threshold: False)
    out = await P._find_duplicate_post(_DB(results=[[other]]), post, acc, "a")
    assert out is other


@pytest.mark.asyncio
async def test_find_duplicate_by_text(monkeypatch):
    post = SimpleNamespace(id=uuid.uuid4(), media_ids=[], content_text="same text")
    other = SimpleNamespace(id=uuid.uuid4(), media_ids=[], content_text="same text")
    acc = SimpleNamespace(id=uuid.uuid4(), platform="x")
    monkeypatch.setattr(P, "_build_post_text", lambda p, plat: "same text")
    monkeypatch.setattr(P, "is_duplicate", lambda a, b, threshold: True)
    out = await P._find_duplicate_post(_DB(results=[[other]]), post, acc, "same text")
    assert out is other


@pytest.mark.asyncio
async def test_find_duplicate_none(monkeypatch):
    post = SimpleNamespace(id=uuid.uuid4(), media_ids=[], content_text="unique")
    monkeypatch.setattr(P, "is_duplicate", lambda a, b, threshold: False)
    out = await P._find_duplicate_post(_DB(results=[[]]), post, SimpleNamespace(id=1, platform="x"), "unique")
    assert out is None


@pytest.mark.asyncio
async def test_resolve_media_paths_order_and_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    a1 = SimpleNamespace(id=uuid.uuid4(), storage_path="b.png", filename="b.png", storage_backend="local")
    a2 = SimpleNamespace(id=uuid.uuid4(), storage_path="a.png", filename="a.png", storage_backend="local")
    (tmp_path / "a.png").write_bytes(b"x")
    (tmp_path / "b.png").write_bytes(b"x")
    post = SimpleNamespace(media_ids=[a2.id, a1.id])
    out = await P._resolve_media_paths(post, _DB(results=[[a1, a2]]))
    assert out[0].endswith("a.png") and out[1].endswith("b.png")  # media_ids order


@pytest.mark.asyncio
async def test_resolve_media_paths_r2_fetch(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    a = SimpleNamespace(id=uuid.uuid4(), storage_path="r2/img.png", filename="img.png", storage_backend="r2")
    import app.services.r2_storage as r2

    monkeypatch.setattr(r2, "get_object", AsyncMock(return_value=b"imgbytes"))
    post = SimpleNamespace(media_ids=[a.id])
    out = await P._resolve_media_paths(post, _DB(results=[[a]]))
    assert len(out) == 1 and open(out[0], "rb").read() == b"imgbytes"


@pytest.mark.asyncio
async def test_resolve_media_paths_empty():
    assert await P._resolve_media_paths(SimpleNamespace(media_ids=[]), _DB()) == []


@pytest.mark.asyncio
async def test_resolve_media_storage_paths():
    a = SimpleNamespace(id=uuid.uuid4(), storage_path="k/x.png")
    b = SimpleNamespace(id=uuid.uuid4(), storage_path=None)
    post = SimpleNamespace(media_ids=[a.id, b.id])
    out = await P._resolve_media_storage_paths(post, _DB(results=[[a, b]]))
    assert out == ["k/x.png"]


@pytest.mark.asyncio
async def test_resolve_music_path(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    a = SimpleNamespace(id=uuid.uuid4(), storage_path="song.mp3", filename="song.mp3", storage_backend="local")
    (tmp_path / "song.mp3").write_bytes(b"mp3")
    out = await P._resolve_music_path(SimpleNamespace(music_asset_id=a.id), _DB(results=[a]))
    assert out.endswith("song.mp3")
    assert await P._resolve_music_path(SimpleNamespace(music_asset_id=None), _DB()) is None


@pytest.mark.asyncio
async def test_mix_audio_no_ffmpeg(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda x: None)
    assert await P._mix_audio_into_video("/v.mp4", "/a.mp3") == "/v.mp4"


# ── text / url helpers ────────────────────────────────────────────────


def test_build_post_text_delegates(monkeypatch):
    import app.services.content_renderer as cr

    monkeypatch.setattr(cr, "render_post_text", lambda p, plat: f"rendered:{plat}")
    assert P._build_post_text(SimpleNamespace(), "twitter") == "rendered:twitter"


def test_images_to_pdf(tmp_path):
    f = tmp_path / "p.png"
    _img(f, 100, 100)
    out = P._images_to_pdf([str(f)])
    assert out.startswith(b"%PDF")


def test_media_public_url(monkeypatch):
    monkeypatch.setattr(P._settings, "MEDIA_PUBLIC_BASE_URL", "https://m.example.com")
    out = P._media_public_url("path/x.png")
    assert out == "https://m.example.com/api/v1/media/view?path=path/x.png"
    out2 = P._media_public_url("path/x.png", force_jpeg=True)
    assert out2.endswith("&format=jpeg")


def test_media_public_url_r2_fallback(monkeypatch):
    monkeypatch.setattr(P._settings, "MEDIA_PUBLIC_BASE_URL", "")
    monkeypatch.setattr(P._settings, "R2_PUBLIC_URL", "https://r2.example.com")
    # no tunnel file → falls through to R2
    monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    out = P._media_public_url("key/x.png")
    assert out == "https://r2.example.com/key/x.png"
    assert P._media_public_url("/abs/x.png") is None  # abs path can't be R2 key


@pytest.mark.asyncio
async def test_x_media_alt_texts(monkeypatch):
    ids = [uuid.uuid4(), uuid.uuid4()]
    a1 = SimpleNamespace(id=ids[0], alt_text="first")
    a2 = SimpleNamespace(id=ids[1], alt_text=None)
    post = SimpleNamespace(media_ids=ids)
    out = await P._x_media_alt_texts(post, _DB(results=[[a2, a1]]))
    assert out == ["first", None]
    assert await P._x_media_alt_texts(post, None) == []


def test_x_weighted_len():
    assert P._x_weighted_len("hello") == 5
    assert P._x_weighted_len("hi \U0001f600") == 5  # emoji weighs 2
    assert P._x_weighted_len("see https://example.com/long-url-here now") == 8 + 23


def test_fit_x_limit():
    assert P._fit_x_limit("short") == "short"
    long = " ".join(["word"] * 100)
    out = P._fit_x_limit(long, 50)
    assert len(out) <= 50


def test_split_thread():
    assert P._split_thread("short") == ["short"]
    tweets = P._split_thread(" ".join(["w"] * 600), 20)
    assert len(tweets) > 1
    assert all(len(t) <= 20 for t in tweets)


def test_linkedin_author_urn():
    acc = SimpleNamespace(meta_data={"author_urn": "urn:li:organization:9"}, account_id="x")
    assert P._linkedin_author_urn(acc, SimpleNamespace()) == "urn:li:organization:9"
    acc2 = SimpleNamespace(meta_data={"account_type": "organization"}, account_id="42")
    client = SimpleNamespace(_author_urn=lambda aid, t: f"urn:li:{t}:{aid}")
    assert P._linkedin_author_urn(acc2, client) == "urn:li:organization:42"


def test_browser_session_flags():
    assert P._has_linkedin_browser_session(SimpleNamespace(meta_data={"browser_storage_state": {"x": 1}})) is True
    assert P._has_linkedin_browser_session(SimpleNamespace(meta_data={})) is False


def test_sidecar_file_path():
    assert P._sidecar_file_path("/app/uploads/2026/01/a.png") == "/uploads/2026/01/a.png"
    assert P._sidecar_file_path("/uploads/x.png") == "/uploads/x.png"
    assert P._sidecar_file_path("uploads/y.png") == "/uploads/y.png"
    assert P._sidecar_file_path("rel/z.png") == "/uploads/rel/z.png"
    assert P._sidecar_file_path("/elsewhere/f.png") is None
    assert P._sidecar_file_path("") is None
