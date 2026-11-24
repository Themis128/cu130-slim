"""Unit tests for linkedin_demographics daily sync task."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
