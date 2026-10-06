"""Unit tests for the WhatsApp verify rate-limit window gate.

Regression: the task used to POST request_code every 30 min even while
inside Meta's 72h/10-request window — each call burns budget and keeps
the window rolling.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

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
