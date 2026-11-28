"""Daily LinkedIn data-portability sync — Member DP + Pages DP exports.

Writes ``linkedin_portability_member`` / ``linkedin_portability_pages`` /
``linkedin_portability_changelog`` AnalyticsEvents so the snapshots flow
into the datalake via ``export-datalake`` like every other devapp pull.

Fully self-gating on two levels:

1. **Scope pre-check** — ``account.scopes`` is authoritative; while the
   recorded grant lacks ``r_dma_portability_self_serve`` /
   ``r_dma_admin_pages_content`` the task skips without any API call.
2. **Live 401/403** — if scopes were granted but the product approval is
   still pending, LinkedIn returns 403 → ``pending-grant`` result, no
   alerts, next run retries.

Member DP is pinned to ``Linkedin-Version: 202312`` and Pages DP to
``202604`` inside ``app.services.linkedin_portability``.

Privacy note: member snapshots include the ``CONNECTIONS`` domain, which
carries third-party LinkedIn member PII (names/positions of the member's
connections). Retention decision (2026-11-15): this is the member's own
LinkedIn data-portability export stored in our own Postgres
(``analytics_events``) and mirrored wholesale to the datalake by
``export-datalake`` — both retain indefinitely, volume is bounded at
``MAX_ELEMENTS_STORE`` elements per domain per daily run. Revisit with a
purge/redaction lane only if lake size or a privacy review requires it.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select

from app.core.security import decrypt_token
from app.models.analytics import AnalyticsEvent
from app.models.social_account import SocialAccount
from app.services import linkedin_portability as lp
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

CHANGELOG_CURSOR_KEY = "linkedin:dma:changelog-cursor"
MAX_ELEMENTS_STORE = 200  # cap elements persisted per domain in meta_data
PAGES_HYDRATE_POSTS = 10  # dmaPosts batch-get size per run
ORG_METRIC_TYPES = "List(IMPRESSIONS,UNIQUE_IMPRESSIONS,CLICKS,COMMENTS,REACTIONS,REPOSTS,ENGAGEMENT_RATE,CTR)"
ORG_ANALYTICS_DAYS = 30

MEMBER_SCOPE = "r_dma_portability_self_serve"
PAGES_SCOPE = "r_dma_admin_pages_content"
MEMBER_DP_SECRET_KEY = "LINKEDIN_MEMBER_DP_TOKEN"


def _scopes(account: SocialAccount) -> set[str]:
    return set(account.scopes or [])


def _has_scope(account: SocialAccount, scope: str) -> bool:
    """Empty scope list = legacy account → probe anyway; recorded list is truth."""
    granted = _scopes(account)
    return not granted or scope in granted


def _redis():
    try:
        import redis

        from app.core.config import get_settings

        return redis.Redis.from_url(get_settings().REDIS_URL, decode_responses=True)
    except Exception:  # noqa: BLE001
        return None


def _org_ids(account: SocialAccount) -> tuple[str, str]:
    """(organization urn for postsByAuthor, organizationalPage urn for analytics)."""
    meta = account.meta_data or {}
    author = str(meta.get("author_urn") or "")
    if author.startswith("urn:li:organization:"):
        org_urn = author
    else:
        org_urn = f"urn:li:organization:{account.account_id}"
    org_id = org_urn.rsplit(":", 1)[-1]
    return org_urn, f"urn:li:organizationalPage:{org_id}"


def _event(
    account: SocialAccount,
    event_type: str,
    payload: dict[str, Any],
) -> AnalyticsEvent:
    return AnalyticsEvent(
        team_id=account.team_id,
        social_account_id=account.id,
        platform="linkedin",
        event_type=event_type,
        occurred_at=datetime.now(UTC),
        meta_data=payload,
    )


async def _member_dp_secret() -> str | None:
    """Raw ``LINKEDIN_MEMBER_DP_TOKEN`` store value, fetched once per sweep."""
    try:
        from app.services.secret_store import secret_store

        value = await secret_store.get(MEMBER_DP_SECRET_KEY)
    except Exception as exc:  # noqa: BLE001 — secret store outage must not break the sweep
        logger.warning("member-dp secret read failed (%s)", type(exc).__name__)
        return None
    return str(value) if value else None


def _parse_member_dp_secret(raw: str) -> tuple[str, str | None]:
    """Split the secret value into ``(token, pinned account_id)``.

    A bare token is unpinned — it applies to the selected person account
    (single-tenant assumption). JSON ``{"token": ..., "account_id": ...}``
    pins the grant to one ``social_accounts.account_id`` so member-DP data
    is always attributed to the LinkedIn identity the token belongs to.
    """
    try:
        data = json.loads(raw)
    except ValueError:
        return raw, None
    if isinstance(data, dict) and data.get("token"):
        pin = data.get("account_id")
        return str(data["token"]), str(pin) if pin else None
    return raw, None


async def _member_dp(
    db,
    person: SocialAccount,
    client: httpx.AsyncClient,
    token: str,
) -> dict[str, Any]:
    summary: dict[str, Any] = {"domains": {}, "truncated_domains": []}
    denied = False
    for domain in lp.MEMBER_DOMAINS:
        res = await lp.member_snapshot_domain(client, token, domain)
        if not res.get("ok"):
            if res.get("not_granted") or res.get("status") in (401, 403):
                denied = True
            summary["domains"][domain] = {"error": res.get("status") or res.get("error")}
            if denied:
                break
            continue
        elements = res.get("elements") or []
        stored = elements[:MAX_ELEMENTS_STORE]
        summary["domains"][domain] = {
            "count": len(elements),
            "total": res.get("total"),
            "pages": res.get("pages"),
            "elements": stored,
        }
        if len(elements) > MAX_ELEMENTS_STORE or (res.get("total") or 0) > MAX_ELEMENTS_STORE:
            summary["truncated_domains"].append(domain)
    if denied:
        return {"pending-grant": "member-dp", "domains_done": len(summary["domains"])}

    # Diff alert — LinkedIn populates the export asynchronously; when a
    # domain's payload first appears or materially changes, surface it
    # instead of letting the data sit silently in analytics_events.
    r = _redis()
    changed_domains: list[str] = []
    if r is not None:
        import hashlib

        for domain, info in summary["domains"].items():
            if "elements" not in info:
                continue
            digest = hashlib.sha256(json.dumps(info["elements"], sort_keys=True, default=str).encode()).hexdigest()[:16]
            key = f"linkedin:dma:hash:{domain}"
            prev = r.get(key)
            if prev is None:
                r.set(key, digest)  # baseline — first sighting is not an alert
            elif prev != digest:
                r.set(key, digest)
                changed_domains.append(domain)
        if changed_domains:
            try:
                from app.services.slack_notifications import send_telegram_alert

                await send_telegram_alert(
                    "ops",
                    "LinkedIn Member DP export changed: " + ", ".join(changed_domains[:10]),
                )
            except Exception:  # noqa: BLE001 — alerting is best-effort
                logger.debug("member-dp diff alert failed", exc_info=True)

    # Changelog — incremental via Redis cursor (latest processedAt).
    _raw_cursor = r.get(CHANGELOG_CURSOR_KEY) if r else None
    cursor = _raw_cursor if isinstance(_raw_cursor, str) else None
    cl = await lp.member_changelog(client, token, start_time=cursor, count=50)
    changelog_payload: dict[str, Any] = {"cursor_in": cursor}
    if cl.get("ok"):
        data = cl.get("data") or {}
        events = data.get("elements") or []
        changelog_payload["events"] = events[:MAX_ELEMENTS_STORE]
        changelog_payload["count"] = len(events)
        newest = max(
            (str(e.get("processedAt") or "") for e in events if isinstance(e, dict)),
            default=None,
        )
        if newest and r:
            r.set(CHANGELOG_CURSOR_KEY, newest)
            changelog_payload["cursor_out"] = newest
    else:
        changelog_payload["error"] = cl.get("status") or cl.get("error")
        if cl.get("not_granted"):
            return {"pending-grant": "member-dp"}

    db.add(_event(person, "linkedin_portability_member", {"captured_at": datetime.now(UTC).isoformat(), **summary}))
    db.add(_event(person, "linkedin_portability_changelog", {"captured_at": datetime.now(UTC).isoformat(), **changelog_payload}))
    return {"domains": len(summary["domains"]), "truncated": summary["truncated_domains"]}


async def _pages_dp(db, account: SocialAccount, client: httpx.AsyncClient) -> dict[str, Any]:
    """Pages DP export for the Company Page — person token preferred (3-legged member)."""
    org_urn, page_urn = _org_ids(account)
    token = decrypt_token(bytes(account.access_token_enc))
    payload: dict[str, Any] = {"captured_at": datetime.now(UTC).isoformat(), "org_urn": org_urn}

    orgs = await lp.pages_organizations(client, token, parent_org_id=org_urn.rsplit(":", 1)[-1])
    if not orgs.get("ok"):
        if orgs.get("not_granted"):
            return {"pending-grant": "pages-dp"}
        payload["organizations_error"] = orgs.get("status") or orgs.get("error")
    else:
        payload["organizations"] = (orgs.get("data") or {}).get("elements") or []

    # Feed listing — 1 call/run (1 req/60s finder limit).
    feed = await lp.pages_feed_page(client, token, org_urn)
    if not feed.get("ok"):
        if feed.get("not_granted"):
            return {"pending-grant": "pages-dp"}
        payload["feed_error"] = feed.get("status") or feed.get("error")
    else:
        data = feed.get("data") or {}
        elements = data.get("elements") or []
        urns = [el.get("id") or el.get("postUrn") or el.get("urn") for el in elements if isinstance(el, dict)]
        urns = [u for u in urns if isinstance(u, str) and u.startswith("urn:")]
        meta = data.get("metadata") or {}
        payload["feed"] = {
            "count": len(urns),
            "newest_cursor": meta.get("nextPaginationCursor"),
            "sample_urns": urns[:PAGES_HYDRATE_POSTS],
        }
        # Hydrate the newest posts + their social metadata (batch endpoints).
        if urns[:PAGES_HYDRATE_POSTS]:
            posts = await lp.pages_batch_get(client, token, "/dmaPosts", urns[:PAGES_HYDRATE_POSTS])
            payload["posts"] = posts[:PAGES_HYDRATE_POSTS]
            meta_counts = await lp.pages_batch_get(client, token, "/dmaSocialMetadata", urns[:PAGES_HYDRATE_POSTS])
            payload["social_metadata"] = meta_counts[:PAGES_HYDRATE_POSTS]

    # Org-level content analytics — last ORG_ANALYTICS_DAYS, single window.
    end_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = int((datetime.now(UTC) - timedelta(days=ORG_ANALYTICS_DAYS)).timestamp() * 1000)
    trend = await lp.pages_content_trend(
        client,
        token,
        source_entity=page_urn,
        metric_types=ORG_METRIC_TYPES,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    if trend.get("ok"):
        payload["analytics_trend"] = (trend.get("data") or {}).get("elements") or []
    else:
        payload["analytics_error"] = trend.get("status") or trend.get("error")

    db.add(_event(account, "linkedin_portability_pages", payload))
    return {"posts_seen": (payload.get("feed") or {}).get("count", 0)}


async def _run() -> dict[str, Any]:
    async with task_session() as db:
        accounts = (
            (
                await db.execute(
                    select(SocialAccount).where(
                        SocialAccount.platform == "linkedin",
                        SocialAccount.status == "active",
                    )
                )
            )
            .scalars()
            .all()
        )
        persons = [a for a in accounts if a.account_type == "person"]
        person = persons[0] if persons else None
        org = next((a for a in accounts if a.account_type == "organization"), None)
        if not person and not org:
            return {"skip": "no-linkedin-accounts"}

        result: dict[str, Any] = {}
        async with httpx.AsyncClient(timeout=60) as client:
            # Member DP — member's own data. Token order: dedicated
            # LINKEDIN_MEMBER_DP_TOKEN secret (app 265100977 grant; satisfies
            # the scope gate even when the account token lacks the scope)
            # → person.access_token_enc fallback.
            member_secret_raw = await _member_dp_secret()
            member_secret, member_pin = _parse_member_dp_secret(member_secret_raw) if member_secret_raw else (None, None)
            member_person = person
            if member_pin:
                pinned = next((a for a in persons if a.account_id == member_pin), None)
                if pinned is not None:
                    member_person = pinned
                else:
                    logger.warning("member-dp secret pin matches no linkedin person account")
                    member_secret = None
            if member_person is not None and (_has_scope(member_person, MEMBER_SCOPE) or member_secret is not None):
                try:
                    token = member_secret or decrypt_token(bytes(member_person.access_token_enc))
                    result["member_dp"] = await _member_dp(db, member_person, client, token)
                except Exception as exc:  # noqa: BLE001 — never abort the sweep
                    result["member_dp"] = {"error": f"{type(exc).__name__}: {exc}"}
                # The dedicated token has ~1yr TTL and no refresh — a
                # pending-grant while it is configured means EXPIRED, not
                # awaiting approval. Alert (send_telegram_alert dedups 6h).
                if member_secret is not None and isinstance(result["member_dp"], dict) and "pending-grant" in result["member_dp"]:
                    try:
                        from app.services.slack_notifications import send_telegram_alert

                        await send_telegram_alert(
                            "ops",
                            "LinkedIn Member DP rejected (401/403) — LINKEDIN_MEMBER_DP_TOKEN "
                            "likely expired (~1yr TTL, no refresh). Re-auth app 265100977 with "
                            "r_dma_portability_self_serve and update the secret.",
                        )
                    except Exception:  # noqa: BLE001
                        logger.debug("member-dp expiry alert failed", exc_info=True)
            else:
                result["member_dp"] = {"skip": "scope-not-granted"}

            # Pages DP — needs an admin member token; person first, org fallback.
            pages_account = None
            for cand in (person, org):
                if cand and _has_scope(cand, PAGES_SCOPE):
                    pages_account = cand
                    break
            if pages_account:
                try:
                    result["pages_dp"] = await _pages_dp(db, pages_account, client)
                except Exception as exc:  # noqa: BLE001
                    result["pages_dp"] = {"error": f"{type(exc).__name__}: {exc}"}
            else:
                result["pages_dp"] = {"skip": "scope-not-granted"}

        await db.commit()
        return result


@celery_app.task(name="app.worker.tasks.linkedin_portability_sync.sync_linkedin_portability")
def sync_linkedin_portability() -> dict[str, Any]:
    return run_async(_run())
