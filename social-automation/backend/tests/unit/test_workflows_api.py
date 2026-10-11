"""Unit tests for app/api/workflows.py — n8n workflow + template router."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.workflows as W


def _team() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4())


def _user() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4())


def _wf(**kw) -> SimpleNamespace:
    d = dict(
        id=uuid.uuid4(), team_id=uuid.uuid4(), user_id=uuid.uuid4(),
        prompt_text="p", n8n_workflow_json={"name": "w", "nodes": []},
        n8n_workflow_id=None, status="draft", template_id=None,
        variables_used={}, created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC))
    d.update(kw)
    return SimpleNamespace(**d)


def _tpl(**kw) -> SimpleNamespace:
    d = dict(
        id=uuid.uuid4(), team_id=uuid.uuid4(), user_id=uuid.uuid4(),
        name="t", description=None, prompt_template="do {{thing}}",
        n8n_workflow_json={"name": "tpl", "nodes": []},
        category="linkedin", tags=["x"], is_public=True,
        usage_count=0, created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC))
    d.update(kw)
    return SimpleNamespace(**d)


class _Res:
    def __init__(self, scalars=None, one=None):
        self._scalars = scalars
        self._one = one

    def scalars(self):
        return SimpleNamespace(all=lambda: self._scalars or [])

    def scalar_one_or_none(self):
        return self._one


class _DB:
    """get → fixed team; execute → FIFO queue of _Res."""

    def __init__(self, team=None, queue=()):
        self.team = team
        self.queue = list(queue)
        self.added: list = []
        self.deleted: list = []
        self.commits = 0

    async def get(self, model, key):
        return self.team

    async def execute(self, *a, **k):
        return self.queue.pop(0) if self.queue else _Res()

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4()
        if not getattr(obj, "created_at", None):
            obj.created_at = obj.updated_at = datetime.now(UTC)

    async def flush(self):
        pass


class _Http:
    """Fake httpx.AsyncClient as async context manager."""

    def __init__(self, post=None, get=None, delete=None):
        self._post = post or AsyncMock()
        self._get = get or AsyncMock()
        self._delete = delete or AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *a, **k):
        return await self._post(*a, **k)

    async def get(self, *a, **k):
        return await self._get(*a, **k)

    async def delete(self, *a, **k):
        return await self._delete(*a, **k)


def _patch_http(monkeypatch, fake):
    monkeypatch.setattr(W.httpx, "AsyncClient", lambda *a, **k: fake)


# ── list/get/delete workflows ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_workflows():
    # no team → []
    assert await W.list_workflows(uuid.uuid4(), None, _user(), _DB()) == []

    ws = [_wf(), _wf()]
    db = _DB(team=_team(), queue=[_Res(scalars=ws)])
    out = await W.list_workflows(uuid.uuid4(), "deployed", _user(), db)
    assert out == ws


@pytest.mark.asyncio
async def test_get_and_delete_workflow():
    tid = uuid.uuid4()
    # get 404
    db = _DB(queue=[_Res()])
    with pytest.raises(HTTPException) as e:
        await W.get_workflow(uuid.uuid4(), tid, _user(), db)
    assert e.value.status_code == 404

    # get found
    wf = _wf()
    db = _DB(queue=[_Res(one=wf)])
    assert await W.get_workflow(wf.id, tid, _user(), db) is wf

    # delete 404 + happy
    db = _DB(queue=[_Res()])
    with pytest.raises(HTTPException) as e:
        await W.delete_workflow(uuid.uuid4(), tid, _user(), db)
    assert e.value.status_code == 404
    db = _DB(queue=[_Res(one=wf)])
    await W.delete_workflow(wf.id, tid, _user(), db)
    assert db.deleted == [wf] and db.commits == 1


# ── prompt templates CRUD ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_templates_crud():
    tid = uuid.uuid4()

    # list — no team → []
    assert await W.list_templates(tid, "c", _user(), _DB()) == []

    tps = [_tpl()]
    db = _DB(team=_team(), queue=[_Res(scalars=tps)])
    assert await W.list_templates(tid, "linkedin", _user(), db) == tps

    # create — no team → 400
    data = W.PromptTemplateCreate(
        name="n", prompt_template="p", n8n_workflow_json={})
    with pytest.raises(HTTPException) as e:
        await W.create_template(data, tid, _user(), _DB())
    assert e.value.status_code == 400

    db = _DB(team=_team())
    out = await W.create_template(data, tid, _user(), db)
    assert db.commits == 1 and out.name == "n"

    # get 404 + found
    with pytest.raises(HTTPException) as e:
        await W.get_template(uuid.uuid4(), tid, _user(), _DB(queue=[_Res()]))
    assert e.value.status_code == 404
    tpl = _tpl()
    db = _DB(queue=[_Res(one=tpl)])
    assert await W.get_template(tpl.id, tid, _user(), db) is tpl

    # update — 404, allowed-fields filter
    with pytest.raises(HTTPException):
        await W.update_template(uuid.uuid4(), {}, tid, _user(),
                                _DB(queue=[_Res()]))
    db = _DB(queue=[_Res(one=tpl)])
    out = await W.update_template(
        tpl.id, {"name": "renamed", "team_id": "FORGED", "category": "x"},
        tid, _user(), db)
    assert out.name == "renamed" and out.team_id == tpl.team_id

    # delete — 404 + happy
    with pytest.raises(HTTPException):
        await W.delete_template(uuid.uuid4(), tid, _user(),
                                _DB(queue=[_Res()]))
    db = _DB(queue=[_Res(one=tpl)])
    await W.delete_template(tpl.id, tid, _user(), db)
    assert db.deleted == [tpl]


# ── content templates CRUD + _resolve_team_id ─────────────────────────


@pytest.mark.asyncio
async def test_content_templates():
    tid = uuid.uuid4()
    data = W.ContentTemplateCreate(
        name="ct", system_prompt="s", user_prompt_template="u")

    # _resolve_team_id → 404 when no team
    with pytest.raises(HTTPException) as e:
        await W.list_content_templates(tid, None, None, None, _user(), _DB())
    assert e.value.status_code == 404

    team = _team()
    tpl = SimpleNamespace(
        id=uuid.uuid4(), team_id=team.id, name="ct",
        pillar_id=None, platform=None, tone=None, system_prompt="s",
        user_prompt_template="u", variables=[], is_default=False,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC))

    db = _DB(team=team, queue=[_Res(scalars=[tpl])])
    out = await W.list_content_templates(
        tid, uuid.uuid4(), "x", "casual", _user(), db)
    assert out == [tpl]

    # create
    db = _DB(team=team)
    out = await W.create_content_template(data, tid, _user(), db)
    assert db.commits == 1

    # update 404 + happy
    with pytest.raises(HTTPException):
        await W.update_content_template(uuid.uuid4(), data, tid, _user(),
                                        _DB(team=team, queue=[_Res()]))
    db = _DB(team=team, queue=[_Res(one=tpl)])
    out = await W.update_content_template(tpl.id, data, tid, _user(), db)
    assert out.name == "ct"

    # delete — missing tolerated, found deleted
    db = _DB(team=team, queue=[_Res()])
    await W.delete_content_template(uuid.uuid4(), tid, _user(), db)
    assert db.deleted == []
    db = _DB(team=team, queue=[_Res(one=tpl)])
    await W.delete_content_template(tpl.id, tid, _user(), db)
    assert db.deleted == [tpl]


# ── generate_workflow ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_workflow(monkeypatch):
    tid = uuid.uuid4()
    team = _team()

    # template path — variables extracted from {{placeholders}}
    tpl = _tpl()
    req = W.WorkflowGenerateRequest(prompt="make it", template_id=tpl.id)
    db = _DB(team=team, queue=[_Res(one=tpl)])
    out = await W.generate_workflow(req, tid, _user(), db)
    assert out.variables_used == {"thing": "<thing>"}
    assert out.n8n_workflow_json["name"].startswith("Generated: ")
    assert out.template_id == tpl.id
    assert db.commits == 1

    # AI path — valid dict returned
    import app.services.inference as INF
    monkeypatch.setattr(INF, "call_inference", AsyncMock(
        return_value={"name": "ai-wf", "nodes": [{"n": 1}],
                      "connections": {}, "settings": {}}))
    req = W.WorkflowGenerateRequest(prompt="daily digest")
    db = _DB(team=team)
    out = await W.generate_workflow(req, tid, _user(), db)
    assert out.n8n_workflow_json["name"] == "ai-wf"

    # AI returns dict missing "nodes" → fallback
    monkeypatch.setattr(INF, "call_inference", AsyncMock(
        return_value={"name": "broken"}))
    out = await W.generate_workflow(req, tid, _user(), _DB(team=team))
    assert out.n8n_workflow_json["nodes"]  # fallback has nodes

    # AI raises → fallback
    monkeypatch.setattr(INF, "call_inference", AsyncMock(
        side_effect=RuntimeError("down")))
    out = await W.generate_workflow(req, tid, _user(), _DB(team=team))
    assert out.n8n_workflow_json["nodes"]

    # non-dict AI result → fallback
    monkeypatch.setattr(INF, "call_inference", AsyncMock(
        return_value="raw text"))
    out = await W.generate_workflow(req, tid, _user(), _DB(team=team))
    assert out.n8n_workflow_json["nodes"]


def test_fallback_workflow():
    out = W._fallback_workflow("my automation")
    assert out["name"].startswith("Generated: ")
    assert out["nodes"] and "connections" in out
    assert out["settings"]["executionOrder"] == "v1"


# ── import_cloudless_carousel ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_import_cloudless_carousel(tmp_path, monkeypatch):
    tid = uuid.uuid4()

    # no team → 400
    with pytest.raises(HTTPException) as e:
        await W.import_cloudless_carousel(tid, _user(), _DB())
    assert e.value.status_code == 400

    # workflow JSON not found → 500
    monkeypatch.setenv("CLOUDLESS_N8N_WORKFLOW_PATH", "/nonexistent.json")
    with pytest.raises(HTTPException) as e:
        await W.import_cloudless_carousel(tid, _user(), _DB(team=_team()))
    assert e.value.status_code == 500

    wf_json = tmp_path / "wf.json"
    wf_json.write_text(json.dumps({"name": "carousel", "nodes": []}))
    monkeypatch.setenv("CLOUDLESS_N8N_WORKFLOW_PATH", str(wf_json))

    # fresh → created
    team = _team()
    db = _DB(team=team, queue=[_Res(), _Res()])
    out = await W.import_cloudless_carousel(tid, _user(), db)
    assert out["action"] == "created"
    assert out["n8n_workflow_id"] == "cloudless-cf-carousel-linkedin"
    assert len(db.added) == 2  # template + generated workflow

    # existing → updated
    db = _DB(team=team, queue=[_Res(one=_tpl()), _Res(one=_wf())])
    out = await W.import_cloudless_carousel(tid, _user(), db)
    assert out["action"] == "updated" and db.added == []


# ── deploy / undeploy / execute / executions ──────────────────────────


@pytest.mark.asyncio
async def test_deploy_workflow(monkeypatch):
    tid = uuid.uuid4()

    # 404
    with pytest.raises(HTTPException) as e:
        await W.deploy_workflow(uuid.uuid4(), tid, _user(), _DB(queue=[_Res()]))
    assert e.value.status_code == 404

    wf = _wf()
    # n8n error → 500
    http = _Http(post=AsyncMock(return_value=SimpleNamespace(
        status_code=500, text="boom")))
    _patch_http(monkeypatch, http)
    db = _DB(queue=[_Res(one=wf)])
    with pytest.raises(HTTPException) as e:
        await W.deploy_workflow(wf.id, tid, _user(), db)
    assert e.value.status_code == 500

    # happy → n8n id stored, status deployed
    http = _Http(post=AsyncMock(return_value=SimpleNamespace(
        status_code=201, json=lambda: {"id": "n8n-123"})))
    _patch_http(monkeypatch, http)
    db = _DB(queue=[_Res(one=wf)])
    out = await W.deploy_workflow(wf.id, tid, _user(), db)
    assert out["n8n_workflow_id"] == "n8n-123" and wf.status == "deployed"


@pytest.mark.asyncio
async def test_undeploy_workflow(monkeypatch):
    tid = uuid.uuid4()

    # not found → 404; not deployed → 400
    with pytest.raises(HTTPException):
        await W.undeploy_workflow(uuid.uuid4(), tid, _user(),
                                  _DB(queue=[_Res()]))
    wf = _wf(n8n_workflow_id=None)
    with pytest.raises(HTTPException) as e:
        await W.undeploy_workflow(wf.id, tid, _user(),
                                  _DB(queue=[_Res(one=wf)]))
    assert e.value.status_code == 400

    # n8n delete fails → 500
    wf = _wf(n8n_workflow_id="n1", status="deployed")
    http = _Http(delete=AsyncMock(return_value=SimpleNamespace(
        status_code=500, text="x")))
    _patch_http(monkeypatch, http)
    with pytest.raises(HTTPException) as e:
        await W.undeploy_workflow(wf.id, tid, _user(),
                                  _DB(queue=[_Res(one=wf)]))
    assert e.value.status_code == 500

    # happy → deactivate + delete, status back to draft
    wf = _wf(n8n_workflow_id="n1", status="deployed")
    http = _Http(delete=AsyncMock(return_value=SimpleNamespace(
        status_code=204)))
    _patch_http(monkeypatch, http)
    db = _DB(queue=[_Res(one=wf)])
    out = await W.undeploy_workflow(wf.id, tid, _user(), db)
    assert out["status"] == "draft" and wf.n8n_workflow_id is None


@pytest.mark.asyncio
async def test_execute_workflow(monkeypatch):
    tid = uuid.uuid4()

    # not deployed → 404
    wf = _wf(n8n_workflow_id=None)
    with pytest.raises(HTTPException):
        await W.execute_workflow(wf.id, tid, _user(),
                                 _DB(queue=[_Res(one=wf)]), {})

    # n8n failure → 500
    wf = _wf(n8n_workflow_id="n1")
    http = _Http(post=AsyncMock(return_value=SimpleNamespace(
        status_code=500, text="boom")))
    _patch_http(monkeypatch, http)
    with pytest.raises(HTTPException):
        await W.execute_workflow(wf.id, tid, _user(),
                                 _DB(queue=[_Res(one=wf)]), {})

    # happy → execution id
    http = _Http(post=AsyncMock(return_value=SimpleNamespace(
        status_code=200, json=lambda: {"id": "exec-9"})))
    _patch_http(monkeypatch, http)
    out = await W.execute_workflow(wf.id, tid, _user(),
                                   _DB(queue=[_Res(one=wf)]), {"a": 1})
    assert out["execution_id"] == "exec-9"


@pytest.mark.asyncio
async def test_get_workflow_executions(monkeypatch):
    tid = uuid.uuid4()

    # not deployed → []
    wf = _wf(n8n_workflow_id=None)
    db = _DB(queue=[_Res(one=wf)])
    assert await W.get_workflow_executions(wf.id, tid, _user(), db) == []

    # n8n error → []
    wf = _wf(n8n_workflow_id="n1")
    http = _Http(get=AsyncMock(return_value=SimpleNamespace(status_code=500)))
    _patch_http(monkeypatch, http)
    db = _DB(queue=[_Res(one=wf)])
    assert await W.get_workflow_executions(wf.id, tid, _user(), db) == []

    # happy → status normalization (success/failed/running)
    exs = {"data": [
        {"id": "1", "status": "success", "stoppedAt": "t1"},
        {"id": "2", "status": "error", "startedAt": "t2"},
        {"id": "3", "status": "waiting", "startedAt": "t3"},
    ]}
    http = _Http(get=AsyncMock(return_value=SimpleNamespace(
        status_code=200, json=lambda: exs)))
    _patch_http(monkeypatch, http)
    db = _DB(queue=[_Res(one=wf)])
    out = await W.get_workflow_executions(wf.id, tid, _user(), db)
    assert [r["status"] for r in out] == ["success", "failed", "running"]
    assert out[0]["time"] == "t1" and out[1]["time"] == "t2"
