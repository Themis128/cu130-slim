"""Unit tests for brand_compliance system-prompt builder."""

from app.services.brand_compliance import build_brand_system_prompt


class TestBuildBrandSystemPrompt:
    def test_empty_brand(self):
        prompt = build_brand_system_prompt({})
        assert "the brand" in prompt

    def test_voice_signature_small(self):
        prompt = build_brand_system_prompt(
            {"name": "Cloudless"},
            {"voice_signature": {"niche": "We help SMBs run simple cloud"}},
        )
        assert "niche=We help SMBs" in prompt

    def test_voice_signature_capped(self):
        # The full VEC signature (~4K tokens) overflows the local models'
        # 2-4K context — every value must be truncated and the whole
        # signature section must stay under the budget.
        signature = {f"key{i}": {"text": "x" * 900} for i in range(20)}
        prompt = build_brand_system_prompt(
            {"name": "Cloudless"}, {"voice_signature": signature}
        )
        sig_line = next(
            ln for ln in prompt.splitlines() if ln.startswith("Voice signature:")
        )
        assert len(sig_line) <= 3400
        assert "…" in sig_line
        # every key still present — truncation shrinks values, never drops keys
        for i in range(20):
            assert f"key{i}=" in sig_line

    def test_banned_phrases_still_rendered(self):
        prompt = build_brand_system_prompt(
            {"name": "Cloudless"},
            {"banned_phrases": ["synergy"], "voice_signature": {"a": "b"}},
        )
        assert "synergy" in prompt
        assert "Voice signature:" in prompt
