"""Unified media text quality pipeline for all media-generation paths.

Every endpoint that generates or persists a media asset (image, video, PDF)
runs ``apply_media_quality`` on the **text fields** associated with the asset:

- ``prompt`` / ``negative_prompt`` — the generation prompt (spellchecked only;
  NLP/SEO do not apply to machine-facing prompts).
- ``caption`` / ``ai_caption`` — user-facing descriptive copy (spellcheck +
  NLP plain-English fix + SEO scoring).
- ``alt_text`` — accessibility text (spellcheck + NLP plain-English fix).
- ``tags`` / ``hashtags`` — keyword tags (spellcheck + deduplication).

**Image bytes are never touched.**  Only the associated text fields are
corrected, scored, and returned with a diagnostic report so callers can
persist quality metadata alongside the asset.

The helper is advisory — it never raises.  If any step fails, the best
available version of each field is returned with a diagnostic report.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.cf_models import CF_TEXT_FREE
from app.services.media_spellcheck import correct_tags, correct_text

logger = logging.getLogger(__name__)

# Captions shorter than this are not SEO-scored (too short to be meaningful).
_MIN_SEO_CAPTION_LEN = 40


@dataclass
class MediaQualityResult:
    """Result of the media quality pipeline.

    All text fields are the *corrected* versions ready to persist/return.
    The ``report`` dict contains diagnostics: spellcheck status, NLP issues,
    SEO score, and iteration metadata.
    """

    prompt: str = ""
    negative_prompt: str = ""
    caption: str = ""
    alt_text: str = ""
    tags: list[str] = field(default_factory=list)
    seo_score: dict[str, Any] = field(default_factory=dict)
    nlp_report: dict[str, Any] = field(default_factory=dict)
    spellcheck_applied: bool = False
    improved: bool = False
    iterations: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "caption": self.caption,
            "alt_text": self.alt_text,
            "tags": self.tags,
            "seo_score": self.seo_score,
            "nlp_report": self.nlp_report,
            "spellcheck_applied": self.spellcheck_applied,
            "improved": self.improved,
            "iterations": self.iterations,
        }


async def apply_media_quality(
    *,
    prompt: str = "",
    negative_prompt: str = "",
    caption: str = "",
    alt_text: str = "",
    tags: list[str] | None = None,
    platform: str = "linkedin",
    db: AsyncSession | None = None,
    team_id: Any | None = None,
    provider_name: str = "cloudflare",
    model: str | None = None,
    run_nlp: bool = True,
    run_spellcheck: bool = True,
    run_seo: bool = True,
) -> MediaQualityResult:
    """Apply the full media quality pipeline to media-associated text.

    Parameters
    ----------
    prompt, negative_prompt : str
        The image-generation prompt. Spellchecked only (machine-facing).
    caption, alt_text : str
        User-facing descriptive text. Spellchecked + NLP plain-English fix.
        ``caption`` is also SEO-scored if long enough.
    tags : list[str] | None
        Keyword tags. Spellchecked + deduplicated.
    platform : str
        Target platform for SEO scoring (linkedin, instagram, twitter, …).
    db, team_id : optional
        Database session and team ID for inference calls (NLP fix).
    provider_name, model : optional
        Inference provider and model for NLP fix.

    Returns
    -------
    MediaQualityResult
        Corrected text fields + diagnostic report (SEO score, NLP report,
        spellcheck status, improvement metadata).
    """
    from app.services import seo as seo_service
    from app.services.plain_english import run_nlp_check_and_fix

    tags = tags or []
    result = MediaQualityResult(
        prompt=prompt,
        negative_prompt=negative_prompt,
        caption=caption,
        alt_text=alt_text,
        tags=list(tags),
    )
    effective_model = model or (CF_TEXT_FREE if provider_name == "cloudflare" else None)

    # ── Step 1: Spellcheck all text fields ─────────────────────────────
    if run_spellcheck:
        try:
            if result.prompt:
                fixed = await correct_text(result.prompt)
                if fixed and fixed.strip():
                    result.prompt = fixed
                    result.spellcheck_applied = True
            if result.negative_prompt:
                fixed = await correct_text(result.negative_prompt)
                if fixed and fixed.strip():
                    result.negative_prompt = fixed
                    result.spellcheck_applied = True
            if result.caption:
                fixed = await correct_text(result.caption)
                if fixed and fixed.strip():
                    result.caption = fixed
                    result.spellcheck_applied = True
            if result.alt_text:
                fixed = await correct_text(result.alt_text)
                if fixed and fixed.strip():
                    result.alt_text = fixed
                    result.spellcheck_applied = True
            if result.tags:
                fixed_tags = await correct_tags(result.tags)
                if fixed_tags:
                    result.tags = fixed_tags
                    result.spellcheck_applied = True
        except Exception as exc:
            logger.warning("Media spellcheck failed (non-fatal): %s", exc)

    # ── Step 2: NLP plain-English check + fix on user-facing text ──────
    # Apply to caption and alt_text (user-facing), NOT to the prompt
    # (machine-facing) — prompts can legitimately contain technical terms.
    if run_nlp:
        for field_name, field_value in (("caption", result.caption), ("alt_text", result.alt_text)):
            if not field_value or not field_value.strip():
                continue
            try:
                _slides, fixed_text, nlp_report = await run_nlp_check_and_fix(
                    slides=[],
                    caption=field_value,
                    provider_name=provider_name,
                    model=effective_model,
                    db=db,
                    team_id=team_id,
                    force_fix=True,
                    allow_fallback=True,
                )
                if fixed_text and fixed_text.strip():
                    setattr(result, field_name, fixed_text)
                # Merge NLP reports (caption report takes precedence)
                if field_name == "caption":
                    result.nlp_report = nlp_report.to_dict()
                else:
                    existing = result.nlp_report or {}
                    existing.setdefault("fields", {})
                    existing["fields"][field_name] = nlp_report.to_dict()
                    result.nlp_report = existing
            except Exception as exc:
                logger.warning("Media NLP check on %s failed (non-fatal): %s", field_name, exc)

    # ── Step 3: SEO scoring on caption (if substantial enough) ─────────
    if run_seo and result.caption and len(result.caption) >= _MIN_SEO_CAPTION_LEN:
        try:
            full_text = result.caption
            if result.tags:
                tag_str = " ".join(f"#{h.lstrip('#')}" for h in result.tags)
                full_text = f"{full_text}\n\n{tag_str}"

            seo_result = await seo_service.analyze_seo(
                text=full_text,
                platform=platform,
                db=db,
                team_id=team_id,
            )
            result.seo_score = seo_result.get("score", {})
        except Exception as exc:
            logger.warning("Media SEO scoring failed (non-fatal): %s", exc)

    return result


async def persist_media_quality_metadata(
    asset: Any,
    quality: MediaQualityResult,
    db: AsyncSession,
) -> None:
    """Persist quality diagnostics into a MediaAsset's ``meta_data``.

    Updates the asset's ``meta_data`` dict with:
    - ``quality`` — the full quality report (spellcheck, NLP, SEO).
    - ``quality_applied`` — boolean, True if any quality step ran.

    Also updates ``alt_text`` and ``tags`` on the asset if they were corrected.
    Does NOT touch the image bytes or storage path.
    """
    from sqlalchemy.orm.attributes import flag_modified

    if asset.meta_data is None:
        asset.meta_data = {}

    asset.meta_data["quality"] = quality.to_dict()
    asset.meta_data["quality_applied"] = quality.spellcheck_applied or bool(quality.nlp_report) or bool(quality.seo_score)

    # Update corrected text fields on the asset
    if quality.alt_text and quality.alt_text != asset.alt_text:
        asset.alt_text = quality.alt_text
    if quality.tags and list(quality.tags) != list(asset.tags or []):
        asset.tags = list(quality.tags)

    flag_modified(asset, "meta_data")
    await db.commit()
