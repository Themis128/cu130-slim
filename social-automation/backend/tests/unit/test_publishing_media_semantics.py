"""Regression: the 2026-11-06 LinkedIn incident — an AI image generated from
the raw playbook instruction ("LinkedIn - 08:00 - text - …") shipped on a
fixed-price offer post. ``validate_media_for_platform`` only proves an asset
*can* publish; ``_enforce_media_semantics`` proves it *matches the copy*.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import publishing as P


def _post(content="Cloudless offers a fixed price with no long-term contracts.", media_ids=None, ps=None):
    return SimpleNamespace(
        id="post-1",
        content_text=content,
        media_ids=media_ids if media_ids is not None else ["m1"],
        platform_specific=ps or {},
    )


def _img(tmp_path, name="a.jpg"):
    f = tmp_path / name
    f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 5000)
    return str(f)


@pytest.mark.asyncio
async def test_semantic_match_passes_and_caches(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.media_ai.verify_media_semantics",
        AsyncMock(return_value={"match": True, "caption": "laptop dashboard", "reason": "YES"}),
    )
    post = _post()
    res = await P._enforce_media_semantics(post, [_img(tmp_path)], "fallback text")
    assert res is None
    gate = post.platform_specific["media_semantic_gate"]
    assert gate["passed"] is True and gate["content_hash"]


@pytest.mark.asyncio
async def test_semantic_mismatch_skips_with_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.media_ai.verify_media_semantics",
        AsyncMock(return_value={"match": False, "caption": "LinkedIn marketing slide", "reason": "NO — unrelated"}),
    )
    post = _post()
    res = await P._enforce_media_semantics(post, [_img(tmp_path)], "fallback text")
    assert res is not None and not res.success and res.skipped
    assert "semantic QA" in res.error.lower() or "does not match" in res.error.lower()
    assert post.platform_specific["media_semantic_gate"]["passed"] is False


@pytest.mark.asyncio
async def test_cached_verdict_skips_vision_call(monkeypatch, tmp_path):
    qa = AsyncMock(return_value={"match": False, "caption": "x", "reason": "NO"})
    monkeypatch.setattr("app.services.media_ai.verify_media_semantics", qa)
    post = _post()
    paths = [_img(tmp_path)]
    first = await P._enforce_media_semantics(post, paths, "t")
    assert first is not None
    qa.reset_mock()
    # second call with same content+media hits the cache — no vision call
    second = await P._enforce_media_semantics(post, paths, "t")
    assert second is not None and second.skipped
    qa.assert_not_called()


@pytest.mark.asyncio
async def test_no_images_or_no_copy_returns_none(monkeypatch, tmp_path):
    qa = AsyncMock()
    monkeypatch.setattr("app.services.media_ai.verify_media_semantics", qa)
    assert await P._enforce_media_semantics(_post(), ["/v/clip.mp4"], "text") is None
    assert await P._enforce_media_semantics(_post(content="", media_ids=[]), [], "") is None
    qa.assert_not_called()


@pytest.mark.asyncio
async def test_vision_unavailable_fails_open(monkeypatch, tmp_path):
    # verify_media_semantics itself returns match=True on infra failure —
    # the gate must never block publishing when DMR/vision is down.
    monkeypatch.setattr(
        "app.services.media_ai.verify_media_semantics",
        AsyncMock(return_value={"match": True, "caption": None, "reason": "vision unavailable"}),
    )
    post = _post()
    assert await P._enforce_media_semantics(post, [_img(tmp_path)], "t") is None


@pytest.mark.asyncio
async def test_disabled_flag_skips_gate(monkeypatch, tmp_path):
    qa = AsyncMock()
    monkeypatch.setattr("app.services.media_ai.verify_media_semantics", qa)
    monkeypatch.setattr(
        P,
        "get_settings",
        lambda: SimpleNamespace(PUBLISH_MEDIA_SEMANTIC_QA=False, PUBLISH_MEDIA_QA_MAX_IMAGES=3),
    )
    post = _post()
    assert await P._enforce_media_semantics(post, [_img(tmp_path)], "text") is None
    qa.assert_not_called()
    assert "media_semantic_gate" not in post.platform_specific


@pytest.mark.asyncio
async def test_cached_pass_skips_vision_call(monkeypatch, tmp_path):
    qa = AsyncMock(return_value={"match": True, "caption": "ok", "reason": "YES"})
    monkeypatch.setattr("app.services.media_ai.verify_media_semantics", qa)
    post = _post()
    paths = [_img(tmp_path)]
    assert await P._enforce_media_semantics(post, paths, "t") is None
    qa.reset_mock()
    # cached passed verdict → early return, no second vision call
    assert await P._enforce_media_semantics(post, paths, "t") is None
    qa.assert_not_called()


def test_vision_safe_image_bytes_reencodes_non_jpeg(tmp_path):
    """Non-JPEG / oversized sources go through the PIL downscale path and
    come back as JPEG bytes for the vision model."""
    from PIL import Image

    src = tmp_path / "big.png"
    Image.new("RGB", (64, 64), (10, 20, 30)).save(src, format="PNG")
    out = P._vision_safe_image_bytes(str(src), max_edge=32)
    assert out[:3] == b"\xff\xd8\xff"  # JPEG magic

    # small JPEG passthrough — returned verbatim, no re-encode
    small = _img(tmp_path, "small.jpg")
    raw = open(small, "rb").read()
    assert P._vision_safe_image_bytes(small) == raw
