"""Unit tests for app/services/whatsapp_flows.py — Flows client + helpers."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

import app.services.whatsapp_flows as S


@pytest.fixture
def client():
    return S.WhatsAppFlowsClient(
        access_token="EAtok", waba_id="111", phone_number_id="222")


def _http_fake(status: int = 200, body=None):
    resp = AsyncMock()
    resp.status_code = status
    body = body or {"ok": 1}
    resp.json = lambda: body
    resp.text = json.dumps(body)
    resp.headers = {}
    calls: list[dict] = []

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def _rec(self, method, url, **kw):
            calls.append({"method": method, "url": url, **kw})
            return resp

        async def get(self, url, **kw):
            return await self._rec("get", url, **kw)

        async def post(self, url, **kw):
            return await self._rec("post", url, **kw)

        async def delete(self, url, **kw):
            return await self._rec("delete", url, **kw)

    return _C(), calls, resp


def _patch(monkeypatch, http):
    import app.services.whatsapp_flows as S
    monkeypatch.setattr(S.httpx, "AsyncClient", lambda **kw: http)


# ── init + internals ──────────────────────────────────────────────────


def test_init_validation():
    with pytest.raises(ValueError, match="Access token"):
        S.WhatsAppFlowsClient(access_token="", waba_id="111")
    with pytest.raises(ValueError):
        S.WhatsAppFlowsClient(access_token="t", waba_id="bad id!")
    c = S.WhatsAppFlowsClient("t", "111", api_version="v21.0")
    assert c._base_url.endswith("/v21.0")
    assert c._url("/abc") == c._base_url + "/abc"
    assert c._params({"x": 1})["x"] == 1
    assert c._params()["access_token"] == "t"


@pytest.mark.asyncio
async def test_raise_for_status(client, monkeypatch):
    from app.services.facebook_api import FacebookAPIError

    http, calls, _ = _http_fake(status=400, body={"error": "bad"})
    _patch(monkeypatch, http)
    with pytest.raises(FacebookAPIError):
        await client.get_flow("123")

    # 5xx → gateway remap (503 → 503, 500 → 502)
    for upstream, expect in ((503, 503), (500, 502)):
        http, _, _ = _http_fake(status=upstream)
        _patch(monkeypatch, http)
        with pytest.raises(FacebookAPIError) as e:
            await client.get_flow("123")
        assert e.value.status_code == expect


# ── CRUD methods ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_flow(client, monkeypatch):
    # validation
    with pytest.raises(ValueError, match="name is required"):
        await client.create_flow("", ["OTHER"])
    with pytest.raises(ValueError, match="category is required"):
        await client.create_flow("f", [])
    with pytest.raises(ValueError, match="Invalid category"):
        await client.create_flow("f", ["BOGUS"])

    http, calls, _ = _http_fake(body={"id": "f1"})
    _patch(monkeypatch, http)
    out = await client.create_flow(
        "f", ["SIGN_UP"], endpoint_uri="https://e", clone_flow_id="99")
    assert out == {"id": "f1"}
    j = calls[0]["json"]
    assert j["categories"] == ["SIGN_UP"]
    assert j["endpoint_uri"] == "https://e" and j["clone_flow_id"] == "99"
    assert f"{client.waba_id}/flows" in calls[0]["url"]


@pytest.mark.asyncio
async def test_list_and_get_flow(client, monkeypatch):
    http, calls, _ = _http_fake(body={"data": [{"id": "1"}]})
    _patch(monkeypatch, http)
    assert await client.list_flows() == [{"id": "1"}]
    assert "id,name,status" in calls[0]["params"]["fields"]

    await client.list_flows(fields=["id"])
    assert calls[1]["params"]["fields"] == "id"

    http, calls, _ = _http_fake(body={"id": "123", "name": "f"})
    _patch(monkeypatch, http)
    out = await client.get_flow("123")
    assert out["id"] == "123"
    assert calls[0]["url"].endswith("/123")
    assert "endpoint_uri" in calls[0]["params"]["fields"]


@pytest.mark.asyncio
async def test_update_flow_metadata(client, monkeypatch):
    with pytest.raises(ValueError, match="field to update"):
        await client.update_flow_metadata("123")
    with pytest.raises(ValueError, match="Invalid category"):
        await client.update_flow_metadata("123", categories=["BOGUS"])

    http, calls, _ = _http_fake(body={"success": True})
    _patch(monkeypatch, http)
    out = await client.update_flow_metadata(
        "123", name="n", categories=["SURVEY"], endpoint_uri="u")
    assert out == {"success": True}
    j = calls[0]["json"]
    assert j["name"] == "n" and j["endpoint_uri"] == "u"

    # endpoint_uri=None counts as no fields → ValueError
    with pytest.raises(ValueError):
        await client.update_flow_metadata("123", endpoint_uri=None)


@pytest.mark.asyncio
async def test_json_and_lifecycle(client, monkeypatch):
    http, calls, _ = _http_fake(body={"success": True})
    _patch(monkeypatch, http)

    # dict → json.dumps; str passthrough
    await client.update_flow_json("123", {"screens": []})
    j = calls[0]["json"]["flow_json"]
    assert json.loads(j) == {"screens": []}
    await client.update_flow_json("123", '{"raw": 1}')
    assert calls[1]["json"]["flow_json"] == '{"raw": 1}'
    assert calls[0]["url"].endswith("/123/flow_json")

    await client.validate_flow_json("123", {"v": 3})
    assert calls[2]["url"].endswith("/123/validate")

    await client.publish_flow("123")
    assert calls[3]["url"].endswith("/123/publish")

    await client.delete_flow("123")
    assert calls[4]["method"] == "delete"

    http2, calls2, _ = _http_fake(body={"screens": [{"id": "S"}]})
    _patch(monkeypatch, http2)
    out = await client.get_flow_json("123")
    assert out["screens"][0]["id"] == "S"

    # invalid flow_id → _validate_id rejects before HTTP
    with pytest.raises(ValueError):
        await client.get_flow("not valid!")


@pytest.mark.asyncio
async def test_send_flow(client, monkeypatch):
    # no phone_number_id
    bare = S.WhatsAppFlowsClient("t", "111")
    with pytest.raises(ValueError, match="phone_number_id"):
        await bare.send_flow("+30", "123", "tok")

    with pytest.raises(ValueError, match="flow_action"):
        await client.send_flow("+30", "123", "tok",
                               flow_action="bogus")

    http, calls, _ = _http_fake(body={"messages": [{"id": "m1"}]})
    _patch(monkeypatch, http)

    # navigate — screen in flow_action_payload
    out = await client.send_flow(
        "+302101234567", "123", "tok",
        body_text="body", header_text="H", footer_text="F",
        flow_action="navigate", screen="S1",
        flow_action_payload={"k": 1}, messaging_type="TEMPLATE")
    assert out["messages"][0]["id"] == "m1"
    inter = calls[0]["json"]["interactive"]
    params = inter["action"]["parameters"]
    assert params["flow_action"] == "navigate"
    assert params["flow_action_payload"]["screen"] == "S1"
    assert params["flow_action_payload"]["k"] == 1
    assert inter["header"]["text"] == "H" and inter["footer"]["text"] == "F"
    assert calls[0]["json"]["messaging_type"] == "TEMPLATE"
    assert "222/messages" in calls[0]["url"]

    # data_exchange — payload passed directly, no screen key
    await client.send_flow("+302101234567", "123", "tok",
                           flow_action="data_exchange",
                           flow_action_payload={"a": 2})
    params = calls[1]["json"]["interactive"]["action"]["parameters"]
    assert params["flow_action"] == "data_exchange"
    assert params["flow_action_payload"] == {"a": 2}
    assert "screen" not in params["flow_action_payload"]

    # navigate without screen → empty payload
    await client.send_flow("+302101234567", "123", "tok",
                           flow_action="navigate", screen="")
    params = calls[2]["json"]["interactive"]["action"]["parameters"]
    assert params["flow_action_payload"] == {}


# ── Flow JSON templates ───────────────────────────────────────────────


def test_flow_templates():
    for tid, t in S.FLOW_TEMPLATES.items():
        assert t["name"] and t["category"] and t["description"]
        out = t["generator"](business_name="TestCo")
        assert out["version"] in ("3.0", "5.0")
        assert out["screens"], tid
        # business_name should appear somewhere in the JSON
        assert "TestCo" in json.dumps(out), tid

    # every template category is a valid Flow category
    for t in S.FLOW_TEMPLATES.values():
        assert t["category"] in S.VALID_FLOW_CATEGORIES


def test_template_generators_direct():
    for gen in (S.lead_generation_flow_json,
                S.customer_support_flow_json,
                S.appointment_booking_flow_json,
                S.feedback_survey_flow_json):
        out = gen("MyBiz")
        assert isinstance(out, dict) and out["screens"]


# ── webhook parsing ───────────────────────────────────────────────────


def _webhook(response_raw='{"a": 1}', itype="nfm_context",
             ts="1700000000", obj="whatsapp_business_account"):
    return {
        "object": obj,
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "222"},
            "messages": [{
                "type": "interactive",
                "interactive": {
                    "type": itype,
                    "nfm_context": {
                        "flow_token": "ft",
                        "flow_id": "99",
                        "response_json": response_raw}},
                "from": "3069", "id": "wamid.1", "timestamp": ts}],
        }}]}]}


def test_parse_flow_response():
    # wrong object → []
    assert S.parse_flow_response({"object": "instagram"}) == []

    # non-interactive + non-nfm messages skipped
    assert S.parse_flow_response(_webhook(itype="button")) == []

    # happy
    ev = S.parse_flow_response(_webhook())
    assert len(ev) == 1
    e = ev[0]
    assert e["phone_number_id"] == "222" and e["sender_phone"] == "3069"
    assert e["flow_token"] == "ft" and e["flow_id"] == "99"
    assert e["response_json"] == {"a": 1}
    assert e["message_id"] == "wamid.1" and e["timestamp"] == 1700000000

    # bad response_json → {"raw": ...}; non-str passthrough; bad ts → 0
    ev = S.parse_flow_response(_webhook(response_raw="{bad"))
    assert ev[0]["response_json"] == {"raw": "{bad"}
    ev = S.parse_flow_response(_webhook(response_raw={"already": "dict"}))
    assert ev[0]["response_json"] == {"already": "dict"}
    ev = S.parse_flow_response(_webhook(ts="notats"))
    assert ev[0]["timestamp"] == 0


def test_endpoint_request_response():
    # ping
    out = S.parse_flow_endpoint_request({"action": "ping"})
    assert out == {"action": "ping", "is_ping": True}

    # navigate/data_exchange normalized
    out = S.parse_flow_endpoint_request({
        "action": "data_exchange", "screen": "S1", "data": {"d": 1},
        "flow_token": "ft", "flow_id": "9",
        "flow_token_encrypted": "enc", "encrypted_flow_data": "ed"})
    assert out["is_ping"] is False and out["screen"] == "S1"
    assert out["data"] == {"d": 1} and out["flow_token_encrypted"] == "enc"
    assert out["encrypted_flow_data"] == "ed"

    # build response — bare, data, error
    out = S.build_flow_endpoint_response("NEXT")
    assert out == {"version": "3.0", "screen": "NEXT"}
    out = S.build_flow_endpoint_response("NEXT", data={"x": 1})
    assert out["data"] == {"x": 1}
    out = S.build_flow_endpoint_response("CUR", error_message="bad")
    assert out["error"] == {"message": "bad"}
