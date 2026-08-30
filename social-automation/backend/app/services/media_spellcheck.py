"""Spell/grammar correction for media assets and collections.

Runs LanguageTool over any user-facing or AI-generated text attached to a media
asset so filenames, alt text, captions, tags, and generation prompts are clean
before they are stored, indexed, or rendered.
"""
from app.services.spellcheck import auto_correct


async def correct_text(text: str | None, language: str = "en-US") -> str | None:
    """Correct a single text field; pass through empty values."""
    if not text:
        return text
    return (await auto_correct(text, language=language)).strip()


async def correct_tags(tags: list[str] | None, language: str = "en-US") -> list[str]:
    """Correct each tag individually and return the deduplicated, non-empty list."""
    if not tags:
        return []
    corrected = []
    for tag in tags:
        tag = tag.strip()
        if not tag:
            continue
        fixed = (await auto_correct(tag, language=language)).strip()
        if fixed:
            corrected.append(fixed)
    # Dedupe while preserving order
    seen = set()
    result = []
    for tag in corrected:
        lower = tag.lower()
        if lower not in seen:
            seen.add(lower)
            result.append(tag)
    return result
