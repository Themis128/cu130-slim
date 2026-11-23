"""ops_services registry: allowed_actions gate + status projection."""

from __future__ import annotations

import uuid

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
