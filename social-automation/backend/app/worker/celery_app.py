from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "social_automation",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.worker.tasks.publishing",
        "app.worker.tasks.workflows",
        "app.worker.tasks.analytics",
        "app.worker.tasks.digest",
        "app.worker.tasks.media",
        "app.worker.tasks.token_refresh",
    ],
)

# Ensure @shared_task and Task.delay() from the API process use Redis, not default AMQP.
celery_app.set_default()
celery_app.set_current()

celery_app.conf.update(
    broker_connection_retry_on_startup=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone=settings.APP_TIMEZONE,
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,
    task_soft_time_limit=3000,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=100,
    # Queue routing: time-sensitive publishing tasks are isolated from
    # CPU-heavy media/AI tasks so a long-running batch enhance never blocks
    # a "publish now" or scheduled-post dispatch.
    task_routes={
        # ── publishing queue: time-sensitive, user-facing ──────────────────
        "app.worker.tasks.publishing.process_publish_queue": {"queue": "publishing"},
        "app.worker.tasks.publishing.check_scheduled_posts": {"queue": "publishing"},
        "app.worker.tasks.publishing.publish_post_now": {"queue": "publishing"},
        "app.worker.tasks.token_refresh.refresh_expiring_tokens": {"queue": "publishing"},
        # ── media queue: CPU-intensive, long-running ───────────────────────
        "app.worker.tasks.media.auto_tag_asset_task": {"queue": "media"},
        "app.worker.tasks.media_enhance.batch_enhance_task": {"queue": "media"},
        # ── default queue: analytics, workflows, digests, everything else ──
        "app.worker.tasks.analytics.sync_all_analytics": {"queue": "default"},
        "app.worker.tasks.analytics.sync_team_analytics_task": {"queue": "default"},
        "app.worker.tasks.workflows.execute_workflow": {"queue": "default"},
        "app.worker.tasks.workflows.deploy_workflow": {"queue": "default"},
        "app.worker.tasks.digest.send_daily_slack_digest": {"queue": "default"},
    },
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
        # Daily SocialAuto report → Slack #socialauto (09:00 Europe/Athens)
        "daily-slack-digest": {
            "task": "app.worker.tasks.digest.send_daily_slack_digest",
            "schedule": crontab(hour=settings.SLACK_DIGEST_HOUR, minute=0),
            "kwargs": {"days": 1, "post_to_slack": True},
        },
        # Auto-refresh expiring OAuth tokens every hour (TikTok expires in 24h,
        # Twitter in 2h, Meta/Threads in ~60 days). Refreshes tokens expiring
        # within the next 4 hours so accounts never go offline unexpectedly.
        "refresh-expiring-tokens": {
            "task": "app.worker.tasks.token_refresh.refresh_expiring_tokens",
            "schedule": crontab(minute=15),  # at :15 past every hour
        },
    },
)
