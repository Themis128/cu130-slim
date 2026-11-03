"""CF mail-outbound worker lane — additive transport in send_email.

Gate: CF_EMAIL_ENABLED + MAIL_OUTBOUND_URL + MAIL_OUTBOUND_TOKEN. On any
worker failure the call falls through to the normal provider path, so the
lane can never break existing sends.
"""

from types import SimpleNamespace

import pytest

from app.services import email_digest as ed


def _settings(**over):
    base = dict(
        EMAIL_PROVIDER="local",
        EMAIL_SUPPRESS_ADDR_PATTERNS="",
        SMTP_FROM="noreply@cloudless.gr",
        DIGEST_EMAIL_TO="ops@example.com",
        CF_EMAIL_ENABLED=True,
        MAIL_OUTBOUND_URL="https://mail-outbound.test/send",
        MAIL_OUTBOUND_TOKEN="tok123",
    )
    base.update(over)
    return SimpleNamespace(**base)


class _Resp:
    def __init__(self, status_code=200, text="{}"):
        self.status_code = status_code
        self.text = text


class _Client:
    def __init__(self, resp=None, exc=None, calls=None):
        self._resp = resp
        self._exc = exc
        self._calls = calls if calls is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        if self._exc is not None:
            raise self._exc
        self._calls.append({"url": url, "headers": headers, "json": json})
        return self._resp


@pytest.fixture
def smtp_calls(monkeypatch):
    calls = []

    def _smtp(**kw):
        calls.append(kw)

    async def _to_thread(fn, **kw):
        return fn(**kw)

    monkeypatch.setattr(ed, "send_email_smtp", _smtp)
    monkeypatch.setattr(ed.asyncio, "to_thread", _to_thread)
    return calls


async def test_enabled_posts_to_worker(monkeypatch, smtp_calls):
    calls = []
    monkeypatch.setattr(ed, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        ed.httpx, "AsyncClient", lambda timeout=0: _Client(resp=_Resp(200), calls=calls)
    )

    await ed.send_email(subject="hi", text_body="body", to_addrs=["a@x.com"])

    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://mail-outbound.test/send"
    assert call["headers"]["Authorization"] == "Bearer tok123"
    assert call["json"]["to"] == "a@x.com"
    assert call["json"]["subject"] == "hi"
    assert call["json"]["category"] == "ops-alert"
    assert smtp_calls == []


async def test_worker_failure_falls_back(monkeypatch, smtp_calls):
    monkeypatch.setattr(ed, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        ed.httpx, "AsyncClient", lambda timeout=0: _Client(resp=_Resp(502), calls=[])
    )

    await ed.send_email(subject="hi", text_body="body", to_addrs=["a@x.com"])

    assert len(smtp_calls) == 1
    assert smtp_calls[0]["subject"] == "hi"


async def test_disabled_never_calls_worker(monkeypatch, smtp_calls):
    calls = []
    monkeypatch.setattr(ed, "get_settings", lambda: _settings(CF_EMAIL_ENABLED=False))
    monkeypatch.setattr(
        ed.httpx, "AsyncClient", lambda timeout=0: _Client(resp=_Resp(200), calls=calls)
    )

    await ed.send_email(subject="hi", text_body="body", to_addrs=["a@x.com"])

    assert calls == []
    assert len(smtp_calls) == 1


async def test_attachments_skip_worker(monkeypatch, smtp_calls):
    calls = []
    monkeypatch.setattr(ed, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        ed.httpx, "AsyncClient", lambda timeout=0: _Client(resp=_Resp(200), calls=calls)
    )

    await ed.send_email(
        subject="hi",
        text_body="body",
        to_addrs=["a@x.com"],
        attachments=[{"filename": "x.pdf", "content": "Zg=="}],
    )

    assert calls == []
    assert len(smtp_calls) == 1
