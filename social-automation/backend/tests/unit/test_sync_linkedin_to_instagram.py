"""Tests for app/scripts/sync_linkedin_to_instagram.py — LinkedIn→IG profile sync."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.scripts.sync_linkedin_to_instagram as sync


class _FakeSidecar:
    def __init__(self, company):
        self.base_url = "http://sidecar:1"
        self._company = company
        self.get_company = AsyncMock(return_value=company)


class _FakeHTTP:
    def __init__(self, eval_result=""):
        self._eval = eval_result
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, timeout=0):
        self.posts.append(url)
        if "/debug/eval" in url:
            return SimpleNamespace(json=lambda: {"result": self._eval})
        return SimpleNamespace(json=lambda: {})


@pytest.mark.asyncio
async def test_read_linkedin_company_complete(monkeypatch):
    client = _FakeSidecar({"company": {"about": "a", "website": "w", "name": "Cloudless"}})
    out = await sync.read_linkedin_company(client)
    assert out["about"] == "a" and out["website"] == "w"


@pytest.mark.asyncio
async def test_read_linkedin_company_scrape_fallback(monkeypatch):
    client = _FakeSidecar({"company": {"name": "Cloudless"}})
    text = "Overview\n☁️ Tagline here\nmore\nhttps://cloudless.gr"
    fake = _FakeHTTP(eval_result=text)
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: fake)
    monkeypatch.setattr(sync.asyncio, "sleep", AsyncMock())
    out = await sync.read_linkedin_company(client)
    assert "https://cloudless.gr" == out["website"]
    assert "☁️ Tagline here" == out["tagline"]
    assert "Overview" in out["about"]


@pytest.mark.asyncio
async def test_read_linkedin_company_scrape_no_markers(monkeypatch):
    client = _FakeSidecar({"company": {"name": "X"}})
    fake = _FakeHTTP(eval_result="nothing useful")
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: fake)
    monkeypatch.setattr(sync.asyncio, "sleep", AsyncMock())
    out = await sync.read_linkedin_company(client)
    # no Overview/☁️ marker -> about stays absent
    assert "about" not in out


@pytest.mark.asyncio
async def test_read_linkedin_company_error():
    import app.services.linkedin_sidecar as lsc

    client = _FakeSidecar(None)
    client.get_company = AsyncMock(side_effect=lsc.LinkedInSidecarError(500, "down"))
    with pytest.raises(lsc.LinkedInSidecarError):
        await sync.read_linkedin_company(client)


def test_build_instagram_bio():
    # picks first content line >= 5 chars, skipping headers
    out = sync.build_instagram_bio({"about": "Overview\n\n☁️ Real tagline"})
    assert out == "☁️ Real tagline"

    # header lines skipped
    out2 = sync.build_instagram_bio({"about": "About Us\nok\nReal line content"})
    assert out2 == "Real line content"

    # too-long line truncated
    long_line = "x" * 200
    out3 = sync.build_instagram_bio({"about": long_line})
    assert len(out3) == 150 and out3.endswith("...")

    # empty about -> fallback bio
    out4 = sync.build_instagram_bio({})
    assert "Clear skies" in out4


@pytest.mark.asyncio
async def test_get_instagram_credentials(monkeypatch):
    monkeypatch.setattr(sync.secret_store, "get", AsyncMock(side_effect=["u", "p"]))
    assert await sync.get_instagram_credentials() == ("u", "p")

    # fall back to settings
    monkeypatch.setattr(sync.secret_store, "get", AsyncMock(return_value=None))
    settings = SimpleNamespace(INSTAGRAM_USERNAME="su", INSTAGRAM_PASSWORD="sp")
    monkeypatch.setattr(sync, "get_settings", lambda: settings)
    assert await sync.get_instagram_credentials() == ("su", "sp")

    # neither -> ValueError
    empty = SimpleNamespace(INSTAGRAM_USERNAME=None, INSTAGRAM_PASSWORD=None)
    monkeypatch.setattr(sync, "get_settings", lambda: empty)
    with pytest.raises(ValueError):
        await sync.get_instagram_credentials()


@pytest.mark.asyncio
async def test_main_success(monkeypatch, capsys):
    monkeypatch.setattr(sync, "LinkedInSidecarClient", lambda: _FakeSidecar({"company": {"about": "about", "website": "https://w"}}))
    monkeypatch.setattr(sync, "read_linkedin_company", AsyncMock(return_value={"about": "☁️ bio", "website": "https://w"}))
    monkeypatch.setattr(sync, "get_instagram_credentials", AsyncMock(return_value=("u", "p")))
    ig = SimpleNamespace(update_profile=AsyncMock(return_value={"ok": 1}))
    monkeypatch.setattr(sync, "InstagrapiClient", lambda **kw: ig)
    monkeypatch.setattr(sync, "get_settings", lambda: SimpleNamespace(INSTAGRAM_PROXY=None))
    await sync.main()
    out = capsys.readouterr().out
    assert "Sync complete" in out
    assert ig.update_profile.call_args.kwargs["biography"] == "☁️ bio"


@pytest.mark.asyncio
async def test_main_no_credentials(monkeypatch, capsys):
    monkeypatch.setattr(sync, "read_linkedin_company", AsyncMock(return_value={"about": "", "website": "w"}))
    monkeypatch.setattr(sync, "get_instagram_credentials", AsyncMock(side_effect=ValueError("none")))
    await sync.main()  # returns early, no exception


@pytest.mark.asyncio
async def test_main_instagram_error(monkeypatch, capsys):
    import app.services.instagrapi_client as ic

    monkeypatch.setattr(sync, "read_linkedin_company", AsyncMock(return_value={"about": "", "website": "w"}))
    monkeypatch.setattr(sync, "get_instagram_credentials", AsyncMock(return_value=("u", "p")))
    ig = SimpleNamespace(update_profile=AsyncMock(side_effect=ic.InstagrapiError("rate-limited")))
    monkeypatch.setattr(sync, "InstagrapiClient", lambda **kw: ig)
    monkeypatch.setattr(sync, "get_settings", lambda: SimpleNamespace(INSTAGRAM_PROXY=None))
    await sync.main()
    assert "Sync failed" in capsys.readouterr().out
