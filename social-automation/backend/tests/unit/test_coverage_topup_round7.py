"""Coverage top-up: limiter wrapper, media probe exception path, minio head
metadata parsing, lead intent classify, and the near-duplicate gate branch."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from slowapi.errors import RateLimitExceeded

# ── app.core.limiter — _safe_check_request_limit wrapper ────────────────────


def test_safe_check_request_limit_passes_through_rate_limit_exceeded(monkeypatch):
    import app.core.limiter as L

    sentinel = RateLimitExceeded(SimpleNamespace(limit="1/second", error_message="slow down"))
    monkeypatch.setattr(L, "_orig_check_request_limit", lambda *a, **k: (_ for _ in ()).throw(sentinel))
    with pytest.raises(RateLimitExceeded) as ei:
        L._safe_check_request_limit(object(), object())
    assert ei.value is sentinel


def test_safe_check_request_limit_wraps_storage_errors(monkeypatch):
    import app.core.limiter as L

    def boom(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(L, "_orig_check_request_limit", boom)
    with pytest.raises(RateLimitExceeded) as ei:
        L._safe_check_request_limit(object(), object())
    assert "rate limit check unavailable" in str(ei.value.detail)
    assert "ConnectionError" in ei.value.detail


def test_safe_check_request_limit_returns_orig_result(monkeypatch):
    import app.core.limiter as L

    monkeypatch.setattr(L, "_orig_check_request_limit", lambda *a, **k: "ok")
    assert L._safe_check_request_limit(object(), object()) == "ok"


# ── app.services.media_url_probe — transport failure path ──────────────────


@pytest.mark.asyncio
async def test_probe_one_transport_error_returns_failure():
    from app.services import media_url_probe as M

    client = MagicMock()
    client.stream.side_effect = RuntimeError("connection reset")
    out = await M._probe_one(client, "https://example.com/x.png", timeout=1.0)
    assert out["ok"] is False
    assert out["status"] is None
    assert "RuntimeError" in out["error"]


# ── app.services.minio_storage.head_object — metadata normalization ─────────


@pytest.mark.asyncio
async def test_head_object_normalizes_headers_and_bad_size(monkeypatch):
    from app.services import minio_storage as S

    monkeypatch.setattr(S, "ensure_bucket", lambda: True)
    fake = MagicMock()
    fake.head_object.return_value = {
        "Content-Length": "not-a-number",
        "ETag": '"abc123"',
        "Content-Type": "image/png",
        "Last-Modified": "Wed, 01 Jan 2026 00:00:00 GMT",
    }
    monkeypatch.setattr(S, "_client", lambda: fake)

    out = await S.head_object("team/x.png")
    assert out["key"] == "team/x.png"
    assert out["size"] is None
    assert out["etag"] == "abc123"
    assert out["content_type"] == "image/png"
    assert out["last_modified"].startswith("Wed, 01 Jan 2026")


@pytest.mark.asyncio
async def test_head_object_parses_content_length(monkeypatch):
    from app.services import minio_storage as S

    monkeypatch.setattr(S, "ensure_bucket", lambda: True)
    fake = MagicMock()
    fake.head_object.return_value = {"content-length": "4096", "etag": "e1"}
    monkeypatch.setattr(S, "_client", lambda: fake)

    out = await S.head_object("k")
    assert out["size"] == 4096
    assert out["etag"] == "e1"


# ── app.services.lead_capture.classify_inbound_intent ──────────────────────


@pytest.mark.asyncio
async def test_classify_inbound_intent_returns_ai_label(monkeypatch):
    from app.services import ai_functions, lead_capture

    ai_functions.ai_classify = AsyncMock(return_value="buying-interest")
    out = await lead_capture.classify_inbound_intent("price for setup?")
    assert out == "buying-interest"
    ai_functions.ai_classify.assert_awaited_once()


# ── app.services.content_sanitizer.content_quality_gate near-dup branch ─────


def test_content_quality_gate_rejects_near_duplicate():
    from app.services.content_sanitizer import content_quality_gate

    text = "cloudless.gr runs your whole digital department for small businesses"
    ok, reason = content_quality_gate(text, [text])
    assert ok is False
    assert "near-duplicate" in reason


def test_content_quality_gate_empty_text():
    from app.services.content_sanitizer import content_quality_gate

    ok, reason = content_quality_gate("   ", ["anything"])
    assert ok is False
    assert reason == "empty"
