"""Coverage for thin celery task wrappers — enabled/skip/error paths."""

from unittest.mock import AsyncMock, patch

import pytest

import app.worker.tasks.nextcloud_docs as NC_TASK
import app.worker.tasks.slack_ops_journal as SJ_TASK
import app.worker.tasks.tiktok_reauth as TT_TASK


def _closing_run_async(ret=None, exc=None):
    def _run(coro):
        if hasattr(coro, "close"):
            coro.close()
        if exc:
            raise exc
        return ret

    return _run


def test_slack_ops_journal_disabled():
    with patch.object(SJ_TASK.slack_ops_journal, "slack_ops_journal_enabled", return_value=False):
        assert SJ_TASK.slack_ops_journal_task() == {"skipped": "SLACK_BOT_TOKEN not set"}


def test_slack_ops_journal_success_and_error():
    with (
        patch.object(SJ_TASK.slack_ops_journal, "slack_ops_journal_enabled", return_value=True),
        patch.object(SJ_TASK, "run_async", _closing_run_async(ret={"posted": 3})),
    ):
        assert SJ_TASK.slack_ops_journal_task() == {"posted": 3}
    with (
        patch.object(SJ_TASK.slack_ops_journal, "slack_ops_journal_enabled", return_value=True),
        patch.object(SJ_TASK, "run_async", _closing_run_async(exc=RuntimeError("boom"))),
    ):
        assert SJ_TASK.slack_ops_journal_task() == {"error": "boom"}


def test_nextcloud_docs_disabled():
    with patch.object(NC_TASK.nextcloud_docs, "nextcloud_docs_enabled", return_value=False):
        assert NC_TASK.sync_nextcloud_docs_task() == {"skipped": "nextcloud export disabled"}


def test_nextcloud_docs_success_and_error():
    with (
        patch.object(NC_TASK.nextcloud_docs, "nextcloud_docs_enabled", return_value=True),
        patch.object(NC_TASK, "run_async", _closing_run_async(ret={"synced": 2})),
    ):
        assert NC_TASK.sync_nextcloud_docs_task() == {"synced": 2}
    with (
        patch.object(NC_TASK.nextcloud_docs, "nextcloud_docs_enabled", return_value=True),
        patch.object(NC_TASK, "run_async", _closing_run_async(exc=RuntimeError("boom"))),
    ):
        assert NC_TASK.sync_nextcloud_docs_task() == {"error": "boom"}


@pytest.mark.asyncio
async def test_tiktok_reauth_alert_paths(monkeypatch):
    calls = []

    async def _ok(text):
        calls.append(text)

    import app.services.slack_notifications as SN

    monkeypatch.setattr(SN, "post_alert_to_slack", _ok)
    await TT_TASK._alert("hello")
    assert calls == ["hello"]

    monkeypatch.setattr(SN, "post_alert_to_slack", AsyncMock(side_effect=RuntimeError("x")))
    await TT_TASK._alert("still fine")  # swallowed, logged
