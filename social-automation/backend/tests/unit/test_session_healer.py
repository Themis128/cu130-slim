"""Tests for app/services/session_healer.py — the session auto-heal sweep."""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.session_healer as H


class _Redis:
    def __init__(self):
        self.store = {}
        self.deleted = []

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.store:
            return None
        self.store[k] = v
        return True

    async def get(self, k):
        return self.store.get(k)

    async def delete(self, k):
        self.deleted.append(k)
        return self.store.pop(k, None) is not None

    async def aclose(self):
        pass


class _Http:
    """Routed fake httpx.AsyncClient: (method, url-substring) → (status, json)."""

    routes: dict = {}
    calls: list = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def _route(self, method, url):
        _Http.calls.append((method, url))
        for (m, key), (status, payload) in _Http.routes.items():
            if m == method and key in url:
                return SimpleNamespace(status_code=status, json=lambda: payload, raise_for_status=lambda: None, text=str(payload))
        return SimpleNamespace(status_code=404, json=lambda: {}, raise_for_status=lambda: None, text="")

    async def get(self, url, **kw):
        return self._route("GET", url)

    async def post(self, url, **kw):
        return self._route("POST", url)

    async def request(self, method, url, **kw):
        return self._route(method, url)


def _res(*, scalars_all=None, first=None, all_=None):
    return SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: scalars_all or [], first=lambda: first),
        all=lambda: all_ or [],
    )


class _FakeDB:
    def __init__(self, results):
        self._results = deque(results)
        self.commits = 0

    async def execute(self, q):
        return self._results.popleft()

    async def commit(self):
        self.commits += 1


class _DBCtx:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _redis(monkeypatch):
    r = _Redis()
    monkeypatch.setattr(H, "_get_redis", AsyncMock(return_value=r))
    monkeypatch.setattr(H.asyncio, "sleep", AsyncMock())
    return r


def _patch_db(monkeypatch, *results):
    db = _FakeDB(results)
    monkeypatch.setattr(H, "_worker_db", lambda: _DBCtx(db))
    return db


def _stats():
    return {"linkedin": {}, "facebook_sidecar": {}}


# ── alerts + secrets + db helpers ─────────────────────────────────────


@pytest.mark.asyncio
async def test_alert_once_per_platform(monkeypatch):
    posted = []
    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", AsyncMock(side_effect=lambda *a, **kw: posted.append(a[0])))
    monkeypatch.setattr("app.services.slack_notifications.session_heal_buttons", lambda: [])
    await H._alert("twitter", "broken")
    await H._alert("twitter", "broken again")  # deduped by nx
    assert posted == ["broken"]


@pytest.mark.asyncio
async def test_alert_recovered_only_after_alert(monkeypatch):
    posted = []
    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", AsyncMock(side_effect=lambda *a, **kw: posted.append(a[0])))
    await H._alert_recovered("twitter", "back")  # no prior alert → silent
    assert posted == []
    r = await H._get_redis()
    r.store[H._ALERT_KEY.format(platform="twitter")] = "1"
    await H._alert_recovered("twitter", "back")
    assert posted == ["back"]


@pytest.mark.asyncio
async def test_clear_dead_marker(_redis):
    await H._clear_dead_marker("instagram")
    assert "browser_bridge:dead:instagram" in _redis.deleted


@pytest.mark.asyncio
async def test_secret_and_env(monkeypatch):
    store = SimpleNamespace(get=AsyncMock(return_value="s3cr3t"))
    monkeypatch.setattr("app.services.secret_store.secret_store", store)
    assert await H._secret("X") == "s3cr3t"
    store.get = AsyncMock(side_effect=Exception("down"))
    assert await H._secret("X") == ""
    monkeypatch.setenv("MY_V", "1")
    assert H._env("MY_V") == "1" and H._env("NOPE") == ""


@pytest.mark.asyncio
async def test_db_sync_helpers(monkeypatch):
    acc = SimpleNamespace(meta_data={"a": 1})
    monkeypatch.setattr(H, "flag_modified", lambda *a, **kw: None)
    _patch_db(monkeypatch, _res(scalars_all=[acc]))
    n = await H._sync_storage_state("twitter", {"cookies": []})
    assert n == 1 and "browser_storage_state" in acc.meta_data

    _patch_db(monkeypatch, _res(scalars_all=[acc]))
    n = await H._sync_browser_cookies("twitter", {"c": "v"})
    assert acc.meta_data["browser_cookies"] == {"c": "v"}

    acc2 = SimpleNamespace(meta_data={"browser_storage_state": {"cookies": [1]}})
    _patch_db(monkeypatch, _res(scalars_all=[acc2]))
    assert await H._load_storage_state("twitter") == {"cookies": [1]}

    _patch_db(monkeypatch, _res(all_=[("twitter",), ("threads",)]))
    assert await H._platforms_with_accounts() == {"twitter", "threads"}

    _patch_db(monkeypatch, _res(first="cu_dev"))
    assert await H._account_username("twitter") == "cu_dev"


# ── linkedin sidecar heal ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_li_sidecar_down(monkeypatch):
    _Http.routes = {("GET", "/health"): (200, {"status": "bad"})}
    _Http.calls = []
    monkeypatch.setattr(H.httpx, "AsyncClient", _Http)
    alert = AsyncMock()
    monkeypatch.setattr(H, "_alert", alert)
    stats = _stats()
    await H._heal_linkedin(stats)
    assert stats["linkedin"]["status"] == "sidecar_down"
    alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_li_rate_limit_paths(monkeypatch):
    import time

    # stale 429 (>6h) → cleared
    old_until = time.time() * 1000 - 7 * 3600 * 1000
    _Http.routes = {
        ("GET", "/health"): (200, {"status": "ok", "rate_limited": True, "rate_limit_until": old_until}),
        ("POST", "/session/clear-rate-limit"): (200, {}),
        ("GET", "/session"): (200, {"logged_in": True}),
        ("GET", "/debug/all-cookies"): (200, {"cookies": {}}),
    }
    _Http.calls = []
    monkeypatch.setattr(H.httpx, "AsyncClient", _Http)
    monkeypatch.setattr(H, "_alert_recovered", AsyncMock())
    stats = _stats()
    await H._heal_linkedin(stats)
    assert stats["linkedin"]["rate_limit_cleared"] is True
    assert stats["linkedin"]["status"] == "healthy"

    # fresh 429 → bail as rate_limited
    fresh = time.time() * 1000 - 1000
    _Http.routes = {("GET", "/health"): (200, {"status": "ok", "rate_limited": True, "rate_limit_until": fresh})}
    stats = _stats()
    await H._heal_linkedin(stats)
    assert stats["linkedin"]["status"] == "rate_limited"


@pytest.mark.asyncio
async def test_li_healthy_syncs_cookies(monkeypatch):
    _Http.routes = {
        ("GET", "/health"): (200, {"status": "ok"}),
        ("GET", "/session"): (200, {"logged_in": True}),
        ("GET", "/debug/all-cookies"): (200, {"cookies": {"li_at": "L", "a": "b"}}),
    }
    monkeypatch.setattr(H.httpx, "AsyncClient", _Http)
    sync = AsyncMock()
    monkeypatch.setattr(H, "_sync_storage_state", sync)
    store = SimpleNamespace(set=AsyncMock())
    monkeypatch.setattr("app.services.secret_store.secret_store", store)
    monkeypatch.setattr(H, "_alert_recovered", AsyncMock())
    stats = _stats()
    await H._heal_linkedin(stats)
    assert stats["linkedin"]["status"] == "healthy"
    sync.assert_awaited_once()
    store.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_li_no_creds_and_pending(monkeypatch):
    _Http.routes = {
        ("GET", "/health"): (200, {"status": "ok"}),
        ("GET", "/session"): (200, {"logged_in": False}),
    }
    monkeypatch.setattr(H.httpx, "AsyncClient", _Http)
    monkeypatch.setattr(H, "_env", lambda n: "")
    monkeypatch.setattr(H, "_secret", AsyncMock(return_value=""))
    monkeypatch.setattr(H, "_alert", AsyncMock())
    stats = _stats()
    await H._heal_linkedin(stats)
    assert stats["linkedin"]["status"] == "needs_manual_login"

    # pending 2fa marker → no relogin
    r = await H._get_redis()
    r.store[H._LI_PENDING_KEY] = "1"
    monkeypatch.setattr(H, "_env", lambda n: "x" if n == "LINKEDIN_EMAIL" else "")
    monkeypatch.setattr(H, "_secret", AsyncMock(return_value="pw"))
    stats = _stats()
    await H._heal_linkedin(stats)
    assert stats["linkedin"]["status"] == "pending_2fa"


@pytest.mark.asyncio
async def test_li_login_and_2fa_flows(monkeypatch):
    monkeypatch.setattr(H, "_env", lambda n: "e@x" if n == "LINKEDIN_EMAIL" else "")
    monkeypatch.setattr(H, "_secret", AsyncMock(return_value="pw"))
    monkeypatch.setattr(H, "_alert", AsyncMock())
    monkeypatch.setattr(H, "_alert_recovered", AsyncMock())
    sync = AsyncMock()
    monkeypatch.setattr(H, "_li_sync_cookies", sync)

    # direct login success
    _Http.routes = {
        ("GET", "/health"): (200, {"status": "ok"}),
        ("GET", "/session"): (200, {"logged_in": False}),
        ("POST", "/login"): (200, {"logged_in": True}),
    }
    monkeypatch.setattr(H.httpx, "AsyncClient", _Http)
    stats = _stats()
    await H._heal_linkedin(stats)
    assert stats["linkedin"]["status"] == "recovered"

    # 2fa flow: login says 2fa, later poll flips logged_in
    session_calls = {"n": 0}

    class _Http2(_Http):
        async def get(self, url, **kw):
            if "/session" in url and "health" not in url and "cookies" not in url:
                session_calls["n"] += 1
                return SimpleNamespace(json=lambda: {"logged_in": session_calls["n"] > 1}, status_code=200)
            return await super().get(url, **kw)

    _Http.routes = {
        ("GET", "/health"): (200, {"status": "ok"}),
        ("POST", "/login"): (200, {"two_factor_required": True}),
    }
    monkeypatch.setattr(H.httpx, "AsyncClient", _Http2)
    stats = _stats()
    await H._heal_linkedin(stats)
    assert stats["linkedin"]["status"] == "recovered"

    # 2fa never approved → pending + alert
    session_calls["n"] = -99
    stats = _stats()
    await H._heal_linkedin(stats)
    assert stats["linkedin"]["status"] == "pending_2fa"

    # login hard fail → needs_manual_login (clear the pending marker first)
    (await H._get_redis()).store.pop(H._LI_PENDING_KEY, None)
    _Http.routes = {
        ("GET", "/health"): (200, {"status": "ok"}),
        ("GET", "/session"): (200, {"logged_in": False}),
        ("POST", "/login"): (200, {"logged_in": False, "error": "bad creds"}),
    }
    monkeypatch.setattr(H.httpx, "AsyncClient", _Http)
    stats = _stats()
    await H._heal_linkedin(stats)
    assert stats["linkedin"]["status"] == "needs_manual_login"


# ── facebook sidecar heal ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fb_sidecar_healthy(monkeypatch):
    _Http.routes = {
        ("GET", "/session/validate"): (200, {"logged_in": True}),
        ("GET", "/profile/cookies"): (200, {"cookies": {"c_user": "1"}}),
    }
    monkeypatch.setattr(H.httpx, "AsyncClient", _Http)
    monkeypatch.setattr(H, "_sync_storage_state", AsyncMock(return_value=2))
    monkeypatch.setattr(H, "_alert_recovered", AsyncMock())
    stats = _stats()
    await H._heal_facebook_sidecar(stats)
    assert stats["facebook_sidecar"]["status"] == "healthy"
    assert stats["facebook_sidecar"]["accounts_synced"] == 2


@pytest.mark.asyncio
async def test_fb_sidecar_reinject_and_dead(monkeypatch):
    # stored state re-inject works → recovered
    _Http.routes = {
        ("GET", "/session/validate"): (200, {"logged_in": False}),
        ("POST", "/session"): (200, {"logged_in": True}),
    }
    monkeypatch.setattr(H.httpx, "AsyncClient", _Http)
    monkeypatch.setattr(H, "_load_storage_state", AsyncMock(return_value={"cookies": [{"n": 1}]}))
    monkeypatch.setattr(H, "_alert_recovered", AsyncMock())
    stats = _stats()
    await H._heal_facebook_sidecar(stats)
    assert stats["facebook_sidecar"]["status"] == "recovered"

    # re-inject fails → needs_manual_login
    _Http.routes = {
        ("GET", "/session/validate"): (200, {"logged_in": False}),
        ("POST", "/session"): (200, {"logged_in": False}),
    }
    monkeypatch.setattr(H, "_alert", AsyncMock())
    stats = _stats()
    await H._heal_facebook_sidecar(stats)
    assert stats["facebook_sidecar"]["status"] == "needs_manual_login"


# ── bridge helpers ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bridge_call_and_eval(monkeypatch):
    _Http.routes = {("POST", "/session/evaluate"): (200, {"result": 42})}
    monkeypatch.setattr(H.httpx, "AsyncClient", _Http)
    code, data = await H._bridge_call("/session/evaluate", "twitter", {"e": 1})
    assert code == 200 and data["result"] == 42
    assert await H._bridge_eval("twitter", "x") == 42

    _Http.routes = {}
    assert await H._bridge_eval("twitter", "x") is None


@pytest.mark.asyncio
async def test_bridge_poll_done(monkeypatch):
    calls = {"n": 0}

    async def _call(path, platform, body=None, timeout=60.0):
        calls["n"] += 1
        return 200, {"platform": platform, "status": "done", "cookies_found": calls["n"] > 1}

    monkeypatch.setattr(H, "_bridge_call", _call)
    assert await H._bridge_poll_done("twitter", seconds=9) is True

    calls["n"] = -99
    assert await H._bridge_poll_done("twitter", seconds=3) is False


@pytest.mark.asyncio
async def test_bridge_extract_and_verify(monkeypatch):
    async def _call(path, platform, body=None, timeout=60.0):
        if path == "/session/extract":
            return 200, {"cookies": {"a": "b"}}
        if path == "/session/navigate":
            return 200, {}
        return 200, {}

    monkeypatch.setattr(H, "_bridge_call", _call)
    sync = AsyncMock()
    monkeypatch.setattr(H, "_sync_browser_cookies", sync)
    assert await H._bridge_extract("twitter") is True
    sync.assert_awaited_once()

    monkeypatch.setattr(H, "_bridge_eval", AsyncMock(return_value=False))
    assert await H._bridge_verify_not_login("twitter") is True
    monkeypatch.setattr(H, "_bridge_eval", AsyncMock(return_value=True))
    assert await H._bridge_verify_not_login("twitter") is False


@pytest.mark.asyncio
async def test_bootstrap_threads(monkeypatch):
    async def _call(path, platform, body=None, timeout=60.0):
        return 200, {}

    monkeypatch.setattr(H, "_bridge_call", _call)
    monkeypatch.setattr(H, "_bridge_eval", AsyncMock(side_effect=[None, "Welcome to Threads", False]))
    assert await H._bootstrap_threads_via_instagram() is True

    # click SSO fails → False
    async def _call_fail(path, platform, body=None, timeout=60.0):
        if path == "/session/click":
            return 404, {}
        return 200, {}

    monkeypatch.setattr(H, "_bridge_call", _call_fail)
    monkeypatch.setattr(H, "_bridge_eval", AsyncMock(return_value=None))
    assert await H._bootstrap_threads_via_instagram() is False


@pytest.mark.asyncio
async def test_bridge_login_and_inject(monkeypatch):
    monkeypatch.setattr(H, "_bridge_call", AsyncMock(return_value=(200, {"status": "done"})))
    assert await H._bridge_login("twitter", "u", "p") is True
    monkeypatch.setattr(H, "_bridge_call", AsyncMock(return_value=(200, {"status": "error"})))
    assert await H._bridge_login("twitter", "u", "p") is False
    monkeypatch.setattr(H, "_bridge_call", AsyncMock(return_value=(200, {"added": 3})))
    assert await H._bridge_inject_cookies("tiktok", []) is True


@pytest.mark.asyncio
async def test_recover_bridge_platform_paths(monkeypatch):
    monkeypatch.setattr(H, "_bootstrap_threads_via_instagram", AsyncMock(return_value=True))
    assert await H._recover_bridge_platform("threads") is True

    # facebook: stored cookies → inject → verify
    monkeypatch.setattr(H, "_load_storage_state", AsyncMock(return_value={"cookies": [1]}))
    monkeypatch.setattr(H, "_bridge_inject_cookies", AsyncMock(return_value=True))
    monkeypatch.setattr(H, "_bridge_verify_not_login", AsyncMock(return_value=True))
    assert await H._recover_bridge_platform("facebook") is True

    # tiktok: secret sessionid
    monkeypatch.setattr(H, "_secret", AsyncMock(return_value="sid"))
    assert await H._recover_bridge_platform("tiktok") is True

    # instagram: creds → bridge login
    monkeypatch.setattr(H, "_env", lambda n: "v")
    monkeypatch.setattr(H, "_bridge_login", AsyncMock(return_value=True))
    assert await H._recover_bridge_platform("instagram") is True

    # twitter: account username + password
    monkeypatch.setattr(H, "_account_username", AsyncMock(return_value="@cu_dev"))
    assert await H._recover_bridge_platform("twitter") is True

    # unknown platform → False
    assert await H._recover_bridge_platform("myspace") is False

    # facebook without stored state → False
    monkeypatch.setattr(H, "_load_storage_state", AsyncMock(return_value=None))
    assert await H._recover_bridge_platform("facebook") is False


@pytest.mark.asyncio
async def test_heal_bridge_platform_states(monkeypatch):
    # already healthy
    monkeypatch.setattr(H, "_bridge_call", AsyncMock(return_value=(200, {"platform": "twitter", "status": "done", "cookies_found": True})))
    monkeypatch.setattr(H, "_clear_dead_marker", AsyncMock())
    stats = {}
    await H._heal_bridge_platform("twitter", stats)
    assert stats["twitter"]["status"] == "healthy"

    # 409 contention
    monkeypatch.setattr(H, "_bridge_call", AsyncMock(side_effect=[(200, {}), (409, {})]))
    stats = {}
    await H._heal_bridge_platform("twitter", stats)
    assert stats["twitter"]["status"] == "contended"

    # poll_done + extract → healthy
    monkeypatch.setattr(H, "_bridge_call", AsyncMock(side_effect=[(200, {}), (200, {})]))
    monkeypatch.setattr(H, "_bridge_poll_done", AsyncMock(return_value=True))
    monkeypatch.setattr(H, "_bridge_extract", AsyncMock(return_value=True))
    monkeypatch.setattr(H, "_alert_recovered", AsyncMock())
    stats = {}
    await H._heal_bridge_platform("twitter", stats)
    assert stats["twitter"]["status"] == "healthy"

    # recovery succeeds + verify → recovered
    monkeypatch.setattr(H, "_bridge_call", AsyncMock(return_value=(200, {})))
    monkeypatch.setattr(H, "_bridge_poll_done", AsyncMock(return_value=False))
    monkeypatch.setattr(H, "_recover_bridge_platform", AsyncMock(return_value=True))
    monkeypatch.setattr(H, "_bridge_verify_not_login", AsyncMock(return_value=True))
    stats = {}
    await H._heal_bridge_platform("twitter", stats)
    assert stats["twitter"]["status"] == "recovered"

    # everything fails → needs_manual_login
    monkeypatch.setattr(H, "_recover_bridge_platform", AsyncMock(return_value=False))
    monkeypatch.setattr(H, "_alert", AsyncMock())
    stats = {}
    await H._heal_bridge_platform("twitter", stats)
    assert stats["twitter"]["status"] == "needs_manual_login"


# ── orchestrator ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_heal_all_lock_contention(_redis):
    _redis.store["session_healer:lock"] = "1"
    stats = await H.heal_all_sessions()
    assert stats["skipped"] == "another heal run in progress"


@pytest.mark.asyncio
async def test_heal_all_orchestration(monkeypatch):
    monkeypatch.setattr(H, "_platforms_with_accounts", AsyncMock(return_value={"linkedin", "facebook", "twitter", "tiktok"}))
    li = AsyncMock()
    fb = AsyncMock()
    bp = AsyncMock()
    monkeypatch.setattr(H, "_heal_linkedin", li)
    monkeypatch.setattr(H, "_heal_facebook_sidecar", fb)
    monkeypatch.setattr(H, "_heal_bridge_platform", bp)
    stats = await H.heal_all_sessions()
    li.assert_awaited_once()
    fb.assert_awaited_once()
    bp_platforms = [c.args[0] for c in bp.await_args_list]
    assert bp_platforms == ["facebook", "twitter", "tiktok"]
    assert "session_healer:lock" not in (await H._get_redis()).store
    assert stats["unhealthy"] == []


@pytest.mark.asyncio
async def test_heal_all_unhealthy_list(monkeypatch):
    monkeypatch.setattr(H, "_platforms_with_accounts", AsyncMock(return_value={"twitter"}))

    async def _broken(platform, stats):
        stats.setdefault(platform, {})["status"] = "needs_manual_login"

    monkeypatch.setattr(H, "_heal_bridge_platform", _broken)
    stats = await H.heal_all_sessions()
    assert stats["unhealthy"] == ["twitter"]
