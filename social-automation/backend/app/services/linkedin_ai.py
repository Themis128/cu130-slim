"""AI-powered LinkedIn content generation.

All generation flows use the configured inference provider (default Cloudflare
Workers AI) and the plain-English fixer so output is suitable for cloudless.gr's
Company Page audience.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException

from app.services.cf_models import CF_TEXT_FREE
from app.services.inference import call_inference
from app.services.plain_english import (
    PLAIN_ENGLISH_RULES,
    build_linkedin_caption,
    extract_rewritten_only,
    rewrite_plain_english,
)

_DEFAULT_PROVIDER = "dmr"
_LINKEDIN_GUIDE = (
    "LinkedIn professional audience. Write in plain everyday English. "
    "SEO: place the primary keyword in the first 140 characters (the mobile preview cutoff) — "
    "this is the hook that earns the 'see more' click. Use 2-3 semantic keyword variations "
    "throughout the body so LinkedIn's search engine can index the post. "
    "Structure for dwell time: hook → value proposition → substantive content (story, framework, "
    "or data) → closing question that invites a reply. Make the post save-worthy: include a "
    "checklist, framework, or data point someone would reference later. "
    "Use line breaks for readability. 3-5 hashtags only — more than 5 triggers spam-like signals. "
    "Mix niche (10K-500K posts), mid-tier, and one branded tag. Hashtags must be semantically "
    "relevant to the post text. Keep the main body under 250 words. No jargon or buzzwords."
)


async def _call_text(
    prompt: str,
    schema: dict | None,
    *,
    provider: str = _DEFAULT_PROVIDER,
    model: str | None = None,
    db=None,
    team_id=None,
    max_tokens: int | None = None,
) -> dict:
    """Call the inference provider and normalize the response."""
    try:
        return await call_inference(
            prompt,
            provider_name=provider,
            db=db,
            team_id=team_id,
            schema=schema,
            model_override=model or CF_TEXT_FREE,
            max_tokens=max_tokens,
            platform="linkedin",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"LinkedIn AI inference failed: {exc}")


async def generate_linkedin_post(
    topic: str,
    *,
    tone: str = "professional",
    length: str = "medium",
    include_hashtags: bool = True,
    include_site_link: bool = True,
    site: str = "www.cloudless.gr",
    provider: str = _DEFAULT_PROVIDER,
    model: str | None = None,
    db=None,
    team_id=None,
) -> dict:
    """Generate a LinkedIn post caption + hashtags, rewritten in plain English."""
    prompt = f"""Write a LinkedIn post about: "{topic}"

{_LINKEDIN_GUIDE}

Tone: {tone}
Length: {length} (short = 100-150 words, medium = 150-250 words, long = 250-300 words)
Include hashtags: {include_hashtags}

{PLAIN_ENGLISH_RULES}

Return JSON with:
- content: the post body (no hashtags, plain English)
- hashtags: array of 3-5 relevant hashtags without the # symbol (or empty if include_hashtags is false).
  Mix niche (10K-500K posts), mid-tier, and one branded tag. All hashtags must be semantically
  relevant to the post content.
- title: an optional short title/lead line"""

    schema = {
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
            "title": {"type": "string"},
        },
        "required": ["content", "hashtags"],
    }

    result = await _call_text(prompt, schema, provider=provider, model=model, db=db, team_id=team_id)

    raw_content = (result.get("title") or "") + "\n\n" + (result.get("content") or "")
    content = await rewrite_plain_english(
        raw_content,
        provider_name=provider,
        model=model,
        db=db,
        team_id=team_id,
        context="LinkedIn post",
        force=False,
    )
    content = extract_rewritten_only(content).strip()

    hashtags = result.get("hashtags") or []
    hashtags = [str(h).lstrip("#").strip() for h in hashtags if str(h).strip()]

    caption = build_linkedin_caption(content, hashtags, site=site if include_site_link else "")

    return {
        "caption": caption,
        "content": content,
        "hashtags": hashtags,
        "topic": topic,
        "tone": tone,
        "length": length,
    }


async def generate_linkedin_article(
    topic: str,
    *,
    tone: str = "professional",
    sections: int = 5,
    include_takeaways: bool = True,
    include_cta: bool = True,
    provider: str = _DEFAULT_PROVIDER,
    model: str | None = None,
    db=None,
    team_id=None,
) -> dict:
    """Generate a long-form LinkedIn article broken into sections.

    LinkedIn does not expose a native Article creation API, so the returned
    ``body`` is intended to be published as a long-form post or copied into
    the LinkedIn editor.
    """
    sections = max(3, min(10, int(sections)))

    prompt = f"""Write a LinkedIn article about: "{topic}"

{_LINKEDIN_GUIDE}

Tone: {tone}
Structure: exactly {sections} short sections with clear headings.
{"End with 2-3 key takeaways." if include_takeaways else ""}
{"End with a short call-to-action." if include_cta else ""}

{PLAIN_ENGLISH_RULES}

Return JSON with:
- title: article title (plain English, no jargon)
- subtitle: one-sentence summary
- sections: array of objects with heading (string) and body (string, 80-150 words)
- takeaways: array of 2-3 plain-English bullet strings (or empty)
- cta: a short call-to-action string (or empty)"""

    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "subtitle": {"type": "string"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "heading": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["heading", "body"],
                },
            },
            "takeaways": {"type": "array", "items": {"type": "string"}},
            "cta": {"type": "string"},
        },
        "required": ["title", "subtitle", "sections", "takeaways", "cta"],
    }

    result = await _call_text(
        prompt,
        schema,
        provider=provider,
        model=model,
        db=db,
        team_id=team_id,
        max_tokens=2048,
    )

    # Rewrite each section body into plain English.
    cleaned_sections: list[dict] = []
    for s in result.get("sections") or []:
        body = await rewrite_plain_english(
            str(s.get("body") or ""),
            provider_name=provider,
            model=model,
            db=db,
            team_id=team_id,
            context="LinkedIn article body",
            force=False,
        )
        cleaned_sections.append(
            {
                "heading": extract_rewritten_only(str(s.get("heading") or "")).strip(),
                "body": extract_rewritten_only(body).strip(),
            }
        )

    body = "\n\n".join(
        f"{s['heading']}\n{s['body']}" for s in cleaned_sections
    )

    title = extract_rewritten_only(result.get("title") or "").strip()
    subtitle = extract_rewritten_only(result.get("subtitle") or "").strip()
    takeaways = [extract_rewritten_only(str(t)).strip() for t in result.get("takeaways") or [] if str(t).strip()]
    cta = extract_rewritten_only(result.get("cta") or "").strip()

    return {
        "title": title,
        "subtitle": subtitle,
        "sections": cleaned_sections,
        "body": body,
        "takeaways": takeaways,
        "cta": cta,
        "topic": topic,
        "tone": tone,
    }


async def generate_linkedin_hashtags(
    content: str,
    *,
    count: int = 5,
    provider: str = _DEFAULT_PROVIDER,
    model: str | None = None,
    db=None,
    team_id=None,
) -> list[str]:
    """Suggest LinkedIn-specific hashtags for the provided content."""
    # LinkedIn 2026 best practice: 3-5 hashtags max. More than 5 triggers spam-like signals.
    count = max(1, min(5, int(count)))

    prompt = f"""Suggest {count} relevant, professional LinkedIn hashtags for this post:

"{content}"

HASHTAG STRATEGY (2026 best practices):
- Choose hashtags semantically relevant to the post content. A disconnect between text and
  hashtags harms distribution.
- Prefer niche and mid-tier hashtags (10K-500K posts) over mega-tags — they outperform 3:1
  on reach-to-engagement ratio because content can actually compete there.
- Include one branded hashtag (e.g. cloudless) when relevant.
- Do not include generic spam-like tags (e.g. #motivation, #follow) unless directly relevant.

Return JSON with: hashtags (array of strings without #)"""

    schema = {
        "type": "object",
        "properties": {
            "hashtags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["hashtags"],
    }

    result = await _call_text(prompt, schema, provider=provider, model=model, db=db, team_id=team_id)
    return [str(h).lstrip("#").strip() for h in result.get("hashtags") or [] if str(h).strip()][:count]


# 00:00–05:59 local — recurring automation publishes overnight, so a winning
# overnight bucket is a scheduler artifact, not audience signal (PR #143).
_DEAD_NIGHT_HOURS = frozenset(range(0, 6))
# A joint (weekday, hour) bucket needs 2+ posts before its mean ER is a
# signal rather than one post's outcome — the joint analogue of
# insights_engine._best()'s min_n=3 on 1-dim buckets.
_MIN_SAMPLES_PER_SLOT = 2
# Same margin rule as insights_engine._best(): the winner must beat the
# account's mean ER by 25% to count as a real signal.
_MARGIN = 1.25
_MAX_WINDOWS = 5


def rank_best_time_windows(
    samples: list[tuple[datetime, float]],
    timezone: str = "Europe/Athens",
) -> list[dict]:
    """Rank (weekday, hour) posting windows by average engagement rate.

    ``samples`` is a list of ``(published_at, engagement_rate)`` pairs — one
    per published post (typically the post's lifetime/max engagement).
    Timestamps are bucketed in ``timezone`` (published_at is stored in UTC).

    Guards (mirroring insights_engine._best + PR #143):
    - dead-night local hours are excluded (scheduler artifacts)
    - a slot needs >= ``_MIN_SAMPLES_PER_SLOT`` posts to rank
    - only slots beating the account's mean ER are returned; "high"
      confidence requires the ``_MARGIN`` margin over the mean
    Returns [] when nothing qualifies — callers fall back to defaults.
    """
    from collections import defaultdict
    from zoneinfo import ZoneInfo

    if len(samples) < 10:
        return []

    tz = ZoneInfo(timezone)
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    engagement_by_slot: dict[tuple[int, int], list[float]] = defaultdict(list)

    for published_at, rate in samples:
        if published_at is None:
            continue
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)
        dt = published_at.astimezone(tz)
        engagement_by_slot[(dt.weekday(), dt.hour)].append(rate)

    mean_er = sum(rate for _, rate in samples) / len(samples)

    # Small-sample shrinkage (empirical-Bayes style, standard analytics
    # practice): a slot's score is pulled toward the account mean in
    # proportion to its sample size, so 1-2 lucky posts can't claim a
    # window. k pseudo-samples of prior belief per slot.
    shrink_k = 4

    def _score(rates: list[float]) -> float:
        n = len(rates)
        return (sum(rates) + shrink_k * mean_er) / (n + shrink_k)

    windows = []
    for (day_idx, hour), rates in sorted(
        engagement_by_slot.items(),
        key=lambda kv: -_score(kv[1]),
    ):
        if len(rates) < _MIN_SAMPLES_PER_SLOT or hour in _DEAD_NIGHT_HOURS:
            continue
        avg = _score(rates)
        if avg < mean_er:
            continue
        windows.append({
            "day": day_names[day_idx],
            "time": f"{hour:02d}:00",
            "timezone": timezone,
            "confidence": "high" if avg >= mean_er * _MARGIN else "medium",
            "avg_engagement_rate": round(avg, 4),
            "sample_size": len(rates),
        })
        if len(windows) >= _MAX_WINDOWS:
            break
    return windows


async def suggest_best_time_to_post(
    *,
    account_type: str = "organization",
    timezone: str = "Europe/Athens",
    samples: list[tuple[datetime, float]] | None = None,
) -> list[dict]:
    """Return LinkedIn best-time-to-post windows.

    When ``samples`` (``(published_at, engagement_rate)`` pairs) qualifies
    under ``rank_best_time_windows``, windows are derived from when those
    posts were published. Otherwise falls back to well-known
    professional-audience windows.

    NOTE: callers must pass the post's *publish* timestamp, not the analytics
    ``captured_at`` — bucketing by capture time only measures the sync cron.
    """
    if samples:
        windows = rank_best_time_windows(samples, timezone)
        if windows:
            return windows

    # Fallback: well-known professional-audience windows
    windows = [
        {"day": "Tuesday", "time": "09:00", "timezone": timezone, "confidence": "high"},
        {"day": "Wednesday", "time": "09:00", "timezone": timezone, "confidence": "high"},
        {"day": "Thursday", "time": "09:00", "timezone": timezone, "confidence": "high"},
        {"day": "Tuesday", "time": "12:00", "timezone": timezone, "confidence": "medium"},
        {"day": "Wednesday", "time": "12:00", "timezone": timezone, "confidence": "medium"},
    ]
    if account_type.lower() in ("person", "personal"):
        windows.extend([
            {"day": "Monday", "time": "17:00", "timezone": timezone, "confidence": "medium"},
            {"day": "Friday", "time": "08:00", "timezone": timezone, "confidence": "medium"},
        ])
    return windows


async def improve_linkedin_post(
    content: str,
    *,
    goal: str = "engagement",
    tone: str = "professional",
    provider: str = _DEFAULT_PROVIDER,
    model: str | None = None,
    db=None,
    team_id=None,
) -> dict:
    """Improve a LinkedIn post for a specific goal (engagement, clarity, leads)."""
    prompt = f"""Improve this LinkedIn post for {goal}.

Original: "{content}"

Tone: {tone}

{_LINKEDIN_GUIDE}

{PLAIN_ENGLISH_RULES}

Return JSON with:
- improved_content: the improved post body (no hashtags)
- changes: array of strings describing what was changed and why
- hashtags: array of 3-5 relevant hashtags without #. Mix niche (10K-500K posts), mid-tier,
  and one branded tag. All hashtags must be semantically relevant to the post content."""

    schema = {
        "type": "object",
        "properties": {
            "improved_content": {"type": "string"},
            "changes": {"type": "array", "items": {"type": "string"}},
            "hashtags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["improved_content", "changes", "hashtags"],
    }

    result = await _call_text(prompt, schema, provider=provider, model=model, db=db, team_id=team_id)

    improved = await rewrite_plain_english(
        result.get("improved_content") or content,
        provider_name=provider,
        model=model,
        db=db,
        team_id=team_id,
        context="LinkedIn post",
        force=False,
    )
    improved = extract_rewritten_only(improved).strip()

    return {
        "improved_content": improved,
        "changes": [str(c) for c in result.get("changes") or []],
        "hashtags": [str(h).lstrip("#").strip() for h in result.get("hashtags") or [] if str(h).strip()],
    }


async def generate_linkedin_comment(
    post_text: str,
    *,
    reply_context: str = "",
    tone: str = "professional",
    length: str = "short",
    provider: str = _DEFAULT_PROVIDER,
    model: str | None = None,
    db=None,
    team_id=None,
) -> str:
    """Generate a plain-English comment or reply to a LinkedIn post."""
    context = f"\n\nContext for the reply: {reply_context}" if reply_context else ""

    prompt = f"""Write a {length} LinkedIn comment in reply to this post:

"{post_text}"{context}

Tone: {tone}

{PLAIN_ENGLISH_RULES}

Return JSON with:
- comment: the comment text only (no hashtags, 1-3 sentences)"""

    schema = {
        "type": "object",
        "properties": {
            "comment": {"type": "string"},
        },
        "required": ["comment"],
    }

    result = await _call_text(prompt, schema, provider=provider, model=model, db=db, team_id=team_id)
    comment = await rewrite_plain_english(
        result.get("comment") or "",
        provider_name=provider,
        model=model,
        db=db,
        team_id=team_id,
        context="LinkedIn comment",
        force=False,
    )
    return extract_rewritten_only(comment).strip()
