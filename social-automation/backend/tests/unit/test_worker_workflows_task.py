"""Unit tests for app/worker/tasks/workflows.py — n8n execute/deploy tasks.

DB session and httpx are faked at module boundaries; workflow status
transitions and n8n API calls run the real code.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.worker.tasks.workflows as W


class _Scalar:
    def __init__(self, v):
        self._v = v

    def scalar_one_or_none(self):
        return self._v


class _DB:
    def __init__(self, wf):
        self.wf = wf
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        return _Scalar(self.wf)

    async def commit(self):
        self.commits += 1


class _Resp:
    def __init__(self, body=None, fail=False):
        self._body = body or {}
        self._fail = fail

    def raise_for_status(self):
        if self._fail:
            raise RuntimeError("n8n 500")

    def json(self):
        return self._body


class _Client:
    def __init__(self, resp):
        self.resp = resp
        self.posts = []
        self.puts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self.posts.append((url, headers, json))
        return self.resp

    async def put(self, url, headers=None, json=None):
        self.puts.append((url, headers, json))
        return self.resp


def _wf(**kw):
    base = {"id": "w1", "n8n_workflow_id": "n8n-1", "n8n_workflow_json": {"nodes": []}, "status": "draft", "updated_at": None}
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_execute_workflow_not_found(monkeypatch):
    monkeypatch.setattr(W, "task_session", lambda: _DB(None))
    out = await W._execute_workflow_async("missing", {})
    assert out == {"success": False, "error": "Workflow not found"}


@pytest.mark.asyncio
async def test_execute_workflow_success(monkeypatch):
    wf = _wf()
    db = _DB(wf)
    client = _Client(_Resp({"data": "ran"}))
    monkeypatch.setattr(W, "task_session", lambda: db)
    monkeypatch.setattr(W.httpx, "AsyncClient", lambda **kw: client)
    out = await W._execute_workflow_async("w1", {"in": 1})
    assert out == {"success": True, "result": {"data": "ran"}}
    assert wf.status == "active" and db.commits == 1
    url, headers, body = client.posts[0]
    assert "/api/v1/workflows/n8n-1/execute" in url
    assert headers == {"X-N8N-API-KEY": W.settings.N8N_API_KEY}
    assert body == {"in": 1}


@pytest.mark.asyncio
async def test_execute_workflow_http_error(monkeypatch):
    wf = _wf()
    db = _DB(wf)
    client = _Client(_Resp(fail=True))
    monkeypatch.setattr(W, "task_session", lambda: db)
    monkeypatch.setattr(W.httpx, "AsyncClient", lambda **kw: client)
    out = await W._execute_workflow_async("w1", {})
    assert out["success"] is False and "n8n 500" in out["error"]
    assert db.commits == 1  # still commits (status untouched)


@pytest.mark.asyncio
async def test_deploy_workflow_not_found(monkeypatch):
    monkeypatch.setattr(W, "task_session", lambda: _DB(None))
    out = await W._deploy_workflow_async("missing")
    assert out == {"success": False, "error": "Workflow not found"}


@pytest.mark.asyncio
async def test_deploy_workflow_put_existing(monkeypatch):
    wf = _wf(n8n_workflow_id="n8n-9")
    db = _DB(wf)
    client = _Client(_Resp({"id": "n8n-9"}))
    monkeypatch.setattr(W, "task_session", lambda: db)
    monkeypatch.setattr(W.httpx, "AsyncClient", lambda **kw: client)
    out = await W._deploy_workflow_async("w1")
    assert out == {"success": True, "n8n_workflow_id": "n8n-9"}
    assert client.puts and not client.posts
    assert wf.status == "active"


@pytest.mark.asyncio
async def test_deploy_workflow_post_new(monkeypatch):
    wf = _wf(n8n_workflow_id=None)
    db = _DB(wf)
    client = _Client(_Resp({"id": "new-id"}))
    monkeypatch.setattr(W, "task_session", lambda: db)
    monkeypatch.setattr(W.httpx, "AsyncClient", lambda **kw: client)
    out = await W._deploy_workflow_async("w1")
    assert out == {"success": True, "n8n_workflow_id": "new-id"}
    assert wf.n8n_workflow_id == "new-id"
    assert client.posts and not client.puts


@pytest.mark.asyncio
async def test_deploy_workflow_error_marks_draft(monkeypatch):
    wf = _wf()
    db = _DB(wf)
    client = _Client(_Resp(fail=True))
    monkeypatch.setattr(W, "task_session", lambda: db)
    monkeypatch.setattr(W.httpx, "AsyncClient", lambda **kw: client)
    out = await W._deploy_workflow_async("w1")
    assert out["success"] is False
    assert wf.status == "draft" and db.commits == 1


def test_celery_wrappers(monkeypatch):
    # run_async drives the coroutine; sync_after_worker_task is stubbed.
    monkeypatch.setattr(W, "run_async", lambda c: (c.close(), {"r": 1})[1] if hasattr(c, "close") else {"r": 1})
    monkeypatch.setattr(W, "sync_after_worker_task", lambda t: None)
    # .run attribute of shared_task — call the wrapped function directly
    out = W.deploy_workflow("w1")
    assert out == {"r": 1}
    out = W.execute_workflow("w1", {})
    assert out == {"r": 1}
