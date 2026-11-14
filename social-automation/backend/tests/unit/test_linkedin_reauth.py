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
