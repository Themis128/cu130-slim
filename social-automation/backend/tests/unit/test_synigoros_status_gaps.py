"""Coverage gaps: synigoros_status — env file, Gmail OTP lane, OTP poll
flow, kpa/status tail raises, session-stop failure paths."""

import json
from unittest.mock import MagicMock

import pytest


def test_env_file_parses(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_status as M

    f = tmp_path / ".env"
    f.write_text("A=1\nB=\"two\"\n# comment\nlower=x\nC='three'\n")
    monkeypatch.setattr(M, "ENV_FILE", str(f))
    assert M._env_file() == {"A": "1", "B": "two", "C": "three"}


def test_env_file_missing(tmp_path, monkeypatch):
    from app.worker.tasks import synigoros_status as M

    monkeypatch.setattr(M, "ENV_FILE", str(tmp_path / "nope"))
    assert M._env_file() == {}


def test_gmail_access_token_missing_creds(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    monkeypatch.setattr(M, "_env_file", lambda: {})
    with pytest.raises(RuntimeError, match="gmail oauth creds missing"):
        M._gmail_access_token()


def test_gmail_access_token_success(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    monkeypatch.setattr(
        M,
        "_env_file",
        lambda: {
            "GMAIL_REFRESH_TOKEN": "rt",
            "GMAIL_OAUTH_CLIENT_ID": "cid",
            "GMAIL_OAUTH_CLIENT_SECRET": "sec",
        },
    )
    seen = {}

    class _R:
        def read(self):
            return b'{"access_token":"tok123"}'

    def fake_urlopen(req, timeout=0):
        seen["data"] = req.data
        return _R()

    monkeypatch.setattr(M.urllib.request, "urlopen", fake_urlopen)
    assert M._gmail_access_token() == "tok123"
    assert b"refresh_token=rt" in seen["data"]
    assert b"client_id=cid" in seen["data"]


class _FakeTime:
    """Module-local stand-in for `time` — deterministic clock, no-op sleep.

    Replaces the `time` name inside synigoros_status only, so pytest internals
    keep the real clock.
    """

    def __init__(self, ticks=()):
        self._ticks = iter(ticks)

    def time(self):
        return next(self._ticks)

    def sleep(self, s):
        pass


class _JsonResp:
    def __init__(self, payload):
        self._p = payload

    def read(self):
        return json.dumps(self._p).encode()


def test_gmail_otp_returns_code(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    monkeypatch.setattr(M, "_gmail_access_token", lambda: "tok")
    msgs = {"messages": [{"id": ""}, {"id": "m1"}]}
    full = {"internalDate": "1000000", "snippet": "Your code is 654321"}
    monkeypatch.setattr(
        M.urllib.request,
        "urlopen",
        MagicMock(side_effect=[_JsonResp(msgs), _JsonResp(full)]),
    )
    monkeypatch.setattr(M, "time", _FakeTime([0, 1, 999]))
    assert M._gmail_otp(1000) == "654321"


def test_gmail_otp_skips_stale_and_codeless(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    monkeypatch.setattr(M, "_gmail_access_token", lambda: "tok")
    msgs = {"messages": [{"id": "m1"}, {"id": "m2"}]}
    stale = {"internalDate": "500000", "snippet": "654321"}
    nocode = {"internalDate": "1000000", "snippet": "no digits"}
    monkeypatch.setattr(
        M.urllib.request,
        "urlopen",
        MagicMock(side_effect=[_JsonResp(msgs), _JsonResp(stale), _JsonResp(nocode)]),
    )
    # t=0 → deadline 100; t=1 enters loop; t=200 exits after one sweep
    monkeypatch.setattr(M, "time", _FakeTime([0, 1, 200]))
    assert M._gmail_otp(1000) is None


def test_gmail_otp_baddate_and_get_failure(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    monkeypatch.setattr(M, "_gmail_access_token", lambda: "tok")
    msgs = {"messages": [{"id": "m1"}, {"id": "m2"}]}
    baddate = {"internalDate": "not-a-number", "snippet": "654321"}
    monkeypatch.setattr(
        M.urllib.request,
        "urlopen",
        MagicMock(side_effect=[_JsonResp(msgs), _JsonResp(baddate), OSError("gmail get down")]),
    )
    monkeypatch.setattr(M, "time", _FakeTime([0, 1, 200]))
    assert M._gmail_otp(1000) is None


def test_gmail_otp_list_failure_resets_token(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    monkeypatch.setattr(M, "_gmail_access_token", lambda: "tok")
    monkeypatch.setattr(M.urllib.request, "urlopen", MagicMock(side_effect=OSError("gmail list down")))
    monkeypatch.setattr(M, "time", _FakeTime([0, 1, 200]))
    assert M._gmail_otp(1000) is None


def test_gmail_otp_token_refresh_failure(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    monkeypatch.setattr(M, "_gmail_access_token", MagicMock(side_effect=RuntimeError("no creds")))
    monkeypatch.setattr(M, "time", _FakeTime([0, 1, 200]))
    assert M._gmail_otp(1000) is None


# ── _poll_inner OTP lane + tail raises ──────────────────────────────────────

WEBMAIL_EXPR = "!!document.getElementById('webMail')"
STATUS_EXPR = "document.body.innerText.includes('Κατάσταση')"
FAIL_EXPR = "document.body.innerText.includes('Δεν έγινε ταυτοποίηση')"
OTP_EXPR = "!!document.getElementById('otpCode')"


def _otp_ev(M, state):
    def _ev(expr):
        if expr == WEBMAIL_EXPR:
            return True
        if expr.startswith("(()=>{const m=document.getElementById('webMail')"):
            return 1
        if expr == STATUS_EXPR:
            return state["done"]
        if expr == FAIL_EXPR:
            return False
        if expr == OTP_EXPR:
            return not state["done"]
        if "otpCode" in expr and "value=" in expr:
            state["done"] = True
            return 1
        return json.dumps({"status": "Αναμονή", "proto": "11", "kpa": M.KPA})

    return _ev


def test_poll_inner_otp_flow(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    monkeypatch.setattr(M, "KPA", "k123")
    monkeypatch.setattr(M, "_bridge", lambda p, d, **k: {"ok": 1})
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    monkeypatch.setattr(M, "_gmail_otp", lambda since: "654321")
    state = {"done": False}
    monkeypatch.setattr(M, "_ev", _otp_ev(M, state))
    out = M._poll_inner()
    assert out["status"] == "Αναμονή"
    assert state["done"] is True


def test_poll_inner_otp_missing_code_raises(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    monkeypatch.setattr(M, "KPA", "k123")
    monkeypatch.setattr(M, "_bridge", lambda p, d, **k: {"ok": 1})
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    monkeypatch.setattr(M, "_gmail_otp", lambda since: None)
    monkeypatch.setattr(M, "_ev", _otp_ev(M, {"done": False}))
    with pytest.raises(RuntimeError, match="OTP screen shown but no fresh Gmail code"):
        M._poll_inner()


def test_poll_inner_otp_loop_exhausts(monkeypatch):
    """30 OTP iterations with no code/status/failure → falls through to extract."""
    from app.worker.tasks import synigoros_status as M

    monkeypatch.setattr(M, "KPA", "k123")
    monkeypatch.setattr(M, "_bridge", lambda p, d, **k: {"ok": 1})
    monkeypatch.setattr(M.time, "sleep", lambda s: None)

    def _ev(expr):
        if expr == WEBMAIL_EXPR:
            return True
        if expr.startswith("(()=>{const m=document.getElementById('webMail')"):
            return 1
        if expr in (STATUS_EXPR, FAIL_EXPR, OTP_EXPR):
            return False
        return json.dumps({"status": "Αναμονή", "proto": "11", "kpa": M.KPA})

    monkeypatch.setattr(M, "_ev", _ev)
    out = M._poll_inner()
    assert out["status"] == "Αναμονή"


def test_poll_inner_kpa_mismatch_raises(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    monkeypatch.setattr(M, "KPA", "expected-kpa")
    monkeypatch.setattr(M, "_bridge", lambda p, d, **k: {"ok": 1})
    monkeypatch.setattr(M, "_ev", lambda e: _status_ev(e, "other-kpa"))
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="cached session showed"):
        M._poll()


def test_poll_inner_empty_status_raises(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    def _ev(expr):
        if expr == WEBMAIL_EXPR:
            return False
        if expr == STATUS_EXPR:
            return True
        return json.dumps({"status": "", "proto": "", "kpa": ""})

    monkeypatch.setattr(M, "_bridge", lambda p, d, **k: {"ok": 1})
    monkeypatch.setattr(M, "_ev", _ev)
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="status extraction empty"):
        M._poll()


# ── _poll stop failure paths ────────────────────────────────────────────────


def test_poll_stop_raises_is_swallowed(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    def _bridge(path, data, **k):
        if path == "/session/stop":
            raise RuntimeError("bridge gone")
        return {"ok": 1}

    monkeypatch.setattr(M, "_bridge", _bridge)
    monkeypatch.setattr(M, "_poll_inner", lambda: {"status": "x"})
    assert M._poll() == {"status": "x"}


def _status_ev(expr, kpa):
    if expr == WEBMAIL_EXPR:
        return False
    if expr == STATUS_EXPR:
        return True
    if "Δεν έγινε" in expr:
        return False
    return json.dumps({"status": "Αναμονή", "proto": "11", "kpa": kpa})


def test_gmail_otp_reuses_token_across_sweeps(monkeypatch):
    """Second poll iteration skips token refresh (token is not None)."""
    from app.worker.tasks import synigoros_status as M

    tok = MagicMock(return_value="tok")
    monkeypatch.setattr(M, "_gmail_access_token", tok)
    empty = {"messages": []}
    msgs = {"messages": [{"id": "m1"}]}
    full = {"internalDate": "1000000", "snippet": "code 111222"}
    monkeypatch.setattr(
        M.urllib.request,
        "urlopen",
        MagicMock(side_effect=[_JsonResp(empty), _JsonResp(msgs), _JsonResp(full)]),
    )
    monkeypatch.setattr(M, "time", _FakeTime([0, 1, 2, 999]))
    assert M._gmail_otp(1000) == "111222"
    assert tok.call_count == 1
