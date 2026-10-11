"""Unit tests for app/services/whatsapp_api.py — client + webhook parser."""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.whatsapp_api as W


@pytest.fixture
def client():
    c = W.WhatsAppAPIClient(access_token="EAtok", phone_number_id="12345",
                            business_phone="+302101234567")
    calls: list[dict] = []
    c._calls = calls

    async def _request(method, path, **kw):
        calls.append({"method": method, "path": path, **kw})
        return {"ok": 1}

    c._client = SimpleNamespace(
        request=AsyncMock(side_effect=_request),
        get_bytes=AsyncMock(return_value=b"bytes"))
    return c


# ── init + validators ─────────────────────────────────────────────────


def test_init_validation():
    with pytest.raises(ValueError, match="Access token"):
        W.WhatsAppAPIClient(access_token="", phone_number_id="1")
    with pytest.raises(ValueError):
        W.WhatsAppAPIClient(access_token="t", phone_number_id="bad id!")
    c = W.WhatsAppAPIClient("t", "123", api_version="/v21.0/")
    assert c.api_version == "v21.0/" or c.api_version == "v21.0"

    with pytest.raises(Exception):
        W._validate_phone("not a phone ###")


# ── send methods ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_methods(client):
    out = await client.send_text("+302101111111", "hi", preview_url=True,
                                 messaging_type="UPDATE")
    assert out == {"ok": 1}
    call = client._calls[0]
    assert call["method"] == "POST" and call["path"] == "12345/messages"
    assert call["json_body"]["text"]["preview_url"] is True

    await client.send_template("+302101111111", "welcome", "en_US")
    body = client._calls[1]["json_body"]
    assert body["type"] == "template" and body["template"]["name"] == "welcome"

    await client.send_image("+302101111111", "https://cdn/i.png",
                            caption="cap")
    body = client._calls[2]["json_body"]
    assert body["image"]["link"] == "https://cdn/i.png"
    assert body["image"]["caption"] == "cap"

    await client.send_document("+302101111111", "https://cdn/d.pdf",
                               filename="f.pdf", caption="c")
    body = client._calls[3]["json_body"]
    assert body["document"]["filename"] == "f.pdf"
    assert body["document"]["link"] == "https://cdn/d.pdf"

    await client.send_reaction("+302101111111", "wamid.1", "👍")
    body = client._calls[4]["json_body"]
    assert body["reaction"]["emoji"] == "👍"

    await client.send_location("+302101111111", 37.9, 23.7,
                               name="HQ", address="Athens")
    body = client._calls[5]["json_body"]
    assert body["location"]["latitude"] == 37.9

    await client.mark_message_read("wamid.2")
    body = client._calls[6]["json_body"]
    assert body["status"] == "read" and body["message_id"] == "wamid.2"


# ── media + profile + phone ops ───────────────────────────────────────


@pytest.mark.asyncio
async def test_media_and_profile(client):
    # upload — real temp file through the files kwarg
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"png")
        path = f.name
    try:
        await client.upload_media(path, mime_type="image/png")
    finally:
        os.unlink(path)
    call = client._calls[0]
    assert call["path"] == "12345/media"
    assert call["data"]["type"] == "image/png"

    # download — info → url → bytes
    client._client.request = AsyncMock(side_effect=[
        {"url": "https://cdn/f.bin"}])
    out = await client.download_media("777")
    assert out == b"bytes"
    client._client.get_bytes.assert_awaited_once()

    # no url → ValueError
    client._client.request = AsyncMock(return_value={})
    with pytest.raises(ValueError, match="No media URL"):
        await client.download_media("777")

    # business profile — data[0] unwrapped; missing → raw
    client._client.request = AsyncMock(return_value={
        "data": [{"about": "we help"}]})
    assert (await client.get_business_profile())["about"] == "we help"
    client._client.request = AsyncMock(return_value={"data": []})
    assert await client.get_business_profile() == {"data": []}

    # update — None values dropped
    client._client.request = AsyncMock(return_value={"success": True})
    await client.update_business_profile(
        {"about": "new", "address": None})
    body = client._client.request.await_args.kwargs["json_body"]
    assert body == {"messaging_product": "whatsapp", "about": "new"}


@pytest.mark.asyncio
async def test_phone_info_and_numbers(client):
    await client.get_phone_number_info()
    assert client._calls[0]["method"] == "GET" and "12345" in \
        client._calls[0]["path"]

    # field-rejection → fallback request without fields
    client._client.request = AsyncMock(side_effect=[
        Exception("fields rejected"), {"id": "999"}])
    out = await client.get_phone_number_info_by_id("999")
    assert out == {"id": "999"}
    assert client._client.request.await_count == 2

    # list phone numbers → data passthrough
    client._client.request = AsyncMock(return_value={"data": [{"id": "1"}]})
    out = await client.get_business_account_phone_numbers("111")
    assert out == [{"id": "1"}]

    # create_phone_number happy
    client._client.request = AsyncMock(return_value={"id": "pn1"})
    out = await client.create_phone_number(
        "111", "30", "2109999999", "My Biz")
    assert out == {"id": "pn1"}
    body = client._client.request.await_args.kwargs["json_body"]
    assert body["cc"] == "30" and body["verified_name"] == "My Biz"


@pytest.mark.asyncio
async def test_verification_flow(client):
    # bad code_method
    with pytest.raises(ValueError, match="code_method"):
        await client.request_verification_code("999", code_method="EMAIL")

    # already VERIFIED → skipped
    client._client.request = AsyncMock(return_value={
        "code_verification_status": "VERIFIED"})
    out = await client.request_verification_code("999")
    assert out["skipped"] is True and out["reason"] == "already_verified"

    # not verified → request_code POSTed
    client._client.request = AsyncMock(side_effect=[
        {"code_verification_status": "NOT_VERIFIED"},
        {"success": True}])
    out = await client.request_verification_code("999", code_method="VOICE",
                                                 language="el_GR")
    assert out == {"success": True}
    call = client._client.request.await_args
    assert "request_code" in call.args[1]
    assert call.kwargs["params"]["code_method"] == "VOICE"

    # status check raises → fallback fetch also fails handled → proceeds
    client._client.request = AsyncMock(side_effect=[
        Exception("x"), {"status": "ok"}, {"success": True}])
    out = await client.request_verification_code("999")
    assert out == {"success": True}

    # WhatsAppPhoneVerificationError re-raised enriched
    from app.services.whatsapp_cloud_client import WhatsAppPhoneVerificationError
    err = WhatsAppPhoneVerificationError(
        status_code=400, url="u", response_text="t",
        error={"code": 136024, "message": "already verified"},
        rate_limit=False)
    client._client.request = AsyncMock(side_effect=[
        {"code_verification_status": "NOT_VERIFIED"}, err])
    with pytest.raises(WhatsAppPhoneVerificationError):
        await client.request_verification_code("999")

    # verify_code / register / deregister / subscriptions
    client._client.request = AsyncMock(return_value={"success": True})
    await client.verify_code("999", "123456")
    assert "verify_code" in client._client.request.await_args.args[1]
    await client.register_number("999", pin="123456")
    assert "register" in client._client.request.await_args.args[1]
    await client.deregister_number("999")
    assert "deregister" in client._client.request.await_args.args[1]
    await client.subscribe_app_to_waba("111")
    assert "subscribed_apps" in client._client.request.await_args.args[1]

    client._client.request = AsyncMock(return_value={"data": [{"s": 1}]})
    out = await client.list_waba_subscriptions("111")
    assert out == [{"s": 1}]

    client._client.request = AsyncMock(return_value={"success": True})
    await client.unsubscribe_app_from_waba("111")
    assert "subscribed_apps" in client._client.request.await_args.args[1]


# ── parse_webhook_event ───────────────────────────────────────────────


def _hook(messages=None, statuses=None, contacts=None):
    value = {"metadata": {"phone_number_id": "p1"},
             "contacts": contacts or [],
             "messages": messages or [],
             "statuses": statuses or []}
    return {"object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": value}]}]}


def test_parse_webhook_event_gates():
    assert W.parse_webhook_event({"object": "instagram"}) == []
    assert W.parse_webhook_event(_hook()) == []


def test_parse_webhook_event_message_types():
    contacts = [{"wa_id": "3069", "profile": {"name": "Ada"}}]
    msgs = [
        {"type": "text", "from": "3069", "id": "m1",
         "timestamp": "1700000000", "text": {"body": "hi"}},
        {"type": "button", "from": "3069", "id": "m2",
         "timestamp": "bad", "button": {"text": "b", "payload": "p"}},
        {"type": "interactive", "from": "1", "id": "m3",
         "timestamp": "1",
         "interactive": {"type": "button_reply",
                         "button_reply": {"title": "T", "id": "r1"}}},
        {"type": "interactive", "from": "1", "id": "m4",
         "timestamp": "1",
         "interactive": {"type": "list_reply",
                         "list_reply": {"title": "L", "id": "r2"}}},
        {"type": "image", "from": "1", "id": "m5", "timestamp": "1",
         "image": {"id": "img", "mime_type": "image/png",
                   "caption": "cap"}},
        {"type": "audio", "from": "1", "id": "m6", "timestamp": "1",
         "audio": {"id": "aud", "mime_type": "audio/ogg"}},
        {"type": "location", "from": "1", "id": "m7", "timestamp": "1",
         "location": {"latitude": 1.0, "longitude": 2.0,
                      "name": "Pin"}},
        {"type": "contacts", "from": "1", "id": "m8", "timestamp": "1",
         "contacts": [{"name": "x"}]},
        {"type": "reaction", "from": "1", "id": "m9", "timestamp": "1",
         "reaction": {"emoji": "❤️", "message_id": "m1"}},
        {"type": "sticker", "from": "1", "id": "m10", "timestamp": "1",
         "sticker": {"id": "st", "animated": True}},
        {"type": "system", "from": "1", "id": "m11", "timestamp": "1",
         "system": {"body": "sys msg"}},
        {"type": "unknown", "from": "1", "id": "m12", "timestamp": "1",
         "errors": [{"message": "unsupported!"}]},
        {"type": "text", "from": "1", "id": "m13", "timestamp": "1",
         "text": {"body": "reply"},
         "context": {"id": "m1", "from": "3069"}},
    ]
    ev = W.parse_webhook_event(_hook(messages=msgs, contacts=contacts))
    assert len(ev) == 13
    e = ev[0]
    assert e["sender_name"] == "Ada" and e["message_text"] == "hi"
    assert e["timestamp"] == 1700000000
    assert ev[1]["timestamp"] == 0  # bad ts → 0
    assert ev[1]["button_payload"] == "p"
    assert ev[2]["button_payload"] == "r1" and ev[3]["button_payload"] == "r2"
    assert ev[4]["media_id"] == "img" and ev[4]["caption"] == "cap"
    assert "caption" not in ev[5]  # audio gets no caption
    assert ev[6]["latitude"] == 1.0 and ev[6]["address"] == "Pin"
    assert ev[7]["contacts"] == [{"name": "x"}]
    assert ev[8]["reaction_emoji"] == "❤️"
    assert ev[9]["animated"] is True
    assert ev[10]["message_text"] == "sys msg"
    assert ev[11]["message_text"] == "unsupported!"
    assert ev[12]["context_message_id"] == "m1" and ev[12]["context_from"] == "3069"


def test_parse_webhook_event_statuses():
    ev = W.parse_webhook_event(_hook(statuses=[
        {"recipient_id": "3069", "id": "s1", "status": "delivered",
         "timestamp": "1700000000"},
        {"recipient_id": "3069", "id": "s2", "status": "read",
         "timestamp": "junk"},
        {"recipient_id": "3069", "id": "s3", "status": "sent",
         "timestamp": ""}]))
    assert ev[0]["message_type"] == "status"
    assert ev[0]["status"] == "delivered" and ev[0]["timestamp"] == 1700000000
    assert ev[1]["timestamp"] == 0 and ev[2]["timestamp"] == 0


@pytest.mark.asyncio
async def test_template_components_and_validation(client):
    # send_template with components → forwarded in body (line 123)
    out = await client.send_template("+302101111111", "promo", "en",
                                     components=[{"type": "body"}])
    assert client._calls[-1]["json_body"]["template"]["components"] == \
        [{"type": "body"}]

    # verify_code non-digit → ValueError (line 447)
    with pytest.raises(ValueError, match="numeric"):
        await client.verify_code("999", "12-ab")

    # register_number pin not 6 digits → ValueError (line 468)
    with pytest.raises(ValueError, match="6 digits"):
        await client.register_number("999", pin="123")

    # status check fully fails (both fallback GETs raise) → outer except
    # path, request_code still attempted (lines 412-414)
    client._client.request = AsyncMock(side_effect=[
        Exception("a"), Exception("b"), {"success": True}])
    out = await client.request_verification_code("999")
    assert out == {"success": True}
