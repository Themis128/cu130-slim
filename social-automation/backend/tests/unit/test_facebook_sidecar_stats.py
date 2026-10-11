"""Coverage for facebook_sidecar.get_profile_stats + remaining methods."""

from unittest.mock import AsyncMock

import pytest

import app.services.facebook_sidecar as F


class _Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code = status
        self._data = data or {}
        self.text = text

    def json(self):
        return self._data


class _HTTP:
    def __init__(self, handler):
        self.handler = handler
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, path, json=None, **kw):
        self.posts.append((path, json))
        return self.handler(path, json)

    async def get(self, path, **kw):
        return self.handler(path, None)


def _wire(monkeypatch, handler):
    holder = {}

    def _factory(**kw):
        c = _HTTP(handler)
        holder["c"] = c
        return c

    monkeypatch.setattr(F.httpx, "AsyncClient", _factory)
    monkeypatch.setattr(F.asyncio, "sleep", AsyncMock())
    F._PROFILE_STATS_CACHE.clear()
    return holder


PROFILE_TEXT = "Themis Baltzakis\n66 followers • 12 following\nposts stuff"
DASH_TEXT = "Home\nInsights\n209\n895%\nViews\n45\n10%\nEngagement\n12\n300%\nNet follows\nMore"


def _handler(profile_text=PROFILE_TEXT, dash_text=DASH_TEXT, nav_url="https://www.facebook.com/me"):
    def h(path, payload):
        if path == "/debug/navigate":
            return _Resp(200, {"url": nav_url})
        if path == "/debug/eval":
            script = (payload or {}).get("script", "")
            text = profile_text if "6000" in script else dash_text
            return _Resp(200, {"result": text})
        return _Resp(200, {})

    return h


class TestProfileStats:
    @pytest.mark.asyncio
    async def test_happy_and_cache(self, monkeypatch):
        holder = _wire(monkeypatch, _handler())
        client = F.FacebookSidecarClient("http://sc")
        stats = await client.get_profile_stats("Themis Baltzakis")
        assert stats["followers"] == 66
        assert stats["profile_name"] == "Themis Baltzakis"
        assert stats["views_28d"] == 209
        assert stats["engagement_28d"] == 45
        assert stats["net_follows_28d"] == 12
        assert stats["profile_url"] == "https://www.facebook.com/me"

        n_posts = len(holder["c"].posts)
        # second call → cache hit, zero new requests
        cached = await client.get_profile_stats("Themis Baltzakis")
        assert cached == stats
        assert len(holder["c"].posts) == n_posts

    @pytest.mark.asyncio
    async def test_no_followers_text(self, monkeypatch):
        _wire(monkeypatch, _handler(profile_text="login wall"))
        client = F.FacebookSidecarClient("http://sc")
        stats = await client.get_profile_stats()
        assert stats["followers"] is None
        assert stats["profile_name"] is None

    @pytest.mark.asyncio
    async def test_name_mismatch_409(self, monkeypatch):
        _wire(monkeypatch, _handler())
        client = F.FacebookSidecarClient("http://sc")
        with pytest.raises(F.FacebookSidecarError) as ei:
            await client.get_profile_stats("Someone Else")
        assert ei.value.status_code == 409
        assert "profile mismatch" in ei.value.detail

    @pytest.mark.asyncio
    async def test_dashboard_never_mounts(self, monkeypatch):
        _wire(monkeypatch, _handler(dash_text="still loading"))
        client = F.FacebookSidecarClient("http://sc")
        stats = await client.get_profile_stats()
        assert "views_28d" not in stats
        assert stats["followers"] == 66

    @pytest.mark.asyncio
    async def test_sidebar_label_skipped(self, monkeypatch):
        # "Views" in the sidebar is NOT preceded by a % line → skipped;
        # the stat card's occurrence is the one parsed.
        dash = "Views\nViews\n1,234\n55%\nViews\n"
        _wire(monkeypatch, _handler(dash_text=dash + "Net follows"))
        client = F.FacebookSidecarClient("http://sc")
        stats = await client.get_profile_stats()
        assert stats["views_28d"] == 1234

    @pytest.mark.asyncio
    async def test_missing_card(self, monkeypatch):
        _wire(monkeypatch, _handler(dash_text="Net follows\nnope\n9\n1%\nViews\n"))
        client = F.FacebookSidecarClient("http://sc")
        stats = await client.get_profile_stats()
        assert stats["views_28d"] == 9
        assert "net_follows_28d" not in stats


class TestRemainingMethods:
    @pytest.mark.asyncio
    async def test_login_with_2fa(self, monkeypatch):
        holder = _wire(monkeypatch, lambda p, j: _Resp(200, {"ok": 1}))
        client = F.FacebookSidecarClient("http://sc")
        await client.login("u", "p", verification_code="123456")
        assert holder["c"].posts[-1][1]["verification_code"] == "123456"

    @pytest.mark.asyncio
    async def test_login_no_2fa(self, monkeypatch):
        holder = _wire(monkeypatch, lambda p, j: _Resp(200, {"ok": 1}))
        client = F.FacebookSidecarClient("http://sc")
        await client.login("u", "p")
        assert "verification_code" not in holder["c"].posts[-1][1]

    @pytest.mark.asyncio
    async def test_update_methods(self, monkeypatch):
        seen = []
        _wire(monkeypatch, lambda p, j: (seen.append((p, j)), _Resp(200, {}))[1])
        client = F.FacebookSidecarClient("http://sc")
        await client.check_session()
        await client.upload_cover(b"img", "c.png")
        await client.update_website("https://x")
        await client.update_work("Co", position="Dev", description="d")
        await client.update_work("Co")
        await client.update_education("Uni", degree="BSc")
        await client.update_education("Uni")
        await client.update_location("Athens", "Lamia")
        await client.update_location()
        await client.update_quotes("q")
        await client.update_contact(email="e", phone="p")
        await client.update_contact()
        await client.export_cookies()
        await client.page_post_photo([{"image_base64": "x"}], "cap")
        await client.page_post_photo([{"image_base64": "x"}])

        paths = [p for p, _ in seen]
        assert "/session" in paths
        assert "/profile/cover" in paths
        assert "/profile/website" in paths
        assert "/profile/work" in paths
        assert "/profile/education" in paths
        assert "/profile/location" in paths
        assert "/profile/quotes" in paths
        assert "/profile/contact" in paths
        assert "/profile/cookies" in paths
        assert "/page/post/photo" in paths
        # optional-field branches
        work_payloads = [j for p, j in seen if p == "/profile/work"]
        assert work_payloads[0]["position"] == "Dev"
        assert "position" not in work_payloads[1]
        edu = [j for p, j in seen if p == "/profile/education"]
        assert edu[0]["degree"] == "BSc" and "degree" not in edu[1]
        loc = [j for p, j in seen if p == "/profile/location"]
        assert loc[0]["current_city"] == "Athens" and loc[1] == {}
        contact = [j for p, j in seen if p == "/profile/contact"]
        assert contact[0]["phone"] == "p" and contact[1] == {}
        page_photos = [j for p, j in seen if p == "/page/post/photo"]
        assert page_photos[0]["message"] == "cap"
        assert "message" not in page_photos[1]

    @pytest.mark.asyncio
    async def test_post_error(self, monkeypatch):
        _wire(monkeypatch, lambda p, j: _Resp(500, text="err"))
        client = F.FacebookSidecarClient("http://sc")
        with pytest.raises(F.FacebookSidecarError) as ei:
            await client.update_bio("x")
        assert ei.value.status_code == 500

    @pytest.mark.asyncio
    async def test_get_error(self, monkeypatch):
        _wire(monkeypatch, lambda p, j: _Resp(403, text="no"))
        client = F.FacebookSidecarClient("http://sc")
        with pytest.raises(F.FacebookSidecarError):
            await client.health()

    @pytest.mark.asyncio
    async def test_privacy_branches(self, monkeypatch):
        seen = []
        _wire(monkeypatch, lambda p, j: (seen.append((p, j)), _Resp(200, {}))[1])
        client = F.FacebookSidecarClient("http://sc")
        await client.post_link("https://x", "m", privacy="friends")
        await client.post_video(b"v", "v.mp4", "m", privacy="public")
        assert seen[0][1]["privacy"] == "friends"
        assert seen[1][1]["privacy"] == "public"
