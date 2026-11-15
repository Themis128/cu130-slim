"""Unit tests for app.services.linkedin_reauth — sidecar two-phase login
client, SMS-code parsing, credential resolution, and run_reauth flow."""

from __future__ import annotations

import pytest

from app.services import linkedin_reauth as R


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or str(self._payload)

    def json(self):
        return self._payload


class _Http:
    """Minimal httpx.AsyncClient stand-in (async context manager)."""

    def __init__(self, routes=None, default=None):
        self.routes = routes or {}
        self.default = default or _Resp(200, {"ok": True})
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def _pick(self, url):
        for needle, resp in self.routes.items():
            if needle in url:
                return resp
        return self.default

    async def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self._pick(url)

    async def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self._pick(url)


def _client(monkeypatch, http=None, **kw) -> R.LinkedInReauthClient:
    http = http or _Http()
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda *a, **k: http)
    return R.LinkedInReauthClient(**kw)


@pytest.mark.asyncio
async def test_client_start_verify_cancel_status(monkeypatch):
    http = _Http(
        routes={
            "/login/start": _Resp(200, {"stage": "enter_code", "two_factor_required": True}),
            "/login/verify": _Resp(200, {"logged_in": True, "stage": "done"}),
            "/login/cancel": _Resp(200, {"cancelled": True}),
            "/session": _Resp(200, {"logged_in": True}),
        }
    )
    c = _client(monkeypatch, http)

    out = await c.start("u", "p", method="sms")
    assert out["stage"] == "enter_code"
    assert http.calls[-1][2]["json"]["method"] == "sms"

    out = await c.verify("123456")
    assert out["logged_in"] is True
    assert http.calls[-1][2]["json"] == {"code": "123456"}

    assert (await c.cancel())["cancelled"] is True
    assert (await c.status())["logged_in"] is True


@pytest.mark.asyncio
async def test_client_error_raises(monkeypatch):
    http = _Http(default=_Resp(500, text="boom"))
    c = _client(monkeypatch, http)
    with pytest.raises(R.LinkedInReauthError) as ei:
        await c.start("u", "p")
    assert ei.value.status_code == 500


@pytest.mark.asyncio
async def test_resolve_credentials_global(monkeypatch):
    async def fake_get(key, default=None):
        return {"LINKEDIN_USERNAME": "u@x.com", "LINKEDIN_PASSWORD": "pw"}.get(key, default)

    monkeypatch.setattr(R.secret_store, "get", fake_get)
    assert await R.resolve_linkedin_credentials() == ("u@x.com", "pw")


@pytest.mark.asyncio
async def test_resolve_credentials_per_account_wins(monkeypatch):
    async def fake_get(key, default=None):
        return {
            "LINKEDIN_USERNAME_acc1": "acc@x.com",
            "LINKEDIN_PASSWORD_acc1": "pw2",
            "LINKEDIN_USERNAME": "u@x.com",
            "LINKEDIN_PASSWORD": "pw",
        }.get(key, default)

    monkeypatch.setattr(R.secret_store, "get", fake_get)
    assert await R.resolve_linkedin_credentials("acc1") == ("acc@x.com", "pw2")


@pytest.mark.asyncio
async def test_resolve_credentials_missing(monkeypatch):
    async def fake_get(key, default=None):
        return default

    monkeypatch.setattr(R.secret_store, "get", fake_get)
    with pytest.raises(R.LinkedInReauthError) as ei:
        await R.resolve_linkedin_credentials()
    assert ei.value.status_code == 401


def test_sms_row_parsing():
    # Mirrors the content-provider row format seen on device.
    line = ('Row: 0 address=+3069, date=1794693498266, '
            'body=Your LinkedIn verification code is 531797.')
    m = R._ROW_RE.search(line)
    assert m and m.group(3).startswith("Your LinkedIn")
    assert R._LI_CODE_RE.search(m.group(3)).group(1) == "531797"
    assert R._LI_HINT_RE.search(m.group(3))
    # Non-LinkedIn rows must not match the code extractor path.
    insta = "Row: 1 address=+1, date=1, body=257641 is your Instagram code."
    row = R._ROW_RE.search(insta)
    assert row and not R._LI_HINT_RE.search(row.group(3))


@pytest.mark.asyncio
async def test_run_reauth_full_flow(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def start(self, u, p, method="sms"):
            calls.append(("start", u, p, method))
            return {"stage": "enter_code", "two_factor_required": True}

        async def verify(self, code):
            calls.append(("verify", code))
            return {"logged_in": True, "stage": "done"}

    monkeypatch.setattr(R, "LinkedInReauthClient", FakeClient)

    async def fake_sms(since_ms=None, timeout_s=240):
        return "654321"

    monkeypatch.setattr(R, "fetch_linkedin_sms_code", fake_sms)

    out = await R.run_reauth("u", "p")
    assert out["logged_in"] is True
    assert calls == [("start", "u", "p", "sms"), ("verify", "654321")]


@pytest.mark.asyncio
async def test_run_reauth_no_2fa_needed(monkeypatch):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def start(self, u, p, method="sms"):
            return {"logged_in": True, "stage": "done"}

    monkeypatch.setattr(R, "LinkedInReauthClient", FakeClient)
    out = await R.run_reauth("u", "p")
    assert out["logged_in"] is True


@pytest.mark.asyncio
async def test_run_reauth_manual_code_skips_sms(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def start(self, u, p, method="sms"):
            return {"stage": "enter_code", "two_factor_required": True}

        async def verify(self, code):
            calls.append(code)
            return {"logged_in": False, "two_factor_required": True}

    monkeypatch.setattr(R, "LinkedInReauthClient", FakeClient)

    async def boom(since_ms=None, timeout_s=240):
        raise AssertionError("SMS fetch must not run when code supplied")

    monkeypatch.setattr(R, "fetch_linkedin_sms_code", boom)
    out = await R.run_reauth("u", "p", code="111222", auto_sms=True)
    assert out["stage"] == "enter_code"
    assert calls == ["111222"]


# ── ADB key dir + SMS inbox fetch ──────────────────────────────────────────


def test_adb_key_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKEDIN_ADB_KEY_DIR", str(tmp_path))
    with pytest.raises(R.LinkedInReauthError) as ei:
        R._adb_key_dir()
    assert ei.value.status_code == 503


def _write_adb_keypair(d) -> None:
    """Real RSA keypair — adb-shell's CryptographySigner parses the PEMs."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (d / "adbkey").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    (d / "adbkey.pub").write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.OpenSSH,
            serialization.PublicFormat.OpenSSH,
        )
    )


def test_adb_key_dir_present(tmp_path, monkeypatch):
    (tmp_path / "adbkey").write_text("k")
    monkeypatch.setenv("LINKEDIN_ADB_KEY_DIR", str(tmp_path))
    assert R._adb_key_dir() == tmp_path


def test_sms_codes_blocking_parses(monkeypatch, tmp_path):
    _write_adb_keypair(tmp_path)

    class _Dev:
        def __init__(self, *a, **k):
            pass

        def connect(self, **kw):
            pass

        def shell(self, cmd):
            return (
                "Row: 0 address=+1, date=999, body=not linkedin 123456.\n"
                "Row: 1 address=+3069, date=2000, "
                "body=Your LinkedIn verification code is 654321.\n"
                "Row: 2 address=+3069, date=1000, "
                "body=Your LinkedIn verification code is 111111.\n"
                "Row: 3 address=+3069, date=500, "
                "body=Your LinkedIn sign-in link expired.\n"
                "Row: 4 address=+1, date=3000, "
                "body=Your verification code is 999999.\n"
                "not a row line\n"
            )

        def close(self):
            pass

    import adb_shell.adb_device as adb_mod

    monkeypatch.setattr(adb_mod, "AdbDeviceTcp", _Dev)
    monkeypatch.setenv("LINKEDIN_SMS_ADB_ADDR", "192.168.1.77:5555")
    codes = R._sms_codes_blocking(tmp_path, None)
    assert codes == [(2000, "654321"), (1000, "111111")]
    # since_ms filters older rows
    assert R._sms_codes_blocking(tmp_path, 1500) == [(2000, "654321")]


def test_sms_codes_blocking_addr_fallback(monkeypatch, tmp_path):
    """First ADB endpoints refuse → next candidate wins; all fail → 503."""
    _write_adb_keypair(tmp_path)
    monkeypatch.delenv("LINKEDIN_SMS_ADB_ADDR", raising=False)

    import adb_shell.adb_device as adb_mod

    attempts = {"n": 0}

    class _Flaky:
        def __init__(self, host, port):
            self.host = host

        def connect(self, rsa_keys=None):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise OSError("connection refused")

        def shell(self, cmd):
            return (
                "Row: 0 address=+3069, date=42, "
                "body=Your LinkedIn verification code is 777777."
            )

        def close(self):
            pass

    monkeypatch.setattr(adb_mod, "AdbDeviceTcp", _Flaky)
    assert R._sms_codes_blocking(tmp_path, None) == [(42, "777777")]
    assert attempts["n"] == 3

    class _Dead:
        def __init__(self, host, port):
            pass

        def connect(self, rsa_keys=None):
            raise OSError("no route")

        def shell(self, cmd):
            return ""

        def close(self):
            raise OSError("close failed")  # finally must swallow it

    monkeypatch.setattr(adb_mod, "AdbDeviceTcp", _Dead)
    with pytest.raises(R.LinkedInReauthError) as ei:
        R._sms_codes_blocking(tmp_path, None)
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_fetch_sms_code_success(tmp_path, monkeypatch):
    (tmp_path / "adbkey").write_text("k")
    monkeypatch.setenv("LINKEDIN_ADB_KEY_DIR", str(tmp_path))

    def fake_blocking(keys_dir, since_ms):
        return [(123, "246810")]

    monkeypatch.setattr(R, "_sms_codes_blocking", fake_blocking)
    assert await R.fetch_linkedin_sms_code() == "246810"


@pytest.mark.asyncio
async def test_fetch_sms_code_timeout(tmp_path, monkeypatch):
    (tmp_path / "adbkey").write_text("k")
    monkeypatch.setenv("LINKEDIN_ADB_KEY_DIR", str(tmp_path))

    def fake_blocking(keys_dir, since_ms):
        return []

    monkeypatch.setattr(R, "_sms_codes_blocking", fake_blocking)
    with pytest.raises(R.LinkedInReauthError) as ei:
        await R.fetch_linkedin_sms_code(timeout_s=0)
    assert ei.value.status_code == 504


@pytest.mark.asyncio
async def test_fetch_sms_code_empty_poll(tmp_path, monkeypatch):
    """Empty poll results must loop until the deadline (180->185 branch)."""
    import asyncio as _aio

    (tmp_path / "adbkey").write_text("k")
    monkeypatch.setenv("LINKEDIN_ADB_KEY_DIR", str(tmp_path))

    def fake_blocking(keys_dir, since_ms):
        return []

    async def _fast_sleep(*a, **k):
        return None

    monkeypatch.setattr(R, "_sms_codes_blocking", fake_blocking)
    monkeypatch.setattr(_aio, "sleep", _fast_sleep)
    with pytest.raises(R.LinkedInReauthError) as ei:
        await R.fetch_linkedin_sms_code(timeout_s=0.05)
    assert ei.value.status_code == 504


@pytest.mark.asyncio
async def test_fetch_sms_code_error_then_timeout(tmp_path, monkeypatch):
    import asyncio as _aio

    (tmp_path / "adbkey").write_text("k")
    monkeypatch.setenv("LINKEDIN_ADB_KEY_DIR", str(tmp_path))

    def fake_blocking(keys_dir, since_ms):
        raise OSError("device offline")

    async def _fast_sleep(*a, **k):
        return None

    monkeypatch.setattr(R, "_sms_codes_blocking", fake_blocking)
    monkeypatch.setattr(_aio, "sleep", _fast_sleep)
    with pytest.raises(R.LinkedInReauthError) as ei:
        await R.fetch_linkedin_sms_code(timeout_s=0.01)
    assert ei.value.status_code == 504
    assert "device offline" in ei.value.detail


@pytest.mark.asyncio
async def test_arm_keepawake_success_and_failure(monkeypatch):
    http = _Http()
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda *a, **k: http)
    await R._arm_sidecar_keepawake()
    assert any("keepawake" in c[1] for c in http.calls)

    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise OSError("stack-ops down")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(R.httpx, "AsyncClient", lambda *a, **k: _Boom())
    await R._arm_sidecar_keepawake()  # non-fatal


@pytest.mark.asyncio
async def test_run_reauth_start_failure(monkeypatch):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def start(self, u, p, method="sms"):
            return {"message": "bad credentials"}

    monkeypatch.setattr(R, "LinkedInReauthClient", FakeClient)
    with pytest.raises(R.LinkedInReauthError) as ei:
        await R.run_reauth("u", "p")
    assert ei.value.status_code == 502


@pytest.mark.asyncio
async def test_run_reauth_enter_code_without_sms(monkeypatch):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def start(self, u, p, method="sms"):
            return {"two_factor_required": True}

    monkeypatch.setattr(R, "LinkedInReauthClient", FakeClient)

    async def boom(since_ms=None, timeout_s=240):
        raise AssertionError("SMS fetch must not run with auto_sms=False")

    monkeypatch.setattr(R, "fetch_linkedin_sms_code", boom)
    out = await R.run_reauth("u", "p", auto_sms=False)
    assert out["stage"] == "enter_code"
    assert out["two_factor_required"] is True


@pytest.mark.asyncio
async def test_status_error_raises(monkeypatch):
    http = _Http(default=_Resp(503, text="down"))
    c = _client(monkeypatch, http)
    with pytest.raises(R.LinkedInReauthError) as ei:
        await c.status()
    assert ei.value.status_code == 503


# ── API endpoints ──────────────────────────────────────────────────────────

from fastapi import HTTPException  # noqa: E402

import app.api.linkedin as L  # noqa: E402


def _user():
    import uuid as _u
    from types import SimpleNamespace as _S

    return _S(id=_u.uuid4(), email="a@x.co")


@pytest.mark.asyncio
async def test_api_reauth_success(monkeypatch):
    async def fake_creds(account_id=None):
        return "u@x.com", "pw"

    async def fake_run(u, p, **kw):
        return {"logged_in": True, "stage": "done"}

    monkeypatch.setattr(R, "resolve_linkedin_credentials", fake_creds)
    monkeypatch.setattr(R, "run_reauth", fake_run)
    out = await L.linkedin_reauth(L.ReauthRequest(), _user())
    assert out["logged_in"] is True


@pytest.mark.asyncio
async def test_api_reauth_error_maps_status(monkeypatch):
    async def fake_creds(account_id=None):
        return "u", "p"

    async def fake_run(u, p, **kw):
        raise R.LinkedInReauthError(502, "login start failed")

    monkeypatch.setattr(R, "resolve_linkedin_credentials", fake_creds)
    monkeypatch.setattr(R, "run_reauth", fake_run)
    with pytest.raises(HTTPException) as ei:
        await L.linkedin_reauth(L.ReauthRequest(), _user())
    assert ei.value.status_code == 502


class _FakeSidecarClient:
    def __init__(self, *a, **k):
        pass

    async def verify(self, code):
        return {"logged_in": True}

    async def cancel(self):
        return {"cancelled": True}

    async def status(self):
        return {"logged_in": True}


@pytest.mark.asyncio
async def test_api_reauth_verify_cancel_status(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(R, "_arm_sidecar_keepawake", _noop)
    monkeypatch.setattr(R, "LinkedInReauthClient", _FakeSidecarClient)

    out = await L.linkedin_reauth_verify(L.ReauthVerifyRequest(code="123456"), _user())
    assert out["logged_in"] is True
    assert (await L.linkedin_reauth_cancel(_user()))["cancelled"] is True
    assert (await L.linkedin_reauth_status(_user()))["logged_in"] is True


@pytest.mark.asyncio
async def test_api_reauth_cancel_status_errors(monkeypatch):
    async def _noop(*a, **k):
        return None

    class _ErrClient:
        def __init__(self, *a, **k):
            pass

        async def cancel(self):
            raise R.LinkedInReauthError(502, "cancel failed")

        async def status(self):
            raise R.LinkedInReauthError(503, "sidecar down")

    monkeypatch.setattr(R, "LinkedInReauthClient", _ErrClient)
    with pytest.raises(HTTPException) as ei:
        await L.linkedin_reauth_cancel(_user())
    assert ei.value.status_code == 502
    with pytest.raises(HTTPException) as ei:
        await L.linkedin_reauth_status(_user())
    assert ei.value.status_code == 503


@pytest.mark.asyncio
async def test_api_reauth_verify_error(monkeypatch):
    async def _noop(*a, **k):
        return None

    class _Boom(_FakeSidecarClient):
        async def verify(self, code):
            raise R.LinkedInReauthError(500, "sidecar down")

    monkeypatch.setattr(R, "_arm_sidecar_keepawake", _noop)
    monkeypatch.setattr(R, "LinkedInReauthClient", _Boom)
    with pytest.raises(HTTPException) as ei:
        await L.linkedin_reauth_verify(L.ReauthVerifyRequest(code="1"), _user())
    assert ei.value.status_code == 500
