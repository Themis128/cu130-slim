"""Coverage gaps: app.services.x_web residual lines/arcs."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from PIL import Image

import app.services.x_web as X

pytestmark = pytest.mark.asyncio


def _img(path, fmt="PNG", mode="RGB", **kw):
    Image.new(mode, (8, 8), (200, 30, 30) if mode == "RGB" else 128).save(path, fmt, **kw)
    return str(path)


# ── _to_jpeg ────────────────────────────────────────────────────────────


async def test_to_jpeg_reencode_and_oversize(tmp_path, monkeypatch):
    # PNG -> re-encode path; tiny MAX -> every quality exceeds -> raise
    src = _img(tmp_path / "a.png", "PNG")
    out = X._to_jpeg(src, str(tmp_path), 0)
    assert out.endswith("img0.jpg") and os.path.getsize(out) > 0

    monkeypatch.setattr(X, "MAX_IMAGE_BYTES", 10)
    (tmp_path / "b").mkdir()
    with pytest.raises(X.XWebMediaError):
        X._to_jpeg(src, str(tmp_path / "b"), 0)

    # baseline_rgb False (CMYK jpeg) -> Image.open re-encode path
    monkeypatch.undo()
    (tmp_path / "c").mkdir()
    cmyk = _img(tmp_path / "c.jpg", "JPEG", mode="CMYK")
    out2 = X._to_jpeg(cmyk, str(tmp_path / "c"), 0)
    assert out2.endswith("img0.jpg")


# ── _patch_tweety_home_fallback ─────────────────────────────────────────


async def test_patch_tweety_import_failure(monkeypatch):
    monkeypatch.setitem(sys.modules, "tweety.http", None)
    X._patch_tweety_home_fallback()  # must not raise


async def test_patch_tweety_patched_body(monkeypatch):
    import tweety.transaction as TT
    from tweety.http import Request

    calls = {}

    def _marker(s):
        return "MARKER" in s

    monkeypatch.setattr(TT, "find_on_demand_file", _marker)

    async def _orig(self):
        return "plain-home-no-marker"

    monkeypatch.setattr(Request, "get_home_html", _orig)
    X._patch_tweety_home_fallback()
    patched_fn = Request.get_home_html
    assert getattr(patched_fn, "_x_web_i_jf_patched", False)

    class _Sess:
        async def request(self, method, url, headers):
            calls["url"] = url
            return SimpleNamespace(status_code=200, content=b"<html>MARKER</html>")

    self = SimpleNamespace(_get_request_headers=lambda: {"authorization": "x"}, _session=_Sess())
    res = await patched_fn(self)
    assert calls["url"] == "https://x.com/i/jf/"
    assert "MARKER" in str(res)

    # non-2xx -> falls back to home_page
    class _Sess2:
        async def request(self, *a, **k):
            return SimpleNamespace(status_code=500, content=b"")

    self2 = SimpleNamespace(_get_request_headers=lambda: {}, _session=_Sess2())
    res2 = await patched_fn(self2)
    assert res2 == "plain-home-no-marker"

    # 200 but candidate lacks the on-demand file -> home_page
    class _Sess3:
        async def request(self, *a, **k):
            return SimpleNamespace(status_code=200, content=b"<html>nothing</html>")

    self3 = SimpleNamespace(_get_request_headers=lambda: {}, _session=_Sess3())
    res3 = await patched_fn(self3)
    assert res3 == "plain-home-no-marker"

    # second patch call -> already-patched early return
    X._patch_tweety_home_fallback()


# ── _find_recent_own_tweet arcs ─────────────────────────────────────────


async def test_find_recent_own_tweet_edge_arcs():
    now = datetime.now(UTC)
    old = now - timedelta(minutes=30)
    match = SimpleNamespace(id="555", text="hello world tweet", created_on=now)
    wrong_text = SimpleNamespace(id="556", text="different words entirely", created_on=now)
    no_created = SimpleNamespace(id="557", text="no date here", created_on=None)
    old_t = SimpleNamespace(id="558", text="hello world tweet old", created_on=old)

    class _C:
        async def get_tweets(self, ident, pages=1):
            return SimpleNamespace(
                tweets=[
                    SimpleNamespace(tweets=[no_created, wrong_text]),  # nested SelfThread
                    SimpleNamespace(tweets=[old_t]),
                    match,  # bare item (no .tweets) -> [item] path
                ]
            )

    out = await X._find_recent_own_tweet(_C(), "hello world tweet")
    assert out == "555"

    # nothing matches -> None
    class _C2:
        async def get_tweets(self, ident, pages=1):
            return []

    assert await X._find_recent_own_tweet(_C2(), "x") is None


# ── _from_tweety_tweet / _read_with_tweety arcs ─────────────────────────


def test_from_tweety_tweet_no_id_returns_none():
    assert X._from_tweety_tweet(SimpleNamespace(text="x")) is None


async def test_read_with_tweety_none_metric_arcs():
    class _C:
        async def get_user_info(self, u):
            return SimpleNamespace(followers_count=1, friends_count=1, statuses_count=1, listed_count=0)

        async def get_tweets(self, u, pages=1):
            # nested item whose tweets all lack ids -> m None arcs
            return SimpleNamespace(tweets=[SimpleNamespace(tweets=[SimpleNamespace(text="no id")])])

        async def tweet_detail(self, tid):
            return SimpleNamespace(text="still no id")  # no id -> m None

    out = await X._read_with_tweety(_C(), "u", None, ["111"], 1)
    assert out.tweets == {}


# ── _default_twscrape_api + _read_with_twscrape ─────────────────────────


async def test_default_twscrape_api_builds(monkeypatch, tmp_path):
    monkeypatch.setattr(X, "load_cookies", lambda: {"auth_token": "a", "ct0": "c"})
    api = await X._default_twscrape_api("user1")
    try:
        assert api._x_web_workdir
    finally:
        import shutil

        shutil.rmtree(api._x_web_workdir, ignore_errors=True)


async def test_read_with_twscrape_edges():
    # user not found -> RuntimeError
    class _ApiNone:
        async def user_by_login(self, u):
            return None

    with pytest.raises(RuntimeError, match="not found"):
        await X._read_with_twscrape(_ApiNone(), "ghost", None, [], 0)

    # user without id + non-digit user_id -> uid 0 -> skip user_tweets
    class _ApiNoId:
        async def user_by_login(self, u):
            return SimpleNamespace(followersCount=1, friendsCount=1, statusesCount=1, listedCount=0)

        async def user_tweets(self, uid, limit=-1):
            raise AssertionError("should not iterate")

        async def tweet_details(self, tid):
            return None

    out = await X._read_with_twscrape(_ApiNoId(), "u", "notdigits", ["111"], 1)
    assert out.tweets == {}

    # tweets yield no-id -> m None arc; details returns no-id -> m None arc
    class _ApiIds:
        async def user_by_login(self, u):
            return SimpleNamespace(id=42, followersCount=2, friendsCount=1, statusesCount=1, listedCount=0)

        async def user_tweets(self, uid, limit=-1):
            yield SimpleNamespace(text="no id")

        async def tweet_details(self, tid):
            return SimpleNamespace(text="no id either")

    out2 = await X._read_with_twscrape(_ApiIds(), "u", "42", ["9"], 1)
    assert out2.tweets == {}
