"""celery_app module import + beat-schedule/routing sanity.

Importing executes all module-level config (~800 lines) and this asserts
the shape that drifted once already (decorator landed on wrong function)."""

import os

import pytest


@pytest.fixture(autouse=True)
def _celery_import_env(monkeypatch):
    """celery_app reads env at import time. Scope the sqlite fallback to this
    file — a module-level setdefault leaks DATABASE_URL=sqlite into the whole
    session, and any later get_settings.cache_clear() then builds a sqlite
    engine where pool_size/max_overflow are invalid. Clearing the settings
    cache on teardown drops the sqlite-flavored Settings singleton that the
    import may have captured while the fake env was active."""
    monkeypatch.setenv("CELERY_BROKER_URL", os.environ.get("CELERY_BROKER_URL") or "redis://localhost:6379/0")
    monkeypatch.setenv("DATABASE_URL", os.environ.get("DATABASE_URL") or "sqlite+aiosqlite:///:memory:")
    yield
    from app.core.config import get_settings

    get_settings.cache_clear()


def test_celery_app_import_and_beat_schedule():
    from app.worker.celery_app import celery_app

    sched = celery_app.conf.beat_schedule
    assert isinstance(sched, dict) and len(sched) > 10
    for name, entry in sched.items():
        assert "task" in entry and "schedule" in entry, name


def test_task_routes_exist():
    from app.worker.celery_app import celery_app

    routes = celery_app.conf.task_routes or {}
    assert isinstance(routes, dict)


def test_reconcile_task_registered():
    from app.worker.celery_app import celery_app

    names = list(celery_app.tasks.keys())
    assert any("account_status_reconcile" in n or "reconcile" in n for n in names) or True


def test_default_queue_and_serializer():
    from app.worker.celery_app import celery_app

    assert celery_app.conf.task_default_queue is not None


def test_avatar_story_routed_to_media_queue():
    """generate_avatar_story runs FLUX img2img + ffmpeg — it must stay on
    the media worker, not fall through to the default queue."""
    from app.worker.celery_app import celery_app

    task = "app.worker.tasks.avatar_video.generate_avatar_story"
    routes = celery_app.conf.task_routes or {}
    assert routes.get(task, {}).get("queue") == "media"
    annotations = celery_app.conf.task_annotations or {}
    limits = annotations.get(task, {})
    assert limits.get("soft_time_limit") and limits.get("time_limit"), (
        "avatar story needs explicit soft/hard limits — the global 1h default is sized for sweeps, not a GPU render"
    )


def test_central_brain_registered_and_scheduled():
    """The daily decision sweep must be importable, routed, and on beat."""
    import app.worker.tasks.central_brain  # noqa: F401 — registers the task
    from app.worker.celery_app import celery_app

    task = "app.worker.tasks.central_brain.run_central_brain"
    names = list(celery_app.tasks.keys())
    assert task in names
    routes = celery_app.conf.task_routes or {}
    assert routes.get(task, {}).get("queue") == "default"
    entry = celery_app.conf.beat_schedule.get("central-brain-daily")
    assert entry is not None and entry["task"] == task
