"""Unit tests for small API routers: audit.py, secrets.py, cf_db.py.

Routes are invoked directly (no TestClient) per repo convention; DB and
secret-store/CF clients are faked at module boundaries.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.audit as AUD
import app.api.cf_db as CFD
import app.api.secrets as SEC

# ── audit.py ────────────────────────────────────────────────────────


class _Res:
    def __init__(self, scalars=None, scalar=None, all_=None):
        self._scalars = scalars
        self._scalar = scalar
        self._all = all_

    def scalars(self):
        return self

    def first(self):
        return self._scalars

    def all(self):
        return self._all or []

    def scalar(self):
        return self._scalar


class _DB:
    """Sequential result feed for db.execute calls."""

    def __init__(self, results):
        self.results = list(results)

    async def execute(self, stmt):
        return self.results.pop(0)


def _log(**kw):
    base = {
        "id": uuid.uuid4(),
        "user_email": "a@b.c",
        "action": "login",
        "resource_type": "user",
        "resource_id": "u1",
        "detail": "d",
        "ip_address": "1.1.1.1",
        "created_at": datetime.now(UTC),
    }
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_list_audit_logs_requires_team():
    db = _DB([_Res(scalars=None)])
    with pytest.raises(HTTPException) as e:
        await AUD.list_audit_logs(current_user=SimpleNamespace(id=uuid.uuid4()), db=db)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_list_audit_logs_filters_and_paginates():
    tid = uuid.uuid4()
    logs = [_log(action="login"), _log(action="logout")]
    db = _DB(
        [
            _Res(scalars=tid),  # team lookup
            _Res(scalar=2),  # count
            _Res(all_=logs),  # entries
        ]
    )
    out = await AUD.list_audit_logs(
        action="login",
        resource_type="user",
        start_date=datetime(2026, 1, 1, tzinfo=UTC),
        end_date=datetime(2026, 12, 31, tzinfo=UTC),
        page=1,
        page_size=50,
        current_user=SimpleNamespace(id=uuid.uuid4()),
        db=db,
    )
    assert out.total == 2 and len(out.entries) == 2
    assert out.page == 1 and out.page_size == 50
    assert out.entries[0].action == "login"


# ── secrets.py ──────────────────────────────────────────────────────


@pytest.fixture
def store(monkeypatch):
    st = SimpleNamespace(
        list_keys=AsyncMock(return_value=[{"key": "K1"}]),
        get=AsyncMock(return_value="v"),
        set=AsyncMock(return_value={"d1": True, "pg": True}),
        delete=AsyncMock(return_value={"d1": True, "pg": True}),
    )
    monkeypatch.setattr(SEC, "secret_store", st)
    return st


@pytest.mark.asyncio
async def test_list_secrets(store):
    out = await SEC.list_secrets(current_user=SimpleNamespace())
    assert out == {"secrets": [{"key": "K1"}]}


@pytest.mark.asyncio
async def test_get_secret_found_and_missing(store):
    out = await SEC.get_secret("K1", current_user=SimpleNamespace())
    assert out["value"] == "v"
    store.get.return_value = None
    with pytest.raises(HTTPException) as e:
        await SEC.get_secret("NOPE", current_user=SimpleNamespace())
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_set_and_update_secret(store):
    out = await SEC.set_secret("K", SEC.SecretValue(value="x"), current_user=SimpleNamespace())
    assert out["key"] == "K" and out["value"] == "***"  # masked
    out = await SEC.update_secret("K", SEC.SecretUpdate(value="y"), current_user=SimpleNamespace())
    assert out["value"] == "***"


@pytest.mark.asyncio
async def test_delete_secret(store):
    await SEC.delete_secret("K1", current_user=SimpleNamespace())
    store.get.return_value = None
    with pytest.raises(HTTPException) as e:
        await SEC.delete_secret("K1", current_user=SimpleNamespace())
    assert e.value.status_code == 404

    store.get.return_value = "exists"
    store.delete.return_value = {"d1": False, "pg": False}
    with pytest.raises(HTTPException) as e:
        await SEC.delete_secret("K1", current_user=SimpleNamespace())
    assert e.value.status_code == 409


@pytest.mark.asyncio
async def test_secrets_require_auth(store):
    for fn in (
        SEC.list_secrets(current_user=None),
        SEC.get_secret("k", current_user=None),
        SEC.set_secret("k", SEC.SecretValue(value="v"), current_user=None),
        SEC.update_secret("k", SEC.SecretUpdate(value="v"), current_user=None),
        SEC.delete_secret("k", current_user=None),
    ):
        with pytest.raises(HTTPException) as e:
            await fn
        assert e.value.status_code == 401


# ── cf_db.py ────────────────────────────────────────────────────────


@pytest.fixture
def cf(monkeypatch):
    m = SimpleNamespace(
        d1=SimpleNamespace(
            health=AsyncMock(return_value={"ok": 1}),
            write_budget=AsyncMock(return_value={"b": 1}),
            enabled=True,
            list_tables=AsyncMock(return_value=["t1", "_cf_internal", "t2"]),
            count=AsyncMock(return_value=5),
        ),
        kv=SimpleNamespace(health=AsyncMock(return_value={"ok": 1})),
        vec=SimpleNamespace(health=AsyncMock(return_value={"ok": 1})),
        router=SimpleNamespace(health=AsyncMock(return_value={"r": 1}), replay_queue=AsyncMock(return_value=3), _replay_queue=[1, 2]),
        sync=SimpleNamespace(full_bidirectional_sync=AsyncMock(return_value={"s": 1})),
    )
    monkeypatch.setattr(CFD, "d1_client", m.d1)
    monkeypatch.setattr(CFD, "kv_client", m.kv)
    monkeypatch.setattr(CFD, "vectorize_client", m.vec)
    monkeypatch.setattr(CFD, "db_router", m.router)
    monkeypatch.setattr(CFD, "sync_service", m.sync)
    return m


@pytest.mark.asyncio
async def test_cf_db_health(cf):
    out = await CFD.cf_db_health()
    assert out["d1"]["ok"] == 1 and out["router"]["r"] == 1
    assert "tokens_available" in out and "account_id_configured" in out


@pytest.mark.asyncio
async def test_cf_db_status_sync_replay(cf):
    assert await CFD.cf_db_status(current_user=SimpleNamespace()) == {"r": 1}
    assert await CFD.cf_db_sync(current_user=SimpleNamespace()) == {"s": 1}
    out = await CFD.cf_db_replay(current_user=SimpleNamespace())
    assert out == {"replayed": 3, "remaining": 2}


@pytest.mark.asyncio
async def test_cf_db_tables(cf):
    out = await CFD.cf_db_tables(current_user=SimpleNamespace())
    assert out["enabled"] is True
    assert "_cf_internal" not in out["tables"]  # CF-internal filtered
    assert out["tables"] == {"t1": 5, "t2": 5}

    cf.d1.count.side_effect = RuntimeError("fail")
    out = await CFD.cf_db_tables(current_user=SimpleNamespace())
    assert out["tables"]["t1"] == -1  # count error → -1 marker

    cf.d1.enabled = False
    out = await CFD.cf_db_tables(current_user=SimpleNamespace())
    assert out == {"enabled": False, "tables": []}


@pytest.mark.asyncio
async def test_cf_db_sync_tables(cf):
    out = await CFD.cf_db_sync_tables(current_user=SimpleNamespace())
    assert out["count"] == len(out["tables"])
    assert out["count"] > 0
