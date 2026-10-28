"""Unit tests for app/api/whatsapp_flows.py — Flows router + endpoint."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

import app.api.whatsapp_flows as W


@pytest.fixture(autouse=True)
def _account(monkeypatch):
    acct = SimpleNamespace(
        id=uuid.uuid4(), team_id=uuid.uuid4(), platform="whatsapp",
        meta_data={"access_token": "EAtok", "waba_id": "w1",
                   "phone_number_id": "p1"})
    monkeypatch.setattr(W, "_get_whatsapp_account", AsyncMock(
        return_value=acct))
    return acct


def _client(**kw) -> SimpleNamespace:
    c = SimpleNamespace()
    for name in ("create_flow", "list_flows", "get_flow",
                 "update_flow_metadata", "update_flow_json",
                 "get_flow_json", "validate_flow_json", "publish_flow",
                 "delete_flow", "send_flow"):
        setattr(c, name, kw.pop(name, AsyncMock(return_value={"ok": 1})))
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _wire(monkeypatch, client):
    monkeypatch.setattr(W, "_get_flows_client", lambda a: client)


# ── _get_flows_client ─────────────────────────────────────────────────


def test_get_flows_client(monkeypatch):
    # missing creds → 400
    with pytest.raises(HTTPException) as e:
        W._get_flows_client(SimpleNamespace(meta_data={}))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException):
        W._get_flows_client(SimpleNamespace(
            meta_data={"access_token": "EAt"}))  # no waba_id

    # EA token passthrough
    c = W._get_flows_client(SimpleNamespace(meta_data={
        "access_token": "EAtok", "waba_id": "111",
        "phone_number_id": "222"}))
    assert c.access_token == "EAtok" and c.waba_id == "111"

    # encrypted → decrypt; decrypt fail → plaintext
    import app.core.security as SEC
    monkeypatch.setattr(SEC, "decrypt_token", lambda t: "DEC")
    c = W._get_flows_client(SimpleNamespace(meta_data={
        "access_token": "enc", "waba_id": "111"}))
    assert c.access_token == "DEC"
    monkeypatch.setattr(SEC, "decrypt_token",
                        Mock(side_effect=Exception("x")))
    c = W._get_flows_client(SimpleNamespace(meta_data={
        "access_token": "enc", "waba_id": "111"}))
    assert c.access_token == "enc"


# ── thin CRUD endpoints — happy + 502 mapping + passthrough ───────────


@pytest.mark.asyncio
async def test_crud_endpoints(monkeypatch, _account):
    aid = _account.id
    uid = uuid.uuid4()
    user = SimpleNamespace(id=uid)
    db = object()

    cli = _client()
    _wire(monkeypatch, cli)

    out = await W.create_flow(aid, W.FlowCreateRequest(
        name="f", categories=["OTHER"]), db, user)
    assert out == {"ok": 1}
    assert cli.create_flow.await_args.kwargs["name"] == "f"

    assert await W.list_flows(aid, db, user) == {"ok": 1}
    assert await W.get_flow(aid, "fid", db, user) == {"ok": 1}

    out = await W.update_flow_metadata(
        aid, "fid", W.FlowUpdateRequest(name="n2"), db, user)
    assert cli.update_flow_metadata.await_args.kwargs["name"] == "n2"

    await W.update_flow_json(aid, "fid",
                             W.FlowJSONUpdateRequest(flow_json={"v": 1}),
                             db, user)
    assert cli.update_flow_json.await_args.args[1] == {"v": 1}

    await W.get_flow_json(aid, "fid", db, user)
    await W.validate_flow_json(aid, "fid",
                               W.FlowJSONUpdateRequest(flow_json={}),
                               db, user)
    await W.publish_flow(aid, "fid", db, user)
    await W.delete_flow(aid, "fid", db, user)

    # send_flow — all body fields forwarded
    out = await W.send_flow(aid, W.FlowSendRequest(
        to="+30", flow_id="f", flow_token="t",
        header_text="H", footer_text="F",
        flow_action="data_exchange", screen="S",
        flow_action_payload={"k": 1}, messaging_type="TEMPLATE"),
        db, user)
    kw = cli.send_flow.await_args.kwargs
    assert kw["flow_action"] == "data_exchange" and kw["screen"] == "S"

    # error → 502; HTTPException passthrough
    cli.get_flow = AsyncMock(side_effect=RuntimeError("api down"))
    with pytest.raises(HTTPException) as e:
        await W.get_flow(aid, "fid", db, user)
    assert e.value.status_code == 502

    cli.get_flow = AsyncMock(side_effect=HTTPException(status_code=418))
    with pytest.raises(HTTPException) as e:
        await W.get_flow(aid, "fid", db, user)
    assert e.value.status_code == 418  # passthrough, not wrapped


# ── templates ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_templates(monkeypatch, _account):
    aid = _account.id
    user = SimpleNamespace(id=uuid.uuid4())
    db = object()

    out = await W.list_flow_templates(aid, db, user)
    assert out and all("id" in t and "name" in t for t in out)

    # unknown → 404
    with pytest.raises(HTTPException) as e:
        await W.get_flow_template_json(aid, "nope", "B", db, user)
    assert e.value.status_code == 404

    # happy — generator invoked with business_name
    tid = next(iter(W.FLOW_TEMPLATES))
    out = await W.get_flow_template_json(aid, tid, "Cloudless", db, user)
    assert isinstance(out, dict)


@pytest.mark.asyncio
async def test_create_from_template(monkeypatch, _account):
    aid = _account.id
    user = SimpleNamespace(id=uuid.uuid4())
    db = object()

    cli = _client(create_flow=AsyncMock(return_value={"id": "fid1"}))
    _wire(monkeypatch, cli)

    # unknown template → 404 (after client build)
    with pytest.raises(HTTPException) as e:
        await W.create_flow_from_template(aid, W.FlowFromTemplateRequest(
            template_id="nope"), db, user)
    assert e.value.status_code == 404

    # happy — create + JSON update + DRAFT result
    tid = next(iter(W.FLOW_TEMPLATES))
    out = await W.create_flow_from_template(aid, W.FlowFromTemplateRequest(
        template_id=tid, name="MyFlow", business_name="B"), db, user)
    assert out["flow_id"] == "fid1" and out["status"] == "DRAFT"
    assert out["name"] == "MyFlow"
    assert out["json_updated"] is True
    cli.update_flow_json.assert_awaited_once()

    # JSON update failure tolerated — still returns
    cli.update_flow_json = AsyncMock(side_effect=RuntimeError("x"))
    out = await W.create_flow_from_template(aid, W.FlowFromTemplateRequest(
        template_id=tid), db, user)
    assert out["flow_id"] == "fid1"
    assert out["json_updated"] is False

    # create returns no id → 502
    cli.create_flow = AsyncMock(return_value={})
    with pytest.raises(HTTPException) as e:
        await W.create_flow_from_template(aid, W.FlowFromTemplateRequest(
            template_id=tid), db, user)
    assert e.value.status_code == 502

    # create raises → 502
    cli.create_flow = AsyncMock(side_effect=RuntimeError("x"))
    with pytest.raises(HTTPException) as e:
        await W.create_flow_from_template(aid, W.FlowFromTemplateRequest(
            template_id=tid), db, user)
    assert e.value.status_code == 502


# ── flow data endpoint + responses ────────────────────────────────────


@pytest.mark.asyncio
async def test_flow_data_endpoint(monkeypatch):
    # bad JSON → 400
    req = SimpleNamespace(json=AsyncMock(side_effect=ValueError("x")))
    out = await W.flow_data_endpoint(req)
    assert out.status_code == 400

    # ping → active
    monkeypatch.setattr(W, "parse_flow_endpoint_request",
                        lambda b: {"is_ping": True})
    req = SimpleNamespace(json=AsyncMock(return_value={}))
    out = await W.flow_data_endpoint(req)
    assert out == {"version": "3.0", "data": {"status": "active"}}

    # routing — FIRST_ENTRY_SCREEN → LEAD_FORM
    monkeypatch.setattr(W, "parse_flow_endpoint_request",
                        lambda b: {"screen": "FIRST_ENTRY_SCREEN",
                                   "data": {}, "flow_token": "t"})
    monkeypatch.setattr(W, "build_flow_endpoint_response",
                        lambda **kw: kw)
    out = await W.flow_data_endpoint(req)
    assert out["screen"] == "LEAD_FORM"

    monkeypatch.setattr(W, "parse_flow_endpoint_request",
                        lambda b: {"screen": "LEAD_FORM",
                                   "data": {"name": "Themis"},
                                   "flow_token": "t"})
    out = await W.flow_data_endpoint(req)
    assert out["screen"] == "SUCCESS_SCREEN"
    assert out["data"] == {"name": "Themis"}

    # unknown screen → echo
    monkeypatch.setattr(W, "parse_flow_endpoint_request",
                        lambda b: {"screen": "OTHER", "data": {"d": 1},
                                   "flow_token": "t"})
    out = await W.flow_data_endpoint(req)
    assert out["screen"] == "OTHER" and out["data"] == {"d": 1}


@pytest.mark.asyncio
async def test_process_flow_responses(monkeypatch):
    db = SimpleNamespace(execute=AsyncMock())

    # no events → []
    monkeypatch.setattr(W, "parse_flow_response", lambda b: [])
    assert await W.process_flow_responses({}, db) == []

    # missing name/email → skipped
    monkeypatch.setattr(W, "parse_flow_response", lambda b: [
        {"response_json": {"name": "n"}, "phone_number_id": "p"}])
    await W.process_flow_responses({}, db)
    db.execute.assert_not_called()

    # missing interest+company_size → skipped (false-positive guard)
    monkeypatch.setattr(W, "parse_flow_response", lambda b: [
        {"response_json": {"name": "n", "email": "e"},
         "phone_number_id": "p"}])
    await W.process_flow_responses({}, db)
    db.execute.assert_not_called()

    # no phone_number_id → skipped
    monkeypatch.setattr(W, "parse_flow_response", lambda b: [
        {"response_json": {"name": "n", "email": "e", "interest": "x"},
         "phone_number_id": ""}])
    await W.process_flow_responses({}, db)
    db.execute.assert_not_called()

    # account not found → skipped
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        scalars=lambda: SimpleNamespace(first=lambda: None))))
    monkeypatch.setattr(W, "parse_flow_response", lambda b: [
        {"response_json": {"name": "n", "email": "e", "interest": "x"},
         "phone_number_id": "p", "sender_phone": "30",
         "flow_id": "f", "flow_token": "ft", "message_id": "m",
         "timestamp": 1}])
    import app.services.leads as LEADS
    created = AsyncMock()
    monkeypatch.setattr(LEADS, "create_lead", created)
    monkeypatch.setattr(LEADS, "coerce_company_size", lambda v: v)
    monkeypatch.setattr(LEADS, "coerce_interest", lambda v: v)
    await W.process_flow_responses({}, db)
    created.assert_not_awaited()

    # happy → lead created with flow meta
    acct = SimpleNamespace(team_id=uuid.uuid4(), id=uuid.uuid4())
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        scalars=lambda: SimpleNamespace(first=lambda: acct))))
    await W.process_flow_responses({}, db)
    created.assert_awaited_once()
    kw = created.await_args.kwargs
    assert kw["name"] == "n" and kw["email"] == "e"
    assert kw["meta_data"]["whatsapp_flow"]["flow_id"] == "f"

    # lead persistence failure tolerated
    created.side_effect = RuntimeError("db down")
    out = await W.process_flow_responses({}, db)
    assert len(out) == 1


@pytest.mark.asyncio
async def test_all_endpoints_502_mapping(monkeypatch, _account):
    """Every endpoint's generic-exception path → HTTPException 502."""
    aid = _account.id
    user = SimpleNamespace(id=uuid.uuid4())
    db = object()
    fail = AsyncMock(side_effect=RuntimeError("api down"))
    cli = _client(
        create_flow=fail, list_flows=fail, get_flow=fail,
        update_flow_metadata=fail, update_flow_json=fail,
        get_flow_json=fail, validate_flow_json=fail,
        publish_flow=fail, delete_flow=fail, send_flow=fail)
    _wire(monkeypatch, cli)

    calls = [
        W.create_flow(aid, W.FlowCreateRequest(
            name="f", categories=["OTHER"]), db, user),
        W.list_flows(aid, db, user),
        W.get_flow(aid, "f", db, user),
        W.update_flow_metadata(aid, "f", W.FlowUpdateRequest(), db, user),
        W.update_flow_json(aid, "f", W.FlowJSONUpdateRequest(flow_json={}),
                           db, user),
        W.get_flow_json(aid, "f", db, user),
        W.validate_flow_json(aid, "f", W.FlowJSONUpdateRequest(flow_json={}),
                             db, user),
        W.publish_flow(aid, "f", db, user),
        W.delete_flow(aid, "f", db, user),
        W.send_flow(aid, W.FlowSendRequest(
            to="+30", flow_id="f", flow_token="t"), db, user),
    ]
    for coro in calls:
        with pytest.raises(HTTPException) as e:
            await coro
        assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_all_endpoints_passthrough(monkeypatch, _account):
    """HTTPException from the client is re-raised unwrapped."""
    aid = _account.id
    user = SimpleNamespace(id=uuid.uuid4())
    db = object()
    fail = AsyncMock(side_effect=HTTPException(status_code=418))
    cli = _client(
        create_flow=fail, list_flows=fail, get_flow=fail,
        update_flow_metadata=fail, update_flow_json=fail,
        get_flow_json=fail, validate_flow_json=fail,
        publish_flow=fail, delete_flow=fail, send_flow=fail)
    _wire(monkeypatch, cli)

    calls = [
        W.create_flow(aid, W.FlowCreateRequest(
            name="f", categories=["OTHER"]), db, user),
        W.list_flows(aid, db, user),
        W.get_flow(aid, "f", db, user),
        W.update_flow_metadata(aid, "f", W.FlowUpdateRequest(), db, user),
        W.update_flow_json(aid, "f", W.FlowJSONUpdateRequest(flow_json={}),
                           db, user),
        W.get_flow_json(aid, "f", db, user),
        W.validate_flow_json(aid, "f", W.FlowJSONUpdateRequest(flow_json={}),
                             db, user),
        W.publish_flow(aid, "f", db, user),
        W.delete_flow(aid, "f", db, user),
        W.send_flow(aid, W.FlowSendRequest(
            to="+30", flow_id="f", flow_token="t"), db, user),
    ]
    for coro in calls:
        with pytest.raises(HTTPException) as e:
            await coro
        assert e.value.status_code == 418


@pytest.mark.asyncio
async def test_process_flow_responses_skip_guards(monkeypatch):
    db = SimpleNamespace(execute=AsyncMock())

    # response_json not a dict → skipped (line 569)
    monkeypatch.setattr(W, "parse_flow_response", lambda b: [
        {"response_json": "raw-string", "phone_number_id": "p"}])
    await W.process_flow_responses({}, db)
    db.execute.assert_not_called()

    # name without email → skipped (line 574)
    monkeypatch.setattr(W, "parse_flow_response", lambda b: [
        {"response_json": {"name": "n"}, "phone_number_id": "p"}])
    await W.process_flow_responses({}, db)
    db.execute.assert_not_called()
