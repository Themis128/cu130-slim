"""Image text guard — diffusion models cannot spell.

Guards generation prompts against text-render clauses, detects explicit
"render this text" requests, and QA-checks generated pixels for garbled
in-image text (2026-10-28 LinkedIn post regression).
"""

import contextlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.image_text_guard import (
    apply_no_text_guard,
    check_image_text,
    strip_text_render_requests,
    wants_rendered_text,
)

# The exact production failure: monetization-report brief → garbled
# caption banner baked into the LinkedIn image (activity 7521477348517363712).
PRODUCTION_PROMPT = (
    "Post a high-quality image showcasing a company success story and "
    "employee achievements, with a caption highlighting the company's "
    "expertise and achievements."
)


class TestStripTextRenderRequests:
    def test_production_caption_clause_removed(self):
        stripped = strip_text_render_requests(PRODUCTION_PROMPT)
        assert "caption" not in stripped.lower()
        assert "success story" in stripped  # scene clause survives

    def test_scene_only_prompt_untouched(self):
        prompt = "A sleek modern office space at sunrise, soft blue and silver tones, clean minimal design"
        assert strip_text_render_requests(prompt) == prompt

    def test_ambiguous_scene_verbs_survive(self):
        prompt = "People reading books in a cozy cafe, warm light"
        assert strip_text_render_requests(prompt) == prompt

    def test_empty_safe(self):
        assert strip_text_render_requests("") == ""
        assert strip_text_render_requests(None) is None


class TestApplyNoTextGuard:
    def test_appends_antitext_directives(self):
        guarded = apply_no_text_guard(PRODUCTION_PROMPT)
        assert "NO TEXT" in guarded
        assert "highlighting" not in guarded  # caption clause content gone
        assert "success story" in guarded

    def test_idempotent(self):
        once = apply_no_text_guard(PRODUCTION_PROMPT)
        assert apply_no_text_guard(once) == once

    def test_all_text_clauses_degrade_to_suffix(self):
        guarded = apply_no_text_guard("with a caption, banner with text")
        assert guarded.startswith("absolutely NO TEXT")

    def test_dmr_echoed_clause_stripped(self):
        # The expansion model re-injecting the caption clause is caught too.
        expanded = "A team of colleagues smiling at a conference, with a caption saying Great Job, warm light"
        guarded = apply_no_text_guard(expanded)
        assert "great job" not in guarded.lower()
        assert "conference" in guarded


class TestWantsRenderedText:
    def test_quoted_text_detected(self):
        assert wants_rendered_text('poster with the text "Grand Opening"') is True
        assert wants_rendered_text("a sign that says 'OPEN 24/7'") is True

    def test_text_field_detected(self):
        assert wants_rendered_text("banner, text: Sale 50%") is True

    def test_plain_scene_not_detected(self):
        assert wants_rendered_text(PRODUCTION_PROMPT) is False
        assert wants_rendered_text("team holding a video camera") is False


@pytest.mark.asyncio
class TestCheckImageText:
    async def test_no_visible_text(self):
        with patch("app.services.media_ai._call_dmr_vision", new=AsyncMock(return_value="NONE")):
            result = await check_image_text(b"png-bytes")
        assert result["checked"] is True
        assert result["has_text"] is False
        assert result["garbled"] is False

    async def test_garbled_text_detected(self):
        # "GGr" (dropped-separator) mangling is caught by the pattern-only
        # path, so this is deterministic whether or not LanguageTool is
        # reachable; in production the LanguageTool misspelling path also
        # flags lowercase garble ("exppertals", "successwher").
        with patch("app.services.media_ai._call_dmr_vision", new=AsyncMock(return_value="SuccessWher GGrteams story")):
            result = await check_image_text(b"png-bytes")
        assert result["checked"] is True
        assert result["has_text"] is True
        assert result["garbled"] is True
        assert result["tokens"]

    async def test_clean_text_passes(self):
        with patch("app.services.media_ai._call_dmr_vision", new=AsyncMock(return_value="Welcome aboard")):
            result = await check_image_text(b"png-bytes")
        assert result["garbled"] is False

    async def test_vision_unavailable_is_pass_through(self):
        with patch("app.services.media_ai._call_dmr_vision", new=AsyncMock(side_effect=RuntimeError("dmr down"))):
            result = await check_image_text(b"png-bytes")
        assert result["checked"] is False
        assert result["garbled"] is False

    async def test_empty_bytes(self):
        result = await check_image_text(b"")
        assert result["checked"] is False


@contextlib.asynccontextmanager
async def _fake_gpu_lock():
    yield


class TestMonetizationGarbleFallback:
    @pytest.mark.asyncio
    async def test_garbled_once_retried_clean(self):
        from app.services import monetization_executor

        # first check garbled → retry; second check clean → retry png wins
        checks = iter([{"garbled": True}, {"garbled": False}])

        async def fake_check(png):
            return next(checks)

        persist = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
        gen = AsyncMock(return_value=(b"flux-png", {"model": "flux1-schnell"}))
        with (
            patch("app.services.media_ai.expand_visual_prompt", new=AsyncMock(side_effect=lambda p, **k: p)),
            patch("app.services.comfyui_image.generate_image", new=gen),
            patch("app.services.gpu_arbiter.media_gpu_lock", new=_fake_gpu_lock),
            patch("app.services.image_text_guard.check_image_text", new=fake_check),
            patch("app.services.media_storage.persist_generated_image", new=persist),
        ):
            team = SimpleNamespace(id=uuid.uuid4())
            action = SimpleNamespace(instruction=PRODUCTION_PROMPT)
            asset_id = await monetization_executor._generate_media(None, team, None, action)

        assert asset_id == persist.return_value.id
        assert gen.await_count == 2  # original + no-text retry
        # retry prompt is the hard no-text variant
        retry_prompt = gen.await_args_list[1].kwargs["prompt"]
        assert "NO TEXT" in retry_prompt
        assert persist.await_args.kwargs["image_bytes"] == b"flux-png"

    @pytest.mark.asyncio
    async def test_garbled_twice_falls_back_to_brand_banner(self):
        from app.services import monetization_executor

        checks = iter([{"garbled": True}, {"garbled": True}])

        async def fake_check(png):
            return next(checks)

        async def fake_correct(text, **kw):
            return text

        persist = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
        gen = AsyncMock(return_value=(b"flux-png", {"model": "flux1-schnell"}))
        og = MagicMock(return_value=b"banner-png")
        with (
            patch("app.services.media_ai.expand_visual_prompt", new=AsyncMock(side_effect=lambda p, **k: p)),
            patch("app.services.comfyui_image.generate_image", new=gen),
            patch("app.services.gpu_arbiter.media_gpu_lock", new=_fake_gpu_lock),
            patch("app.services.image_text_guard.check_image_text", new=fake_check),
            patch("app.services.brand_assets.generate_og_image", new=og),
            patch("app.services.spellcheck.auto_correct", new=fake_correct),
            patch("app.services.media_storage.persist_generated_image", new=persist),
        ):
            team = SimpleNamespace(id=uuid.uuid4())
            action = SimpleNamespace(instruction=PRODUCTION_PROMPT)
            asset_id = await monetization_executor._generate_media(None, team, None, action)

        assert asset_id == persist.return_value.id
        assert gen.await_count == 2  # no third flux attempt
        assert persist.await_args.kwargs["image_bytes"] == b"banner-png"

    @pytest.mark.asyncio
    async def test_clean_image_skips_retry(self):
        from app.services import monetization_executor

        async def fake_check(png):
            return {"garbled": False}

        persist = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
        gen = AsyncMock(return_value=(b"flux-png", {"model": "flux1-schnell"}))
        with (
            patch("app.services.media_ai.expand_visual_prompt", new=AsyncMock(side_effect=lambda p, **k: p)),
            patch("app.services.comfyui_image.generate_image", new=gen),
            patch("app.services.gpu_arbiter.media_gpu_lock", new=_fake_gpu_lock),
            patch("app.services.image_text_guard.check_image_text", new=fake_check),
            patch("app.services.media_storage.persist_generated_image", new=persist),
        ):
            team = SimpleNamespace(id=uuid.uuid4())
            action = SimpleNamespace(instruction="team holding a video camera")
            await monetization_executor._generate_media(None, team, None, action)

        assert gen.await_count == 1  # no QA retry
