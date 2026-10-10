"""Unit tests for the shared quality pipeline gibberish guard."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.plain_english as plain_english
import app.services.seo as seo_service
import app.services.inference as inference
import app.services.quality_pipeline as qp


def _neutral_patches(monkeypatch, seo_overall=95):
    """Patch every external dependency of apply_quality_pipeline so only the
    gibberish-guard control flow is exercised."""
    monkeypatch.setattr(qp, "auto_correct", AsyncMock(side_effect=lambda t: t))
    monkeypatch.setattr(
        plain_english,
        "run_nlp_check_and_fix",
        AsyncMock(
            side_effect=lambda **kw: (
                [],
                kw.get("caption", ""),
                SimpleNamespace(to_dict=lambda: {}),
            )
        ),
    )
    monkeypatch.setattr(
        seo_service,
        "analyze_seo",
        AsyncMock(return_value={"score": {"overall": seo_overall, "recommendations": []}}),
    )
    monkeypatch.setattr(plain_english, "sofia_nlp_score", lambda text: (80, []))


@pytest.mark.asyncio
async def test_pipeline_gibberish_forces_improvement_round(monkeypatch):
    """SEO 95 normally stops at iteration 0 — a gibberish flag must trigger
    one improvement round even though the score already meets the target."""
    _neutral_patches(monkeypatch, seo_overall=95)

    detect = AsyncMock(side_effect=[["GGr"], []])
    monkeypatch.setattr(qp, "detect_gibberish", detect)

    improved_content = "A much longer, fully corrected post body " * 8
    improve = AsyncMock(
        return_value={"content": improved_content, "hashtags": ["cloudless"]}
    )
    monkeypatch.setattr(inference, "call_inference", improve)

    result = await qp.apply_quality_pipeline(
        "Speed + security handled cloudless.g GGr",
        platform="linkedin",
        run_spellcheck=False,
    )

    assert improve.await_count == 1  # improvement ran despite score >= target
    assert result.improved is True
    assert result.gibberish_tokens == []  # second detection round cleared it
    # The improvement prompt must name the flagged token
    prompt = improve.await_args.args[0]
    assert "GGr" in prompt


@pytest.mark.asyncio
async def test_pipeline_clean_text_stops_at_score(monkeypatch):
    _neutral_patches(monkeypatch, seo_overall=95)
    detect = AsyncMock(return_value=[])
    monkeypatch.setattr(qp, "detect_gibberish", detect)
    improve = AsyncMock()
    monkeypatch.setattr(inference, "call_inference", improve)

    result = await qp.apply_quality_pipeline(
        "We help small teams ship faster with managed hosting.",
        platform="linkedin",
        run_spellcheck=False,
    )

    assert result.iterations == 0
    assert improve.await_count == 0
    assert result.improved is False


@pytest.mark.asyncio
async def test_pipeline_gibberish_respects_max_iterations(monkeypatch):
    """Persistent corruption must not loop forever."""
    _neutral_patches(monkeypatch, seo_overall=50)
    monkeypatch.setattr(qp, "detect_gibberish", AsyncMock(return_value=["GGr"]))
    improve = AsyncMock(return_value={"content": "", "hashtags": []})
    monkeypatch.setattr(inference, "call_inference", improve)

    result = await qp.apply_quality_pipeline(
        "broken cloudless.g GGr text",
        platform="linkedin",
        max_iterations=2,
        run_spellcheck=False,
    )

    assert result.iterations == 2
    assert improve.await_count == 2
    assert result.gibberish_tokens == ["GGr"]  # residual flag surfaces


@pytest.mark.asyncio
async def test_pipeline_detect_failure_is_advisory(monkeypatch):
    _neutral_patches(monkeypatch, seo_overall=95)
    monkeypatch.setattr(
        qp, "detect_gibberish", AsyncMock(side_effect=RuntimeError("boom"))
    )

    result = await qp.apply_quality_pipeline(
        "Perfectly good content here.",
        platform="linkedin",
        run_spellcheck=False,
    )

    assert result.gibberish_tokens == []
    assert result.improved is False
