"""Unit tests for app.services.linkedin_invite_outreach + worker task."""

from __future__ import annotations

import pytest

import app.services.linkedin_invite_outreach as SIO
import app.worker.tasks.linkedin_invite_outreach as W


class _FakeRedis:
    def __init__(self, sent=None, cursor=None):
        self._sent = set(sent or [])
        self._cursor = cursor
        self.sadded = []
        self.set_values = {}

    async def smembers(self, key):
        return set(self._sent)

    async def get(self, key):
        return self._cursor

    async def sadd(self, key, *members):
        self.sadded.extend(members)
        self._sent.update(members)

    async def expire(self, key, ttl):
        return True

    async def set(self, key, value):
        self.set_values[key] = value

    async def aclose(self):
        return None


def _patch_common(monkeypatch, *, session=True, scrape=None, connect=None, redis=None):
    """Wire module-level seams; returns dict of recorded calls."""
    calls = {"connect": [], "scrape": []}
    redis = redis if redis is not None else _FakeRedis()

    async def _session_ok():
        return session

    async def _scrape(keyword, limit=25):
        calls["scrape"].append(keyword)
        if isinstance(scrape, Exception):
            raise scrape
        return list(scrape or [])

    async def _post(path, payload, timeout=90.0, allow_error=False):
        if path == "/connect":
            calls["connect"].append(payload)
            if isinstance(connect, Exception):
                raise connect
            return connect(payload) if callable(connect) else (connect or {})
        return {}

    async def _redis_factory():
        return redis

    monkeypatch.setattr(SIO, "_session_ok", _session_ok)
    monkeypatch.setattr(SIO, "_scrape_slugs", _scrape)
    monkeypatch.setattr(SIO, "_post", _post)
    monkeypatch.setattr(SIO, "_redis", _redis_factory)
    # keep pacing deterministic
    monkeypatch.setattr(SIO.asyncio, "sleep", _noop_sleep)
    monkeypatch.setattr(SIO.random, "uniform", lambda a, b: 0.0)
    return calls, redis


async def _noop_sleep(*a, **k):
    return None


@pytest.mark.asyncio
async def test_skips_when_sidecar_session_inactive(monkeypatch):
    _patch_common(monkeypatch, session=False)
    res = await SIO.send_profile_invites(5)
    assert res == {"status": "skipped", "reason": "sidecar session not active"}


@pytest.mark.asyncio
async def test_sends_invites_and_records_sent(monkeypatch):
    seq = iter([["slug-a", "slug-b"], ["slug-c"]])

    def _scrape_list():
        try:
            return next(seq)
        except StopIteration:
            return []

    calls, redis = _patch_common(
        monkeypatch,
        scrape=None,
        connect={"invited": True},
    )
    # override scrape to rotate slug lists
    async def _scrape2(keyword, limit=25):
        calls["scrape"].append(keyword)
        return _scrape_list()

    monkeypatch.setattr(SIO, "_scrape_slugs", _scrape2)

    res = await SIO.send_profile_invites(2)
    assert res["status"] == "sent"
    assert res["sent"] == ["slug-a", "slug-c"]
    assert len(calls["connect"]) == 2
    assert set(redis.sadded) == {"slug-a", "slug-c"}
    assert redis.set_values[SIO._CURSOR_KEY] == 2 % len(SIO.SEARCH_KEYWORDS)


@pytest.mark.asyncio
async def test_dedupes_against_redis_sent_set(monkeypatch):
    calls, redis = _patch_common(
        monkeypatch,
        scrape=["slug-a", "slug-b"],
        connect={"invited": True},
        redis=_FakeRedis(sent=["slug-a"]),
    )
    res = await SIO.send_profile_invites(1)
    assert res["sent"] == ["slug-b"]
    assert calls["connect"][0]["vanity"] == "slug-b"


@pytest.mark.asyncio
async def test_already_connected_and_pending_are_not_resent(monkeypatch):
    results = iter([{"already_connected": True}, {"code": "PENDING"}, {"invited": True}])

    def _connect(payload):
        return next(results)

    _patch_common(
        monkeypatch,
        scrape=["s1", "s2", "s3"],
        connect=_connect,
    )
    res = await SIO.send_profile_invites(1)
    assert res["sent"] == ["s3"]


@pytest.mark.asyncio
async def test_rate_limited_stops_early(monkeypatch):
    _patch_common(
        monkeypatch,
        scrape=["s1", "s2"],
        connect={"code": "RATE_LIMITED"},
    )
    res = await SIO.send_profile_invites(5)
    assert res["status"] == "rate_limited"
    assert res["sent"] == []


@pytest.mark.asyncio
async def test_scrape_failure_continues_to_next_keyword(monkeypatch):
    attempts = {"n": 0}

    async def _scrape(keyword, limit=25):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("sidecar down")
        return ["ok-slug"]

    calls, _ = _patch_common(monkeypatch, connect={"invited": True})
    monkeypatch.setattr(SIO, "_scrape_slugs", _scrape)
    res = await SIO.send_profile_invites(1)
    assert res["sent"] == ["ok-slug"]
    assert attempts["n"] >= 2


@pytest.mark.asyncio
async def test_connect_transport_error_skips_slug(monkeypatch):
    results = iter([RuntimeError("boom"), {"invited": True}])

    def _connect(payload):
        r = next(results)
        if isinstance(r, Exception):
            raise r
        return r

    _patch_common(
        monkeypatch,
        scrape=["bad", "good"],
        connect=_connect,
    )
    res = await SIO.send_profile_invites(1)
    assert res["sent"] == ["good"]


@pytest.mark.asyncio
async def test_no_candidates_returns_none(monkeypatch):
    _patch_common(monkeypatch, scrape=[], connect={"invited": True})
    res = await SIO.send_profile_invites(3)
    assert res["status"] == "none"
    assert res["sent"] == []


@pytest.mark.asyncio
async def test_post_raises_on_http_error(monkeypatch):
    class _Resp:
        status_code = 500
        text = "err"

        def json(self):
            return {"err": True}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(SIO.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(SIO, "_base_url", lambda: "http://x")
    with pytest.raises(RuntimeError, match="sidecar"):
        await SIO._post("/connect", {})
    # allow_error suppresses the raise
    body = await SIO._post("/connect", {}, allow_error=True)
    assert body == {"err": True}


@pytest.mark.asyncio
async def test_session_ok_helpers(monkeypatch):
    class _Resp:
        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _Resp({"has_session": True})

    monkeypatch.setattr(SIO.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(SIO, "_base_url", lambda: "http://x")
    assert await SIO._session_ok() is True
    assert await SIO._get("/session") == {"has_session": True}


@pytest.mark.asyncio
async def test_session_ok_false_on_exception(monkeypatch):
    async def _boom(path, timeout=30.0):
        raise RuntimeError("sidecar down")

    monkeypatch.setattr(SIO, "_get", _boom)
    assert await SIO._session_ok() is False


@pytest.mark.asyncio
async def test_scrape_slugs_extracts_and_dedupes(monkeypatch):
    posts = []

    async def _post(path, payload, timeout=90.0, allow_error=False):
        posts.append((path, payload))
        if path == "/debug/eval":
            return {"value": '["a-slug", "b-slug", 42, ""]'}
        return {}

    monkeypatch.setattr(SIO, "_post", _post)
    slugs = await SIO._scrape_slugs("social media manager", limit=10)
    assert slugs == ["a-slug", "b-slug"]
    assert posts[0][0] == "/debug/navigate"
    assert "keywords=social+media+manager" in posts[0][1]["url"]
    assert posts[1][0] == "/debug/eval"


@pytest.mark.asyncio
async def test_scrape_slugs_non_list_value_returns_empty(monkeypatch):
    async def _post(path, payload, timeout=90.0, allow_error=False):
        if path == "/debug/eval":
            return {"value": "not-json{{{"}
        return {}

    monkeypatch.setattr(SIO, "_post", _post)
    assert await SIO._scrape_slugs("kw") == []


def test_worker_task_delegates(monkeypatch):
    captured = {}

    async def _fake_send(count):
        captured["count"] = count
        return {"status": "sent", "sent": ["x"]}

    monkeypatch.setattr(
        "app.services.linkedin_invite_outreach.send_profile_invites", _fake_send
    )
    # run_async executes the coroutine — call the underlying service path
    res = W.send_profile_invites(count=3)
    assert res == {"status": "sent", "sent": ["x"]}
    assert captured["count"] == 3
