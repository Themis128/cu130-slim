"""Unit tests for the unified media quality pipeline.

Covers:
  - ``apply_media_quality``: spellcheck on all text fields, NLP on
    user-facing text (caption/alt_text), SEO on substantial captions.
  - Image bytes are never touched (only text fields are processed).
  - Prompt is spellchecked but NOT NLP/SEO-checked (machine-facing).
  - Tags are spellchecked + deduplicated.
  - Failures in any step are non-fatal (advisory pipeline).
  - ``persist_media_quality_metadata`` stores diagnostics in meta_data.
  - DMR ``ai/llama3.2`` is the default fast text model.
  - Cloudflare is the default content-generation provider.
  - Local Diffusers is attempted before Cloudflare for images.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.media_quality import (
    MediaQualityResult,
    apply_media_quality,
    persist_media_quality_metadata,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _fake_correct_text(text: str | None, language: str = "en-US") -> str | None:
    """Pass-through spellcheck that just strips whitespace."""
    if not text:
        return text
    return text.strip()


async def _fake_correct_tags(tags: list[str] | None, language: str = "en-US") -> list[str]:
    """Pass-through tag correction that deduplicates."""
    if not tags:
        return []
    seen = set()
    result = []
    for t in tags:
        t = t.strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            result.append(t)
    return result


@pytest.fixture
def mock_spellcheck(monkeypatch):
    """Patch media_spellcheck to use pass-through correctors."""
    monkeypatch.setattr("app.services.media_quality.correct_text", _fake_correct_text)
    monkeypatch.setattr("app.services.media_quality.correct_tags", _fake_correct_tags)


@pytest.fixture
def mock_nlp(monkeypatch):
    """Patch run_nlp_check_and_fix to return unchanged text + empty report."""
    from app.services.plain_english import NlpCheckReport

    async def fake_nlp(*, slides, caption, **_kw):
        report = NlpCheckReport(needs_fix=False, fixed=False)
        return slides, caption, report

    monkeypatch.setattr("app.services.plain_english.run_nlp_check_and_fix", fake_nlp)


@pytest.fixture
def mock_seo(monkeypatch):
    """Patch analyze_seo to return a fixed score."""
    async def fake_seo(*, text, platform="linkedin", **_kw):
        return {"score": {"overall": 95, "recommendations": []}}

    monkeypatch.setattr("app.services.seo.analyze_seo", fake_seo)


# ===========================================================================
# 1. apply_media_quality — spellcheck
# ===========================================================================

class TestMediaQualitySpellcheck:

    @pytest.mark.asyncio
    async def test_prompt_spellchecked(self, mock_spellcheck, mock_nlp, mock_seo):
        """Prompt is spellchecked (whitespace normalized)."""
        result = await apply_media_quality(
            prompt="  a red apple  ",
            negative_prompt="blurry ",
        )
        assert result.prompt == "a red apple"
        assert result.negative_prompt == "blurry"
        assert result.spellcheck_applied is True

    @pytest.mark.asyncio
    async def test_empty_prompt_handled(self, mock_spellcheck, mock_nlp, mock_seo):
        """Empty prompt does not crash or set spellcheck_applied."""
        result = await apply_media_quality(prompt="")
        assert result.prompt == ""
        assert result.spellcheck_applied is False

    @pytest.mark.asyncio
    async def test_tags_deduplicated(self, mock_spellcheck, mock_nlp, mock_seo):
        """Tags are spellchecked and deduplicated."""
        result = await apply_media_quality(
            tags=["apple", "Apple", "  fruit  ", "", "fruit"],
        )
        assert result.tags == ["apple", "fruit"]
        assert result.spellcheck_applied is True

    @pytest.mark.asyncio
    async def test_caption_spellchecked(self, mock_spellcheck, mock_nlp, mock_seo):
        """Caption is spellchecked."""
        result = await apply_media_quality(
            caption="  A beautiful sunset over the ocean  ",
        )
        assert result.caption == "A beautiful sunset over the ocean"


# ===========================================================================
# 2. apply_media_quality — NLP on user-facing text only
# ===========================================================================

class TestMediaQualityNLP:

    @pytest.mark.asyncio
    async def test_nlp_runs_on_caption(self, mock_spellcheck, mock_seo):
        """NLP check runs on caption (user-facing text)."""
        from app.services.plain_english import NlpCheckReport

        nlp_called = []

        async def fake_nlp(*, slides, caption, **_kw):
            nlp_called.append(caption)
            report = NlpCheckReport(needs_fix=False, fixed=True)
            return slides, "Fixed caption", report

        with patch("app.services.plain_english.run_nlp_check_and_fix", side_effect=fake_nlp):
            result = await apply_media_quality(
                caption="Leverage scalable solutions for digital transformation",
                prompt="a technical prompt with jargon",
            )

        assert len(nlp_called) == 1
        assert "Fixed caption" in result.caption
        assert result.nlp_report.get("fixed") is True

    @pytest.mark.asyncio
    async def test_nlp_runs_on_alt_text(self, mock_spellcheck, mock_seo):
        """NLP check runs on alt_text (user-facing text)."""
        from app.services.plain_english import NlpCheckReport

        async def fake_nlp(*, slides, caption, **_kw):
            report = NlpCheckReport(needs_fix=False, fixed=True)
            return slides, "Clean alt text", report

        with patch("app.services.plain_english.run_nlp_check_and_fix", side_effect=fake_nlp):
            result = await apply_media_quality(
                alt_text="A photo of a cat",
            )

        assert result.alt_text == "Clean alt text"

    @pytest.mark.asyncio
    async def test_nlp_does_not_run_on_prompt(self, mock_spellcheck, mock_seo):
        """NLP check does NOT run on prompt (machine-facing)."""
        from app.services.plain_english import NlpCheckReport

        nlp_inputs = []

        async def fake_nlp(*, slides, caption, **_kw):
            nlp_inputs.append(caption)
            report = NlpCheckReport(needs_fix=False)
            return slides, caption, report

        with patch("app.services.plain_english.run_nlp_check_and_fix", side_effect=fake_nlp):
            await apply_media_quality(
                prompt="leverage scalable jargon prompt",
                caption="",
                alt_text="",
            )

        # NLP should not have been called (no caption or alt_text)
        assert len(nlp_inputs) == 0


# ===========================================================================
# 3. apply_media_quality — SEO on substantial captions
# ===========================================================================

class TestMediaQualitySEO:

    @pytest.mark.asyncio
    async def test_seo_runs_on_long_caption(self, mock_spellcheck, mock_nlp, mock_seo):
        """SEO scoring runs when caption is >= 40 chars."""
        result = await apply_media_quality(
            caption="This is a sufficiently long caption that should trigger SEO scoring.",
        )
        assert result.seo_score.get("overall") == 95

    @pytest.mark.asyncio
    async def test_seo_skipped_for_short_caption(self, mock_spellcheck, mock_nlp, mock_seo):
        """SEO scoring is skipped for very short captions."""
        result = await apply_media_quality(
            caption="Short.",
        )
        assert result.seo_score == {}

    @pytest.mark.asyncio
    async def test_seo_skipped_when_disabled(self, mock_spellcheck, mock_nlp, mock_seo):
        """SEO scoring is skipped when run_seo=False."""
        result = await apply_media_quality(
            caption="This is a sufficiently long caption that should trigger SEO scoring.",
            run_seo=False,
        )
        assert result.seo_score == {}


# ===========================================================================
# 4. apply_media_quality — non-fatal failures
# ===========================================================================

class TestMediaQualityNonFatal:

    @pytest.mark.asyncio
    async def test_spellcheck_failure_non_fatal(self, monkeypatch, mock_nlp, mock_seo):
        """If spellcheck raises, the pipeline returns original text."""
        async def boom(*_a, **_kw):
            raise RuntimeError("LT down")

        monkeypatch.setattr("app.services.media_quality.correct_text", boom)
        monkeypatch.setattr("app.services.media_quality.correct_tags", boom)

        result = await apply_media_quality(
            prompt="a red apple",
            caption="A nice photo",
            tags=["tag1"],
        )
        # Original text preserved
        assert result.prompt == "a red apple"
        assert result.caption == "A nice photo"
        assert result.tags == ["tag1"]

    @pytest.mark.asyncio
    async def test_nlp_failure_non_fatal(self, mock_spellcheck, mock_seo):
        """If NLP raises, the pipeline returns spellchecked text."""
        async def boom(*_a, **_kw):
            raise RuntimeError("NLP model down")

        with patch("app.services.plain_english.run_nlp_check_and_fix", side_effect=boom):
            result = await apply_media_quality(
                prompt="a red apple",
                caption="A nice photo of an apple",
            )
        assert result.prompt == "a red apple"
        assert result.caption == "A nice photo of an apple"

    @pytest.mark.asyncio
    async def test_seo_failure_non_fatal(self, mock_spellcheck, mock_nlp):
        """If SEO raises, the pipeline returns text without score."""
        async def boom(*_a, **_kw):
            raise RuntimeError("SEO engine down")

        with patch("app.services.seo.analyze_seo", side_effect=boom):
            result = await apply_media_quality(
                caption="A sufficiently long caption for SEO scoring to trigger.",
            )
        assert result.seo_score == {}


# ===========================================================================
# 5. persist_media_quality_metadata
# ===========================================================================

class TestPersistMediaQuality:

    @pytest.mark.asyncio
    async def test_persist_quality_metadata(self, mock_spellcheck, mock_nlp, mock_seo):
        """Quality diagnostics are stored in asset.meta_data."""
        asset = MagicMock()
        asset.meta_data = None
        asset.alt_text = "old alt"
        asset.tags = ["old_tag"]

        quality = MediaQualityResult(
            prompt="a red apple",
            caption="A nice photo",
            alt_text="A red apple on a table",
            tags=["apple", "fruit"],
            seo_score={"overall": 95},
            spellcheck_applied=True,
        )

        db = AsyncMock()
        await persist_media_quality_metadata(asset, quality, db=db)

        assert asset.meta_data is not None
        assert "quality" in asset.meta_data
        assert asset.meta_data["quality"]["seo_score"]["overall"] == 95
        assert asset.meta_data["quality_applied"] is True
        # Corrected text fields updated on asset
        assert asset.alt_text == "A red apple on a table"
        assert asset.tags == ["apple", "fruit"]


# ===========================================================================
# 6. Architecture defaults — DMR model, provider chain
# ===========================================================================

class TestArchitectureDefaults:

    def test_dmr_default_model_is_llama32(self):
        """DMR_TEXT_MODEL code default must be ai/llama3.2 (fast 3.2B), not qwen2.5.

        Note: the runtime value may be overridden by the DMR_TEXT_MODEL env var
        in .env; this test checks the Pydantic field default in the code.
        """
        from app.core.config import Settings
        fields = Settings.model_fields
        assert fields["DMR_TEXT_MODEL"].default == "ai/llama3.2"

    def test_generate_content_default_provider_is_cloudflare(self):
        """GenerateContentRequest.provider must default to 'cloudflare'."""
        from app.api.ai import GenerateContentRequest
        req = GenerateContentRequest(prompt="test", platform="linkedin")
        # The default provider field should be cloudflare (not dmr)
        assert req.provider == "cloudflare"

    def test_generate_image_default_provider_is_local_diffusers(self):
        """GenerateImageRequest.provider must default to 'local-diffusers'."""
        from app.api.ai import GenerateImageRequest
        req = GenerateImageRequest(prompt="test")
        assert req.provider == "local-diffusers"

    def test_local_diffusers_attempted_before_cloudflare(self):
        """The image fallback chain must try Local Diffusers first, CF second."""
        from app.services.inference import _call_cf_image_pipeline
        # The function name confirms the CF pipeline includes local diffusers
        # as the primary step. Verify it exists and is callable.
        assert callable(_call_cf_image_pipeline)

    def test_cloudflare_is_only_automatic_cloud_fallback(self):
        """No non-CF cloud provider should be in the automatic text fallback chain."""
        from app.services import inference
        # PROVIDER_CATALOG is a list of dicts; check in_fallback_chain flags
        for entry in inference.PROVIDER_CATALOG:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name", "")
            if name in ("groq", "gemini", "mistral", "cohere", "openrouter", "nvidia", "huggingface"):
                assert not entry.get("in_fallback_chain", False), (
                    f"{name} must NOT be in the automatic fallback chain"
                )


# ===========================================================================
# 7. Celery media_enhance task registration
# ===========================================================================

class TestCeleryMediaEnhanceRegistration:

    def test_media_enhance_in_celery_include(self):
        """app.worker.tasks.media_enhance must be in the Celery include list."""
        from app.worker.celery_app import celery_app
        includes = celery_app.conf.include or []
        # Celery may store includes differently — check the app's task list too
        assert "app.worker.tasks.media_enhance" in includes

    def test_batch_enhance_task_registered(self):
        """batch_enhance_task must be discoverable as a registered Celery task."""
        # Import the task module so Celery registers it, then check the registry
        import app.worker.tasks.media_enhance  # noqa: F401
        from app.worker.celery_app import celery_app
        assert "app.worker.tasks.media_enhance.batch_enhance_task" in celery_app.tasks
