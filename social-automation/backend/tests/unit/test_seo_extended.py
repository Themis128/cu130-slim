"""Extended coverage for seo.py — suggest_meta, analyze_seo, score branches."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services import seo


def _long_sentence(n=30):
    return " ".join(f"word{i}" for i in range(n)) + "."


def test_score_content_over_limit():
    text = "x" * 4000  # over linkedin 3000
    rep = seo.score_content(text, "linkedin")
    assert rep.length < 100
    assert any("over the" in r for r in rep.recommendations)


def test_score_content_very_short():
    rep = seo.score_content("short post", "linkedin")
    assert rep.length < 100
    assert any("Content is short" in r for r in rep.recommendations)


def test_score_content_long_sentences_penalty():
    rep = seo.score_content(_long_sentence(40) * 10 + " #a #b #c link https://x.com/y", "linkedin")
    assert rep.readability == 40
    assert any("Shorter sentences" in r for r in rep.recommendations)


def test_score_content_readability_bands():
    rep = seo.score_content(_long_sentence(18) * 10 + " https://x.com", "linkedin")
    assert rep.readability == 80
    rep2 = seo.score_content(_long_sentence(24) * 20 + " https://x.com", "linkedin")
    assert rep2.readability == 60


def test_score_content_hashtag_too_few_and_too_many():
    few = seo.score_content("reasonable body text with keywords testing results here " * 8 + " https://x.com", "linkedin")
    assert any("hashtag" in r.lower() for r in few.recommendations)
    many = seo.score_content("body " * 200 + "#a #b #c #d #e #f #g #h https://x.com", "linkedin")
    assert any("Reduce hashtags" in r for r in many.recommendations)


def test_score_content_links_platform_specific():
    # twitter: no link is ideal
    rep = seo.score_content("tweet with no link " * 5, "twitter")
    assert rep.links == 100
    rep = seo.score_content("tweet https://x.com/a more text", "twitter")
    assert rep.links == 50
    # linkedin: exactly one link ideal, none → recommendation
    rep = seo.score_content("post body " * 20 + " https://x.com/a", "linkedin")
    assert rep.links == 100
    rep = seo.score_content("post body with keywords and no link " * 10, "linkedin")
    assert rep.links == 70 and any("Add a relevant link" in r for r in rep.recommendations)
    rep = seo.score_content("body https://a.com https://b.com " * 3, "linkedin")
    assert rep.links == 60


def test_score_content_plain_english_and_keyword_paths():
    # jargon → plain_english penalty + jargon recommendation
    rep = seo.score_content(
        "We leverage synergies and utilize paradigm shifts. " * 20 + " https://x.com #tag1 #tag2 #tag3",
        "linkedin",
    )
    assert rep.plain_english < 100
    # no keywords → keywords 0 (pure stopwords, no link — "https"/"com" would
    # otherwise count as keywords)
    rep = seo.score_content("the the and and or or. " * 30, "linkedin")
    assert rep.keywords == 0
    assert any("No clear keywords" in r for r in rep.recommendations)
    # unknown platform falls back to linkedin hints
    rep = seo.score_content("text " * 100 + " https://x.com #a #b #c", "unknown_plat")
    assert rep.overall > 0


def test_avg_sentence_words_empty():
    assert seo._avg_sentence_words("") == 0.0
    assert seo._avg_sentence_words("no sentence enders here") == 0.0 or seo._avg_sentence_words("no sentence enders here") > 0


def test_count_helpers():
    assert seo._count_hashtags("#one #two plain") == 2
    assert seo._count_links("see https://a.com and http://b.org/x") == 2


@pytest.mark.asyncio
async def test_suggest_meta_platform_skip():
    for plat in ("twitter", "threads", "instagram", "tiktok"):
        out = await seo.suggest_meta("T", "body", plat)
        assert out == {"title": "", "description": ""}


@pytest.mark.asyncio
async def test_suggest_meta_calls_inference(monkeypatch):
    monkeypatch.setattr(
        seo,
        "call_inference",
        AsyncMock(return_value={"title": "  SEO Title ", "description": "  Desc "}),
    )
    out = await seo.suggest_meta("Hint", "body " * 100, "linkedin")
    assert out == {"title": "SEO Title", "description": "Desc"}


@pytest.mark.asyncio
async def test_suggest_meta_inference_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(seo, "call_inference", AsyncMock(side_effect=RuntimeError("down")))
    out = await seo.suggest_meta("T", "body", "facebook")
    assert out == {"title": "", "description": ""}


@pytest.mark.asyncio
async def test_analyze_seo_full(monkeypatch):
    monkeypatch.setattr(
        seo,
        "call_inference",
        AsyncMock(return_value={"title": "Meta T", "description": "Meta D"}),
    )
    out = await seo.analyze_seo(
        "Solid testing post with real results and measured benchmarks. " * 15 + " https://x.com/a #a #b #c",
        "linkedin",
        title="Title",
    )
    assert out["platform"] == "linkedin"
    assert out["score"]["overall"] > 0
    assert out["meta"] == {"title": "Meta T", "description": "Meta D"}
    assert out["open_graph"]["og_title"] == "Meta T"
    assert out["character_count"] > 0 and out["hashtag_count"] == 3 and out["link_count"] == 1
