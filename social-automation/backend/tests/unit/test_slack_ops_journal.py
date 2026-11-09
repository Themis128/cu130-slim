"""Unit tests for the Slack ops journal — classification, report, channels."""

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
