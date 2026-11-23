"""Endpoint tests for app.api.ops_watchers + app.api.ops_services.

Route functions are invoked directly with a fake AsyncSession; the
service-token settings and celery app are monkeypatched. No network,
no database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import ops_services as svc_api
from app.api import ops_watchers as w_api
from app.models.ops_service import (
    OpsService,
    ServiceExecutor,
    ServiceKind,
    ServiceManagedBy,
    ServiceTier,
)
from app.models.ops_watcher import (
    OpsWatcher,
    WatcherExecutor,
    WatcherKind,
    WatcherManagedBy,
    WatcherTier,
)

TOKEN = "test-agent-token"


class _Scalars:
    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return list(self._items)


class _Result:
    def __init__(self, value):
        self._v = value if isinstance(value, list) else ([value] if value else [])

    def scalar_one_or_none(self):
        return self._v[0] if self._v else None

    def scalars(self):
        return _Scalars(self._v)


class FakeDB:
    """Name-aware row lookup; ``scripted`` results are consumed in order
    for endpoints that issue multiple distinct queries."""

    def __init__(self, watchers=(), services=(), scripted=()):
        self.watchers = list(watchers)
        self.services = list(services)
        self.scripted = list(scripted)
        self.added = []
        self.commits = 0

    async def execute(self, stmt):
        if self.scripted:
            return self.scripted.pop(0)
        entity = stmt.column_descriptions[0].get("entity")
        cparams = stmt.compile().params
        name = next((v for k, v in cparams.items() if k.startswith("name")), None)
        if entity is OpsWatcher:
            rows = self.watchers + [o for o in self.added if isinstance(o, OpsWatcher)]
        elif entity is OpsService:
            rows = self.services + [o for o in self.added if isinstance(o, OpsService)]
            where = str(stmt.whereclause) if stmt.whereclause is not None else ""
            if "pending_action" in where and "!=" in where:
                rows = [s for s in rows if s.pending_action]
        else:
            return _Result([])
        if name is not None:
            rows = [r for r in rows if r.name == name]
        return _Result(rows)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _watcher(**kw) -> OpsWatcher:
    w = OpsWatcher(
        name="w-" + uuid.uuid4().hex[:8],
        title="t",
        tier=WatcherTier.PLATFORM,
        kind=WatcherKind.WATCH,
        executor=WatcherExecutor.CELERY,
        managed_by=WatcherManagedBy.PLATFORM,
        command="app.worker.tasks.x.run",
    )
    for k, v in kw.items():
        setattr(w, k, v)
    return w


def _service(**kw) -> OpsService:
    s = OpsService(
        name="s-" + uuid.uuid4().hex[:8],
        title="t",
        tier=ServiceTier.DOCKER,
        kind=ServiceKind.CONTAINER,
        executor=ServiceExecutor.DOCKER_CLI,
        managed_by=ServiceManagedBy.PLATFORM,
        target="c1",
        meta={"allowed_actions": ["status", "logs", "restart"]},
    )
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _req(token: str | None = TOKEN):
    headers = {"X-Service-Token": token} if token else {}
    return SimpleNamespace(headers=headers)


def _settings(token: str | None):
    return SimpleNamespace(OPS_WATCHER_AGENT_TOKEN=token or "")


# ── watchers: management endpoints ───────────────────────────────────────


@pytest.mark.asyncio
async def test_list_watchers_filters():
    a = _watcher(tier=WatcherTier.PLATFORM, enabled=True)
    b = _watcher(tier=WatcherTier.HOST, enabled=False)
    db = FakeDB(watchers=[a, b])
    out = await w_api.list_watchers(tier="platform", enabled=True, db=db, current_user=None)
    assert out["count"] == 1
    assert out["watchers"][0]["name"] == a.name
    out_all = await w_api.list_watchers(db=FakeDB(watchers=[a, b]), current_user=None)
    assert out_all["count"] == 2


@pytest.mark.asyncio
async def test_toggle_watcher_404_and_states():
    w = _watcher(enabled=False, next_due_at=None)
    db = FakeDB(watchers=[w])
    with pytest.raises(HTTPException) as e:
        await w_api.toggle_watcher("missing", w_api.ToggleBody(enabled=True), db=db, current_user=None)
    assert e.value.status_code == 404

    out = await w_api.toggle_watcher(w.name, w_api.ToggleBody(enabled=True), db=db, current_user=None)
    assert out["enabled"] is True and out["status"] == "ok"
    assert w.next_due_at is not None  # platform watcher gets a due slot
    assert db.commits == 1

    out = await w_api.toggle_watcher(w.name, w_api.ToggleBody(enabled=False), db=db, current_user=None)
    assert out["status"] == "disabled"


@pytest.mark.asyncio
async def test_run_watcher_dispatch_paths(monkeypatch):
    sent = []
    monkeypatch.setattr("app.worker.celery_app.celery_app.send_task", lambda *a, **kw: sent.append(a))

    celery_w = _watcher(executor=WatcherExecutor.CELERY, command="app.worker.tasks.x.run")
    host_w = _watcher(executor=WatcherExecutor.HOST_AGENT, command="")
    ext_w = _watcher(executor=WatcherExecutor.EXTERNAL, command="")
    db = FakeDB(watchers=[celery_w, host_w, ext_w])

    out = await w_api.run_watcher(celery_w.name, db=db, current_user=None)
    assert out["dispatched"] == "celery" and sent == [("app.worker.tasks.x.run",)]
    assert celery_w.status == "running"

    out = await w_api.run_watcher(host_w.name, db=db, current_user=None)
    assert out["dispatched"] == "host-agent" and host_w.next_due_at is not None

    with pytest.raises(HTTPException) as e:
        await w_api.run_watcher(ext_w.name, db=db, current_user=None)
    assert e.value.status_code == 400

    with pytest.raises(HTTPException) as e:
        await w_api.run_watcher("missing", db=db, current_user=None)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_sync_watchers_auth_and_upsert(monkeypatch):
    monkeypatch.setattr(w_api, "get_settings", lambda: _settings(TOKEN))

    with pytest.raises(HTTPException) as e:
        await w_api.sync_watchers(
            w_api.SyncBody(watchers=[]),
            _req(token=None),
            db=FakeDB(),
        )
    assert e.value.status_code == 401

    existing = _watcher(name="inv-1", title="old")
    db = FakeDB(watchers=[existing])
    body = w_api.SyncBody(
        watchers=[
            w_api.SyncItem(name="inv-1", title="new", tier="host", executor="host-agent", managed_by="platform"),
            w_api.SyncItem(name="inv-2", title="fresh", tier="docker", executor="celery"),
        ]
    )
    out = await w_api.sync_watchers(body, _req(), db=db)
    assert out == {"created": 1, "upserted": 2}
    assert existing.title == "new"
    assert existing.executor == WatcherExecutor.HOST_AGENT
    assert existing.managed_by == WatcherManagedBy.PLATFORM
    created = [o for o in db.added if isinstance(o, OpsWatcher)][0]
    assert created.name == "inv-2"
    assert created.kind == WatcherKind.WATCH  # default via model __init__? kind has column default
    assert db.commits == 1


@pytest.mark.asyncio
async def test_due_watchers_claim_and_observe(monkeypatch):
    monkeypatch.setattr(w_api, "get_settings", lambda: _settings(TOKEN))
    past = datetime.now(UTC) - timedelta(minutes=1)
    due_w = _watcher(
        executor=WatcherExecutor.HOST_AGENT,
        managed_by=WatcherManagedBy.PLATFORM,
        enabled=True,
        next_due_at=past,
        interval_seconds=60,
    )
    future_w = _watcher(
        executor=WatcherExecutor.HOST_AGENT,
        managed_by=WatcherManagedBy.PLATFORM,
        enabled=True,
        next_due_at=datetime.now(UTC) + timedelta(hours=1),
    )
    obs_w = _watcher(
        executor=WatcherExecutor.EXTERNAL,
        managed_by=WatcherManagedBy.EXTERNAL,
        enabled=True,
    )
    # endpoint issues two queries: due host-agent candidates, then observe-only
    db = FakeDB(scripted=[_Result([due_w, future_w]), _Result([obs_w])])

    out = await w_api.due_watchers(_req(), db=db)
    names = [w["name"] for w in out["due"]]
    assert due_w.name in names and future_w.name not in names
    assert [w["name"] for w in out["observe"]] == [obs_w.name]
    assert due_w.next_due_at > datetime.now(UTC)  # claimed forward
    assert due_w.status == "running"

    with pytest.raises(HTTPException) as e:
        await w_api.due_watchers(_req(token="bad"), db=db)
    assert e.value.status_code == 401

    monkeypatch.setattr(w_api, "get_settings", lambda: _settings(None))
    with pytest.raises(HTTPException) as e:
        await w_api.due_watchers(_req(), db=db)
    assert e.value.status_code == 503


@pytest.mark.asyncio
async def test_watcher_result_paths(monkeypatch):
    monkeypatch.setattr(w_api, "get_settings", lambda: _settings(TOKEN))
    w = _watcher(consecutive_failures=2)
    db = FakeDB(watchers=[w])

    with pytest.raises(HTTPException) as e:
        await w_api.watcher_result("missing", w_api.ResultBody(), _req(), db=db)
    assert e.value.status_code == 404

    out = await w_api.watcher_result(
        w.name,
        w_api.ResultBody(exit_code=1, output_tail="boom" * 1000),
        _req(),
        db=db,
    )
    assert out == {"ok": True, "status": "error"}
    assert w.consecutive_failures == 3
    assert w.last_exit_code == 1
    assert len(w.last_output_tail) <= 2000
    assert w.next_due_at is not None  # platform → rescheduled

    out = await w_api.watcher_result(
        w.name,
        w_api.ResultBody(observed=True, exit_code=0, output_tail="hb"),
        _req(),
        db=db,
    )
    assert out["status"] == "ok"
    assert w.last_output_tail == "hb"
    assert w.consecutive_failures == 3  # observed heartbeat doesn't reset

    out = await w_api.watcher_result(w.name, w_api.ResultBody(exit_code=0), _req(), db=db)
    assert out["status"] == "ok" and w.consecutive_failures == 0


# ── services endpoints ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_services_filters():
    a = _service(tier=ServiceTier.DOCKER, status="running", enabled=True)
    b = _service(tier=ServiceTier.SYSTEMD, status="down", enabled=False)
    db = FakeDB(services=[a, b])
    out = await svc_api.list_services(tier="docker", status="running", db=db, current_user=None)
    assert out["count"] == 1 and out["services"][0]["name"] == a.name
    # tier filter is applied on the projected dict
    out_t = await svc_api.list_services(tier="systemd", db=db, current_user=None)
    assert out_t["count"] == 1 and out_t["services"][0]["name"] == b.name
    out_all = await svc_api.list_services(db=db, current_user=None)
    assert out_all["count"] == 2


@pytest.mark.asyncio
async def test_service_action_gates():
    s = _service(meta={"allowed_actions": ["status"]})
    db = FakeDB(services=[s])

    with pytest.raises(HTTPException) as e:
        await svc_api.service_action(s.name, svc_api.ActionBody(action="bogus"), db=db, current_user=None)
    assert e.value.status_code == 400

    with pytest.raises(HTTPException) as e:
        await svc_api.service_action("missing", svc_api.ActionBody(action="status"), db=db, current_user=None)
    assert e.value.status_code == 404

    disabled = _service(enabled=False)
    db2 = FakeDB(services=[disabled])
    with pytest.raises(HTTPException) as e:
        await svc_api.service_action(disabled.name, svc_api.ActionBody(action="status"), db=db2, current_user=None)
    assert e.value.status_code == 409

    with pytest.raises(HTTPException) as e:
        await svc_api.service_action(s.name, svc_api.ActionBody(action="restart"), db=db, current_user=None)
    assert e.value.status_code == 403

    out = await svc_api.service_action(s.name, svc_api.ActionBody(action="status", reason="r"), db=db, current_user=None)
    assert out["queued"] == "status" and s.pending_action == "status"
    assert s.pending_action_at is not None and db.commits == 1


@pytest.mark.asyncio
async def test_sync_services_upsert_and_meta_merge(monkeypatch):
    monkeypatch.setattr(svc_api, "get_settings", lambda: _settings(TOKEN))
    existing = _service(name="svc-1", meta={"allowed_actions": ["status"], "keep": 1})
    db = FakeDB(services=[existing])

    with pytest.raises(HTTPException) as e:
        await svc_api.sync_services(svc_api.SyncBody(services=[]), _req(token=None), db=db)
    assert e.value.status_code == 401

    body = svc_api.SyncBody(
        services=[
            svc_api.SyncItem(
                name="svc-1",
                title="renamed",
                tier="systemd",
                executor="systemctl",
                managed_by="platform",
                target="h1",
                meta={"new": 2},
            ),
            svc_api.SyncItem(name="svc-2", tier="docker", executor="docker-cli"),
        ]
    )
    out = await svc_api.sync_services(body, _req(), db=db)
    assert out == {"created": 1, "upserted": 2}
    assert existing.title == "renamed"
    assert existing.managed_by == ServiceManagedBy.PLATFORM
    assert existing.meta == {"allowed_actions": ["status"], "keep": 1, "new": 2}
    created = [o for o in db.added if isinstance(o, OpsService)][0]
    assert created.name == "svc-2"


@pytest.mark.asyncio
async def test_agent_token_unconfigured_503(monkeypatch):
    monkeypatch.setattr(svc_api, "get_settings", lambda: _settings(None))
    with pytest.raises(HTTPException) as e:
        await svc_api.pending_actions(_req(), db=FakeDB())
    assert e.value.status_code == 503


@pytest.mark.asyncio
async def test_pending_actions_and_report(monkeypatch):
    monkeypatch.setattr(svc_api, "get_settings", lambda: _settings(TOKEN))
    s = _service(pending_action="restart")
    s2 = _service()
    db = FakeDB(services=[s, s2])

    out = await svc_api.pending_actions(_req(), db=db)
    assert [a["name"] for a in out["actions"]] == [s.name]

    body = svc_api.ReportBody(
        reports=[
            svc_api.ReportItem(name=s.name, status="running", health="ok", detail="fine"),
            svc_api.ReportItem(name="ghost", status="down"),
        ]
    )
    out = await svc_api.service_report(body, _req(), db=db)
    assert out == {"applied": 1}
    assert s.status == "running" and s.health == "ok"
    assert s.last_seen_at is not None and s.last_reported_by == "watcher-agent"
    assert s.meta["last_detail"] == "fine"


@pytest.mark.asyncio
async def test_action_result_paths(monkeypatch):
    monkeypatch.setattr(svc_api, "get_settings", lambda: _settings(TOKEN))
    s = _service(pending_action="restart", consecutive_failures=1)
    db = FakeDB(services=[s])

    with pytest.raises(HTTPException) as e:
        await svc_api.action_result("missing", svc_api.ActionResultBody(ok=True), _req(), db=db)
    assert e.value.status_code == 404

    out = await svc_api.action_result(s.name, svc_api.ActionResultBody(ok=False, output_tail="err"), _req(), db=db)
    assert out == {"ok": True}
    assert s.pending_action == "" and s.pending_action_at is None
    assert s.consecutive_failures == 2
    assert "FAIL" in s.last_action
    assert s.meta["last_action_output"] == "err"

    await svc_api.action_result(s.name, svc_api.ActionResultBody(ok=True), _req(), db=db)
    assert s.consecutive_failures == 0 and "ok" in s.last_action
