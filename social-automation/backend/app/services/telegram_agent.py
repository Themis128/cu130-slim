"""Telegram channel agent — fully self-hosted channel autopilot.

Owns the managed channel's lifecycle inside SocialAuto (supersedes the n8n
``cloudless-telegram-channel`` workflow):

- **Plan** — pillar rotation ported from the n8n planner:
  Mon authority · Wed engagement · Fri offer/authority/proof (~11% offers)
  plus a Sunday native poll.
- **Create** — copy via ``call_inference`` (DMR, brand-context) through the
  ≥90 SEO quality pipeline; media via CF FLUX schnell → ``persist_generated_image``
  (brand footer + media library).
- **Publish** — photo + inline CTA buttons / poll via TelegramAPIClient,
  ``channel_post`` analytics event for the funnel.
- **Engage** — daily-throttled welcome post on ``member_joined`` (called from
  the webhook), join-request auto-approve, weekly poll.
- **Adapt** — pillars re-ranked by joins-per-post from ``analytics_events``
  (24h post-publish attribution window); exploration floor keeps rotation alive.

Config lives in ``account.meta_data["telegram_agent"]`` — the agent runs only
when a managed channel is configured; set ``enabled: false`` to pause.
"""

from __future__ import annotations

import base64
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import AnalyticsEvent
from app.models.social_account import SocialAccount
from app.services.inference import call_inference
from app.services.telegram_api import (
    TelegramAPIClient,
    TelegramAPIError,
    inline_keyboard,
)

logger = logging.getLogger(__name__)

AGENT_META_KEY = "telegram_agent"

# ── content plan (ported from n8n-workflows/cloudless-telegram-channel.json) ──

OFFERS = [
    {
        "key": "audit",
        "prompt": (
            "Write an offer post for the Cloudless Telegram channel: invite "
            "readers to a free 30-minute cloud & automation audit. Explain in "
            "one line what they get (a concrete map of what to automate first "
            "+ honest cost savings estimate). No pressure language, no fake "
            "scarcity — it is a real free service."
        ),
        "image_prompt": (
            "minimalist dark tech illustration, a magnifying glass over a "
            "cloud infrastructure diagram, cyan and magenta accents, "
            "professional, no text"
        ),
        "buttons": [[{"text": "Book the free audit", "url": "https://cloudless.gr/contact"}]],
        "tag": "[Deal]",
    },
    {
        "key": "plans",
        "prompt": (
            "Write an offer post for the Cloudless Telegram channel: our "
            "automation plans are live — self-hosted social automation that "
            "runs while you sleep. One line on what a plan includes, honest "
            "pricing link, no hype."
        ),
        "image_prompt": (
            "minimalist dark tech illustration, a rocket leaving a cloud "
            "platform, cyan and magenta accents, professional, no text"
        ),
        "buttons": [[{"text": "See plans & pricing", "url": "https://social.cloudless.gr/pricing"}]],
        "tag": "[Deal]",
    },
    {
        "key": "checklist",
        "prompt": (
            "Write an offer post for the Cloudless Telegram channel: give away "
            "our 12-Automation Checklist PDF — the exact checklist we use to "
            "decide what to automate in a small business. Free download, "
            "explain the one insight it gives you."
        ),
        "image_prompt": (
            "minimalist dark tech illustration, a checklist document floating "
            "over clouds, cyan and magenta accents, professional, no text"
        ),
        # The real lead-magnet PDF goes out as a native document post.
        "document": "https://cloudless.gr/automation-checklist.pdf",
        "buttons": [[{"text": "Download the checklist", "url": "https://cloudless.gr/automation-checklist.pdf"}]],
        "tag": "[Deal]",
    },
]

PILLARS: dict[str, dict[str, Any]] = {
    "authority": {
        "prompt": (
            "Write an authority post for the Cloudless Telegram channel: one "
            "practical automation or self-hosting insight for small-business "
            "owners, drawn from real cloudless.gr engineering (self-hosted "
            "stack, Cloudflare workers, n8n pipelines, local AI inference). "
            "Teach ONE thing fully. No invented metrics."
        ),
        "image_prompt": (
            "minimalist dark tech illustration, glowing server racks and "
            "automation gears inside a cloud, cyan and magenta accents, "
            "professional, no text"
        ),
        "tag": "[Playbook]",
        "buttons": None,
    },
    "engagement": {
        "prompt": (
            "Write an engagement post for the Cloudless Telegram channel: ask "
            "the audience ONE opinion question about their own automation or "
            "hosting setup (e.g. what they still do manually that a machine "
            "should handle). Two lines of context max, then the question, "
            "then a reaction CTA."
        ),
        "image_prompt": (
            "minimalist dark tech illustration, two chat bubbles over a "
            "circuit board, cyan and magenta accents, professional, no text"
        ),
        "tag": "[Question]",
        "buttons": None,
    },
    "proof": {
        "prompt": (
            "Write a proof post for the Cloudless Telegram channel: share ONE "
            "real result from the cloudless.gr self-hosted stack — use only "
            "verifiable facts (runs on a Raspberry Pi cluster, zero SaaS "
            "subscriptions, self-hosted Nextcloud + mail + social "
            "automation). Concrete and humble, never invented numbers."
        ),
        "image_prompt": (
            "minimalist dark tech illustration, a small raspberry pi board "
            "powering a giant cloud, cyan and magenta accents, professional, "
            "no text"
        ),
        "tag": "[Build Log]",
        "buttons": None,
    },
}

# Sunday polls — deterministic rotation (engagement driver + content research).
POLLS = [
    ("What's the most annoying thing you still do manually every week?", ["Invoicing / admin", "Social posting", "Backups", "Reports"]),
    ("Where would you automate first?", ["Email triage", "Content calendar", "Monitoring/alerts", "Client onboarding"]),
    ("What stops you from self-hosting more?", ["Time to set up", "Fear of breakage", "Hardware cost", "Nothing — already self-hosting"]),
    ("Which automation would pay for itself fastest?", ["Social media", "Lead follow-up", "Server maintenance", "Reporting"]),
    ("What do you pay for monthly that a script could replace?", ["Scheduling tools", "Cloud storage", "Email marketing", "Nothing I can think of"]),
    ("How do you handle server alerts today?", ["I don't get alerts", "Email notifications", "A monitoring dashboard", "I check manually"]),
]

_CONTENT_SCHEMA = {
    "type": "object",
    "properties": {"content": {"type": "string"}},
    "required": ["content"],
}

_POLL_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["question", "options"],
}

_FLUX_MODEL = "@cf/black-forest-labs/flux-1-schnell"
_ATTRITION_DAYS = 30
_MIN_PILLAR_POSTS = 3  # below this, adaptation falls back to the fixed rotation
_MAX_WELCOMES_PER_DAY = 2


def _agent_cfg(account: SocialAccount) -> dict[str, Any]:
    return (account.meta_data or {}).get(AGENT_META_KEY) or {}


def agent_enabled(account: SocialAccount) -> bool:
    """Agent runs when a managed channel exists and ``enabled`` isn't false."""
    managed = (account.meta_data or {}).get("telegram_channel") or {}
    return bool(managed.get("chat_id")) and _agent_cfg(account).get("enabled", True)


def _iso_week(dt: datetime) -> int:
    return int(dt.strftime("%G")) * 100 + int(dt.strftime("%V"))


def plan_post(now: datetime, pillar_stats: dict[str, dict[str, int]] | None = None) -> dict[str, Any] | None:
    """Pick today's content plan. Returns None on non-posting days."""
    dow = now.weekday()  # Mon=0
    if dow == 0:
        pillar = "authority"
    elif dow == 2:
        pillar = "engagement"
    elif dow == 4:
        week = int(now.strftime("%V"))
        if week % 3 == 0:
            pillar = "offer"
        else:
            # Adaptation: prefer the pillar with the best joins-per-post rate.
            scored = {
                k: v["joins"] / max(v["posts"], 1)
                for k, v in (pillar_stats or {}).items()
                if v.get("posts", 0) >= _MIN_PILLAR_POSTS
            }
            pillar = max(scored, key=scored.get) if scored else ("authority" if week % 3 == 1 else "proof")
    else:
        return None

    if pillar == "offer":
        week_num = int(now.strftime("%V")) // 3
        offer = OFFERS[week_num % len(OFFERS)]
    else:
        offer = PILLARS.get(pillar, PILLARS["authority"])

    return {
        "pillar": pillar,
        "series_tag": offer["tag"],
        "prompt": f"{offer['tag']} {offer['prompt']}",
        "image_prompt": offer["image_prompt"],
        "buttons": offer.get("buttons"),
        "document": offer.get("document"),
        # Offers are the conversion surface — pin them like a channel admin would.
        "pin": pillar == "offer",
    }


def _pillar_stats(events: list[tuple[datetime, dict]]) -> dict[str, dict[str, int]]:
    """Joins-per-post per pillar kind (channel_post meta.tag → joins in 24h)."""
    posts: dict[str, dict[str, int]] = {}
    for occurred, meta in events:
        if meta.get("_event") != "channel_post":
            continue
        kind = meta.get("kind") or meta.get("tag") or "other"
        bucket = posts.setdefault(kind, {"posts": 0, "joins": 0})
        bucket["posts"] += 1
        window_end = occurred + timedelta(hours=24)
        bucket["joins"] += sum(
            1
            for jt, jmeta in events
            if jmeta.get("_event") == "member_joined" and occurred <= jt < window_end
        )
    return posts


async def _generate_image(db: AsyncSession, account: SocialAccount, prompt: str) -> str | None:
    """CF FLUX schnell → persisted MediaAsset → public URL. None on failure."""
    try:
        from app.services.inference import _call_workers_ai_image
        from app.services.media_storage import persist_generated_image

        result = await _call_workers_ai_image(prompt, _FLUX_MODEL)
        b64 = result.get("image_base64")
        if not b64:
            return None
        asset = await persist_generated_image(
            db,
            team_id=account.team_id,
            user_id=None,
            image_bytes=base64.b64decode(b64),
            prompt=prompt,
            source="telegram-agent",
        )
        await db.commit()
        if asset.public_url:
            return asset.public_url
        from app.services.publishing import _media_public_url

        return _media_public_url(asset.storage_path or "")
    except Exception as exc:  # noqa: BLE001 — image gen is non-fatal
        logger.warning("telegram-agent image generation failed: %s", exc)
        await db.rollback()
        return None


async def _generate_copy(db: AsyncSession, account: SocialAccount, prompt: str) -> str:
    """DMR generation → plain-English + SEO ≥90 quality pipeline."""
    from app.services.quality_pipeline import apply_quality_pipeline

    result = await call_inference(
        f"{prompt}\n\nReturn JSON with a single 'content' field — the post text "
        "under 900 characters, Telegram-conversational, emojis allowed, no hashtags.",
        provider_name="dmr",
        db=db,
        team_id=account.team_id,
        schema=_CONTENT_SCHEMA,
        platform="telegram",
    )
    content = (result.get("content") or "").strip()
    if not content:
        raise RuntimeError("empty generation")
    quality = await apply_quality_pipeline(
        content,
        "telegram",
        db=db,
        team_id=account.team_id,
        provider_name="dmr",
        target_score=90,
        topic="telegram channel post",
        tone="conversational",
    )
    return (quality.content or content).strip()


async def _weekly_poll(
    client: TelegramAPIClient, chat_id: Any, cfg: dict[str, Any], now: datetime
) -> dict[str, Any] | None:
    """Sunday poll — rotates through POLLS; DMR may rewrite the question."""
    if not cfg.get("polls_enabled", True):
        return None
    week = int(now.strftime("%V"))
    if cfg.get("last_poll_week") == week:
        return None  # already sent this ISO week
    question, options = POLLS[week % len(POLLS)]
    await client.send_poll(chat_id, question, options)
    cfg["last_poll_week"] = week
    return {"kind": "poll", "question": question}


async def run_channel_agent(account: SocialAccount, db: AsyncSession) -> dict[str, Any]:
    """One daily pass for one telegram account. Idempotent per calendar day."""
    cfg = dict(_agent_cfg(account))
    managed = (account.meta_data or {}).get("telegram_channel") or {}
    chat_id = managed.get("chat_id")
    if not chat_id or cfg.get("enabled", True) is False:
        return {"skipped": "disabled_or_no_channel"}

    now = datetime.now(UTC)
    token = _bot_token(account)
    if not token:
        return {"skipped": "no_token"}
    client = TelegramAPIClient(token)
    out: dict[str, Any] = {}

    # Sunday = poll day, no feed post.
    if now.weekday() == 6:
        poll = await _weekly_poll(client, chat_id, cfg, now)
        if poll:
            out["poll"] = poll["question"]
        _save_cfg(account, cfg)
        await db.commit()
        return out

    plan = plan_post(now, cfg.get("pillar_stats"))
    if not plan:
        return {"skipped": "rest_day"}
    if cfg.get("last_post_date") == now.date().isoformat():
        return {"skipped": "already_posted"}

    text = await _generate_copy(db, account, plan["prompt"])
    image_url = (
        None if plan.get("document")
        else await _generate_image(db, account, plan["image_prompt"])
    )
    markup = inline_keyboard(plan["buttons"]) if plan["buttons"] else None

    caption = text[:1024]
    try:
        if plan.get("document"):
            results = [
                await client.send_document(
                    chat_id, plan["document"], caption=caption, reply_markup=markup
                )
            ]
            kind = "document"
        elif image_url:
            results = [await client.send_photo(chat_id, image_url, caption=caption, reply_markup=markup)]
            kind = "photo"
        else:
            results = [
                await client.send_message_with_markup(
                    chat_id, caption, reply_markup=markup
                )
            ]
            kind = "message"
    except TelegramAPIError as exc:
        return {"error": f"telegram send failed ({exc.status_code}): {exc.description[:200]}"}
    if len(text) > 1024:
        results.append(await client.send_message(chat_id, text))
    if plan.get("pin"):
        first_id = results[0].get("message_id")
        if isinstance(first_id, int):
            try:
                await client.pin_chat_message(chat_id, first_id)
            except Exception:  # noqa: BLE001 — pin is best-effort
                logger.info("telegram-agent pin failed chat=%s", chat_id)

    _record_post_event(db, account, chat_id, managed, results, plan["pillar"], kind)
    cfg["last_post_date"] = now.date().isoformat()
    cfg["pillar_stats"] = _updated_pillar_stats(cfg.get("pillar_stats"), plan["pillar"])
    _save_cfg(account, cfg)
    await db.commit()
    out["published"] = {"pillar": plan["pillar"], "message_ids": [r.get("message_id") for r in results], "kind": kind}
    return out


def _bot_token(account: SocialAccount) -> str:
    """Same decode order as api.telegram._decrypt_bot_token (enc may be str)."""
    from app.core.security import decrypt_token

    def _dec(raw: str | bytes) -> str:
        return decrypt_token(raw if isinstance(raw, bytes) else raw.encode())

    meta = account.meta_data or {}
    for raw in (meta.get("bot_token_enc"), account.access_token_enc):
        if raw:
            try:
                return _dec(raw)
            except Exception:  # noqa: BLE001 — try the next credential slot
                continue
    return ""


def _save_cfg(account: SocialAccount, cfg: dict[str, Any]) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    meta = dict(account.meta_data or {})
    meta[AGENT_META_KEY] = cfg
    account.meta_data = meta
    flag_modified(account, "meta_data")


def _record_post_event(
    db: AsyncSession,
    account: SocialAccount,
    chat_id: Any,
    managed: dict,
    results: list[Any],
    pillar: str,
    kind: str,
) -> None:
    db.add(
        AnalyticsEvent(
            team_id=account.team_id,
            social_account_id=account.id,
            event_type="channel_post",
            platform="telegram",
            occurred_at=datetime.now(UTC),
            meta_data={
                "chat_id": str(chat_id),
                "chat_title": managed.get("title"),
                "kind": kind,
                "tag": pillar,
                "has_media": kind == "photo",
                "agent": True,
                "message_ids": [m.get("message_id") for m in results if isinstance(m, dict)],
            },
        )
    )


def _updated_pillar_stats(stats: dict | None, pillar: str) -> dict[str, dict[str, int]]:
    stats = dict(stats or {})
    bucket = stats.setdefault(pillar, {"posts": 0, "joins": 0})
    bucket["posts"] += 1
    return stats


# ── engagement: welcomes + join-request approval (called from the webhook) ──


async def maybe_welcome_member(
    client: TelegramAPIClient,
    account: SocialAccount,
    member_event: dict[str, Any],
    db: AsyncSession,
) -> bool:
    """Daily-throttled welcome post when a new member joins the channel.

    Called from the ``chat_member`` webhook path after the join event is
    persisted. Silent on any failure — never breaks the webhook response.
    """
    try:
        cfg = dict(_agent_cfg(account))
        if cfg.get("enabled", True) is False or not cfg.get("welcome_enabled", True):
            return False
        today = datetime.now(UTC).date().isoformat()
        welcomed = cfg.get("welcomed_today") or {}
        if welcomed.get("date") == today and welcomed.get("count", 0) >= _MAX_WELCOMES_PER_DAY:
            return False
        managed = (account.meta_data or {}).get("telegram_channel") or {}
        chat_id = managed.get("chat_id")
        if not chat_id:
            return False
        name = member_event.get("first_name") or member_event.get("username") or "there"
        via = member_event.get("invite_link_name") or ""
        await client.send_message(
            chat_id,
            f"Welcome {name} 👋 Glad you found us{' via ' + via if via else ''}. "
            "This channel is the automation build log — real self-hosted "
            "engineering, no fluff. Pinned post has the map.",
        )
        cfg["welcomed_today"] = {"date": today, "count": welcomed.get("count", 0) + 1}
        _save_cfg(account, cfg)
        await db.commit()
        return True
    except Exception:  # noqa: BLE001
        logger.warning("telegram-agent welcome send failed", exc_info=True)
        await db.rollback()
        return False


async def refresh_pillar_stats(account: SocialAccount, db: AsyncSession) -> dict[str, dict[str, int]]:
    """Recompute joins-per-post per pillar tag from the last 30 days of events."""
    since = datetime.now(UTC) - timedelta(days=_ATTRITION_DAYS)
    rows = (
        await db.execute(
            select(AnalyticsEvent.occurred_at, AnalyticsEvent.event_type, AnalyticsEvent.meta_data)
            .where(
                AnalyticsEvent.social_account_id == account.id,
                AnalyticsEvent.platform == "telegram",
                AnalyticsEvent.event_type.in_(("channel_post", "member_joined")),
                AnalyticsEvent.occurred_at >= since,
            )
            .order_by(AnalyticsEvent.occurred_at)
        )
    ).all()
    events = [
        (occ, {**(meta or {}), "_event": et}) for occ, et, meta in rows
    ]
    stats = _pillar_stats(events)
    cfg = dict(_agent_cfg(account))
    cfg["pillar_stats"] = stats
    _save_cfg(account, cfg)
    return stats


async def run_agent_sweep() -> dict[str, Any]:
    """Beat entry — every active telegram account with a managed channel."""
    from app.worker._async import task_session

    summary: dict[str, Any] = {"accounts": 0, "published": 0, "results": []}
    async with task_session() as db:
        rows = (
            await db.execute(
                select(SocialAccount).where(
                    SocialAccount.platform == "telegram",
                    SocialAccount.status == "active",
                )
            )
        ).scalars().all()
        for account in rows:
            if not agent_enabled(account):
                continue
            summary["accounts"] += 1
            try:
                await refresh_pillar_stats(account, db)
                result = await run_channel_agent(account, db)
                if result.get("published"):
                    summary["published"] += 1
                summary["results"].append({"account_id": str(account.id), **result})
            except Exception as exc:  # noqa: BLE001 — one bad account never blocks the rest
                logger.warning("telegram-agent run failed account=%s: %s", account.id, exc)
                summary["results"].append({"account_id": str(account.id), "error": str(exc)[:200]})
    return summary
