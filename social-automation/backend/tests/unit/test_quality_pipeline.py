"""Unit tests for the shared quality pipeline gibberish guard."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.inference as inference
import app.services.plain_english as plain_english
import app.services.quality_pipeline as qp
import app.services.seo as seo_service


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


# ── coverage: to_dict + remaining branches + with_quality ─────────────


def test_quality_result_to_dict():
    r = qp.QualityResult(content="c", hashtags=["a"])
    d = r.to_dict()
    assert d["content"] == "c" and d["hashtags"] == ["a"]
    assert d["improved"] is False and d["iterations"] == 0


@pytest.mark.asyncio
async def test_pipeline_spellcheck_applies_and_exceptions(monkeypatch):
    monkeypatch.setattr(qp, "auto_correct", AsyncMock(return_value="fixed text"))
    monkeypatch.setattr(
        plain_english, "run_nlp_check_and_fix",
        AsyncMock(side_effect=RuntimeError("nlp down")))
    monkeypatch.setattr(qp, "detect_gibberish", AsyncMock(return_value=[]))
    monkeypatch.setattr(plain_english, "sofia_nlp_score", lambda t: (0, []))

    # run_seo=False triggers the standalone gibberish check + spellcheck applied
    r = await qp.apply_quality_pipeline("raw text", "linkedin", run_seo=False)
    assert r.content == "fixed text"
    assert r.spellcheck_applied is True
    assert r.nlp_report == {}  # NLP exception swallowed

    # spellcheck exception also swallowed
    monkeypatch.setattr(qp, "auto_correct", AsyncMock(side_effect=RuntimeError("x")))
    monkeypatch.setattr(qp, "detect_gibberish",
                        AsyncMock(side_effect=RuntimeError("gib down")))
    r2 = await qp.apply_quality_pipeline("raw", "linkedin", run_seo=False)
    assert r2.content == "raw"
    assert r2.spellcheck_applied is False
    assert r2.gibberish_tokens == []


@pytest.mark.asyncio
async def test_pipeline_improve_spellcheck_and_hashtags(monkeypatch):
    monkeypatch.setattr(qp, "auto_correct", AsyncMock(side_effect=lambda t: "sc:" + t))
    monkeypatch.setattr(
        plain_english, "run_nlp_check_and_fix",
        AsyncMock(return_value=([], "nlp fixed", SimpleNamespace(to_dict=lambda: {"ok": 1}))))
    monkeypatch.setattr(qp, "detect_gibberish", AsyncMock(return_value=["BadTok"]))
    monkeypatch.setattr(
        seo_service, "analyze_seo",
        AsyncMock(return_value={"score": {"overall": 50, "recommendations": ["do better"]}}))
    monkeypatch.setattr(plain_english, "sofia_nlp_score", lambda t: (77, ["i"]))
    monkeypatch.setattr(
        inference, "call_inference",
        AsyncMock(return_value={"content": "x" * 500, "hashtags": ["#one", " two ", ""]}))

    r = await qp.apply_quality_pipeline("short", "twitter", run_spellcheck=True, max_iterations=1)
    assert r.content == "sc:" + "x" * 500  # improved content got spellchecked
    assert r.hashtags == ["one", "two"]    # stripped #, trimmed, empties dropped
    assert r.improved is True
    assert r.nlp_report == {"ok": 1}
    assert r.sofia_score == 77


@pytest.mark.asyncio
async def test_pipeline_seo_exception_breaks_and_sofia_exception(monkeypatch):
    monkeypatch.setattr(qp, "auto_correct", AsyncMock(side_effect=lambda t: t))
    monkeypatch.setattr(
        plain_english, "run_nlp_check_and_fix",
        AsyncMock(return_value=([], "t", SimpleNamespace(to_dict=lambda: {}))))
    monkeypatch.setattr(qp, "detect_gibberish", AsyncMock(return_value=[]))
    monkeypatch.setattr(seo_service, "analyze_seo",
                        AsyncMock(side_effect=RuntimeError("seo down")))
    monkeypatch.setattr(plain_english, "sofia_nlp_score",
                        lambda t: (_ for _ in ()).throw(RuntimeError("sofia")))

    r = await qp.apply_quality_pipeline("text", "linkedin")
    assert r.seo_score == {}
    assert r.sofia_score == 0


@pytest.mark.asyncio
async def test_pipeline_shorter_improvement_rejected_and_max_iter(monkeypatch):
    monkeypatch.setattr(qp, "auto_correct", AsyncMock(side_effect=lambda t: t))
    monkeypatch.setattr(
        plain_english, "run_nlp_check_and_fix",
        AsyncMock(return_value=([], "original content here", SimpleNamespace(to_dict=lambda: {}))))
    monkeypatch.setattr(qp, "detect_gibberish", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        seo_service, "analyze_seo",
        AsyncMock(return_value={"score": {"overall": 10, "recommendations": []}}))
    monkeypatch.setattr(plain_english, "sofia_nlp_score", lambda t: (0, []))
    monkeypatch.setattr(
        inference, "call_inference",
        AsyncMock(return_value={"content": "tiny", "hashtags": []}))

    r = await qp.apply_quality_pipeline("c", "linkedin", max_iterations=1)
    assert r.content == "original content here"  # shorter improvement rejected
    assert r.improved is False
    assert r.iterations == 1  # loop ran to max_iterations


@pytest.mark.asyncio
async def test_pipeline_cloudflare_model_and_no_recs_default(monkeypatch):
    monkeypatch.setattr(qp, "auto_correct", AsyncMock(side_effect=lambda t: t))
    monkeypatch.setattr(
        plain_english, "run_nlp_check_and_fix",
        AsyncMock(return_value=([], "c", SimpleNamespace(to_dict=lambda: {}))))
    monkeypatch.setattr(qp, "detect_gibberish", AsyncMock(return_value=["tok"]))
    monkeypatch.setattr(
        seo_service, "analyze_seo",
        AsyncMock(return_value={"score": {"overall": 10, "recommendations": []}}))
    monkeypatch.setattr(plain_english, "sofia_nlp_score", lambda t: (0, []))
    improve = AsyncMock(return_value={"content": "z" * 600, "hashtags": []})
    monkeypatch.setattr(inference, "call_inference", improve)

    r = await qp.apply_quality_pipeline(
        "c", "linkedin", provider_name="cloudflare", max_iterations=0)
    # max_iterations=0 -> break on iteration 0 before improve... but gibberish
    # forces the check; iteration >= max -> break, improve never called
    assert r.gibberish_tokens == ["tok"]


# ── with_quality decorator ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_with_quality_dict_result(monkeypatch):
    seen = {}

    async def fake_pipeline(**kw):
        seen.update(kw)
        return qp.QualityResult(content="better", hashtags=["h"], improved=True,
                                seo_score={"overall": 95}, nlp_report={"r": 1})
    monkeypatch.setattr(qp, "apply_quality_pipeline", fake_pipeline)

    @qp.with_quality()
    async def endpoint():
        return {"content": "raw", "hashtags": [], "platform": "twitter"}

    out = await endpoint()
    assert out["content"] == "better"
    assert out["hashtags"] == ["h"]
    assert out["seo_score"] == {"overall": 95}
    assert out["nlp_report"] == {"r": 1}
    assert out["quality"]["improved"] is True
    assert seen["platform"] == "twitter"


@pytest.mark.asyncio
async def test_with_quality_pydantic_result_and_platform_fallback(monkeypatch):
    async def fake_pipeline(**kw):
        return qp.QualityResult(content="q", hashtags=["x"],
                                seo_score={}, nlp_report={})
    monkeypatch.setattr(qp, "apply_quality_pipeline", fake_pipeline)

    class _Resp:
        def __init__(self):
            self.content = "raw"
            self.hashtags = []

        def model_dump(self):
            return {"content": self.content, "hashtags": self.hashtags,
                    "platform": ""}

    @qp.with_quality()
    async def endpoint(req):
        return _Resp()

    # platform falls back to request arg attribute
    out = await endpoint(SimpleNamespace(platform="instagram"))
    assert out.content == "q" and out.hashtags == ["x"]

    # no platform anywhere -> default "linkedin"
    captured = {}
    async def capture(**kw):
        captured.update(kw)
        return qp.QualityResult(content="q", hashtags=[])
    monkeypatch.setattr(qp, "apply_quality_pipeline", capture)

    @qp.with_quality()
    async def endpoint2():
        return _Resp()

    await endpoint2()
    assert captured["platform"] == "linkedin"


@pytest.mark.asyncio
async def test_with_quality_early_returns():
    @qp.with_quality()
    async def non_dict():
        return 42

    assert await non_dict() == 42  # unprocessable type returned as-is

    @qp.with_quality()
    async def empty_content():
        return {"content": "", "hashtags": [], "platform": "linkedin"}

    assert (await empty_content())["content"] == ""  # nothing to check


@pytest.mark.asyncio
async def test_pipeline_improve_spellcheck_exception(monkeypatch):
    monkeypatch.setattr(qp, "auto_correct", AsyncMock(side_effect=RuntimeError("down")))
    monkeypatch.setattr(
        plain_english, "run_nlp_check_and_fix",
        AsyncMock(return_value=([], "c", SimpleNamespace(to_dict=lambda: {}))))
    monkeypatch.setattr(qp, "detect_gibberish", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        seo_service, "analyze_seo",
        AsyncMock(return_value={"score": {"overall": 10, "recommendations": []}}))
    monkeypatch.setattr(plain_english, "sofia_nlp_score", lambda t: (0, []))
    monkeypatch.setattr(
        inference, "call_inference",
        AsyncMock(return_value={"content": "y" * 500, "hashtags": []}))

    r = await qp.apply_quality_pipeline("c", "linkedin", max_iterations=1)
    assert r.content == "y" * 500  # improved content applied despite spellcheck error


@pytest.mark.asyncio
async def test_with_quality_setattr_meta_fields(monkeypatch):
    async def fake_pipeline(**kw):
        return qp.QualityResult(content="q", hashtags=["x"], improved=True,
                                seo_score={"overall": 90}, nlp_report={"r": 2})
    monkeypatch.setattr(qp, "apply_quality_pipeline", fake_pipeline)

    class _Resp:
        def __init__(self):
            self.content = "raw"
            self.hashtags = []
            self.seo_score = None
            self.nlp_report = None
            self.quality = None

        def model_dump(self):
            return {"content": "raw", "hashtags": [], "platform": "linkedin"}

    @qp.with_quality()
    async def endpoint():
        return _Resp()

    out = await endpoint()
    assert out.seo_score == {"overall": 90}
    assert out.nlp_report == {"r": 2}
    assert out.quality["improved"] is True
