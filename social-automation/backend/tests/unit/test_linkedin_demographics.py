"""Unit tests for linkedin_demographics daily sync task."""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.worker.tasks import linkedin_demographics as ld


def _account(scopes=None, meta=None, platform="linkedin", account_type="organization"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        platform=platform,
        account_type=account_type,
        account_id="108614163",
        scopes=scopes,
        meta_data=meta or {},
        access_token_enc=b"tok",
    )


def test_org_urn_from_meta():
    acc = _account(meta={"author_urn": "urn:li:organization:99"})
    assert ld._org_urn(acc) == "urn:li:organization:99"


def test_org_urn_fallback():
    acc = _account()
    assert ld._org_urn(acc) == "urn:li:organization:108614163"


def test_scope_gate():
    assert ld._has_scope(_account(scopes=[]))
    assert ld._has_scope(_account(scopes=["r_organization_followers"]))
    assert not ld._has_scope(_account(scopes=["openid"]))


@pytest.mark.asyncio
async def test_sync_account_missing_scope():
    acc = _account(scopes=["openid"])
    db = MagicMock()
    out = await ld._sync_account(db, acc)
    assert out == {"skipped": "missing-scope"}
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_sync_account_writes_two_events(monkeypatch):
    monkeypatch.setattr(ld, "decrypt_token", lambda b: "TOKEN")

    async def fake_get(client, path):
        return {"status": 200, "data": {"elements": [{"a": 1}, {"b": 2}]}}

    monkeypatch.setattr(ld, "_get", fake_get)
    acc = _account()
    db = MagicMock()
    out = await ld._sync_account(db, acc)
    assert out == {"followers": 2, "page_stats": 2}
    kinds = [c.args[0].event_type for c in db.add.call_args_list]
    assert kinds == ["linkedin_org_follower_demographics", "linkedin_org_page_stats"]


@pytest.mark.asyncio
async def test_sync_account_api_error(monkeypatch):
    monkeypatch.setattr(ld, "decrypt_token", lambda b: "TOKEN")

    async def fake_get(client, path):
        return {"status": 403, "error": "denied"}

    monkeypatch.setattr(ld, "_get", fake_get)
    acc = _account()
    db = MagicMock()
    out = await ld._sync_account(db, acc)
    assert out == {"followers": 403, "page_stats": 403}
    assert db.add.call_count == 2


def test_task_happy(monkeypatch):
    called = {}

    async def fake_run():
        called["ran"] = True
        return {"ok": True}

    monkeypatch.setattr(ld, "run_async", lambda coro: {"ok": True} if coro else {})
    out = ld.sync_linkedin_demographics()
    assert out == {"ok": True}


@pytest.mark.asyncio
async def test_get_success_and_error():
    class FakeResp:
        def __init__(self, code, data=None, text=""):
            self.status_code = code
            self._d = data or {}
            self.text = text

        def json(self):
            return self._d

    class FakeClient:
        def __init__(self, resp):
            self.resp = resp

        async def get(self, url, timeout=None):
            return self.resp

    ok = await ld._get(FakeClient(FakeResp(200, {"elements": [1]})), "x")
    assert ok == {"status": 200, "data": {"elements": [1]}}
    bad = await ld._get(FakeClient(FakeResp(429, text="rate limited")), "x")
    assert bad["status"] == 429
    assert "rate limited" in bad["error"]


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeDB:
    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.committed = False

    async def execute(self, _q):
        return _FakeResult(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


class _FakeSession:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_run_inner_fallback_and_account_error(monkeypatch):
    acc = _account()
    db = _FakeDB([[], [acc]])  # org query empty -> fallback returns acc
    monkeypatch.setattr(ld, "task_session", lambda: _FakeSession(db))

    async def boom(_db, _acc):
        raise RuntimeError("nope")

    monkeypatch.setattr(ld, "_sync_account", boom)
    coros = []
    monkeypatch.setattr(ld, "run_async", lambda c: coros.append(c) or "sentinel")
    out = ld.sync_linkedin_demographics()
    assert out == "sentinel"
    res = await coros[0]
    assert res[str(acc.id)] == {"error": "nope"}
    assert db.committed


@pytest.mark.asyncio
async def test_run_inner_happy_path(monkeypatch):
    acc = _account()
    db = _FakeDB([[acc]])
    monkeypatch.setattr(ld, "task_session", lambda: _FakeSession(db))

    async def ok_sync(_db, _acc):
        return {"followers": 1, "page_stats": 1}

    monkeypatch.setattr(ld, "_sync_account", ok_sync)
    coros = []
    monkeypatch.setattr(ld, "run_async", lambda c: coros.append(c) or "sentinel")
    ld.sync_linkedin_demographics()
    res = await coros[0]
    assert res[str(acc.id)] == {"followers": 1, "page_stats": 1}
    assert db.committed
