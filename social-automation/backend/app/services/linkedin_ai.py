"""AI-powered LinkedIn content generation.

All generation flows use the configured inference provider (default Cloudflare
Workers AI) and the plain-English fixer so output is suitable for cloudless.gr's
Company Page audience.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.services.cf_models import CF_TEXT_FREE
from app.services.inference import call_inference
from app.services.plain_english import (
    PLAIN_ENGLISH_RULES,
    build_linkedin_caption,
    extract_rewritten_only,
    rewrite_plain_english,
)

_DEFAULT_PROVIDER = "cloudflare"
_LINKEDIN_GUIDE = (
    "LinkedIn professional audience. Write in plain everyday English. "
    "Use line breaks for readability. 3-5 relevant hashtags. "
    "Keep the main body under 250 words. No jargon or buzzwords."
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
- hashtags: array of 3-8 relevant hashtags without the # symbol (or empty if include_hashtags is false)
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
    count = max(1, min(15, int(count)))

    prompt = f"""Suggest {count} relevant, professional LinkedIn hashtags for this post:

"{content}"

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


async def suggest_best_time_to_post(
    *,
    account_type: str = "organization",
    timezone: str = "Europe/Athens",
) -> list[dict]:
    """Return LinkedIn best-time-to-post windows.

    In the future this can be personalized by historical engagement; for now it
    returns the well-known professional-audience windows in the requested
    timezone.
    """
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
- hashtags: array of 3-8 relevant hashtags without #"""

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
