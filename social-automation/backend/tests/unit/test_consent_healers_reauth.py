"""consent_healers: X bridge re-auth, credentials login, media backfill."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.worker.tasks import consent_healers as M


def _db(first=None, all_=None):
    db = MagicMock()
    res = MagicMock()
    res.scalars.return_value.first.return_value = first
    res.scalars.return_value.all.return_value = all_ or []
    db.execute = AsyncMock(return_value=res)
    db.commit = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    return db


def _resp(status=200, payload=None):
    r = MagicMock(status_code=status)
    r.json = MagicMock(return_value=payload if payload is not None else {})
    return r


class _Bridge:
    """Records bridge posts; evaluate of the wall probe returns ``wall``."""

    def __init__(self, wall="Home|feed", click_status=200):
        self.posts: list = []
        self.wall = wall
        self.click_status = click_status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        self.posts.append((url, json))
        if url.endswith("/session/evaluate") and "document.title" in (json or {}).get("expression", ""):
            return _resp(200, {"result": self.wall})
        if url.endswith("/session/click") and "Authorize" in (json or {}).get("selector", ""):
            return _resp(self.click_status, {"ok": True})
        return _resp(200, {})


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(M, "asyncio", SimpleNamespace(sleep=AsyncMock()))


@pytest.mark.asyncio
async def test_twitter_decrypt_failure_skips(monkeypatch):
    acc = MagicMock(status="expired", access_token_enc=b"x")
    monkeypatch.setattr(M, "task_session", lambda: _db(acc))

    def boom(_):
        raise ValueError("bad")

    monkeypatch.setattr(M, "decrypt_token", boom)
    assert await M._twitter() == "skip"


@pytest.mark.asyncio
async def test_twitter_probe_fails_falls_back_to_bridge(monkeypatch):
    acc = MagicMock(status="expired", access_token_enc=b"x", team_id="team-1")
    monkeypatch.setattr(M, "task_session", lambda: _db(acc))
    monkeypatch.setattr(M, "decrypt_token", lambda b: "tok")
    client = MagicMock()
    client.get = AsyncMock(return_value=_resp(401))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: client)
    reauth = AsyncMock(return_value="approve-fired {}")
    monkeypatch.setattr(M, "_try_bridge_reauth", reauth)
    assert await M._twitter() == "approve-fired {}"
    reauth.assert_awaited_once_with("team-1")


@pytest.mark.asyncio
async def test_credentials_login_skips_without_creds(monkeypatch, no_sleep):
    monkeypatch.delenv("TWITTER_LOGIN_USERNAME", raising=False)
    monkeypatch.delenv("TWITTER_LOGIN_PASSWORD", raising=False)
    c = _Bridge()
    await M._x_credentials_login(c, "http://b")
    assert c.posts == []


@pytest.mark.asyncio
async def test_credentials_login_fills_steps_with_totp(monkeypatch, no_sleep):
    monkeypatch.setenv("TWITTER_LOGIN_USERNAME", "user")
    monkeypatch.setenv("TWITTER_LOGIN_EMAIL", "")  # email step skipped
    monkeypatch.setenv("TWITTER_LOGIN_PASSWORD", "pw")
    monkeypatch.setenv("TWITTER_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
    c = _Bridge()
    await M._x_credentials_login(c, "http://b")
    fills = [j["value"] for u, j in c.posts if u.endswith("/session/fill")]
    assert fills[0] == "user" and "pw" in fills
    # one TOTP fill after each of the two credential steps
    assert len(fills) == 4 and all(len(v) == 6 and v.isdigit() for v in (fills[1], fills[3]))
    assert c.posts[0][0] == "http://b/session/evaluate"


@pytest.mark.asyncio
async def test_credentials_login_totp_error_swallowed(monkeypatch, no_sleep):
    monkeypatch.setenv("TWITTER_LOGIN_USERNAME", "user")
    monkeypatch.setenv("TWITTER_LOGIN_EMAIL", "e@x")
    monkeypatch.setenv("TWITTER_LOGIN_PASSWORD", "pw")
    monkeypatch.setenv("TWITTER_TOTP_SECRET", "not base32 !!")
    c = _Bridge()
    await M._x_credentials_login(c, "http://b")
    fills = [j["value"] for u, j in c.posts if u.endswith("/session/fill")]
    assert fills == ["user", "e@x", "pw"]


@pytest.fixture
def oauth(monkeypatch):
    import app.core.config as cfg
    import app.core.security as sec

    monkeypatch.setattr(sec, "sign_oauth_state", lambda d: "signed-state")
    monkeypatch.setattr(cfg, "settings", SimpleNamespace(TWITTER_CLIENT_ID="cid", TWITTER_REDIRECT_URI="https://cb"))


@pytest.mark.asyncio
async def test_bridge_reauth_with_cookies_no_wall(monkeypatch, no_sleep, oauth):
    monkeypatch.setenv("BROWSER_ADHOC_URL", "http://adhoc")
    monkeypatch.setenv("X_WEB_AUTH_TOKEN", "at")
    monkeypatch.setenv("X_WEB_CT0", "")
    bridge = _Bridge()
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: bridge)
    login = AsyncMock()
    monkeypatch.setattr(M, "_x_credentials_login", login)
    out = await M._try_bridge_reauth("team-1")
    assert out.startswith("approve-fired {")
    urls = [u for u, _ in bridge.posts]
    assert urls[0] == "http://adhoc/session/start"
    seed = next(j for u, j in bridge.posts if u.endswith("/session/seed"))
    assert [c["name"] for c in seed["cookies"]] == ["auth_token"]
    nav = [j["expression"] for u, j in bridge.posts if u.endswith("/session/evaluate")]
    assert any("client_id=cid" in e and "state=signed-state" in e for e in nav)
    login.assert_not_awaited()


@pytest.mark.asyncio
async def test_bridge_reauth_login_wall_no_cookies(monkeypatch, no_sleep, oauth):
    monkeypatch.delenv("X_WEB_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("X_WEB_CT0", raising=False)
    bridge = _Bridge(wall="Log in to X | Sign in", click_status=409)
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: bridge)
    login = AsyncMock()
    monkeypatch.setattr(M, "_x_credentials_login", login)
    assert await M._try_bridge_reauth("t") == "approve-fired 409"
    assert not any(u.endswith("/session/seed") for u, _ in bridge.posts)
    login.assert_awaited_once()


@pytest.mark.asyncio
async def test_bridge_reauth_error(monkeypatch, no_sleep, oauth):
    def boom(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(M.httpx, "AsyncClient", boom)
    assert await M._try_bridge_reauth("t") == "still-expired (ConnectionError)"


def test_heal_consents_runs_all(monkeypatch):
    for name in ("_twitter", "_linkedin_snapshot", "_whatsapp", "_bridge_health", "_dmr_health"):
        monkeypatch.setattr(M, name, AsyncMock(return_value=name))
    monkeypatch.setattr(M, "run_async", lambda coro: asyncio.run(coro))
    out = M.heal_consents.run()
    assert out == {"twitter": "_twitter", "linkedin": "_linkedin_snapshot", "whatsapp": "_whatsapp", "bridge": "_bridge_health", "dmr": "_dmr_health"}


def test_media_backfill_task(monkeypatch):
    monkeypatch.setattr(M, "_media_backfill", AsyncMock(return_value={"attached": 0, "failed": 0}))
    assert M.media_backfill.run() == {"attached": 0, "failed": 0}


@pytest.mark.asyncio
async def test_media_backfill_sweep(monkeypatch):
    import base64

    import app.services.inference as inf
    import app.services.media_storage as ms
    import app.worker._async as wa

    ok = SimpleNamespace(meta_data={"needs_media": "true", "topic": "AI ops"}, content_text="", team_id="t", user_id="u", media_ids=[])
    no_img = SimpleNamespace(meta_data=None, content_text="body text", team_id="t", user_id="u", media_ids=[])
    err = SimpleNamespace(meta_data={"needs_media": "true"}, content_text="x", team_id="t", user_id="u", media_ids=[])
    db = _db(all_=[ok, no_img, err])
    monkeypatch.setattr(wa, "task_session", lambda: db)
    gen = AsyncMock(side_effect=[{"image_base64": base64.b64encode(b"png").decode()}, None, RuntimeError("ai")])
    monkeypatch.setattr(inf, "_call_workers_ai_image", gen)
    persist = AsyncMock(return_value=SimpleNamespace(id="asset-1"))
    monkeypatch.setattr(ms, "persist_generated_image", persist)

    out = await M._media_backfill()
    assert out == {"attached": 1, "failed": 2}
    assert ok.media_ids == ["asset-1"] and ok.meta_data["needs_media"] is False
    assert persist.await_args.kwargs["image_bytes"] == b"png"
    assert "AI ops" in gen.await_args_list[0].args[0]
    assert "body text" in gen.await_args_list[1].args[0]
    db.commit.assert_awaited_once()
