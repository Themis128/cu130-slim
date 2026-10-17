"""Export SocialAuto data to the cloudless.gr R2 datalake.

Writes JSON tables to ``lake/socialauto-*/`` in the datalake bucket so the
site's ``materialize-datalake-snapshots`` ETL can build gold sections:

    lake/socialauto-accounts/accounts.json
    lake/socialauto-posts/posts.json
    lake/socialauto-post-metrics/metrics.json
    lake/socialauto-followers/followers.json
    lake/socialauto-account-events/events.json
    lake/socialauto-insights/<team>.json
    lake/socialauto-leads/leads.json
    lake/socialauto-telegram/channels.json
    lake/socialauto-web-events/events.json
    lake/socialauto-ads/snapshots.json
    lake/socialauto-ads/daily.json
    lake/socialauto-ads/demographics.json

Each run overwrites the whole table (snapshot-style export) — idempotent
by construction, no dedup needed downstream. PII is minimized: leads keep
only a sha256 email hash + domain; web events drop client_ip/user_agent.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from celery import shared_task
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.analytics import AnalyticsEvent, FollowerSnapshot, PostAnalyticsSnapshot
from app.models.content import Post, PostTarget
from app.models.lead import Lead
from app.models.linkedin_ads import (
    AdCampaignSnapshot,
    AdDailyMetric,
    AdDemographicSegment,
)
from app.models.social_account import SocialAccount
from app.models.user import Team
from app.models.web_analytics import WebAnalyticsEvent
from app.services import r2_storage
from app.services.insights_engine import build_team_insights
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

settings = get_settings()

_WEB_EVENTS_DAYS = 90


_worker_db = task_session


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()


def _email_hash(email: str | None) -> str | None:
    e = (email or "").strip().lower()
    return hashlib.sha256(e.encode()).hexdigest()[:16] if e else None


def _email_domain(email: str | None) -> str | None:
    e = (email or "").strip().lower()
    return e.rsplit("@", 1)[-1] if "@" in e else None


async def _put_json(key: str, rows: Any, bucket: str) -> dict:
    data = json.dumps(rows, ensure_ascii=False, default=str).encode()
    return await r2_storage.upload_object(
        key, data, content_type="application/json", bucket=bucket
    )


async def _export_accounts(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(select(SocialAccount))).scalars().all()
    return [
        {
            "account_id": str(a.id),
            "team_id": str(a.team_id),
            "platform": a.platform,
            "username": a.username,
            "display_name": a.display_name,
            "account_type": a.account_type,
            "is_business": a.is_business,
            "status": a.status,
            "scopes": sorted(a.scopes or []),
            "token_expires_at": _iso(a.token_expires_at),
            "connected_at": _iso(a.created_at),
        }
        for a in rows
    ]


async def _export_posts(db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(Post, PostTarget, SocialAccount.platform)
        .join(PostTarget, PostTarget.post_id == Post.id)
        .join(SocialAccount, SocialAccount.id == PostTarget.social_account_id)
        .order_by(Post.created_at.desc())
    )
    by_post: dict[str, dict] = {}
    for post, target, platform in result.all():
        key = str(post.id)
        entry = by_post.setdefault(key, {
            "post_id": key,
            "team_id": str(post.team_id),
            "status": str(post.status),
            "content_snippet": (post.content_text or "")[:200],
            "link_url": post.link_url,
            "hashtags": list(post.hashtags or []),
            "media_count": len(post.media_ids or []),
            "scheduled_at": _iso(post.scheduled_at),
            "published_at": _iso(post.published_at),
            "created_at": _iso(post.created_at),
            "targets": [],
        })
        entry["targets"].append({
            "account_id": str(target.social_account_id),
            "platform": platform,
            "platform_post_id": target.platform_post_id,
            "platform_url": target.platform_url,
            "status": target.status,
        })
    return list(by_post.values())


async def _export_metrics(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            select(PostAnalyticsSnapshot).order_by(
                PostAnalyticsSnapshot.captured_at.desc()
            )
        )
    ).scalars().all()
    return [
        {
            "snapshot_id": str(s.id),
            "team_id": str(s.team_id),
            "post_id": str(s.post_id) if s.post_id else None,
            "account_id": str(s.social_account_id),
            "platform": s.platform,
            "platform_post_id": s.platform_post_id,
            "impressions": s.impressions,
            "clicks": s.clicks,
            "likes": s.likes,
            "comments": s.comments,
            "shares": s.shares,
            "reach": s.reach,
            "engagement": s.engagement,
            "engagement_rate": s.engagement_rate,
            "source": s.source,
            "notes": s.notes,
            "captured_at": _iso(s.captured_at),
        }
        for s in rows
    ]


async def _export_followers(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            select(FollowerSnapshot).order_by(FollowerSnapshot.captured_at)
        )
    ).scalars().all()
    return [
        {
            "account_id": str(f.social_account_id),
            "team_id": str(f.team_id),
            "platform": f.platform,
            "followers": f.followers,
            "captured_at": _iso(f.captured_at),
        }
        for f in rows
    ]


async def _export_account_events(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            select(AnalyticsEvent)
            .where(AnalyticsEvent.post_id.is_(None))
            .order_by(AnalyticsEvent.occurred_at.desc())
        )
    ).scalars().all()
    return [
        {
            "team_id": str(e.team_id),
            "account_id": str(e.social_account_id) if e.social_account_id else None,
            "platform": e.platform,
            "event_type": e.event_type,
            "meta_data": e.meta_data or {},
            "occurred_at": _iso(e.occurred_at),
        }
        for e in rows
    ]


# CI functional tests write fixture leads into the live table (they hit the
# real endpoint). Reserved example.* domains can never be a deliverable lead,
# so exclude them from the lake rather than let digests report test rows as
# unsynced leads.
_FIXTURE_EMAIL_SUFFIXES = (
    "%@example.invalid",
    "%@example.com",
    "%@example.org",
    "%@example.net",
)


async def _export_leads(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            select(Lead, SocialAccount.platform)
            .outerjoin(SocialAccount, SocialAccount.id == Lead.social_account_id)
            .where(
                *[Lead.email.not_ilike(s) for s in _FIXTURE_EMAIL_SUFFIXES]
            )
            .order_by(Lead.created_at.desc())
        )
    ).all()
    return [
        {
            "lead_id": str(lead.id),
            "team_id": str(lead.team_id),
            "source": str(lead.source),
            "platform": platform,
            "interest": str(lead.interest) if lead.interest else None,
            "company_size": str(lead.company_size) if lead.company_size else None,
            # PII-minimized: hash supports dedup/joins, domain supports
            # segmentation — no raw email/name in the lake.
            "email_hash": _email_hash(lead.email),
            "email_domain": _email_domain(lead.email),
            "espocrm_synced": bool((lead.meta_data or {}).get("espocrm_lead_id")),
            "created_at": _iso(lead.created_at),
        }
        for lead, platform in rows
    ]


async def _export_email_campaigns(db: AsyncSession) -> list[dict]:
    """Campaigns with per-status recipient counts + delivery events.

    PII-minimized: recipients are exported as ``email_hash`` rows only —
    campaign content, delivery status and funnel stage, never raw addresses.
    """
    from app.models.email_campaign import (
        EmailCampaign,
        EmailRecipient,
        SurveyResponse,
    )

    campaigns = (
        await db.execute(select(EmailCampaign).order_by(EmailCampaign.created_at.desc()))
    ).scalars().all()
    out: list[dict] = []
    for c in campaigns:
        recs = (
            await db.execute(
                select(EmailRecipient).where(EmailRecipient.campaign_id == c.id)
            )
        ).scalars().all()
        responses = (
            await db.execute(
                select(SurveyResponse, EmailRecipient.email)
                .outerjoin(
                    EmailRecipient, EmailRecipient.id == SurveyResponse.recipient_id
                )
                .where(SurveyResponse.campaign_id == c.id)
            )
        ).all()
        out.append(
            {
                "campaign_id": str(c.id),
                "team_id": str(c.team_id),
                "name": c.name,
                "kind": str(c.kind),
                "status": str(c.status),
                "survey_url": bool(c.survey_url),
                "sent_at": _iso(c.sent_at),
                "created_at": _iso(c.created_at),
                "recipients": [
                    {
                        "email_hash": _email_hash(r.email),
                        "lead_id": str(r.lead_id) if r.lead_id else None,
                        "status": str(r.status),
                        "sent_at": _iso(r.sent_at),
                    }
                    for r in recs
                ],
                # Funnel submissions — answers keyed by question id; respondent
                # identified by hash/lead only, never raw email.
                "responses": [
                    {
                        "email_hash": _email_hash(email) if email else None,
                        "lead_id": str(resp.lead_id) if resp.lead_id else None,
                        "answers": resp.answers,
                        "espocrm_noted": resp.espocrm_noted,
                        "submitted_at": _iso(resp.submitted_at),
                    }
                    for resp, email in responses
                ],
            }
        )
    return out


async def _export_telegram_channels(db: AsyncSession) -> list[dict]:
    """Dimension table for the Telegram channel funnel — one row per chat
    the bot administers (discovered via ``my_chat_member`` webhook events),
    plus named invite links. Invite-link URLs are excluded: they grant
    access to the private channel and are treated as secrets."""
    rows = (
        await db.execute(
            select(SocialAccount).where(SocialAccount.platform == "telegram")
        )
    ).scalars().all()
    out: list[dict] = []
    for a in rows:
        meta = a.meta_data or {}
        managed = meta.get("telegram_channel") or {}
        channels = meta.get("telegram_channels") or {}
        for chat_id, ch in channels.items():
            if not isinstance(ch, dict):
                continue
            links = [
                {
                    "name": link.get("name"),
                    "creates_join_request": bool(link.get("creates_join_request")),
                    "created_at": link.get("created_at"),
                }
                for link in (ch.get("invite_links") or [])
                if isinstance(link, dict)
            ]
            out.append(
                {
                    "account_id": str(a.id),
                    "team_id": str(a.team_id),
                    "chat_id": str(chat_id),
                    "title": ch.get("title") or "",
                    "type": ch.get("type") or "",
                    "bot_status": ch.get("status") or "",
                    "managed": str(managed.get("chat_id") or "") == str(chat_id),
                    "invite_links": links,
                    "added_at": ch.get("added_at"),
                    "updated_at": ch.get("updated_at"),
                }
            )
    return out


async def _export_web_events(db: AsyncSession) -> list[dict]:
    since = datetime.now(UTC) - timedelta(days=_WEB_EVENTS_DAYS)
    rows = (
        await db.execute(
            select(WebAnalyticsEvent)
            .where(WebAnalyticsEvent.occurred_at >= since)
            .order_by(WebAnalyticsEvent.occurred_at.desc())
        )
    ).scalars().all()
    out = []
    for e in rows:
        payload = e.payload or {}
        out.append({
            "team_id": str(e.team_id),
            "domain": e.domain,
            "event_name": e.event_name,
            "session_id": e.session_id,
            "path": e.path,
            "referrer": e.referrer,
            "locale": e.locale,
            "utm_source": payload.get("utm_source"),
            "utm_medium": payload.get("utm_medium"),
            "utm_campaign": payload.get("utm_campaign"),
            # client_ip / user_agent intentionally dropped (PII minimization)
            "occurred_at": _iso(e.occurred_at),
        })
    return out


async def _export_ad_snapshots(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            select(AdCampaignSnapshot).order_by(AdCampaignSnapshot.captured_at.desc())
        )
    ).scalars().all()
    return [
        {
            "team_id": str(s.team_id),
            "platform": s.platform,
            "account_id": s.account_id,
            "campaign_id": s.campaign_id,
            "campaign_name": s.campaign_name,
            "status": s.status,
            "impressions": s.impressions,
            "clicks": s.clicks,
            "engagements": s.engagements,
            "spend_eur": s.spend_eur,
            "ctr": s.ctr,
            "engagement_rate": s.engagement_rate,
            "cpc_eur": s.cpc_eur,
            "budget_eur": s.budget_eur,
            "captured_at": _iso(s.captured_at),
        }
        for s in rows
    ]


async def _export_ad_daily(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            select(AdDailyMetric).order_by(AdDailyMetric.day.desc())
        )
    ).scalars().all()
    return [
        {
            "team_id": str(r.team_id),
            "platform": r.platform,
            "account_id": r.account_id,
            "report_type": r.report_type,
            "campaign_id": r.campaign_id,
            "campaign_name": r.campaign_name,
            "ad_set_id": r.ad_set_id,
            "ad_set_name": r.ad_set_name,
            "ad_id": r.ad_id,
            "ad_name": r.ad_name,
            "placement": r.placement,
            "status": r.status,
            "day": r.day.isoformat() if r.day else None,
            "impressions": r.impressions,
            "clicks": r.clicks,
            "engagements": r.engagements,
            "leads": r.leads,
            "conversions": r.conversions,
            "reach": r.reach,
            "clicks_to_landing_page": r.clicks_to_landing_page,
            "clicks_to_linkedin_page": r.clicks_to_linkedin_page,
            "spend_eur": r.spend_eur,
            "ctr": r.ctr,
            "cpc_eur": r.cpc_eur,
            "cpm_eur": r.cpm_eur,
            "engagement_rate": r.engagement_rate,
            "budget_eur": r.budget_eur,
            "source": r.source,
        }
        for r in rows
    ]


async def _export_ad_demographics(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            select(AdDemographicSegment).order_by(
                AdDemographicSegment.impressions.desc()
            )
        )
    ).scalars().all()
    return [
        {
            "team_id": str(r.team_id),
            "platform": r.platform,
            "account_id": r.account_id,
            "segment_type": r.segment_type,
            "segment_value": r.segment_value,
            "window_start": r.window_start.isoformat() if r.window_start else None,
            "window_end": r.window_end.isoformat() if r.window_end else None,
            "impressions": r.impressions,
            "clicks": r.clicks,
            "conversions": r.conversions,
            "ctr": r.ctr,
            "pct_impressions": r.pct_impressions,
            "pct_clicks": r.pct_clicks,
            "source": r.source,
        }
        for r in rows
    ]


# Hostnames traced by Cloudflare zone tracing — per-host aggregates are
# computed against Tempo so the lake keeps edge latency beyond Tempo's
# 7-day retention. Apex pattern is anchored so cloud./www. don't leak in.
# TraceQL regex strings do not support \. escapes — a bare "." in the
# pattern already matches the literal dot (and would only over-match on
# hostnames like "cloudless_gr" which don't exist on this zone).
_EDGE_HOSTS = [
    ("cloudless.gr", "https://cloudless.gr/"),
    ("cloud.cloudless.gr", "https://cloud.cloudless.gr/"),
    ("office.cloudless.gr", "https://office.cloudless.gr/"),
    ("social.cloudless.gr", "https://social.cloudless.gr/"),
    ("espocrm.cloudless.gr", "https://espocrm.cloudless.gr/"),
    ("webmail.cloudless.gr", "https://webmail.cloudless.gr/"),
    ("grafana.cloudless.gr", "https://grafana.cloudless.gr/"),
]

_NOTEBOOK_OUTPUT = Path("/notebooks/output")


_CF_GQL = "https://api.cloudflare.com/client/v4/graphql"
_CF_REST = "https://api.cloudflare.com/client/v4"


async def _cf_gql(client: httpx.AsyncClient, token: str, acct: str, body: str) -> dict:
    query = f'{{viewer{{accounts(filter:{{accountTag:"{acct}"}}){{{body}}}}}}}'
    resp = await client.post(
        _CF_GQL,
        json={"query": query},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errors"):
        raise RuntimeError(
            "; ".join(e.get("message", "?") for e in payload["errors"])
        )
    return payload["data"]["viewer"]["accounts"][0]


async def _cf_rest(
    client: httpx.AsyncClient, token: str, acct: str, path: str
) -> list:
    # Paths starting with "/" are account-independent (e.g. /zones).
    url = f"{_CF_REST}{path}" if path.startswith("/") else f"{_CF_REST}/accounts/{acct}/{path}"
    resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    return resp.json().get("result") or []


async def _export_cloudflare_usage() -> dict:
    """Cloudflare platform usage from the free GraphQL Analytics API.

    Covers Workers invocations/errors/status, zone HTTP traffic, R2 ops,
    D1 rows, KV ops, Workers AI inferences, AI Gateway tokens and Vectorize
    indexes (REST — no GraphQL dataset exists for it). The site's
    materialize ETL joins this with socialauto-* gold sections so infra
    cost/funnel signals sit next to content metrics.
    """
    acct = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
    token = (settings.CLOUDFLARE_API_TOKEN or "").strip()
    if not (acct and token):
        return {"error": "Cloudflare credentials not configured"}

    now = datetime.now(UTC)
    dt_start = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    d_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    d_end = now.strftime("%Y-%m-%d")

    async with httpx.AsyncClient(timeout=60) as client:
        data = await _cf_gql(
            client,
            token,
            acct,
            f'''
            workers: workersInvocationsAdaptive(limit:100,
                filter:{{datetime_geq:"{dt_start}"}},
                orderBy:[sum_requests_DESC]){{
              sum{{ requests errors }} dimensions{{ scriptName status }} }}
            r2: r2OperationsAdaptiveGroups(limit:200,
                filter:{{date_geq:"{d_start}", date_leq:"{d_end}"}},
                orderBy:[sum_requests_DESC]){{
              sum{{ requests }} dimensions{{ bucketName actionType }} }}
            d1: d1AnalyticsAdaptiveGroups(limit:100,
                filter:{{date_geq:"{d_start}", date_leq:"{d_end}"}},
                orderBy:[sum_rowsRead_DESC]){{
              sum{{ readQueries writeQueries rowsRead rowsWritten }}
              dimensions{{ databaseId }} }}
            kv: kvOperationsAdaptiveGroups(limit:100,
                filter:{{date_geq:"{d_start}", date_leq:"{d_end}"}},
                orderBy:[sum_requests_DESC]){{
              sum{{ requests }} dimensions{{ namespaceId actionType }} }}
            ai: aiInferenceAdaptiveGroups(limit:100,
                filter:{{datetime_geq:"{dt_start}"}}, orderBy:[count_DESC]){{
              count dimensions{{ modelId }} }}
            aigw: aiGatewayRequestsAdaptiveGroups(limit:100,
                filter:{{datetime_geq:"{dt_start}"}}, orderBy:[count_DESC]){{
              count sum{{ uncachedTokensIn uncachedTokensOut }}
              dimensions{{ gateway provider }} }}
            ''',
        )
        d1_names = {
            d["uuid"]: d["name"]
            for d in await _cf_rest(client, token, acct, "d1/database")
        }
        kv_names = {
            n["id"]: n["title"]
            for n in await _cf_rest(client, token, acct, "storage/kv/namespaces")
        }
        # Zone HTTP traffic needs the "Zone Analytics Read" token perm —
        # intentionally omitted (unverifiable with the current token); the
        # funnel signal lives in socialauto-web-events regardless.
        vectorize = await _cf_rest(client, token, acct, "vectorize/v2/indexes")

    workers: dict[str, dict] = {}
    for row in data.get("workers") or []:
        w = workers.setdefault(
            row["dimensions"]["scriptName"],
            {"requests": 0, "errors": 0, "by_status": {}},
        )
        w["requests"] += row["sum"]["requests"]
        w["errors"] += row["sum"]["errors"]
        w["by_status"][row["dimensions"]["status"]] = (
            w["by_status"].get(row["dimensions"]["status"], 0)
            + row["sum"]["requests"]
        )

    r2: dict[str, dict] = {}
    for row in data.get("r2") or []:
        b = r2.setdefault(
            row["dimensions"]["bucketName"], {"class_a": 0, "class_b": 0}
        )
        cls = (
            "class_a"
            if row["dimensions"]["actionType"].startswith(
                ("Put", "List", "Create", "Complete", "Abort", "Delete")
            )
            else "class_b"
        )
        b[cls] += row["sum"]["requests"]

    return {
        "window_days": 7,
        "generated_at": now.isoformat(),
        "workers": workers,
        "r2_operations": r2,
        "d1": [
            {"database": d1_names.get(r["dimensions"]["databaseId"], r["dimensions"]["databaseId"]), **r["sum"]}
            for r in data.get("d1") or []
        ],
        "kv": [
            {"namespace": kv_names.get(r["dimensions"]["namespaceId"], r["dimensions"]["namespaceId"]),
             "action": r["dimensions"]["actionType"], "requests": r["sum"]["requests"]}
            for r in data.get("kv") or []
        ],
        "workers_ai": [
            {"model": r["dimensions"]["modelId"], "inferences": r["count"]}
            for r in data.get("ai") or []
        ],
        "ai_gateway": [
            {"gateway": r["dimensions"]["gateway"], "provider": r["dimensions"]["provider"],
             "requests": r["count"], **r["sum"]}
            for r in data.get("aigw") or []
        ],
        "vectorize_indexes": [
            {"name": i.get("name"), "dimensions": (i.get("config") or {}).get("dimensions")}
            for i in vectorize
        ],
    }


async def _export_edge_metrics() -> dict:
    """Last-24h Tempo aggregates per hostname: sampled traces + latency.

    TraceQL on span.url.full; durationMs is the full edge request time.
    Counts are *sampled* (10% zone sampling) — flagged so downstream
    doesn't mistake them for request totals.
    """
    end = int(datetime.now(UTC).timestamp())
    start = end - 86400
    base = settings.TEMPO_API_URL.rstrip("/")
    hosts: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30) as client:
        for host, pattern in _EDGE_HOSTS:
            entry: dict[str, Any] = {"host": host}
            try:
                span_q = f'{{span.url.full =~ "{pattern}.*"}}'

                async def _search(extra: str = "") -> list:
                    resp = await client.get(
                        f"{base}/api/search",
                        params={
                            "q": span_q + extra,
                            "start": start,
                            "end": end,
                            "limit": 1000,
                        },
                    )
                    resp.raise_for_status()
                    return resp.json().get("traces", [])

                traces = await _search()
                # CF stores status_code as a string attribute but TraceQL
                # indexes it numerically — compare as int, and use a
                # structural && spanset since status lives on a different
                # span than url.full.
                entry["errors_5xx"] = len(
                    await _search(" && {span.http.response.status_code >= 500}")
                )
                entry["errors_4xx"] = len(
                    await _search(
                        " && {span.http.response.status_code >= 400"
                        " && span.http.response.status_code < 500}"
                    )
                )
                durs = sorted(t.get("durationMs", 0) for t in traces)
                entry["sampled_traces"] = len(durs)
                if durs:
                    entry["p50_ms"] = durs[len(durs) // 2]
                    entry["p95_ms"] = durs[min(len(durs) - 1, int(len(durs) * 0.95))]
                    entry["max_ms"] = durs[-1]
            except Exception as exc:  # noqa: BLE001 — one host's failure ≠ all
                entry["error"] = str(exc)[:200]
            hosts.append(entry)
    return {
        "window_hours": 24,
        "sampled": True,
        "sampling_ratio": 0.1,
        "generated_at": datetime.now(UTC).isoformat(),
        "hosts": hosts,
    }


async def _export_reports_index() -> list[dict]:
    """Index of every rendered notebook report (manifest files)."""
    out = []
    if not _NOTEBOOK_OUTPUT.is_dir():
        return out
    for p in sorted(_NOTEBOOK_OUTPUT.glob("*.manifest.json")):
        try:
            m = json.loads(p.read_text())
            reports = m.get("reports") or ([m] if m else [])
            out.append(
                {
                    "manifest": p.name,
                    "generated_at": datetime.fromtimestamp(
                        p.stat().st_mtime, UTC
                    ).isoformat(),
                    "subjects": [r.get("subject") for r in reports],
                    "files": [
                        f
                        for r in reports
                        for f in (
                            [r.get("html_file"), r.get("text_file")]
                            + [a.get("file") for a in r.get("attachments", [])]
                        )
                        if f
                    ],
                }
            )
        except Exception:  # noqa: BLE001 — skip unreadable manifest
            continue
    return out


async def _export_monetization(db: AsyncSession) -> list[dict]:
    """One row per active account — creator-program gate evaluation so the
    site's materialize ETL can render monetization readiness next to the
    funnel tables."""
    from app.models.social_account import SocialAccount
    from app.services.monetization import (
        FUNNEL_ONLY,
        evaluate_program,
        gather_account_metrics,
    )

    accounts = (
        await db.execute(select(SocialAccount).where(SocialAccount.status == "active"))
    ).scalars().all()
    now = datetime.now(UTC).isoformat()
    rows: list[dict] = []
    for a in accounts:
        try:
            metrics = await gather_account_metrics(db, a)
        except Exception as exc:  # noqa: BLE001 — skip, don't abort the export
            rows.append(
                {
                    "platform": a.platform,
                    "account_id": str(a.id),
                    "error": str(exc)[:200],
                    "evaluated_at": now,
                }
            )
            continue
        report = evaluate_program(a.platform, metrics, account_type=a.account_type)
        rows.append(
            {
                "platform": a.platform,
                "account_id": str(a.id),
                "username": a.username,
                "account_type": a.account_type,
                "followers": metrics.get("followers"),
                "gain_per_day": metrics.get("gain_per_day"),
                "program": report.program if report else None,
                "role": None if report else FUNNEL_ONLY.get(a.platform, "funnel"),
                "criteria": (
                    [
                        {
                            "name": c.name,
                            "current": c.current,
                            "target": c.target,
                            "met": c.met,
                        }
                        for c in report.criteria
                    ]
                    if report
                    else []
                ),
                "evaluated_at": now,
            }
        )
    return rows


async def _export_ops_health(db: AsyncSession) -> dict:
    """Current ops snapshot: queue state, recent failures, sync freshness."""
    queue = (
        await db.execute(
            text(
                "SELECT sa.platform, pq.status, count(*) n"
                " FROM publish_queue pq"
                " JOIN social_accounts sa ON sa.id=pq.social_account_id"
                " GROUP BY 1,2 ORDER BY 1,2"
            )
        )
    ).mappings().all()
    failed_7d = (
        await db.execute(
            text(
                "SELECT sa.platform, pt.status, count(*) n"
                " FROM post_targets pt"
                " JOIN posts p ON p.id=pt.post_id"
                " JOIN social_accounts sa ON sa.id=pt.social_account_id"
                " WHERE pt.status IN ('failed','skipped')"
                " AND p.created_at > now() - interval '7 days'"
                " GROUP BY 1,2"
            )
        )
    ).mappings().all()
    freshness = (
        await db.execute(
            text(
                "SELECT sa.platform, sa.username,"
                " (SELECT max(pas.captured_at) FROM post_analytics_snapshots pas"
                "  WHERE pas.social_account_id=sa.id) AS last_metrics"
                " FROM social_accounts sa WHERE sa.status='active'"
            )
        )
    ).mappings().all()
    now = datetime.now(UTC)
    stale_accounts = [
        f"{r['platform']}/@{r['username']}"
        for r in freshness
        if r["last_metrics"] is None
        or (now - r["last_metrics"].replace(tzinfo=UTC)) > timedelta(hours=2)
    ]
    return {
        "generated_at": now.isoformat(),
        "queue": [dict(r) for r in queue],
        "failed_or_skipped_7d": [dict(r) for r in failed_7d],
        "stale_sync_accounts": stale_accounts,
    }


@shared_task
def export_datalake() -> dict:
    """Snapshot-export all SocialAuto lake tables to the datalake bucket."""
    return run_async(_export_async())


async def _export_async() -> dict:
    bucket = (settings.DATALAKE_R2_BUCKET or "").strip()
    if not bucket:
        return {"ok": False, "error": "DATALAKE_R2_BUCKET not configured"}
    if not (settings.CLOUDFLARE_ACCOUNT_ID and settings.CLOUDFLARE_API_TOKEN):
        return {"ok": False, "error": "Cloudflare credentials not configured"}

    async with _worker_db() as db:
        tables: dict[str, Any] = {
            "lake/socialauto-accounts/accounts.json": await _export_accounts(db),
            "lake/socialauto-posts/posts.json": await _export_posts(db),
            "lake/socialauto-post-metrics/metrics.json": await _export_metrics(db),
            "lake/socialauto-followers/followers.json": await _export_followers(db),
            "lake/socialauto-account-events/events.json": await _export_account_events(db),
            "lake/socialauto-leads/leads.json": await _export_leads(db),
            "lake/socialauto-telegram/channels.json": await _export_telegram_channels(db),
            "lake/socialauto-web-events/events.json": await _export_web_events(db),
            "lake/socialauto-ads/snapshots.json": await _export_ad_snapshots(db),
            "lake/socialauto-ads/daily.json": await _export_ad_daily(db),
            "lake/socialauto-ads/demographics.json": await _export_ad_demographics(db),
            "lake/socialauto-ops/health.json": await _export_ops_health(db),
            "lake/socialauto-monetization/gates.json": await _export_monetization(db),
            "lake/socialauto-email-campaigns/campaigns.json": await _export_email_campaigns(db),
        }

        tables["lake/socialauto-edge-metrics/daily.json"] = await _export_edge_metrics()
        tables["lake/socialauto-reports/reports.json"] = await _export_reports_index()
        try:
            cf_usage = await _export_cloudflare_usage()
        except Exception as exc:  # noqa: BLE001 — CF outage ≠ abort the export
            cf_usage = {"error": str(exc)[:300]}
        tables["lake/cloudflare-usage/latest.json"] = cf_usage
        # Dated copy keeps a daily history (runs 4x/day overwrite same-day).
        tables[
            f"lake/cloudflare-usage/history/{datetime.now(UTC).date().isoformat()}.json"
        ] = cf_usage

        # Insights engine output — one gold-ready file per team.
        teams = (await db.execute(select(Team))).scalars().all()
        for team in teams:
            try:
                insights = await build_team_insights(db, team.id)
                tables[f"lake/socialauto-insights/{team.id}.json"] = insights
            except Exception as exc:  # noqa: BLE001 — export others anyway
                tables[f"lake/socialauto-insights/{team.id}.json"] = {
                    "error": str(exc)[:300]
                }

    written = {}
    for key, rows in tables.items():
        try:
            res = await _put_json(key, rows, bucket)
            written[key] = res.get("size", 0)
        except Exception as exc:  # noqa: BLE001 — report, don't abort others
            written[key] = f"ERROR: {exc}"

    errors = {k: v for k, v in written.items() if isinstance(v, str)}
    return {
        "ok": not errors,
        "bucket": bucket,
        "files": len(written),
        "bytes": sum(v for v in written.values() if isinstance(v, int)),
        "errors": errors,
    }
