"""Unit tests for the Talk room-management additions + meeting_notes service.

HTTP boundary (httpx) and settings are faked; EspoCRM calls are patched at
the espocrm_client module seam.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import app.services.meeting_notes as MN
import app.services.nextcloud_talk as TALK


def _settings(**over) -> SimpleNamespace:
    base = dict(
        NEXTCLOUD_TALK_NOTIFY=True,
        NEXTCLOUD_USERNAME="u@cloudless.gr",
        NEXTCLOUD_APP_PASSWORD="app-pw",
        NEXTCLOUD_DAV_URL="https://cloud.cloudless.gr/remote.php/dav/files/u@cloudless.gr",
        NEXTCLOUD_BASE_URL="",
        NEXTCLOUD_TALK_ROOM="room-ops",
        NEXTCLOUD_TALK_ROOM_ALERTS="room-approvals",
        NEXTCLOUD_TALK_ROOM_CHANGELOG="room-changelog",
        NEXTCLOUD_TALK_BOT_SECRET="bot-secret",
    )
    base.update(over)
    return SimpleNamespace(**base)


class _Resp:
    def __init__(self, status=200, payload=None, content=b"", headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.content = content
        self.headers = headers or {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class _Client:
    calls: list[dict] = []
    resp: _Resp = _Resp()

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        type(self).calls.append({"method": "POST", "url": url, **kw})
        return type(self).resp

    async def get(self, url, **kw):
        type(self).calls.append({"method": "GET", "url": url, **kw})
        return type(self).resp

    async def delete(self, url, **kw):
        type(self).calls.append({"method": "DELETE", "url": url, **kw})
        return type(self).resp


@pytest.fixture(autouse=True)
def _reset():
    _Client.calls = []
    _Client.resp = _Resp()
    yield


# ── token guard + room routing ────────────────────────────────────────


def test_valid_room_token():
    assert TALK.valid_room_token("abc12345")
    assert not TALK.valid_room_token("ABC12345")
    assert not TALK.valid_room_token("short")
    assert not TALK.valid_room_token("../../etc")
    assert not TALK.valid_room_token("")
    assert not TALK.valid_room_token(None)


def test_room_token_changelog():
    with patch.object(TALK, "get_settings", return_value=_settings()):
        assert TALK.room_token("changelog") == "room-changelog"
        assert TALK.room_token("digest") == "room-ops"


def test_room_token_changelog_falls_back():
    with patch.object(TALK, "get_settings", return_value=_settings(NEXTCLOUD_TALK_ROOM_CHANGELOG="")):
        assert TALK.room_token("changelog") == "room-ops"


# ── room CRUD over OCS v4 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_room_posts_roomname():
    _Client.resp = _Resp(201, {"ocs": {"data": {"token": "abc12345"}}})
    with (
        patch.object(TALK, "get_settings", return_value=_settings()),
        patch.object(TALK.httpx, "AsyncClient", _Client),
    ):
        token = await TALK.create_room("Consultation — X", description="d")
    assert token == "abc12345"
    call = _Client.calls[0]
    assert call["url"].endswith("/ocs/v2.php/apps/spreed/api/v4/room")
    assert call["data"]["roomName"] == "Consultation — X"
    assert call["data"]["roomType"] == "3"


@pytest.mark.asyncio
async def test_create_room_rejects_bad_token():
    _Client.resp = _Resp(201, {"ocs": {"data": {"token": "BAD!!"}}})
    with (
        patch.object(TALK, "get_settings", return_value=_settings()),
        patch.object(TALK.httpx, "AsyncClient", _Client),
    ):
        assert await TALK.create_room("x") is None


@pytest.mark.asyncio
async def test_create_room_disabled():
    with patch.object(TALK, "get_settings", return_value=_settings(NEXTCLOUD_TALK_NOTIFY=False)):
        assert await TALK.create_room("x") is None
    assert _Client.calls == []


@pytest.mark.asyncio
async def test_post_to_room_validates_token():
    with patch.object(TALK, "get_settings", return_value=_settings()):
        assert await TALK.post_to_room("../evil", "hi") is False
        assert await TALK.post_to_room("notaroom!", "hi") is False
    assert _Client.calls == []


@pytest.mark.asyncio
async def test_post_to_room_ok():
    _Client.resp = _Resp(201)
    with (
        patch.object(TALK, "get_settings", return_value=_settings()),
        patch.object(TALK.httpx, "AsyncClient", _Client),
    ):
        assert await TALK.post_to_room("abc12345", "hello") is True
    assert _Client.calls[0]["url"].endswith("/ocs/v2.php/apps/spreed/api/v1/chat/abc12345")


@pytest.mark.asyncio
async def test_list_room_messages():
    msgs = [{"id": 1, "message": "hi"}]
    _Client.resp = _Resp(200, {"ocs": {"data": msgs}})
    with (
        patch.object(TALK, "get_settings", return_value=_settings()),
        patch.object(TALK.httpx, "AsyncClient", _Client),
    ):
        out = await TALK.list_room_messages("abc12345", limit=50)
    assert out == msgs
    assert _Client.calls[0]["params"]["limit"] == "50"


@pytest.mark.asyncio
async def test_delete_room():
    _Client.resp = _Resp(200)
    with (
        patch.object(TALK, "get_settings", return_value=_settings()),
        patch.object(TALK.httpx, "AsyncClient", _Client),
    ):
        assert await TALK.delete_room("abc12345") is True
        assert await TALK.delete_room("bad") is False


# ── recording file detection ──────────────────────────────────────────


def test_find_recording_file_picks_video():
    msgs = [
        {"id": 1, "messageParameters": {"file": {"id": 11, "name": "photo.png"}}},
        {"id": 2, "messageParameters": {"file": {"id": 22, "name": "call-2026-11-10.mp4"}}},
        {"id": 3, "message": "thanks"},
    ]
    rec = TALK.find_recording_file(msgs)
    assert rec == {"file_id": 22, "name": "call-2026-11-10.mp4", "message_id": 2}


def test_find_recording_file_matches_recording_name():
    msgs = [{"id": 9, "messageParameters": {"file": {"id": 5, "name": "Talk Recording.webm"}}}]
    assert TALK.find_recording_file(msgs)["file_id"] == 5


def test_find_recording_file_none():
    assert TALK.find_recording_file([{"id": 1, "message": "hi"}]) is None
    assert TALK.find_recording_file([]) is None


def test_room_url():
    with patch.object(TALK, "get_settings", return_value=_settings()):
        assert TALK.room_url("abc12345") == "https://cloud.cloudless.gr/call/abc12345"


# ── meeting_notes service ─────────────────────────────────────────────


def _opp(**over):
    base = {
        "id": "opp-1",
        "name": "Consultation – Acme",
        "stage": "appointmentscheduled",
        "closeDate": "2026-11-20",
        "accountId": None,
        "accountName": None,
    }
    base.update(over)
    return base


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def commit(self):
        pass


@pytest.mark.asyncio
async def test_ensure_disabled():
    with (
        patch.object(MN.talk, "talk_enabled", return_value=False),
        patch.object(MN.espo, "configured", return_value=True),
    ):
        out = await MN.ensure_consult_rooms(session_factory=lambda: _Session())
    assert out["status"] == "skipped"


@pytest.mark.asyncio
async def test_ensure_creates_room_and_notes():
    notes_seen = []

    async def _notes(oid):
        return []

    async def _contact(opp):
        return "lead@acme.com"

    async def _add_note(oid, note, *, entity="Lead"):
        notes_seen.append((oid, note, entity))
        return True

    with (
        patch.object(MN, "_enabled", return_value=True),
        patch.object(MN.espo, "list_all", new=AsyncMock(return_value=[_opp()])),
        patch.object(MN, "_opp_notes", new=_notes),
        patch.object(MN, "_contact_email_for_opp", new=_contact),
        patch.object(MN.talk, "create_room", new=AsyncMock(return_value="abc12345")),
        patch.object(MN.talk, "post_to_room", new=AsyncMock(return_value=True)),
        patch.object(MN.talk, "room_url", return_value="https://cloud.cloudless.gr/call/abc12345"),
        patch.object(MN.espo, "add_lead_note", new=_add_note),
    ):
        out = await MN.ensure_consult_rooms(session_factory=lambda: _Session())
    assert out["created"] == 1
    assert notes_seen and "talk_room:abc12345" in notes_seen[0][1]
    assert "call/abc12345" in notes_seen[0][1]


@pytest.mark.asyncio
async def test_ensure_skips_existing_marker():
    async def _notes(oid):
        return [{"post": "talk_room:zzzz9999 — join: x"}]

    create = AsyncMock(return_value="new12345")
    with (
        patch.object(MN, "_enabled", return_value=True),
        patch.object(MN.espo, "list_all", new=AsyncMock(return_value=[_opp()])),
        patch.object(MN, "_opp_notes", new=_notes),
        patch.object(MN.talk, "create_room", new=create),
    ):
        out = await MN.ensure_consult_rooms(session_factory=lambda: _Session())
    assert out["existing"] == 1
    create.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_skips_past_meetings():
    create = AsyncMock()
    with (
        patch.object(MN, "_enabled", return_value=True),
        patch.object(
            MN.espo,
            "list_all",
            new=AsyncMock(return_value=[_opp(closeDate="2020-01-01")]),
        ),
        patch.object(MN.talk, "create_room", new=create),
    ):
        out = await MN.ensure_consult_rooms(session_factory=lambda: _Session())
    assert out["created"] == 0
    create.assert_not_called()


@pytest.mark.asyncio
async def test_process_no_room_marks_skip():
    async def _has_marker(oid, marker):
        return False

    notes = []

    async def _add_note(oid, note, *, entity="Lead"):
        notes.append(note)
        return True

    with (
        patch.object(MN, "_enabled", return_value=True),
        patch.object(MN.espo, "list_all", new=AsyncMock(return_value=[_opp(closeDate="2020-01-01")])),
        patch.object(MN, "_opp_has_marker", new=_has_marker),
        patch.object(MN, "_find_room_token", new=AsyncMock(return_value=None)),
        patch.object(MN.espo, "add_lead_note", new=_add_note),
    ):
        out = await MN.process_meeting_notes(session_factory=lambda: _Session())
    assert out["no_room"] == 1
    assert notes == []  # no room → nothing written, retried next sweep


@pytest.mark.asyncio
async def test_process_no_recording_writes_skip_note():
    async def _has_marker(oid, marker):
        return False

    notes = []

    async def _add_note(oid, note, *, entity="Lead"):
        notes.append(note)
        return True

    with (
        patch.object(MN, "_enabled", return_value=True),
        patch.object(MN.espo, "list_all", new=AsyncMock(return_value=[_opp(closeDate="2020-01-01")])),
        patch.object(MN, "_opp_has_marker", new=_has_marker),
        patch.object(MN, "_find_room_token", new=AsyncMock(return_value="abc12345")),
        patch.object(MN.talk, "list_room_messages", new=AsyncMock(return_value=[{"id": 1, "message": "hi"}])),
        patch.object(MN.espo, "add_lead_note", new=_add_note),
    ):
        out = await MN.process_meeting_notes(session_factory=lambda: _Session())
    assert out["no_recording"] == 1
    assert notes and notes[0].startswith("Meeting notes:")


@pytest.mark.asyncio
async def test_process_full_pipeline_writes_minutes():
    async def _has_marker(oid, marker):
        return False

    notes = []

    async def _add_note(oid, note, *, entity="Lead"):
        notes.append(note)
        return True

    messages = [{"id": 5, "messageParameters": {"file": {"id": 77, "name": "recording.mp4"}}}]
    with (
        patch.object(MN, "_enabled", return_value=True),
        patch.object(MN.espo, "list_all", new=AsyncMock(return_value=[_opp(closeDate="2020-01-01")])),
        patch.object(MN, "_opp_has_marker", new=_has_marker),
        patch.object(MN, "_find_room_token", new=AsyncMock(return_value="abc12345")),
        patch.object(MN.talk, "list_room_messages", new=AsyncMock(return_value=messages)),
        patch.object(MN.talk, "download_talk_file", new=AsyncMock(return_value=(b"mp4bytes", "video/mp4"))),
        patch.object(MN, "_extract_wav", new=AsyncMock(return_value=b"wavbytes")),
        patch.object(
            MN,
            "_transcribe",
            new=AsyncMock(return_value="Client needs migration help. We agreed a fixed scope."),
        ),
        patch.object(MN, "_summarize_minutes", new=AsyncMock(return_value="## Summary\nFixed-scope migration.")),
        patch.object(MN.talk, "post_to_talk", new=AsyncMock(return_value=True)),
        patch.object(MN.espo, "add_lead_note", new=_add_note),
    ):
        out = await MN.process_meeting_notes(session_factory=lambda: _Session())
    assert out["minutes_written"] == 1
    assert notes and "Meeting notes:" in notes[0] and "Fixed-scope migration" in notes[0]


@pytest.mark.asyncio
async def test_process_skips_future_opps():
    with (
        patch.object(MN, "_enabled", return_value=True),
        patch.object(MN.espo, "list_all", new=AsyncMock(return_value=[_opp()])),
    ):
        out = await MN.process_meeting_notes(session_factory=lambda: _Session())
    assert out["checked"] == 0


@pytest.mark.asyncio
async def test_run_sweep_composes_both():
    with (
        patch.object(MN, "ensure_consult_rooms", new=AsyncMock(return_value={"created": 1})),
        patch.object(MN, "process_meeting_notes", new=AsyncMock(return_value={"checked": 2})),
    ):
        out = await MN.run_meeting_notes_sweep(session_factory=lambda: _Session())
    assert out == {"ensure": {"created": 1}, "process": {"checked": 2}}


# ── helper coverage: notes/lead/contact lookups ───────────────────────


@pytest.mark.asyncio
async def test_factory_default_and_custom():
    sentinel = object()
    assert MN._factory(sentinel) is sentinel
    fake = lambda: _Session()  # noqa: E731
    with patch("app.worker._async.get_session_factory", return_value=fake):
        assert MN._factory() is fake


@pytest.mark.asyncio
async def test_opp_notes_and_marker():
    rows = [{"post": "talk_room:abc12345 — join"}, {"post": "Meeting notes: x"}]
    with patch.object(MN.espo, "list_all", new=AsyncMock(return_value=rows)) as la:
        assert await MN._opp_notes("o1") == rows
        assert await MN._opp_has_marker("o1", "talk_room:") is True
        assert await MN._opp_has_marker("o1", "absent") is False
    assert la.await_count == 3


def test_token_from_notes():
    assert MN._token_from_notes([{"post": "no marker"}, {"post": "talk_room:zz99yyxx ok"}]) == "zz99yyxx"
    assert MN._token_from_notes([{"post": "none"}]) is None
    assert MN._token_from_notes([]) is None


@pytest.mark.asyncio
async def test_lead_id_for_email_paths():
    assert await MN._lead_id_for_email("") is None
    with patch.object(MN.espo, "espo_get", new=AsyncMock(return_value={"list": [{"id": "lead-9"}]})):
        assert await MN._lead_id_for_email("a@b.co") == "lead-9"
    with patch.object(MN.espo, "espo_get", new=AsyncMock(side_effect=RuntimeError("boom"))):
        assert await MN._lead_id_for_email("a@b.co") is None


@pytest.mark.asyncio
async def test_contact_email_for_opp_paths():
    opp = _opp(accountId="acc-1")
    with patch.object(
        MN.espo,
        "espo_get",
        new=AsyncMock(return_value={"list": [{"emailAddress": "  Lead@Acme.com "}]}),
    ):
        assert await MN._contact_email_for_opp(opp) == "lead@acme.com"

    # contacts empty → account fallback
    async def _get(path, params=None):
        if path.startswith("/Opportunity"):
            return {"list": []}
        return {"emailAddress": "Acct@X.io"}

    with patch.object(MN.espo, "espo_get", new=_get):
        assert await MN._contact_email_for_opp(opp) == "acct@x.io"
    # contacts raise + account raise → None
    with patch.object(MN.espo, "espo_get", new=AsyncMock(side_effect=RuntimeError("x"))):
        assert await MN._contact_email_for_opp(opp) is None


@pytest.mark.asyncio
async def test_find_room_token_lead_fallback():
    opp = _opp()

    # opp notes have no marker → resolve via contact email → lead notes
    async def _notes(oid):
        return [{"post": "unrelated"}]

    async def _lead_notes(*a, **kw):
        return [{"post": "talk_room:lead1234 — join"}]

    with (
        patch.object(MN, "_opp_notes", new=_notes),
        patch.object(MN, "_contact_email_for_opp", new=AsyncMock(return_value="l@x.co")),
        patch.object(MN, "_lead_id_for_email", new=AsyncMock(return_value="lead-1")),
        patch.object(MN.espo, "list_all", new=_lead_notes),
    ):
        assert await MN._find_room_token(opp) == "lead1234"
    # no lead id → None
    with (
        patch.object(MN, "_opp_notes", new=_notes),
        patch.object(MN, "_contact_email_for_opp", new=AsyncMock(return_value=None)),
        patch.object(MN, "_lead_id_for_email", new=AsyncMock(return_value=None)),
    ):
        assert await MN._find_room_token(opp) is None


@pytest.mark.asyncio
async def test_create_consult_room_token_none():
    with (
        patch.object(MN.talk, "create_room", new=AsyncMock(return_value=None)),
        patch.object(MN.talk, "post_to_room", new=AsyncMock()) as post,
    ):
        assert await MN._create_consult_room(_opp(), "e@x.co") is None
    post.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_error_path_counts():
    async def _boom(oid):
        raise RuntimeError("espo down")

    with (
        patch.object(MN, "_enabled", return_value=True),
        patch.object(MN.espo, "list_all", new=AsyncMock(return_value=[_opp()])),
        patch.object(MN, "_opp_notes", new=_boom),
    ):
        out = await MN.ensure_consult_rooms(session_factory=lambda: _Session())
    assert out["errors"] == 1


@pytest.mark.asyncio
async def test_process_disabled():
    with patch.object(MN, "_enabled", return_value=False):
        out = await MN.process_meeting_notes(session_factory=lambda: _Session())
    assert out["status"] == "skipped"


# ── ffmpeg / whisper / minutes helpers ────────────────────────────────


@pytest.mark.asyncio
async def test_extract_wav_no_ffmpeg():
    with patch.object(MN.shutil, "which", return_value=None):
        assert await MN._extract_wav(b"mp4") is None


class _FfmpegProc:
    def __init__(self, rc):
        self.returncode = rc

    async def wait(self):
        return self.returncode


@pytest.mark.asyncio
async def test_extract_wav_success_and_failure():
    async def _fake_exec(*args, **kw):
        out = args[-1]
        with open(out, "wb") as f:
            f.write(b"RIFFwav")
        return _FfmpegProc(0)

    with (
        patch.object(MN.shutil, "which", return_value="/usr/bin/ffmpeg"),
        patch.object(MN.asyncio, "create_subprocess_exec", _fake_exec),
    ):
        assert await MN._extract_wav(b"mp4bytes") == b"RIFFwav"

    async def _fake_fail(*args, **kw):
        return _FfmpegProc(1)

    with (
        patch.object(MN.shutil, "which", return_value="/usr/bin/ffmpeg"),
        patch.object(MN.asyncio, "create_subprocess_exec", _fake_fail),
    ):
        assert await MN._extract_wav(b"mp4bytes") is None

    async def _fake_boom(*args, **kw):
        raise OSError("no exec")

    with (
        patch.object(MN.shutil, "which", return_value="/usr/bin/ffmpeg"),
        patch.object(MN.asyncio, "create_subprocess_exec", _fake_boom),
    ):
        assert await MN._extract_wav(b"mp4bytes") is None


@pytest.mark.asyncio
async def test_transcribe_paths():
    import app.services.inference as INF

    # wav path succeeds
    with (
        patch.object(MN, "_extract_wav", new=AsyncMock(return_value=b"wav")),
        patch.object(INF, "transcribe_workers_ai", new=AsyncMock(return_value={"text": " hello "})),
    ):
        assert await MN._transcribe((b"raw", "video/mp4")) == "hello"
    # wav empty → raw fallback with audio/mp4 ctype
    calls = []

    async def _tr(raw, ctype):
        calls.append(ctype)
        return {"response": "raw text"}

    with (
        patch.object(MN, "_extract_wav", new=AsyncMock(return_value=b"")),
        patch.object(INF, "transcribe_workers_ai", new=_tr),
    ):
        assert await MN._transcribe((b"raw", "video/mp4")) == "raw text"
    assert calls == ["audio/mp4"]
    # both fail → None
    with (
        patch.object(MN, "_extract_wav", new=AsyncMock(return_value=None)),
        patch.object(INF, "transcribe_workers_ai", new=AsyncMock(side_effect=RuntimeError("x"))),
    ):
        assert await MN._transcribe((b"raw", "audio/wav")) is None


@pytest.mark.asyncio
async def test_summarize_minutes_paths():
    import app.core.config as CFG
    import app.services.inference as INF

    with (
        patch.object(CFG, "get_settings", return_value=SimpleNamespace(DMR_MID_MODEL="m")),
        patch.object(INF, "call_inference", new=AsyncMock(return_value={"response": "## Summary\nhi"})),
    ):
        out = await MN._summarize_minutes("t", _opp(), _Session())
    assert out == "## Summary\nhi"
    # list response joined
    with (
        patch.object(CFG, "get_settings", return_value=SimpleNamespace(DMR_MID_MODEL="m")),
        patch.object(INF, "call_inference", new=AsyncMock(return_value={"text": ["a", "b"]})),
    ):
        assert await MN._summarize_minutes("t", _opp(), _Session()) == "a\nb"
    # exception → None
    with (
        patch.object(CFG, "get_settings", return_value=SimpleNamespace(DMR_MID_MODEL="m")),
        patch.object(INF, "call_inference", new=AsyncMock(side_effect=RuntimeError("x"))),
    ):
        assert await MN._summarize_minutes("t", _opp(), _Session()) is None


# ── process_meeting_notes error branches ──────────────────────────────


async def _past_opp(*a, **kw):
    return [_opp(closeDate="2020-01-01")]


@pytest.mark.asyncio
async def test_process_download_fail_and_oversize():
    async def _has_marker(oid, marker):
        return False

    msgs = [{"id": 5, "messageParameters": {"file": {"id": 77, "name": "r.mp4"}}}]
    with (
        patch.object(MN, "_enabled", return_value=True),
        patch.object(MN.espo, "list_all", new=_past_opp),
        patch.object(MN, "_opp_has_marker", new=_has_marker),
        patch.object(MN, "_find_room_token", new=AsyncMock(return_value="abc12345")),
        patch.object(MN.talk, "list_room_messages", new=AsyncMock(return_value=msgs)),
        patch.object(MN.talk, "download_talk_file", new=AsyncMock(return_value=None)),
    ):
        out = await MN.process_meeting_notes(session_factory=lambda: _Session())
    assert out["errors"] == 1

    big = b"x" * 11
    with (
        patch.object(MN, "_enabled", return_value=True),
        patch.object(MN, "_MAX_RECORDING_BYTES", 10),
        patch.object(MN.espo, "list_all", new=_past_opp),
        patch.object(MN, "_opp_has_marker", new=_has_marker),
        patch.object(MN, "_find_room_token", new=AsyncMock(return_value="abc12345")),
        patch.object(MN.talk, "list_room_messages", new=AsyncMock(return_value=msgs)),
        patch.object(MN.talk, "download_talk_file", new=AsyncMock(return_value=(big, "video/mp4"))),
    ):
        out = await MN.process_meeting_notes(session_factory=lambda: _Session())
    assert out["errors"] == 1


@pytest.mark.asyncio
async def test_process_transcribe_and_minutes_failures():
    async def _has_marker(oid, marker):
        return False

    msgs = [{"id": 5, "messageParameters": {"file": {"id": 77, "name": "r.mp4"}}}]
    patches_common = [
        patch.object(MN, "_enabled", return_value=True),
        patch.object(MN.espo, "list_all", new=_past_opp),
        patch.object(MN, "_opp_has_marker", new=_has_marker),
        patch.object(MN, "_find_room_token", new=AsyncMock(return_value="abc12345")),
        patch.object(MN.talk, "list_room_messages", new=AsyncMock(return_value=msgs)),
        patch.object(MN.talk, "download_talk_file", new=AsyncMock(return_value=(b"v", "video/mp4"))),
    ]
    import contextlib

    with contextlib.ExitStack() as st:
        for p in patches_common:
            st.enter_context(p)
        st.enter_context(patch.object(MN, "_transcribe", new=AsyncMock(return_value=None)))
        out = await MN.process_meeting_notes(session_factory=lambda: _Session())
    assert out["errors"] == 1

    with contextlib.ExitStack() as st:
        for p in patches_common:
            st.enter_context(p)
        st.enter_context(patch.object(MN, "_transcribe", new=AsyncMock(return_value="text")))
        st.enter_context(patch.object(MN, "_summarize_minutes", new=AsyncMock(return_value=None)))
        out = await MN.process_meeting_notes(session_factory=lambda: _Session())
    assert out["errors"] == 1


@pytest.mark.asyncio
async def test_process_loop_exception_counts():
    async def _has_marker(oid, marker):
        raise RuntimeError("db gone")

    with (
        patch.object(MN, "_enabled", return_value=True),
        patch.object(MN.espo, "list_all", new=_past_opp),
        patch.object(MN, "_opp_has_marker", new=_has_marker),
    ):
        out = await MN.process_meeting_notes(session_factory=lambda: _Session())
    assert out["errors"] == 1


# ── worker task wrapper ───────────────────────────────────────────────


def test_worker_task_success_and_failure():
    import app.worker.tasks.meeting_notes as TASK

    def _run_async(coro):
        if hasattr(coro, "close"):
            coro.close()
        return {"ensure": {}, "process": {}}

    with patch.object(TASK, "run_async", _run_async):
        assert TASK.run_meeting_notes_sweep() == {"ensure": {}, "process": {}}

    def _boom(coro):
        if hasattr(coro, "close"):
            coro.close()
        raise RuntimeError("x")

    with patch.object(TASK, "run_async", _boom):
        assert TASK.run_meeting_notes_sweep() is None


# ── nextcloud_talk error paths ────────────────────────────────────────


def _no_base_settings():
    return _settings(NEXTCLOUD_BASE_URL="", NEXTCLOUD_DAV_URL="")


class _BoomClient:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _boom(self, *a, **kw):
        raise RuntimeError("conn reset")

    post = _boom
    get = _boom
    delete = _boom


@pytest.mark.asyncio
async def test_talk_no_base_paths():
    with patch.object(TALK, "get_settings", return_value=_no_base_settings()):
        assert await TALK.create_room("x") is None
        assert await TALK.delete_room("abc12345") is False
        assert await TALK.post_to_room("abc12345", "hi") is False
        assert await TALK.list_room_messages("abc12345") == []
        assert await TALK.download_talk_file(123) is None


@pytest.mark.asyncio
async def test_talk_http_and_exception_failures():
    with (
        patch.object(TALK, "get_settings", return_value=_settings()),
        patch.object(TALK.httpx, "AsyncClient", _BoomClient),
    ):
        assert await TALK.create_room("x") is None
        assert await TALK.delete_room("abc12345") is False
        assert await TALK.post_to_room("abc12345", "hi") is False
        assert await TALK.list_room_messages("abc12345") == []
        assert await TALK.download_talk_file(5) is None

    _Client.resp = _Resp(500, {}, content=b"err")
    with (
        patch.object(TALK, "get_settings", return_value=_settings()),
        patch.object(TALK.httpx, "AsyncClient", _Client),
    ):
        assert await TALK.create_room("x") is None
        assert await TALK.delete_room("abc12345") is False
        assert await TALK.post_to_room("abc12345", "hi") is False
        assert await TALK.list_room_messages("abc12345") == []
        assert await TALK.download_talk_file(5) is None


@pytest.mark.asyncio
async def test_list_room_messages_params_and_shapes():
    _Client.resp = _Resp(200, {"ocs": {"data": {"not": "a list"}}})
    with (
        patch.object(TALK, "get_settings", return_value=_settings()),
        patch.object(TALK.httpx, "AsyncClient", _Client),
    ):
        assert await TALK.list_room_messages("abc12345", limit=999, last_known_id=42) == []
    call = _Client.calls[0]
    assert call["params"]["limit"] == "500"
    assert call["params"]["lastKnownMessageId"] == "42"


@pytest.mark.asyncio
async def test_download_talk_file_success():
    _Client.resp = _Resp(200, content=b"bytes", headers={"content-type": "video/mp4; charset=b"})
    with (
        patch.object(TALK, "get_settings", return_value=_settings()),
        patch.object(TALK.httpx, "AsyncClient", _Client),
    ):
        got = await TALK.download_talk_file(42)
    assert got == (b"bytes", "video/mp4")
    assert _Client.calls[0]["url"].endswith("/index.php/f/42")
    # invalid ids
    with patch.object(TALK, "get_settings", return_value=_settings()):
        assert await TALK.download_talk_file(0) is None
        assert await TALK.download_talk_file(-3) is None


def test_find_recording_file_non_dict_params():
    msgs = [
        {"id": 1, "messageParameters": {"file": "not-a-dict"}},
        {"id": 2, "messageParameters": {"file": {"id": 9}}},
    ]
    assert TALK.find_recording_file(msgs) is None


@pytest.mark.asyncio
async def test_find_room_token_opp_notes_hit():
    opp = _opp()
    with patch.object(MN, "_opp_notes", new=AsyncMock(return_value=[{"post": "talk_room:opp77777 — x"}])):
        assert await MN._find_room_token(opp) == "opp77777"


@pytest.mark.asyncio
async def test_ensure_create_room_none_counts_error():
    async def _notes(oid):
        return []

    with (
        patch.object(MN, "_enabled", return_value=True),
        patch.object(MN.espo, "list_all", new=AsyncMock(return_value=[_opp()])),
        patch.object(MN, "_opp_notes", new=_notes),
        patch.object(MN, "_contact_email_for_opp", new=AsyncMock(return_value="e@x.co")),
        patch.object(MN.talk, "create_room", new=AsyncMock(return_value=None)),
    ):
        out = await MN.ensure_consult_rooms(session_factory=lambda: _Session())
    assert out["errors"] == 1
    assert out["created"] == 0


@pytest.mark.asyncio
async def test_transcribe_wav_raises_falls_back_to_raw():
    import app.services.inference as INF

    calls = []

    async def _tr(raw, ctype):
        calls.append(ctype)
        if ctype == "audio/wav":
            raise RuntimeError("whisper wav boom")
        return {"text": "raw fallback"}

    with (
        patch.object(MN, "_extract_wav", new=AsyncMock(return_value=b"wav")),
        patch.object(INF, "transcribe_workers_ai", new=_tr),
    ):
        assert await MN._transcribe((b"raw", "video/mp4")) == "raw fallback"
    assert calls == ["audio/wav", "audio/mp4"]


@pytest.mark.asyncio
async def test_process_skips_opp_with_existing_notes():
    async def _has_marker(oid, marker):
        return True

    with (
        patch.object(MN, "_enabled", return_value=True),
        patch.object(MN.espo, "list_all", new=_past_opp),
        patch.object(MN, "_opp_has_marker", new=_has_marker),
    ):
        out = await MN.process_meeting_notes(session_factory=lambda: _Session())
    assert out["checked"] == 0
