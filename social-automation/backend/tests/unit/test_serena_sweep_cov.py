"""Coverage for guards added by the serena sweep batches (#2789/#2804/#2806/#2808).

dmr_health keep-warm run lock, consent_healers vanished-account refresh,
synigoros_status environ override + empty-eval raise, tiktok_inbox_reconcile
missing open_id.
"""

from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.dmr_health as DH

# ── dmr_health.keep_warm_dmr_models run lock ────────────────────────────────


class _FakeRedis:
    def __init__(self, last=None, set_ok=True, get_raises=False, release_raises=False):
        self.store: dict = {}
        if last is not None:
            self.store[DH._KEEP_WARM_LAST] = last
        self._set_ok = set_ok
        self._get_raises = get_raises
        self._release_raises = release_raises
        self.deleted: list = []

    def get(self, key):
        if self._get_raises:
            raise ConnectionError("redis down")
        if self._release_raises and key == DH._KEEP_WARM_LOCK:
            raise ConnectionError("redis gone mid-run")
        return self.store.get(key)

    def set(self, key, val, nx=False, ex=None):
        if nx:
            if not self._set_ok:
                return False
        self.store[key] = val
        return True

    def delete(self, key):
        self.deleted.append(key)
        self.store.pop(key, None)


def _stub_run(monkeypatch, result=None):
    calls = []

    def _run(coro):
        coro.close()
        calls.append(1)
        return result or {"warmed": ["emb"], "skipped": []}

    monkeypatch.setattr(DH, "_run_async", _run)
    return calls


def test_keep_warm_lock_client_builds_redis(monkeypatch):
    import redis

    seen = {}

    def _from_url(url, decode_responses=False):
        seen["url"], seen["decode"] = url, decode_responses
        return "client"

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(_from_url))
    assert DH._keep_warm_lock_client() == "client"
    assert seen["decode"] is True


def test_keep_warm_lock_client_fail_open(monkeypatch):
    import redis

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(lambda *a, **k: (_ for _ in ()).throw(ValueError("bad url"))))
    assert DH._keep_warm_lock_client() is None


def test_keep_warm_recent_run_bytes_skips(monkeypatch):
    calls = _stub_run(monkeypatch)
    r = _FakeRedis(last=str(time.time()).encode())
    monkeypatch.setattr(DH, "_keep_warm_lock_client", lambda: r)
    assert DH.keep_warm_dmr_models()["reason"] == "recent-run"
    assert not calls


def test_keep_warm_run_in_progress_skips(monkeypatch):
    calls = _stub_run(monkeypatch)
    r = _FakeRedis(last=str(time.time() - 3600), set_ok=False)
    monkeypatch.setattr(DH, "_keep_warm_lock_client", lambda: r)
    assert DH.keep_warm_dmr_models()["reason"] == "run-in-progress"
    assert not calls


def test_keep_warm_locked_run_releases_and_stamps(monkeypatch):
    calls = _stub_run(monkeypatch, {"warmed": ["emb"], "skipped": []})
    r = _FakeRedis()
    monkeypatch.setattr(DH, "_keep_warm_lock_client", lambda: r)
    before = time.time()
    assert DH.keep_warm_dmr_models() == {"warmed": ["emb"], "skipped": []}
    assert calls == [1]
    assert DH._KEEP_WARM_LOCK in r.deleted
    assert float(r.store[DH._KEEP_WARM_LAST]) >= before


def test_keep_warm_lock_check_failure_proceeds_unlocked(monkeypatch):
    calls = _stub_run(monkeypatch)
    r = _FakeRedis(get_raises=True)
    monkeypatch.setattr(DH, "_keep_warm_lock_client", lambda: r)
    assert DH.keep_warm_dmr_models()["warmed"] == ["emb"]
    assert calls == [1]
    assert not r.deleted


def test_keep_warm_release_failure_swallowed(monkeypatch):
    calls = _stub_run(monkeypatch)
    r = _FakeRedis(release_raises=True)
    monkeypatch.setattr(DH, "_keep_warm_lock_client", lambda: r)
    assert DH.keep_warm_dmr_models()["warmed"] == ["emb"]
    assert calls == [1]
    assert DH._KEEP_WARM_LAST not in r.store


# ── consent_healers._linkedin_token_health: account vanished mid-refresh ────


class _AsyncCM:
    def __init__(self, v):
        self._v = v

    async def __aenter__(self):
        return self._v

    async def __aexit__(self, *a):
        return False


class _Client:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        return SimpleNamespace(status_code=401)


@pytest.mark.asyncio
async def test_li_token_health_account_deleted_before_refresh_write(monkeypatch):
    import httpx

    import app.api.auth as auth
    import app.core.security as sec
    import app.worker.tasks.consent_healers as CH

    acc = SimpleNamespace(id="a1", team_id="t1", access_token_enc=b"x", refresh_token_enc=b"r")
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: acc))),
        get=AsyncMock(return_value=None),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(CH, "task_session", lambda: _AsyncCM(db))
    monkeypatch.setattr(CH, "decrypt_token", lambda b: "tok")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client())
    monkeypatch.setattr(auth, "linkedin_client", SimpleNamespace(refresh_token=AsyncMock(return_value={"access_token": "new"})))
    monkeypatch.setattr(sec, "encrypt_token", lambda t: b"enc")
    assert await CH._linkedin_token_health() == "skip"
    db.commit.assert_not_awaited()


# ── synigoros_status ────────────────────────────────────────────────────────


def test_synigoros_env_file_environ_overrides(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_status as M

    f = tmp_path / ".env"
    f.write_text("GMAIL_REFRESH_TOKEN=from-file\nGMAIL_OAUTH_CLIENT_ID=cid\n")
    monkeypatch.setattr(M, "ENV_FILE", str(f))
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "from-env")
    monkeypatch.delenv("GMAIL_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GMAIL_OAUTH_CLIENT_SECRET", raising=False)
    out = M._env_file()
    assert out["GMAIL_REFRESH_TOKEN"] == "from-env"
    assert out["GMAIL_OAUTH_CLIENT_ID"] == "cid"


def test_synigoros_poll_inner_empty_eval_raises(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    def _ev(expr):
        if expr == "!!document.getElementById('webMail')":
            return False
        if expr == "document.body.innerText.includes('Κατάσταση')":
            return True
        if "Δεν έγινε" in expr:
            return False
        return ""  # dead bridge session: the extract eval comes back empty

    monkeypatch.setattr(M, "_bridge", lambda p, d, **k: {"ok": 1})
    monkeypatch.setattr(M, "_ev", _ev)
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="eval returned empty"):
        M._poll_inner()


# ── tiktok_inbox_reconcile: account without open_id ─────────────────────────


@pytest.mark.asyncio
async def test_tiktok_reconcile_missing_open_id_records_error(monkeypatch):
    import app.services.tiktok_api as tt
    import app.worker.tasks.tiktok_inbox_reconcile as rec

    acc = SimpleNamespace(id=uuid.uuid4(), access_token_enc=b"enc", username="u", meta_data={})
    t = SimpleNamespace(id=uuid.uuid4(), platform_post_id="pub_9", post_id="p9", social_account=acc, post=None, error_message=None)

    class _DB:
        async def execute(self, q):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [t]))

        async def commit(self):
            pass

    monkeypatch.setattr(rec, "_worker_db", lambda: _AsyncCM(_DB()))
    monkeypatch.setattr(rec, "decrypt_token", lambda b: "tok")
    monkeypatch.setattr(tt, "is_tiktok_publish_id", lambda pid: True)
    ctor = AsyncMock()
    monkeypatch.setattr(tt, "TikTokAPIClient", ctor)
    out = await rec._reconcile_async()
    assert out["checked"] == 1
    assert out["errors"] == ["target pub_9: account meta_data missing open_id"]
    ctor.assert_not_called()
