"""Tests for app/services/metrics.py — Prometheus middleware + business gauges."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.metrics as M

# ── middleware ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_middleware_passthrough_non_http_and_metrics_path():
    calls = []

    async def app(scope, receive, send):
        calls.append(scope["type"])

    mw = M.PrometheusMiddleware(app)
    await mw({"type": "websocket"}, None, None)
    await mw({"type": "http", "path": "/metrics"}, None, None)
    assert calls == ["websocket", "http"]


@pytest.mark.asyncio
async def test_middleware_records_request():
    sent = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 201})
        await send({"type": "http.response.body", "body": b""})

    async def send(msg):
        sent.append(msg)

    mw = M.PrometheusMiddleware(app)
    endpoint = object()
    route = SimpleNamespace(path="/api/v1/posts/{id}")
    scope = {
        "type": "http", "path": "/api/v1/posts/1", "method": "GET",
        "endpoint": endpoint, "route": route,
    }
    M._ENDPOINT_PATHS[endpoint] = "/api/v1/posts/{id}"
    await mw(scope, None, send)
    sample = M.HTTP_REQUESTS.labels(
        method="GET", path="/api/v1/posts/{id}", status=201)
    assert sample._value.get() >= 1


@pytest.mark.asyncio
async def test_middleware_fallback_route_and_unmatched():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 404})

    async def send(msg):
        pass

    mw = M.PrometheusMiddleware(app)
    # no endpoint -> falls back to route.path
    scope = {"type": "http", "path": "/x", "method": "GET",
             "route": SimpleNamespace(path="/x")}
    await mw(scope, None, send)
    # no route either -> "unmatched"
    scope2 = {"type": "http", "path": "/y", "method": "GET"}
    await mw(scope2, None, send)


@pytest.mark.asyncio
async def test_middleware_exception_still_records():
    async def app(scope, receive, send):
        raise RuntimeError("boom")

    mw = M.PrometheusMiddleware(app)
    scope = {"type": "http", "path": "/z", "method": "GET"}
    with pytest.raises(RuntimeError):
        await mw(scope, None, AsyncMock())
    # status stays 500 default; metrics still emitted in finally


# ── business metrics refresh ─────────────────────────────────────────


class _Row(tuple):
    def __getattr__(self, name):
        mapping = {"total": 0, "twofa": 1}
        return self[mapping[name]]


class _Res:
    def __init__(self, rows=None, one=None, scalar=None):
        self._rows = rows or []
        self._one = one
        self._scalar = scalar

    def one(self):
        return self._one

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar


class _DB:
    def __init__(self):
        self.calls = 0

    async def execute(self, q):
        self.calls += 1
        n = self.calls
        if n == 1:   # users
            return _Res(one=_Row((10, 4)))
        if n == 2:   # teams by tier
            return _Res(rows=[("pro", 2), ("free", 5)])
        if n == 3:   # subscriptions
            return _Res(rows=[("active", 2)])
        if n == 4:   # social accounts
            return _Res(rows=[("linkedin", "active", 3)])
        if n == 5:   # posts
            return _Res(rows=[("published", 9)])
        if n == 6:   # queue
            return _Res(rows=[("pending", 1)])
        if n == 7:   # media
            return _Res(scalar=42)
        if n == 8:   # billing events
            return _Res(rows=[("checkout", 3)])
        if n == 9:   # billing errors
            return _Res(scalar=1)
        if n == 10:  # ai by provider
            return _Res(rows=[("dmr", 20, 0.5, 10)])
        if n == 11:  # ai errors
            return _Res(scalar=2)
        raise AssertionError(f"unexpected query {n}")


class _CM:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_refresh_business_metrics(monkeypatch):
    db = _DB()
    monkeypatch.setattr(M, "async_session_maker", lambda: _CM(db))
    await M.refresh_business_metrics()
    assert db.calls == 11
    assert M.USERS.labels(state="total")._value.get() == 10
    assert M.USERS.labels(state="two_factor_enabled")._value.get() == 4
    assert M.TEAMS.labels(plan_tier="pro")._value.get() == 2
    assert M.SOCIAL_ACCOUNTS.labels(platform="linkedin", status="active")._value.get() == 3
    assert M.MEDIA._value.get() == 42
    assert M.AI_CALLS.labels(provider="dmr")._value.get() == 20
    assert M.AI_ERRORS._value.get() == 2


@pytest.mark.asyncio
async def test_refresh_business_metrics_db_failure_soft(monkeypatch):
    def broken():
        raise RuntimeError("db down")
    monkeypatch.setattr(M, "async_session_maker", broken)
    await M.refresh_business_metrics()  # no raise


# ── route registration + response ────────────────────────────────────


def test_register_route_metrics():
    from fastapi.routing import APIRoute

    async def handler():
        pass

    route = APIRoute("/api/v1/x", endpoint=handler, methods=["GET", "POST"])
    app = SimpleNamespace(routes=[route])
    M._ENDPOINT_PATHS.clear()
    M.register_route_metrics(app)
    assert M._ENDPOINT_PATHS[handler] == "/api/v1/x"
    assert M.HTTP_ROUTE.labels(method="GET", path="/api/v1/x")._value.get() == 1


def test_register_route_metrics_nested():
    from fastapi.routing import APIRoute

    async def h():
        pass

    inner = APIRoute("/leaf", endpoint=h, methods=["GET"])
    mounted = SimpleNamespace(
        include_context=SimpleNamespace(prefix="/pre"),
        routes=[inner],
    )
    app = SimpleNamespace(routes=[mounted])
    M.register_route_metrics(app)
    assert M._ENDPOINT_PATHS[h] == "/pre/leaf"


def test_metrics_response():
    out = M.metrics_response()
    assert b"socialauto_app" in out or b"socialauto" in out
