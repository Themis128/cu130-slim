"""Shared quality pipeline: NLP plain-English check + spellcheck + SEO scoring + auto-improvement.

Every content-generating endpoint should run ``apply_quality_pipeline`` to ensure
consistent quality across all workflows:

1. **Spellcheck** — ``auto_correct`` via LanguageTool (grammar + spelling).
2. **NLP** — ``run_nlp_check_and_fix`` to flag and rewrite jargon / hard sentences.
3. **SEO** — ``analyze_seo`` to score the content against platform best practices.
4. **Auto-improve** — if the SEO score is below the target (default 90), the
   pipeline regenerates the content with explicit recommendations fed back
   into the LLM prompt, then re-checks. Up to ``max_iterations`` rounds.

The helper is advisory — it never raises. If any step fails, the best available
version of the content is returned with a diagnostic report.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.cf_models import CF_TEXT_FREE
from app.services.spellcheck import auto_correct

logger = logging.getLogger(__name__)


@dataclass
class QualityResult:
    """Result of the quality pipeline."""

    content: str
    hashtags: list[str]
    seo_score: dict[str, Any] = field(default_factory=dict)
    nlp_report: dict[str, Any] = field(default_factory=dict)
    spellcheck_applied: bool = False
    iterations: int = 0
    improved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "hashtags": self.hashtags,
            "seo_score": self.seo_score,
            "nlp_report": self.nlp_report,
            "spellcheck_applied": self.spellcheck_applied,
            "iterations": self.iterations,
            "improved": self.improved,
        }


async def apply_quality_pipeline(
    content: str,
    platform: str,
    hashtags: list[str] | None = None,
    *,
    db: AsyncSession | None = None,
    team_id: Any | None = None,
    provider_name: str = "cloudflare",
    model: str | None = None,
    target_score: int = 90,
    max_iterations: int = 2,
    run_nlp: bool = True,
    run_spellcheck: bool = True,
    run_seo: bool = True,
    topic: str = "",
    tone: str = "professional",
) -> QualityResult:
    """Apply the full quality pipeline to generated content.

    Parameters
    ----------
    content : str
        The generated post caption / body text.
    platform : str
        Target platform (linkedin, instagram, twitter, facebook, threads, tiktok).
    hashtags : list[str] | None
        Hashtags to include in SEO scoring and text assembly.
    db, team_id : optional
        Database session and team ID for inference calls (NLP fix + improve).
    provider_name, model : optional
        Inference provider and model for NLP fix and content improvement.
    target_score : int
        Minimum SEO overall score to accept (default 90).
    max_iterations : int
        Maximum number of improvement rounds (default 2).
    run_nlp, run_spellcheck, run_seo : bool
        Toggle individual quality steps.

    Returns
    -------
    QualityResult
        The final content, hashtags, SEO score, NLP report, and metadata.
    """
    from app.services import seo as seo_service
    from app.services.plain_english import run_nlp_check_and_fix

    hashtags = hashtags or []
    result = QualityResult(content=content, hashtags=hashtags)
    effective_model = model or (CF_TEXT_FREE if provider_name == "cloudflare" else None)

    # ── Step 1: Spellcheck ──────────────────────────────────────────────
    if run_spellcheck and content:
        try:
            corrected = await auto_correct(content)
            if corrected and corrected.strip():
                result.content = corrected
                result.spellcheck_applied = True
        except Exception as exc:
            logger.warning("Spellcheck failed (non-fatal): %s", exc)

    # ── Step 2: NLP plain-English check + fix ───────────────────────────
    if run_nlp and content:
        try:
            # run_nlp_check_and_fix expects slides + caption; we pass empty slides
            # and just the caption so it fixes the body text.
            _slides, fixed_caption, nlp_report = await run_nlp_check_and_fix(
                slides=[],
                caption=result.content,
                provider_name=provider_name,
                model=effective_model,
                db=db,
                team_id=team_id,
                force_fix=True,
                allow_fallback=False,
            )
            if fixed_caption and fixed_caption.strip():
                result.content = fixed_caption
            result.nlp_report = nlp_report.to_dict()
        except Exception as exc:
            logger.warning("NLP check failed (non-fatal): %s", exc)

    # ── Step 3: SEO scoring + auto-improvement loop ─────────────────────
    if run_seo:
        for iteration in range(max_iterations + 1):
            result.iterations = iteration
            try:
                # Assemble text with hashtags for scoring
                full_text = result.content
                if result.hashtags:
                    tag_str = " ".join(f"#{h.lstrip('#')}" for h in result.hashtags)
                    full_text = f"{full_text}\n\n{tag_str}"

                seo_result = await seo_service.analyze_seo(
                    text=full_text,
                    platform=platform,
                    db=db,
                    team_id=team_id,
                )
                result.seo_score = seo_result.get("score", {})
                overall = result.seo_score.get("overall", 0)

                if overall >= target_score or iteration >= max_iterations:
                    break

                # ── Auto-improve: feed recommendations back to LLM ───────
                recs = result.seo_score.get("recommendations", [])
                rec_text = "\n".join(f"- {r}" for r in recs) if recs else "Improve length and hashtag coverage."

                from app.services.inference import call_inference

                improve_prompt = f"""Improve this {platform} post to achieve a high SEO score (target: {target_score}+).

Current content:
"{result.content}"

Current hashtags: {result.hashtags}

SEO issues to fix:
{rec_text}

Current SEO score: {overall}/100

Requirements:
- Write at least 440 characters of natural, engaging content.
- Use plain everyday English (no jargon).
- Keep the same topic and meaning.
- Make it more detailed and valuable to the reader.
- Include a clear hook in the first line.
- End with a call to action.

Return JSON with: content (string), hashtags (array of strings without #)"""

                schema = {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "hashtags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["content", "hashtags"],
                }

                improved = await call_inference(
                    improve_prompt,
                    provider_name=provider_name,
                    db=db,
                    team_id=team_id,
                    schema=schema,
                    model_override=effective_model,
                )

                new_content = improved.get("content", "").strip()
                new_hashtags = improved.get("hashtags", [])

                if new_content and len(new_content) > len(result.content):
                    # Spellcheck the improved content
                    if run_spellcheck:
                        try:
                            new_content = await auto_correct(new_content)
                        except Exception:
                            pass
                    result.content = new_content
                    result.improved = True

                if new_hashtags:
                    result.hashtags = [h.lstrip("#").strip() for h in new_hashtags if h.strip()]

            except Exception as exc:
                logger.warning("SEO scoring iteration %d failed (non-fatal): %s", iteration, exc)
                break

    return result


# ── Decorator for automatic quality enforcement ────────────────────────────

def with_quality(
    *,
    content_field: str = "content",
    hashtags_field: str = "hashtags",
    platform_field: str = "platform",
    target_score: int = 90,
    max_iterations: int = 2,
):
    """Decorator that auto-applies the quality pipeline to endpoint responses.

    Extracts ``content``, ``hashtags``, and ``platform`` from the Pydantic
    response model, runs the full quality pipeline, and patches the response
    with corrected content + SEO score + NLP report.

    Usage::

        @router.post("/generate-content")
        @with_quality(target_score=90)
        async def generate_content(...):
            ...

    The endpoint must return a Pydantic model (or dict) with the fields
    named by ``content_field``, ``hashtags_field``, and ``platform_field``.
    If the platform is not in the response, it falls back to the request's
    ``platform`` field.

    For image-generation endpoints, apply this to the **caption/alt_text**
    field — the image bytes are never touched.
    """
    import functools

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            result = await fn(*args, **kwargs)

            # Extract content, hashtags, platform from the response
            if hasattr(result, "model_dump"):
                data = result.model_dump()
            elif isinstance(result, dict):
                data = result
            else:
                return result  # can't process — return as-is

            content = data.get(content_field, "")
            hashtags = data.get(hashtags_field, [])
            platform = data.get(platform_field, "")

            # If no platform in response, try to get it from the request
            if not platform:
                # Look for a request object in kwargs or args
                for arg in list(args) + list(kwargs.values()):
                    if hasattr(arg, "platform"):
                        platform = arg.platform
                        break
                if not platform:
                    platform = "linkedin"  # safe default

            if not content:
                return result  # nothing to quality-check

            # Find db and team_id from kwargs (FastAPI dependency injection)
            db = kwargs.get("db")
            team_id = None
            # Try to resolve team_id from db if available

            # Run the quality pipeline
            quality = await apply_quality_pipeline(
                content=content,
                platform=platform,
                hashtags=hashtags or [],
                db=db,
                team_id=team_id,
                target_score=target_score,
                max_iterations=max_iterations,
            )

            # Patch the response with quality results
            if hasattr(result, content_field):
                setattr(result, content_field, quality.content)
            elif isinstance(result, dict):
                result[content_field] = quality.content

            if hasattr(result, hashtags_field):
                setattr(result, hashtags_field, quality.hashtags)
            elif isinstance(result, dict):
                result[hashtags_field] = quality.hashtags

            # Add quality metadata if the model supports it
            for meta_field, meta_value in [
                ("seo_score", quality.seo_score or None),
                ("nlp_report", quality.nlp_report or None),
                ("quality", quality.to_dict() if quality.improved else None),
            ]:
                if meta_value is None:
                    continue
                if hasattr(result, meta_field):
                    setattr(result, meta_field, meta_value)
                elif isinstance(result, dict):
                    result[meta_field] = meta_value

            return result

        return wrapper

    return decorator
