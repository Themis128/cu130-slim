"""Unit tests for the WhatsApp Cloud API router (app/api/whatsapp.py).

Covers account/client resolution, the setup checklist, profile endpoints,
the phone-registration and WABA-subscription flows, send paths, auto-reply
config, webhook verification/dispatch, the bot builder, and per-thread
control — previously near-0% covered.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import uuid
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api import whatsapp
from app.api.whatsapp import (
    AutoReplyConfig,
    BotConfig,
    BotCreateRequest,
    CreatePhoneNumberRequest,
    DeregisterNumberRequest,
    RegisterNumberRequest,
    RequestCodeRequest,
    SendMessageRequest,
    SendTemplateRequest,
    ThreadConfigRequest,
    ThreadPauseRequest,
    VerifyCodeRequest,
    WabaSubscriptionRequest,
    WhatsAppCredentialsUpdate,
    WhatsAppProfileUpdate,
    WhatsAppSetupRequest,
    _get_verify_token,
    _get_whatsapp_account,
    _get_whatsapp_client,
    _next_setup_step,
    _process_inline,
    _process_waba_level_events,
    _record_message,
    _verify_webhook_signature,
    activate_bot,
    create_bot,
    create_phone_number,
    deactivate_bot,
    deregister_phone_number,
    get_auto_reply_config,
    get_bot,
    get_bot_personalities,
    get_phone_status,
    get_service_window,
    get_setup_status,
    get_thread_config,
    get_thread_memory,
    get_whatsapp_profile,
    index_brand,
    list_waba_subscriptions,
    pause_thread,
    receive_webhook,
    register_account_phone,
    register_phone_number,
    request_phone_code,
    request_verification_code,
    resume_thread,
    send_message,
    send_template,
    set_thread_config,
    setup_whatsapp,
    subscribe_app_to_waba,
    unsubscribe_app_from_waba,
    update_auto_reply_config,
    update_bot,
    update_whatsapp_credentials,
    update_whatsapp_profile,
    verify_account_phone_code,
    verify_phone_code,
    verify_webhook,
)
from app.core.config import settings

# ── Fakes ─────────────────────────────────────────────────────────────


class _Res:
    def __init__(self, v):
        self._v = v

    def scalar_one_or_none(self):
        return self._v

    def scalars(self):
        return self

    def first(self):
        return self._v

    def all(self):
        return self._v if isinstance(self._v, list) else ([] if self._v is None else [self._v])


class _DB:
    def __init__(self, results=()):
        self._q = list(results)
        self.executed = []
        self.committed = 0
        self.rolled_back = 0
        self.fail_commit = False

    async def execute(self, stmt):
        self.executed.append(stmt)
        return _Res(self._q.pop(0) if self._q else None)

    async def commit(self):
        if self.fail_commit:
            raise RuntimeError("db down")
        self.committed += 1

    async def rollback(self):
        self.rolled_back += 1

    def add(self, obj):
        pass


def _wa_account(**kw):
    meta = kw.pop(
        "meta_data",
        {
            "access_token": "EAAtoken123",
            "phone_number_id": "1334613883061552",
            "waba_id": "1073707258453499",
            "display_phone_number": "+301234567890",
        },
    )
    return SimpleNamespace(
        id=kw.pop("id", uuid.uuid4()),
        team_id=kw.pop("team_id", uuid.uuid4()),
        platform="whatsapp",
        account_type="business",
        display_name=kw.pop("display_name", "Cloudless WA"),
        meta_data=meta,
        access_token_enc=b"enc",
        **kw,
    )


def _user(email=None):
    return SimpleNamespace(id=uuid.uuid4(), email=email or settings.SOCIAL_ADMIN_EMAIL)


class _FakeClient:
    def __init__(self, **overrides):
        self.phone_number_id = overrides.pop("phone_number_id", "1334613883061552")
        for name, ret in (
            ("get_phone_number_info", {
                "display_phone_number": "+301234567890", "verified_name": "Cloudless",
                "code_verification_status": "VERIFIED", "quality_rating": "GREEN",
            }),
            ("get_business_profile", {"about": "hi", "description": "desc", "websites": ["https://cloudless.gr"]}),
            ("update_business_profile", {"result": "success"}),
            ("create_phone_number", {"id": "new-pnid"}),
            ("request_verification_code", {"success": True}),
            ("verify_code", {"success": True}),
            ("register_number", {"success": True}),
            ("deregister_number", {"success": True}),
            ("subscribe_app_to_waba", {"success": True}),
            ("list_waba_subscriptions", [{"id": "app1"}]),
            ("unsubscribe_app_from_waba", {"success": True}),
            ("send_text", {"messages": [{"id": "wamid.1"}]}),
            ("send_image", {"messages": [{"id": "wamid.2"}]}),
            ("send_document", {"messages": [{"id": "wamid.3"}]}),
            ("send_template", {"messages": [{"id": "wamid.4"}]}),
            ("mark_message_read", {"success": True}),
        ):
            v = overrides.pop(name, ret)
            setattr(self, name, AsyncMock(side_effect=v) if isinstance(v, Exception) else AsyncMock(return_value=v))
        for k, v in overrides.items():
            setattr(self, k, v)


def _patch_client(monkeypatch, client=None):
    client = client or _FakeClient()
    monkeypatch.setattr(whatsapp, "_get_whatsapp_client", lambda account: client)
    return client


@pytest.fixture(autouse=True)
def _no_orm_flag_modified(monkeypatch):
    """flag_modified needs a real mapped instance — fakes use plain dicts."""
    monkeypatch.setattr(whatsapp, "flag_modified", lambda *a, **k: None)


# ── _get_whatsapp_account ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_account_404_missing():
    with pytest.raises(HTTPException) as e:
        await _get_whatsapp_account(_DB([None]), uuid.uuid4(), _user())
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_account_400_wrong_platform():
    acc = _wa_account()
    acc.platform = "instagram"
    with pytest.raises(HTTPException) as e:
        await _get_whatsapp_account(_DB([acc]), acc.id, _user())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_account_admin_bypasses_membership():
    acc = _wa_account()
    out = await _get_whatsapp_account(_DB([acc]), acc.id, _user())
    assert out is acc


@pytest.mark.asyncio
async def test_account_non_admin_requires_membership():
    acc = _wa_account()
    with pytest.raises(HTTPException) as e:
        await _get_whatsapp_account(_DB([acc, None]), acc.id, _user("other@x.com"))
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_account_non_admin_member_ok():
    acc = _wa_account()
    out = await _get_whatsapp_account(_DB([acc, SimpleNamespace(id=uuid.uuid4())]), acc.id, _user("m@x.com"))
    assert out is acc


# ── _get_whatsapp_client ──────────────────────────────────────────────


def test_client_400_no_credentials():
    acc = _wa_account(meta_data={})
    with pytest.raises(HTTPException) as e:
        _get_whatsapp_client(acc)
    assert e.value.status_code == 400


def test_client_plaintext_token_no_decrypt(monkeypatch):
    called = []
    monkeypatch.setattr(whatsapp, "decrypt_token", lambda t: called.append(t) or "dec")
    acc = _wa_account()
    client = _get_whatsapp_client(acc)
    assert client.phone_number_id == "1334613883061552"
    assert not called  # "EA..." tokens skip decryption


def test_client_encrypted_token_decrypted(monkeypatch):
    monkeypatch.setattr(whatsapp, "decrypt_token", lambda t: "EAdecrypted")
    acc = _wa_account(meta_data={"access_token": "gAAAAenc", "phone_number_id": "12345"})
    client = _get_whatsapp_client(acc)
    assert client.phone_number_id == "12345"


def test_client_decrypt_failure_falls_back(monkeypatch):
    monkeypatch.setattr(whatsapp, "decrypt_token", lambda t: (_ for _ in ()).throw(ValueError("bad")))
    acc = _wa_account(meta_data={"access_token": "weirdtoken", "phone_number_id": "12345"})
    client = _get_whatsapp_client(acc)  # plaintext fallback, no raise
    assert client.phone_number_id == "12345"


# ── _next_setup_step ──────────────────────────────────────────────────


def test_next_step_no_creds():
    assert "permanent access token" in _next_setup_step(False, False, False, False, False)


def test_next_step_no_waba():
    assert "waba_id" in _next_setup_step(True, False, False, False, False)


def test_next_step_phone_unregistered():
    assert "4-step" in _next_setup_step(True, True, False, False, False)


def test_next_step_webhook():
    assert "webhooks" in _next_setup_step(True, True, False, True, False)


def test_next_step_profile():
    assert "business profile" in _next_setup_step(True, True, True, True, False)


def test_next_step_complete():
    assert _next_setup_step(True, True, True, True, True) == "setup_complete"


# ── setup_whatsapp ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_setup_happy_path(monkeypatch):
    acc = _wa_account()
    db = _DB([acc])
    client = _patch_client(monkeypatch)
    out = await setup_whatsapp(acc.id, WhatsAppSetupRequest(greeting_text="hello"), db, _user())
    assert out["status"] == "ok"
    assert out["verified_name"] == "Cloudless"
    client.update_business_profile.assert_awaited_once()
    assert db.committed == 1
    assert acc.meta_data["whatsapp_setup"]["setup_complete"] is True


@pytest.mark.asyncio
async def test_setup_phone_info_failure_tolerated(monkeypatch):
    acc = _wa_account()
    db = _DB([acc])
    _patch_client(monkeypatch, _FakeClient(get_phone_number_info=RuntimeError("api down")))
    out = await setup_whatsapp(acc.id, None, db, _user())
    assert out["status"] == "ok"
    assert out["phone_number_info"] == {}
    assert out["verified_name"] == "Cloudless WA"  # falls back to display_name


@pytest.mark.asyncio
async def test_setup_profile_update_failure_tolerated(monkeypatch):
    acc = _wa_account()
    db = _DB([acc])
    _patch_client(monkeypatch, _FakeClient(update_business_profile=RuntimeError("nope")))
    out = await setup_whatsapp(acc.id, WhatsAppSetupRequest(greeting_text="hi"), db, _user())
    assert out["status"] == "ok"
    assert out["profile"]["result"] == "error"


# ── update_whatsapp_credentials ───────────────────────────────────────


@pytest.mark.asyncio
async def test_credentials_stores_encrypted(monkeypatch):
    acc = _wa_account(meta_data={})
    db = _DB([acc])
    monkeypatch.setattr(whatsapp, "encrypt_token", lambda t: b"enc-token")
    _patch_client(monkeypatch)
    body = WhatsAppCredentialsUpdate(
        access_token="EAAnew", phone_number_id="pn1", waba_id="w1", subscribe_webhooks=False
    )
    out = await update_whatsapp_credentials(acc.id, body, db, _user())
    assert out["credentials_stored"] is True
    assert acc.meta_data["access_token"] == "enc-token"
    assert acc.meta_data["credentials_configured"] is True
    assert out["webhook_subscription"] is None


@pytest.mark.asyncio
async def test_credentials_phone_change_wipes_code_state(monkeypatch):
    acc = _wa_account(meta_data={
        "access_token": "EAa", "phone_number_id": "old-pn",
        "whatsapp_code_status": "sent", "whatsapp_rate_limited_at": "t",
        "whatsapp_code_request_log": {"old-pn": 3},
    })
    db = _DB([acc])
    monkeypatch.setattr(whatsapp, "encrypt_token", lambda t: b"enc")
    _patch_client(monkeypatch)
    body = WhatsAppCredentialsUpdate(
        access_token="EAAnew", phone_number_id="new-pn", subscribe_webhooks=False
    )
    await update_whatsapp_credentials(acc.id, body, db, _user())
    assert "whatsapp_code_status" not in acc.meta_data
    assert "whatsapp_rate_limited_at" not in acc.meta_data
    assert acc.meta_data["whatsapp_code_request_log"] == {}


@pytest.mark.asyncio
async def test_credentials_webhook_subscribe_failure_tolerated(monkeypatch):
    acc = _wa_account(meta_data={})
    db = _DB([acc])
    monkeypatch.setattr(whatsapp, "encrypt_token", lambda t: b"enc")
    _patch_client(monkeypatch, _FakeClient(subscribe_app_to_waba=RuntimeError("boom")))
    body = WhatsAppCredentialsUpdate(
        access_token="EAAnew", phone_number_id="pn1", waba_id="w1", subscribe_webhooks=True
    )
    out = await update_whatsapp_credentials(acc.id, body, db, _user())
    assert out["webhook_subscription"]["success"] is False


# ── get_setup_status ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_setup_status_no_credentials():
    acc = _wa_account(meta_data={})
    out = await get_setup_status(acc.id, _DB([acc]), _user())
    assert out["credentials_configured"] is False
    assert out["can_send_messages"] is False
    assert "permanent access token" in out["next_step"]


@pytest.mark.asyncio
async def test_setup_status_full(monkeypatch):
    acc = _wa_account(meta_data={
        "access_token": "EAa", "phone_number_id": "pn", "waba_id": "w1",
        "whatsapp_setup": {"setup_complete": True},
    })
    _patch_client(monkeypatch)
    out = await get_setup_status(acc.id, _DB([acc]), _user())
    assert out["phone_number_registered"] is True
    assert out["webhook_subscribed"] is True
    assert out["can_send_messages"] is True
    assert out["next_step"] == "setup_complete"


@pytest.mark.asyncio
async def test_setup_status_api_failures_tolerated(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch, _FakeClient(
        get_phone_number_info=RuntimeError("x"),
        list_waba_subscriptions=RuntimeError("y"),
    ))
    out = await get_setup_status(acc.id, _DB([acc]), _user())
    assert out["phone_number_registered"] is False
    assert out["webhook_subscribed"] is False


# ── profile ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_profile_success(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch)
    out = await get_whatsapp_profile(acc.id, _DB([acc]), _user())
    assert out.about == "hi"
    assert out.verified_name == "Cloudless"
    assert out.quality_rating == "GREEN"


@pytest.mark.asyncio
async def test_get_profile_502(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch, _FakeClient(get_business_profile=RuntimeError("meta down")))
    with pytest.raises(HTTPException) as e:
        await get_whatsapp_profile(acc.id, _DB([acc]), _user())
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_update_profile_400_empty():
    acc = _wa_account()
    with pytest.raises(HTTPException) as e:
        await update_whatsapp_profile(acc.id, WhatsAppProfileUpdate(), _DB([acc]), _user())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_update_profile_success(monkeypatch):
    acc = _wa_account()
    client = _patch_client(monkeypatch)
    out = await update_whatsapp_profile(acc.id, WhatsAppProfileUpdate(about="new"), _DB([acc]), _user())
    assert out["result"] == "success"
    client.update_business_profile.assert_awaited_once_with({"about": "new"})


@pytest.mark.asyncio
async def test_update_profile_502(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch, _FakeClient(update_business_profile=RuntimeError("x")))
    with pytest.raises(HTTPException) as e:
        await update_whatsapp_profile(acc.id, WhatsAppProfileUpdate(about="x"), _DB([acc]), _user())
    assert e.value.status_code == 502


# ── 4-step registration + WABA subscriptions ─────────────────────────


async def _reg_endpoint(fn, body):
    return await fn(body, _DB(), _user())


@pytest.mark.asyncio
async def test_register_flow_400_no_account():
    with pytest.raises(HTTPException) as e:
        await create_phone_number(
            CreatePhoneNumberRequest(waba_id="w", cc="30", phone_number="123", verified_name="C"),
            _DB([None]), _user())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_create_number_success(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch)
    out = await create_phone_number(
        CreatePhoneNumberRequest(waba_id="w", cc="30", phone_number="123", verified_name="C"),
        _DB([acc]), _user())
    assert out["id"] == "new-pnid"


@pytest.mark.asyncio
async def test_create_number_502(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch, _FakeClient(create_phone_number=RuntimeError("x")))
    with pytest.raises(HTTPException) as e:
        await create_phone_number(
            CreatePhoneNumberRequest(waba_id="w", cc="30", phone_number="1", verified_name="C"),
            _DB([acc]), _user())
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_request_code_success(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch)
    out = await request_verification_code(RequestCodeRequest(phone_number_id="pn"), _DB([acc]), _user())
    assert out["success"] is True


@pytest.mark.asyncio
async def test_request_code_502(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch, _FakeClient(request_verification_code=RuntimeError("x")))
    with pytest.raises(HTTPException) as e:
        await request_verification_code(RequestCodeRequest(phone_number_id="pn"), _DB([acc]), _user())
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_verify_code_success(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch)
    out = await verify_phone_code(VerifyCodeRequest(phone_number_id="pn", code="123830"), _DB([acc]), _user())
    assert out["success"] is True


@pytest.mark.asyncio
async def test_verify_code_502(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch, _FakeClient(verify_code=RuntimeError("x")))
    with pytest.raises(HTTPException) as e:
        await verify_phone_code(VerifyCodeRequest(phone_number_id="pn", code="1"), _DB([acc]), _user())
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_register_number_success(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch)
    out = await register_phone_number(RegisterNumberRequest(phone_number_id="pn", pin="123456"), _DB([acc]), _user())
    assert out["success"] is True


@pytest.mark.asyncio
async def test_register_number_502(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch, _FakeClient(register_number=RuntimeError("x")))
    with pytest.raises(HTTPException) as e:
        await register_phone_number(RegisterNumberRequest(phone_number_id="pn", pin="1"), _DB([acc]), _user())
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_deregister_success(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch)
    out = await deregister_phone_number(DeregisterNumberRequest(phone_number_id="pn"), _DB([acc]), _user())
    assert out["success"] is True


@pytest.mark.asyncio
async def test_waba_subscribe_success(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch)
    out = await subscribe_app_to_waba(WabaSubscriptionRequest(waba_id="w1"), _DB([acc]), _user())
    assert out["success"] is True


@pytest.mark.asyncio
async def test_waba_subscribe_502(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch, _FakeClient(subscribe_app_to_waba=RuntimeError("x")))
    with pytest.raises(HTTPException) as e:
        await subscribe_app_to_waba(WabaSubscriptionRequest(waba_id="w"), _DB([acc]), _user())
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_waba_list_success(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch)
    out = await list_waba_subscriptions("w1", _DB([acc]), _user())
    assert out == [{"id": "app1"}]


@pytest.mark.asyncio
async def test_waba_unsubscribe_success(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch)
    out = await unsubscribe_app_from_waba(WabaSubscriptionRequest(waba_id="w"), _DB([acc]), _user())
    assert out["success"] is True


# ── send paths ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_400_no_content():
    acc = _wa_account()
    with pytest.raises(HTTPException) as e:
        await send_message(acc.id, SendMessageRequest(to="+30"), _DB([acc]), _user())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_send_text_records_message(monkeypatch):
    acc = _wa_account()
    db = _DB([acc])
    client = _patch_client(monkeypatch)
    out = await send_message(acc.id, SendMessageRequest(to="+30", text="hi"), db, _user())
    assert out["messages"][0]["id"] == "wamid.1"
    client.send_text.assert_awaited_once()
    assert db.committed == 1  # _record_message commits


@pytest.mark.asyncio
async def test_send_image_dispatch(monkeypatch):
    acc = _wa_account()
    client = _patch_client(monkeypatch)
    out = await send_message(
        acc.id, SendMessageRequest(to="+30", image_url="https://x/img.jpg", caption="cap"),
        _DB([acc]), _user())
    client.send_image.assert_awaited_once()
    assert out["messages"][0]["id"] == "wamid.2"


@pytest.mark.asyncio
async def test_send_document_dispatch(monkeypatch):
    acc = _wa_account()
    client = _patch_client(monkeypatch)
    await send_message(
        acc.id, SendMessageRequest(to="+30", document_url="https://x/d.pdf", filename="d.pdf"),
        _DB([acc]), _user())
    client.send_document.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_502(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch, _FakeClient(send_text=RuntimeError("meta down")))
    with pytest.raises(HTTPException) as e:
        await send_message(acc.id, SendMessageRequest(to="+30", text="x"), _DB([acc]), _user())
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_send_template_success(monkeypatch):
    acc = _wa_account()
    db = _DB([acc])
    client = _patch_client(monkeypatch)
    out = await send_template(
        acc.id, SendTemplateRequest(to="+30", template_name="welcome"), db, _user())
    client.send_template.assert_awaited_once()
    assert out["messages"][0]["id"] == "wamid.4"


@pytest.mark.asyncio
async def test_send_template_502(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch, _FakeClient(send_template=RuntimeError("x")))
    with pytest.raises(HTTPException) as e:
        await send_template(acc.id, SendTemplateRequest(to="+30", template_name="t"), _DB([acc]), _user())
    assert e.value.status_code == 502


# ── auto-reply config ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_auto_reply_defaults():
    acc = _wa_account(meta_data={})
    out = await get_auto_reply_config(acc.id, _DB([acc]), _user())
    assert out.enabled is False


@pytest.mark.asyncio
async def test_update_auto_reply_disabled_skips_plan_check():
    acc = _wa_account()
    db = _DB([acc])
    await update_auto_reply_config(acc.id, AutoReplyConfig(enabled=False), db, _user())
    assert db.committed == 1
    assert acc.meta_data["whatsapp_auto_reply"]["enabled"] is False


@pytest.mark.asyncio
async def test_update_auto_reply_enabled_checks_plan(monkeypatch):
    acc = _wa_account()
    checked = []
    monkeypatch.setattr("app.api.deps.check_plan_feature", AsyncMock(side_effect=lambda *a: checked.append(a)))
    db = _DB([acc])
    await update_auto_reply_config(acc.id, AutoReplyConfig(enabled=True), db, _user())
    assert len(checked) == 1
    assert checked[0][0] == "dm_auto_reply"


# ── webhook verify + signature ────────────────────────────────────────


def test_verify_token_resolution():
    expected = getattr(settings, "WHATSAPP_VERIFY_TOKEN", "") or "cloudless_whatsapp_verify"
    assert _get_verify_token() == expected


def test_signature_valid():
    body = b'{"x":1}'
    sig = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert _verify_webhook_signature(body, f"sha256={sig}", "secret") is True


def test_signature_invalid():
    assert _verify_webhook_signature(b"{}", "sha256=deadbeef", "secret") is False


def test_signature_missing_or_malformed():
    assert _verify_webhook_signature(b"{}", "", "secret") is False
    assert _verify_webhook_signature(b"{}", "md5=abc", "secret") is False


@pytest.mark.asyncio
async def test_verify_webhook_echoes_challenge():
    out = await verify_webhook("subscribe", _get_verify_token(), "ch4ll3nge")
    assert out.body == b"ch4ll3nge"


@pytest.mark.asyncio
async def test_verify_webhook_403():
    with pytest.raises(HTTPException) as e:
        await verify_webhook("subscribe", "wrong", "c")
    assert e.value.status_code == 403


# ── receive_webhook ──────────────────────────────────────────────────


class _Req:
    def __init__(self, body: bytes, sig: str = ""):
        self._body = body
        self.headers = {"X-Hub-Signature-256": sig} if sig else {}

    async def body(self):
        return self._body


@pytest.mark.asyncio
async def test_receive_webhook_bad_signature_403(monkeypatch):
    monkeypatch.setattr(whatsapp.settings, "FACEBOOK_APP_SECRET", "s3cret")
    with pytest.raises(HTTPException) as e:
        await receive_webhook(_Req(b"{}", "sha256=bad"), _DB())
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_receive_webhook_valid_signature(monkeypatch):
    monkeypatch.setattr(whatsapp.settings, "FACEBOOK_APP_SECRET", "s3cret")
    body = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()
    sig = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    monkeypatch.setattr(whatsapp, "parse_webhook_event", lambda b: [])
    monkeypatch.setitem(sys.modules, "app.api.whatsapp_flows", ModuleType("app.api.whatsapp_flows"))
    sys.modules["app.api.whatsapp_flows"].process_flow_responses = AsyncMock(return_value=[])
    out = await receive_webhook(_Req(body, f"sha256={sig}"), _DB())
    assert out["status"] == "ok"


@pytest.mark.asyncio
async def test_receive_webhook_no_secret_processes_events(monkeypatch):
    monkeypatch.setattr(whatsapp.settings, "FACEBOOK_APP_SECRET", "")
    monkeypatch.delenv("FACEBOOK_APP_SECRET", raising=False)
    events = [
        {"phone_number_id": "pn", "sender_phone": "+30", "sender_name": "A",
         "message_text": "hi", "message_type": "text", "message_id": "m1"},
        {"phone_number_id": "pn", "message_type": "status", "status": "read", "message_id": "m2"},
    ]
    monkeypatch.setattr(whatsapp, "parse_webhook_event", lambda b: events)
    inline = AsyncMock()
    monkeypatch.setattr(whatsapp, "_process_inline", inline)
    out = await receive_webhook(_Req(b"{}"), _DB())
    assert out["events_received"] == 2
    assert out["auto_replies_sent"] == 1
    inline.assert_awaited_once()


# ── _process_waba_level_events ────────────────────────────────────────


def test_waba_events_wrong_object():
    assert _process_waba_level_events({"object": "page", "entry": []}) == 0


def test_waba_events_messages_skipped():
    body = {"object": "whatsapp_business_account", "entry": [{"id": "w", "changes": [
        {"field": "messages", "value": {}},
        {"field": "account_update", "value": {"event": "PARTNER_INSTALLED"}},
    ]}]}
    assert _process_waba_level_events(body) == 1


def test_waba_events_field_branches():
    changes = [
        {"field": "account_update", "value": {
            "event": "OFFICIAL_BUSINESS_ACCOUNT",
            "ban_info": {"waba_ban_state": "SCHEDULE_FOR_DISABLE", "waba_ban_date": "d"},
        }},
        {"field": "account_review_update", "value": {"decision": "APPROVED"}},
        {"field": "account_alerts", "value": {"entity_type": "PHONE_NUMBER"}},
        {"field": "business_capability_update", "value": {"max_daily_conversations_per_phone_number": 250}},
        {"field": "phone_number_name_update", "value": {"decision": "APPROVED", "requested_verified_name": "X"}},
        {"field": "phone_number_quality_update", "value": {"event": "HIGH", "current_limit": "TIER_1K"}},
        {"field": "message_template_status_update", "value": {"event": "APPROVED", "message_template_name": "t"}},
        {"field": "template_category_update", "value": {"event": "MARKETING"}},
        {"field": "security", "value": {"display_phone_number": "+1"}},
        {"field": "unknown_field", "value": {}},
    ]
    body = {"object": "whatsapp_business_account", "entry": [{"id": "w", "changes": changes}]}
    assert _process_waba_level_events(body) == len(changes)


# ── _record_message ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_message_commits():
    acc = _wa_account()
    db = _DB()
    await _record_message(db, acc, peer_phone="+30", direction="inbound",
                          message_type="text", message_text="hi", message_id="w1")
    assert db.committed == 1
    assert len(db.executed) == 1


@pytest.mark.asyncio
async def test_record_message_failure_tolerated():
    acc = _wa_account()
    db = _DB()
    db.fail_commit = True
    await _record_message(db, acc, peer_phone="+30", direction="inbound",
                          message_type="text", message_text="hi")
    assert db.rolled_back == 1  # swallowed — non-fatal


# ── _process_inline ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_inline_no_account():
    db = _DB([None])
    await _process_inline(db, "pn", "+30", "A", "hi", "text", "m1")  # warn + return, no crash


@pytest.mark.asyncio
async def test_process_inline_records_and_skips_when_disabled(monkeypatch):
    acc = _wa_account(meta_data={"phone_number_id": "pn", "whatsapp_auto_reply": {"enabled": False}})
    db = _DB([acc])
    await _process_inline(db, "pn", "+30", "A", "hi", "text", "m1")
    assert db.committed == 1  # inbound recorded, no reply


@pytest.mark.asyncio
async def test_process_inline_bot_reply_path(monkeypatch):
    acc = _wa_account(meta_data={
        "phone_number_id": "pn", "access_token": "EAa",
        "whatsapp_auto_reply": {"enabled": True, "system_prompt": "s"},
    })
    db = _DB([acc])
    client = _patch_client(monkeypatch)

    mod = ModuleType("app.services.whatsapp_chatbot")
    mod.process_inbound_message = AsyncMock(return_value={"reply": "auto answer"})
    monkeypatch.setitem(sys.modules, "app.services.whatsapp_chatbot", mod)

    await _process_inline(db, "pn", "+30", "A", "hi", "text", "m1")
    client.mark_message_read.assert_awaited_once_with("m1")
    mod.process_inbound_message.assert_awaited_once()
    client.send_text.assert_awaited_once()
    assert db.committed == 2  # inbound + outbound


@pytest.mark.asyncio
async def test_process_inline_bot_skipped(monkeypatch):
    acc = _wa_account(meta_data={"phone_number_id": "pn", "access_token": "EAa",
                                 "whatsapp_auto_reply": {"enabled": True}})
    db = _DB([acc])
    client = _patch_client(monkeypatch)
    mod = ModuleType("app.services.whatsapp_chatbot")
    mod.process_inbound_message = AsyncMock(return_value={"skipped": True, "reason": "cooldown"})
    monkeypatch.setitem(sys.modules, "app.services.whatsapp_chatbot", mod)
    await _process_inline(db, "pn", "+30", "A", "hi", "text", "m1")
    client.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_inline_bot_error_swallowed(monkeypatch):
    acc = _wa_account(meta_data={"phone_number_id": "pn", "access_token": "EAa",
                                 "whatsapp_auto_reply": {"enabled": True}})
    db = _DB([acc])
    _patch_client(monkeypatch)
    mod = ModuleType("app.services.whatsapp_chatbot")
    mod.process_inbound_message = AsyncMock(side_effect=RuntimeError("ai down"))
    monkeypatch.setitem(sys.modules, "app.services.whatsapp_chatbot", mod)
    await _process_inline(db, "pn", "+30", "A", "hi", "text", "m1")  # logged, no raise


# ── Bot builder ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_bot_preset(monkeypatch):
    acc = _wa_account()
    db = _DB([acc])
    monkeypatch.setattr("app.api.deps.check_plan_feature", AsyncMock())
    _patch_client(monkeypatch)
    out = await create_bot(acc.id, BotCreateRequest(personality="support"), _user(), db)
    assert out["status"] == "ok"
    assert "customer support assistant" in out["bot"]["system_prompt"]
    assert acc.meta_data["whatsapp_auto_reply"]["enabled"] is True


@pytest.mark.asyncio
async def test_create_bot_custom_prompt_and_language(monkeypatch):
    acc = _wa_account()
    db = _DB([acc])
    monkeypatch.setattr("app.api.deps.check_plan_feature", AsyncMock())
    _patch_client(monkeypatch)
    out = await create_bot(
        acc.id,
        BotCreateRequest(custom_prompt="You help {business_name} users.", language="el"),
        _user(), db)
    assert "Cloudless WA" in out["bot"]["system_prompt"]
    assert "Greek" in out["bot"]["system_prompt"]


@pytest.mark.asyncio
async def test_create_bot_unknown_preset_falls_back(monkeypatch):
    acc = _wa_account()
    db = _DB([acc])
    monkeypatch.setattr("app.api.deps.check_plan_feature", AsyncMock())
    _patch_client(monkeypatch)
    out = await create_bot(acc.id, BotCreateRequest(personality="bogus"), _user(), db)
    assert "professional yet friendly" in out["bot"]["system_prompt"]


@pytest.mark.asyncio
async def test_get_bot_absent_and_present():
    acc = _wa_account(meta_data={})
    out = await get_bot(acc.id, _user(), _DB([acc]))
    assert out["exists"] is False
    acc.meta_data["whatsapp_bot"] = {"name": "B", "enabled": True}
    out = await get_bot(acc.id, _user(), _DB([acc]))
    assert out["exists"] is True


@pytest.mark.asyncio
async def test_update_bot_syncs_auto_reply():
    acc = _wa_account()
    db = _DB([acc])
    cfg = BotConfig(name="NB", enabled=True, system_prompt="sys")
    out = await update_bot(acc.id, cfg, _user(), db)
    assert acc.meta_data["whatsapp_auto_reply"]["system_prompt"] == "sys"
    assert out["status"] == "ok"


@pytest.mark.asyncio
async def test_activate_bot_400_no_bot(monkeypatch):
    acc = _wa_account(meta_data={})
    monkeypatch.setattr("app.api.deps.check_plan_feature", AsyncMock())
    with pytest.raises(HTTPException) as e:
        await activate_bot(acc.id, _user(), _DB([acc]))
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_activate_and_deactivate_bot(monkeypatch):
    acc = _wa_account(meta_data={
        "whatsapp_bot": {"name": "B", "enabled": False},
        "whatsapp_auto_reply": {"enabled": False},
    })
    monkeypatch.setattr("app.api.deps.check_plan_feature", AsyncMock())
    db = _DB([acc])
    out = await activate_bot(acc.id, _user(), db)
    assert acc.meta_data["whatsapp_bot"]["enabled"] is True
    assert out["enabled"] is True
    out = await deactivate_bot(acc.id, _user(), _DB([acc]))
    assert acc.meta_data["whatsapp_bot"]["enabled"] is False
    assert acc.meta_data["whatsapp_auto_reply"]["enabled"] is False


@pytest.mark.asyncio
async def test_deactivate_bot_400_no_bot():
    acc = _wa_account(meta_data={})
    with pytest.raises(HTTPException) as e:
        await deactivate_bot(acc.id, _user(), _DB([acc]))
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_bot_personalities():
    out = await get_bot_personalities(uuid.uuid4(), _user())
    ids = {p["id"] for p in out["personalities"]}
    assert {"professional_friendly", "casual", "formal", "support", "sales"} <= ids


# ── per-thread control ────────────────────────────────────────────────


def _chatbot_mod(**fns):
    mod = ModuleType("app.services.whatsapp_chatbot")
    for k, v in fns.items():
        setattr(mod, k, AsyncMock(return_value=v))
    return mod


@pytest.mark.asyncio
async def test_pause_and_resume_thread(monkeypatch):
    acc = _wa_account()
    mod = _chatbot_mod(pause_thread=None, resume_thread=None)
    monkeypatch.setitem(sys.modules, "app.services.whatsapp_chatbot", mod)
    out = await pause_thread(acc.id, ThreadPauseRequest(phone="+30"), _user(), _DB([acc]))
    mod.pause_thread.assert_awaited_once()
    out = await resume_thread(acc.id, ThreadPauseRequest(phone="+30"), _user(), _DB([acc]))
    mod.resume_thread.assert_awaited_once()
    assert out["status"] == "ok"


@pytest.mark.asyncio
async def test_thread_config_get_set(monkeypatch):
    acc = _wa_account()
    mod = _chatbot_mod(get_thread_config={"model": "m"}, set_thread_config=None)
    monkeypatch.setitem(sys.modules, "app.services.whatsapp_chatbot", mod)
    out = await get_thread_config(acc.id, "+30", _user(), _DB([acc]))
    assert out["config"]["model"] == "m"
    out = await set_thread_config(
        acc.id, "+30", ThreadConfigRequest(phone="+30", model="x", max_tokens=10),
        _user(), _DB([acc]))
    assert out["config"] == {"model": "x", "max_tokens": 10}
    mod.set_thread_config.assert_awaited_once()


@pytest.mark.asyncio
async def test_thread_memory_and_window(monkeypatch):
    acc = _wa_account()
    mod = _chatbot_mod(
        get_conversation_memory=[{"role": "user", "text": "hi"}],
        get_window_remaining=3600.0,
        is_in_service_window=True,
    )
    monkeypatch.setitem(sys.modules, "app.services.whatsapp_chatbot", mod)
    out = await get_thread_memory(acc.id, "+30", _user(), _DB([acc]))
    assert out["count"] == 1
    out = await get_service_window(acc.id, "+30", _user(), _DB([acc]))
    assert out["in_window"] is True
    assert out["hours_remaining"] == 1.0


# ── index_brand ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_index_brand_no_brand(monkeypatch):
    acc = _wa_account()
    mod = _chatbot_mod(index_brand_knowledge=0)
    monkeypatch.setitem(sys.modules, "app.services.whatsapp_chatbot", mod)
    out = await index_brand(acc.id, _user(), _DB([acc, None]))
    assert out["status"] == "error"


@pytest.mark.asyncio
async def test_index_brand_success(monkeypatch):
    acc = _wa_account()
    brand = SimpleNamespace(name="Cloudless", tagline="t", positioning_statement="p",
                            mission="m", industry="cloud", values=["v"],
                            target_audience={}, competitor_names=[])
    mod = _chatbot_mod(index_brand_knowledge=7)
    monkeypatch.setitem(sys.modules, "app.services.whatsapp_chatbot", mod)
    out = await index_brand(acc.id, _user(), _DB([acc, brand]))
    assert out["indexed"] == 7


# ── account-scoped phone endpoints ────────────────────────────────────


@pytest.mark.asyncio
async def test_phone_request_code(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch)
    out = await request_phone_code(acc.id, "SMS", "en_US", _user(), _DB([acc]))
    assert out["success"] is True


@pytest.mark.asyncio
async def test_phone_verify_code_502(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch, _FakeClient(verify_code=RuntimeError("x")))
    with pytest.raises(HTTPException) as e:
        await verify_account_phone_code(acc.id, "123", _user(), _DB([acc]))
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_phone_register_marks_meta(monkeypatch):
    acc = _wa_account()
    db = _DB([acc])
    _patch_client(monkeypatch)
    # this endpoint does a function-local `from sqlalchemy.orm.attributes import flag_modified`
    monkeypatch.setattr("sqlalchemy.orm.attributes.flag_modified", lambda *a, **k: None)
    out = await register_account_phone(acc.id, "123456", _user(), db)
    assert out["success"] is True
    assert acc.meta_data["phone_registered"] is True
    assert "phone_registered_at" in acc.meta_data


@pytest.mark.asyncio
async def test_phone_status(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch)
    out = await get_phone_status(acc.id, _user(), _DB([acc]))
    assert out["verified_name"] == "Cloudless"


@pytest.mark.asyncio
async def test_phone_status_502(monkeypatch):
    acc = _wa_account()
    _patch_client(monkeypatch, _FakeClient(get_phone_number_info=RuntimeError("x")))
    with pytest.raises(HTTPException) as e:
        await get_phone_status(acc.id, _user(), _DB([acc]))
    assert e.value.status_code == 502
