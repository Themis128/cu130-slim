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

    messages = [
        {"id": 5, "messageParameters": {"file": {"id": 77, "name": "recording.mp4"}}}
    ]
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
