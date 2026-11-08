"""Unit tests for devapps_sync platform pullers + dispatcher.

HTTP is mocked with respx (Meta Graph) or by patching the lazily-imported
API client classes; the DB is a tiny fake — no real session, no network.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import respx

from app.models.social_account import SocialAccount
from app.services import devapps_sync as ds


def _account(platform: str, **kw: Any) -> SocialAccount:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "team_id": uuid.uuid4(),
        "platform": platform,
        "account_id": "12345",
        "username": "testuser",
        "display_name": "Test User",
        "account_type": "person",
        "access_token_enc": b"enc",
        "scopes": ["scope_a"],
        "status": "active",
        "meta_data": {},
    }
    defaults.update(kw)
    return SocialAccount(**defaults)


class _FakeDB:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flushed = False
        self.committed = False

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed = True

    async def commit(self) -> None:
        self.committed = True


# ── Meta family (facebook / instagram) via respx on graph.facebook.com ────────

_GRAPH = r"https://graph\.facebook\.com/.*"


def _graph_routes(mock) -> None:
    mock.get(url__regex=rf"{_GRAPH}debug_token.*").mock(
        return_value=respx.MockResponse(200, json={"data": {"is_valid": True, "expires_at": 1893456000, "scopes": ["pages_show_list"]}})
    )
    mock.get(url__regex=rf"{_GRAPH}12345/insights.*").mock(
        return_value=respx.MockResponse(
            200,
            json={"data": [{"name": "page_impressions_unique", "values": [{"value": i} for i in range(1, 9)]}]},
        )
    )
    mock.get(url__regex=rf"{_GRAPH}12345\?.*").mock(
        return_value=respx.MockResponse(200, json={"name": "Page", "username": "iguser", "followers_count": 42, "media_count": 7, "link": "https://fb.com/p"})
    )


@pytest.mark.asyncio
async def test_pull_facebook_happy_path() -> None:
    with respx.mock(assert_all_called=False) as mock:
        _graph_routes(mock)
        account = _account("facebook", account_type="page")
        out = await ds._pull_facebook(None, account, "tok")
    assert out["ok"] is True
    assert out["health"]["token_valid"] is True
    assert out["health"]["expires_at"] == 1893456000
    assert out["insights"]["followers"] == 42
    assert out["insights"]["page_impressions_unique_7d"] == 8
    assert out["insights"]["page_impressions_unique_30d"] == 36


@pytest.mark.asyncio
async def test_pull_instagram_happy_path() -> None:
    with respx.mock(assert_all_called=False) as mock:
        _graph_routes(mock)
        account = _account("instagram")
        out = await ds._pull_instagram(None, account, "tok")
    assert out["ok"] is True
    assert out["insights"]["username"] == "iguser"
    assert out["insights"]["media_count"] == 7
    assert "demographics" in out


@pytest.mark.asyncio
async def test_pull_facebook_degraded_on_http_errors() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=_GRAPH).mock(return_value=respx.MockResponse(500, json={"error": "boom"}))
        account = _account("facebook")
        out = await ds._pull_facebook(None, account, "tok")
    # puller degrades per-section; outer ok stays True, health shows invalid
    assert out["ok"] is True
    assert out["health"]["token_valid"] is False


# ── Client-class pullers (lazy imports patched at the source module) ──────────


class _FakeThreads:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    async def validate_token(self):
        return {"is_valid": True}

    async def get_profile(self):
        return {"username": "th_user"}

    async def get_insights(self, metric: str):
        return {"metric": metric, "values": [1]}


@pytest.mark.asyncio
async def test_pull_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.threads_api.ThreadsAPIClient", _FakeThreads)
    out = await ds._pull_threads(None, _account("threads"), "tok")
    assert out["ok"] is True
    assert out["health"] == {"is_valid": True}
    assert out["insights"]["profile"]["username"] == "th_user"
    assert "insights_views" in out["insights"]


class _FakeLinkedIn:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    async def validate_token(self):
        return {"sub": "member-1"}

    async def get_follower_count(self, urn: str):
        return 555

    async def get_organization(self, org_id: str):
        return {"id": org_id, "name": "Org"}

    async def get_organization_lifetime_stats(self, urn: str):
        return {"followerCounts": [{"totalFollowerCount": 555}]}


@pytest.mark.asyncio
async def test_pull_linkedin_member_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.linkedin_api.LinkedInAPIClient", _FakeLinkedIn)
    out = await ds._pull_linkedin(None, _account("linkedin"), "tok")
    assert out["ok"] is True
    assert out["health"]["token_valid"] is True
    assert "org stats not applicable" in out["insights"]["note"]


@pytest.mark.asyncio
async def test_pull_linkedin_org_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.linkedin_api.LinkedInAPIClient", _FakeLinkedIn)
    monkeypatch.setattr("app.services.analytics_sync._org_urn", lambda a: "urn:li:organization:42")
    account = _account("linkedin", account_type="organization", meta_data={"author_urn": "urn:li:organization:42"})
    out = await ds._pull_linkedin(None, account, "tok")
    assert out["ok"] is True
    assert out["insights"]["followers"] == 555
    assert out["insights"]["organization"]["name"] == "Org"


class _FakeTwitter:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    async def validate_token(self):
        return {"username": "tb"}

    async def get_me_metrics(self):
        return {"public_metrics": {"followers_count": 10}}


@pytest.mark.asyncio
async def test_pull_twitter_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.twitter_api.TwitterAPIClient", _FakeTwitter)
    out = await ds._pull_twitter(None, _account("twitter"), "tok")
    assert out["ok"] is True
    assert out["insights"]["me_metrics"]["public_metrics"]["followers_count"] == 10


class _FakeTwitterQuota(_FakeTwitter):
    async def get_me_metrics(self):
        raise RuntimeError("402 Payment Required: quota exceeded")


@pytest.mark.asyncio
async def test_pull_twitter_quota_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.twitter_api.TwitterAPIClient", _FakeTwitterQuota)
    out = await ds._pull_twitter(None, _account("twitter"), "tok")
    assert out["ok"] is True
    assert out["insights"]["quota_exhausted"] is True


class _FakeTikTok:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    async def validate_token(self):
        return {"is_valid": True}

    async def get_creator_info(self):
        return {"nickname": "tt"}

    async def get_stats(self):
        return {"likes": 3}


@pytest.mark.asyncio
async def test_pull_tiktok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.tiktok_api.TikTokAPIClient", _FakeTikTok)
    out = await ds._pull_tiktok(None, _account("tiktok"), "tok")
    assert out["ok"] is True
    assert out["insights"]["creator_info"]["nickname"] == "tt"
    assert out["insights"]["stats"]["likes"] == 3


class _FakeTelegram:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    async def get_me(self):
        return {"id": 1, "username": "bot"}

    async def get_webhook_info(self):
        return {"url": "https://x/hook"}

    async def get_chat(self, chat_id):
        return {"id": chat_id, "title": "Chan"}


@pytest.mark.asyncio
async def test_pull_telegram_uses_meta_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.telegram_api.TelegramAPIClient", _FakeTelegram)
    monkeypatch.setattr(ds, "decrypt_token", lambda raw: "decrypted-bot-token")
    account = _account(
        "telegram",
        meta_data={"bot_token_enc": b"enc-bot", "telegram_channel": {"chat_id": "-100123"}},
    )
    out = await ds._pull_telegram(None, account)
    assert out["ok"] is True
    assert out["health"]["token_valid"] is True
    assert out["health"]["webhook"]["url"] == "https://x/hook"
    assert out["insights"]["channel"]["title"] == "Chan"


class _FakeBluesky:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    async def create_session(self):
        return {"did": "did:plc:x"}

    async def get_profile(self):
        return {"did": "did:plc:x", "handle": "h.bsky.social"}


@pytest.mark.asyncio
async def test_pull_bluesky(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.bluesky_api.BlueskyClient", _FakeBluesky)
    out = await ds._pull_bluesky(None, _account("bluesky"), "app-pass")
    assert out["ok"] is True
    assert out["health"]["did"] == "did:plc:x"
    assert out["health"]["handle"] == "h.bsky.social"


@pytest.mark.asyncio
async def test_pull_bluesky_error_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boom(_FakeBluesky):
        async def create_session(self):
            raise RuntimeError("429 rate limited")

    monkeypatch.setattr("app.services.bluesky_api.BlueskyClient", _Boom)
    out = await ds._pull_bluesky(None, _account("bluesky"), "app-pass")
    assert out["ok"] is False
    assert "429" in out["error"]


class _FakeWhatsApp:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    async def get_phone_number_info_by_id(self, pid: str):
        return {"display_phone_number": "+30 210 000 0000", "id": pid}


@pytest.mark.asyncio
async def test_pull_whatsapp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.whatsapp_api.WhatsAppAPIClient", _FakeWhatsApp)
    out = await ds._pull_whatsapp(None, _account("whatsapp"), "tok")
    assert out["ok"] is True
    assert out["health"]["token_valid"] is True
    assert out["insights"]["phone_number"]["display_phone_number"].startswith("+30")


class _FakeViber:
    def __init__(self, *a: Any, **kw: Any) -> None:
        pass

    async def get_account_info(self):
        return {"id": "viber-bot"}


@pytest.mark.asyncio
async def test_pull_viber_ok_and_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.viber_api.ViberAPIClient", _FakeViber)
    out = await ds._pull_viber(None, _account("viber"), "tok")
    assert out["ok"] is True
    assert out["insights"]["account_info"]["id"] == "viber-bot"

    class _ViberFail(_FakeViber):
        async def get_account_info(self):
            raise RuntimeError("unauthorized")

    monkeypatch.setattr("app.services.viber_api.ViberAPIClient", _ViberFail)
    out2 = await ds._pull_viber(None, _account("viber"), "tok")
    assert out2["ok"] is False
    assert out2["health"]["token_valid"] is False


# ── Dispatcher ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_devapp_account_persists_event() -> None:
    db = _FakeDB()
    account = _account("twitter")
    payload = await ds.sync_devapp_account(db, account)
    # twitter puller will fail on real client construction? No — it builds
    # TwitterAPIClient(access_token=...) lazily inside try; without network it
    # errors → ok False payload, still persisted.
    assert db.added and db.flushed
    event = db.added[0]
    assert event.event_type == ds.DEVAPP_EVENT_TYPE
    assert event.social_account_id == account.id
    assert event.meta_data["platform"] == "twitter"
    assert payload["platform"] == "twitter"


@pytest.mark.asyncio
async def test_sync_devapp_account_unknown_platform() -> None:
    db = _FakeDB()
    account = _account("myspace")
    payload = await ds.sync_devapp_account(db, account)
    assert payload["ok"] is True
    assert "no devapp puller" in payload["insights"]["note"]
    assert db.flushed


@pytest.mark.asyncio
async def test_sync_all_devapps_sweeps_and_isolates() -> None:
    acct_ok = _account("myspace", username="good")
    acct_bad = _account("also-unknown", username="bad")

    class _Result:
        def scalars(self):
            return self

        def all(self):
            return [acct_ok, acct_bad]

    class _DB(_FakeDB):
        async def execute(self, q):
            return _Result()

    db = _DB()
    res = await ds.sync_all_devapps(db)
    # fake execute ignores the WHERE clause — both accounts are swept
    assert res["accounts"] == 2
    assert res["ok"] == 2
    assert db.committed
