"""Regression: every celery-beat scheduled task must be import-registered.

The paddle_digest module was scheduled by beat but missing from the Celery
`include` list → workers raised "Received unregistered task" on every run
(PR #189). This test asserts every beat entry resolves to a registered
task after default-module import, so a missing include entry fails CI.
"""
from app.worker.celery_app import celery_app


def test_every_beat_task_is_registered():
    celery_app.loader.import_default_modules()
    missing = {
        name: entry["task"]
        for name, entry in celery_app.conf.beat_schedule.items()
        if entry["task"] not in celery_app.tasks
    }
    assert not missing, f"beat-scheduled tasks not registered (check include list): {missing}"


def test_paddle_digest_registered():
    celery_app.loader.import_default_modules()
    assert "app.worker.tasks.paddle_digest.send_paddle_slack_digest" in celery_app.tasks


def test_every_beat_task_module_is_in_include():
    # Registry checks can pass spuriously in the full suite: other tests import
    # task modules during collection, registering them regardless of `include`.
    # Check the include list itself (task names follow `<module>.<function>`).
    include = set(celery_app.conf.include or ())
    missing = {
        name: entry["task"].rsplit(".", 1)[0]
        for name, entry in celery_app.conf.beat_schedule.items()
        if entry["task"].rsplit(".", 1)[0] not in include
    }
    assert not missing, f"beat task modules missing from Celery include list: {missing}"
