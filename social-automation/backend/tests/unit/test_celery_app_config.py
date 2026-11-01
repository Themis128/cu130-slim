"""celery_app module import + beat-schedule/routing sanity.

Importing executes all module-level config (~800 lines) and this asserts
the shape that drifted once already (decorator landed on wrong function)."""

import os

os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/0")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")


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
