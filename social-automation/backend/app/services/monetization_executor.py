"""Monetization action executor — turns the daily report's action list into
real SCHEDULED posts.

Every action goes through the same production chain the UI's
``/generate-content`` uses: brand context + platform guide + structured
inference → plain-English rewrite → quality pipeline (SEO ≥ 90) → brand
compliance → branded/AI media via ComfyUI FLUX → Cloudflare Workers AI →
``persist_generated_image``.

Safety rails (owner rules):
- never schedule a post without media — falls back to a DRAFT with a note
- compliance score < 3/5 → DRAFT, not auto-scheduled
- idempotent per (report date, platform, instruction) via
  ``meta_data.monetization_action`` hash — reruns never double-schedule
- dead-night times (00:00–06:00 Athens) remap to the platform baseline;
  slot conflicts shift +30min (up to 3 hops)
- messaging-only platforms (whatsapp/messenger/viber) can't take posts and
  are skipped
"""

from __future__ import annotations

import base64
import hashlib
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import Post, PostStatus, PostTarget
from app.models.social_account import SocialAccount
from app.models.user import Team
from app.services.content_sanitizer import sanitize_post_text
from app.services.monetization_report import MonetizationAction

logger = logging.getLogger(__name__)

_ATHENS = ZoneInfo("Europe/Athens")
_MIN_COMPLIANCE = 3.0  # 5-point brand_compliance scale
_MAX_ACTIONS_PER_RUN = 4
_MAX_SLOT_HOPS = 3

# Platforms with no post-publish path — report only.
_NON_POSTING = {"whatsapp", "messenger", "viber"}

# Platforms whose only accepted media is video — the image pipeline can't
# satisfy them, so actions land as drafts instead of scheduling a post that
# would fail publish validation.
_VIDEO_ONLY = {"tiktok", "youtube", "snapchat"}

# Baseline posting windows (Athens) when an action lacks a sane time, and the
# dead-night remap target. Conservative B2B defaults.
_PLATFORM_BASELINE: dict[str, str] = {
    "linkedin": "09:30",
    "facebook": "12:00",
    "instagram": "19:00",
    "threads": "18:00",
    "twitter": "17:00",
    "tiktok": "20:00",
    "telegram": "10:00",
}


def _action_hash(report_date: str, action: MonetizationAction) -> str:
    raw = f"{report_date}|{action.platform}|{action.instruction.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def _resolve_account(db: AsyncSession, team_id: uuid.UUID, platform: str, instruction: str) -> SocialAccount | None:
    accounts = (
        (
            await db.execute(
                select(SocialAccount).where(
                    SocialAccount.team_id == team_id,
                    SocialAccount.platform == platform,
                    SocialAccount.status == "active",
                )
            )
        )
        .scalars()
        .all()
    )
    if not accounts:
        return None
    if len(accounts) == 1:
        return accounts[0]
    low = instruction.lower()
    corporate = any(k in low for k in ("company page", "corporate", "brand", "audit", "cloudless"))
    if platform == "linkedin":
        # org page for corporate pushes, personal profile for founder voice
        org = [a for a in accounts if a.account_type in ("organization", "page")]
        personal = [a for a in accounts if a.account_type == "person"]
        pick = org if corporate else personal
        return (pick or accounts)[0]
    if platform == "facebook":
        # personal profile is the Stars-growth surface; the page takes
        # brand/corporate content
        page = [a for a in accounts if a.account_type == "page"]
        personal = [a for a in accounts if a.account_type in ("person", "user")]
        pick = page if corporate else personal
        return (pick or accounts)[0]
    return accounts[0]


def _resolve_slot(
    action: MonetizationAction,
    now_utc: datetime,
    learned_hours: dict[str, int] | None = None,
    target_date: date | None = None,
) -> datetime:
    """Action's Athens HH:MM → next UTC occurrence; dead-night → baseline.

    When the action has no time, prefer the insights engine's learned best
    hour (shrunk-bucket winner, min n=3) over the static baseline.
    ``target_date`` pins the slot to a specific Athens day (the strategy
    brief's "tomorrow's playbook"); None keeps next-occurrence semantics."""
    hhmm = action.time_athens
    if not hhmm and learned_hours and action.platform in learned_hours:
        hhmm = f"{learned_hours[action.platform]:02d}:00"
    hhmm = hhmm or _PLATFORM_BASELINE.get(action.platform, "10:00")
    try:
        hour, minute = (int(x) for x in hhmm.split(":"))
    except ValueError:
        hour, minute = (int(x) for x in _PLATFORM_BASELINE.get(action.platform, "10:00").split(":"))
    if not (0 <= hour <= 23 and 0 <= minute <= 59) or hour < 7:
        hour, minute = (int(x) for x in _PLATFORM_BASELINE.get(action.platform, "10:00").split(":"))
    base = now_utc.astimezone(_ATHENS)
    if target_date:
        base = base.replace(year=target_date.year, month=target_date.month, day=target_date.day)
    local = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local.astimezone(UTC) <= now_utc + timedelta(minutes=10):
        local += timedelta(days=1)  # too soon / already passed → tomorrow
    return local.astimezone(UTC)


async def _slot_taken(db: AsyncSession, team_id: uuid.UUID, account_id: uuid.UUID, when: datetime) -> bool:
    return bool(
        (
            await db.execute(
                select(Post.id)
                .join(PostTarget, PostTarget.post_id == Post.id)
                .where(
                    Post.team_id == team_id,
                    Post.status == PostStatus.SCHEDULED,
                    PostTarget.social_account_id == account_id,
                    Post.scheduled_at >= when - timedelta(minutes=30),
                    Post.scheduled_at <= when + timedelta(minutes=30),
                )
            )
        ).first()
    )


async def _generate_copy(
    db: AsyncSession,
    team: Team,
    action: MonetizationAction,
    brand_ctx: str,
) -> tuple[str, list[str], dict | None]:
    """Full production chain — same services as /api/v1/ai/generate-content."""
    from app.services.content_guides import CONTENT_SCHEMA, HASHTAG_RULES, PLATFORM_GUIDES
    from app.services.inference import call_inference
    from app.services.plain_english import PLAIN_ENGLISH_RULES, SOFIA_POST_SPEC, rewrite_plain_english
    from app.services.quality_pipeline import apply_quality_pipeline

    guide = PLATFORM_GUIDES.get(action.platform, PLATFORM_GUIDES["linkedin"])
    brand_section = f"\n\nBRAND CONTEXT:\n{brand_ctx}\n" if brand_ctx else ""

    prompt = f"""Write a {action.platform} post based on this instruction: "{action.instruction}"
{brand_section}
Platform guidelines: {guide}
Tone: professional
Length: medium
Include hashtags: true
Include emojis: true

{PLAIN_ENGLISH_RULES}
{SOFIA_POST_SPEC}

{HASHTAG_RULES}

Return JSON with: content, hashtags (array), suggested_media (string or null)"""

    result = await call_inference(
        prompt,
        provider_name="dmr",
        db=db,
        team_id=team.id,
        schema=CONTENT_SCHEMA,
        platform=action.platform,
    )
    content = await rewrite_plain_english(
        result.get("content", ""),
        provider_name="dmr",
        db=db,
        team_id=team.id,
        context=f"{action.platform} post",
    )
    quality = await apply_quality_pipeline(
        content=content,
        platform=action.platform,
        hashtags=result.get("hashtags", []),
        db=db,
        team_id=team.id,
        provider_name="dmr",
        target_score=90,
        max_iterations=2,
        topic=action.instruction,
    )
    return quality.content, quality.hashtags, getattr(quality, "seo_score", None)


async def _generate_media(db: AsyncSession, team: Team, user_id, action: MonetizationAction) -> uuid.UUID | None:
    """Branded image for the post — ComfyUI FLUX → Cloudflare Workers AI →
    brand banner (PIL, always succeeds). Returns a MediaAsset id or None."""
    from app.services.media_ai import expand_visual_prompt
    from app.services.media_storage import persist_generated_image

    prompt = await expand_visual_prompt(action.instruction, media_type="image")

    png: bytes | None = None
    try:  # local GPU first
        from app.services.comfyui_image import generate_image as comfyui_generate_image
        from app.services.gpu_arbiter import media_gpu_lock

        async with media_gpu_lock():
            png, _meta = await comfyui_generate_image(prompt=prompt, steps=6)
    except Exception as exc:  # noqa: BLE001
        logger.info("comfyui image gen failed: %s", str(exc)[:120])

    if png is None:
        try:  # Cloudflare free-tier fallback
            from app.services.inference import _call_workers_ai_image

            result = await _call_workers_ai_image(prompt, "@cf/black-forest-labs/flux-1-schnell")
            b64 = result.get("image_base64")
            if b64:
                png = base64.b64decode(b64)
        except Exception as exc:  # noqa: BLE001
            logger.info("cf image gen failed: %s", str(exc)[:120])

    if png is None:
        try:  # deterministic brand banner — never leaves a post media-less
            from app.services.brand_assets import generate_og_image
            from app.services.spellcheck import auto_correct, preprocess_for_render

            # action.instruction is LLM text going straight onto the image —
            # gate it through LanguageTool + PIL-safe preprocessing first.
            tagline = preprocess_for_render(await auto_correct(action.instruction))
            png = generate_og_image("cloudless.gr", tagline=tagline[:80])
        except Exception as exc:  # noqa: BLE001
            logger.warning("brand banner gen failed: %s", str(exc)[:120])
            return None

    asset = await persist_generated_image(
        db,
        team_id=team.id,
        user_id=user_id,
        image_bytes=png,
        prompt=prompt,
        source="monetization-report",
    )
    return asset.id if asset else None


async def schedule_report_actions(
    db: AsyncSession,
    team: Team,
    actions: list[MonetizationAction],
    report_date: str,
    learned_hours: dict[str, int] | None = None,
    *,
    source: str = "monetization-report",
    dedup_field: str = "monetization_action",
    max_actions: int = _MAX_ACTIONS_PER_RUN,
    target_date: date | None = None,
) -> list[dict[str, Any]]:
    """Materialise report actions as SCHEDULED posts. Returns a per-action
    result list for the report email. Idempotent per report date.

    ``learned_hours``: platform → Athens hour from the insights engine's
    shrunk-bucket best-window analysis; used when an action has no time.
    ``source``/``dedup_field`` tag the created posts and drive the rerun
    dedup, so the monetization report and the strategy brief share the
    executor without colliding. ``target_date`` pins slots to a specific
    Athens day (the strategy brief's "tomorrow"); None = next occurrence."""
    from app.models.user import User

    results: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    owner = (await db.execute(select(User).where(User.id == team.owner_id))).scalars().first()

    from app.services.brand_compliance import load_brand_context, score_brand_compliance

    brand_dict, voice_dict, brand_ctx = await load_brand_context(db, team.id)

    for action in actions[:max_actions]:
        platform = action.platform
        entry: dict[str, Any] = {
            "platform": platform,
            "instruction": action.instruction,
            "status": "pending",
        }
        if platform in _NON_POSTING:
            entry.update(status="skipped", reason="messaging-only platform")
            results.append(entry)
            continue

        account = await _resolve_account(db, team.id, platform, action.instruction)
        if not account:
            entry.update(status="skipped", reason="no active account")
            results.append(entry)
            continue

        ahash = _action_hash(report_date, action)
        existing = await db.scalar(
            select(func.count(Post.id)).where(
                Post.team_id == team.id,
                Post.meta_data[dedup_field].astext == ahash,
            )
        )
        if existing:
            entry.update(status="skipped", reason="already scheduled", scheduled_at=None)
            results.append(entry)
            continue

        when = _resolve_slot(action, now, learned_hours, target_date)
        for _ in range(_MAX_SLOT_HOPS):
            if not await _slot_taken(db, team.id, account.id, when):
                break
            when += timedelta(minutes=30)
        entry["scheduled_at"] = when.astimezone(_ATHENS).strftime("%Y-%m-%d %H:%M %Z")

        try:
            content, hashtags, seo = await _generate_copy(db, team, action, brand_ctx)
        except Exception as exc:  # noqa: BLE001
            logger.warning("action copy gen failed: %s", str(exc)[:160])
            entry.update(status="failed", reason=f"content generation: {str(exc)[:100]}")
            results.append(entry)
            continue
        if not content.strip():
            entry.update(status="failed", reason="empty generated copy")
            results.append(entry)
            continue

        video_only = action.format == "video" or platform in _VIDEO_ONLY
        media_id = None
        if video_only:
            entry["reason"] = "video format — media pipeline produces images; left as draft"
        else:
            media_id = await _generate_media(db, team, owner.id if owner else None, action)
            if not media_id:
                entry["reason"] = "media generation failed — left as draft"
        status = PostStatus.SCHEDULED if media_id else PostStatus.DRAFT

        if status == PostStatus.SCHEDULED and brand_dict:
            try:
                comp = await score_brand_compliance(
                    content=content,
                    brand=brand_dict,
                    voice=voice_dict,
                    platform=platform,
                    db=db,
                    team_id=team.id,
                )
                if (comp.get("score") or 0) < _MIN_COMPLIANCE:
                    status = PostStatus.DRAFT
                    entry["reason"] = f"brand compliance {comp.get('score')}/5 — left as draft"
            except Exception as exc:  # noqa: BLE001
                logger.info("compliance scoring skipped: %s", str(exc)[:120])

        post = Post(
            team_id=team.id,
            user_id=owner.id if owner else None,
            content_text=sanitize_post_text(content),
            hashtags=hashtags,
            media_ids=[media_id] if media_id else [],
            status=status,
            scheduled_at=when if status == PostStatus.SCHEDULED else None,
            meta_data={
                "source": source,
                dedup_field: ahash,
                "report_date": report_date,
                "instruction": action.instruction,
                "seo_score": seo,
            },
        )
        db.add(post)
        await db.flush()
        db.add(PostTarget(post_id=post.id, social_account_id=account.id))
        await db.commit()

        entry["status"] = "scheduled" if status == PostStatus.SCHEDULED else "drafted"
        entry["post_id"] = str(post.id)
        entry["account"] = f"@{account.username}" if account.username else account.account_id[:12]
        results.append(entry)

    return results
