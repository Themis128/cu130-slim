"""Owner rule: every post goes out with exactly the media it was given.

Each platform path must *fail* (never silently degrade to text-only or to a
partial album/carousel) when some of the post's media cannot be uploaded or
exposed via a public URL, and mixed sets a platform would silently trim are
refused up front.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import publishing as P
from tests.unit.test_publishing import _IGClient
from tests.unit.test_publishing_flow import _account, _FBHttp, _post
from tests.unit.test_tail_coverage3 import (
    _base_patches,
    _mkmedia,
    _tg_account,
    _tg_db,
    _tg_post,
)


def _files(tmp_path, *names):
    out = []
    for n in names:
        f = tmp_path / n
        f.write_bytes(b"\x89PNG" + b"\x00" * 12000)
        out.append(str(f))
    return out


# ── central pre-flight: mixed sets a platform would trim ────────────────────


@pytest.mark.parametrize("platform", ["linkedin", "facebook", "bluesky", "tiktok"])
def test_video_mixed_with_images_is_refused(platform):
    r = P.validate_media_for_platform(platform, ["/m/clip.mp4", "/m/a.jpg"])
    assert r is not None and not r.success and r.skipped
    assert "one video and nothing else" in r.error and "1 video" in r.error


def test_linkedin_pdf_mixed_with_images_is_refused():
    r = P.validate_media_for_platform("linkedin", ["/m/deck.pdf", "/m/a.jpg"])
    assert r is not None and not r.success and r.skipped
    assert "1 PDF(s) and 1 other item(s)" in r.error


def test_mixed_sets_still_allowed_where_platform_supports_them():
    # Instagram/Threads carousels mix images and videos natively — no refusal
    # from the single-kind rule (the per-file checks then run as usual).
    for plat in ("instagram", "threads"):
        r = P.validate_media_for_platform(plat, ["/m/clip.mp4", "/m/a.jpg"])
        assert r is None or "nothing else" not in (r.error or "")


# ── Facebook Graph: partial album ───────────────────────────────────────────


class _FBHttpSeq(_FBHttp):
    seq: list = []

    async def post(self, url, data=None, files=None):
        _FBHttp.posts.append((url, data, bool(files)))
        payload = {"id": "alb"} if url.endswith("/feed") else _FBHttpSeq.seq.pop(0)
        return SimpleNamespace(status_code=200, text=str(payload), json=lambda: payload, raise_for_status=lambda: None)


@pytest.mark.asyncio
async def test_fb_partial_album_refuses_to_publish(monkeypatch, tmp_path):
    paths = _files(tmp_path, "a.png", "b.png")
    monkeypatch.setattr(P, "_has_facebook_browser_session", lambda acc: False)
    monkeypatch.setattr(P, "_facebook_page_token", AsyncMock(return_value="ptok"))
    create_post = AsyncMock()
    monkeypatch.setattr(P, "FacebookAPIClient", lambda **kw: SimpleNamespace(create_post=create_post))
    monkeypatch.setattr(P.httpx, "AsyncClient", _FBHttpSeq)
    monkeypatch.setattr(_FBHttp, "posts", [])
    monkeypatch.setattr(_FBHttpSeq, "seq", [{"id": "ph1"}, {}])
    r = await P._publish_facebook("t", "hi", _account("facebook", account_id="pg1"), _post(), paths, None)
    assert not r.success and "1/2" in r.error
    assert not any(u.endswith("/feed") for u, *_ in _FBHttp.posts)
    create_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_fb_full_album_still_publishes(monkeypatch, tmp_path):
    paths = _files(tmp_path, "a.png", "b.png")
    monkeypatch.setattr(P, "_has_facebook_browser_session", lambda acc: False)
    monkeypatch.setattr(P, "_facebook_page_token", AsyncMock(return_value="ptok"))
    monkeypatch.setattr(P.httpx, "AsyncClient", _FBHttpSeq)
    monkeypatch.setattr(_FBHttp, "posts", [])
    monkeypatch.setattr(_FBHttpSeq, "seq", [{"id": "ph1"}, {"id": "ph2"}])
    r = await P._publish_facebook("t", "hi", _account("facebook", account_id="1234567890"), _post(), paths, None)
    assert r.success and r.platform_post_id == "alb"
    feed = [d for u, d, _ in _FBHttp.posts if u.endswith("/feed")]
    assert len(feed) == 1 and "ph1" in str(feed[0]) and "ph2" in str(feed[0])


# ── Instagram Graph: partial carousel ───────────────────────────────────────


@pytest.mark.asyncio
async def test_instagram_partial_public_urls_refuses(monkeypatch):
    built = []
    monkeypatch.setattr(P, "InstagramAPIClient", lambda **kw: built.append(kw) or _IGClient())
    monkeypatch.setattr(P, "_instagram_public_urls", AsyncMock(return_value=["https://cdn/a.jpg"]))
    acct = SimpleNamespace(account_id="ig1", username="me", meta_data={"account_type": "business"})
    post = SimpleNamespace(platform_specific={})
    r = await P._publish_instagram_via_graph("tok", "cap", acct, post, ["/a.jpg", "/b.jpg"], ["sp/a.jpg", "sp/b.jpg"], None)
    assert not r.success and r.skipped and "1/2" in r.error and "missing media" in r.error
    assert built == []  # never reached the API client


# ── TikTok photo mode: partial URLs ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_tiktok_photo_partial_public_urls_refuses(monkeypatch):
    client = SimpleNamespace(
        get_creator_info=AsyncMock(return_value={"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE"]}}),
        init_photo_post=AsyncMock(),
    )
    monkeypatch.setattr(P, "TikTokAPIClient", lambda **_: client)
    monkeypatch.setattr(P, "_media_public_url", lambda p, **kw: None if p.endswith("b.jpg") else f"https://v/{p}")
    acct = SimpleNamespace(account_id="open-1", username="c", meta_data={})
    r = await P._publish_tiktok("tok", "cap", acct, SimpleNamespace(platform_specific={}), ["a.jpg", "b.jpg"], ["sp/a.jpg", "sp/b.jpg"])
    assert not r.success and r.skipped and "1/2" in r.error
    client.init_photo_post.assert_not_awaited()


# ── Telegram: partial album ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_telegram_partial_album_refuses(monkeypatch, tmp_path):
    media, storage = _mkmedia(tmp_path, ["a.jpg", "b.jpg"])
    group = AsyncMock()
    photo = AsyncMock()
    client = SimpleNamespace(send_media_group=group, send_photo=photo)
    P2 = _base_patches(
        monkeypatch,
        media=media,
        storage=storage,
        client=client,
        url_map=lambda sp, **kw: None if sp.endswith("b.jpg") else f"https://x/{sp}",
    )
    r = await P2.publish_to_platform(_tg_account(), _tg_post(), _tg_db())
    assert not r.success and r.skipped and "1/2 resolved" in r.error
    group.assert_not_awaited()
    photo.assert_not_awaited()
