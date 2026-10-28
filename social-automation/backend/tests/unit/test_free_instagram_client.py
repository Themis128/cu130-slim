"""Unit tests for app/services/free_instagram_client.py."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.services.free_instagram_client as FC


def _client():
    with patch.object(FC, "get_settings",
                      return_value=SimpleNamespace(
                          INSTAGRAM_PRIVATE_API_URL="http://side")):
        return FC.FreeInstagramClient()


class _Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code = status
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


class _AC:
    def __init__(self, outcome):
        self.outcome = outcome

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, headers=None):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


# ── pure helpers ────────────────────────────────────────────────────


def test_unabbrev():
    assert FC._unabbrev("104M") == 104000000
    assert FC._unabbrev("4,853") == 4853
    assert FC._unabbrev("1.5K") == 1500
    assert FC._unabbrev("2B") == 2000000000
    assert FC._unabbrev("abc") is None
    assert FC._unabbrev("12x") is None


def test_decode_unicode_escapes():
    assert FC._decode_unicode_escapes("hello") == "hello"
    assert FC._decode_unicode_escapes("bad\\xescape!!!") == "bad\\xescape!!!"


def test_parse_og():
    html = ('<meta property="og:description" content="100 Followers, '
            '5 Following, 20 Posts - See Instagram photos and videos '
            'from Cloudless (@cloudless.gr)">')
    out = FC._parse_og(html)
    assert out["follower_count"] == 100
    assert out["following_count"] == 5
    assert out["media_count"] == 20
    assert out["username"] is None  # 'from Cloudless (@x)' variant
    assert out["full_name"] == "Cloudless"
    out2 = FC._parse_og(
        '<meta property="og:description" content="3 Followers, 1 '
        'Following, 0 Posts - videos from @someone">')
    assert out2["username"] == "someone"
    assert FC._parse_og("<html></html>") is None
    assert FC._parse_og('<meta property="og:description" content="no counts">') is None


def test_parse_embedded():
    html = ('{"follower_count": 321, "following_count": 10, '
            '"media_count": 7, "biography": "the bio\\\\nline", '
            '"profile_pic_url": "https:\\\\/\\\\/pic"}')
    out = FC._parse_embedded(html)
    assert out["follower_count"] == 321
    assert out["following_count"] == 10
    assert out["media_count"] == 7
    assert "biography" in out and "profile_pic_url" in out
    assert FC._parse_embedded("{}") is None


def test_parse_og_image_and_meta_description():
    html = '<meta property="og:image" content="https://img/x.jpg">'
    assert FC._parse_og_image(html) == "https://img/x.jpg"
    assert FC._parse_og_image("") is None

    desc = ('<meta name="description" content="0 Followers, 0 Following, '
            '10 Posts - @cloudless.gr on Instagram: &quot;cool bio&quot;&quot;">')
    assert FC._parse_meta_description(desc) == "cool bio"
    assert FC._parse_meta_description("") is None


# ── client tiers ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sidecar_path():
    c = _client()
    fake = _AC(_Resp(200, {"pk": "1", "username": "x"}))
    with patch.object(FC.httpx, "AsyncClient", return_value=fake):
        out = await c.get_user_by_username("x")
    assert out["pk"] == "1" and out["_method"] == "sidecar_anon"

    fake = _AC(_Resp(200, {"pk": "2"}))
    with patch.object(FC.httpx, "AsyncClient", return_value=fake):
        out = await c.get_user_by_id(2)
    assert out["_method"] == "sidecar_anon"


@pytest.mark.asyncio
async def test_html_fallback_and_errors():
    c = _client()
    html = ('<meta property="og:description" content="9 Followers, 1 '
            'Following, 2 Posts - from @x"><meta property="og:image" '
            'content="https://i">')
    outcomes = [
        _Resp(404),                      # sidecar miss
        _Resp(200, text=html),           # html hit
    ]
    class _Seq:
        def __init__(self):
            self._it = iter(outcomes)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, *a, **kw):
            return next(self._it)
    with patch.object(FC.httpx, "AsyncClient",
                      return_value=_Seq()):
        out = await c.get_user_by_username("x")
    assert out["_method"] == "html_scraper"
    assert out["follower_count"] == 9
    assert out["profile_pic_url"] == "https://i"

    # sidecar error + login wall → FreeInstagramError
    wall = "<html><body>Please log in to Instagram</body></html>"
    class _Seq2(_Seq):
        pass
    outcomes2 = [RuntimeError("boom"), _Resp(200, text=wall)]
    seq2 = _Seq2()
    seq2._it = iter(outcomes2)
    with patch.object(FC.httpx, "AsyncClient", return_value=seq2):
        with pytest.raises(FC.FreeInstagramError):
            await c.get_user_by_username("x")

    # sidecar miss + html miss → error for by_id too
    with patch.object(FC.httpx, "AsyncClient",
                      return_value=_AC(_Resp(500))):
        with pytest.raises(FC.FreeInstagramError):
            await c.get_user_by_id(9)


@pytest.mark.asyncio
async def test_get_user_medias_shapes():
    c = _client()
    for outcome, want in [
        (_Resp(200, [{"m": 1}]), [{"m": 1}]),
        (_Resp(200, {"medias": [{"m": 2}]}), [{"m": 2}]),
        (_Resp(200, {"items": [{"m": 3}]}), [{"m": 3}]),
        (_Resp(200, {}), []),
        (_Resp(500), []),
        (RuntimeError("x"), []),
    ]:
        with patch.object(FC.httpx, "AsyncClient",
                          return_value=_AC(outcome)):
            assert await c.get_user_medias("u") == want


def test_enabled():
    assert _client().enabled is True


def test_unabbrev_bad_float():
    assert FC._unabbrev("1.2.3") is None


@pytest.mark.asyncio
async def test_html_non200_and_meta_bio():
    c = _client()
    # html non-200 → both tiers miss → error
    class _Seq:
        def __init__(self, outs):
            self._it = iter(outs)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, *a, **kw):
            n = next(self._it)
            if isinstance(n, Exception):
                raise n
            return n

    with patch.object(FC.httpx, "AsyncClient",
                      return_value=_Seq([_Resp(404), _Resp(503)])):
        with pytest.raises(FC.FreeInstagramError):
            await c.get_user_by_username("x")

    # og-only + meta description bio fallback
    html = ('<meta property="og:description" content="9 Followers, 1 '
            'Following, 2 Posts - from @x">'
            '<meta name="description" content="x on Instagram: '
            '&quot;the bio&quot;&quot;">')
    with patch.object(FC.httpx, "AsyncClient",
                      return_value=_Seq([_Resp(404), _Resp(200, text=html)])):
        out = await c.get_user_by_username("x")
    assert out["biography"] == "the bio" and out["_source"] == "og"

    # html get raising → outer except → None
    with patch.object(FC.httpx, "AsyncClient",
                      return_value=_Seq([_Resp(404), RuntimeError("x")])):
        with pytest.raises(FC.FreeInstagramError):
            await c.get_user_by_username("x")
