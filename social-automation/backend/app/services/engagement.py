"""Outbound engagement engine — data-driven like/comment/repost.

Implements the interaction-multiplier scoring + 3C comment synthesis +
sentiment kill-switch + jitter pacing from the 2026 algorithm research.

Action value:  like=1x, repost=3x, substantive comment=5x.
Every action is scored, jittered, and (for comments/reposts) human-gated.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

ACTION_WEIGHTS = {"like": 1, "repost": 3, "comment": 5}

# Jitter windows per platform (seconds after target post is seen)
JITTER_RANGE = (143, 412)

# Reciprocity tiers — connections who reply/view get boosted
RYR_TIERS = {"high": 2.0, "medium": 1.0, "low": 0.3}

# Hard kill-switch terms — never auto-engage on these
KILL_SWITCH_TERMS = {
    "job loss",
    "laid off",
    "fired",
    "let go",
    "restructuring",
    "health",
    "surgery",
    "hospital",
    "cancer",
    "diagnosis",
    "died",
    "death",
    "passing",
    "funeral",
    "grief",
    "mourning",
    "divorce",
    "breakup",
    "crisis",
}


@dataclass
class EngagementCandidate:
    platform: str
    author_handle: str
    author_name: str
    post_id: str
    post_text: str
    posted_seconds_ago: float
    connection_tier: str  # high | medium | low
    is_followed: bool
    is_connection: bool
    in_crm: bool  # lead/contact in espocrm
    meta_data: dict = field(default_factory=dict)  # e.g. {"viral": True}


@dataclass
class EngagementPlan:
    action: str | None  # like | repost | comment | None (skip)
    score: float  # weighted opportunity score
    jitter_seconds: int  # how long to wait before acting
    comment_draft: str | None  # 3C comment if action==comment
    kill_switched: bool  # sentiment block
    reason: str


def _sentiment_kill(post_text: str) -> bool:
    """Semantic kill-switch — never auto-engage sensitive posts."""
    low = post_text.lower()
    return any(t in low for t in KILL_SWITCH_TERMS)


def score_candidate(c: EngagementCandidate, ryr_tier: str = "medium") -> EngagementPlan:
    """Score a target post → engagement plan (or skip)."""
    if _sentiment_kill(c.post_text):
        return EngagementPlan(None, 0.0, 0, None, True, "kill-switch: sensitive content")

    # base opportunity score
    s = 0.0
    if c.is_connection:
        s += 3.0
    if c.is_followed:
        s += 2.0
    if c.in_crm:
        s += 4.0  # leads/contacts are highest-value
    s += RYR_TIERS.get(ryr_tier, 1.0)

    # freshness decay — engagement in the first 90min counts most
    age_min = c.posted_seconds_ago / 60
    if age_min < 90:
        s += 4.0
    elif age_min < 360:
        s += 2.0
    elif age_min < 1440:
        s += 0.5

    # text richness — longer posts deserve comments, short ones get likes
    words = len(c.post_text.split())
    if words > 60:
        s += 2.0  # substantive → comment-worthy
    elif words < 15:
        s += 0.5  # short → like only

    if s < 4.0:
        return EngagementPlan(None, s, 0, None, False, f"below threshold ({s:.1f})")

    jitter = random.randint(*JITTER_RANGE)
    # pick action by score tier
    if s >= 8.0 and words > 40:
        return EngagementPlan("comment", s, jitter, None, False, "5x action: comment")
    if s >= 7.0:
        return EngagementPlan("repost", s, jitter, None, False, "3x action: repost")
    return EngagementPlan("like", s, jitter, None, False, "1x action: like")


GROWTH_KEYWORDS = {"founder", "startup", "saas", "bootstrap", "mvp", "run rate", "agency", "overhead", "revenue", "growth", "indie", "vc", "funding"}
TECH_KEYWORDS = {"serverless", "cloudflare", "workers", "edge", "d1", "r2", "vercel", "latency", "next.js", "docker", "k8s", "database", "engineer", "cto"}


def classify_vector(author_bio: str, post_text: str) -> tuple[str, str]:
    """Dual-vector routing: GROWTH_UNIT_ECONOMICS vs TECH_STACK."""
    text = (author_bio + " " + post_text).lower()
    g = sum(k in text for k in GROWTH_KEYWORDS)
    t = sum(k in text for k in TECH_KEYWORDS)
    if t > g:
        return (
            "TECH_STACK",
            "Critique the infra angle — cold layouts, edge-data sync limits, D1/R2 architecture, ultra-low latency configs. Code-adjacent peer tone.",
        )
    return (
        "GROWTH_UNIT_ECONOMICS",
        "Focus on the 14-day delivery frame and cost compression — serverless automation compressing 6-month roadmaps. No deep deployment jargon.",
    )


def build_3c_prompt(post_text: str, author_name: str, voice_hints: list[str], vector_override: str | None = None) -> str:
    """Prompt for the DMR LLM to write a 3C comment — never generic."""
    override = f"- VECTOR FOCUS: {vector_override}\n" if vector_override else ""
    return f"""Write a LinkedIn comment replying to {author_name}'s post. Rules:
{override}
- COMPLIMENT one specific point (quote or paraphrase it — prove you read it)
- CONTRIBUTION: add one net-new expert insight or data point
- CONVERSATION: end with an open question inviting reply
- QUALIFIED-VIEW HOOK: where natural, seed a cliffhanger pointing to the
  author's pinned media ("the 5s teardown in your pinned post nails this")
  — drives >5s qualified views on the reader's own video assets
- 15–45 words. Conversational, first-person. NO "Great post!", no filler,
  no emoji spam, no hashtags. Sound like a human colleague, not a bot.
Author's brand voice: {voice_hints or "professional, technical"}
Post text:
---
{post_text[:1200]}
---
Comment:"""


async def queue_engagement(candidate: EngagementCandidate, db) -> dict:
    """Score + queue an engagement action (respecting daily caps)."""
    from sqlalchemy import select

    plan = score_candidate(candidate)
    if plan.action is None:
        return {"skipped": plan.reason, "kill_switched": plan.kill_switched}

    # per-platform daily cap check — count today's actions
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func

    from app.models.engagement import EngagementAction

    day_ago = datetime.now(UTC) - timedelta(days=1)
    today = (
        await db.execute(
            select(func.count())
            .select_from(EngagementAction)
            .where(
                EngagementAction.platform == candidate.platform,
                EngagementAction.created_at > day_ago,
            )
        )
    ).scalar() or 0
    cap = {"linkedin": 30, "twitter": 60, "instagram": 40, "threads": 40, "facebook": 20, "tiktok": 30}.get(candidate.platform, 25)
    if today >= cap:
        return {"skipped": f"daily cap {cap} reached", "queued_for_tomorrow": True}

    # Meta hard-cap: max 5 comments/hour on facebook (anti-spam ceiling)
    if candidate.platform == "facebook" and plan.action == "comment":
        hour_ago = datetime.now(UTC) - timedelta(hours=1)
        this_hour = (
            await db.execute(
                select(func.count())
                .select_from(EngagementAction)
                .where(EngagementAction.platform == "facebook", EngagementAction.action == "comment", EngagementAction.created_at > hour_ago)
            )
        ).scalar() or 0
        if this_hour >= 5:
            return {"skipped": "fb hourly comment cap (5/h)"}

    # viral tiktok candidates surface as duet/stitch flags for the phone
    action_name = plan.action
    gated = plan.action in ("comment", "repost")
    if candidate.platform == "tiktok" and (candidate.meta_data or {}).get("viral"):
        action_name = "duet_flag"
        gated = True
    row = EngagementAction(
        platform=candidate.platform,
        target_post_id=candidate.post_id,
        target_author=candidate.author_handle,
        action=action_name,
        score=plan.score,
        jitter_seconds=plan.jitter_seconds,
        comment_draft=plan.comment_draft,
        status="pending_human_gate" if gated else "pending",
    )
    db.add(row)
    await db.commit()
    return {"queued": action_name, "score": plan.score, "needs_approval": gated}
