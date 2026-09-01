"""AI Brand Voice Analyzer — analyze content samples and extract a voice signature.

Sends 1-5 content samples to Cloudflare Workers AI with a structured prompt
that returns tone dimensions, messaging pillars, banned/preferred phrases,
and a voice signature.
"""

import logging

from app.services.inference import call_inference

logger = logging.getLogger(__name__)

MAX_SAMPLE_CHARS = 4000


async def analyze_brand_voice(samples: list[str]) -> dict:
    """Analyze content samples and return a voice signature.

    Args:
        samples: 1-5 content pieces (blog posts, social media posts, website copy)

    Returns:
        Dict with tone_dimensions, messaging_pillars, banned_phrases,
        preferred_phrases, and voice_signature.
    """
    if not samples:
        return {}

    # Combine samples with separators
    combined = "\n\n---\n\n".join(s.strip()[:MAX_SAMPLE_CHARS] for s in samples if s.strip())
    if not combined:
        return {}

    prompt = f"""Analyze the brand voice from the following content samples.
Identify the tone, messaging themes, phrases the brand prefers, and phrases it would never use.

Content samples:
---
{combined[:6000]}
---

Return a JSON object with these fields:
{{
  "tone_dimensions": {{
    "formality": 1-5 (1=very casual, 5=very formal),
    "playfulness": 1-5 (1=very serious, 5=very playful),
    "authority": 1-5 (1=humble, 5=highly authoritative),
    "friendliness": 1-5 (1=distant, 5=very friendly),
    "technical": 1-5 (1=simple language, 5=highly technical)
  }},
  "messaging_pillars": [
    {{"pillar": "name of recurring theme", "description": "one sentence describing this theme"}}
  ],
  "banned_phrases": ["3-5 phrases this brand would NEVER use based on the content tone"],
  "preferred_phrases": ["3-5 phrases or words the brand uses frequently or naturally"],
  "voice_signature": {{
    "tone": "one word describing the overall tone",
    "style": "short description of writing style",
    "persona": "who the brand sounds like when speaking"
  }}
}}

Return ONLY the JSON object, no other text."""

    try:
        result = await call_inference(
            prompt=prompt,
            provider_name="cloudflare",
            schema={
                "type": "object",
                "properties": {
                    "tone_dimensions": {"type": "object"},
                    "messaging_pillars": {"type": "array", "items": {"type": "object"}},
                    "banned_phrases": {"type": "array", "items": {"type": "string"}},
                    "preferred_phrases": {"type": "array", "items": {"type": "string"}},
                    "voice_signature": {"type": "object"},
                },
            },
        )
        return result.get("response", result)
    except Exception as e:
        logger.warning("AI voice analysis failed: %s", e)
        return {}
