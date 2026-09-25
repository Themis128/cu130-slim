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
        "app.worker.tasks.tiktok_inbox_reconcile",
        "app.worker.tasks.instagram_messenger",
        "app.worker.tasks.instagram_token_refresh",
        "app.worker.tasks.linkedin_session_refresh",
        "app.worker.tasks.whatsapp_verify",
        "app.worker.tasks.telegram_digest",
        "app.worker.tasks.dmr_health",
        "app.worker.tasks.dodo_live_check",
        "app.worker.tasks.datalake_export",
        "app.worker.tasks.linkedin_invites",
        "app.worker.tasks.linkedin_ads_report",
        "app.worker.tasks.linkedin_ads_control",
        "app.worker.tasks.notebook_reports",
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
        # One run processes all pending rows; a browser-fallback row under
        # poller contention needs ~5-7 min (lock wait + 409 retries +
        # force-preempt + compose), so the soft limit must exceed that or
        # the batch dies mid-row and strands the rest in "processing".
        "app.worker.tasks.publishing.process_publish_queue": {
            "soft_time_limit": 900,
            "time_limit": 1200,
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
        "app.worker.tasks.telegram_digest.send_telegram_group_digests": {
            "soft_time_limit": 300,
            "time_limit": 600,
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
        "app.worker.tasks.digest.send_daily_strategy_report": {"queue": "default"},
        "app.worker.tasks.notebook_reports.run_notebook_report": {"queue": "default"},
        "app.worker.tasks.telegram_digest.send_telegram_group_digests": {"queue": "default"},
        "app.worker.tasks.recurring.process_recurring_posts": {"queue": "publishing"},
        "app.worker.tasks.publishing.cleanup_publish_queue": {"queue": "default"},
        "app.worker.tasks.instagram_session_check.check_instagram_sessions": {"queue": "default"},
        "app.worker.tasks.linkedin_session_check.check_linkedin_sessions": {"queue": "default"},
        "app.worker.tasks.dodo_live_check.check_dodo_live": {"queue": "default"},
        "app.worker.tasks.datalake_export.export_datalake": {"queue": "default"},
        "app.worker.tasks.linkedin_invites.send_linkedin_invites": {"queue": "default"},
        "app.worker.tasks.linkedin_ads_report.send_linkedin_ads_report": {"queue": "default"},
        "app.worker.tasks.linkedin_ads_control.linkedin_ads_control": {"queue": "default"},
        "app.worker.tasks.tiktok_inbox_reconcile.reconcile_tiktok_inbox": {"queue": "default"},
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
        # Export SocialAuto data to the cloudless.gr R2 datalake every 6h.
        # Snapshot-style overwrite of lake/socialauto-*/ JSON tables; the
        # site's materialize-datalake-snapshots ETL builds gold sections.
        "export-datalake": {
            "task": "app.worker.tasks.datalake_export.export_datalake",
            "schedule": crontab(minute=10, hour="*/6"),  # every 6h at :10
        },
        "check-scheduled-posts": {
            "task": "app.worker.tasks.publishing.check_scheduled_posts",
            "schedule": 60.0,
        },
        # Daily SocialAuto report → Slack #socialauto is triggered by the
        # n8n workflow `socialauto-daily-slack-digest` (cron 0 9 * * * →
        # POST /api/v1/ops/daily-digest), NOT by beat — a beat entry here
        # double-posts the digest every morning (observed 2026-09-18/21/22).
        # Weekly SocialAuto rollup → Slack #socialauto (Monday 09:00 Europe/Athens)
        "weekly-slack-rollup": {
            "task": "app.worker.tasks.digest.send_weekly_slack_digest",
            "schedule": crontab(hour=settings.SLACK_DIGEST_HOUR, minute=0, day_of_week=1),
            "kwargs": {"days": 7, "post_to_slack": True, "post_to_email": False},
        },
        # End-of-day strategy brief → email. Notebook-generated (papermill in
        # the worker env) so the report layout/charts are editable from the
        # Jupyter UI; falls back to the code-path report on notebook failure.
        "daily-strategy-report": {
            "task": "app.worker.tasks.notebook_reports.run_notebook_report",
            "schedule": crontab(hour=settings.STRATEGY_REPORT_HOUR, minute=0),
            "kwargs": {
                "notebook": "daily_strategy_brief",
                "parameters": {"insight_days": 30},
                "send": True,
                "fallback_to_code": True,
            },
        },
        # Daily billing usage/revenue digest → Slack billing channel (10:00 Europe/Athens)
        "daily-paddle-digest": {
            "task": "app.worker.tasks.paddle_digest.send_paddle_slack_digest",
            "schedule": crontab(
                hour=settings.SLACK_BILLING_DIGEST_HOUR
                if settings.SLACK_BILLING_DIGEST_HOUR is not None
                else settings.SLACK_PADDLE_DIGEST_HOUR,
                minute=0,
            ),
            "kwargs": {"post_to_slack": True},
        },
        # Auto-refresh expiring OAuth tokens every hour (TikTok expires in 24h,
        # Twitter in 2h, Meta/Threads in ~60 days). Refreshes tokens expiring
        # within the next 4 hours so accounts never go offline unexpectedly.
        "refresh-expiring-tokens": {
            "task": "app.worker.tasks.token_refresh.refresh_expiring_tokens",
            "schedule": crontab(minute=15),  # at :15 past every hour
        },
        # Purge terminal publish_queue rows (failed/cancelled) older than 3
        # days — keeps the queue bounded and the failed-count metric
        # meaningful. Error details persist on posts/post_targets.
        "cleanup-publish-queue": {
            "task": "app.worker.tasks.publishing.cleanup_publish_queue",
            "schedule": crontab(hour=3, minute=0, day_of_week=0),  # Sunday 03:00
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
        # Daily LinkedIn Page invite-to-follow batch (10:30 Europe/Athens).
        # Monthly invite credits expire unused at refill — NLP-scored top-N
        # selection via browser bridge; idles automatically when credits hit 0.
        "send-linkedin-invites": {
            "task": "app.worker.tasks.linkedin_invites.send_linkedin_invites",
            "schedule": crontab(hour=10, minute=30),
            "kwargs": {"batch_size": 40},
        },
        # Daily LinkedIn Ads report → Slack ads channel + email (10:00
        # Europe/Athens). Scrapes Campaign Manager via the LinkedIn sidecar,
        # snapshots metrics, and self-terminates after LINKEDIN_ADS_END_DATE.
        "linkedin-ads-daily-report": {
            "task": "app.worker.tasks.linkedin_ads_report.send_linkedin_ads_report",
            "schedule": crontab(hour=10, minute=0),
        },
        # Reconcile TikTok MEDIA_UPLOAD inbox drafts — upgrade publish_ids to
        # real video ids when the draft is finished in-app, flag stale drafts
        # (>24h) with an actionable error_message.
        "reconcile-tiktok-inbox": {
            "task": "app.worker.tasks.tiktok_inbox_reconcile.reconcile_tiktok_inbox",
            "schedule": crontab(minute=20, hour="*/6"),  # every 6h at :20
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
        # Check WhatsApp phone numbers with NOT_VERIFIED status and attempt
        # to request a verification code via the Meta Cloud API. The 72h
        # rate limit window (10 requests) is enforced by Meta, so this task
        # is a no-op when rate-limited. When a code is sent successfully,
        # the user must complete verify + register steps manually.
        "check-whatsapp-verification": {
            "task": "app.worker.tasks.whatsapp_verify.check_whatsapp_verification",
            "schedule": crontab(minute="*/30"),  # every 30 minutes
            "options": {"queue": "default"},
        },
        # Telegram group digests → owner DM via Bot API sendMessage.
        # Runs hourly; each account only sends when local hour == digest_hour.
        "telegram-group-digests": {
            "task": "app.worker.tasks.telegram_digest.send_telegram_group_digests",
            "schedule": crontab(minute=5),  # :05 every hour
            "options": {"queue": "default"},
        },
        # DMR GPU runner health — probes the models endpoint + validates the
        # model store every 5 min, publishes `dmr:status` to Redis. On failure
        # the inference circuit breaker already fails over to Cloudflare; this
        # exists for visibility. Runner restarts are handled by dmr-watchdog.
        "dmr-health-check": {
            "task": "app.worker.tasks.dmr_health.check_dmr_health",
            "schedule": 300.0,  # every 5 minutes
            "options": {"queue": "default"},
        },
        # Watch for Dodo approving live payments (merchant review). Once the
        # MERCHANT_NOT_LIVE gate lifts this alerts Slack and self-disables via
        # the billing:dodo_live_confirmed Redis flag.
        "dodo-live-check": {
            "task": "app.worker.tasks.dodo_live_check.check_dodo_live",
            "schedule": 1800.0,  # every 30 minutes
            "options": {"queue": "default"},
        },
    },
)
