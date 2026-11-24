"""Tests for newsletter_publisher._ensure_sidecar_ready — the pre-publish
warm-up that keeps a cold sidecar from dropping the long publish call."""

from __future__ import annotations

import httpx
import pytest

from app.services import newsletter_publisher as pub
from app.services.linkedin_sidecar import LinkedInSidecarError


class _FakeClient:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    async def check_session(self):
        self.calls += 1
        outcome = self._outcomes.pop(0) if len(self._outcomes) > 1 else self._outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.asyncio
async def test_ready_first_try(monkeypatch):
    monkeypatch.setattr(pub.asyncio, "sleep", _noop)
    client = _FakeClient([{"logged_in": True}])
    await pub._ensure_sidecar_ready(client, attempts=3, delay=0)
    assert client.calls == 1


@pytest.mark.asyncio
async def test_retries_until_logged_in(monkeypatch):
    monkeypatch.setattr(pub.asyncio, "sleep", _noop)
    client = _FakeClient(
        [
            {"logged_in": False},
            httpx.ConnectError("refused"),
            LinkedInSidecarError(503, "booting"),
            {"logged_in": True},
        ]
    )
    await pub._ensure_sidecar_ready(client, attempts=6, delay=0)
    assert client.calls == 4


@pytest.mark.asyncio
async def test_exhausts_attempts_then_returns(monkeypatch):
    monkeypatch.setattr(pub.asyncio, "sleep", _noop)
    client = _FakeClient([{"logged_in": False}])
    await pub._ensure_sidecar_ready(client, attempts=3, delay=0)
    assert client.calls == 3  # gives up silently — publish proceeds anyway


async def _noop(_):
    return None
