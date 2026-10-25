"""Media quality gates — spellcheck before render, platform casing,
brand-domain guard."""
import asyncio
from unittest.mock import AsyncMock, patch


class TestPlatformDisplay:
    def test_canonical_casing(self):
        from app.services.strategy_report import platform_display
        assert platform_display("linkedin") == "LinkedIn"
        assert platform_display("tiktok") == "TikTok"
        assert platform_display("whatsapp") == "WhatsApp"
        assert platform_display("threads") == "Threads"

    def test_fallback_capitalize(self):
        from app.services.strategy_report import platform_display
        assert platform_display("mastodon") == "Mastodon"

    def test_none_safe(self):
        from app.services.strategy_report import platform_display
        assert platform_display(None) == "—"


class TestBrandDomainGuard:
    def test_com_rewritten_to_gr(self):
        from app.services.carousel_pipeline import _normalize_brand_domain
        out = _normalize_brand_domain({"suggested_caption": "Visit https://cloudless.com"})
        assert "cloudless.com" not in out["suggested_caption"]
        assert "cloudless.gr" in out["suggested_caption"]

    def test_nested_slide_fields_fixed(self):
        from app.services.carousel_pipeline import _normalize_brand_domain
        out = _normalize_brand_domain({
            "slides": [{"title": "t", "body": "check www.cloudless.com now"}]
        })
        assert "cloudless.gr" in out["slides"][0]["body"]

    def test_clean_copy_untouched(self):
        from app.services.carousel_pipeline import _normalize_brand_domain
        node = {"slides": [{"title": "cloudless.gr is the domain"}]}
        assert _normalize_brand_domain(node) == node


class TestSpellcheckGate:
    def test_carousel_copy_corrected(self):
        # generate_carousel_copy must run correct_text on every text field
        async def _run():
            fake = {
                "slides": [{"title": "hedline", "body": "bodi", "highlight": "x"}],
                "suggested_caption": "capton",
            }
            calls = []

            async def fake_correct(t):
                calls.append(t)
                return t + "*fixed"

            import sys, types
            sys.modules.setdefault(
                "app.services.kakkava",
                types.SimpleNamespace(doctrine_context=AsyncMock(return_value="")),
            )
            with patch(
                "app.services.carousel_pipeline.call_inference",
                new=AsyncMock(return_value=fake),
            ), patch(
                "app.services.media_spellcheck.correct_text",
                new=fake_correct,
            ):
                from app.services.carousel_pipeline import generate_carousel_copy
                out = await generate_carousel_copy(
                    topic="t", num_slides=3, tone="casual",
                    include_cta=True, text_model="m",
                    db=None, team_id=None,
                )
            # every slide text field + caption corrected
            assert len(calls) == 4
            assert out["slides"][0]["title"] == "hedline*fixed"
            assert out["suggested_caption"] == "capton*fixed"

        asyncio.run(_run())
