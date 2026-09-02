"""Autonomous brand content agent.

Reads empty calendar slots for the next 7 days, selects topics from
brand messaging pillars, generates on-brand content, keeps only content
scoring >= 4/5 compliance, generates or selects matching images,
creates drafts, and notifies the user.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.brand import Brand, BrandVoice
from app.models.content import Post, PostStatus
from app.services.brand_compliance import build_brand_system_prompt, score_brand_compliance


async def find_empty_calendar_slots(
    db: AsyncSession,
    team_id: uuid.UUID,
    days: int = 7,
) -> list[datetime]:
    """Find empty slots in the content calendar for the next N days.

    Returns list of datetime slots that have no scheduled posts.
    """
    now = datetime.now(UTC)
    slots: list[datetime] = []
    for day_offset in range(1, days + 1):
        # Check morning (9am) and afternoon (2pm) slots
        for hour in [9, 14]:
            slot = (now + timedelta(days=day_offset)).replace(hour=hour, minute=0, second=0, microsecond=0)
            # Check if a post is already scheduled within 1 hour of this slot
            result = await db.execute(
                select(Post).where(
                    Post.team_id == team_id,
                    Post.status == PostStatus.SCHEDULED,
                    Post.scheduled_at >= slot - timedelta(hours=1),
                    Post.scheduled_at <= slot + timedelta(hours=1),
                )
            )
            if not result.scalars().first():
                slots.append(slot)
    return slots


async def generate_on_brand_content(
    db: AsyncSession,
    brand: Brand,
    voice: BrandVoice | None,
    topic: str,
    platform: str = "linkedin",
) -> str:
    """Generate on-brand content for a topic using AI inference.

    Uses brand context (voice, tone, banned phrases) to ensure compliance.
    """
    from app.services.inference import call_inference

    brand_context = build_brand_system_prompt(
        brand={
            "name": brand.name,
            "positioning_statement": brand.positioning_statement,
            "mission": brand.mission,
            "values": brand.values,
            "tagline": brand.tagline,
        },
        voice={
            "tone_dimensions": voice.tone_dimensions if voice else {},
            "banned_phrases": voice.banned_phrases if voice else [],
            "preferred_phrases": voice.preferred_phrases if voice else [],
            "messaging_pillars": voice.messaging_pillars if voice else [],
        } if voice else None,
    )

    prompt = f"""Write a {platform} post about: {topic}

Requirements:
- Keep it under 280 characters for Twitter, or 1300 characters for LinkedIn
- Use the brand voice and tone from the system prompt
- Avoid any banned phrases
- Include relevant hashtags
- Make it engaging and shareable

Return only the post content, no preamble."""

    result = await call_inference(
        prompt=prompt,
        provider_name="cloudflare",
        db=db,
        brand_context=brand_context,
        max_tokens=500,
    )

    if isinstance(result.get("response"), str):
        return result["response"].strip()
    return str(result.get("response", "")).strip()


async def run_autopilot(
    db: AsyncSession,
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    days: int = 7,
    min_compliance_score: int = 4,
) -> dict[str, Any]:
    """Run the autonomous brand content agent.

    1. Find empty calendar slots for the next N days
    2. Select topics from brand messaging pillars
    3. Generate on-brand content for each slot
    4. Check compliance — only keep content scoring >= min_compliance_score
    5. Create draft posts
    6. Return summary of created drafts

    Returns dict with created_count, skipped_count, slots_filled, drafts.
    """
    # Load brand and voice
    brand_result = await db.execute(select(Brand).where(Brand.team_id == team_id))
    brand = brand_result.scalars().first()
    if not brand:
        return {"error": "No brand found", "created_count": 0, "skipped_count": 0}

    voice_result = await db.execute(select(BrandVoice).where(BrandVoice.brand_id == brand.id))
    voice = voice_result.scalars().first()

    # Get messaging pillars for topic selection
    pillars = voice.messaging_pillars if voice and voice.messaging_pillars else []
    if not pillars:
        # Fall back to brand values as topics
        pillars = [{"pillar": v, "description": ""} for v in (brand.values or [])]
    if not pillars:
        return {"error": "No messaging pillars or values found", "created_count": 0, "skipped_count": 0}

    # Find empty slots
    slots = await find_empty_calendar_slots(db, team_id, days)
    if not slots:
        return {"message": "No empty calendar slots found", "created_count": 0, "skipped_count": 0}

    # Generate content for each slot
    drafts: list[dict] = []
    created_count = 0
    skipped_count = 0

    for i, slot in enumerate(slots):
        # Cycle through pillars
        pillar = pillars[i % len(pillars)]
        topic = pillar.get("pillar", pillar.get("title", ""))

        try:
            content = await generate_on_brand_content(db, brand, voice, topic)

            # Check compliance
            compliance = await score_brand_compliance(
                content=content,
                brand={
                    "name": brand.name,
                    "positioning_statement": brand.positioning_statement,
                    "mission": brand.mission,
                    "values": brand.values,
                },
                voice={
                    "tone_dimensions": voice.tone_dimensions if voice else {},
                    "banned_phrases": voice.banned_phrases if voice else [],
                    "preferred_phrases": voice.preferred_phrases if voice else [],
                } if voice else None,
            )

            score = compliance.get("score", 0)
            if score < min_compliance_score:
                skipped_count += 1
                continue

            # Create draft post
            post = Post(
                team_id=team_id,
                user_id=user_id,
                content=content,
                status=PostStatus.DRAFT,
                scheduled_at=slot,
            )
            db.add(post)
            await db.commit()
            await db.refresh(post)

            drafts.append({
                "id": str(post.id),
                "topic": topic,
                "scheduled_at": slot.isoformat(),
                "compliance_score": score,
                "content_preview": content[:100],
            })
            created_count += 1
        except Exception:
            skipped_count += 1
            continue

    return {
        "created_count": created_count,
        "skipped_count": skipped_count,
        "slots_filled": created_count,
        "drafts": drafts,
    }
