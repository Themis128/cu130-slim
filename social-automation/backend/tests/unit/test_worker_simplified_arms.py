"""Pin behaviour around the branches simplified from ``elif``/``isinstance``
guards to plain ``else`` (instagram_messenger, skool_watch). The removed
arms were unreachable; these tests lock in what the remaining arms do."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.instagram_messenger as IM
import app.worker.tasks.skool_watch as S
from tests.unit import test_instagram_messenger_worker as im_suite
from tests.unit import test_skool_watch as sk_suite


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "envelope,expected",
    [
        ('{"post": "from post key"}', "from post key"),
        ('{"text": "  padded  "}', "padded"),
        ('{"response": "", "text": "fallback"}', "fallback"),
    ],
)
async def test_skool_json_envelope_keys(monkeypatch, envelope, expected):
    import app.services.brand_compliance as BC
    import app.services.inference as INF

    monkeypatch.setattr(BC, "load_brand_context", AsyncMock(return_value=(1, 2, "ctx")))
    monkeypatch.setattr(INF, "call_inference", AsyncMock(return_value={"text": envelope}))
    db = sk_suite._DB(queue=[sk_suite._Res()])
    out = await S._create_draft(db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "md", "t", "txt", "")
    assert out and db.added[0].content_text == expected


@pytest.mark.asyncio
async def test_skool_empty_json_envelope_yields_no_draft(monkeypatch):
    import app.services.brand_compliance as BC
    import app.services.inference as INF

    monkeypatch.setattr(BC, "load_brand_context", AsyncMock(return_value=(1, 2, "ctx")))
    monkeypatch.setattr(INF, "call_inference", AsyncMock(return_value={"text": '{"other": 1}'}))
    db = sk_suite._DB(queue=[sk_suite._Res()])
    assert await S._create_draft(db, uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "md", "t", "txt", "") is None
    assert db.added == []


@pytest.mark.asyncio
async def test_instagram_bridge_path_reads_and_replies_via_bridge(monkeypatch):
    """Graph client unavailable (app rate-limited) → the bridge handles the
    message fetch and the reply send; no Graph calls are made."""
    im_suite._wire(monkeypatch, rate_limited=True)
    graph = AsyncMock()
    monkeypatch.setattr(IM, "InstagramAPIClient", graph)
    bridge = im_suite._bridge(
        convos={"conversations": [{"id": "c1", "participant_id": "u1", "name": "Ada"}]},
        msgs={"messages": [{"is_sent_by_viewer": False, "text": "hi there"}]},
    )
    im_suite._wire_bridge(monkeypatch, bridge)
    seen: dict = {}
    n = await IM._process_account(None, im_suite._account({}), {}, seen, "t", "a", "u")
    assert n == 1
    graph.assert_not_called()
    bridge.get_instagram_dm_messages.assert_awaited_once_with("c1")
    bridge.send_instagram_dm_message.assert_awaited_once_with("u1", "reply")
