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

    async def fake_call_inference(prompt, *, provider_name="cloudflare", db=None, team_id=None, schema=None, model_override=None, max_tokens=None, allow_fallback=True):
        calls.append({
            "prompt": prompt,
            "provider_name": provider_name,
            "db": db,
            "team_id": team_id,
            "schema": schema,
            "model_override": model_override,
            "max_tokens": max_tokens,
        })
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
    assert call["provider_name"] == "cloudflare"


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
async def test_generate_linkedin_hashtags(mock_inference, mock_plain_english):
    hashtags = await linkedin_ai.generate_linkedin_hashtags("We love cloud computing", count=3)

    assert hashtags == ["cloud", "serverless", "cloudless"]


@pytest.mark.asyncio
async def test_suggest_best_time_to_post():
    times = await linkedin_ai.suggest_best_time_to_post()

    assert all(t["timezone"] == "Europe/Athens" for t in times)
    assert any(t["day"] == "Wednesday" and t["time"] == "09:00" for t in times)


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
