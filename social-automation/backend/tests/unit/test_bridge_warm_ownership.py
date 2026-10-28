import asyncio
import importlib.util
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import HTTPException


@pytest.fixture
def bridge(monkeypatch, tmp_path):
    monkeypatch.setenv("BRIDGE_COOKIE_DIR", str(tmp_path))
    source = next(
        (parent / "browser-novnc/browser-bridge.py" for parent in Path(__file__).resolve().parents if (parent / "browser-novnc/browser-bridge.py").exists()),
        Path("/app/browser-bridge.py"),
    )
    spec = importlib.util.spec_from_file_location("bridge_warm_under_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize("interactive,owner,caller", [(True, "facebook", "facebook"), (False, "twitter", "facebook")])
async def test_warm_rejects_owned_session(bridge, interactive, owner, caller):
    page = AsyncMock()
    bridge._state.update(page=page, context=object(), interactive=interactive, busy_owner=owner, busy_until=time.time() + 300)
    token = bridge._req_platform.set(caller)
    try:
        with pytest.raises(HTTPException) as exc:
            await bridge.warm_session("facebook")
        assert exc.value.status_code == 409
        page.goto.assert_not_awaited()
        assert bridge._keepalive_task is None
    finally:
        bridge._req_platform.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("protected", ["interactive", "busy", "replaced", "idle"])
async def test_keepalive_respects_session_ownership(bridge, monkeypatch, protected):
    page = AsyncMock()
    bridge._state.update(
        page=page, context=object(), interactive=protected == "interactive", busy_owner="facebook", busy_until=time.time() + 300 if protected == "busy" else 0
    )
    ticks = 0

    async def tick(seconds):
        nonlocal ticks
        ticks += 1
        if ticks > 1:
            raise asyncio.CancelledError
        if protected == "replaced":
            bridge._state["page"] = AsyncMock()

    monkeypatch.setattr(bridge.asyncio, "sleep", tick)
    await bridge._keepalive_loop("facebook")
    if protected == "idle":
        page.goto.assert_awaited_once()
    else:
        page.goto.assert_not_awaited()
    if protected == "replaced":
        bridge._state["page"].goto.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "interactive,owner,caller,status",
    [
        (True, "facebook", "facebook", 409),
        (False, "twitter", "facebook", 409),
        (False, "facebook", "twitter", 409),
        (False, "facebook", "facebook", 200),
    ],
)
async def test_warm_http_route(bridge, interactive, owner, caller, status):
    page = Mock()
    page.is_closed.return_value = False
    page.goto = AsyncMock()
    bridge._state.update(page=page, context=object(), interactive=interactive, busy_owner=owner, busy_until=time.time() + 300)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge.app), base_url="http://bridge") as client:
        response = await client.post("/session/warm?platform=facebook", headers={"X-Platform": caller})
    assert response.status_code == status
    if status == 200:
        page.goto.assert_awaited_once()
        bridge._keepalive_task.cancel()
        try:
            await bridge._keepalive_task
        except asyncio.CancelledError:
            pass
    else:
        page.goto.assert_not_awaited()
