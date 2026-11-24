"""ops_services registry: allowed_actions gate + status projection."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import ops_services as api
from app.api.ops_services import _allowed, _out
from app.models.ops_service import (
    OpsService,
    ServiceExecutor,
    ServiceKind,
    ServiceManagedBy,
    ServiceTier,
)


def _svc(**kw) -> OpsService:
    s = OpsService(
        name="svc-" + uuid.uuid4().hex[:8],
        title="t",
        tier=ServiceTier.DOCKER,
        kind=ServiceKind.CONTAINER,
        executor=ServiceExecutor.DOCKER_CLI,
        managed_by=ServiceManagedBy.PLATFORM,
        target="c1",
        meta={"allowed_actions": ["status", "logs"]},
    )
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_observe_only_blocks_restart():
    assert _allowed(_svc(), "status")
    assert _allowed(_svc(), "logs")
    assert not _allowed(_svc(), "restart")
    assert not _allowed(_svc(), "stop")


def test_manage_allows_lifecycle():
    s = _svc(meta={"allowed_actions": ["status", "logs", "restart", "start", "stop"]})
    for a in ("status", "logs", "restart", "start", "stop"):
        assert _allowed(s, a)


def test_default_is_status_only():
    assert _allowed(_svc(meta={}), "status")
    assert not _allowed(_svc(meta={}), "restart")


def test_out_disabled_projection():
    s = _svc(enabled=False, status="running")
    assert _out(s)["status"] == "disabled"


def test_out_pending_action_visible():
    s = _svc(pending_action="restart")
    assert _out(s)["pending_action"] == "restart"


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return _Scalars(self._rows)


class FakeDB:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.queue = []
        self.commits = 0
        self.added = []

    async def execute(self, _q):
        if self.queue:
            return _Result(self.queue.pop(0))
        return _Result(self.rows)

    def add(self, obj):
        self.added.append(obj)
        self.rows.append(obj)

    async def commit(self):
        self.commits += 1


def _req(token="tok"):
    return SimpleNamespace(headers={"X-Service-Token": token})


def _agent_token(monkeypatch, value="tok"):
    monkeypatch.setattr(api, "get_settings", lambda: SimpleNamespace(OPS_WATCHER_AGENT_TOKEN=value))


def test_agent_token_unset_503(monkeypatch):
    _agent_token(monkeypatch, "")
    with pytest.raises(HTTPException) as e:
        api._require_agent_token(_req())
    assert e.value.status_code == 503


def test_agent_token_bad_401(monkeypatch):
    _agent_token(monkeypatch)
    with pytest.raises(HTTPException) as e:
        api._require_agent_token(_req("wrong"))
    assert e.value.status_code == 401


def test_agent_token_ok(monkeypatch):
    _agent_token(monkeypatch)
    api._require_agent_token(_req())


@pytest.mark.asyncio
async def test_list_services_filters():
    s1 = _svc()
    s2 = _svc(meta={"allowed_actions": ["status", "restart"]})
    s1.enabled = True
    s2.enabled = False
    db = FakeDB([s1, s2])
    out = await api.list_services(db=db, current_user=None)
    assert out["count"] == 2
    assert out["services"][1]["status"] == "disabled"
    out = await api.list_services(tier=s2.tier.value, status="disabled", db=db, current_user=None)
    assert out["count"] == 1


@pytest.mark.asyncio
async def test_service_action_paths():
    s = _svc()
    s.enabled = True
    db = FakeDB([s])
    with pytest.raises(HTTPException) as e:
        await api.service_action(s.name, api.ActionBody(action="nope"), db=db, current_user=None)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        await api.service_action("missing", api.ActionBody(action="status"), db=FakeDB(), current_user=None)
    assert e.value.status_code == 404
    s.enabled = False
    with pytest.raises(HTTPException) as e:
        await api.service_action(s.name, api.ActionBody(action="status"), db=db, current_user=None)
    assert e.value.status_code == 409
    s.enabled = True
    with pytest.raises(HTTPException) as e:
        await api.service_action(s.name, api.ActionBody(action="restart"), db=db, current_user=None)
    assert e.value.status_code == 403
    out = await api.service_action(s.name, api.ActionBody(action="status"), db=db, current_user=None)
    assert out["queued"] == "status" and s.pending_action == "status" and db.commits


@pytest.mark.asyncio
async def test_sync_services_upsert_and_agent_gate(monkeypatch):
    _agent_token(monkeypatch)
    existing = _svc()
    item = api.SyncItem(
        name=existing.name, title="new", tier="docker", kind="container", executor="docker-cli", managed_by="platform", target="t", meta={"a": 1}
    )
    item2 = api.SyncItem(name="new-svc", title="n2", tier="systemd", kind="service", executor="systemctl", managed_by="external")
    db = FakeDB([existing])
    db.queue = [[existing], []]
    out = await api.sync_services(api.SyncBody(services=[item, item2]), _req(), db=db)
    assert out["created"] == 1 and out["upserted"] == 2
    assert existing.meta["a"] == 1 and existing.meta["allowed_actions"] == ["status", "logs"]


@pytest.mark.asyncio
async def test_pending_and_report(monkeypatch):
    _agent_token(monkeypatch)
    s = _svc()
    s.pending_action = "restart"
    db = FakeDB([s])
    out = await api.pending_actions(_req(), db=db)
    assert len(out["actions"]) == 1
    s2 = _svc()
    db.queue = [[s], [s2], []]
    body = api.ReportBody(
        reports=[
            api.ReportItem(name=s.name, status="ok", health="up", detail="d"),
            api.ReportItem(name=s2.name, status="ok", health="up"),
            api.ReportItem(name="ghost"),
        ]
    )
    out = await api.service_report(body, _req(), db=db)
    assert out["applied"] == 2 and s.status == "ok" and s.meta["last_detail"] == "d" and "last_detail" not in s2.meta


@pytest.mark.asyncio
async def test_action_result_ok_fail_404(monkeypatch):
    _agent_token(monkeypatch)
    s = _svc()
    s.pending_action = "restart"
    s.consecutive_failures = 0
    db = FakeDB([s])
    out = await api.action_result(s.name, api.ActionResultBody(ok=False, output_tail="err"), _req(), db=db)
    assert out["ok"] is True and s.pending_action == "" and s.consecutive_failures == 1
    assert s.meta["last_action_output"] == "err"
    await api.action_result(s.name, api.ActionResultBody(ok=True), _req(), db=db)
    assert s.consecutive_failures == 0
    with pytest.raises(HTTPException) as e:
        await api.action_result("ghost", api.ActionResultBody(ok=True), _req(), db=FakeDB())
    assert e.value.status_code == 404
