"""Coverage for the LinkedIn Ad Library watch task."""

import asyncio
from unittest.mock import AsyncMock, patch

import app.worker.tasks.linkedin_intel as LI


def _drain(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_watch_skip_short_circuits():
    with (
        patch.object(LI, "run_async", _drain),
        patch.object(LI, "run_watch", AsyncMock(return_value={"skip": "not configured"})),
        patch.object(LI, "send_telegram_alert", AsyncMock()) as alert,
    ):
        out = LI.run_linkedin_ad_library_watch()
    assert out == {"skip": "not configured"}
    alert.assert_not_awaited()


def test_watch_digest_posts_intel():
    summary = {"watches": 3, "new": 2, "updated": 1, "ended": 0, "errors": ["e1"]}
    sent = []

    async def _alert(sev, text):
        sent.append((sev, text))

    with (
        patch.object(LI, "run_async", _drain),
        patch.object(LI, "run_watch", AsyncMock(return_value=summary)),
        patch.object(LI, "send_telegram_alert", _alert),
    ):
        out = LI.run_linkedin_ad_library_watch()
    assert out is summary
    assert sent and sent[0][0] == "intel"
    assert "watches=3 new=2 updated=1 ended=0" in sent[0][1]
    assert "err: e1" in sent[0][1]


def test_digest_format_errors_capped():
    summary = {"errors": [f"err{i}" for i in range(9)]}
    text = LI._format_digest(summary)
    assert text.count("err:") == 5
