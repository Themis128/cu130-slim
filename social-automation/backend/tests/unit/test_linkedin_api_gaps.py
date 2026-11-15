"""Coverage for the LinkedIn feed + conversion-ingest API routers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api import linkedin_conversions as LC
from app.api import linkedin_feed as LFAPI
from app.services import linkedin_feed as LFSVC


def _request(key=None):
    return SimpleNamespace(headers={"x-conversion-key": key} if key else {})


# ── linkedin_conversions.ingest_conversion ──────────────────────────────


@pytest.mark.asyncio
async def test_ingest_rejects_bad_key(monkeypatch):
    monkeypatch.setenv("LINKEDIN_CONVERSION_INGEST_KEY", "sekret")
    body = LC.ConversionIn(event="audit_booked")
    with pytest.raises(HTTPException) as exc:
        await LC.ingest_conversion(body, _request("wrong"))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_ingest_accepts_no_key_configured(monkeypatch):
    monkeypatch.delenv("LINKEDIN_CONVERSION_INGEST_KEY", raising=False)
    body = LC.ConversionIn(event="not_a_real_event")
    out = await LC.ingest_conversion(body, _request())
    assert b"skipped" in out.body


@pytest.mark.asyncio
async def test_ingest_unknown_event_skips(monkeypatch):
    monkeypatch.delenv("LINKEDIN_CONVERSION_INGEST_KEY", raising=False)
    body = LC.ConversionIn(event="bogus_event")
    out = await LC.ingest_conversion(body, _request())
    assert out.status_code == 200


@pytest.mark.asyncio
async def test_ingest_no_rule_409(monkeypatch):
    monkeypatch.delenv("LINKEDIN_CONVERSION_INGEST_KEY", raising=False)
    event = next(iter(__import__("app.services.linkedin_advanced", fromlist=["x"]).CONVERSION_EVENTS))
    body = LC.ConversionIn(event=event, email="a@b.c")
    adv = __import__("app.services.linkedin_advanced", fromlist=["x"])
    monkeypatch.setattr(adv, "resolve_conversion_rule", AsyncMock(return_value=None))
    out = await LC.ingest_conversion(body, _request())
    assert out.status_code == 409


@pytest.mark.asyncio
async def test_ingest_success_streams_conversion(monkeypatch):
    monkeypatch.delenv("LINKEDIN_CONVERSION_INGEST_KEY", raising=False)
    adv = __import__("app.services.linkedin_advanced", fromlist=["x"])
    event = next(iter(adv.CONVERSION_EVENTS))
    body = LC.ConversionIn(event=event, email="User@Example.com", value=10.0, currency="EUR")
    monkeypatch.setattr(adv, "resolve_conversion_rule", AsyncMock(return_value="urn:lla:rule:1"))
    stream = AsyncMock(return_value={"ok": True, "id": "evt-1"})
    monkeypatch.setattr(adv, "stream_conversion", stream)
    out = await LC.ingest_conversion(body, _request())
    assert out.status_code == 200
    assert stream.await_args.kwargs["email_sha256"]
    assert stream.await_args.kwargs["conversion_value"] == 10.0


# ── linkedin_feed API routes ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_feed_posts_validates_urn_and_delegates(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        await LFAPI.feed_posts("not-a-urn", _user=None)
    assert exc.value.status_code == 400

    mock = AsyncMock(return_value={"posts": []})
    monkeypatch.setattr(LFSVC, "list_posts", mock)
    out = await LFAPI.feed_posts("urn:li:organization:1", count=999, _user=None)
    assert out == {"posts": []}
    mock.assert_awaited_once_with("urn:li:organization:1", count=50)


@pytest.mark.asyncio
async def test_feed_metadata_filters_and_delegates(monkeypatch):
    mock = AsyncMock(return_value={"metadata": {}})
    monkeypatch.setattr(LFSVC, "social_metadata", mock)
    await LFAPI.feed_metadata("urn:li:share:1, bad, urn:li:share:2", _user=None)
    mock.assert_awaited_once_with(["urn:li:share:1", "urn:li:share:2"])


@pytest.mark.asyncio
async def test_feed_comments_validates_and_delegates(monkeypatch):
    with pytest.raises(HTTPException):
        await LFAPI.feed_comments("nope", _user=None)
    mock = AsyncMock(return_value={"comments": []})
    monkeypatch.setattr(LFSVC, "list_comments", mock)
    await LFAPI.feed_comments("urn:li:share:1", count=500, _user=None)
    mock.assert_awaited_once_with("urn:li:share:1", count=50)


@pytest.mark.asyncio
async def test_feed_react_and_comment_delegate(monkeypatch):
    react = AsyncMock(return_value={"dry_run": True})
    comment = AsyncMock(return_value={"dry_run": True})
    monkeypatch.setattr(LFSVC, "react", react)
    monkeypatch.setattr(LFSVC, "comment", comment)
    body = LFAPI.ReactionIn(actor="member", root="urn:li:share:123456", reaction_type="LIKE")
    assert (await LFAPI.feed_react(body, _user=None))["dry_run"] is True
    cbody = LFAPI.CommentIn(actor="organization", object="urn:li:share:123456", text="hi")
    assert (await LFAPI.feed_comment(cbody, _user=None))["dry_run"] is True
