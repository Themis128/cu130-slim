"""Unit tests for LinkedIn AI content generation."""

import pytest

from app.services import linkedin_ai as linkedin_ai
from app.services import plain_english


class _AsyncLiteral:
    """Wrap a literal return value (or a callable) in an awaitable."""

    def __init__(self, value):
        self.value = value

    def __call__(self, *args, **kwargs):
        async def _coro():
            if callable(self.value):
                return self.value(*args, **kwargs)
            return self.value

        return _coro()


@pytest.fixture
def mock_inference(monkeypatch):
    """Replace call_inference with a controllable fake."""
    calls: list[dict] = []

    async def fake_call_inference(
        prompt,
        *,
        provider_name="cloudflare",
        db=None,
        team_id=None,
        schema=None,
        model_override=None,
        max_tokens=None,
        allow_fallback=True,
        platform=None,
    ):
        calls.append(
            {
                "prompt": prompt,
                "provider_name": provider_name,
                "db": db,
                "team_id": team_id,
                "schema": schema,
                "model_override": model_override,
                "max_tokens": max_tokens,
                "platform": platform,
            }
        )
        return {
            "content": "This is generated content.",
            "hashtags": ["cloud", "serverless", "cloudless"],
            "title": "Generated title",
            "subtitle": "A short summary",
            "sections": [
                {"heading": "Section 1", "body": "Body one."},
                {"heading": "Section 2", "body": "Body two."},
            ],
            "takeaways": ["Keep it simple", "Ship fast"],
            "cta": "Try cloudless.gr",
            "quote": {"text": "Ship small, ship often.", "attribution": "Jane Dev, CTO — Wired",
                      "source_url": "https://wired.com/example"},
            "sources": [{"title": "The Small-Batch Advantage", "publisher": "HBR",
                          "url": "https://hbr.org/example"}],
            "cover_prompt": "minimalist neon grid, server racks",
            "comment": "Great post!",
            "improved_content": "This is improved content.",
            "changes": ["Made it clearer"],
        }

    monkeypatch.setattr(linkedin_ai, "call_inference", fake_call_inference)
    return calls


@pytest.fixture
def mock_plain_english(monkeypatch):
    """Make the plain-English helpers pass text through unchanged."""
    monkeypatch.setattr(linkedin_ai, "rewrite_plain_english", _AsyncLiteral(lambda x, **kwargs: x))
    monkeypatch.setattr(linkedin_ai, "extract_rewritten_only", lambda text: str(text or "").strip())


@pytest.mark.asyncio
async def test_generate_linkedin_post(mock_inference, mock_plain_english):
    result = await linkedin_ai.generate_linkedin_post("Why cloudless rocks")

    assert "cloudless.gr" in result["caption"]
    assert result["hashtags"] == ["cloud", "serverless", "cloudless"]
    assert result["tone"] == "professional"
    assert "#cloud" in result["caption"]

    call = mock_inference[0]
    assert "LinkedIn post" in call["prompt"]
    assert call["provider_name"] == "dmr"


@pytest.mark.asyncio
async def test_generate_linkedin_post_omits_site_link(mock_inference, mock_plain_english):
    result = await linkedin_ai.generate_linkedin_post(
        "Why cloudless rocks",
        include_site_link=False,
    )

    assert "cloudless.gr" not in result["caption"]


@pytest.mark.asyncio
async def test_generate_linkedin_article(mock_inference, mock_plain_english):
    result = await linkedin_ai.generate_linkedin_article(
        "Building serverless apps",
        sections=3,
    )

    assert result["title"] == "Generated title"
    assert result["body"].startswith("Section 1")
    assert len(result["sections"]) == 2
    assert result["takeaways"] == ["Keep it simple", "Ship fast"]
    assert result["cta"] == "Try cloudless.gr"


@pytest.mark.asyncio
async def test_generate_linkedin_article_quote_and_sources(mock_inference, mock_plain_english):
    result = await linkedin_ai.generate_linkedin_article("Building serverless apps", sections=3)
    assert result["quote"]["attribution"].startswith("Jane Dev")
    assert result["sources"][0]["publisher"] == "HBR"
    assert result["cover_prompt"] == "minimalist neon grid, server racks"
    # rendered into the paste body — pull-quote + sources block
    assert "\u201cShip small" in result["body"]
    assert "Sources & further reading" in result["body"]
    assert "https://hbr.org/example" in result["body"]


@pytest.mark.asyncio
async def test_generate_linkedin_article_no_quote(mock_plain_english, monkeypatch):
    async def fake(*a, **k):
        return {"title": "T", "subtitle": "S", "sections": [{"heading": "H", "body": "B"}],
                "takeaways": [], "cta": "", "quote": {}, "sources": [], "cover_prompt": ""}
    monkeypatch.setattr(linkedin_ai, "_call_text", fake)
    result = await linkedin_ai.generate_linkedin_article("x")
    assert result["quote"] == {} and result["sources"] == []
    assert "Sources & further reading" not in result["body"]


@pytest.mark.asyncio
async def test_generate_linkedin_hashtags(mock_inference, mock_plain_english):
    hashtags = await linkedin_ai.generate_linkedin_hashtags("We love cloud computing", count=3)

    assert hashtags == ["cloud", "serverless", "cloudless"]


@pytest.mark.asyncio
async def test_suggest_best_time_to_post():
    times = await linkedin_ai.suggest_best_time_to_post()

    assert all(t["timezone"] == "Europe/Athens" for t in times)
    assert any(t["day"] == "Wednesday" and t["time"] == "09:00" for t in times)


def test_rank_best_time_windows_derives_windows_from_published_at():
    from datetime import UTC, datetime, timedelta

    # 6 posts published Mondays 07:00 UTC = 09:00 Athens, ER 2.0
    samples = [(datetime(2026, 1, 5, 7, 0, tzinfo=UTC) + timedelta(days=7 * i), 2.0) for i in range(6)]
    # 8 posts Wednesdays 17:00 UTC = 19:00 Athens, ER 0.5 (below the mean)
    samples += [(datetime(2026, 1, 7, 17, 0, tzinfo=UTC) + timedelta(days=7 * i), 0.5) for i in range(8)]

    windows = linkedin_ai.rank_best_time_windows(samples, "Europe/Athens")

    # mean ER ≈ 1.14 — the Monday slot beats it by the 1.25 margin → high
    assert windows[0]["day"] == "Monday"
    assert windows[0]["time"] == "09:00"
    assert windows[0]["confidence"] == "high"
    assert windows[0]["sample_size"] == 6
    # The Wednesday slot is below the mean ER — not a recommendation.
    assert all(w["day"] != "Wednesday" for w in windows)


def test_rank_best_time_windows_suppresses_dead_night_scheduler_artifacts():
    from datetime import UTC, datetime, timedelta

    # 12 posts at 02:00 UTC = 04:00 Athens with the highest ER — a recurring
    # automation slot. Per PR #143 dead-night hours must never be recommended.
    samples = [(datetime(2026, 1, 5, 2, 0, tzinfo=UTC) + timedelta(days=i), 5.0) for i in range(12)]
    # 4 daytime posts so the sample floor is comfortably met.
    samples += [(datetime(2026, 1, 5, 9, 0, tzinfo=UTC) + timedelta(days=i), 0.5) for i in range(4)]

    windows = linkedin_ai.rank_best_time_windows(samples, "Europe/Athens")

    assert all(w["time"] != "04:00" for w in windows)


def test_rank_best_time_windows_requires_min_samples_per_slot():
    from datetime import UTC, datetime, timedelta

    # 12 posts scattered across different slots — no slot reaches min_n=2.
    samples = [(datetime(2026, 1, 5, 8, 0, tzinfo=UTC) + timedelta(hours=i), 2.0) for i in range(12)]

    assert linkedin_ai.rank_best_time_windows(samples, "Europe/Athens") == []


@pytest.mark.asyncio
async def test_improve_linkedin_post(mock_inference, mock_plain_english):
    result = await linkedin_ai.improve_linkedin_post("Old post text", goal="clarity")

    assert result["improved_content"] == "This is improved content."
    assert result["changes"] == ["Made it clearer"]
    assert result["hashtags"] == ["cloud", "serverless", "cloudless"]


@pytest.mark.asyncio
async def test_generate_linkedin_comment(mock_inference, mock_plain_english):
    comment = await linkedin_ai.generate_linkedin_comment(
        "We just shipped a new feature.",
        reply_context="Reply as a happy customer",
        tone="friendly",
    )

    assert comment == "Great post!"


@pytest.mark.asyncio
async def test_generate_linkedin_hashtags_respects_count_cap(mock_inference, mock_plain_english):
    hashtags = await linkedin_ai.generate_linkedin_hashtags("content", count=2)

    assert hashtags == ["cloud", "serverless"]


@pytest.mark.asyncio
async def test_generate_linkedin_post_includes_plain_english_rules_in_prompt(mock_inference, mock_plain_english):
    await linkedin_ai.generate_linkedin_post("test topic")

    prompt = mock_inference[0]["prompt"]
    assert "PLAIN ENGLISH RULES" in prompt
    assert "jargon" in prompt or "buzzwords" in prompt


def test_build_linkedin_caption_adds_site_and_hashtags():
    caption = plain_english.build_linkedin_caption(
        "We launched today.",
        ["cloud", "serverless"],
        site="www.cloudless.gr",
    )

    assert "www.cloudless.gr" in caption
    assert "#cloud" in caption
    assert "#serverless" in caption
    # Should not duplicate the site if it is already in the text.
    assert caption.count("cloudless.gr") == 1


@pytest.mark.asyncio
async def test_call_inference_wraps_provider_errors(monkeypatch):
    from fastapi import HTTPException

    async def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(linkedin_ai, "call_inference", boom)
    with pytest.raises(HTTPException) as e:
        await linkedin_ai.generate_linkedin_post("topic")
    assert e.value.status_code == 500
    assert "provider down" in e.value.detail


@pytest.mark.asyncio
async def test_call_inference_reraises_http_exception(monkeypatch):
    from fastapi import HTTPException

    async def http_boom(*a, **k):
        raise HTTPException(status_code=429, detail="rate limited")

    monkeypatch.setattr(linkedin_ai, "call_inference", http_boom)
    with pytest.raises(HTTPException) as e:
        await linkedin_ai.generate_linkedin_post("topic")
    assert e.value.status_code == 429


def test_rank_best_time_windows_skips_none_and_naive_timestamps():
    from datetime import datetime

    # naive datetimes → treated as UTC; None → skipped entirely
    samples = [(None, 0.5)] + [
        (datetime(2026, 10, 6, 9, 0), 0.9)
        for _ in range(30)  # Tue 09:00 naive
    ]
    windows = linkedin_ai.rank_best_time_windows(samples, "UTC")
    assert all(w["day"] == "Tuesday" and w["time"] == "09:00" for w in windows)
    assert all(w["sample_size"] == 30 for w in windows)


def test_rank_best_time_windows_caps_at_max_windows():
    from datetime import datetime

    # seed > _MAX_WINDOWS distinct slots, each above the mean
    samples = []
    for day in range(7):
        for hour in range(24):
            for _ in range(10):
                samples.append((datetime(2026, 10, 5 + day, hour, 0), 1.0))
    windows = linkedin_ai.rank_best_time_windows(samples, "UTC")
    assert len(windows) <= linkedin_ai._MAX_WINDOWS


@pytest.mark.asyncio
async def test_suggest_best_time_derives_windows_from_samples():
    from datetime import datetime

    # hot slot must beat the account mean, so mix in low-ER filler slots
    samples = [(datetime(2026, 10, 6, 9, 0), 0.9) for _ in range(30)]
    for day_off in (1, 2):
        for _ in range(15):
            samples.append((datetime(2026, 10, 6 + day_off, 15, 0), 0.05))
    windows = await linkedin_ai.suggest_best_time_to_post(timezone="UTC", samples=samples)
    assert windows[0]["day"] == "Tuesday"
    assert windows[0]["time"] == "09:00"
    assert "sample_size" in windows[0]  # derived windows, not the static fallback


def test_rank_best_time_windows_fewer_than_10_samples():
    from datetime import datetime

    assert linkedin_ai.rank_best_time_windows([(datetime(2026, 10, 6, 9, 0), 0.9)] * 9, "UTC") == []


@pytest.mark.asyncio
async def test_suggest_best_time_personal_account_extends_windows():
    windows = await linkedin_ai.suggest_best_time_to_post(account_type="person", timezone="UTC")
    assert any(w["day"] == "Monday" and w["time"] == "17:00" for w in windows)
    assert any(w["day"] == "Friday" and w["time"] == "08:00" for w in windows)
