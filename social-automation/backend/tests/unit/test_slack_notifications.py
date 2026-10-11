from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import app.services.slack_notifications as sn


class _FakeResponse:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body

    @property
    def text(self) -> str:
        return str(self._body)

    def json(self):
        return self._body


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self._response


@pytest.mark.asyncio
async def test_post_digest_prefers_webhook_when_set():
    settings = SimpleNamespace(
        SLACK_WEBHOOK_URL="https://hooks.slack.test/abc",
        SLACK_BOT_TOKEN="",
        SLACK_ACCESS_TOKEN="",
        SLACK_CHANNEL_ID="C123",
    )
    fake = _FakeAsyncClient(_FakeResponse(200, "ok"))

    with patch.object(sn, "get_settings", return_value=settings), patch.object(
        sn.httpx, "AsyncClient", return_value=fake
    ):
        ok, err, ts = await sn.post_digest_text_to_slack("hello")

    assert ok is True
    assert err is None
    assert ts is None
    assert fake.calls[0]["url"] == settings.SLACK_WEBHOOK_URL
    assert fake.calls[0]["json"] == {"text": "hello"}


@pytest.mark.asyncio
async def test_post_digest_falls_back_to_token_and_channel():
    settings = SimpleNamespace(
        SLACK_WEBHOOK_URL="",
        SLACK_BOT_TOKEN="xoxb-test",
        SLACK_ACCESS_TOKEN="",
        SLACK_CHANNEL_ID="C999",
    )
    fake = _FakeAsyncClient(_FakeResponse(200, {"ok": True, "ts": "123.456"}))

    with patch.object(sn, "get_settings", return_value=settings), patch.object(
        sn.httpx, "AsyncClient", return_value=fake
    ):
        ok, err, ts = await sn.post_digest_text_to_slack("digest")

    assert ok is True
    assert err is None
    assert ts is not None
    assert fake.calls[0]["url"] == "https://api.slack.com/api/chat.postMessage"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer xoxb-test"
    assert fake.calls[0]["json"]["channel"] == "C999"
    assert fake.calls[0]["json"]["text"] == "digest"


@pytest.mark.asyncio
async def test_post_alert_is_non_fatal_when_not_configured():
    settings = SimpleNamespace(
        SLACK_ALERTS_WEBHOOK_URL="",
        SLACK_ALERTS_CHANNEL_ID="",
        SLACK_BOT_TOKEN="",
        SLACK_ACCESS_TOKEN="",
    )
    with patch.object(sn, "get_settings", return_value=settings):
        # Should not raise.
        await sn.post_alert_to_slack("alert text")


@pytest.mark.asyncio
async def test_post_alert_uses_alerts_webhook():
    settings = SimpleNamespace(
        SLACK_ALERTS_WEBHOOK_URL="https://hooks.slack.test/alerts",
        SLACK_ALERTS_CHANNEL_ID="",
        SLACK_BOT_TOKEN="",
        SLACK_ACCESS_TOKEN="",
    )
    fake = _FakeAsyncClient(_FakeResponse(200, "ok"))

    with patch.object(sn, "get_settings", return_value=settings), patch.object(
        sn.httpx, "AsyncClient", return_value=fake
    ):
        await sn.post_alert_to_slack("boom")

    assert fake.calls[0]["url"] == settings.SLACK_ALERTS_WEBHOOK_URL
    assert fake.calls[0]["json"] == {"text": "boom"}



# ---------- files.getUploadURLExternal flow ----------


class _UploadFakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None, data=None, files=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "data": data, "files": files})
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_upload_file_no_token_fails_soft():
    with patch.object(sn, "_get_slack_token", return_value=""):
        ok, fid, err = await sn.upload_file_to_slack(
            content=b"data", filename="a.png", channel_id="C1",
        )
    assert not ok and fid is None and "TOKEN" in err


@pytest.mark.asyncio
async def test_upload_file_empty_content_fails_soft():
    with patch.object(sn, "_get_slack_token", return_value="xoxb-t"):
        ok, fid, err = await sn.upload_file_to_slack(
            content=b"", filename="a.png", channel_id="C1",
        )
    assert not ok and "empty" in err


@pytest.mark.asyncio
async def test_upload_file_full_flow():
    fake = _UploadFakeClient([
        _FakeResponse(200, {"ok": True, "upload_url": "https://up.example/u", "file_id": "F123"}),
        _FakeResponse(200, {}),  # raw PUT to upload_url
        _FakeResponse(200, {"ok": True}),
    ])
    with (
        patch.object(sn, "_get_slack_token", return_value="xoxb-t"),
        patch("app.services.slack_notifications.httpx.AsyncClient", return_value=fake),
    ):
        ok, fid, err = await sn.upload_file_to_slack(
            content=b"png-bytes", filename="digest.png", channel_id="C1",
            title="Daily", initial_comment="hi", thread_ts="1.2",
        )

    assert ok and fid == "F123" and err is None
    urls = [c["url"] for c in fake.calls]
    assert urls[0].endswith("files.getUploadURLExternal")
    assert urls[1] == "https://up.example/u"
    assert urls[2].endswith("files.completeUploadExternal")
    assert fake.calls[0]["data"]["length"] == str(len(b"png-bytes"))
    assert fake.calls[2]["json"]["channel_id"] == "C1"
    assert fake.calls[2]["json"]["thread_ts"] == "1.2"


@pytest.mark.asyncio
async def test_upload_file_step1_error():
    fake = _UploadFakeClient([_FakeResponse(200, {"ok": False, "error": "invalid_auth"})])
    with (
        patch.object(sn, "_get_slack_token", return_value="xoxb-t"),
        patch("app.services.slack_notifications.httpx.AsyncClient", return_value=fake),
    ):
        ok, fid, err = await sn.upload_file_to_slack(
            content=b"x", filename="a.png", channel_id="C1",
        )
    assert not ok and "invalid_auth" in err


# ── _post_slack_text branches ────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_slack_text_missing_config():
    ok, err, ts = await sn._post_slack_text(
        text="t", webhook_url="", token="", channel_id="", purpose="p")
    assert not ok and "isn’t configured" in err

    ok, err, ts = await sn._post_slack_text(
        text="t", webhook_url="", token="tok", channel_id="", purpose="p")
    assert not ok and "channel id" in err


@pytest.mark.asyncio
async def test_post_slack_text_webhook_ok_with_blocks_and_channel():
    fake = _FakeAsyncClient(_FakeResponse(200, "ok"))
    with patch.object(sn.httpx, "AsyncClient", return_value=fake):
        ok, err, ts = await sn._post_slack_text(
            text="t", webhook_url="https://hooks/x", token="",
            channel_id="#chan", purpose="p", blocks=[{"type": "section"}])
    assert ok
    payload = fake.calls[0]["json"]
    assert payload["blocks"] == [{"type": "section"}]
    assert payload["channel"] == "#chan"


@pytest.mark.asyncio
async def test_post_slack_text_token_path_ok_and_api_error():
    ok_resp = _FakeAsyncClient(_FakeResponse(200, {"ok": True, "ts": "1.9"}))
    with patch.object(sn.httpx, "AsyncClient", return_value=ok_resp):
        ok, err, ts = await sn._post_slack_text(
            text="t", webhook_url="", token="tok", channel_id="C1", purpose="p")
    assert ok and ts == "1.9"
    assert ok_resp.calls[0]["url"].endswith("chat.postMessage")

    err_resp = _FakeAsyncClient(
        _FakeResponse(200, {"ok": False, "error": "missing_scope", "needed": "chat:write"}))
    with patch.object(sn.httpx, "AsyncClient", return_value=err_resp):
        ok, err, ts = await sn._post_slack_text(
            text="t", webhook_url="", token="tok", channel_id="C1", purpose="p")
    assert not ok and "missing_scope" in err and "chat:write" in err

    err_resp2 = _FakeAsyncClient(_FakeResponse(200, {"ok": False}))
    with patch.object(sn.httpx, "AsyncClient", return_value=err_resp2):
        ok, err, _ = await sn._post_slack_text(
            text="t", webhook_url="", token="tok", channel_id="C1", purpose="p")
    assert not ok and "unknown" in err


@pytest.mark.asyncio
async def test_post_slack_text_webhook_4xx_fallback_to_token():
    # webhook 404 -> falls back to bot token when configured
    class _Both(_FakeAsyncClient):
        def __init__(self):
            super().__init__(None)
            self.n = 0

        async def post(self, url, headers=None, json=None):
            self.calls.append({"url": url, "headers": headers, "json": json})
            self.n += 1
            return _FakeResponse(404, "no_service") if self.n == 1 else _FakeResponse(
                200, {"ok": True, "ts": "2.2"})

    fake = _Both()
    with patch.object(sn.httpx, "AsyncClient", return_value=fake):
        ok, err, ts = await sn._post_slack_text(
            text="t", webhook_url="https://hooks/dead", token="tok",
            channel_id="C1", purpose="p")
    assert ok and ts == "2.2"

    # webhook 400 with no token/channel -> returns error immediately
    fake2 = _FakeAsyncClient(_FakeResponse(400, "bad"))
    with patch.object(sn.httpx, "AsyncClient", return_value=fake2):
        ok, err, _ = await sn._post_slack_text(
            text="t", webhook_url="https://hooks/x", token="",
            channel_id="", purpose="p")
    assert not ok and "HTTP 400" in err


@pytest.mark.asyncio
async def test_post_slack_text_webhook_5xx_retries_then_fails():
    fake = _FakeAsyncClient(_FakeResponse(500, "oops"))
    with (
        patch.object(sn.httpx, "AsyncClient", return_value=fake),
        patch.object(sn.asyncio, "sleep", AsyncMock()),
    ):
        ok, err, _ = await sn._post_slack_text(
            text="t", webhook_url="https://hooks/x", token="",
            channel_id="", purpose="p")
    # 5xx does not hit the <500 fallback branch — loop just returns last_err
    assert not ok and "HTTP 500" in err


@pytest.mark.asyncio
async def test_post_slack_text_connect_errors_retry_and_fallback():
    import httpx as _hx

    class _Flaky(_FakeAsyncClient):
        def __init__(self, calls_before_ok):
            super().__init__(None)
            self.n = 0
            self._ok_after = calls_before_ok

        async def post(self, url, headers=None, json=None):
            self.calls.append({"url": url})
            self.n += 1
            if self.n <= self._ok_after:
                raise _hx.ConnectError("conn refused")
            return _FakeResponse(200, {"ok": True, "ts": "3.3"})

    # retries then gives up on attempt 4 -> falls back to token
    fake = _Flaky(99)
    with (
        patch.object(sn.httpx, "AsyncClient", return_value=fake),
        patch.object(sn.asyncio, "sleep", AsyncMock()),
    ):
        ok, err, _ = await sn._post_slack_text(
            text="t", webhook_url="https://hooks/x", token="tok",
            channel_id="C1", purpose="p")
    # after 4 webhook failures, the token attempt also fails ConnectError
    # inside _via_token which raises -> caught by outer except? No —
    # _via_token raise propagates inside the retry loop's try -> treated
    # as attempt failure... but attempts are exhausted, so falls into
    # fallback call which also raises ConnectError -> outer except.
    assert not ok

    # retry succeeds on attempt 2 via webhook path
    fake2 = _Flaky(1)
    fake2.resp = _FakeResponse(200, "ok")
    with (
        patch.object(sn.httpx, "AsyncClient", return_value=fake2),
        patch.object(sn.asyncio, "sleep", AsyncMock()),
    ):
        ok, err, _ = await sn._post_slack_text(
            text="t", webhook_url="https://hooks/x", token="",
            channel_id="", purpose="p")
    assert ok


@pytest.mark.asyncio
async def test_post_slack_text_outer_exception():
    class _Boom(_FakeAsyncClient):
        async def post(self, url, headers=None, json=None):
            raise ValueError("weird")

    with patch.object(sn.httpx, "AsyncClient", return_value=_Boom(None)):
        ok, err, _ = await sn._post_slack_text(
            text="t", webhook_url="https://hooks/x", token="",
            channel_id="", purpose="p")
    assert not ok and "weird" in err


# ── alert / digest / sender wrappers ─────────────────────────────────


@pytest.mark.asyncio
async def test_post_alert_to_slack():
    called = {}

    async def fake_post(**kw):
        called.update(kw)
        return True, None, None

    settings = SimpleNamespace(
        SLACK_ALERTS_WEBHOOK_URL="https://hooks/alerts",
        SLACK_ALERTS_CHANNEL_ID="C1",
        SLACK_BOT_TOKEN="", SLACK_ACCESS_TOKEN="",
    )
    with (
        patch.object(sn, "get_settings", return_value=settings),
        patch.object(sn, "_post_slack_text", fake_post),
    ):
        await sn.post_alert_to_slack("alert!", blocks=[{"b": 1}])
    assert called["purpose"] == "alerts" and called["blocks"] == [{"b": 1}]

    # failure path — only logs
    async def fake_fail(**kw):
        return False, "nope", None

    with (
        patch.object(sn, "get_settings", return_value=settings),
        patch.object(sn, "_post_slack_text", fake_fail),
    ):
        await sn.post_alert_to_slack("alert!")


@pytest.mark.asyncio
async def test_post_publishing_to_slack():
    settings = SimpleNamespace(SLACK_PUBLISHING_WEBHOOK_URL="")
    with patch.object(sn, "get_settings", return_value=settings):
        await sn.post_publishing_to_slack("t")  # early return

    called = {}

    async def fake_post(**kw):
        called.update(kw)
        return False, "err", None

    settings2 = SimpleNamespace(SLACK_PUBLISHING_WEBHOOK_URL="https://hooks/pub")
    with (
        patch.object(sn, "get_settings", return_value=settings2),
        patch.object(sn, "_post_slack_text", fake_post),
    ):
        await sn.post_publishing_to_slack("t")
    assert called["purpose"] == "publishing"


@pytest.mark.asyncio
async def test_billing_and_paddle_and_support_senders():
    settings = SimpleNamespace(
        SLACK_BILLING_WEBHOOK_URL="", SLACK_PADDLE_WEBHOOK_URL="",
        SLACK_BILLING_CHANNEL_ID="", SLACK_PADDLE_CHANNEL_ID="",
        SLACK_BOT_TOKEN="", SLACK_ACCESS_TOKEN="",
        SLACK_SUPPORT_WEBHOOK_URL="", SLACK_SUPPORT_CHANNEL_ID="",
    )
    with patch.object(sn, "get_settings", return_value=settings):
        ok, err = await sn.post_billing_digest_to_slack("t")
        assert not ok and "not configured" in err
        ok, err = await sn.post_support_report_to_slack("t")
        assert not ok and "not configured" in err

    settings2 = SimpleNamespace(
        SLACK_BILLING_WEBHOOK_URL="https://hooks/bill",
        SLACK_PADDLE_WEBHOOK_URL="", SLACK_BILLING_CHANNEL_ID="",
        SLACK_PADDLE_CHANNEL_ID="C9", SLACK_BOT_TOKEN="t",
        SLACK_ACCESS_TOKEN="",
        SLACK_SUPPORT_WEBHOOK_URL="https://hooks/sup",
        SLACK_SUPPORT_CHANNEL_ID="",
    )
    calls = []

    async def fake_post(**kw):
        calls.append(kw["purpose"])
        return True, None, "1.1"

    with (
        patch.object(sn, "get_settings", return_value=settings2),
        patch.object(sn, "_post_slack_text", fake_post),
    ):
        ok, err = await sn.post_billing_digest_to_slack("t")
        assert ok and err is None
        ok, err = await sn.post_paddle_digest_to_slack("t")
        assert ok
        ok, err = await sn.post_support_report_to_slack("t")
        assert ok
    assert calls == ["billing-digest", "billing-digest", "support"]


@pytest.mark.asyncio
async def test_post_thread_reply():
    # no token -> early return
    with patch.object(sn, "_get_slack_token", return_value=""):
        await sn.post_thread_reply(channel_id="C", thread_ts="1", text="t")
    # blank channel/ts -> early return
    with patch.object(sn, "_get_slack_token", return_value="tok"):
        await sn.post_thread_reply(channel_id="", thread_ts="1", text="t")

    ok_resp = _FakeAsyncClient(_FakeResponse(200, {"ok": True}))
    with (
        patch.object(sn, "_get_slack_token", return_value="tok"),
        patch.object(sn.httpx, "AsyncClient", return_value=ok_resp),
    ):
        await sn.post_thread_reply(channel_id="C", thread_ts="1.1", text="t")
    assert ok_resp.calls[0]["json"]["thread_ts"] == "1.1"

    err_resp = _FakeAsyncClient(_FakeResponse(200, {"ok": False, "error": "e"}))
    with (
        patch.object(sn, "_get_slack_token", return_value="tok"),
        patch.object(sn.httpx, "AsyncClient", return_value=err_resp),
    ):
        await sn.post_thread_reply(channel_id="C", thread_ts="1", text="t")

    class _Boom(_FakeAsyncClient):
        async def post(self, url, headers=None, json=None):
            raise RuntimeError("x")

    with (
        patch.object(sn, "_get_slack_token", return_value="tok"),
        patch.object(sn.httpx, "AsyncClient", return_value=_Boom(None)),
    ):
        await sn.post_thread_reply(channel_id="C", thread_ts="1", text="t")


# ── block-kit buttons ────────────────────────────────────────────────


def test_button_variants():
    b = sn._button("T", "act")
    assert b["action_id"] == "act" and b["value"] == "act"
    b2 = sn._button("T", "act", value="v", style="danger", confirm="sure?")
    assert b2["style"] == "danger" and b2["confirm"]["text"]["text"] == "sure?"
    b3 = sn._button("T", "act", url="https://x")
    assert b3["url"] == "https://x" and "value" not in b3


def test_session_heal_buttons():
    blocks = sn.session_heal_buttons()
    actions = blocks[0]["elements"]
    ids = [a["action_id"] for a in actions]
    assert "socialauto_session_heal" in ids and "socialauto_ops_status" in ids
    assert any(a.get("url", "").endswith("/admin") for a in actions)


def test_publish_failure_buttons():
    blocks = sn.publish_failure_buttons("q1")
    ids = [a["action_id"] for a in blocks[0]["elements"]]
    assert "socialauto_retry_queue" in ids and "socialauto_cancel_queue" in ids
    cancel = next(a for a in blocks[0]["elements"] if a["action_id"] == "socialauto_cancel_queue")
    assert cancel["style"] == "danger" and "confirm" in cancel

    blocks2 = sn.publish_failure_buttons("unknown")
    ids2 = [a["action_id"] for a in blocks2[0]["elements"]]
    assert "socialauto_retry_queue" not in ids2
    assert "open_socialauto_queue" in ids2


@pytest.mark.asyncio
async def test_upload_file_step2_and_step3_errors():
    responses = [
        _FakeResponse(200, {"ok": True, "upload_url": "https://up/u", "file_id": "F1"}),
        _FakeResponse(500, {}),  # PUT fails
    ]
    fake = _UploadFakeClient(responses)
    with (
        patch.object(sn, "_get_slack_token", return_value="t"),
        patch.object(sn.httpx, "AsyncClient", return_value=fake),
    ):
        ok, fid, err = await sn.upload_file_to_slack(
            content=b"x", filename="f", channel_id="C")
        assert not ok and "HTTP 500" in err

    responses2 = [
        _FakeResponse(200, {"ok": True, "upload_url": "https://up/u", "file_id": "F1"}),
        _FakeResponse(200, {}),  # PUT ok
        _FakeResponse(200, {"ok": False, "error": "bad_channel"}),
    ]
    fake2 = _UploadFakeClient(responses2)
    with (
        patch.object(sn, "_get_slack_token", return_value="t"),
        patch.object(sn.httpx, "AsyncClient", return_value=fake2),
    ):
        ok, fid, err = await sn.upload_file_to_slack(
            content=b"x", filename="f", channel_id="C")
        assert not ok and "bad_channel" in err


@pytest.mark.asyncio
async def test_upload_file_no_token_and_empty_content():
    with patch.object(sn, "_get_slack_token", return_value=""):
        ok, fid, err = await sn.upload_file_to_slack(
            content=b"x", filename="f", channel_id="C")
        assert not ok and "needs SLACK_BOT_TOKEN" in err
    with patch.object(sn, "_get_slack_token", return_value="t"):
        ok, fid, err = await sn.upload_file_to_slack(
            content=b"", filename="f", channel_id="C")
        assert not ok and "empty" in err


@pytest.mark.asyncio
async def test_upload_file_exception_soft():
    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            raise RuntimeError("net down")

    with (
        patch.object(sn, "_get_slack_token", return_value="t"),
        patch.object(sn.httpx, "AsyncClient", return_value=_Boom()),
    ):
        ok, fid, err = await sn.upload_file_to_slack(
            content=b"x", filename="f", channel_id="C")
        assert not ok and "net down" in err


@pytest.mark.asyncio
async def test_post_slack_text_connect_error_no_fallback():
    import httpx as _hx

    class _Down(_FakeAsyncClient):
        async def post(self, url, headers=None, json=None):
            raise _hx.ConnectError("refused")

    with (
        patch.object(sn.httpx, "AsyncClient", return_value=_Down(None)),
        patch.object(sn.asyncio, "sleep", AsyncMock()),
    ):
        # webhook unreachable, no token/channel for fallback -> last_err
        ok, err, _ = await sn._post_slack_text(
            text="t", webhook_url="https://hooks/x", token="",
            channel_id="", purpose="p")
        assert not ok and "ConnectError" in err


@pytest.mark.asyncio
async def test_billing_and_support_failure_warning_paths():
    settings = SimpleNamespace(
        SLACK_BILLING_WEBHOOK_URL="https://hooks/b",
        SLACK_PADDLE_WEBHOOK_URL="", SLACK_BILLING_CHANNEL_ID="",
        SLACK_PADDLE_CHANNEL_ID="", SLACK_BOT_TOKEN="",
        SLACK_ACCESS_TOKEN="",
        SLACK_SUPPORT_WEBHOOK_URL="https://hooks/s",
        SLACK_SUPPORT_CHANNEL_ID="",
    )

    async def fake_fail(**kw):
        return False, "delivery failed", None

    with (
        patch.object(sn, "get_settings", return_value=settings),
        patch.object(sn, "_post_slack_text", fake_fail),
    ):
        ok, err = await sn.post_billing_digest_to_slack("t")
        assert not ok and err == "delivery failed"
        ok, err = await sn.post_support_report_to_slack("t")
        assert not ok and err == "delivery failed"
