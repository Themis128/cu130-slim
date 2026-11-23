"""Coverage for celery_app tail: signal handlers + LINKEDIN_SCRAPE_PAUSED."""

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest


def test_start_worker_async_loop_calls_start():
    from app.worker import celery_app as M

    with patch("app.worker._async.start_worker_loop") as m:
        M._start_worker_async_loop()
    m.assert_called_once_with()


def test_stop_worker_async_loop_calls_stop():
    from app.worker import celery_app as M

    with patch("app.worker._async.stop_worker_loop") as m:
        M._stop_worker_async_loop()
    m.assert_called_once_with()


def test_notify_task_failure_posts_to_slack():
    from app.worker import celery_app as M

    sender = MagicMock()
    sender.name = "app.worker.tasks.demo"
    posted = []

    async def _post(msg):
        posted.append(msg)

    with patch("app.services.slack_notifications.post_alert_to_slack", _post):
        # no running loop in sync test → run_until_complete path
        M._notify_task_failure(sender=sender, task_id="t1", exception=ValueError("kaput"))
    assert posted and "demo" in posted[0] and "kaput" in posted[0]


def test_notify_task_failure_inside_running_loop():
    import asyncio

    from app.worker import celery_app as M

    async def _main():
        posted = []

        async def _post(msg):
            posted.append(msg)

        sender = MagicMock()
        sender.name = "task.x"
        with patch("app.services.slack_notifications.post_alert_to_slack", _post):
            M._notify_task_failure(sender=sender, task_id="t", exception=RuntimeError("r"))
            await asyncio.sleep(0)
        return posted

    posted = asyncio.run(_main())
    assert posted and "task.x" in posted[0]


def test_notify_task_failure_never_raises():
    from app.worker import celery_app as M

    with patch(
        "app.services.slack_notifications.post_alert_to_slack",
        side_effect=RuntimeError("slack down"),
    ):
        # outer try swallows everything — including sync-context failure
        M._notify_task_failure(sender=None, task_id=None, exception=None)


@pytest.mark.parametrize(
    "paused,expected_present",
    [("true", False), ("false", True)],
)
def test_linkedin_scrape_paused_prunes_beat(paused, expected_present):
    """Module-level pause block: LINKEDIN_SCRAPE_PAUSED=1 pops LinkedIn
    sidecar lanes from beat_schedule at import time."""
    env = dict(os.environ)
    env["LINKEDIN_SCRAPE_PAUSED"] = paused
    code = "import app.worker.celery_app as m;ks=m.celery_app.conf.beat_schedule.keys();print('send-linkedin-invites' in ks, 'engagement-ingest-li-feed' in ks)"
    out = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )
    assert out.returncode == 0, out.stderr[-2000:]
    invites_present, li_ingest_present = out.stdout.strip().split()
    # Write-ish lanes prune under pause; the read-only voyager ingest stays.
    assert (invites_present == "True") is expected_present
    assert li_ingest_present == "True"
