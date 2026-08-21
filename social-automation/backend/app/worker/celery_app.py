from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "social_automation",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.worker.tasks.publishing",
        "app.worker.tasks.workflows",
        "app.worker.tasks.analytics",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,
    task_soft_time_limit=3000,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=100,
    beat_schedule={
        "process-publish-queue": {
            "task": "app.worker.tasks.publishing.process_publish_queue",
            "schedule": 30.0,
        },
        "sync-analytics": {
            "task": "app.worker.tasks.analytics.sync_all_analytics",
            "schedule": 300.0,
        },
        "check-scheduled-posts": {
            "task": "app.worker.tasks.publishing.check_scheduled_posts",
            "schedule": 60.0,
        },
    },
)
