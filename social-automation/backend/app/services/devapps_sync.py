"""Developer-app ingest — pull live platform-app data into the lake.

The analytics sync (``analytics_sync.py``) already lands post-level and
follower-level snapshots; this module is the *developer-app* layer: for every
connected account it asks the platform's developer API for what the app
grant actually exposes — account/page insights, audience demographics,
token validity/scopes/expiry, and API quota/health state.

Each pull persists one ``AnalyticsEvent`` row (``event_type="devapp_snapshot"``,
``post_id=None``) so:

- the existing ``socialauto-account-events`` lake export already carries the
  raw snapshots downstream,
- ``datalake_export._export_devapps`` emits a per-channel
  ``lake/socialauto-devapps/snapshots.json`` rollup,
- ``datalake_insights.compute_devapp_signals`` turns health + audience data
  into report signals and per-channel actions for the generation loop.

Every puller is defensive: a platform failure degrades to
``{"ok": false, "error": ...}`` in the payload — one dead app never aborts
the sweep.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_token
from app.models.analytics import AnalyticsEvent
from app.models.social_account import SocialAccount

DEVAPP_EVENT_TYPE = "devapp_snapshot"
_DEVAPPS_HTTP_TIMEOUT = 20.0


def _sanitize(payload: dict[str, Any]) -> dict[str, Any]:
    """Ensure the event payload is JSONB-safe (dates, Decimals, bytes)."""
    return json.loads(json.dumps(payload, default=str))


def _base_payload(account: SocialAccount) -> dict[str, Any]:
    meta = account.meta_data or {}
    return {
        "platform": account.platform,
        "account_id": str(account.account_id),
        "username": account.username,
        "display_name": account.display_name,
        "account_type": account.account_type,
        "scopes": sorted(account.scopes or []),
        "token_expires_at": account.token_expires_at.isoformat() if account.token_expires_at else None,
        "has_refresh_token": bool(account.refresh_token_enc or meta.get("refresh_token")),
        "captured_at": datetime.now(UTC).isoformat(),
    }


def _err(base: dict[str, Any], exc: Exception) -> dict[str, Any]:
    base["ok"] = False
    base["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    return base


def _resp_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        data = {}
    return data if isinstance(data, dict) else {"value": data}


# ── Meta family (facebook / instagram / threads share the Graph API) ──────────


async def _meta_object_get(client: httpx.AsyncClient, path: str, token: str, fields: str) -> dict[str, Any]:
    from app.services.meta_graph import facebook_graph_url

    resp = await client.get(facebook_graph_url(path), params={"fields": fields, "access_token": token})
    return _resp_json(resp)


async def _meta_debug_token(client: httpx.AsyncClient, token: str) -> dict[str, Any]:
    """Graph debug_token — returns {is_valid, expires_at, data_access_expires_at, scopes}."""
    from app.services.meta_graph import facebook_graph_url

    resp = await client.get(
        facebook_graph_url("debug_token"),
        params={"input_token": token, "access_token": token},
    )
    data = _resp_json(resp)
    inner_raw = data.get("data")
    inner: dict[str, Any] = inner_raw if isinstance(inner_raw, dict) else data
    out: dict[str, Any] = {"token_valid": bool(inner.get("is_valid"))}
    for k in ("expires_at", "data_access_expires_at"):
        if inner.get(k):
            out[k] = inner[k]
    scopes = inner.get("scopes") or inner.get("granular_scopes")
    if isinstance(scopes, list):
        names = {s.get("scope") for s in scopes if isinstance(s, dict) and s.get("scope")}
        names |= {s for s in scopes if isinstance(s, str)}
        if names:
            out["debug_scopes"] = sorted(names)
    return out


async def _pull_facebook(db: AsyncSession, account: SocialAccount, token: str) -> dict[str, Any]:
    base = _base_payload(account)
    try:
        async with httpx.AsyncClient(timeout=_DEVAPPS_HTTP_TIMEOUT) as client:
            meta = account.meta_data or {}
            page_token = meta.get("page_token") or token
            health: dict[str, Any] = {}
            try:
                health = await _meta_debug_token(client, page_token)
            except Exception as exc:  # noqa: BLE001
                health = {"token_valid": False, "debug_error": str(exc)[:150]}

            insights: dict[str, Any] = {}
            try:
                obj = await _meta_object_get(client, str(account.account_id), page_token, "name,followers_count,fan_count,link")
                insights.update(
                    {
                        "page_name": obj.get("name"),
                        "followers": obj.get("followers_count") or obj.get("fan_count"),
                        "page_url": obj.get("link"),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                insights["object_error"] = str(exc)[:200]
            # Page insights only exist on Page objects — user-type accounts
            # have no /insights edge ("(#100) nonexisting field (insights)").
            if str(getattr(account, "account_type", "") or "") == "page":
                try:
                    from app.services.analytics_sync import _meta_insights_get
                    from app.services.meta_graph import facebook_graph_url

                    resp = await _meta_insights_get(
                        client,
                        facebook_graph_url(f"{account.account_id}/insights"),
                        ["page_views_total", "page_total_media_view_unique", "page_post_engagements", "page_daily_follows"],
                        params={"period": "day", "access_token": page_token},
                    )
                    if resp.status_code == 200:
                        rows = (_resp_json(resp)).get("data") or []
                        for row in rows:
                            name = row.get("name")
                            values = row.get("values") or []
                            if values:
                                insights[f"{name}_7d"] = values[-1].get("value") if len(values) >= 1 else None
                                if len(values) >= 7:
                                    insights[f"{name}_30d"] = sum(v.get("value") or 0 for v in values[-30:])
                except Exception as exc:  # noqa: BLE001
                    insights["insights_error"] = str(exc)[:200]

            base.update({"ok": True, "health": health, "insights": insights})
            return base
    except Exception as exc:  # noqa: BLE001
        return _err(base, exc)


async def _pull_instagram(db: AsyncSession, account: SocialAccount, token: str) -> dict[str, Any]:
    from app.services.meta_graph import facebook_graph_url

    base = _base_payload(account)
    try:
        async with httpx.AsyncClient(timeout=_DEVAPPS_HTTP_TIMEOUT) as client:
            health: dict[str, Any] = {}
            try:
                health = await _meta_debug_token(client, token)
            except Exception as exc:  # noqa: BLE001
                health = {"token_valid": False, "debug_error": str(exc)[:150]}

            insights: dict[str, Any] = {}
            demographics: dict[str, Any] = {}
            try:
                obj = await _meta_object_get(client, str(account.account_id), token, "username,followers_count,media_count,profile_picture_url")
                insights.update(
                    {
                        "username": obj.get("username"),
                        "followers": obj.get("followers_count"),
                        "media_count": obj.get("media_count"),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                insights["object_error"] = str(exc)[:200]
            try:
                from app.services.analytics_sync import _meta_insights_get

                resp = await _meta_insights_get(
                    client,
                    facebook_graph_url(f"{account.account_id}/insights"),
                    ["reach", "accounts_engaged", "total_interactions"],
                    params={"period": "day", "access_token": token},
                )
                if resp.status_code == 200:
                    rows = (_resp_json(resp)).get("data") or []
                    for row in rows:
                        values = row.get("values") or []
                        if values:
                            name = row.get("name")
                            insights[f"{name}_30d"] = sum(v.get("value") or 0 for v in values[-30:])
            except Exception as exc:  # noqa: BLE001
                insights["insights_error"] = str(exc)[:200]
            try:
                # Audience demographics — follower_demographics (v22+ business accounts).
                resp = await client.get(
                    facebook_graph_url(f"{account.account_id}/insights"),
                    params={
                        "metric": "follower_demographics",
                        "period": "lifetime",
                        "metric_type": "total_value",
                        "access_token": token,
                    },
                )
                if resp.status_code == 200:
                    demographics = _resp_json(resp)
            except Exception:  # noqa: BLE001
                pass

            base.update({"ok": True, "health": health, "insights": insights, "demographics": demographics})
            return base
    except Exception as exc:  # noqa: BLE001
        return _err(base, exc)


async def _pull_threads(db: AsyncSession, account: SocialAccount, token: str) -> dict[str, Any]:
    base = _base_payload(account)
    try:
        from app.services.threads_api import ThreadsAPIClient

        client = ThreadsAPIClient(token, user_id=account.account_id)
        insights: dict[str, Any] = {}
        health: dict[str, Any] = {}
        try:
            health = await client.validate_token() or {}
        except Exception as exc:  # noqa: BLE001
            health = {"token_valid": False, "error": str(exc)[:200]}
        try:
            profile = await client.get_profile()
            insights["profile"] = profile
        except Exception as exc:  # noqa: BLE001
            insights["profile_error"] = str(exc)[:200]
        for metric in ("views", "likes", "replies"):
            try:
                data = await client.get_insights(metric=metric)
                if isinstance(data, dict):
                    insights[f"insights_{metric}"] = data
            except Exception:  # noqa: BLE001
                pass
        base.update({"ok": True, "health": health, "insights": insights})
        return base
    except Exception as exc:  # noqa: BLE001
        return _err(base, exc)


# ── LinkedIn ──────────────────────────────────────────────────────────────────


async def _pull_linkedin(db: AsyncSession, account: SocialAccount, token: str) -> dict[str, Any]:
    base = _base_payload(account)
    try:
        from app.services.analytics_sync import _org_urn
        from app.services.linkedin_api import LinkedInAPIClient

        client = LinkedInAPIClient(access_token=token)
        insights: dict[str, Any] = {}
        health: dict[str, Any] = {"scopes": sorted(account.scopes or [])}
        try:
            userinfo = await client.validate_token()
            health["token_valid"] = True
            health["userinfo"] = userinfo
        except Exception as exc:  # noqa: BLE001
            health["token_valid"] = False
            health["error"] = str(exc)[:200]
        # Org-level pull only for organization accounts with an org URN.
        meta = account.meta_data or {}
        is_org = account.account_type == "organization" or str(meta.get("author_urn") or "").startswith("urn:li:organization:")
        if is_org:
            try:
                org_urn = _org_urn(account)
                followers = await client.get_follower_count(org_urn)
                insights["followers"] = followers
            except Exception as exc:  # noqa: BLE001
                insights["followers_error"] = str(exc)[:200]
            try:
                org = await client.get_organization(org_urn.rsplit(":", 1)[-1])
                insights["organization"] = org
            except Exception as exc:  # noqa: BLE001
                insights["organization_error"] = str(exc)[:200]
            try:
                stats = await client.get_organization_lifetime_stats(_org_urn(account))
                insights["lifetime_stats"] = stats
            except Exception as exc:  # noqa: BLE001
                insights["stats_error"] = str(exc)[:200]
        else:
            # Member accounts: token health only — member post analytics need
            # restricted scopes (see socialauto-analytics skill truth table).
            insights["note"] = "member account — org stats not applicable"
        base.update({"ok": True, "health": health, "insights": insights})
        return base
    except Exception as exc:  # noqa: BLE001
        return _err(base, exc)


# ── X / Twitter ───────────────────────────────────────────────────────────────


async def _pull_twitter(db: AsyncSession, account: SocialAccount, token: str) -> dict[str, Any]:
    base = _base_payload(account)
    try:
        from app.services.twitter_api import TwitterAPIClient

        client = TwitterAPIClient(access_token=token)
        insights: dict[str, Any] = {}
        health: dict[str, Any] = {}
        try:
            health = await client.validate_token() or {}
        except Exception as exc:  # noqa: BLE001
            health = {"token_valid": False, "error": str(exc)[:200]}
        try:
            metrics = await client.get_me_metrics()
            insights["me_metrics"] = metrics
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)[:200]
            insights["me_metrics_error"] = msg
            insights["quota_exhausted"] = "402" in msg or "quota" in msg.lower()
        base.update({"ok": True, "health": health, "insights": insights})
        return base
    except Exception as exc:  # noqa: BLE001
        return _err(base, exc)


# ── TikTok ────────────────────────────────────────────────────────────────────


async def _pull_tiktok(db: AsyncSession, account: SocialAccount, token: str) -> dict[str, Any]:
    base = _base_payload(account)
    try:
        from app.services.tiktok_api import TikTokAPIClient

        client = TikTokAPIClient(token, open_id=account.account_id)
        insights: dict[str, Any] = {}
        health: dict[str, Any] = {}
        try:
            health = await client.validate_token() or {}
        except Exception as exc:  # noqa: BLE001
            health = {"token_valid": False, "error": str(exc)[:200]}
        try:
            creator = await client.get_creator_info()
            insights["creator_info"] = creator
        except Exception as exc:  # noqa: BLE001
            insights["creator_error"] = str(exc)[:200]
        try:
            stats = await client.get_stats()
            insights["stats"] = stats
        except Exception as exc:  # noqa: BLE001
            insights["stats_error"] = str(exc)[:200]
        base.update({"ok": True, "health": health, "insights": insights})
        return base
    except Exception as exc:  # noqa: BLE001
        return _err(base, exc)


# ── Telegram ──────────────────────────────────────────────────────────────────


async def _pull_telegram(db: AsyncSession, account: SocialAccount) -> dict[str, Any]:
    base = _base_payload(account)
    try:
        from app.services.telegram_api import TelegramAPIClient

        meta = account.meta_data or {}
        enc = meta.get("bot_token_enc")
        raw = enc if isinstance(enc, bytes | bytearray) else str(enc or "").encode()
        bot_token = decrypt_token(raw) if raw else decrypt_token(account.access_token_enc)
        client = TelegramAPIClient(bot_token)
        insights: dict[str, Any] = {}
        health: dict[str, Any] = {}
        try:
            me = await client.get_me()
            health["token_valid"] = True
            health["bot"] = me
        except Exception as exc:  # noqa: BLE001
            health["token_valid"] = False
            health["error"] = str(exc)[:200]
        try:
            health["webhook"] = await client.get_webhook_info()
        except Exception:  # noqa: BLE001
            pass
        managed = meta.get("telegram_channel") or {}
        chat_id = managed.get("chat_id")
        if chat_id:
            try:
                chat = await client.get_chat(chat_id)
                insights["channel"] = chat
            except Exception as exc:  # noqa: BLE001
                insights["channel_error"] = str(exc)[:200]
        base.update({"ok": True, "health": health, "insights": insights})
        return base
    except Exception as exc:  # noqa: BLE001
        return _err(base, exc)


# ── Bluesky ───────────────────────────────────────────────────────────────────


async def _pull_bluesky(db: AsyncSession, account: SocialAccount, token: str) -> dict[str, Any]:
    base = _base_payload(account)
    try:
        from app.services.bluesky_api import BlueskyClient

        pds = (account.meta_data or {}).get("pds_url") or "https://bsky.social"
        client = BlueskyClient(account.username or "", token, pds_url=pds)
        from app.services.bluesky_api import ensure_session, persist_session

        await ensure_session(client, (account.meta_data or {}).get("bsky_session"))
        persist_session(account, client)
        profile = await client.get_profile()
        insights: dict[str, Any] = {"profile": profile}
        health: dict[str, Any] = {
            "token_valid": True,
            "did": profile.get("did") or getattr(client, "did", None),
            "handle": profile.get("handle"),
        }
        base.update({"ok": True, "health": health, "insights": insights})
        return base
    except Exception as exc:  # noqa: BLE001
        return _err(base, exc)


# ── WhatsApp Cloud ────────────────────────────────────────────────────────────


async def _pull_whatsapp(db: AsyncSession, account: SocialAccount, token: str) -> dict[str, Any]:
    base = _base_payload(account)
    try:
        from app.services.whatsapp_api import WhatsAppAPIClient

        client = WhatsAppAPIClient(access_token=token, phone_number_id=account.account_id)
        insights: dict[str, Any] = {}
        health: dict[str, Any] = {}
        try:
            info = await client.get_phone_number_info_by_id(account.account_id)
            insights["phone_number"] = info
            health["token_valid"] = True
        except Exception as exc:  # noqa: BLE001
            health["token_valid"] = False
            health["error"] = str(exc)[:200]
        base.update({"ok": True, "health": health, "insights": insights})
        return base
    except Exception as exc:  # noqa: BLE001
        return _err(base, exc)


# ── Viber ─────────────────────────────────────────────────────────────────────


async def _pull_viber(db: AsyncSession, account: SocialAccount, token: str) -> dict[str, Any]:
    base = _base_payload(account)
    try:
        from app.services.viber_api import ViberAPIClient

        client = ViberAPIClient(auth_token=token)
        try:
            info = await client.get_account_info()
            base.update({"ok": True, "health": {"token_valid": True}, "insights": {"account_info": info}})
        except Exception as exc:  # noqa: BLE001
            base.update({"ok": False, "health": {"token_valid": False}, "error": str(exc)[:200]})
        return base
    except Exception as exc:  # noqa: BLE001
        return _err(base, exc)


# ── Dispatcher ────────────────────────────────────────────────────────────────

_Puller = Callable[..., Awaitable[dict[str, Any]]]

_PULLERS: dict[str, _Puller] = {
    "facebook": _pull_facebook,
    "instagram": _pull_instagram,
    "threads": _pull_threads,
    "linkedin": _pull_linkedin,
    "twitter": _pull_twitter,
    "tiktok": _pull_tiktok,
    "telegram": _pull_telegram,
    "bluesky": _pull_bluesky,
    "whatsapp": _pull_whatsapp,
    "viber": _pull_viber,
}


async def sync_devapp_account(db: AsyncSession, account: SocialAccount) -> dict[str, Any]:
    """Pull one account's developer-app surface and persist the snapshot."""
    puller = _PULLERS.get((account.platform or "").lower())
    payload: dict[str, Any]
    if puller is None:
        base = _base_payload(account)
        base["ok"] = True
        base["insights"] = {"note": "no devapp puller for platform"}
        payload = base
    else:
        try:
            token = decrypt_token(account.access_token_enc)
            if (account.platform or "").lower() == "telegram":
                payload = await puller(db, account)
            else:
                payload = await puller(db, account, token)
        except Exception as exc:  # noqa: BLE001
            payload = _err(_base_payload(account), exc)
    payload = _sanitize(payload)
    db.add(
        AnalyticsEvent(
            team_id=account.team_id,
            social_account_id=account.id,
            platform=account.platform,
            event_type=DEVAPP_EVENT_TYPE,
            occurred_at=datetime.now(UTC),
            meta_data=payload,
        )
    )
    await db.flush()
    return payload


async def sync_all_devapps(db: AsyncSession, *, platform: str | None = None) -> dict[str, Any]:
    """Sweep every active connected account through its developer-app pull."""
    q = select(SocialAccount).where(SocialAccount.status == "active")
    if platform:
        q = q.where(SocialAccount.platform == platform)
    accounts = (await db.execute(q)).scalars().all()
    results: list[dict[str, Any]] = []
    for account in accounts:
        try:
            results.append(await sync_devapp_account(db, account))
        except Exception as exc:  # noqa: BLE001 — never abort the sweep
            results.append(
                _sanitize(
                    _err(
                        _base_payload(account),
                        exc,
                    )
                )
            )
    await db.commit()
    ok = sum(1 for r in results if r.get("ok"))
    return {"accounts": len(results), "ok": ok, "failed": len(results) - ok, "results": results}


# ── Channel actions — the per-channel hand-off to post generation ─────────────
#
# Each active account yields ONE action row: the strongest evidence-backed
# thing the channel should do next. Pure function — the lake export and the
# insights sweep both consume it.


def _num(obj: dict[str, Any], *keys: str) -> float | None:
    for k in keys:
        v = obj.get(k)
        if isinstance(v, int | float) and not isinstance(v, bool):
            return float(v)
        if isinstance(v, dict):
            found = _num(v, *keys[1:])
            if found is not None:
                return found
    return None


def _followers(payload: dict[str, Any]) -> float | None:
    ins = payload.get("insights") or {}
    for container in (ins, ins.get("profile") or {}, ins.get("organization") or {}):
        if not isinstance(container, dict):
            continue
        for key in ("followers", "followers_count", "follower_count", "firstDegreeSize"):
            v = container.get(key)
            if isinstance(v, int | float):
                return float(v)
    creator = ins.get("creator_info") or {}
    if isinstance(creator, dict):
        for key in ("follower_count", "followers", "follower_count"):
            v = creator.get(key)
            if isinstance(v, int | float):
                return float(v)
    return None


def _days_to_expiry(payload: dict[str, Any], now: datetime) -> float | None:
    raw = (payload.get("health") or {}).get("expires_at") or payload.get("token_expires_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (dt - now).total_seconds() / 86400.0
    except ValueError:
        return None


def _health_action(payload: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    """Evidence-backed health action for one channel, or None when healthy."""
    health = payload.get("health") or {}
    if health.get("token_valid") is False or payload.get("ok") is False:
        return {
            "type": "channel_health",
            "priority": "high",
            "action": "heal_session",
            "reason": payload.get("error") or health.get("error") or "devapp pull failed",
        }
    days = _days_to_expiry(payload, now)
    if days is not None:
        if days <= 0:
            return {
                "type": "channel_health",
                "priority": "high",
                "action": "reauthorize",
                "reason": f"token expired {abs(days):.0f}d ago",
            }
        if days <= 3:
            return {
                "type": "channel_health",
                "priority": "medium",
                "action": "refresh_token",
                "reason": f"token expires in {days:.1f}d",
            }
    if (payload.get("insights") or {}).get("quota_exhausted"):
        return {
            "type": "channel_health",
            "priority": "medium",
            "action": "wait_for_quota_reset",
            "reason": "platform API quota exhausted",
        }
    return None


def _content_hint(payload: dict[str, Any]) -> dict[str, Any]:
    """Lightweight per-channel generation hint from the app-level pull."""
    platform = payload.get("platform")
    followers = _followers(payload)
    ins = payload.get("insights") or {}
    media_ok = platform not in ("tiktok",)  # TikTok requires video
    hint: dict[str, Any] = {
        "type": "channel_action",
        "platform": platform,
        "username": payload.get("username"),
        "followers": followers,
        "media_required": platform in ("instagram", "tiktok"),
        "note": "text-first" if not media_ok else "media-first",
    }
    # Surface the freshest engagement counters the app returned.
    for key in (
        "reach_30d",
        "page_views_total_30d",
        "page_total_media_view_unique_30d",
        "page_post_engagements_30d",
        "page_impressions_unique_30d",
        "accounts_engaged_30d",
        "total_interactions_30d",
    ):
        v = ins.get(key)
        if isinstance(v, int | float):
            hint[key] = v
    return hint


def build_channel_actions(payloads: list[dict[str, Any]], *, now: datetime | None = None) -> list[dict[str, Any]]:
    """One action row per account: health override wins, else content hint."""
    now = now or datetime.now(UTC)
    out: list[dict[str, Any]] = []
    for p in payloads:
        if not isinstance(p, dict):
            continue
        health = _health_action(p, now)
        if health:
            out.append(
                {
                    **health,
                    "platform": p.get("platform"),
                    "account_id": p.get("account_id"),
                    "account_type": p.get("account_type"),
                    "username": p.get("username"),
                    "captured_at": p.get("captured_at"),
                }
            )
        else:
            hint = _content_hint(p)
            hint["account_type"] = p.get("account_type")
            hint["captured_at"] = p.get("captured_at")
            out.append(hint)
    return out


# ── Report → publish hand-off ────────────────────────────────────────────────
#
# ``channel_action`` rows become MonetizationAction instructions for
# ``monetization_executor.schedule_report_actions`` — the same production
# chain the strategy brief and monetization report use (copy → quality
# pipeline → media → brand compliance → schedule/draft). Health overrides
# never reach this stage; the devapp_health lane owns them.


def _channel_instruction(action: dict[str, Any]) -> str:
    platform = action.get("platform") or "this channel"
    username = action.get("username")
    handle = f"@{username}" if username else "the connected account"
    followers = action.get("followers")
    audience = f"{int(followers)} followers" if isinstance(followers, int | float) else "its current audience"
    corporate = "company page" if action.get("account_type") in ("organization", "page") else ""
    return (
        f"Channel pulse for {platform} {handle}{f' ({corporate})' if corporate else ''}: "
        f"share one concrete Cloudless engineering outcome or real cost-saving result "
        f"tailored to {audience}."
    )


def monetization_actions_from_channels(
    channel_actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """channel_action rows → executor-ready action dicts.

    Returns ``{"platform", "instruction", "format"}`` dicts; the worker task
    wraps them in ``MonetizationAction``. Health rows and non-dict entries
    are dropped — only healthy channels generate posts.
    """
    out: list[dict[str, Any]] = []
    for a in channel_actions:
        if not isinstance(a, dict) or a.get("type") != "channel_action":
            continue
        platform = a.get("platform")
        if not platform:
            continue
        out.append(
            {
                "platform": platform,
                "instruction": _channel_instruction(a),
                "format": "image" if a.get("media_required") else "",
            }
        )
    return out
