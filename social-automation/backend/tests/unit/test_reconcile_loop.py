"""Coverage: account_status_reconcile._reconcile loop paths."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest


def _acc(platform="facebook", status="active", token="enc", expires=None, acc_id="a1", meta=None):
    a = MagicMock()
    a.platform = platform
    a.account_id = acc_id
    a.username = "acct"
    a.status = status
    a.access_token_enc = token
    a.token_expires_at = expires
    a.meta_data = meta or {}
    a.id = "uuid-1"
    return a


class _Sess:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


def _db(rows):
    res = MagicMock()
    res.scalars.return_value.all.return_value = rows
    db = MagicMock()
    db.execute = AsyncMock(return_value=res)
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_reconcile_all_paths(monkeypatch):
    import app.worker.tasks.account_status_reconcile as R

    now = datetime.now(UTC)
    rows = [
        _acc(platform="x", status="active", expires=now - timedelta(days=1)),  # expired w/o probe
        _acc(platform="facebook", status="revoked", token="enc2"),  # probe→active — revoked counts toward drift
        _acc(platform="whatsapp", status="active", token="enc3"),  # probe→revoked drift + slack
        _acc(platform="tiktok", status="active", token="enc4"),  # probe→inconclusive → error
        _acc(platform="mastodon", status="active", token="enc5"),  # unprobed → skip
        _acc(platform="linkedin", status="active", token="enc6"),  # probe→active clean
    ]
    db = _db(rows)
    monkeypatch.setattr(R, "task_session", lambda: _Sess(db))
    monkeypatch.setattr(R, "decrypt_token", lambda t: "tok" if t != "enc_bad" else "")
    probes = {"facebook": "active", "whatsapp": "revoked", "linkedin": "active", "tiktok": "inconclusive"}
    monkeypatch.setattr(R, "_probe", AsyncMock(side_effect=lambda p, a, t, m: probes.get(p, "unprobed")))
    alert = AsyncMock()
    monkeypatch.setattr(R, "post_alert_to_slack", alert)
    counts = await R._reconcile()
    assert counts["checked"] == 6
    assert counts["expired"] >= 1
    assert counts["revoked"] >= 1
    assert counts["error"] >= 1  # inconclusive/no-token
    assert counts["drifted"] >= 1
    alert.assert_awaited()


@pytest.mark.asyncio
async def test_reconcile_decrypt_failure(monkeypatch):
    import app.worker.tasks.account_status_reconcile as R

    rows = [_acc(platform="facebook", token="bad")]
    db = _db(rows)
    monkeypatch.setattr(R, "task_session", lambda: _Sess(db))
    monkeypatch.setattr(R, "decrypt_token", MagicMock(side_effect=Exception("x")))
    monkeypatch.setattr(R, "_probe", AsyncMock(return_value="inconclusive"))
    monkeypatch.setattr(R, "post_alert_to_slack", AsyncMock())
    counts = await R._reconcile()
    assert counts["error"] == 1  # empty token → inconclusive → error path


@pytest.mark.asyncio
async def test_reconcile_d1_mirror_swallowed(monkeypatch):
    import app.worker.tasks.account_status_reconcile as R

    rows = [_acc(platform="whatsapp", status="active", token="enc")]
    db = _db(rows)
    monkeypatch.setattr(R, "task_session", lambda: _Sess(db))
    monkeypatch.setattr(R, "decrypt_token", lambda t: "tok")
    monkeypatch.setattr(R, "_probe", AsyncMock(return_value="revoked"))
    monkeypatch.setattr(R, "post_alert_to_slack", AsyncMock())
    router = MagicMock()
    router.execute = AsyncMock(side_effect=RuntimeError("D1 down"))
    import sys

    mod = MagicMock()
    mod.db_router = router
    monkeypatch.setitem(sys.modules, "app.services.db_router", mod)
    counts = await R._reconcile()
    assert counts["drifted"] == 1


@pytest.mark.asyncio
async def test_ingest_bytes_empty_and_happy(monkeypatch):
    from app.services import research_workspace as RW

    # empty extract → early return
    monkeypatch.setattr(RW, "_extract", lambda n, r: "")
    assert await RW.ingest_bytes("t", "p", "n.txt", b"x") == {"ingested": 0, "file": "n.txt"}
    # happy path
    monkeypatch.setattr(RW, "_extract", lambda n, r: "Sentence one is long enough to pass. " * 10)
    monkeypatch.setattr(RW, "_get_embedding", AsyncMock(return_value=[0.1]))
    monkeypatch.setattr(RW, "_collection_id", AsyncMock(return_value="col1"))
    up = AsyncMock()
    monkeypatch.setattr(RW, "_upsert", up)
    out = await RW.ingest_bytes("t", "p", "n.txt", b"x")
    assert out["ingested"] > 0
    up.assert_awaited()
