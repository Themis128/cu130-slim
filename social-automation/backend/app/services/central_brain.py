"""Central brain — the daily decision layer above the reporting lanes.

One bounded sweep per day that looks at every signal the stack already
collects and emits the day's prioritized actions:

* **channels** — latest ``devapp_snapshot`` analytics events (written by
  ``devapps-sync`` when that lane is live; absent → neutral, not dead)
* **market** — ``trend_signals`` rows scored by ``trend_ingest``
  (Google Trends + TikTok Creative Center, SWOT-classified)
* **performance** — ``post_analytics_snapshots`` engagement per platform
  and published/failed post counts over the lookback window
* **audience** — latest ``follower_snapshots`` per account

``decide()`` is pure and returns ``BrainDecision`` rows; ``run()``
persists the plan to ``company_brain`` (so the strategist and future
cycles can cite it) and dispatches ``post`` decisions through
``schedule_report_actions`` — the same production chain every other
report lane uses (brand copy → plain-English → quality → media →
compliance → scheduled/draft). Dedup rides on
``meta_data.central_brain_action`` so reruns are idempotent.

Guardrails even under full-auto: unhealthy channels never generate
posts (they produce ``health`` decisions for the journal/alert lanes),
trend ``noise`` classifications are ignored, and ``max_actions`` caps
the daily dispatch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models.analytics import (
    AnalyticsEvent,
    FollowerSnapshot,
    PostAnalyticsSnapshot,
)
from app.models.content import Post, PostStatus, PostTarget
from app.models.trends import TrendSignal
from app.models.user import Team

logger = logging.getLogger(__name__)

settings = get_settings()

DEVAPP_EVENT_TYPE = "devapp_snapshot"
LOOKBACK_DAYS = 7
MAX_POST_ACTIONS = 5
MAX_TREND_ACTIONS = 2
_EXPIRY_WARN_DAYS = 7.0

# Score weights — followers dominate reach, engagement rate dominates
# quality, trend alignment is the market-adjustment term.
_W_FOLLOWERS = 0.45
_W_ENGAGEMENT = 0.35
_W_TREND = 0.20

TREND_CLASSES = ("opportunity", "early_window")


@dataclass
class ChannelState:
    """Everything the brain knows about one connected account."""

    platform: str
    username: str = ""
    account_id: str = ""
    followers: int | None = None
    healthy: bool | None = None  # None → no devapp data yet
    health_reason: str = ""
    days_to_expiry: float | None = None
    engagement_rate_7d: float = 0.0
    posts_7d: int = 0
    published_7d: int = 0
    failed_7d: int = 0


@dataclass
class BrainDecision:
    platform: str
    kind: str  # "post" | "health"
    instruction: str
    format: str = ""
    priority: int = 50
    reason: str = ""
    trend_query: str = ""


@dataclass
class BrainSnapshot:
    team_id: uuid.UUID
    generated_at: datetime
    channels: dict[str, ChannelState] = field(default_factory=dict)
    trends: list[dict] = field(default_factory=list)


# ── collection ────────────────────────────────────────────────────────


async def _latest_devapp_snapshots(db: AsyncSession, team_id) -> dict[str, dict]:
    """Latest devapp_snapshot payload per account, keyed by account id."""
    rows = (
        (
            await db.execute(
                select(AnalyticsEvent)
                .where(
                    AnalyticsEvent.team_id == team_id,
                    AnalyticsEvent.event_type == DEVAPP_EVENT_TYPE,
                    AnalyticsEvent.social_account_id.is_not(None),
                )
                .order_by(AnalyticsEvent.occurred_at.desc())
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    latest: dict[str, dict] = {}
    for ev in rows:
        key = str(ev.social_account_id)
        if key not in latest:
            latest[key] = ev.meta_data or {}
    return latest


def _snapshot_health(payload: dict, now: datetime) -> tuple[bool | None, str, float | None]:
    """Extract (healthy, reason, days_to_expiry) from a devapp payload."""
    if not payload:
        return None, "", None
    health = payload.get("health") or {}
    ok = payload.get("ok")
    token_valid = health.get("token_valid")

    expiry_raw = payload.get("token_expires_at") or health.get("expires_at")
    days: float | None = None
    if expiry_raw:
        try:
            exp = datetime.fromisoformat(str(expiry_raw).replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=UTC)
            days = (exp - now).total_seconds() / 86400
        except (ValueError, TypeError):
            days = None

    if ok is False or token_valid is False:
        return False, str(payload.get("error") or health.get("error") or "token invalid")[:160], days
    if days is not None and days <= _EXPIRY_WARN_DAYS:
        return True, f"token expires in {days:.1f}d", days
    return True, "", days


async def _engagement_7d(db: AsyncSession, team_id, since: datetime) -> dict[str, dict]:
    """Per-platform engagement rate + snapshot count over the window."""
    rows = (
        await db.execute(
            select(
                PostAnalyticsSnapshot.platform,
                func.avg(PostAnalyticsSnapshot.engagement_rate),
                func.count(PostAnalyticsSnapshot.id),
            )
            .where(
                PostAnalyticsSnapshot.team_id == team_id,
                PostAnalyticsSnapshot.captured_at >= since,
            )
            .group_by(PostAnalyticsSnapshot.platform)
        )
    ).all()
    return {str(p).lower(): {"er": float(er or 0.0), "n": int(n)} for p, er, n in rows}


async def _post_outcomes_7d(db: AsyncSession, team_id, since: datetime) -> dict[str, dict]:
    """Published/failed post counts per platform over the window."""
    rows = (
        (
            await db.execute(
                select(Post)
                .where(
                    Post.team_id == team_id,
                    func.coalesce(Post.published_at, Post.failed_at) >= since,
                )
                .options(selectinload(Post.targets).selectinload(PostTarget.social_account))
            )
        )
        .scalars()
        .unique()
        .all()
    )
    out: dict[str, dict] = {}
    for post in rows:
        platforms = {str(t.social_account.platform).lower() for t in post.targets if t.social_account} or {"unknown"}
        for plat in platforms:
            slot = out.setdefault(plat, {"published": 0, "failed": 0})
            if post.status == PostStatus.PUBLISHED:
                slot["published"] += 1
            elif post.status == PostStatus.FAILED:
                slot["failed"] += 1
    return out


async def _latest_followers(db: AsyncSession, team_id) -> dict[str, int]:
    """Latest follower count per platform (most recent snapshot wins)."""
    rows = (
        await db.execute(
            select(FollowerSnapshot.platform, FollowerSnapshot.followers)
            .where(FollowerSnapshot.team_id == team_id)
            .order_by(FollowerSnapshot.captured_at.desc())
            .limit(1000)
        )
    ).all()
    out: dict[str, int] = {}
    for plat, followers in rows:
        out.setdefault(str(plat).lower(), followers)
    return out


async def _hot_trends(db: AsyncSession, since: datetime) -> list[dict]:
    """Opportunity/early-window trend signals, best first."""
    rows = (
        (
            await db.execute(
                select(TrendSignal)
                .where(
                    TrendSignal.captured_at >= since,
                    TrendSignal.classification.in_(TREND_CLASSES),
                )
                .order_by(TrendSignal.traffic.desc())
                .limit(25)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "query": t.query,
            "source": t.source,
            "geo": t.geo,
            "classification": t.classification,
            "traffic": t.traffic,
            "period_key": t.period_key,
        }
        for t in rows
    ]


async def collect_snapshot(db: AsyncSession, team: Team, now: datetime | None = None) -> BrainSnapshot:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=LOOKBACK_DAYS)

    devapps = await _latest_devapp_snapshots(db, team.id)
    engagement = await _engagement_7d(db, team.id, since)
    outcomes = await _post_outcomes_7d(db, team.id, since)
    followers = await _latest_followers(db, team.id)
    trends = await _hot_trends(db, since)

    # Channels = union of every surface that knows about an account.
    platforms: dict[str, ChannelState] = {}

    for payload in devapps.values():
        plat = str(payload.get("platform") or "").lower()
        if not plat:
            continue
        state = platforms.setdefault(plat, ChannelState(platform=plat))
        state.username = str(payload.get("username") or state.username or "")
        ins = payload.get("insights") or {}
        f = (
            ins.get("followers")
            or (ins.get("profile") or {}).get("followers")
            or (ins.get("profile") or {}).get("followers_count")
            or (ins.get("organization") or {}).get("followers")
            or (ins.get("creator_info") or {}).get("follower_count")
        )
        if isinstance(f, int | float):
            state.followers = int(f)
        healthy, reason, days = _snapshot_health(payload, now)
        state.healthy = healthy
        state.health_reason = reason
        state.days_to_expiry = days

    for plat in set(engagement) | set(outcomes) | set(followers):
        plat = str(plat).lower()
        state = platforms.setdefault(plat, ChannelState(platform=plat))
        if state.followers is None and plat in followers:
            state.followers = followers[plat]
        eng = engagement.get(plat)
        if eng:
            state.engagement_rate_7d = eng["er"]
            state.posts_7d = eng["n"]
        out = outcomes.get(plat) or {}
        state.published_7d = int(out.get("published", 0))
        state.failed_7d = int(out.get("failed", 0))

    return BrainSnapshot(
        team_id=team.id,
        generated_at=now,
        channels=platforms,
        trends=trends,
    )


# ── decision (pure) ───────────────────────────────────────────────────


def _norm(v: float, cap: float) -> float:
    return min(max(v, 0.0), 1.0) if cap > 0 else 0.0


def _channel_score(state: ChannelState, trend_boost: float) -> float:
    followers_term = _norm(math.log10(max(state.followers or 0, 1) + 1) / 5.0, 1.0)
    er_term = _norm(state.engagement_rate_7d / 10.0, 1.0)
    return _W_FOLLOWERS * followers_term + _W_ENGAGEMENT * er_term + _W_TREND * trend_boost


def _top_trends(trends: list[dict], n: int) -> list[dict]:
    return trends[:n]


def decide(snapshot: BrainSnapshot) -> list[BrainDecision]:
    """Rank channels and emit the day's action plan. Pure — no I/O."""
    decisions: list[BrainDecision] = []

    healthy: list[tuple[float, ChannelState]] = []
    for state in snapshot.channels.values():
        if state.healthy is False:
            decisions.append(
                BrainDecision(
                    platform=state.platform,
                    kind="health",
                    instruction="",
                    priority=90,
                    reason=state.health_reason or "account unhealthy",
                )
            )
            continue
        healthy.append((0.0, state))

    trends = _top_trends(snapshot.trends, MAX_TREND_ACTIONS)
    # Trend boost is global market momentum — it lifts every healthy
    # channel's score proportionally to the strongest signal's traffic.
    trend_boost = 0.0
    if trends:
        top = trends[0]
        trend_boost = _norm(math.log10(max(int(top.get("traffic") or 0), 1) + 1) / 4.0, 1.0)

    scored = sorted(
        ((_channel_score(s, trend_boost), s) for _, s in healthy),
        key=lambda pair: pair[0],
        reverse=True,
    )

    post_count = 0
    trend_iter = iter(trends)
    current_trend = next(trend_iter, None)

    for score, state in scored:
        if post_count >= MAX_POST_ACTIONS:
            break
        total_outcomes = state.published_7d + state.failed_7d
        if total_outcomes > 0 and state.failed_7d > state.published_7d:
            # Majority-failure channel — don't feed it more until healed.
            decisions.append(
                BrainDecision(
                    platform=state.platform,
                    kind="health",
                    instruction="",
                    priority=80,
                    reason=f"{state.failed_7d} failed vs {state.published_7d} published in {LOOKBACK_DAYS}d",
                )
            )
            continue

        trend_query = ""
        instruction = (
            f"Post a high-engagement update for our {state.platform} audience "
            f"({state.followers or '?'} followers) grounded in our real build "
            f"outcomes — concrete numbers, no hype."
        )
        reason = f"score={score:.3f} er7d={state.engagement_rate_7d:.1f} posts7d={state.posts_7d}"
        if state.days_to_expiry is not None and state.days_to_expiry <= _EXPIRY_WARN_DAYS:
            reason += f" expiry_warn={state.days_to_expiry:.1f}d"

        # Market adjustment: rotate top trend signals across the best
        # channels so each day's plan carries live market context.
        if current_trend and post_count < MAX_TREND_ACTIONS:
            trend_query = str(current_trend.get("query") or "")
            cls = current_trend.get("classification") or ""
            instruction = (
                f"Trend-jack '{trend_query}' ({cls}) for our {state.platform} "
                f"audience: connect it to a real Cloudless build outcome with "
                f"concrete numbers — timely, expert angle, one clear CTA."
            )
            reason += f" trend={trend_query}({cls})"
            current_trend = next(trend_iter, None)

        decisions.append(
            BrainDecision(
                platform=state.platform,
                kind="post",
                instruction=instruction,
                priority=int(100 * score),
                reason=reason,
                trend_query=trend_query,
            )
        )
        post_count += 1

    decisions.sort(key=lambda d: (d.kind != "post", -d.priority))
    return decisions


# ── LLM refinement ────────────────────────────────────────────────────
#
# The rule scorer above is the floor, not the ceiling: when
# CENTRAL_BRAIN_LLM_ENABLED is on, the post decisions are rewritten by the
# inference chain (DMR → Cloudflare fallback) using three knowledge
# surfaces the rules can't see — company-brain recall of what actually
# happened, the Kakkava doctrine is left to generation, and the canonical
# per-platform audience-fit lines. Health decisions are never touched and
# any failure returns None so the rule plan dispatches unchanged.

_LLM_DECISION_TIMEOUT = 90.0

_LLM_PROMPT = """You are the central brain of Cloudless's multi-platform social operation
(cloudless.gr — managed cloud hosting, Cloudflare, fast websites). Below is
today's channel snapshot, live trend signals, recent verified outcomes from
the company journal, per-platform audience-fit notes, and a rule-based
draft plan.

Refine the draft plan into the final post instructions for today's content generator.

Rules:
- Only use platforms present in CANDIDATES — never add one, you may drop one.
- instruction: one concrete directive — what to post, what angle, which real Cloudless build outcome or number to cite. Real outcomes only, no hype.
- priority: integer 1-100 ranking expected impact.
- Output STRICT JSON only, no prose: {{"posts":[{{"platform":"...","instruction":"...","priority":N}}]}}
- At most {max_posts} posts.

CHANNELS (7d outcomes):
{channels}

HOT TRENDS:
{trends}

COMPANY JOURNAL (recent verified outcomes — build on what grew, avoid what failed):
{learnings}

PLATFORM AUDIENCE FIT:
{fit}

CANDIDATE PLAN (rule-based):
{candidates}"""


def _fit_line(platform: str) -> str:
    """The 'Audience fit:' tail of a platform's PLATFORM_GUIDES entry."""
    try:
        from app.services.content_guides import PLATFORM_GUIDES

        guide = PLATFORM_GUIDES.get(platform, "")
        idx = guide.find("Audience fit:")
        if idx < 0:
            return ""
        return guide[idx + len("Audience fit:") :].strip().rstrip(".")
    except Exception:  # noqa: BLE001 — fit lines are advisory
        return ""


def _parse_llm_decisions(text: str, candidates: list[BrainDecision]) -> list[BrainDecision] | None:
    """Extract and validate the LLM's post decisions. Pure — no I/O.

    A decision survives only if its platform was a rule-approved candidate
    (unhealthy/majority-failure channels can never be re-added), its
    instruction is non-empty, and it isn't a duplicate platform. Anything
    unparseable returns None so callers fall back to the rule plan.
    """
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    posts = payload.get("posts") if isinstance(payload, dict) else None
    if not isinstance(posts, list):
        return None

    by_platform = {d.platform: d for d in candidates}
    out: list[BrainDecision] = []
    seen: set[str] = set()
    for item in posts:
        if not isinstance(item, dict):
            continue
        platform = str(item.get("platform") or "").lower()
        instruction = str(item.get("instruction") or "").strip()
        if platform not in by_platform or platform in seen or not instruction:
            continue
        base = by_platform[platform]
        try:
            priority = int(item.get("priority", base.priority))
        except (TypeError, ValueError):
            priority = base.priority
        out.append(
            BrainDecision(
                platform=platform,
                kind="post",
                instruction=instruction[:1500],
                format=base.format,
                priority=max(1, min(100, priority)),
                reason=f"llm-refined | rule={base.reason}"[:300],
                trend_query=base.trend_query,
            )
        )
        seen.add(platform)
        if len(out) >= MAX_POST_ACTIONS:
            break
    return out or None


async def _llm_adjust(
    db: AsyncSession,
    team: Team,
    snapshot: BrainSnapshot,
    candidates: list[BrainDecision],
) -> list[BrainDecision] | None:
    """Rewrite candidate post decisions via the inference chain.

    Returns None on any failure — the rule plan is the guaranteed floor.
    """
    if not settings.CENTRAL_BRAIN_LLM_ENABLED or not candidates:
        return None
    try:
        from app.services.company_brain import recall_cited
        from app.services.inference import call_inference

        recalled = await recall_cited(
            team.id,
            "publish outcomes failures engagement follower growth",
            n_results=3,
        )
        learnings = "\n".join(f"- {h['text'][:300]}" for h in recalled.get("hits", [])) or "(no journal history yet)"

        channel_lines = []
        for plat in sorted({d.platform for d in candidates}):
            state = snapshot.channels.get(plat)
            if not state:
                continue
            channel_lines.append(
                f"- {plat}: followers={state.followers or '?'} "
                f"er7d={state.engagement_rate_7d:.1f} posts7d={state.posts_7d} "
                f"published7d={state.published_7d} failed7d={state.failed_7d}"
            )
        trend_lines = "\n".join(
            f"- {t.get('query')} ({t.get('classification')}, traffic={t.get('traffic')})"
            for t in snapshot.trends[:5]
        ) or "(none)"
        fit_lines = "\n".join(
            f"- {plat}: {_fit_line(plat) or 'no fit data'}"
            for plat in sorted({d.platform for d in candidates})
        )
        candidate_lines = "\n".join(
            f"- {d.platform} p{d.priority}: {d.instruction[:200]}"
            for d in candidates
        )

        prompt = _LLM_PROMPT.format(
            max_posts=MAX_POST_ACTIONS,
            channels="\n".join(channel_lines) or "(none)",
            trends=trend_lines,
            learnings=learnings,
            fit=fit_lines,
            candidates=candidate_lines,
        )
        resp = await asyncio.wait_for(
            call_inference(
                prompt,
                provider_name="dmr",
                db=db,
                team_id=team.id,
                max_tokens=1200,
                allow_fallback=True,
            ),
            timeout=_LLM_DECISION_TIMEOUT,
        )
        text = ""
        if isinstance(resp, dict):
            text = str(resp.get("text") or resp.get("response") or "")
            if not text.strip() and isinstance(resp.get("posts"), list):
                text = json.dumps({"posts": resp["posts"]})
        refined = _parse_llm_decisions(text, candidates)
        if refined:
            logger.info(
                "central_brain llm adjust: %d -> %d post decisions",
                len(candidates),
                len(refined),
            )
        else:
            logger.warning("central_brain llm adjust produced no usable decisions")
        return refined
    except Exception as exc:  # noqa: BLE001 — rules are the floor
        logger.warning("central_brain llm adjust failed: %s", str(exc)[:160])
        return None


# ── run ───────────────────────────────────────────────────────────────


async def _journal_plan(db: AsyncSession, team: Team, snapshot: BrainSnapshot, decisions: list[BrainDecision]) -> None:
    """Write the day's plan into company_brain so recall() can cite it."""
    try:
        from app.services.company_brain import remember

        lines = [
            f"Central brain plan {snapshot.generated_at.date().isoformat()} — "
            f"{len(snapshot.channels)} channels, {len(snapshot.trends)} hot trends, "
            f"{sum(1 for d in decisions if d.kind == 'post')} post actions."
        ]
        for d in decisions:
            tag = "POST" if d.kind == "post" else "HEALTH"
            lines.append(f"- [{tag}] {d.platform} p{d.priority}: {d.reason}")
        await remember(
            team.id,
            f"central-brain-{snapshot.generated_at.date().isoformat()}",
            "\n".join(lines)[:6000],
            {"kind": "central_brain", "day": snapshot.generated_at.date().isoformat()},
        )
    except Exception as exc:  # noqa: BLE001 — journaling must not break dispatch
        logger.warning("central_brain journal failed for team %s: %s", team.id, str(exc)[:160])


async def run_for_team(db: AsyncSession, team: Team, now: datetime | None = None) -> dict:
    """Collect → decide → journal → dispatch for one team."""
    from app.services.monetization_executor import schedule_report_actions
    from app.services.monetization_report import MonetizationAction

    snapshot = await collect_snapshot(db, team, now=now)
    decisions = decide(snapshot)

    # LLM refinement rewrites only the post instructions — health
    # decisions and the candidate platform set stay rule-bound.
    health_decisions = [d for d in decisions if d.kind != "post"]
    post_decisions = [d for d in decisions if d.kind == "post"][:MAX_POST_ACTIONS]
    refined = await _llm_adjust(db, team, snapshot, post_decisions)
    if refined:
        post_decisions = refined
    post_decisions.sort(key=lambda d: -d.priority)
    decisions = sorted(
        post_decisions + health_decisions,
        key=lambda d: (d.kind != "post", -d.priority),
    )
    await _journal_plan(db, team, snapshot, decisions)

    decision_rows = [{"platform": d.platform, "kind": d.kind, "priority": d.priority, "reason": d.reason} for d in decisions]
    if not post_decisions:
        return {
            "team_id": str(team.id),
            "channels": len(snapshot.channels),
            "trends": len(snapshot.trends),
            "posts_dispatched": 0,
            "decisions": decision_rows,
        }

    actions = [
        MonetizationAction(
            platform=d.platform,
            instruction=d.instruction,
            format=d.format,
            extra_meta={
                "brain_priority": d.priority,
                "brain_reason": d.reason[:300],
                "brain_trend": d.trend_query,
            },
        )
        for d in post_decisions
    ]
    results = await schedule_report_actions(
        db,
        team,
        actions,
        report_date=snapshot.generated_at.date().isoformat(),
        source="central-brain",
        dedup_field="central_brain_action",
        max_actions=MAX_POST_ACTIONS,
    )
    return {
        "team_id": str(team.id),
        "channels": len(snapshot.channels),
        "trends": len(snapshot.trends),
        "posts_dispatched": len(post_decisions),
        "scheduled": results,
        "decisions": decision_rows,
    }
