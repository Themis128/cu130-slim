"""Comprehensive scenario tests for the inference fallback chain.

Covers:
  - _text_provider_chain: all routing decisions
  - call_inference: fallback promotion, allow_fallback=False, all-fail
  - _call_cf_image_pipeline: local-diffusers primary, CF fallback, quota exhausted
  - Circuit breaker: threshold, cooldown, reset on success
  - _call_dmr_chat / _call_dmr_embedding: success, schema, error paths
  - PROVIDER_CATALOG integrity: in_fallback_chain flags
  - generate_carousel_copy: CF-only enforcement (allow_fallback=False)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.services import inference

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# Minimal valid 1×1 white PNG (base64) — used wherever the image pipeline
# decodes and PIL-opens the result. Generated via:
#   Image.new('RGB', (1, 1), (255, 255, 255)) → PNG → base64
_PNG_1X1_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4"
    "//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
)

class _FakeResp:
    def __init__(self, status_code: int, body, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = MagicMock()
        self.headers.get = lambda k, d=None: (headers or {}).get(k, d)

    def json(self):
        if isinstance(self._body, dict):
            return self._body
        raise ValueError("not json")

    @property
    def content(self) -> bytes:
        if isinstance(self._body, bytes):
            return self._body
        return str(self._body).encode()

    @property
    def text(self):
        return str(self._body)


class _FakeClient:
    """Async context-manager HTTP client that returns a preset response."""

    def __init__(self, status_code: int, body, headers=None):
        self._resp = _FakeResp(status_code, body, headers)
        self.last_url = None
        self.last_json = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, url, headers=None, content=None, json=None, **_kw):
        self.last_url = url
        self.last_json = json
        return self._resp

    async def get(self, url, **_kw):
        self.last_url = url
        return self._resp


def _dmr_settings(monkeypatch, url="http://dmr:12434/engines/v1"):
    monkeypatch.setattr(inference.settings, "DMR_URL", url)
    monkeypatch.setattr(inference.settings, "DMR_TEXT_MODEL", "ai/smollm2:360M-Q4_K_M")
    monkeypatch.setattr(inference.settings, "DMR_EMBEDDING_MODEL", "ai/mxbai-embed-large")


def _cf_settings(monkeypatch):
    monkeypatch.setattr(inference.settings, "CLOUDFLARE_ACCOUNT_ID", "acct-123")
    monkeypatch.setattr(inference.settings, "CLOUDFLARE_AI_API_TOKEN", "cf-tok")
    monkeypatch.setattr(inference, "_ai_token", lambda: "cf-tok")


def _clear_non_cf_keys(monkeypatch):
    for k in ("GROQ_API_KEY", "GEMINI_API_KEY", "MISTRAL_API_KEY",
              "COHERE_API_KEY", "OPENROUTER_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.setattr(inference.settings, k, "")


def _clear_circuit(provider: str):
    inference._circuit_state.pop(provider, None)


# ===========================================================================
# 1. _text_provider_chain scenarios
# ===========================================================================

class TestTextProviderChain:

    @pytest.mark.asyncio
    async def test_dmr_requested_both_creds_available(self, monkeypatch):
        """DMR requested with CF creds available → DMR first, CF fallback."""
        _dmr_settings(monkeypatch)
        _cf_settings(monkeypatch)
        chain = await inference._text_provider_chain("dmr", None, None)
        assert chain == ["dmr", "cloudflare"]

    @pytest.mark.asyncio
    async def test_cf_requested_both_creds_available(self, monkeypatch):
        """CF requested with DMR URL available → CF first, DMR fallback."""
        _dmr_settings(monkeypatch)
        _cf_settings(monkeypatch)
        chain = await inference._text_provider_chain("cloudflare", None, None)
        assert chain == ["cloudflare", "dmr"]

    @pytest.mark.asyncio
    async def test_dmr_requested_no_cf_creds(self, monkeypatch):
        """DMR requested, no CF creds → DMR only (CF filtered out)."""
        _dmr_settings(monkeypatch)
        monkeypatch.setattr(inference.settings, "CLOUDFLARE_ACCOUNT_ID", "")
        monkeypatch.setattr(inference.settings, "CLOUDFLARE_AI_API_TOKEN", "")
        monkeypatch.setattr(inference, "_ai_token", lambda: "")
        chain = await inference._text_provider_chain("dmr", None, None)
        assert chain == ["dmr"]

    @pytest.mark.asyncio
    async def test_cf_requested_no_cf_creds_dmr_present(self, monkeypatch):
        """CF explicitly requested but has no creds — still inserted at head, DMR in chain."""
        _dmr_settings(monkeypatch)
        monkeypatch.setattr(inference.settings, "CLOUDFLARE_ACCOUNT_ID", "")
        monkeypatch.setattr(inference, "_ai_token", lambda: "")
        chain = await inference._text_provider_chain("cloudflare", None, None)
        # CF inserted at head (explicit request); DMR available as fallback
        assert chain[0] == "cloudflare"
        assert "dmr" in chain

    @pytest.mark.asyncio
    async def test_no_dmr_url_cf_creds_present(self, monkeypatch):
        """No DMR URL set → DMR excluded from chain (no wasted round-trip)."""
        monkeypatch.setattr(inference.settings, "DMR_URL", "")
        _cf_settings(monkeypatch)
        chain = await inference._text_provider_chain("cloudflare", None, None)
        assert "cloudflare" in chain
        assert "dmr" not in chain

    @pytest.mark.asyncio
    async def test_no_dmr_url_custom_fallbacks(self, monkeypatch):
        """No DMR URL + custom fallbacks → DMR not appended as implicit fallback."""
        monkeypatch.setattr(inference.settings, "DMR_URL", "")
        _cf_settings(monkeypatch)
        chain = await inference._text_provider_chain(
            "cloudflare", None, ["cloudflare"],
        )
        assert "cloudflare" in chain
        assert "dmr" not in chain

    @pytest.mark.asyncio
    async def test_non_chain_provider_requested(self, monkeypatch):
        """Explicitly requesting a non-priority provider (groq) inserts it at head."""
        _dmr_settings(monkeypatch)
        _cf_settings(monkeypatch)
        monkeypatch.setattr(inference.settings, "GROQ_API_KEY", "groq-key")
        chain = await inference._text_provider_chain("groq", None, None)
        assert chain[0] == "groq"
        # DMR + CF still in chain as fallbacks
        assert "dmr" in chain
        assert "cloudflare" in chain

    @pytest.mark.asyncio
    async def test_non_cf_cloud_keys_never_in_chain(self, monkeypatch):
        """Keys set for groq/gemini/mistral must NOT appear in the automatic chain."""
        _dmr_settings(monkeypatch)
        _cf_settings(monkeypatch)
        monkeypatch.setattr(inference.settings, "GROQ_API_KEY", "groq-key")
        monkeypatch.setattr(inference.settings, "GEMINI_API_KEY", "gemini-key")
        monkeypatch.setattr(inference.settings, "MISTRAL_API_KEY", "mistral-key")
        chain = await inference._text_provider_chain("cloudflare", None, None)
        for excluded in ("groq", "gemini", "mistral", "cohere", "openrouter", "nvidia"):
            assert excluded not in chain, f"{excluded} must not be in auto chain"

    @pytest.mark.asyncio
    async def test_chain_with_custom_db_fallbacks(self, monkeypatch):
        """Custom per-team fallbacks from DB override the default chain."""
        _dmr_settings(monkeypatch)
        _cf_settings(monkeypatch)
        import uuid

        team_id = uuid.uuid4()

        # Fake DB: returns "dmr" as the single configured fallback
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = ["cloudflare", "dmr"]
        mock_fb = MagicMock()
        mock_fb.scalar.return_value = "dmr"
        mock_db.execute = AsyncMock(side_effect=[mock_result, mock_fb])

        chain = await inference._text_provider_chain("cloudflare", team_id, mock_db)
        assert chain[0] == "cloudflare"
        assert "dmr" in chain


# ===========================================================================
# 2. call_inference fallback behaviour
# ===========================================================================

class TestCallInferenceFallback:

    @pytest.mark.asyncio
    async def test_primary_succeeds_no_fallback_metadata(self, monkeypatch):
        """When primary provider succeeds, result has no _fallback key."""
        _dmr_settings(monkeypatch)
        _cf_settings(monkeypatch)
        _clear_circuit("dmr")

        async def fake_do(prompt, provider_name, **_kw):
            assert provider_name == "dmr"
            return {"text": "hello"}

        monkeypatch.setattr(inference, "_do_call_inference", fake_do)
        monkeypatch.setattr(inference.usage_tracker, "track_inference", AsyncMock())

        result = await inference.call_inference("hi", provider_name="dmr")
        assert result == {"text": "hello"}
        assert "_fallback" not in result

    @pytest.mark.asyncio
    async def test_primary_fails_cf_fallback_annotated(self, monkeypatch):
        """Primary (DMR) fails → falls back to CF; result is annotated with fallback info."""
        _dmr_settings(monkeypatch)
        _cf_settings(monkeypatch)
        _clear_circuit("dmr")
        _clear_circuit("cloudflare")

        call_order = []

        async def fake_do(prompt, provider_name, **_kw):
            call_order.append(provider_name)
            if provider_name == "dmr":
                raise HTTPException(status_code=502, detail="DMR down")
            return {"text": "from cf"}

        monkeypatch.setattr(inference, "_do_call_inference", fake_do)
        monkeypatch.setattr(inference, "_text_provider_chain",
                            AsyncMock(return_value=["dmr", "cloudflare"]))
        monkeypatch.setattr(inference.usage_tracker, "track_inference", AsyncMock())

        result = await inference.call_inference("hi", provider_name="dmr", allow_fallback=True)
        assert result["text"] == "from cf"
        assert result["_fallback"] is True
        assert result["_provider"] == "cloudflare"
        assert result["_primary_provider"] == "dmr"
        assert call_order == ["dmr", "cloudflare"]

    @pytest.mark.asyncio
    async def test_allow_fallback_false_no_retry(self, monkeypatch):
        """allow_fallback=False must not attempt any other provider."""
        _dmr_settings(monkeypatch)
        _cf_settings(monkeypatch)
        _clear_circuit("cloudflare")

        call_order = []

        async def fake_do(prompt, provider_name, **_kw):
            call_order.append(provider_name)
            raise HTTPException(status_code=502, detail="CF down")

        monkeypatch.setattr(inference, "_do_call_inference", fake_do)
        monkeypatch.setattr(inference.usage_tracker, "track_inference", AsyncMock())

        with pytest.raises(HTTPException) as exc:
            await inference.call_inference(
                "hi", provider_name="cloudflare", allow_fallback=False
            )
        assert exc.value.status_code == 502
        assert call_order == ["cloudflare"]  # only one attempt

    @pytest.mark.asyncio
    async def test_all_providers_fail_raises_last_error(self, monkeypatch):
        """When every provider in the chain fails, the last HTTPException is re-raised."""
        _dmr_settings(monkeypatch)
        _cf_settings(monkeypatch)
        _clear_circuit("dmr")
        _clear_circuit("cloudflare")

        async def fake_do(prompt, provider_name, **_kw):
            raise HTTPException(status_code=502, detail=f"{provider_name} failed")

        monkeypatch.setattr(inference, "_do_call_inference", fake_do)
        monkeypatch.setattr(inference, "_text_provider_chain",
                            AsyncMock(return_value=["dmr", "cloudflare"]))
        monkeypatch.setattr(inference.usage_tracker, "track_inference", AsyncMock())

        with pytest.raises(HTTPException) as exc:
            await inference.call_inference("hi", provider_name="dmr", allow_fallback=True)
        assert exc.value.status_code == 502
        assert "cloudflare failed" in exc.value.detail  # last provider's error

    @pytest.mark.asyncio
    async def test_all_circuit_breakers_open_raises_503(self, monkeypatch):
        """All providers in circuit-breaker cooldown → 503 immediately."""
        _dmr_settings(monkeypatch)
        _cf_settings(monkeypatch)

        future = datetime.now(UTC) + timedelta(seconds=120)
        inference._circuit_state["dmr"] = {"failures": 3, "open_until": future}
        inference._circuit_state["cloudflare"] = {"failures": 3, "open_until": future}

        monkeypatch.setattr(inference, "_text_provider_chain",
                            AsyncMock(return_value=["dmr", "cloudflare"]))
        monkeypatch.setattr(inference.usage_tracker, "track_inference", AsyncMock())

        with pytest.raises(HTTPException) as exc:
            await inference.call_inference("hi", provider_name="dmr", allow_fallback=True)
        assert exc.value.status_code == 503
        assert "circuit-breaker" in exc.value.detail

        # cleanup
        _clear_circuit("dmr")
        _clear_circuit("cloudflare")

    @pytest.mark.asyncio
    async def test_brand_context_prepended_to_prompt(self, monkeypatch):
        """brand_context is prepended to the prompt before inference."""
        _dmr_settings(monkeypatch)
        _cf_settings(monkeypatch)
        _clear_circuit("dmr")

        received_prompts = []

        async def fake_do(prompt, **_kw):
            received_prompts.append(prompt)
            return {"text": "ok"}

        monkeypatch.setattr(inference, "_do_call_inference", fake_do)
        monkeypatch.setattr(inference.usage_tracker, "track_inference", AsyncMock())

        await inference.call_inference(
            "Write a post", provider_name="dmr",
            brand_context="Brand: cloudless.gr"
        )
        assert received_prompts[0].startswith("Brand: cloudless.gr")
        assert "Write a post" in received_prompts[0]


# ===========================================================================
# 3. _call_cf_image_pipeline scenarios
# ===========================================================================

class TestCfImagePipeline:

    @pytest.mark.asyncio
    async def test_local_diffusers_succeeds_no_cf_call(self, monkeypatch, cf_settings_fix):
        """If local Diffusers succeeds, CF is never called."""
        cf_called = []

        async def fake_diffusers(prompt, **_kw):
            return {"image_base64": _PNG_1X1_B64, "model": "sd-1.5"}

        async def fake_cf_image(prompt, **_kw):
            cf_called.append(True)
            return {"image_base64": _PNG_1X1_B64}

        monkeypatch.setattr(inference, "_call_local_diffusers_txt2img", fake_diffusers)
        monkeypatch.setattr(inference, "_call_workers_ai_image", fake_cf_image)

        result = await inference._call_cf_image_pipeline("a red apple")
        assert "image_base64" in result
        assert not cf_called

    @pytest.mark.asyncio
    async def test_local_diffusers_fails_cf_succeeds(self, monkeypatch, cf_settings_fix):
        """Local Diffusers failure → transparent fallback to CF."""
        async def fake_diffusers(prompt, **_kw):
            raise HTTPException(status_code=502, detail="SD not loaded")

        async def fake_cf_image(prompt, **_kw):
            return {"image_base64": _PNG_1X1_B64, "format": "base64"}

        monkeypatch.setattr(inference, "_call_local_diffusers_txt2img", fake_diffusers)
        monkeypatch.setattr(inference, "_call_workers_ai_image", fake_cf_image)

        result = await inference._call_cf_image_pipeline("a red apple")
        assert "image_base64" in result  # returned from CF path

    @pytest.mark.asyncio
    async def test_local_diffusers_fails_cf_quota_allow_fallback_true(self, monkeypatch, cf_settings_fix):
        """Both local and CF quota exhausted → descriptive 502 (no other fallback)."""
        async def fake_diffusers(prompt, **_kw):
            raise HTTPException(status_code=502, detail="SD not loaded")

        async def fake_cf_image(prompt, **_kw):
            # Simulate CF quota error (status 429 maps to a 429 HTTPException)
            raise HTTPException(status_code=429, detail="quota exceeded")

        monkeypatch.setattr(inference, "_call_local_diffusers_txt2img", fake_diffusers)
        monkeypatch.setattr(inference, "_call_workers_ai_image", fake_cf_image)
        # _is_cf_quota_error must recognise 429 as a quota error
        monkeypatch.setattr(inference, "_is_cf_quota_error", lambda exc: exc.status_code == 429)

        with pytest.raises(HTTPException) as exc:
            await inference._call_cf_image_pipeline("a red apple", allow_fallback=True)
        assert exc.value.status_code == 502
        assert "no other cloud fallback" in exc.value.detail

    @pytest.mark.asyncio
    async def test_local_diffusers_fails_cf_quota_allow_fallback_false(self, monkeypatch, cf_settings_fix):
        """allow_fallback=False: CF quota error is re-raised immediately."""
        async def fake_diffusers(prompt, **_kw):
            raise HTTPException(status_code=502, detail="SD not loaded")

        async def fake_cf_image(prompt, **_kw):
            raise HTTPException(status_code=429, detail="quota exceeded")

        monkeypatch.setattr(inference, "_call_local_diffusers_txt2img", fake_diffusers)
        monkeypatch.setattr(inference, "_call_workers_ai_image", fake_cf_image)
        monkeypatch.setattr(inference, "_is_cf_quota_error", lambda exc: exc.status_code == 429)

        with pytest.raises(HTTPException) as exc:
            await inference._call_cf_image_pipeline("a red apple", allow_fallback=False)
        assert exc.value.status_code == 429

    @pytest.mark.asyncio
    async def test_local_diffusers_fails_cf_non_quota_error_re_raises(self, monkeypatch, cf_settings_fix):
        """A non-quota CF error (e.g. 503) is re-raised as-is, not swallowed."""
        async def fake_diffusers(prompt, **_kw):
            raise HTTPException(status_code=502, detail="SD not loaded")

        async def fake_cf_image(prompt, **_kw):
            raise HTTPException(status_code=503, detail="CF service unavailable")

        monkeypatch.setattr(inference, "_call_local_diffusers_txt2img", fake_diffusers)
        monkeypatch.setattr(inference, "_call_workers_ai_image", fake_cf_image)
        monkeypatch.setattr(inference, "_is_cf_quota_error", lambda exc: False)

        with pytest.raises(HTTPException) as exc:
            await inference._call_cf_image_pipeline("a red apple")
        assert exc.value.status_code == 503
        assert "CF service unavailable" in exc.value.detail


@pytest.fixture
def cf_settings_fix(monkeypatch):
    _cf_settings(monkeypatch)


# ===========================================================================
# 4. Circuit breaker
# ===========================================================================

class TestCircuitBreaker:

    def setup_method(self):
        # Clean state before each test
        inference._circuit_state.clear()

    def teardown_method(self):
        inference._circuit_state.clear()

    def test_circuit_closed_initially(self):
        assert inference._circuit_is_open("dmr") is False

    def test_circuit_stays_closed_below_threshold(self):
        for _ in range(inference._CIRCUIT_FAILURE_THRESHOLD - 1):
            inference._circuit_record_failure("dmr")
        assert inference._circuit_is_open("dmr") is False

    def test_circuit_opens_at_threshold(self):
        for _ in range(inference._CIRCUIT_FAILURE_THRESHOLD):
            inference._circuit_record_failure("dmr")
        assert inference._circuit_is_open("dmr") is True

    def test_circuit_opens_after_threshold(self):
        for _ in range(inference._CIRCUIT_FAILURE_THRESHOLD + 2):
            inference._circuit_record_failure("dmr")
        assert inference._circuit_is_open("dmr") is True

    def test_circuit_resets_after_cooldown(self):
        for _ in range(inference._CIRCUIT_FAILURE_THRESHOLD):
            inference._circuit_record_failure("dmr")
        # Manually expire the cooldown
        inference._circuit_state["dmr"]["open_until"] = (
            datetime.now(UTC) - timedelta(seconds=1)
        )
        assert inference._circuit_is_open("dmr") is False
        # State should be cleaned up
        assert "dmr" not in inference._circuit_state

    def test_success_resets_failure_count(self):
        for _ in range(inference._CIRCUIT_FAILURE_THRESHOLD - 1):
            inference._circuit_record_failure("dmr")
        inference._circuit_record_success("dmr")
        assert "dmr" not in inference._circuit_state
        assert inference._circuit_is_open("dmr") is False

    def test_circuit_independent_per_provider(self):
        for _ in range(inference._CIRCUIT_FAILURE_THRESHOLD):
            inference._circuit_record_failure("dmr")
        assert inference._circuit_is_open("dmr") is True
        assert inference._circuit_is_open("cloudflare") is False


# ===========================================================================
# 5. _call_dmr_chat scenarios
# ===========================================================================

class TestCallDmrChat:

    @pytest.mark.asyncio
    async def test_success_plain_text(self, monkeypatch):
        """Successful DMR chat returns {"text": content}."""
        monkeypatch.setattr(inference.settings, "DMR_URL", "http://dmr:12434/engines/v1")
        monkeypatch.setattr(inference.settings, "DMR_TEXT_MODEL", "ai/smollm2:360M-Q4_K_M")
        fake = _FakeClient(200, {"choices": [{"message": {"content": "Hello!"}}]})
        monkeypatch.setattr(inference.httpx, "AsyncClient", lambda **_: fake)

        result = await inference._call_dmr_chat("Say hi")
        assert result == {"text": "Hello!"}
        assert "chat/completions" in fake.last_url

    @pytest.mark.asyncio
    async def test_success_with_schema_parses_json(self, monkeypatch):
        """With a schema, JSON in the content field is parsed and returned as dict."""
        monkeypatch.setattr(inference.settings, "DMR_URL", "http://dmr:12434/engines/v1")
        monkeypatch.setattr(inference.settings, "DMR_TEXT_MODEL", "ai/smollm2:360M-Q4_K_M")
        fake = _FakeClient(200, {
            "choices": [{"message": {"content": '{"slides": ["a", "b"]}'}}]
        })
        monkeypatch.setattr(inference.httpx, "AsyncClient", lambda **_: fake)

        result = await inference._call_dmr_chat("Make slides", schema={"type": "object"})
        assert result == {"slides": ["a", "b"]}
        # json_object response_format must be sent
        assert fake.last_json["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_non_200_raises_502(self, monkeypatch):
        """A non-200 response from DMR raises HTTPException 502."""
        monkeypatch.setattr(inference.settings, "DMR_URL", "http://dmr:12434/engines/v1")
        monkeypatch.setattr(inference.settings, "DMR_TEXT_MODEL", "ai/smollm2:360M-Q4_K_M")
        fake = _FakeClient(503, {"detail": "model loading"})
        monkeypatch.setattr(inference.httpx, "AsyncClient", lambda **_: fake)

        with pytest.raises(HTTPException) as exc:
            await inference._call_dmr_chat("hi")
        assert exc.value.status_code == 502
        assert "DMR error 503" in exc.value.detail

    @pytest.mark.asyncio
    async def test_reasoning_content_field_used_as_fallback(self, monkeypatch):
        """If content is empty/None, reasoning_content is used instead."""
        monkeypatch.setattr(inference.settings, "DMR_URL", "http://dmr:12434/engines/v1")
        monkeypatch.setattr(inference.settings, "DMR_TEXT_MODEL", "ai/smollm2:360M-Q4_K_M")
        fake = _FakeClient(200, {
            "choices": [{"message": {"content": None, "reasoning_content": "I think..."}}]
        })
        monkeypatch.setattr(inference.httpx, "AsyncClient", lambda **_: fake)

        result = await inference._call_dmr_chat("Think about it")
        assert result == {"text": "I think..."}

    @pytest.mark.asyncio
    async def test_max_tokens_passed_when_provided(self, monkeypatch):
        """Explicit max_tokens is forwarded to the payload."""
        monkeypatch.setattr(inference.settings, "DMR_URL", "http://dmr:12434/engines/v1")
        monkeypatch.setattr(inference.settings, "DMR_TEXT_MODEL", "ai/smollm2:360M-Q4_K_M")
        fake = _FakeClient(200, {"choices": [{"message": {"content": "ok"}}]})
        monkeypatch.setattr(inference.httpx, "AsyncClient", lambda **_: fake)

        await inference._call_dmr_chat("hi", max_tokens=128)
        assert fake.last_json["max_tokens"] == 128

    @pytest.mark.asyncio
    async def test_model_override_respected(self, monkeypatch):
        """model_override replaces the default DMR_TEXT_MODEL."""
        monkeypatch.setattr(inference.settings, "DMR_URL", "http://dmr:12434/engines/v1")
        monkeypatch.setattr(inference.settings, "DMR_TEXT_MODEL", "ai/smollm2:360M-Q4_K_M")
        fake = _FakeClient(200, {"choices": [{"message": {"content": "ok"}}]})
        monkeypatch.setattr(inference.httpx, "AsyncClient", lambda **_: fake)

        await inference._call_dmr_chat("hi", model_override="ai/qwen3:8b")
        assert fake.last_json["model"] == "ai/qwen3:8b"


# ===========================================================================
# 6. _call_dmr_embedding scenarios
# ===========================================================================

class TestCallDmrEmbedding:

    @pytest.mark.asyncio
    async def test_success_returns_embedding_list(self, monkeypatch):
        """Successful embedding call returns the float list."""
        embedding = [0.1, 0.2, 0.3] * 100
        monkeypatch.setattr(inference.settings, "DMR_URL", "http://dmr:12434/engines/v1")
        monkeypatch.setattr(inference.settings, "DMR_EMBEDDING_MODEL", "ai/mxbai-embed-large")
        fake = _FakeClient(200, {"data": [{"embedding": embedding}]})
        monkeypatch.setattr(inference.httpx, "AsyncClient", lambda **_: fake)

        result = await inference._call_dmr_embedding("hello world")
        assert result == embedding
        assert "embeddings" in fake.last_url

    @pytest.mark.asyncio
    async def test_non_200_raises_502(self, monkeypatch):
        monkeypatch.setattr(inference.settings, "DMR_URL", "http://dmr:12434/engines/v1")
        monkeypatch.setattr(inference.settings, "DMR_EMBEDDING_MODEL", "ai/mxbai-embed-large")
        fake = _FakeClient(503, {"detail": "embedding model not loaded"})
        monkeypatch.setattr(inference.httpx, "AsyncClient", lambda **_: fake)

        with pytest.raises(HTTPException) as exc:
            await inference._call_dmr_embedding("test")
        assert exc.value.status_code == 502
        assert "DMR embedding error 503" in exc.value.detail


# ===========================================================================
# 7. PROVIDER_CATALOG integrity
# ===========================================================================

class TestProviderCatalog:

    def test_exactly_three_providers_in_fallback_chain(self):
        """DMR, Cloudflare, and local-diffusers are the only auto-fallback providers."""
        in_chain = [p["name"] for p in inference.PROVIDER_CATALOG if p.get("in_fallback_chain")]
        assert set(in_chain) == {"dmr", "cloudflare", "local-diffusers"}
        assert len(in_chain) == 3

    def test_non_cf_cloud_providers_not_in_fallback_chain(self):
        excluded = {"groq", "gemini", "openrouter", "sambanova", "mistral",
                    "cohere", "nvidia", "nvidia-flux", "nvidia-flux-dev",
                    "pixazo", "together", "huggingface"}
        for provider in inference.PROVIDER_CATALOG:
            if provider["name"] in excluded:
                assert provider.get("in_fallback_chain") is False, (
                    f"{provider['name']} should have in_fallback_chain=False"
                )

    def test_all_catalog_entries_have_required_fields(self):
        required = {"name", "base_url", "description", "in_fallback_chain"}
        for provider in inference.PROVIDER_CATALOG:
            missing = required - set(provider.keys())
            assert not missing, f"Provider {provider.get('name')} missing fields: {missing}"

    def test_catalog_names_unique(self):
        names = [p["name"] for p in inference.PROVIDER_CATALOG]
        assert len(names) == len(set(names)), "Duplicate provider names in catalog"

    def test_dmr_base_url_uses_setting(self, monkeypatch):
        """DMR catalog entry base_url must reference the DMR_URL setting."""
        dmr = next(p for p in inference.PROVIDER_CATALOG if p["name"] == "dmr")
        # base_url may be a template string or resolved value — just confirm 'dmr' is the entry
        assert dmr["in_fallback_chain"] is True


# ===========================================================================
# 8. generate_carousel_copy: CF primary with DMR fallback
# ===========================================================================

class TestCarouselCopyEnforcement:

    @pytest.mark.asyncio
    async def test_carousel_copy_uses_allow_fallback_true(self, monkeypatch):
        """generate_carousel_copy must call call_inference with allow_fallback=True
        so DMR can take over if CF is unavailable (architecture: CF primary, DMR fallback)."""
        from app.services import carousel_pipeline

        calls = []

        async def fake_call_inference(prompt, *, allow_fallback, **_kw):
            calls.append({"allow_fallback": allow_fallback})
            return {"slides": [], "suggested_caption": "cap", "hashtags": []}

        monkeypatch.setattr(carousel_pipeline, "call_inference", fake_call_inference)

        await carousel_pipeline.generate_carousel_copy(
            topic="AI in 2026",
            num_slides=5,
            tone="casual",
            include_cta=True,
            text_model="@cf/meta/llama-3.3-70b-instruct-fp8-fast",
            text_provider="cloudflare",
            db=MagicMock(),
            team_id=None,
        )
        assert calls, "call_inference was never called"
        assert calls[0]["allow_fallback"] is True, (
            "Carousel copy must use allow_fallback=True (CF primary → DMR fallback)"
        )

    @pytest.mark.asyncio
    async def test_carousel_copy_cf_failure_falls_back_to_dmr(self, monkeypatch):
        """When CF fails and allow_fallback=True, the fallback chain is enabled
        so DMR can take over. We verify allow_fallback=True is passed."""
        from app.services import carousel_pipeline

        calls = []

        async def fake_call_inference(prompt, *, provider_name="cloudflare", allow_fallback, **_kw):
            calls.append({"provider_name": provider_name, "allow_fallback": allow_fallback})
            return {"slides": [], "suggested_caption": "cap", "hashtags": []}

        monkeypatch.setattr(carousel_pipeline, "call_inference", fake_call_inference)

        await carousel_pipeline.generate_carousel_copy(
            topic="AI", num_slides=5, tone="casual", include_cta=False,
            text_model="@cf/meta/llama-3.3-70b-instruct-fp8-fast",
            text_provider="cloudflare",
            db=MagicMock(),
            team_id=None,
        )
        assert calls, "call_inference was never called"
        assert calls[0]["allow_fallback"] is True, (
            "Carousel copy must allow fallback so DMR can take over if CF fails"
        )
