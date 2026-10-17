"""Remaining real branch arms in app/services/inference.py.

- A team with no AIProvider row falls back to env/catalog config.
- Workers AI img2img with no negative prompt, where every attempt is
  rate-limited and no HF key is set, raises the original error.
- An explicitly requested but unconfigured DMR is left out of the chain.
- JSON salvage: a stray closing brace, a list-wrapped object, empty
  output.
- Local SD3.5 with cfg_scale=0 leaves the field out of the payload.

All HTTP goes through respx; nothing leaves the process."""
from __future__ import annotations

import asyncio
import io
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

import app.services.inference as I


@pytest.mark.asyncio
async def test_provider_config_team_without_row_uses_env(monkeypatch):
    monkeypatch.setattr(I.settings, "GROQ_API_KEY", "groq-env", raising=False)
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)))
    url, model, key = await I._get_provider_config("groq", uuid.uuid4(), db)
    assert key == "groq-env" and "groq" in url and model
    db.execute.assert_awaited_once()


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_img2img_rate_limited_without_hf_key_raises(monkeypatch):
    import respx

    monkeypatch.setattr(I, "_workers_ai_credentials", lambda k: ("acct", "cf-key"))
    monkeypatch.setattr(I.settings, "HUGGINGFACE_API_KEY", "", raising=False)
    sleep = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep)
    with respx.mock(assert_all_called=True) as router:
        route = router.post(url__regex=r".*/accounts/acct/ai/run/.*").mock(
            return_value=httpx.Response(429, text="neurons exhausted")
        )
        with pytest.raises(HTTPException) as ei:
            await I._call_workers_ai_img2img("p", _png(), negative_prompt="", max_retries=2)
    assert ei.value.status_code == 502
    assert route.call_count == 2 and sleep.await_count == 2
    body = json.loads(route.calls[0].request.content)
    assert "negative_prompt" not in body
    assert not any("huggingface" in str(c.request.url) for c in router.calls)


@pytest.mark.asyncio
async def test_chain_requested_dmr_unconfigured_is_skipped(monkeypatch):
    monkeypatch.setattr(I.settings, "DMR_URL", "", raising=False)
    monkeypatch.setattr(I, "_ai_token", lambda: "cf-key")
    monkeypatch.setattr(I.settings, "CLOUDFLARE_ACCOUNT_ID", "acct", raising=False)
    assert await I._text_provider_chain("dmr", None, None) == ["cloudflare"]


def test_extract_json_object_ignores_stray_closing_brace():
    assert I._extract_json_object('} noise {"a": {"b": 1}} tail') == '{"a": {"b": 1}}'


def test_parse_json_response_unwraps_list_and_rejects_empty():
    assert I._parse_json_response('[{"a": 1}]') == {"a": 1}
    with pytest.raises(HTTPException) as ei:
        I._parse_json_response("   ")
    assert ei.value.status_code == 500


@pytest.mark.asyncio
async def test_local_sd35_zero_cfg_omits_field():
    import base64

    import respx

    url = "http://sd35.local/v1/genimg"
    with respx.mock() as router:
        post = router.post(url).mock(return_value=httpx.Response(
            200, json={"artifacts": [{"base64": base64.b64encode(b"IMG").decode()}]}))
        assert await I._call_local_sd35("p", url, cfg_scale=0) == b"IMG"
    body = json.loads(post.calls[0].request.content)
    assert "cfg_scale" not in body and "negative_prompt" not in body
