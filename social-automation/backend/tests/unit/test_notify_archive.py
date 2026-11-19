"""Unit tests for the notification-archive collector + incident classifier.

Covers the Slack/ntfy surfaces landed in the all-channels archive work:
id encoding (bigint-safe), channel discovery, and _classify target rules.
"""

import asyncio
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.worker.tasks import notify_archive as NA
from app.worker.tasks.telegram_incidents import _classify

SIGNED_BIGINT_MAX = 0x7FFFFFFFFFFFFFFF


# ---- id encodings -----------------------------------------------------


def test_slack_msg_id_microseconds():
    assert NA._slack_msg_id("1795087811.752809") == 1795087811752809


def test_slack_msg_id_pads_short_fraction():
    assert NA._slack_msg_id("1795087811.75") == 1795087811750000


def test_slack_msg_id_fits_signed_bigint():
    for ts in ("9999999999.999999", "1795087811.752809", "1.1"):
        assert 0 <= NA._slack_msg_id(ts) <= SIGNED_BIGINT_MAX


def test_ntfy_msg_id_is_stable_and_signed():
    a = NA._ntfy_msg_id("rlRnVLI9G6hi", 1700000000)
    b = NA._ntfy_msg_id("rlRnVLI9G6hi", 1700000000)
    assert a == b
    assert 0 <= a <= SIGNED_BIGINT_MAX


def test_ntfy_msg_id_differs_per_message_same_second():
    a = NA._ntfy_msg_id("rlRnVLI9G6hi", 1700000000)
    b = NA._ntfy_msg_id("XwA53rVRYaZ4", 1700000000)
    assert a != b  # same-second messages must not collide


def test_ntfy_msg_id_falls_back_to_time():
    assert NA._ntfy_msg_id("", 1700000000) == 1700000000


# ---- slack channel discovery ------------------------------------------


def test_slack_channel_ids_explicit_override():
    with patch.dict(os.environ, {"SLACK_ARCHIVE_CHANNELS": "C111,C222"}, clear=False):
        assert NA._slack_channel_ids() == ["C111", "C222"]


def test_slack_channel_ids_env_defaults_dedupe():
    env = {
        "SLACK_ARCHIVE_CHANNELS": "",
        "SLACK_ALERTS_CHANNEL_ID": "C0AAA",
        "SLACK_OPS_CHANNEL": "C0BBB",
        "SLACK_CHANNEL_ID": "C0AAA",  # dup of alerts
        "SLACK_TELEGRAM_CHANNEL_ID": "C0CCC",
    }
    with patch.dict(os.environ, env, clear=False):
        ids = NA._slack_channel_ids()
    assert ids == ["C0AAA", "C0BBB", "C0CCC"]


def test_slack_channel_ids_ignores_non_ids():
    with patch.dict(os.environ, {"SLACK_OPS_CHANNEL": "#ops-alerts", "SLACK_CHANNEL_ID": ""}, clear=False):
        assert NA._slack_channel_ids() == []


# ---- incident classifier ----------------------------------------------


def test_classify_container_name_target():
    kind, target = _classify("ops", "social-api unhealthy - restart loop detected")
    assert kind == "container_unhealthy"
    assert target == "social-api"


def test_classify_backtick_task_target():
    kind, target = _classify(
        "ntfy:cloudless-alerts",
        "[ops] cloudless\n:warning: Worker task failed `app.worker.tasks.notify_archive.notify_archive_ingest`",
    )
    assert kind == "infra_alert"
    assert target == "notify_archive.notify_archive_ingest"


def test_classify_emoji_shortcode_not_target():
    kind, target = _classify("slack:ops-alerts", ":warning: Worker task failed `app.worker.tasks.log_intel.digest`")
    assert target == "log_intel.digest"  # not ":warning:"


def test_classify_severity_prefix_stripped():
    kind, target = _classify("ntfy:nextcloud-ops", "[critical] nextcloud cron failed on omv")
    assert kind == "infra_alert"
    assert target == "nextcloud"  # not "[critical]" nor "cloudless"


def test_classify_generic_tokens_skipped():
    kind, target = _classify("ntfy:cloudless-ops", "cloudless disk pressure on omv - 92% full")
    assert kind == "disk_pressure"
    assert target != "cloudless"  # generic word skipped for a real token


def test_classify_non_incident_ignored():
    assert _classify("ntfy:cloudless-ops", "canary ok - cloudless-ops") is None
    assert _classify("ops", "all services recovered, healthy again") is None
    assert _classify("ops", "") is None


def test_classify_kind_mapping():
    assert _classify("ops", "disk full on omv")[0] == "disk_pressure"
    assert _classify("ops", "worker OOM killed")[0] == "oom_kill"
    assert _classify("ops", "tunnel returns 530")[0] == "tunnel_down"
    assert _classify("ops", "token expired for meta")[0] == "credential_expired"


# ---- gmail id + noise filters -----------------------------------------


def test_gmail_msg_id_signed_bigint_and_stable():
    a = NA._gmail_msg_id("19abc123def", 1700000000000)
    assert a == NA._gmail_msg_id("19abc123def", 1700000000000)
    assert 0 <= a <= SIGNED_BIGINT_MAX
    assert NA._gmail_msg_id("", 1700000000000) == 1700000000000


def test_classify_skips_canary_and_digests():
    from app.worker.tasks.telegram_incidents import _classify as c

    assert c("ntfy:cloudless-ops", "canary ok - cloudless-ops\nself-check ok") is None
    assert c("ntfy:cloudless-ops", "[digest] cloudless\n*CRM engagement sweep*") is None
    assert c("ntfy:cloudless-ops", "[intel] trend digest") is None


def test_classify_skips_reports():
    from app.worker.tasks.telegram_incidents import _classify as c

    assert c("email:gmail", "New jobs posted from careers.eurobank.gr") is None
    assert c("email:gmail", "SocialAuto daily report - failed tasks below") is None


def test_email_alert_sender_regex():
    from app.worker.tasks.telegram_incidents import _EMAIL_ALERT_RE as R

    assert R.search("Vercel <notifications@vercel.com>")
    assert R.search("noreply@github.com")
    assert R.search("alerts@cloudless.gr")
    assert not R.search("Medium Daily Digest <noreply@medium.com>")
    assert not R.search("careers@eurobank.gr")


# ---- collector internals (coverage of the fetch/insert paths) ----------


class _Resp:
    def __init__(self, json_data=None, text="", status=200):
        self._json = json_data
        self.text = text
        self.status_code = status

    def json(self):
        if isinstance(self._json, Exception):
            raise self._json
        return self._json


class _FakeClient:
    """Sequential-response httpx.AsyncClient stand-in."""

    responses: list = []

    def __init__(self, *a, **kw):
        self.calls = []
        self._r = list(_FakeClient.responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _next(self, method, url, **kw):
        self.calls.append((method, url, kw))
        item = self._r.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def get(self, url, **kw):
        return await self._next("GET", url, **kw)

    async def post(self, url, **kw):
        return await self._next("POST", url, **kw)


class _Res:
    def __init__(self, rowcount=1, scalar=None, all_=None):
        self.rowcount = rowcount
        self._scalar = scalar
        self._all = all_ if all_ is not None else []

    def scalar(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._all


class _FakeDB:
    def __init__(self, results=None, rowcount=1):
        self.results = list(results or [])
        self.rowcount = rowcount
        self.committed = 0

    async def execute(self, stmt):
        if self.results:
            return self.results.pop(0)
        return _Res(rowcount=self.rowcount)

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        pass


class _FakeRedis:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.writes = {}

    async def hget(self, key, field):
        return self.values.get(field)

    async def hset(self, key, field, val):
        self.writes[field] = val

    async def aclose(self):
        pass


def _stats():
    return {"scanned": 0, "inserted": 0, "dupes": 0}


@pytest.mark.asyncio
async def test_insert_msg_dedupe(monkeypatch):
    from app.models.telegram_message import TelegramChannelMessage

    db = _FakeDB(rowcount=1)
    ok = await NA._insert_msg(db, TelegramChannelMessage, "t1", "c", 1, datetime.now(UTC), "s", "x", "")
    assert ok is True
    db2 = _FakeDB(rowcount=0)
    ok = await NA._insert_msg(db2, TelegramChannelMessage, "t1", "c", 1, datetime.now(UTC), "s", "x", "")
    assert ok is False


@pytest.mark.asyncio
async def test_collect_slack_no_token(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    st = _stats()
    await NA._collect_slack(_FakeDB(), _FakeRedis(), None, st, "t1")
    assert st["slack"] == "no SLACK_BOT_TOKEN"


@pytest.mark.asyncio
async def test_collect_slack_no_channels(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "x")
    monkeypatch.setenv("SLACK_ARCHIVE_CHANNELS", "")
    for e in ("SLACK_ALERTS_CHANNEL_ID", "SLACK_OPS_CHANNEL", "SLACK_CHANNEL_ID", "SLACK_TELEGRAM_CHANNEL_ID"):
        monkeypatch.delenv(e, raising=False)
    st = _stats()
    await NA._collect_slack(_FakeDB(), _FakeRedis(), None, st, "t1")
    assert st["slack"] == "no channels"


@pytest.mark.asyncio
async def test_collect_slack_happy_path(monkeypatch):
    from app.models.telegram_message import TelegramChannelMessage as M

    monkeypatch.setenv("SLACK_BOT_TOKEN", "x")
    monkeypatch.setenv("SLACK_ARCHIVE_CHANNELS", "C1")
    _FakeClient.responses = [
        _Resp({"ok": True, "channel": {"name": "ops-alerts"}}),
        _Resp(
            {
                "ok": True,
                "messages": [
                    {"ts": "1795087811.752809", "text": "alert", "user": "U1"},
                    {"ts": "1795087812.000001", "text": "", "user": "U2"},  # skipped
                    {"ts": "1795087813.5", "text": "m2", "bot_id": "B1"},
                ],
                "response_metadata": {"next_cursor": "n1"},
            }
        ),
        _Resp({"ok": True, "messages": []}),  # end page
    ]
    monkeypatch.setattr(NA.httpx, "AsyncClient", _FakeClient)
    st = _stats()
    await NA._collect_slack(_FakeDB(), _FakeRedis(), M, st, "t1")
    assert st["scanned"] == 3 and st["inserted"] == 2
    assert st["pending"]["slack:ops-alerts"] == "1795087813.5"


@pytest.mark.asyncio
async def test_collect_slack_api_error(monkeypatch):
    from app.models.telegram_message import TelegramChannelMessage as M

    monkeypatch.setenv("SLACK_BOT_TOKEN", "x")
    monkeypatch.setenv("SLACK_ARCHIVE_CHANNELS", "C1")
    _FakeClient.responses = [
        _Resp({"ok": True, "channel": {"name": "c"}}),
        _Resp({"ok": False, "error": "ratelimited"}),
    ]
    monkeypatch.setattr(NA.httpx, "AsyncClient", _FakeClient)
    st = _stats()
    await NA._collect_slack(_FakeDB(), _FakeRedis(), M, st, "t1")
    assert st["errors"] == ["slack:c: ratelimited"]


@pytest.mark.asyncio
async def test_collect_slack_request_error_and_name_fallback(monkeypatch):
    from app.models.telegram_message import TelegramChannelMessage as M

    monkeypatch.setenv("SLACK_BOT_TOKEN", "x")
    monkeypatch.setenv("SLACK_ARCHIVE_CHANNELS", "C1")
    _FakeClient.responses = [
        RuntimeError("info fail"),  # conversations.info raises
        ConnectionError("history fail"),  # history raises
    ]
    monkeypatch.setattr(NA.httpx, "AsyncClient", _FakeClient)
    st = _stats()
    await NA._collect_slack(_FakeDB(), _FakeRedis(), M, st, "t1")
    assert st["errors"] == ["slack:C1: request"]


@pytest.mark.asyncio
async def test_collect_ntfy_no_token(monkeypatch):
    monkeypatch.delenv("NTFY_READER_TOKEN", raising=False)
    st = _stats()
    await NA._collect_ntfy(_FakeDB(), _FakeRedis(), None, st, "t1")
    assert st["ntfy"] == "no NTFY_READER_TOKEN"


@pytest.mark.asyncio
async def test_collect_ntfy_happy_and_noise_lines(monkeypatch):
    from app.models.telegram_message import TelegramChannelMessage as M

    monkeypatch.setenv("NTFY_READER_TOKEN", "tk_x")
    monkeypatch.setenv("NTFY_ARCHIVE_TOPICS", "ops-t")
    body = "\n".join(
        [
            '{"event":"message","id":"rlRnVLI9G6hi","time":1700000100,"title":"t","message":"m"}',
            "not json",
            '{"event":"keepalive","time":1700000101}',
            '{"event":"message","id":"XwA53rVRYaZ4","time":1700000102,"message":"m2","attachment":{"url":"u"}}',
            "",
        ]
    )
    _FakeClient.responses = [_Resp(text=body)]
    monkeypatch.setattr(NA.httpx, "AsyncClient", _FakeClient)
    st = _stats()
    await NA._collect_ntfy(_FakeDB(), _FakeRedis({"ntfy:ops-t": "1700000000"}), M, st, "t1")
    assert st["scanned"] == 2 and st["inserted"] == 2
    assert st["pending"]["ntfy:ops-t"] == "1700000102"


@pytest.mark.asyncio
async def test_collect_ntfy_http_error(monkeypatch):
    from app.models.telegram_message import TelegramChannelMessage as M

    monkeypatch.setenv("NTFY_READER_TOKEN", "tk_x")
    monkeypatch.setenv("NTFY_ARCHIVE_TOPICS", "ops-t")
    _FakeClient.responses = [_Resp(status=403)]
    monkeypatch.setattr(NA.httpx, "AsyncClient", _FakeClient)
    st = _stats()
    await NA._collect_ntfy(_FakeDB(), _FakeRedis(), M, st, "t1")
    assert st["errors"] == ["ntfy:ops-t: http 403"]


@pytest.mark.asyncio
async def test_collect_ntfy_request_error(monkeypatch):
    from app.models.telegram_message import TelegramChannelMessage as M

    monkeypatch.setenv("NTFY_READER_TOKEN", "tk_x")
    monkeypatch.setenv("NTFY_ARCHIVE_TOPICS", "ops-t")
    _FakeClient.responses = [TimeoutError("x")]
    monkeypatch.setattr(NA.httpx, "AsyncClient", _FakeClient)
    st = _stats()
    await NA._collect_ntfy(_FakeDB(), _FakeRedis(), M, st, "t1")
    assert st["errors"] == ["ntfy:ops-t: request"]


@pytest.mark.asyncio
async def test_collect_email_no_token(monkeypatch):
    monkeypatch.delenv("GMAIL_REFRESH_TOKEN", raising=False)
    st = _stats()
    await NA._collect_email(_FakeDB(), _FakeRedis(), None, st, "t1")
    assert st["email"] == "no GMAIL_REFRESH_TOKEN"


@pytest.mark.asyncio
async def test_collect_email_token_fail(monkeypatch):
    from app.models.telegram_message import TelegramChannelMessage as M

    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "r")
    _FakeClient.responses = [RuntimeError("oauth down")]
    monkeypatch.setattr(NA.httpx, "AsyncClient", _FakeClient)
    st = _stats()
    await NA._collect_email(_FakeDB(), _FakeRedis(), M, st, "t1")
    assert st["email"] == "token refresh failed"


@pytest.mark.asyncio
async def test_collect_email_full_flow(monkeypatch):
    from app.models.telegram_message import TelegramChannelMessage as M

    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "r")
    monkeypatch.setenv("EMAIL_ARCHIVE_MAILBOX", "gmail")
    _FakeClient.responses = [
        _Resp({"access_token": "AT"}),  # oauth
        _Resp({"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": "p2"}),  # list p1
        _Resp(
            {
                "internalDate": "1700000500000",
                "snippet": "snip",
                "payload": {"headers": [{"name": "From", "value": "a@b"}, {"name": "Subject", "value": "subj"}]},
            }
        ),  # get m1
        RuntimeError("get fail"),  # get m2 fails
        _Resp({"messages": [{"id": "m3"}]}),  # list p2
        _Resp({"internalDate": "1700000400000", "snippet": "s"}),  # get m3
    ]
    monkeypatch.setattr(NA.httpx, "AsyncClient", _FakeClient)
    st = _stats()
    r = _FakeRedis({"email:gmail": "1700000300000"})
    await NA._collect_email(_FakeDB(), r, M, st, "t1")
    assert st["scanned"] == 3 and st["inserted"] == 2
    assert st["pending"]["email:gmail"] == "1700000500000"


@pytest.mark.asyncio
async def test_collect_email_list_error(monkeypatch):
    from app.models.telegram_message import TelegramChannelMessage as M

    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "r")
    _FakeClient.responses = [
        _Resp({"access_token": "AT"}),
        ValueError("bad json"),
    ]
    monkeypatch.setattr(NA.httpx, "AsyncClient", _FakeClient)
    st = _stats()
    await NA._collect_email(_FakeDB(), _FakeRedis(), M, st, "t1")
    assert st["errors"] == ["email:gmail: list"]


# ---- telegram_incidents._run ------------------------------------------


def _msg(channel, text, sender="", ms=1700001000000, mid=1):
    return SimpleNamespace(channel=channel, text=text, sender=sender, msg_id=mid, created_at=datetime.fromtimestamp(ms / 1000, UTC))


def test_incidents_ingest_loop(monkeypatch):
    from app.worker.tasks import telegram_incidents as TI

    db = _FakeDB(
        results=[
            _Res(all_=[("critical",), ("slack:ops-alerts",), ("email:gmail",), ("intel",)]),
            # channels are queried in sorted order: critical, email:gmail,
            # ops, slack:ops-alerts (intel is in the skip set — never queried)
            _Res(all_=[_msg("critical", "social-api down")]),  # critical
            _Res(all_=[_msg("email:gmail", "deploy failed", sender="Vercel <n@vercel.com>"), _msg("email:gmail", "x", sender="n@medium.com")]),  # email:gmail
            _Res(all_=[]),  # ops: no rows
            _Res(all_=[_msg("slack:ops-alerts", "worker `a.b.task` failed")]),  # slack:ops-alerts
        ]
    )
    r = _FakeRedis()
    rep = AsyncMock()

    class _Sess:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(TI, "task_session", lambda: _Sess())
    monkeypatch.setenv("TG_INGEST_SKIP", "other-skip")
    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: r)
    monkeypatch.setattr("app.services.ops_agent.report_incident", rep)
    monkeypatch.setattr(TI, "run_async", asyncio.run)

    st = TI.telegram_incidents_ingest.run()
    assert st["ingested"] == 3  # critical + slack + gmail-alert
    assert rep.await_count == 3
    assert db.committed == 1
    # cursor advanced per scanned channel, intel untouched
    assert r.writes["critical"] == 1700001000000
    assert "intel" not in r.writes
    # email non-alert sender skipped but cursor still advanced
    assert st["skipped"] == 1


def test_incidents_ingest_email_extra_senders(monkeypatch):
    from app.worker.tasks import telegram_incidents as TI

    db = _FakeDB(
        results=[
            _Res(all_=[("email:gmail",)]),
            _Res(all_=[_msg("email:gmail", "backup failed", sender="cron@acme-alerts.io")]),
        ]
    )
    r = _FakeRedis()
    rep = AsyncMock()

    class _Sess:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(TI, "task_session", lambda: _Sess())
    monkeypatch.setenv("TG_INGEST_EMAIL_SENDERS", "acme-alerts")
    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: r)
    monkeypatch.setattr("app.services.ops_agent.report_incident", rep)
    monkeypatch.setattr(TI, "run_async", asyncio.run)
    st = TI.telegram_incidents_ingest.run()
    assert st["ingested"] == 1
