"""Unit tests for app/services/newsletter_subscribers.py."""

from __future__ import annotations

import sys
import types

import httpx
import pytest

import app.services.newsletter_subscribers as N


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    calls: list = []
    eval_payload: dict = {}

    def __init__(self, *a, **kw):
        self.kw = kw

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, path, json=None):
        _Client.calls.append((path, json))
        if path == "/debug/eval":
            return _Resp(_Client.eval_payload)
        return _Resp({})


@pytest.fixture
def client(monkeypatch):
    _Client.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(N.asyncio, "sleep", _no_sleep)
    return _Client


@pytest.mark.asyncio
async def test_fetch_subscribers_parses_rows(client):
    client.eval_payload = {"value": {"subs": [
        {"name": "Ann Lee", "urn": "ann-lee", "headline": "CTO at X"},
        {"name": "Bob", "urn": "bob"},
    ]}}
    out = await N.fetch_subscribers("urn:li:fsd_contentSeries:123")
    assert client.calls[0] == ("/debug/navigate", {
        "url": "https://www.linkedin.com/analytics/newsletter/urn:li:fsd_contentSeries:123/"})
    assert out == [
        {"name": "Ann Lee", "headline": "CTO at X", "urn": "urn:li:in:ann-lee",
         "profile_url": "https://www.linkedin.com/in/ann-lee/"},
        {"name": "Bob", "headline": "", "urn": "urn:li:in:bob",
         "profile_url": "https://www.linkedin.com/in/bob/"},
    ]


@pytest.mark.asyncio
async def test_fetch_subscribers_scrape_error_and_empty(client):
    client.eval_payload = {"value": {"err": "no See all link"}}
    assert await N.fetch_subscribers("123") == []
    client.eval_payload = {}
    assert await N.fetch_subscribers("123") == []


def _install_espo(monkeypatch, found):
    posts: list = []
    mod = types.ModuleType("app.services.espocrm_client")

    async def espo_get(path, params=None):
        return found

    async def espo_post(path, body):
        posts.append((path, body))
        return {"id": "1"}

    mod.espo_get = espo_get
    mod.espo_post = espo_post
    monkeypatch.setitem(sys.modules, "app.services.espocrm_client", mod)
    return posts


SUB = {"name": "Ann Lee", "headline": "CTO at X", "urn": "urn:li:in:ann",
       "profile_url": "https://www.linkedin.com/in/ann/"}


@pytest.mark.asyncio
async def test_espo_upsert_creates_and_dedupes(monkeypatch):
    posts = _install_espo(monkeypatch, {"list": []})
    assert await N.espo_upsert_subscriber(SUB) is True
    body = posts[0][1]
    assert (body["firstName"], body["lastName"], body["source"]) == ("Ann", "Lee", "Newsletter")
    assert "LI-URN:urn:li:in:ann" in body["description"]

    posts = _install_espo(monkeypatch, {"list": []})
    await N.espo_upsert_subscriber({**SUB, "name": "Mononym"})
    assert posts[0][1]["lastName"] == "(LinkedIn)"

    posts = _install_espo(monkeypatch, {"list": [{"id": "x"}]})
    assert await N.espo_upsert_subscriber(SUB) is False
    assert posts == []


def test_personalized_note_variants():
    assert "Saw you're CTO, " in N._personalized_note(SUB)
    assert "Saw you're" not in N._personalized_note({**SUB, "headline": ""})
    assert "Saw you're" not in N._personalized_note({**SUB, "headline": "x" * 80})


@pytest.mark.asyncio
async def test_subscriber_alert(monkeypatch):
    sent: list = []
    mod = types.ModuleType("app.services.slack_notifications")

    async def post_alert_to_slack(text):
        sent.append(text)

    mod.post_alert_to_slack = post_alert_to_slack
    monkeypatch.setitem(sys.modules, "app.services.slack_notifications", mod)
    await N._subscriber_alert(SUB)
    await N._subscriber_alert({**SUB, "headline": "", "profile_url": ""})
    assert "| CTO at X" in sent[0]
    assert "(no profile link)" in sent[1]


class _Redis:
    def __init__(self, members=(), fail_smembers=False, fail_sadd=False):
        self.members = set(members)
        self.fail_smembers = fail_smembers
        self.fail_sadd = fail_sadd
        self.added: list = []

    async def smembers(self, key):
        if self.fail_smembers:
            raise ConnectionError("redis down")
        return self.members

    async def sadd(self, key, *vals):
        if self.fail_sadd:
            raise ConnectionError("redis down")
        self.added.extend(vals)


def _patch_redis(monkeypatch, redis_obj):
    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "from_url", lambda url: redis_obj)


@pytest.mark.asyncio
async def test_sync_no_series():
    assert await N.sync_newsletter_subscribers("") == {
        "skipped": "no LINKEDIN_NEWSLETTER_SERIES_URN configured"}


@pytest.mark.asyncio
async def test_sync_no_subscribers(monkeypatch):
    async def fetch(_u):
        return []

    monkeypatch.setattr(N, "fetch_subscribers", fetch)
    assert await N.sync_newsletter_subscribers("u") == {"fetched": 0, "created": 0, "skipped": 0}


@pytest.mark.asyncio
async def test_sync_full_flow(monkeypatch):
    subs = [
        {**SUB, "urn": "seen"},
        {**SUB, "urn": "new"},
        {**SUB, "urn": "dupe"},
        {**SUB, "urn": "boom"},
    ]

    async def fetch(_u):
        return subs

    async def upsert(sub):
        if sub["urn"] == "boom":
            raise RuntimeError("espo down")
        return sub["urn"] == "new"

    alerts: list = []

    async def alert(sub):
        alerts.append(sub["urn"])

    monkeypatch.setattr(N, "fetch_subscribers", fetch)
    monkeypatch.setattr(N, "espo_upsert_subscriber", upsert)
    monkeypatch.setattr(N, "_subscriber_alert", alert)
    r = _Redis(members=[b"seen"])
    _patch_redis(monkeypatch, r)
    out = await N.sync_newsletter_subscribers("u")
    assert out == {"fetched": 4, "created": 1, "skipped": 3}
    assert alerts == ["new"]
    assert r.added == ["new", "dupe"]


@pytest.mark.asyncio
async def test_sync_redis_failures_are_tolerated(monkeypatch):
    async def fetch(_u):
        return [SUB]

    async def upsert(sub):
        return True

    async def alert(sub):
        return None

    monkeypatch.setattr(N, "fetch_subscribers", fetch)
    monkeypatch.setattr(N, "espo_upsert_subscriber", upsert)
    monkeypatch.setattr(N, "_subscriber_alert", alert)

    _patch_redis(monkeypatch, _Redis(fail_smembers=True, fail_sadd=True))
    assert (await N.sync_newsletter_subscribers("u"))["created"] == 1

    import redis.asyncio as aioredis

    def boom(url):
        raise ConnectionError("no redis")

    monkeypatch.setattr(aioredis, "from_url", boom)
    assert (await N.sync_newsletter_subscribers("u"))["created"] == 1
