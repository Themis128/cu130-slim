"""Unit tests for the Slack ops journal — classification, report, channels."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.slack_ops_journal import (
    _channels,
    _is_noise,
    _message_text,
    build_report,
    classify,
)


def test_classify_incident() -> None:
    assert classify("social-worker-media hit the restart cap — needs a human") == "incident"
    assert classify(":rotating_light: n8n workflow failed") == "incident"
    assert classify("Slack publishing webhook dead — channel_not_found") == "incident"


def test_classify_warning() -> None:
    assert classify(":warning: social-worker was exited — restarted") == "warning"
    assert classify("only 3/4 celery nodes answering") == "warning"
    assert classify("Publish blocked: empty caption — timed out") == "warning"


def test_classify_info() -> None:
    assert classify("Daily summary posted") == "info"
    assert classify("something routine happened") == "info"


def test_message_text_prefers_text() -> None:
    msg = {"text": "hello", "attachments": [{"fallback": "ignored"}]}
    assert _message_text(msg) == "hello"


def test_message_text_falls_back_to_attachments() -> None:
    msg = {
        "text": "",
        "attachments": [{"fallback": "PR opened", "title": "feat: x", "text": "body"}],
    }
    out = _message_text(msg)
    assert "PR opened" in out and "feat: x" in out and "body" in out


def test_is_noise() -> None:
    assert _is_noise("*Published* > hello")
    assert _is_noise("*New lead captured*")
    assert not _is_noise("restart cap hit")


def test_build_report_sections_and_stats() -> None:
    by_channel = {
        "alerts": [
            {"ts": "1760000000.0", "text": ":rotating_light: worker restart cap"},
            {"ts": "1760000100.0", "text": ":warning: celery-beat was created — restarted"},
            {"ts": "1760000200.0", "text": "*Published* routine"},
        ],
        "empty": [],
    }
    md, stats = build_report(by_channel, "2026-11-08")
    assert stats["incident"] == 1
    assert stats["warning"] == 1
    assert stats["channels"] == 1
    assert "# Slack Ops Journal — 2026-11-08" in md
    assert "## Needs attention" in md
    assert "restart cap" in md
    # published noise excluded from listing but still counted as info
    assert "routine" not in md


def test_build_report_empty() -> None:
    md, stats = build_report({}, "2026-11-08")
    assert stats["messages"] == 0
    assert "0 messages" in md


def test_channels_default() -> None:
    ch = _channels()
    assert ch["alerts"] == "C0C1D46212N"
    assert "engagement-gate" in ch


def test_channels_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_OPS_CHANNELS", "foo:C123;bar:C456")
    ch = _channels()
    assert ch == {"foo": "C123", "bar": "C456"}


def test_channels_env_override_empty_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_OPS_CHANNELS", "")
    assert _channels()["alerts"] == "C0C1D46212N"


# ── enabled flag + slack api + collect_window ───────────────────────────


def test_enabled_flag(monkeypatch):
    from app.services import slack_ops_journal as J

    monkeypatch.setattr(J.settings, "SLACK_BOT_TOKEN", "xoxb-tok", raising=False)
    assert J.slack_ops_journal_enabled() is True
    monkeypatch.setattr(J.settings, "SLACK_BOT_TOKEN", "", raising=False)
    assert J.slack_ops_journal_enabled() is False


def test_slack_api_success_and_failure(monkeypatch):
    import json as _json
    import urllib.request

    from app.services import slack_ops_journal as J

    monkeypatch.setattr(J.settings, "SLACK_BOT_TOKEN", "tok", raising=False)

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _json.dumps({"ok": True, "messages": []}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=20: _Resp())
    assert J._slack_api("conversations.history", channel="C1") == {"ok": True, "messages": []}

    def _boom(req, timeout=20):
        raise OSError("net down")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    assert J._slack_api("conversations.history") == {}


def test_collect_window_ok_and_failed_channel(monkeypatch):
    from app.services import slack_ops_journal as J

    monkeypatch.setenv("SLACK_OPS_CHANNELS", "good:C1;bad:C2")

    def fake_api(method, **params):
        if params["channel"] == "C1":
            return {"ok": True, "messages": [{"ts": "1", "text": "hi"}]}
        return {"ok": False, "error": "channel_not_found"}

    monkeypatch.setattr(J, "_slack_api", fake_api)
    out = J.collect_window()
    assert out["good"] == [{"ts": "1", "text": "hi"}]
    assert out["bad"] == []


# ── _write_note ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_note_no_base_returns_false(monkeypatch):
    from app.services import slack_ops_journal as J

    monkeypatch.setattr(J.settings, "NEXTCLOUD_BASE_URL", "", raising=False)
    monkeypatch.setattr(J.settings, "NEXTCLOUD_DAV_URL", "", raising=False)
    monkeypatch.setattr(J.settings, "NEXTCLOUD_USERNAME", "", raising=False)
    assert await J._write_note("t", "c") is False


@pytest.mark.asyncio
async def test_write_note_updates_existing_and_creates_new(monkeypatch):
    from app.services import slack_ops_journal as J

    monkeypatch.setattr(J.settings, "NEXTCLOUD_BASE_URL", "https://cloud.example", raising=False)
    monkeypatch.setattr(J.settings, "NEXTCLOUD_USERNAME", "user", raising=False)
    monkeypatch.setattr(J.settings, "NEXTCLOUD_APP_PASSWORD", "pw", raising=False)

    calls = []

    class _Resp:
        def __init__(self, status=200, payload=None):
            self.status_code = status
            self._payload = payload or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("http error")

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            calls.append(("GET", url))
            return _Resp(200, [{"id": 7, "title": "Slack Ops — 2026-01-01"}])

        async def put(self, url, json=None):
            calls.append(("PUT", url, json))
            return _Resp(200)

        async def post(self, url, json=None):
            calls.append(("POST", url, json))
            return _Resp(200)

    monkeypatch.setattr(J.httpx, "AsyncClient", _Client)
    assert await J._write_note("Slack Ops — 2026-01-01", "body") is True
    assert calls[1][0] == "PUT" and calls[1][1].endswith("/7")

    # no matching title -> POST create
    class _Client2(_Client):
        async def get(self, url):
            return _Resp(200, [{"id": 9, "title": "Other"}])

    monkeypatch.setattr(J.httpx, "AsyncClient", _Client2)
    assert await J._write_note("New Title", "body") is True
    assert calls[-1][0] == "POST"


@pytest.mark.asyncio
async def test_write_note_exception_returns_false(monkeypatch):
    from app.services import slack_ops_journal as J

    monkeypatch.setattr(J.settings, "NEXTCLOUD_BASE_URL", "https://cloud.example", raising=False)
    monkeypatch.setattr(J.settings, "NEXTCLOUD_USERNAME", "user", raising=False)
    monkeypatch.setattr(J.settings, "NEXTCLOUD_APP_PASSWORD", "pw", raising=False)

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            raise RuntimeError("down")

    monkeypatch.setattr(J.httpx, "AsyncClient", _Client)
    assert await J._write_note("t", "c") is False


# ── run_journal + _team_ids ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_journal_end_to_end(monkeypatch):
    from app.services import slack_ops_journal as J

    monkeypatch.setattr(J, "collect_window", lambda: {"alerts": [{"ts": "1", "text": "workflow failed"}]})
    monkeypatch.setattr(J, "_write_note", AsyncMock(return_value=True))
    monkeypatch.setattr(J, "_team_ids", AsyncMock(return_value=["team-1", "team-2"]))
    monkeypatch.setattr(J.company_brain, "remember", AsyncMock(side_effect=[True, False]))

    out = await J.run_journal()
    assert out["note"] is True and out["memory"] is True
    assert out["incident"] == 1 and out["channels"] == 1
    assert J.company_brain.remember.await_count == 2


@pytest.mark.asyncio
async def test_team_ids_success_and_failure(monkeypatch):
    from app.services import slack_ops_journal as J

    class _CM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, q):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ["t1", "t2"]))

    monkeypatch.setattr("app.db.session.async_session_maker", lambda: _CM())
    assert await J._team_ids() == ["t1", "t2"]

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.session.async_session_maker", _boom)
    assert await J._team_ids() == []


def test_build_report_skips_empty_and_info_lines():
    from app.services import slack_ops_journal as J

    md, stats = J.build_report(
        {
            "ch": [
                {"ts": "1", "text": ""},  # no text -> skipped (168)
                {"ts": "2", "text": "all good today"},  # info, not noise -> line (180)
                {"ts": "3", "text": "post published ok"},  # info but noise -> counted, no line
            ]
        },
        "2026-01-01",
    )
    assert stats["info"] == 2
    assert "all good today" in md
    assert "post published ok" not in md


def test_channels_env_skips_parts_without_colon(monkeypatch):
    from app.services import slack_ops_journal as J

    monkeypatch.setenv("SLACK_OPS_CHANNELS", "badpart;good:C9")
    assert J._channels() == {"good": "C9"}


@pytest.mark.asyncio
async def test_write_note_get_non200_goes_to_post(monkeypatch):
    from app.services import slack_ops_journal as J

    monkeypatch.setattr(J.settings, "NEXTCLOUD_BASE_URL", "", raising=False)
    monkeypatch.setattr(J.settings, "NEXTCLOUD_DAV_URL", "https://dav.example/remote.php/dav", raising=False)
    monkeypatch.setattr(J.settings, "NEXTCLOUD_USERNAME", "user", raising=False)
    monkeypatch.setattr(J.settings, "NEXTCLOUD_APP_PASSWORD", "pw", raising=False)

    calls = []

    class _Resp:
        status_code = 500

        def raise_for_status(self):
            pass

        def json(self):
            return []

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            calls.append(("GET", url))
            return _Resp()

        async def post(self, url, json=None):
            calls.append(("POST", url))
            return _Resp()

    monkeypatch.setattr(J.httpx, "AsyncClient", _Client)
    assert await J._write_note("t", "c") is True
    assert calls[0][1].startswith("https://dav.example/index.php")
    assert calls[1][0] == "POST"
