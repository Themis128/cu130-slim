"""Unit tests for the WhatsApp verify rate-limit window gate.

Regression: the task used to POST request_code every 30 min even while
inside Meta's 72h/10-request window — each call burns budget and keeps
the window rolling.
"""

import asyncio
import json as _json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.worker.tasks import whatsapp_verify as wv


def _account(meta: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id="acc-1",
        meta_data=meta,
        access_token_enc="enc",
    )


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, accounts):
        self._accounts = accounts

    async def execute(self, _stmt):
        return _Result(self._accounts)

    async def commit(self):
        pass


def _run_with(meta: dict, *, request_code=None):
    """Run _check_and_request against one fake account with the given
    meta_data. Returns (stats, request_code_calls)."""
    account = _account(meta)
    calls: list[str] = []

    async def _status(_pid, _tok):
        return {"code_verification_status": "NOT_VERIFIED"}

    async def _req(_pid, _tok, method="SMS"):
        calls.append(method)
        if request_code:
            return await request_code()
        return {"_status_code": 200}

    @asynccontextmanager
    async def _db():
        yield _FakeDB([account])

    async def _go():
        with (
            patch.object(wv, "_get_db", _db),
            patch.object(wv, "decrypt_token", return_value="tok"),
            patch.object(wv, "_check_phone_status", side_effect=_status),
            patch.object(wv, "_request_code", side_effect=_req),
            patch.object(wv, "flag_modified", lambda *a, **k: None),
        ):
            return await wv._check_and_request()

    import asyncio

    stats = asyncio.run(_go())
    return stats, calls


def test_rate_limit_window_skips_code_request():
    """Inside the 72h window the task must NOT call request_code."""
    meta = {
        "phone_number_id": "pid-1",
        "whatsapp_code_status": "rate_limited",
        "whatsapp_rate_limited_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
    }
    stats, calls = _run_with(meta)
    assert calls == []  # no request_code call burned
    assert stats["rate_limited"] == 1
    assert "skipping code request" in stats["details"][0]


def test_rate_limit_window_expired_retries():
    """After the 72h window the task retries normally."""
    meta = {
        "phone_number_id": "pid-1",
        "whatsapp_code_status": "rate_limited",
        "whatsapp_rate_limited_at": (datetime.now(UTC) - timedelta(hours=73)).isoformat(),
    }
    stats, calls = _run_with(meta)
    assert calls == ["SMS"]
    assert stats["code_sent"] == 1


def test_rate_limited_without_timestamp_still_attempts_once():
    """Legacy rows (status set before the timestamp field existed) attempt
    one request, then get stamped by the 136024 handler."""
    meta = {
        "phone_number_id": "pid-1",
        "whatsapp_code_status": "rate_limited",
    }

    async def _limited():
        return {"_status_code": 400, "error": {"code": 136024, "error_user_msg": "limit"}}

    stats, calls = _run_with(meta, request_code=_limited)
    assert calls == ["SMS"]
    assert stats["rate_limited"] == 1


def test_sent_code_is_not_resent_within_24h():
    """A successful request must not re-burst: while a code is outstanding
    for this phone, the 30-min beat keeps polling status but never calls
    request_code again."""
    meta = {
        "phone_number_id": "pid-1",
        "whatsapp_code_status": "sent",
        "whatsapp_code_sent_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
        "whatsapp_code_sent_phone_id": "pid-1",
    }
    stats, calls = _run_with(meta)
    assert calls == []
    assert stats["code_sent"] == 0
    assert "code already sent" in stats["details"][0]


def test_sent_code_resent_after_24h():
    """If the code went stale unverified, a resend is allowed."""
    meta = {
        "phone_number_id": "pid-1",
        "whatsapp_code_status": "sent",
        "whatsapp_code_sent_at": (datetime.now(UTC) - timedelta(hours=25)).isoformat(),
        "whatsapp_code_sent_phone_id": "pid-1",
    }
    stats, calls = _run_with(meta)
    assert calls == ["SMS"]
    assert stats["code_sent"] == 1


def test_rate_limit_is_scoped_to_phone_id():
    """A rate-limit stamped for a different phone_number_id must not block
    the current phone (Meta quotas are per-number)."""
    meta = {
        "phone_number_id": "pid-new",
        "whatsapp_code_status": "rate_limited",
        "whatsapp_rate_limited_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        "whatsapp_rate_limited_phone_id": "pid-old",
    }
    stats, calls = _run_with(meta)
    assert calls == ["SMS"]
    assert stats["code_sent"] == 1


def test_saturated_request_log_skips():
    """9 logged attempts inside 72h = headroom cap hit — skip without
    calling Meta."""
    recent = datetime.now(UTC) - timedelta(hours=3)
    meta = {
        "phone_number_id": "pid-1",
        "whatsapp_code_request_log": {
            "pid-1": [(recent + timedelta(minutes=i)).isoformat() for i in range(9)]
        },
    }
    stats, calls = _run_with(meta)
    assert calls == []
    assert stats["rate_limited"] == 1
    assert "saturated" in stats["details"][0]


def test_attempt_logged_before_request():
    """Every request_code attempt is recorded in the per-phone log so the
    72h cap is exact — even when Meta rejects it."""
    meta = {"phone_number_id": "pid-1"}
    stats, calls = _run_with(meta)
    assert calls == ["SMS"]
    assert stats["code_sent"] == 1
    assert len(meta["whatsapp_code_request_log"]["pid-1"]) == 1


# ── remaining branches: HTTP helpers + poller edges ───────────────────


def _http(status=200, body=None, text=""):
    resp = SimpleNamespace(status_code=status, text=text,
                           json=lambda: body or {})
    calls = []

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            calls.append(("get", url, kw))
            return resp

        async def post(self, url, **kw):
            calls.append(("post", url, kw))
            return resp

    return _C(), calls


def test_check_phone_status_and_request_code(monkeypatch):
    import app.worker.tasks.whatsapp_verify as wv2

    http, calls = _http(body={"code_verification_status": "VERIFIED"})
    monkeypatch.setattr(wv2.httpx, "AsyncClient", lambda **kw: http)
    out = asyncio.run(wv2._check_phone_status("111", "tok"))
    assert out["code_verification_status"] == "VERIFIED"
    assert "111" in calls[0][1]
    assert calls[0][2]["params"]["access_token"] == "tok"

    # non-200 → error text
    http, _ = _http(status=400, text="bad request")
    monkeypatch.setattr(wv2.httpx, "AsyncClient", lambda **kw: http)
    out = asyncio.run(wv2._check_phone_status("111", "tok"))
    assert out["error"] == "bad request"

    # request_code — status annotated on body
    http, calls = _http(status=200, body={"success": True})
    monkeypatch.setattr(wv2.httpx, "AsyncClient", lambda **kw: http)
    out = asyncio.run(wv2._request_code("111", "tok", "VOICE"))
    assert out["success"] is True and out["_status_code"] == 200
    assert calls[0][2]["params"]["code_method"] == "VOICE"
    assert calls[0][2]["headers"]["Authorization"] == "Bearer tok"


def _run(meta, *, status=None, req=None, token="tok"):
    account = _account(meta)
    calls = []

    async def _status(*a):
        return status if status is not None else {
            "code_verification_status": "NOT_VERIFIED"}

    async def _req(*a, **k):
        calls.append(k.get("method", "SMS"))
        return req if req is not None else {"_status_code": 200}

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _db():
        yield _FakeDB([account])

    async def _go():
        with (
            patch.object(wv, "_get_db", _db),
            patch.object(wv, "decrypt_token", return_value=token),
            patch.object(wv, "_check_phone_status", side_effect=_status),
            patch.object(wv, "_request_code", side_effect=_req),
            patch.object(wv, "flag_modified", lambda *a, **k: None),
        ):
            return await wv._check_and_request()

    return asyncio.run(_go()), calls, account


def test_poller_edge_paths():
    # no phone_number_id → error
    stats, calls, _ = _run({})
    assert stats["errors"] == 1 and "no phone_number_id" in stats["details"][0]

    # decrypt fails → error
    stats, calls, _ = _run({"phone_number_id": "1"}, token=None)
    assert stats["errors"] == 1 and "decrypt" in stats["details"][0]

    # status check error → error
    stats, calls, _ = _run({"phone_number_id": "1"},
                           status={"error": "boom"})
    assert stats["errors"] == 1 and "status check failed" in \
        stats["details"][0]

    # meta as JSON string → parsed
    meta_json = _json.dumps({"phone_number_id": "1"})
    stats, calls, acct = _run(meta_json)
    assert stats["checked"] == 1 and stats["code_sent"] == 1

    # already VERIFIED → counted, no request
    stats, calls, _ = _run({"phone_number_id": "1"},
                           status={"code_verification_status": "VERIFIED"})
    assert stats["already_verified"] == 1 and calls == []

    # code sent → meta marked
    stats, calls, acct = _run({"phone_number_id": "1"})
    assert stats["code_sent"] == 1
    meta = acct.meta_data
    assert meta["whatsapp_code_status"] == "sent"
    assert meta["whatsapp_code_sent_phone_id"] == "1"
    assert "whatsapp_code_sent_at" in meta

    # 136024 → rate_limited + meta recorded
    stats, calls, acct = _run(
        {"phone_number_id": "1"},
        req={"_status_code": 400,
             "error": {"code": 136024,
                       "error_user_msg": "too many"}})
    assert stats["rate_limited"] == 1
    assert acct.meta_data["whatsapp_code_status"] == "rate_limited"
    assert acct.meta_data["whatsapp_code_error"] == "too many"

    # other error → errors bucket
    stats, calls, _ = _run(
        {"phone_number_id": "1"},
        req={"_status_code": 500, "error": {"code": 1, "message": "x"}})
    assert stats["errors"] == 1 and "request failed" in stats["details"][0]


def test_check_whatsapp_verification_task(monkeypatch):
    monkeypatch.setattr(wv, "run_async",
                        lambda c: asyncio.get_event_loop()
                        .run_until_complete(c) if False else "ran")
    monkeypatch.setattr(wv, "_check_and_request",
                        AsyncMock(return_value={"ok": 1}))
    # run_async is called with the coroutine
    seen = {}
    def fake_ra(coro):
        seen["coro"] = True
        coro.close()
        return {"ok": 1}
    monkeypatch.setattr(wv, "run_async", fake_ra)
    out = wv.check_whatsapp_verification()
    assert out == {"ok": 1} and seen["coro"]


def test_request_log_timestamp_edges_and_task_error():
    # bad iso string skipped, naive datetime gets UTC tz, old entry pruned
    meta = {
        "phone_number_id": "1",
        "whatsapp_code_request_log": {"1": [
            "not-a-date",                      # ValueError → skip
            "2020-01-01T00:00:00",             # naive + old → dropped
            datetime.now(UTC).isoformat(),      # recent → kept
        ]},
        # _meta_dt bad value → treated as unset (no rate-limit skip)
        "whatsapp_rate_limited_at": "garbage",
        "whatsapp_code_sent_at": 12345,         # TypeError → None
    }
    stats, calls, acct = _run(meta)
    assert stats["code_sent"] == 1  # proceeded past the guards
    log = acct.meta_data["whatsapp_code_request_log"]["1"]
    assert len(log) == 2  # recent kept + new attempt appended

    # task-level exception → error dict
    def _boom(coro):
        coro.close()
        raise RuntimeError("celery dead")
    with patch.object(wv, "run_async", _boom):
        out = wv.check_whatsapp_verification()
    assert out == {"error": "celery dead"}
