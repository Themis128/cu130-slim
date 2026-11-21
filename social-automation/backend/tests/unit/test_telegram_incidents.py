"""telegram_incidents._classify skip rules — self-feed and GitHub relay."""

from app.worker.tasks.telegram_incidents import _classify


def test_agent_alert_post_is_skipped():
    text = (
        "[ops-agent/critical] [ops] cloudless\n"
        ":warning: Worker task failed `app.worker.tasks.x.y`\n"
        "```the database system is starting up```"
    )
    assert _classify("critical", text) is None


def test_gh_relay_pr_comment_is_skipped():
    text = (
        "[Themis128/cu130-slim] fix(publish): routing (PR #2440)\n"
        "@devin-ai-integration[bot] commented\n"
        "## [Codecov](https://app.codecov.io) Report :white_check_mark:"
    )
    assert _classify("critical", text) is None


def test_gh_relay_run_failure_classifies():
    text = "[Themis128/www] Run failed: Security - main (5ba73ec)"
    out = _classify("critical", text)
    assert out is not None


def test_worker_failure_still_classifies():
    text = (
        "[ops] cloudless\n"
        ":warning: Worker task failed `app.worker.tasks.publish_retry.run_publish_retry_sweep`\n"
        "```the database system is starting up```"
    )
    out = _classify("critical", text)
    assert out is not None
    assert out[0] == "infra_alert"


def test_recovered_post_is_skipped():
    text = "Worker task failed `x.y` — RECOVERED, all healthy again"
    assert _classify("critical", text) is None
