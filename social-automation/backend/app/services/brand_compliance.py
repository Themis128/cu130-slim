"""Brand Compliance Scorer — score content against brand guidelines.

Checks banned phrases, preferred phrases, and uses AI to score
tone/voice match. Returns a 1-5 score with issues and suggested fixes.
"""

import logging

from app.services.inference import call_inference

logger = logging.getLogger(__name__)


async def score_brand_compliance(
    content: str,
    brand: dict,
    voice: dict | None = None,
    platform: str | None = None,
) -> dict:
    """Score content against brand guidelines.

    Args:
        content: The content text to score.
        brand: Brand dict with name, positioning_statement, mission, values, etc.
        voice: BrandVoice dict with tone_dimensions, banned_phrases, preferred_phrases, etc.
        platform: Optional platform name (linkedin, twitter, etc.) for context.

    Returns:
        Dict with:
        - score: 1-5 overall compliance score
        - issues: list of {type, message, suggestion}
        - banned_found: list of banned phrases found in content
        - preferred_found: list of preferred phrases found in content
        - tone_match: 1-5 score for tone/voice match
    """
    voice = voice or {}
    issues: list[dict] = []
    banned_found: list[str] = []
    preferred_found: list[str] = []

    content_lower = content.lower()

    # 1. Check banned phrases (exact match, case-insensitive)
    banned_phrases = voice.get("banned_phrases", [])
    for phrase in banned_phrases:
        if phrase.lower() in content_lower:
            banned_found.append(phrase)
            issues.append({
                "type": "banned_phrase",
                "message": f'Banned phrase "{phrase}" found in content',
                "suggestion": f'Replace "{phrase}" with an alternative that fits your brand voice',
            })

    # 2. Check preferred phrases (presence bonus)
    preferred_phrases = voice.get("preferred_phrases", [])
    for phrase in preferred_phrases:
        if phrase.lower() in content_lower:
            preferred_found.append(phrase)

    # 3. AI tone/voice match scoring
    tone_match = await _ai_tone_match(content, brand, voice, platform)

    # 4. Calculate overall score
    score = _calculate_score(banned_found, preferred_found, tone_match, banned_phrases, preferred_phrases)

    return {
        "score": score,
        "issues": issues,
        "banned_found": banned_found,
        "preferred_found": preferred_found,
        "tone_match": tone_match,
        "suggestions": tone_match.get("suggestions", []) if isinstance(tone_match, dict) else [],
    }


def _calculate_score(
    banned_found: list[str],
    preferred_found: list[str],
    tone_match: dict | int,
    total_banned: list[str],
    total_preferred: list[str],
) -> int:
    """Calculate overall 1-5 compliance score."""
    score = 5

    # Deduct for banned phrases (heavy penalty)
    score -= len(banned_found) * 2

    # Bonus for preferred phrases (up to +1)
    if total_preferred and len(preferred_found) >= 2:
        score += 1
    elif total_preferred and len(preferred_found) >= 1:
        score += 0

    # Factor in AI tone match
    if isinstance(tone_match, dict):
        ai_score = tone_match.get("score", 3)
    elif isinstance(tone_match, (int, float)):
        ai_score = tone_match
    else:
        ai_score = 3

    # Blend: 60% current score, 40% AI tone match
    score = round(score * 0.6 + ai_score * 0.4)

    return max(1, min(5, score))


async def _ai_tone_match(content: str, brand: dict, voice: dict, platform: str | None) -> dict:
    """Use AI to score how well the content matches the brand voice."""
    brand_name = brand.get("name", "the brand")
    positioning = brand.get("positioning_statement", "")
    tone_dims = voice.get("tone_dimensions", {})
    example = voice.get("example_content", "")
    messaging_pillars = voice.get("messaging_pillars", [])

    platform_ctx = f" Platform context: {platform}." if platform else ""

    prompt = f"""You are a brand compliance checker. Score how well the following content matches the brand voice.

Brand: {brand_name}
Positioning: {positioning}
Tone dimensions (1-5): {tone_dims}
Messaging pillars: {messaging_pillars}
Example on-brand content: {example[:300] if example else "N/A"}
{platform_ctx}

Content to score:
---
{content[:2000]}
---

Return a JSON object:
{{
  "score": 1-5 (1=completely off-brand, 5=perfectly on-brand),
  "issues": ["specific issues if any"],
  "suggestions": ["specific suggestions to improve brand alignment"]
}}

Return ONLY the JSON object."""

    try:
        result = await call_inference(
            prompt=prompt,
            provider_name="cloudflare",
            schema={
                "type": "object",
                "properties": {
                    "score": {"type": "number"},
                    "issues": {"type": "array", "items": {"type": "string"}},
                    "suggestions": {"type": "array", "items": {"type": "string"}},
                },
            },
        )
        data = result.get("response", result)
        return data
    except Exception as e:
        logger.warning("AI tone match failed: %s", e)
        return {"score": 3, "issues": [], "suggestions": []}


def build_brand_system_prompt(brand: dict, voice: dict | None = None, visual: dict | None = None) -> str:
    """Build a system prompt that enforces brand identity for AI content generation.

    Args:
        brand: Brand dict with name, positioning_statement, mission, values, etc.
        voice: BrandVoice dict with tone_dimensions, banned_phrases, etc.
        visual: BrandVisual dict (optional, for image generation context).

    Returns:
        A system prompt string to prepend to AI generation calls.
    """
    voice = voice or {}
    name = brand.get("name", "the brand")
    positioning = brand.get("positioning_statement", "")
    mission = brand.get("mission", "")
    values = brand.get("values", [])
    tagline = brand.get("tagline", "")
    target_audience = brand.get("target_audience", {})

    tone_dims = voice.get("tone_dimensions", {})
    banned = voice.get("banned_phrases", [])
    preferred = voice.get("preferred_phrases", [])
    pillars = voice.get("messaging_pillars", [])
    example = voice.get("example_content", "")
    signature = voice.get("voice_signature", {})

    parts: list[str] = [f"You are writing content for {name}."]

    if tagline:
        parts.append(f"Brand tagline: {tagline}")
    if positioning:
        parts.append(f"Brand positioning: {positioning}")
    if mission:
        parts.append(f"Brand mission: {mission}")
    if values:
        parts.append(f"Brand values: {', '.join(values)}")
    if target_audience:
        parts.append(f"Target audience: {target_audience}")

    if tone_dims:
        tone_desc = ", ".join(f"{k}: {v}/5" for k, v in tone_dims.items())
        parts.append(f"Voice & tone dimensions: {tone_desc}")
    if signature:
        parts.append(f"Voice signature: {signature}")
    if pillars:
        pillar_names = [p.get("pillar", p.get("title", "")) for p in pillars if isinstance(p, dict)]
        parts.append(f"Messaging pillars: {', '.join(pillar_names)}")

    if banned:
        parts.append(f"NEVER use these phrases: {', '.join(banned)}")
    if preferred:
        parts.append(f"Prefer these phrases: {', '.join(preferred)}")

    if example:
        parts.append(f"Example of on-brand content: {example[:300]}")

    parts.append("Write content that is on-brand, authentic, and matches the voice described above.")

    return "\n\n".join(parts)
