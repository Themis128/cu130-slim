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
        "app.worker.tasks.avatar_video",
        "app.worker.tasks.media_enhance",
        "app.worker.tasks.nextcloud",
        "app.worker.tasks.nextcloud_sync",
        "app.worker.tasks.company_brain",
        "app.worker.tasks.crm_hygiene",
        "app.worker.tasks.crm_lifecycle",
        "app.worker.tasks.crm_engagement",
        "app.worker.tasks.email_drip",
        "app.worker.tasks.lead_research",
        "app.worker.tasks.token_refresh",
        "app.worker.tasks.recurring",
        "app.worker.tasks.instagram_session_check",
        "app.worker.tasks.account_status_reconcile",
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
        "app.worker.tasks.synigoros_status",
        "app.worker.tasks.mastersheet_watch",
        "app.worker.tasks.datalake_executor",
        "app.worker.tasks.telegram_agent",
        "app.worker.tasks.dmr_health",
        "app.worker.tasks.auto_gates",
        "app.worker.tasks.consent_healers",
        "app.worker.tasks.log_intel",
        "app.worker.tasks.dodo_live_check",
        "app.worker.tasks.datalake_export",
        "app.worker.tasks.datalake_insights",
        "app.worker.tasks.linkedin_invites",
        "app.worker.tasks.linkedin_ads_report",
        "app.worker.tasks.linkedin_ads_control",
        "app.worker.tasks.notebook_reports",
        "app.worker.tasks.paddle_digest",
        "app.worker.tasks.session_healer",
        "app.worker.tasks.lead_emails",
        "app.worker.tasks.email_campaigns",
        "app.worker.tasks.skool_watch",
        "app.worker.tasks.monetization",
        "app.worker.tasks.promo_scan",
        "app.worker.tasks.failure_learning",
        "app.worker.tasks.edit_diff",
        "app.worker.tasks.kakkava_sync",
        "app.worker.tasks.agency",
        "app.worker.tasks.notebooks",
        "app.worker.tasks.human_gate",
        "app.worker.tasks.nurture",
        "app.worker.tasks.funnel_sync",
        "app.worker.tasks.funnel_score",
        "app.worker.tasks.gate_sweep",
        "app.worker.tasks.publish_retry",
        "app.worker.tasks.newsletter_subs",
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
            # LinkedIn org image publishes legitimately take ~15-25min
            # (quality gate → LanguageTool wake → image init/PUT/poll →
            # post create → D1 sync). 900s was killing them mid-publish
            # and stranding the row in 'processing' — observed 2026-10-28.
            "soft_time_limit": 2700,
            "time_limit": 3000,
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
        # WebDAV PUT to the omv Nextcloud — I/O bound, same budget as auto-tag.
        "app.worker.tasks.nextcloud.export_media_to_nextcloud": {
            "soft_time_limit": 120,
            "time_limit": 300,
        },
        # ComfyUI T2V: LTXV ~15min worst case; Wan2.2-5B 720p ~35min+
        # (slow temporal VAE decode on the 8GB card).
        "app.worker.tasks.media.generate_video_asset_task": {
            "soft_time_limit": 2700,
            "time_limit": 3000,
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
        # Agency pipeline — outbound site fetches, bounded per run.
        "app.worker.tasks.agency.run_agency_triage_sweep": {
            "soft_time_limit": 300,
            "time_limit": 420,
        },
        "app.worker.tasks.agency.run_agency_audit_sweep": {
            "soft_time_limit": 300,
            "time_limit": 420,
        },
        "app.worker.tasks.agency.run_agency_proposal_sweep": {
            "soft_time_limit": 120,
            "time_limit": 180,
        },
        "app.worker.tasks.agency.run_agency_pm_sweep": {
            "soft_time_limit": 120,
            "time_limit": 180,
        },
        "app.worker.tasks.agency.run_agency_engineer_sweep": {
            "soft_time_limit": 120,
            "time_limit": 180,
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
        "app.worker.tasks.publishing.reconcile_instagram_publish": {"queue": "publishing"},
        "app.worker.tasks.token_refresh.refresh_expiring_tokens": {"queue": "publishing"},
        # ── media queue: CPU-intensive, long-running ───────────────────────
        "app.worker.tasks.media.auto_tag_asset_task": {"queue": "media"},
        "app.worker.tasks.media.generate_video_asset_task": {"queue": "media"},
        "app.worker.tasks.media_enhance.batch_enhance_task": {"queue": "media"},
        "app.worker.tasks.nextcloud.export_media_to_nextcloud": {"queue": "media"},
        # DMR keep-warm/health fire every 90s and can hold a worker slot ~10min
        # via gpu:dmr_lock — on the 2-slot default worker they starve all
        # default/celery queue tasks (observed 2026-10-27: llen=20 stuck).
        "app.worker.tasks.dmr_health.keep_warm_dmr_models": {"queue": "media"},
        "app.worker.tasks.dmr_health.check_dmr_health": {"queue": "media"},
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
        "app.worker.tasks.synigoros_status.poll_synigoros": {"queue": "default"},
        "app.worker.tasks.mastersheet_watch.watch_mastersheet": {"queue": "default"},
        "app.worker.tasks.datalake_executor.execute_actions": {"queue": "default"},
        "app.worker.tasks.telegram_agent.run_channel_agent_daily": {"queue": "default"},
        "app.worker.tasks.recurring.process_recurring_posts": {"queue": "publishing"},
        "app.worker.tasks.publishing.cleanup_publish_queue": {"queue": "default"},
        "app.worker.tasks.publishing.requeue_capacity_stuck_x_targets": {"queue": "default"},
        "app.worker.tasks.instagram_session_check.check_instagram_sessions": {"queue": "default"},
        "app.worker.tasks.account_status_reconcile.reconcile_account_status": {"queue": "default"},
        "app.worker.tasks.linkedin_session_check.check_linkedin_sessions": {"queue": "default"},
        "app.worker.tasks.session_healer.heal_sessions": {"queue": "default"},
        "app.worker.tasks.dodo_live_check.check_dodo_live": {"queue": "default"},
        "app.worker.tasks.skool_watch.watch_skool_vec": {"queue": "default"},
        "app.worker.tasks.datalake_export.export_datalake": {"queue": "default"},
        "app.worker.tasks.datalake_insights.datalake_insights_sweep": {"queue": "default"},
        "app.worker.tasks.company_brain.write_company_brain_journal": {"queue": "default"},
        "app.worker.tasks.crm_hygiene.run_crm_hygiene": {"queue": "default"},
        "app.worker.tasks.linkedin_invites.send_linkedin_invites": {"queue": "default"},
        "app.worker.tasks.lead_emails.send_playbook_email": {"queue": "default"},
        "app.worker.tasks.email_campaigns.send_campaign": {"queue": "default"},
        "app.worker.tasks.auto_gates.survey_autosend": {"queue": "default"},
        "app.worker.tasks.auto_gates.post_autoapprove": {"queue": "default"},
        "app.worker.tasks.linkedin_ads_report.send_linkedin_ads_report": {"queue": "default"},
        "app.worker.tasks.linkedin_ads_control.linkedin_ads_control": {"queue": "default"},
        "app.worker.tasks.tiktok_inbox_reconcile.reconcile_tiktok_inbox": {"queue": "default"},
        "app.worker.tasks.failure_learning.run_failure_learning_sweep": {"queue": "default"},
        "app.worker.tasks.edit_diff.run_edit_diff_sweep": {"queue": "default"},
        "app.worker.tasks.kakkava_sync.sync_kakkava_corpus": {"queue": "default"},
        # ── agency pipeline: lead triage → site audit → proposal → PM ──
        "app.worker.tasks.agency.run_agency_triage_sweep": {"queue": "default"},
        "app.worker.tasks.agency.run_agency_audit_sweep": {"queue": "default"},
        "app.worker.tasks.agency.run_agency_proposal_sweep": {"queue": "default"},
        "app.worker.tasks.agency.run_agency_pm_sweep": {"queue": "default"},
        "app.worker.tasks.agency.run_agency_engineer_sweep": {"queue": "default"},
        "app.worker.tasks.notebooks.run_weekly_analytics": {"queue": "default"},
        "app.worker.tasks.human_gate.run_human_gate_watch": {"queue": "default"},
        "app.worker.tasks.nurture.run_nurture_sweep": {"queue": "default"},
        "app.worker.tasks.funnel_sync.run_funnel_sync": {"queue": "default"},
        "app.worker.tasks.funnel_score.run_funnel_score": {"queue": "default"},
        "app.worker.tasks.auto_gates.engagement_autoapprove": {"queue": "default"},
        "app.worker.tasks.gate_sweep.run_gate_sweep": {"queue": "default"},
        "app.worker.tasks.newsletter_subs.run_newsletter_subscriber_sync": {"queue": "default"},
        "app.worker.tasks.publish_retry.run_publish_retry_sweep": {"queue": "publishing"},
        "app.worker.tasks.personal_messenger.poll_personal_messenger": {"queue": "messenger"},
        "app.worker.tasks.linkedin_messenger.poll_linkedin_messenger": {"queue": "messenger"},
        "app.worker.tasks.threads_messenger.poll_threads_messenger": {"queue": "messenger"},
        "app.worker.tasks.twitter_messenger.poll_twitter_messenger": {"queue": "messenger"},
        "app.worker.tasks.tiktok_messenger.poll_tiktok_messenger": {"queue": "messenger"},
        "app.worker.tasks.instagram_messenger.poll_instagram_messenger": {"queue": "messenger"},
    },
    beat_schedule={
        # Weekly publish/agency/audit KPI digest → ops channel (notebook twin).
        "weekly-analytics-digest": {
            "task": "app.worker.tasks.notebooks.run_weekly_analytics",
            "schedule": crontab(day_of_week="mon", hour=8, minute=0),
        },
        # Human-gate watcher: sidecar checkpoints (LinkedIn 2FA, Meta
        # restriction) → ops nudge, deduped 6h per gate in Redis.
        "human-gate-watch": {
            "task": "app.worker.tasks.human_gate.run_human_gate_watch",
            "schedule": crontab(minute="*/30"),
        },
        # Platform-truth reconciliation — re-reads each connected account's
        # live status and flags drift (issue #986).
        "account-status-reconcile": {
            "task": "app.worker.tasks.account_status_reconcile.reconcile_account_status",
            "schedule": crontab(minute=40, hour="*/6"),
        },
        # Engagement-gate auto-approve — AUTONOMY_LEVEL dial (2=safe, 3=full).
        "engagement-autoapprove": {
            "task": "app.worker.tasks.auto_gates.engagement_autoapprove",
            "schedule": crontab(minute="*/10"),
        },
        # Gate-sweep — fires pending human-gates via the CDP bridge when
        # the debug Chrome is live (consent/OAuth stops queuing for clicks).
        "gate-sweep": {
            "task": "app.worker.tasks.gate_sweep.run_gate_sweep",
            "schedule": crontab(minute=15),
        },
        # Funnel sync — EspoCRM→Mautic contacts + MinIO funnel-zone export.
        "funnel-score": {
            "task": "app.worker.tasks.funnel_score.run_funnel_score",
            "schedule": crontab(minute=15),
        },
        "funnel-sync": {
            "task": "app.worker.tasks.funnel_sync.run_funnel_sync",
            "schedule": crontab(minute=20),
        },
        # Public-lead nurture sequence — day 3/7/14 touches on
        # newsletter + free-audit leads (idempotent meta_data stamps).
        "nurture-sweep": {
            "task": "app.worker.tasks.nurture.run_nurture_sweep",
            "schedule": crontab(hour=11, minute=0),
        },
        # Weekly LinkedIn newsletter subscribers → EspoCRM (sidecar voyager call).
        "newsletter-subs-sync": {
            "task": "app.worker.tasks.newsletter_subs.run_newsletter_subscriber_sync",
            "schedule": crontab(day_of_week="sun", hour=9, minute=15),
        },
        "process-publish-queue": {
            "task": "app.worker.tasks.publishing.process_publish_queue",
            "schedule": 30.0,
        },
        # Retry failed publish targets — transient causes (media URL
        # expiry, token contention) resolve on requeue. 15min cadence.
        "publish-retry-sweep": {
            "task": "app.worker.tasks.publish_retry.run_publish_retry_sweep",
            "schedule": crontab(minute="*/15"),
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
        # Read the lake source tables back into decision signals every 6h,
        # right after the export — persists an AnalyticsEvent the next
        # export carries into the gold layer + pings the alert channel.
        "datalake-insights-sweep": {
            "task": "app.worker.tasks.datalake_insights.datalake_insights_sweep",
            "schedule": crontab(minute=20, hour="*/6"),  # every 6h at :20
        },
        # Push real follower metrics → Nextcloud Tables and mirror the
        # post pipeline → Deck board (idempotent, deduped by post:<uuid>
        # markers + date/platform/metric row keys).
        "sync-nextcloud-surfaces": {
            "task": "app.worker.tasks.nextcloud_sync.sync_nextcloud_surfaces",
            "schedule": crontab(minute=25, hour="*/3"),  # every 3h at :25
        },
        # Nightly company-brain journal — real publish/follower outcomes →
        # Chroma team_<id>_brain (semantic recall) + Nextcloud Notes entry.
        # Runs after the day's work settles so the morning strategy brief
        # can recall what actually happened (services.company_brain.recall).
        "company-brain-journal": {
            "task": "app.worker.tasks.company_brain.write_company_brain_journal",
            "schedule": crontab(
                hour=settings.COMPANY_BRAIN_HOUR,
                minute=settings.COMPANY_BRAIN_MINUTE,
            ),
        },
        # Weekly EspoCRM hygiene (Monday 07:30) — dedupe leads, quarantine
        # CI/test probes, surface stale New leads past the follow-up SLA.
        # Runs before the week's campaigns so audiences stay clean.
        "crm-hygiene": {
            "task": "app.worker.tasks.crm_hygiene.run_crm_hygiene",
            "schedule": crontab(hour=7, minute=30, day_of_week="mon"),
        },
        # Daily autonomous email drip — clones the drip-template campaigns
        # (meta_data.drip_template) and mails only recipients never mailed
        # for that drip/cycle: first-approach to new EspoCRM leads, renewal
        # to contacts/opportunities entering their annual window.
        "email-drip-sweep": {
            "task": "app.worker.tasks.email_drip.run_email_drip_sweep",
            "schedule": crontab(
                hour=settings.EMAIL_DRIP_HOUR,
                minute=settings.EMAIL_DRIP_MINUTE,
            ),
        },
        # Daily lead diligence — attaches a research note (website summary +
        # Wikipedia + CRM context) to each un-researched EspoCRM lead so the
        # Sales dept's next touch is informed. The note is the dedupe marker.
        "lead-research-sweep": {
            "task": "app.worker.tasks.lead_research.run_lead_research_sweep",
            "schedule": crontab(
                hour=settings.LEAD_RESEARCH_HOUR,
                minute=settings.LEAD_RESEARCH_MINUTE,
            ),
        },
        # CRM lifecycle — score every unscored live lead (covers playbook/
        # Messenger/manual ingress the cloudless.gr contact-path scorer
        # misses) and auto-convert hot real-email leads via Espo's own
        # /Lead/action/convert. Runs just after the research sweep so a new
        # lead gets diligence → score → convert in the same morning.
        "crm-lifecycle-sweep": {
            "task": "app.worker.tasks.crm_lifecycle.run_crm_lifecycle_sweep",
            "schedule": crontab(hour=8, minute=50),  # 08:50 after lead research
        },
        # CRM engagement — poll the dovecot mailbox every 30 min; a reply
        # from a CRM email attaches a "Reply received" note and bumps the
        # lead to In Process. Closes the loop that drip-send notes open.
        "engagement-ingest": {
            "task": "app.worker.tasks.engagement.engagement_ingest",
            "schedule": crontab(minute=15),
            "options": {"queue": "default"},
        },
        "crm-engagement-sweep": {
            "task": "app.worker.tasks.crm_engagement.run_crm_engagement_sweep",
            "schedule": crontab(minute="*/30"),
        },
        # ── Autonomous agency (agent-led fixed-scope pipeline) ─────────
        # Front Desk: classify new leads into the service catalog and
        # queue a site audit when an auditable domain is found.
        "agency-triage-sweep": {
            "task": "app.worker.tasks.agency.run_agency_triage_sweep",
            "schedule": crontab(minute="*/15"),
        },
        # Architect: run the deterministic site audit for pending rows —
        # demand-driven, only works while audits wait.
        "agency-audit-sweep": {
            "task": "app.worker.tasks.agency.run_agency_audit_sweep",
            "schedule": crontab(minute="*/10"),
        },
        # Sales drafter: completed audits become priced proposals in the
        # Slack review lane — approval is always a human click.
        "agency-proposal-sweep": {
            "task": "app.worker.tasks.agency.run_agency_proposal_sweep",
            "schedule": crontab(minute="*/30"),
        },
        # Supervisor: 14-day milestone loop for won engagements, after the
        # 09:00 digest so the PM report rides the morning batch.
        "agency-pm-sweep": {
            "task": "app.worker.tasks.agency.run_agency_pm_sweep",
            "schedule": crontab(hour=9, minute=20),
        },
        # Engineers (Joshi 2025 agentic-DevOps): draft PR manifests for
        # sent proposals — plans only, humans/Devin implement.
        "agency-engineer-sweep": {
            "task": "app.worker.tasks.agency.run_agency_engineer_sweep",
            "schedule": crontab(hour=9, minute=40),
        },
        "check-scheduled-posts": {
            "task": "app.worker.tasks.publishing.check_scheduled_posts",
            "schedule": 60.0,
        },
        # Failure-trajectory learning — classify failed post_targets into
        # the signature taxonomy; signatures recurring >=3x in 7d escalate
        # as remediation proposals (Slack + audit_logs, human stays the
        # approval gate). Daily after the publish/analytics dust settles.
        "failure-learning-sweep": {
            "task": "app.worker.tasks.failure_learning.run_failure_learning_sweep",
            "schedule": crontab(hour=10, minute=5),
        },
        # Weekly edit-diff voice learning (Sunday 07:00) — diffs generated
        # vs kept copy on edited posts and proposes voice_signature
        # improvements to the human (never auto-applies).
        "edit-diff-sweep": {
            "task": "app.worker.tasks.edit_diff.run_edit_diff_sweep",
            "schedule": crontab(hour=7, minute=0, day_of_week="sun"),
        },
        # Daily SocialAuto report → Slack #socialauto is triggered by the
        # n8n workflow `socialauto-daily-slack-digest` (cron 0 9 * * * →
        # POST /api/v1/ops/daily-digest), NOT by beat — a beat entry here
        # double-posts the digest every morning (observed 2026-09-18/21/22).
        # Weekly SocialAuto rollup → Slack #socialauto + owner email
        # (Monday 09:00 Europe/Athens)
        "weekly-slack-rollup": {
            "task": "app.worker.tasks.digest.send_weekly_slack_digest",
            "schedule": crontab(hour=settings.SLACK_DIGEST_HOUR, minute=0, day_of_week=1),
            "kwargs": {"days": 7, "post_to_slack": True, "post_to_email": True},
        },
        # Monthly rollup → Slack + owner email (1st of month). The 30-day
        # window adds MoM follower deltas, a linear next-month forecast,
        # and the impressions→engagement→clicks funnel per platform.
        "monthly-slack-rollup": {
            "task": "app.worker.tasks.digest.send_weekly_slack_digest",
            "schedule": crontab(hour=settings.SLACK_DIGEST_HOUR, minute=0, day_of_month=1),
            "kwargs": {"days": 30, "post_to_slack": True, "post_to_email": True},
        },
        # Daily free-promotion scan → email. Sweeps the digest mailbox for
        # ad-credit/coupon/promo-offer mail (how every platform delivers
        # credits), pulls live LinkedIn credit state, flags near/unlocked
        # creator-program gates, and lists console-only promo surfaces to
        # check by hand. Read-only — reports before the monetization report
        # so the morning batch leads with what's exploitable for free.
        "daily-promo-scan": {
            "task": "app.worker.tasks.promo_scan.run_promo_scan",
            "schedule": crontab(
                hour=settings.PROMO_SCAN_HOUR,
                minute=settings.PROMO_SCAN_MINUTE,
            ),
            "kwargs": {"send": True},
        },
        # Daily monetization report → email + auto-scheduled posts. Runs
        # before the strategy brief: evaluates every account against its
        # creator-program gate, writes per-platform strategy via the
        # inference chain, and schedules the resulting actions as real
        # posts through the normal publish queue. Notebook-generated so
        # the layout/charts are editable from the Jupyter UI; falls back
        # to the code-path report (build → execute → email) on failure.
        "daily-monetization-report": {
            "task": "app.worker.tasks.notebook_reports.run_notebook_report",
            "schedule": crontab(
                hour=settings.MONETIZATION_REPORT_HOUR,
                minute=settings.MONETIZATION_REPORT_MINUTE,
            ),
            "kwargs": {
                "notebook": "monetization_daily",
                "parameters": {"insight_days": 30, "execute": True},
                "send": True,
                "fallback_to_code": True,
            },
        },
        # Morning strategy brief → email. Notebook-generated (papermill in
        # the worker env) so the report layout/charts are editable from the
        # Jupyter UI; falls back to the code-path report on notebook failure.
        "daily-strategy-report": {
            "task": "app.worker.tasks.notebook_reports.run_notebook_report",
            "schedule": crontab(
                hour=settings.STRATEGY_REPORT_HOUR,
                minute=settings.STRATEGY_REPORT_MINUTE,
            ),
            "kwargs": {
                "notebook": "daily_strategy_brief",
                "parameters": {"insight_days": 30},
                "send": True,
                "fallback_to_code": True,
            },
        },
        # Ops health check 15min after the strategy brief — queue state,
        # failed targets, follower-series anomalies, sync freshness, content
        # scoreboard. No code-path equivalent, so a notebook failure must not
        # substitute a strategy brief.
        "daily-ops-health": {
            "task": "app.worker.tasks.notebook_reports.run_notebook_report",
            "schedule": crontab(
                hour=settings.STRATEGY_REPORT_HOUR,
                minute=(settings.STRATEGY_REPORT_MINUTE + 15) % 60,
            ),
            "kwargs": {
                "notebook": "ops_health",
                "send": True,
                "fallback_to_code": False,
            },
        },
        # Daily billing usage/revenue digest → Slack billing channel (10:00 Europe/Athens)
        "daily-paddle-digest": {
            "task": "app.worker.tasks.paddle_digest.send_paddle_slack_digest",
            "schedule": crontab(
                hour=settings.SLACK_BILLING_DIGEST_HOUR if settings.SLACK_BILLING_DIGEST_HOUR is not None else settings.SLACK_PADDLE_DIGEST_HOUR,
                minute=0,
            ),
            "kwargs": {"post_to_slack": True},
        },
        # Auto-refresh expiring OAuth tokens (TikTok expires in 24h,
        # Twitter in 2h, Meta/Threads in ~60 days). Refreshes tokens expiring
        # within the next 4 hours so accounts never go offline unexpectedly.
        # Runs at :15 and :45 — a failed run retries in 30min instead of
        # leaving a 2h-lifetime X token to lapse into 401s for a full hour.
        "refresh-expiring-tokens": {
            "task": "app.worker.tasks.token_refresh.refresh_expiring_tokens",
            "schedule": crontab(minute="15,45"),
        },
        # Daily Ombudsman complaint-status poll → Telegram on change
        "synigoros-status": {
            "task": "app.worker.tasks.synigoros_status.poll_synigoros",
            "schedule": crontab(hour=7, minute=30),
        },
        # Founders' Circle mastersheet watcher — Telegram + datalake on change
        "mastersheet-watch": {
            "task": "app.worker.tasks.mastersheet_watch.watch_mastersheet",
            "schedule": crontab(hour=8, minute=15),
        },
        # Execute datalake actions — drafts, pings, weights (+25min after sweep)
        "datalake-executor": {
            "task": "app.worker.tasks.datalake_executor.execute_actions",
            "schedule": crontab(hour="*/6", minute=45),
        },
        # Purge terminal publish_queue rows (failed/cancelled) older than 3
        # days — keeps the queue bounded and the failed-count metric
        # meaningful. Error details persist on posts/post_targets.
        # Safety net for X targets stuck failed/skipped by a capacity error
        # (credits/quota/fallback cap) — re-queues each at most once.
        "requeue-capacity-stuck-x": {
            "task": "app.worker.tasks.publishing.requeue_capacity_stuck_x_targets",
            "schedule": crontab(minute="*/30"),
        },
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
        # Self-healing session sweep every hour at :20 — probes the
        # LinkedIn/Facebook sidecars and all shared-bridge platforms,
        # attempts the cheapest recovery (cookie re-inject, credential
        # login, Threads IG-SSO bootstrap), persists fresh session
        # material, and Slack-alerts only when human action is needed.
        "heal-sessions": {
            "task": "app.worker.tasks.session_healer.heal_sessions",
            "schedule": crontab(minute=20),
        },
        # Keep-warm ping every 90s — refreshes keep_alive on the warm-tier
        # DMR models (mid 4B + llama3.2) so pipeline and chatbot calls never
        # cold-load. Interval stays below llama3.2's 2m keep_alive; the task
        # skips itself while a media job owns the GPU.
        "survey-autosend": {
            "task": "app.worker.tasks.auto_gates.survey_autosend",
            "schedule": 900.0,  # every 15 min — fires flagged survey campaigns
            "options": {"queue": "default"},
        },
        "log-intel": {
            "task": "app.worker.tasks.log_intel.digest",
            "schedule": 1800.0,  # 30 min — syslog→LLM triage digest
            "options": {"queue": "default"},
        },
        "consent-healers": {
            "task": "app.worker.tasks.consent_healers.heal_consents",
            "schedule": 3600.0,  # hourly — finish the chain when consent lands
        },
        "media-backfill": {
            "task": "app.worker.tasks.consent_healers.media_backfill",
            "schedule": 3600.0,  # hourly — attach media to needs_media drafts
            "options": {"queue": "default"},
        },
        "post-autoapprove": {
            "task": "app.worker.tasks.auto_gates.post_autoapprove",
            "schedule": 600.0,  # every 10 min — promotes REVIEW when enabled
            "options": {"queue": "default"},
        },
        "keep-warm-dmr-models": {
            "task": "app.worker.tasks.dmr_health.keep_warm_dmr_models",
            "schedule": 90.0,
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
        # Telegram channel agent — daily autopilot for the managed channel
        # (supersedes the n8n cloudless-telegram-channel workflow): pillar
        # content Mon/Wed/Fri, Sunday poll, adaptation from funnel analytics.
        # Welcomes + join-request approvals are webhook-driven, not scheduled.
        "telegram-channel-agent": {
            "task": "app.worker.tasks.telegram_agent.run_channel_agent_daily",
            "schedule": crontab(hour=11, minute=0),
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
        # Watch the Sofia Kakkava VEC Skool community (classroom + feed)
        # through the browser bridge; new DAY lessons auto-draft a LinkedIn
        # post and Slack-alert, other changes post to the digest channel.
        "skool-vec-watch": {
            "task": "app.worker.tasks.skool_watch.watch_skool_vec",
            "schedule": 10800.0,  # every 3 hours
            "options": {"queue": "default"},
        },
        # Daily Kakkava doctrine sync — re-walk the read-only corpus mount,
        # embed new/changed chunks into team_<id>_doctrine so generation
        # recalls the current source material (services.kakkava.sync_corpus).
        "kakkava-corpus-sync": {
            "task": "app.worker.tasks.kakkava_sync.sync_kakkava_corpus",
            "schedule": crontab(
                hour=settings.KAKKAVA_SYNC_HOUR,
                minute=settings.KAKKAVA_SYNC_MINUTE,
            ),
        },
    },
)

# ── Worker-process async loop ────────────────────────────────────────────────
# One asyncio loop per prefork child, created AFTER the fork. Tasks submit
# coroutines via app.worker._async.run_async; the pooled DB engine lives as
# long as the child. Building it here (not at import time) is what prevents
# forked parents from sharing connections into children.

from celery.signals import task_failure, worker_process_init, worker_process_shutdown  # noqa: E402


@worker_process_init.connect
def _start_worker_async_loop(**_kwargs):
    from app.worker._async import start_worker_loop

    start_worker_loop()


@worker_process_shutdown.connect
def _stop_worker_async_loop(**_kwargs):
    from app.worker._async import stop_worker_loop

    stop_worker_loop()


@task_failure.connect
def _notify_task_failure(sender=None, task_id=None, exception=None, **_kwargs):
    """Post unexpected task failures to the ops channel — silent task
    deaths currently only surface in docker logs."""
    try:
        import asyncio

        from app.services.slack_notifications import post_alert_to_slack

        name = getattr(sender, "name", str(sender))
        msg = f":warning: Worker task failed `{name}`\n```{str(exception)[:300]}```"
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(post_alert_to_slack(msg))
            else:
                loop.run_until_complete(post_alert_to_slack(msg))
        except RuntimeError:
            asyncio.run(post_alert_to_slack(msg))
    except Exception:  # noqa: BLE001 — never let the notifier crash the worker
        pass
