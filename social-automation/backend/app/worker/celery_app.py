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
        "app.worker.tasks.media_enhance",
        "app.worker.tasks.token_refresh",
        "app.worker.tasks.recurring",
        "app.worker.tasks.instagram_session_check",
        "app.worker.tasks.linkedin_session_check",
        "app.worker.tasks.personal_messenger",
        "app.worker.tasks.linkedin_messenger",
        "app.worker.tasks.threads_messenger",
        "app.worker.tasks.twitter_messenger",
        "app.worker.tasks.tiktok_messenger",
        "app.worker.tasks.instagram_messenger",
        "app.worker.tasks.instagram_token_refresh",
        "app.worker.tasks.linkedin_session_refresh",
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
    # ── Reliability ──────────────────────────────────────────────────────
    # Ack after completion (not receipt) so a worker crash mid-task
    # triggers redelivery instead of silent loss.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Auto-clean Redis result backend — prevents unbounded growth.
    result_expires=3600,
    # Redis visibility timeout must exceed the longest task time limit.
    broker_transport_options={"visibility_timeout": 3600},
    # ── Global limits (overridden per-task via task_annotations) ──────────
    task_time_limit=3600,
    task_soft_time_limit=3000,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=100,
    # ── Per-task time limits ─────────────────────────────────────────────
    # Publishing tasks are I/O-bound API calls — fail fast instead of
    # hanging for the global 1-hour limit.  Media tasks are CPU-heavy
    # (Pillow, AI) and get a longer window.  Analytics/digest/workflow
    # tasks get intermediate limits based on their expected duration.
    task_annotations={
        # ── publishing queue: fail fast ──────────────────────────────────
        "app.worker.tasks.publishing.process_publish_queue": {
            "soft_time_limit": 600,
            "time_limit": 900,
        },
        "app.worker.tasks.publishing.check_scheduled_posts": {
            "soft_time_limit": 120,
            "time_limit": 180,
        },
        "app.worker.tasks.publishing.publish_post_now": {
            "soft_time_limit": 300,
            "time_limit": 600,
        },
        "app.worker.tasks.token_refresh.refresh_expiring_tokens": {
            "soft_time_limit": 300,
            "time_limit": 600,
        },
        "app.worker.tasks.recurring.process_recurring_posts": {
            "soft_time_limit": 120,
            "time_limit": 180,
        },
        # ── media queue: CPU-heavy, allow up to 40 min ───────────────────
        "app.worker.tasks.media.auto_tag_asset_task": {
            "soft_time_limit": 120,
            "time_limit": 300,
        },
        "app.worker.tasks.media_enhance.batch_enhance_task": {
            "soft_time_limit": 1800,
            "time_limit": 2400,
        },
        # ── default queue: intermediate ─────────────────────────────────
        "app.worker.tasks.analytics.sync_all_analytics": {
            "soft_time_limit": 900,
            "time_limit": 1200,
        },
        "app.worker.tasks.analytics.sync_team_analytics_task": {
            "soft_time_limit": 600,
            "time_limit": 900,
        },
        "app.worker.tasks.workflows.execute_workflow": {
            "soft_time_limit": 360,
            "time_limit": 420,
        },
        "app.worker.tasks.workflows.deploy_workflow": {
            "soft_time_limit": 120,
            "time_limit": 180,
        },
        "app.worker.tasks.digest.send_daily_slack_digest": {
            "soft_time_limit": 300,
            "time_limit": 600,
        },
        "app.worker.tasks.digest.send_weekly_slack_digest": {
            "soft_time_limit": 600,
            "time_limit": 900,
        },
    },
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
        "app.worker.tasks.digest.send_weekly_slack_digest": {"queue": "default"},
        "app.worker.tasks.recurring.process_recurring_posts": {"queue": "publishing"},
        "app.worker.tasks.instagram_session_check.check_instagram_sessions": {"queue": "default"},
        "app.worker.tasks.linkedin_session_check.check_linkedin_sessions": {"queue": "default"},
        "app.worker.tasks.personal_messenger.poll_personal_messenger": {"queue": "messenger"},
        "app.worker.tasks.linkedin_messenger.poll_linkedin_messenger": {"queue": "messenger"},
        "app.worker.tasks.threads_messenger.poll_threads_messenger": {"queue": "messenger"},
        "app.worker.tasks.twitter_messenger.poll_twitter_messenger": {"queue": "messenger"},
        "app.worker.tasks.tiktok_messenger.poll_tiktok_messenger": {"queue": "messenger"},
        "app.worker.tasks.instagram_messenger.poll_instagram_messenger": {"queue": "messenger"},
    },
    beat_schedule={
        "process-publish-queue": {
            "task": "app.worker.tasks.publishing.process_publish_queue",
            "schedule": 30.0,
        },
        "sync-analytics": {
            "task": "app.worker.tasks.analytics.sync_all_analytics",
            "schedule": 1800.0,
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
        # Weekly SocialAuto rollup → Slack #socialauto (Monday 09:00 Europe/Athens)
        "weekly-slack-rollup": {
            "task": "app.worker.tasks.digest.send_weekly_slack_digest",
            "schedule": crontab(hour=settings.SLACK_DIGEST_HOUR, minute=0, day_of_week=1),
            "kwargs": {"days": 7, "post_to_slack": True, "post_to_email": False},
        },
        # Auto-refresh expiring OAuth tokens every hour (TikTok expires in 24h,
        # Twitter in 2h, Meta/Threads in ~60 days). Refreshes tokens expiring
        # within the next 4 hours so accounts never go offline unexpectedly.
        "refresh-expiring-tokens": {
            "task": "app.worker.tasks.token_refresh.refresh_expiring_tokens",
            "schedule": crontab(minute=15),  # at :15 past every hour
        },
        # Check for due recurring posts every 5 minutes — clones published
        # posts flagged as recurring into new scheduled posts.
        "process-recurring-posts": {
            "task": "app.worker.tasks.recurring.process_recurring_posts",
            "schedule": 300.0,
        },
        # Check Instagram private-API sidecar sessions every 6 hours.
        # Marks expired sessions and alerts the team owner via email.
        "check-instagram-sessions": {
            "task": "app.worker.tasks.instagram_session_check.check_instagram_sessions",
            "schedule": crontab(minute=30, hour="*/6"),  # every 6h at :30
        },
        # Check and refresh LinkedIn browser sidecar session every 12 hours.
        # Exports fresh cookies and persists them to the secret store.
        "check-linkedin-sessions": {
            "task": "app.worker.tasks.linkedin_session_check.check_linkedin_sessions",
            "schedule": crontab(minute=45, hour="*/12"),  # every 12h at :45
        },
        # Poll personal Messenger conversations for new messages and send
        # AI auto-replies via browser bridge. Personal Messenger has no
        # webhook support, so polling is the only option.
        "poll-personal-messenger": {
            "task": "app.worker.tasks.personal_messenger.poll_personal_messenger",
            "schedule": 120.0,  # every 2 minutes
            "options": {"queue": "messenger"},
        },
        # Poll LinkedIn DM conversations for new messages and send AI
        # auto-replies via the browser sidecar. LinkedIn has no DM API,
        # so polling is the only option.
        "poll-linkedin-messenger": {
            "task": "app.worker.tasks.linkedin_messenger.poll_linkedin_messenger",
            "schedule": 21600.0,  # every 6 hours — LinkedIn rate-limits browser DM polling hard
            "options": {"queue": "messenger"},
        },
        # Poll Threads DM conversations for new messages and send AI
        # auto-replies via the browser bridge. Threads has no DM API,
        # so polling is the only option.
        "poll-threads-messenger": {
            "task": "app.worker.tasks.threads_messenger.poll_threads_messenger",
            "schedule": 180.0,  # every 3 minutes
            "options": {"queue": "messenger"},
        },
        # Poll Twitter DM events for new messages and send AI auto-replies
        # via the official X API v2. Twitter has strict rate limits, so we
        # poll less frequently (every 5 minutes). Requires dm.read + dm.write
        # scopes and a paid tier (Basic $200/mo or Pro $5000/mo).
        "poll-twitter-messenger": {
            "task": "app.worker.tasks.twitter_messenger.poll_twitter_messenger",
            "schedule": 300.0,  # every 5 minutes
            "options": {"queue": "messenger"},
        },
        # Poll TikTok DM conversations for new messages and send AI auto-replies
        # via the Business Messaging API (Open Beta — not yet in EU).
        # Runs every 5 minutes; gracefully skips accounts without API access.
        "poll-tiktok-messenger": {
            "task": "app.worker.tasks.tiktok_messenger.poll_tiktok_messenger",
            "schedule": 300.0,  # every 5 minutes
            "options": {"queue": "messenger"},
        },
        # Poll Instagram DM conversations for new messages and send AI auto-replies
        # via the Instagram Messaging API (same as Messenger Platform API).
        # Runs every 3 minutes; gracefully skips accounts without OAuth token.
        "poll-instagram-messenger": {
            "task": "app.worker.tasks.instagram_messenger.poll_instagram_messenger",
            "schedule": 180.0,  # every 3 minutes
            "options": {"queue": "messenger"},
        },
        # Refresh Instagram long-lived access tokens before they expire (60 days).
        # Runs weekly to refresh tokens that will expire within 5 days.
        "refresh-instagram-tokens": {
            "task": "app.worker.tasks.instagram_token_refresh.refresh_instagram_tokens",
            "schedule": 604800.0,  # every 7 days
            "options": {"queue": "default"},
        },
        # Validate and refresh LinkedIn browser sessions weekly.
        # Clears stale rate limits and reports session health.
        "refresh-linkedin-sessions": {
            "task": "app.worker.tasks.linkedin_session_refresh.refresh_linkedin_sessions",
            "schedule": 604800.0,  # every 7 days
            "options": {"queue": "default"},
        },
    },
)
