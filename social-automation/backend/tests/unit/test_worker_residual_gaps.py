"""Residual line/branch coverage for worker task modules.

Each test targets a specific uncovered line or branch arc reported by the
full-suite coverage run — real task code, fakes only at the boundaries
(DB session, httpx, redis, slack, sidecars).
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class _Res:
    def __init__(self, rows=(), one=None):
        self._rows = list(rows)
        self._one = one

    def scalars(self):
        return SimpleNamespace(
            all=lambda: list(self._rows),
            first=lambda: self._rows[0] if self._rows else None,
        )

    def scalar_one_or_none(self):
        return self._one

    def scalar_one(self):
        return self._rows[0] if self._rows else None


class _DB:
    def __init__(self, results=()):
        self.results = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, stmt, *a):
        r = self.results.pop(0) if self.results else _Res()
        if isinstance(r, _Res) or hasattr(r, "first") or hasattr(r, "scalar_one_or_none"):
            return r
        return _Res(r)

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1


class _ACM:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


def _use_task_session(monkeypatch, mod, db):
    monkeypatch.setattr(mod, "task_session", lambda: _ACM(db))
    return db


# ── account_status_reconcile ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_asr_probe_tiktok_non200(monkeypatch):
    from app.worker.tasks import account_status_reconcile as R

    class _Resp:
        def __init__(self, code):
            self.status_code = code

        def json(self):
            return {}

    class _Client:
        def __init__(self, code):
            self._r = _Resp(code)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return self._r

        async def post(self, *a, **k):
            return self._r

    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **k: _Client(401))
    assert await R._probe("tiktok", "x", "tok", {}) == "revoked"
    monkeypatch.setattr(R.httpx, "AsyncClient", lambda **k: _Client(500))
    assert await R._probe("tiktok", "x", "tok", {}) == "inconclusive"


@pytest.mark.asyncio
async def test_asr_reconcile_wrapper(monkeypatch):
    from app.worker.tasks import account_status_reconcile as R

    def _fake(c):
        c.close()
        return {"checked": 2}

    monkeypatch.setattr(R, "run_async", _fake)
    monkeypatch.setattr(R, "_reconcile", AsyncMock(return_value={"checked": 2}))
    assert R.reconcile_account_status() == {"checked": 2}


@pytest.mark.asyncio
async def test_asr_reconcile_no_token_and_nonactive_drift(monkeypatch):
    from app.worker.tasks import account_status_reconcile as R

    monkeypatch.setattr(R, "decrypt_token", lambda t: "tok")
    _use_task_session(
        monkeypatch,
        R,
        _DB(
            [
                _Res(
                    rows=[
                        # no access_token_enc → probe skipped entirely (elif False arc)
                        SimpleNamespace(
                            id=uuid.uuid4(),
                            platform="facebook",
                            account_id="a1",
                            username="u1",
                            access_token_enc=None,
                            token_expires_at=datetime.now(UTC) + timedelta(days=1),
                            status="active",
                            meta_data={},
                        ),
                        # probe says revoked but DB already non-active → no slack alert, status still updated
                        SimpleNamespace(
                            id=uuid.uuid4(),
                            platform="facebook",
                            account_id="a2",
                            username="u2",
                            access_token_enc="enc",
                            token_expires_at=None,
                            status="expired",
                            meta_data={},
                        ),
                    ]
                )
            ]
        ),
    )
    monkeypatch.setattr(R, "_probe", AsyncMock(return_value="revoked"))
    monkeypatch.setattr(R, "post_alert_to_slack", AsyncMock())
    out = await R._reconcile()
    assert out["checked"] == 2 and out["revoked"] == 1 and out["drifted"] == 1
    R.post_alert_to_slack.assert_not_awaited()


# ── auto_gates ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_gates_survey_pending_none(monkeypatch):
    from app.worker.tasks import auto_gates as AG

    camp = SimpleNamespace(id=1, meta_data={"auto_send": True})
    db = _DB([_Res(rows=[camp]), SimpleNamespace(first=lambda: None)])
    _use_task_session(monkeypatch, AG, db)
    import app.services.email_campaigns as EC

    send = AsyncMock()
    monkeypatch.setattr(EC, "send_campaign", send)
    out = await AG._survey_autosend()
    assert out == {"sent": 0}
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_gates_post_comment_add_failure(monkeypatch):
    from app.worker.tasks import auto_gates as AG

    monkeypatch.setenv("AUTO_APPROVE_POSTS", "1")
    post = SimpleNamespace(id=1, user_id=2, status="review")
    db = _DB([_Res(rows=[post])])
    db.add = lambda row: (_ for _ in ()).throw(RuntimeError("db.add boom"))
    _use_task_session(monkeypatch, AG, db)
    out = await AG._post_autoapprove()
    assert out == {"approved": 1}


# ── avatar_video: tid=None asset lookup skips team filter ───────────────


@pytest.mark.asyncio
async def test_avatar_video_asset_bytes_no_team(monkeypatch):
    import app.services.avatar_video as SVC
    import app.services.media_dedup as DEDUP
    import app.services.media_storage as STORE
    import app.worker.tasks.avatar_video as AVW

    uid, aid = uuid.uuid4(), uuid.uuid4()
    user = SimpleNamespace(id=uid)
    member = SimpleNamespace(team_id=None)  # tid stays None → no team filter
    asset_row = SimpleNamespace(id=aid)

    db = _DB(
        [
            _Res(rows=[user]),  # User lookup
            _Res(rows=[member]),  # TeamMember lookup
            _Res(one=asset_row),  # avatar asset query (unfiltered)
        ]
    )
    import app.worker._async as WA

    monkeypatch.setattr(WA, "task_session", lambda: _ACM(db))

    async def _bytes(row):
        return b"PNG"

    monkeypatch.setattr(DEDUP, "load_asset_bytes", _bytes)
    monkeypatch.setattr(SVC, "generate_avatar_video", AsyncMock(return_value=(b"MP4", {})))
    monkeypatch.setattr(STORE, "save_uploaded_media", AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())))

    out = await AVW._run(
        scenes=[{"clip": "c1"}],
        user_id=None,
        team_id=None,
        avatar_asset_id=str(aid),
        source_photo_asset_id=None,
        music_url="",
        width=1080,
        height=1920,
        filename="a.mp4",
        alt_text="a",
        tags=[],
    )
    assert out["bytes"] == 3


# ── bluesky_credential wrapper ──────────────────────────────────────────


def test_bluesky_sweep_wrapper(monkeypatch):
    from app.worker.tasks import bluesky_credential as BC

    def _fake(c):
        c.close()
        return {"swept": 1}

    monkeypatch.setattr(BC, "run_async", _fake)
    monkeypatch.setattr(BC, "_sweep", AsyncMock(return_value={"swept": 1}))
    assert BC.run_bluesky_credential_sweep() == {"swept": 1}


# ── datalake_executor: devapp_health dedup-key override ─────────────────


@pytest.mark.asyncio
async def test_datalake_executor_devapp_health_key(monkeypatch):
    import app.worker.tasks.datalake_executor as EX

    team_id = uuid.uuid4()
    signals = {"team_id": str(team_id), "actions": [{"type": "devapp_health", "platform": "gh", "reason": "down"}]}
    _use_task_session(monkeypatch, EX, _DB())
    monkeypatch.setattr(EX, "_latest_signals", AsyncMock(return_value=signals))
    monkeypatch.setattr(EX, "_already_executed", AsyncMock(return_value=False))
    fake_fn = AsyncMock(return_value={"ok": True})
    monkeypatch.setitem(EX._EXECUTORS, "devapp_health", fake_fn)

    out = await EX._run()
    assert out["executed"] == 1
    fake_fn.assert_awaited_once()


# ── datalake_insights: dedupe + single-line skip ────────────────────────


def test_datalake_insights_dedupe(monkeypatch):
    import app.services.datalake_insights as di
    import app.services.slack_notifications as SN
    import app.worker.tasks.datalake_insights as DI

    t1, t2, t3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db = _DB([_Res(rows=[t1, t2, t3])])
    _use_task_session(monkeypatch, DI, db)

    def _fake_run(c):
        return asyncio.run(c)

    signals = {"actions": [1]}
    # three teams: first two render identical 2-line text (dedupe), third 1 line
    calls = iter([signals, signals, {"actions": []}])
    monkeypatch.setattr(di, "apply_signals", AsyncMock(side_effect=lambda db, tid: next(calls)))
    posted = []

    async def _post(text):
        posted.append(text)

    monkeypatch.setattr(SN, "post_alert_to_slack", _post)

    seq = iter([["x", "y"], ["x", "y"], ["z"]])
    monkeypatch.setattr(di, "render_slack_lines", lambda s: next(seq))

    monkeypatch.setattr(DI, "run_async", _fake_run)

    out = DI.datalake_insights_sweep()
    assert out["teams"] == 3 and out["alerts"] == 1
    assert posted == ["x\ny"]


# ── instagram_messenger: blob cleanup OSError swallowed ─────────────────


def test_ig_dm_blob_unlink_oserror(monkeypatch):
    import os as _os

    import app.worker.tasks.instagram_messenger as IM

    class _FakeClient:
        def __init__(self):
            self.logged_in = None

        def set_proxy(self, p):
            pass

        def set_locale(self, loc):
            pass

        def set_timezone_offset(self, o):
            pass

        def load_settings(self, p):
            pass

        def login_by_sessionid(self, sid):
            self.logged_in = sid

    monkeypatch.setitem(sys.modules, "instagrapi", SimpleNamespace(Client=_FakeClient))

    real_unlink = _os.unlink
    calls = {"n": 0}

    def _boom(path, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("gone")
        return real_unlink(path, *a, **k)

    monkeypatch.setattr(_os, "unlink", _boom)
    cl = IM.InstagramPrivateDMClient("sid-1", settings_blob="{}")._client()
    assert cl.logged_in == "sid-1"


# ── linkedin_dmp_sync: no redis → still return urn ──────────────────────


@pytest.mark.asyncio
async def test_dmp_segment_urn_no_redis(monkeypatch):
    import app.services.linkedin_advanced as LA
    from app.worker.tasks import linkedin_dmp_sync as LD

    monkeypatch.setattr(LA, "create_dmp_segment", AsyncMock(return_value={"urn": "urn:li:seg:1"}))
    assert await LD._segment_urn(None) == ("urn:li:seg:1", True)


# ── linkedin_messenger: capture_ack prepended to reply ──────────────────


@pytest.mark.asyncio
async def test_li_messenger_capture_ack_prepended(monkeypatch):
    import redis.asyncio as aioredis

    import app.worker.tasks.linkedin_messenger as LM

    account = SimpleNamespace(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        display_name="Agent",
        username="agent",
        meta_data={"linkedin_messenger": {"enabled": True}},
    )

    class _Redis:
        async def get(self, k):
            return None

        async def setex(self, *a):
            return True

        async def aclose(self):
            return None

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: _Redis())

    class _Sidecar:
        def __init__(self, url):
            pass

        async def health(self):
            return {"rate_limited": False, "has_session": True}

        async def get_conversations(self):
            return {"conversations": [{"thread_id": "t1", "name": "Bob"}]}

        async def get_thread_messages(self, tid):
            return {"messages": [{"sender": "bob", "text": "hi there", "id": "m1"}]}

        send_message = AsyncMock()

    monkeypatch.setattr(LM, "LinkedInSidecarClient", _Sidecar)
    monkeypatch.setattr(LM, "check_cooldown", AsyncMock(return_value=True))
    monkeypatch.setattr(LM, "is_thread_paused", AsyncMock(return_value=False))
    monkeypatch.setattr(LM, "_capture_contact", AsyncMock(return_value="Got your email — "))
    monkeypatch.setattr(LM, "detect_intent", AsyncMock(return_value="general"))
    monkeypatch.setattr(LM, "retrieve_brand_context", AsyncMock(return_value=""))
    monkeypatch.setattr(LM, "generate_contextual_reply", AsyncMock(return_value="REPLY"))
    monkeypatch.setattr(LM, "store_message_memory", AsyncMock())
    monkeypatch.setattr(LM, "set_cooldown", AsyncMock())
    monkeypatch.setattr(LM.asyncio, "sleep", AsyncMock())

    seen: dict = {}
    n = await LM._process_account(account, {"cooldown_seconds": 1}, seen, "", "", "", "")
    assert n == 1
    _Sidecar.send_message.assert_awaited_once_with("t1", "Got your email — REPLY")
    assert seen["t1"] == "hi there"


# ── linkedin_portability_sync: empty changelog elements ─────────────────


@pytest.mark.asyncio
async def test_portability_member_dp_empty_changelog(monkeypatch):
    import app.services.linkedin_portability as lp
    from app.worker.tasks import linkedin_portability_sync as LPS

    person = SimpleNamespace(access_token_enc=b"enc", team_id=uuid.uuid4(), id=uuid.uuid4())
    monkeypatch.setattr(LPS, "decrypt_token", lambda b: "tok")
    monkeypatch.setattr(lp, "member_snapshot_domain", AsyncMock(return_value={"ok": True, "elements": [], "total": 0, "pages": 0}))
    monkeypatch.setattr(lp, "member_changelog", AsyncMock(return_value={"ok": True, "data": {"elements": []}}))
    monkeypatch.setattr(LPS, "_redis", lambda: None)

    db = _DB()
    out = await LPS._member_dp(db, person, client=None, token="tok")
    assert out["domains"] == len(lp.MEMBER_DOMAINS)
    assert len(db.added) == 2


# ── notebook_reports: no redis lock → run proceeds ──────────────────────


def test_notebook_report_no_lock(monkeypatch):
    import app.services.notebook_runner as NR_SVC
    from app.worker.tasks import notebook_reports as NR

    monkeypatch.setattr(NR, "_report_lock", lambda: None)
    monkeypatch.setattr(
        NR_SVC,
        "run_report_notebook",
        lambda nb, params: {
            "manifest": {},
            "manifest_path": "/tmp/nb/m.json",
            "duration_s": 0.1,
            "notebook": nb,
        },
    )
    out = NR.run_notebook_report("nb-nolock", {}, send=False)
    assert out["executed"] is True and out["emailed"] == 0


# ── owner_gates: stale reply key popped ─────────────────────────────────


@pytest.mark.asyncio
async def test_owner_gates_stale_reply_popped(monkeypatch):
    import app.services.slack_notifications as SN
    from app.worker.tasks import owner_gates as OG

    state = {"replies": {"9": "done"}, "open": []}
    saved = {}
    monkeypatch.setattr(OG, "_token", lambda: "tok")
    monkeypatch.setattr(OG, "_open_gates", AsyncMock(return_value=[{"number": 5, "title": "gate"}]))
    monkeypatch.setattr(OG, "_latest_owner_comment", AsyncMock(return_value=""))
    monkeypatch.setattr(OG, "_load_state", lambda: state)
    monkeypatch.setattr(OG, "_save_state", lambda s: saved.update(s))
    posted = []

    async def _post(text):
        posted.append(text)

    monkeypatch.setattr(SN, "post_alert_to_slack", _post)

    out = await OG._poll()
    assert out == {"open": 1, "actions": 0}
    assert "9" not in saved["replies"]
    assert posted  # digest path fired (open set changed)


# ── publish_verify wrapper ──────────────────────────────────────────────


def test_publish_verify_wrapper(monkeypatch):
    from app.worker.tasks import publish_verify as PV

    def _fake(c):
        c.close()
        return {"ok": True}

    monkeypatch.setattr(PV, "run_async", _fake)
    monkeypatch.setattr(PV, "_run", AsyncMock(return_value={"ok": True}))
    out = PV.verify_publish_paths()
    assert out == {"ok": True}


# ── publish_retry: preset error_class skips reclassification ────────────


@pytest.mark.asyncio
async def test_publish_retry_preset_terminal_class(monkeypatch):
    from app.services.error_taxonomy import TERMINAL
    from app.worker.tasks import publish_retry as PR

    tgt = SimpleNamespace(
        id=uuid.uuid4(),
        post_id=uuid.uuid4(),
        social_account_id=uuid.uuid4(),
        status="failed",
        error_message="bad",
        error_class=TERMINAL,
        updated_at=datetime.now(UTC),
    )
    db = _DB([_Res(rows=[tgt])])
    _use_task_session(monkeypatch, PR, db)
    out = await PR._sweep()
    assert out == {"retried": 0, "skipped": 1}


# ── skool_watch: intravox digest push ───────────────────────────────────


@pytest.mark.asyncio
async def test_skool_intravox_digest_paths(monkeypatch):
    import httpx

    import app.worker.tasks.skool_watch as SW

    monkeypatch.setenv("NEXTCLOUD_USERNAME", "u")
    monkeypatch.setenv("NEXTCLOUD_APP_PASSWORD", "p")
    monkeypatch.setenv("NEXTCLOUD_DAV_URL", "https://nc.example/remote.php/dav")
    try:
        from app.core.config import settings as _settings

        monkeypatch.setattr(_settings, "NEXTCLOUD_BASE_URL", "", raising=False)
    except Exception:
        pass

    posts = [{"t": "digest title", "h": "/p/1"}]

    class _Resp:
        def __init__(self, code=200, payload=None):
            self.status_code = code
            self._p = payload or {}

        def json(self):
            return self._p

    class _Client:
        mode = "no_uid"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **kw):
            if _Client.mode == "raise":
                raise RuntimeError("nc down")
            return _Resp(200, {"uniqueId": "u1" if _Client.mode == "ok" else None})

        async def get(self, url, **kw):
            return _Resp(200, {})

        async def put(self, url, json=None, **kw):
            return _Resp(200, {})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _Client())

    # uid falsy → early return
    _Client.mode = "no_uid"
    await SW._push_intravox_digest(posts)

    # full path: create → get → put
    _Client.mode = "ok"
    await SW._push_intravox_digest(posts)

    # exception → swallowed
    _Client.mode = "raise"
    await SW._push_intravox_digest(posts)


# ── synigoros_case ──────────────────────────────────────────────────────


def test_synigoros_gmail_get_non401_reraiseses(monkeypatch):
    import urllib.request

    from app.worker.tasks import synigoros_case as SC

    monkeypatch.setattr(SC, "_gmail_access_token", lambda: "tok")

    def _boom(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 500, "err", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(urllib.error.HTTPError):
        SC._gmail_get("/x")


def _imap_cfg(monkeypatch):
    from app.worker.tasks import synigoros_case as SC

    mapping = {"IMAP_PASSWORD": "pw", "IMAP_HOST": "h", "IMAP_PORT": "993", "IMAP_USER": "u"}
    monkeypatch.setattr(SC, "_cfg", lambda k, d="": mapping.get(k, d))
    return SC


class _FakeMail:
    """imaplib stand-in; FETCH returns header+text parts, logout raises."""

    def __init__(self, host, port):
        pass

    def login(self, u, p):
        return "OK"

    def select(self, m):
        return "OK"

    def uid(self, cmd, *a):
        if cmd == "SEARCH":
            return "OK", [b"1"]
        return "OK", [
            (b"1 (UID 1 BODY[HEADER.FIELDS (FROM SUBJECT DATE)] BODY[TEXT]<0.1200>)", b"From: a@x.com\r\nSubject: hello there\r\nDate: Mon\r\n"),
            (b"2 (TEXT)", "not-bytes"),
            (b"3 (TEXT)<0.1200>", b"body blob"),
        ]

    def logout(self):
        raise RuntimeError("imap logout boom")


def test_synigoros_scan_replies_imap_and_gmail(monkeypatch):
    import imaplib

    SC = _imap_cfg(monkeypatch)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", _FakeMail)

    gmail_calls = []

    def _gmail(path):
        gmail_calls.append(path)
        if "messages?q=" in path:
            return {"messages": [{"id": "m1", "threadId": "t1", "snippet": "snip"}]}
        return {
            "payload": {
                "headers": [
                    {"name": "From", "value": "a@other.com"},
                    {"name": "Subject", "value": "plain subj"},
                    {"name": "Date", "value": "d"},
                ]
            }
        }

    monkeypatch.setattr(SC, "_gmail_get", _gmail)

    # domains: zzz never matches (for-loop exhaust arc), example.com require=NEEDLE
    domains = [("zzz", None), ("example.com", r"NEEDLE")]
    fresh = SC._scan_replies_sync(domains, "2026-01-01")
    # imap: zzz-domain hit appended (no require); example.com hit filtered by NEEDLE
    assert any(h["lane"] == "cloudless" and h["key"].startswith("imap:zzz") for h in fresh)
    assert not any("imap:example.com" in h["key"] for h in fresh)
    # gmail: neither domain in low → appended with no require
    assert any(h["lane"] == "gmail" for h in fresh)

    # second pass: gmail From matches example.com → require NEEDLE filters it out
    def _gmail2(path):
        if "messages?q=" in path:
            return {"messages": [{"id": "m2", "threadId": "t2", "snippet": "s"}]}
        return {
            "payload": {
                "headers": [
                    {"name": "From", "value": "a@example.com"},
                    {"name": "Subject", "value": "plain subj"},
                    {"name": "Date", "value": "d"},
                ]
            }
        }

    monkeypatch.setattr(SC, "_gmail_get", _gmail2)
    fresh2 = SC._scan_replies_sync([("example.com", r"NEEDLE")], "2026-01-01")
    assert not any(h["lane"] == "gmail" for h in fresh2)


def test_synigoros_imap_hits_logout_swallowed(monkeypatch):
    import imaplib

    SC = _imap_cfg(monkeypatch)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", _FakeMail)
    out = SC._imap_hits("zzz", "2026-01-01", None)
    assert len(out) == 1 and out[0]["lane"] == "cloudless"


# ── token_refresh: slack alert failure swallowed ────────────────────────


@pytest.mark.asyncio
async def test_token_refresh_alert_failure_swallowed(monkeypatch):
    import app.services.slack_notifications as SN
    from app.worker.tasks import token_refresh as TR

    monkeypatch.setattr(TR, "decrypt_token", lambda t: f"dec-{t}")
    monkeypatch.setattr(TR, "encrypt_token", lambda t: f"enc-{t}")
    monkeypatch.setattr(TR, "_get_oauth_client", lambda p: SimpleNamespace(refresh_token=AsyncMock(side_effect=RuntimeError("revoked"))))

    acct = SimpleNamespace(
        platform="linkedin",
        username="u",
        account_id="a",
        access_token_enc="enc_a",
        refresh_token_enc="enc_r",
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        updated_at=None,
        scopes=[],
        status="active",
        meta_data={},
    )

    class _DB:
        commit = AsyncMock()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [acct]))

    monkeypatch.setattr(TR, "_worker_db", lambda: _DB())
    monkeypatch.setattr(SN, "_post_slack_text", AsyncMock(side_effect=RuntimeError("slack down")))

    out = await TR._refresh_expiring_tokens_async()
    assert out["errors"] and acct.status == "expired"
