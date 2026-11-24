"""Talk call-event ingest tests — Nextcloud HTTP + DB are faked."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import app.services.talk_calls as TC


def _settings(**over):
    base = dict(
        NEXTCLOUD_USERNAME="svc",
        NEXTCLOUD_APP_PASSWORD="app-pw",
        NEXTCLOUD_BASE_URL="https://cloud.example.com",
        NEXTCLOUD_DAV_URL="",
    )
    base.update(over)
    return SimpleNamespace(**base)


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return _Scalars(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    """Returns queued results per execute() call; records added objects."""

    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, *a, **kw):
        rows = self._results.pop(0) if self._results else []
        return _Result(rows)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _sf(db):
    class _SF:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    return _SF()


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


class _Client:
    """Async-context httpx.AsyncClient stand-in; `get` is an AsyncMock."""

    instances = []

    def __init__(self, *a, get_side_effect=None, **kw):
        self.get = AsyncMock(side_effect=get_side_effect or (lambda *a, **k: _Resp()))
        _Client.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _patch_client(get_side_effect=None):
    return patch.object(TC.httpx, "AsyncClient", lambda *a, **k: _Client(get_side_effect=get_side_effect))


# ── pure helpers ────────────────────────────────────────────────────


def test_enabled_requires_both_creds():
    with patch.object(TC, "get_settings", return_value=_settings()):
        assert TC.talk_calls_enabled() is True
    with patch.object(TC, "get_settings", return_value=_settings(NEXTCLOUD_APP_PASSWORD="")):
        assert TC.talk_calls_enabled() is False


def test_base_prefers_base_url_then_dav_host():
    with patch.object(TC, "get_settings", return_value=_settings()):
        assert TC._base() == "https://cloud.example.com"
    with patch.object(
        TC,
        "get_settings",
        return_value=_settings(NEXTCLOUD_BASE_URL="", NEXTCLOUD_DAV_URL="https://dav.example.com/remote.php/dav/files/svc/"),
    ):
        assert TC._base() == "https://dav.example.com"
    with patch.object(TC, "get_settings", return_value=_settings(NEXTCLOUD_BASE_URL="", NEXTCLOUD_DAV_URL="")):
        assert TC._base() == ""


def test_parse_duration_formats():
    assert TC._parse_duration("Duration 1:02:03") == 3723
    assert TC._parse_duration("Duration 12:34") == 754
    assert TC._parse_duration("Duration 05:09") == 309
    assert TC._parse_duration("no duration here") is None
    assert TC._parse_duration("") is None
    assert TC._parse_duration(None) is None


def test_actor_prefers_message_parameters():
    msg = {
        "actorId": "42",
        "actorDisplayName": "fallback",
        "messageParameters": {"actor": {"id": "7", "name": "Jane", "type": "user"}},
    }
    out = TC._actor(msg)
    assert out == {"actor_id": "7", "actor_name": "Jane", "actor_type": "user"}
    bare = TC._actor({"actorId": "9", "actorDisplayName": "Bob", "actorType": "guests"})
    assert bare["actor_id"] == "9" and bare["actor_name"] == "Bob" and bare["actor_type"] == "guests"
    assert TC._actor({}) == {"actor_id": "", "actor_name": "", "actor_type": ""}


# ── _rooms / _chat_messages ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_rooms_merges_db_rows_and_api_list():
    db = _FakeDB([[("tok-db", "team-1")]])
    payload = {"ocs": {"data": [{"token": "tok-api"}, {"token": "tok-db"}, {}]}}

    def _get(url, **kw):
        return _Resp(200, payload)

    with patch.object(TC, "get_settings", return_value=_settings()), _patch_client(_get):
        rooms = await TC._rooms(db)
    by_token = {r["token"]: r["team_id"] for r in rooms}
    assert by_token["tok-db"] == "team-1"
    assert by_token["tok-api"] == ""


@pytest.mark.asyncio
async def test_rooms_swallows_api_failure():
    db = _FakeDB([[("tok-db", "team-1")]])

    async def _boom(*a, **k):
        raise RuntimeError("nc down")

    with patch.object(TC, "get_settings", return_value=_settings()), _patch_client(_boom):
        rooms = await TC._rooms(db)
    assert rooms == [{"token": "tok-db", "team_id": "team-1"}]


@pytest.mark.asyncio
async def test_rooms_skips_api_when_no_base():
    _Client.instances.clear()
    db = _FakeDB([[("tok-db", "team-1")]])
    with patch.object(TC, "get_settings", return_value=_settings(NEXTCLOUD_BASE_URL="", NEXTCLOUD_DAV_URL="")):
        rooms = await TC._rooms(db)
    assert rooms == [{"token": "tok-db", "team_id": "team-1"}]
    assert _Client.instances == []


@pytest.mark.asyncio
async def test_chat_messages_status_and_payload():
    client = _Client(get_side_effect=lambda *a, **k: _Resp(500))
    assert await TC._chat_messages(client, "https://b", "tok") == []

    client2 = _Client(get_side_effect=lambda *a, **k: _Resp(200, {"ocs": {"data": [{"id": 1}]}}))
    out = await TC._chat_messages(client2, "https://b", "tok")
    assert out == [{"id": 1}]

    client3 = _Client(get_side_effect=lambda *a, **k: _Resp(200, {"ocs": {"data": None}}))
    assert await TC._chat_messages(client3, "https://b", "tok") == []


# ── ingest_call_events ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_skipped_when_disabled():
    with patch.object(TC, "get_settings", return_value=_settings(NEXTCLOUD_USERNAME="", NEXTCLOUD_APP_PASSWORD="")):
        out = await TC.ingest_call_events(session_factory=_sf(_FakeDB([])))
    assert out == {"skipped": "nextcloud talk not configured"}


@pytest.mark.asyncio
async def test_ingest_skipped_without_base():
    with patch.object(TC, "get_settings", return_value=_settings(NEXTCLOUD_BASE_URL="", NEXTCLOUD_DAV_URL="")):
        out = await TC.ingest_call_events(session_factory=_sf(_FakeDB([])))
    assert out == {"skipped": "no talk base url"}


@pytest.mark.asyncio
async def test_ingest_skipped_without_teams():
    db = _FakeDB([[], []])  # TalkRoom rows, then Team.id -> None
    with patch.object(TC, "get_settings", return_value=_settings()):
        with _patch_client():
            out = await TC.ingest_call_events(session_factory=_sf(db))
    assert out == {"skipped": "no teams"}


@pytest.mark.asyncio
async def test_ingest_writes_events_and_dedupes():
    team_id = uuid.uuid4()
    msgs = [
        {"id": 11, "systemMessage": "call_started", "timestamp": 1730000000, "message": "", "displayName": "Consult", "actorId": "1"},
        {
            "id": 12,
            "systemMessage": "call_ended",
            "timestamp": 1730000600,
            "message": "Duration 10:00",
            "displayName": "Consult",
            "messageParameters": {"actor": {"id": "2", "name": "Guest", "type": "guests"}},
        },
        {"id": 13, "systemMessage": "chat", "timestamp": 1730000700, "message": "hi"},  # not a call msg
        {"systemMessage": "call_left"},  # no id -> skipped
    ]
    chat_payload = {"ocs": {"data": msgs}}
    room_list_payload = {"ocs": {"data": [{"token": "tok-x"}]}}

    call_n = {"n": 0}

    async def _get(url, **kw):
        call_n["n"] += 1
        # first GET = room list (inside _rooms), second = chat for tok-x
        return _Resp(200, room_list_payload if call_n["n"] == 1 else chat_payload)

    db = _FakeDB(
        [
            [],  # TalkRoom rows
            [team_id],  # Team.id
            ["tok-x:11"],  # existing platform_event_ids
        ]
    )
    with patch.object(TC, "get_settings", return_value=_settings()):
        with _patch_client(_get):
            out = await TC.ingest_call_events(session_factory=_sf(db))

    assert out["rooms"] == 1
    assert out["events"] == 1  # only id 12 is new (11 dup, 13 non-call, no-id skipped)
    assert out["duplicates"] == 1
    assert len(db.added) == 1
    ev = db.added[0]
    assert ev.event_type == "talk_call_call_ended"
    assert ev.platform == "talk"
    assert ev.platform_event_id == "tok-x:12"
    assert ev.team_id == str(team_id)
    assert ev.meta_data["duration_seconds"] == 600
    assert ev.meta_data["actor_name"] == "Guest"
    assert ev.meta_data["room_token"] == "tok-x"
    assert db.commits == 1


@pytest.mark.asyncio
async def test_ingest_counts_chat_errors():
    team_id = uuid.uuid4()

    async def _get(url, **kw):
        raise RuntimeError("boom")

    # TalkRoom row gives a room even though the API room-list call fails
    db = _FakeDB([[("tok-err", "team-1")], [team_id]])
    with patch.object(TC, "get_settings", return_value=_settings()):
        with _patch_client(_get):
            out = await TC.ingest_call_events(session_factory=_sf(db))
    assert out["errors"] == 1
    assert out["rooms"] == 0
    assert out["events"] == 0


@pytest.mark.asyncio
async def test_ingest_room_with_no_call_messages_skips():
    team_id = uuid.uuid4()
    db = _FakeDB([[], [team_id], []])

    async def _get(url, **kw):
        if url.endswith("/room"):
            return _Resp(200, {"ocs": {"data": [{"token": "tok-quiet"}]}})
        return _Resp(200, {"ocs": {"data": [{"id": 1, "systemMessage": "chat", "message": "hi"}]}})

    with patch.object(TC, "get_settings", return_value=_settings()):
        with _patch_client(_get):
            out = await TC.ingest_call_events(session_factory=_sf(db))
    assert out["rooms"] == 1
    assert out["events"] == 0
    assert db.added == []


@pytest.mark.asyncio
async def test_ingest_room_without_team_uses_default():
    team_id = uuid.uuid4()
    msgs = [{"id": 5, "systemMessage": "call_started", "timestamp": None, "message": ""}]
    db = _FakeDB([[], [team_id], []])

    async def _get(url, **kw):
        if url.endswith("/room"):
            return _Resp(200, {"ocs": {"data": [{"token": "tok-y"}]}})
        return _Resp(200, {"ocs": {"data": msgs}})

    with patch.object(TC, "get_settings", return_value=_settings()):
        with _patch_client(_get):
            out = await TC.ingest_call_events(session_factory=_sf(db))
    assert out["events"] == 1
    assert db.added[0].team_id == str(team_id)
    assert db.added[0].meta_data["duration_seconds"] is None


# ── celery task wrapper ─────────────────────────────────────────────


def test_task_delegates_to_service():
    import app.worker.tasks.talk_call_ingest as TASK

    with patch.object(TASK, "ingest_call_events") as m_svc, patch.object(TASK, "run_async", return_value={"events": 3}) as m_run:
        out = TASK.talk_call_ingest()
    m_svc.assert_called_once_with()
    m_run.assert_called_once()
    assert out == {"events": 3}
