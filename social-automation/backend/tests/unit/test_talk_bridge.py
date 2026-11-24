"""Talk-bridge service tests — platform lanes + DB are faked."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import app.api.talk_bridge as API
import app.services.talk_bridge as TB


def _settings(**over):
    base = dict(
        NEXTCLOUD_TALK_NOTIFY=True,
        NEXTCLOUD_TALK_BOT_SECRET="bot-secret",
        TALK_BRIDGE_ENABLED=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _acct(**kw):
    base = dict(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        platform="facebook",
        account_type="page",
        account_id="page-1",
        meta_data={"page_token": "EAtoken"},
        status="active",
        access_token_enc=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _room(**kw):
    base = dict(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        platform="messenger",
        account_id=str(uuid.uuid4()),
        thread_id="t-1",
        contact_name="Jane",
        recipient_id="psid-1",
        is_e2ee=False,
        room_token="room-1",
        enabled=True,
        last_inbound_key=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    """execute() returns queued _Result sets; records commits/flushes."""

    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.commits = 0
        self._nested_fail = False

    async def execute(self, *a, **kw):
        rows = self._results.pop(0) if self._results else []
        return _Result(rows)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1

    def begin_nested(self):
        db = self

        class _N:
            async def __aenter__(self):
                if db._nested_fail:
                    raise RuntimeError("fk boom")
                return db

            async def __aexit__(self, *a):
                return False

        return _N()


def _sf(db):
    class _SF:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    return _SF()


# ── pure helpers ────────────────────────────────────────────────────


def test_bridge_enabled_requires_flag_notify_secret():
    with patch.object(TB, "get_settings", return_value=_settings()):
        assert TB.bridge_enabled() is True
    for over in (
        dict(TALK_BRIDGE_ENABLED=False),
        dict(NEXTCLOUD_TALK_NOTIFY=False),
        dict(NEXTCLOUD_TALK_BOT_SECRET=""),
    ):
        with patch.object(TB, "get_settings", return_value=_settings(**over)):
            assert TB.bridge_enabled() is False


def test_room_name_prefixes_and_truncates():
    assert TB._room_name("messenger", "Jane Doe") == "FB · Jane Doe"
    assert TB._room_name("instagram", "@u") == "IG · @u"
    assert TB._room_name("linkedin", "") == "LI · Unknown"
    assert len(TB._room_name("whatsapp", "x" * 200)) == 100


def test_format_inbound_labels_and_attachments():
    out = TB._format_inbound("Jane", "hi", False, ["img.png"])
    assert out == "**Jane**\nhi\n📎 img.png"
    assert TB._format_inbound("Jane", "hi", True, []) == "**You**\nhi"
    assert "(non-text message)" in TB._format_inbound("Jane", "", False, [])


def test_attachment_urls_only_dict_data():
    assert TB._attachment_urls(None) == []
    assert TB._attachment_urls("x") == []
    out = TB._attachment_urls({"data": [{"image_data": {"url": "u1"}}, {"file_url": "u2"}, {}]})
    assert out == ["u1", "u2", "attachment"]


def test_norm_synthesizes_hash_key_when_no_ids():
    m = TB._norm("", "", "Jane", "hi", False, [])
    assert m["key"].startswith("h_")
    same = TB._norm("", "", "Jane", "hi", False, [])
    assert same["key"] == m["key"]
    diff = TB._norm("", "", "Jane", "hi", True, [])
    assert diff["key"] != m["key"]
    assert TB._norm("ts1", "id1", "Jane", "hi", False)["key"] == "ts1"
    assert TB._norm("", "id1", "Jane", "hi", False)["key"] == "id1"


def test_keyless_newest_first_reverses_and_suffixes_dupes():
    raw = [
        {"ts": "1", "sender": "a", "text": "same"},
        {"ts": "1", "sender": "a", "text": "same"},  # identical key → deduped
        {"ts": "3", "sender": "a", "text": "new"},
        "not-a-dict",
    ]

    def to_msg(m):
        if not isinstance(m, dict):
            return None
        return TB._norm(m["ts"], "", m["sender"], m["text"], False)

    out = TB._keyless_newest_first(raw, to_msg)
    assert [m["text"] for m in out] == ["new", "same", "same"]
    assert out[1]["key"] == "1#2"
    assert out[2]["key"] == "1"


# ── _log_event / _post ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_event_adds_analytics_row():
    db = _FakeDB([])
    room = _room()
    await TB._log_event(db, room, "inbound", msg={"id": "m1", "sender": "Jane", "text": "hey"})
    assert len(db.added) == 1
    ev = db.added[0]
    assert ev.event_type == "talk_bridge_inbound"
    assert ev.platform == "messenger"


@pytest.mark.asyncio
async def test_log_event_savepoint_failure_is_swallowed():
    db = _FakeDB([])
    db._nested_fail = True
    await TB._log_event(db, _room(), "inbound", msg={})
    assert db.added == []


@pytest.mark.asyncio
async def test_post_prefers_bot_reply_then_fallback():
    room = _room()
    with (
        patch.object(TB, "bot_reply", return_value=None) as _br,
        patch.object(TB, "post_to_room", return_value=None) as _pr,
    ):
        async def bot_ok(*a, **kw):
            return True

        async def post_fail(*a, **kw):
            return False

        with patch.object(TB, "bot_reply", bot_ok):
            assert await TB._post(room, "x") is True

        async def bot_no(*a, **kw):
            return False

        async def post_yes(*a, **kw):
            return True

        with patch.object(TB, "bot_reply", bot_no), patch.object(TB, "post_to_room", post_yes):
            assert await TB._post(room, "x") is True
        with patch.object(TB, "bot_reply", bot_no), patch.object(TB, "post_to_room", post_fail):
            assert await TB._post(room, "x") is False


@pytest.mark.asyncio
async def test_room_for_token_queries_by_token():
    room = _room(room_token="tok-9")
    db = _FakeDB([[room]])
    with patch.object(TB, "async_session_maker", _sf(db)):
        found = await TB.room_for_token("tok-9")
    assert found is room


# ── inbound_sync ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inbound_sync_posts_unseen_and_advances_watermark():
    acct = _acct()
    room = _room(account_id=str(acct.id), last_inbound_key="old-1")
    db = _FakeDB([[room], [acct]])

    msgs = [
        {"key": "n1", "id": "n1", "sender": "Jane", "text": "new1", "ours": False, "attachments": []},
        {"key": "old-1", "id": "old-1", "sender": "Jane", "text": "seen", "ours": False, "attachments": []},
    ]
    posted = []

    async def fetch(a, r):
        return msgs

    async def post(r, text, db=None):
        posted.append(text)
        return True

    with (
        patch.object(TB, "_fetch_messages", fetch),
        patch.object(TB, "_post", post),
        patch.object(TB, "_log_event", return_value=None),
    ):
        out = await TB.inbound_sync(uuid.uuid4(), session_factory=_sf(db))
    assert out == {"posted": 1}
    assert posted == ["**Jane**\nnew1"]
    assert room.last_inbound_key == "n1"


@pytest.mark.asyncio
async def test_inbound_sync_failed_post_keeps_watermark():
    acct = _acct()
    room = _room(account_id=str(acct.id))
    db = _FakeDB([[room], [acct]])

    msgs = [
        {"key": "n2", "id": "n2", "sender": "Jane", "text": "newest", "ours": False, "attachments": []},
        {"key": "n1", "id": "n1", "sender": "Jane", "text": "older", "ours": False, "attachments": []},
    ]
    calls = []

    async def fetch(a, r):
        return msgs

    async def flaky(r, text, db=None):
        calls.append(text)
        return len(calls) < 2  # second (newest) post fails

    with (
        patch.object(TB, "_fetch_messages", fetch),
        patch.object(TB, "_post", flaky),
        patch.object(TB, "_log_event", return_value=None),
    ):
        await TB.inbound_sync(uuid.uuid4(), session_factory=_sf(db))
    assert room.last_inbound_key == "n1"


@pytest.mark.asyncio
async def test_inbound_sync_skips_when_no_messages():
    acct = _acct()
    room = _room(account_id=str(acct.id))
    db = _FakeDB([[room], [acct]])

    async def fetch(a, r):
        return []

    with patch.object(TB, "_fetch_messages", fetch):
        out = await TB.inbound_sync(uuid.uuid4(), session_factory=_sf(db))
    assert out == {"posted": 0}


# ── outbound ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_outbound_disabled_and_unmapped():
    with patch.object(TB, "get_settings", return_value=_settings(TALK_BRIDGE_ENABLED=False)):
        assert await TB.outbound("r", "hi") == (False, "bridge disabled")

    db = _FakeDB([[]])
    with (
        patch.object(TB, "get_settings", return_value=_settings()),
        patch.object(TB, "async_session_maker", _sf(db)),
    ):
        assert await TB.outbound("r", "hi") == (False, "not a bridged room")


@pytest.mark.asyncio
async def test_outbound_muted_room():
    room = _room(enabled=False)
    db = _FakeDB([[room]])
    with (
        patch.object(TB, "get_settings", return_value=_settings()),
        patch.object(TB, "async_session_maker", _sf(db)),
    ):
        assert await TB.outbound("room-1", "hi") == (False, "bridge muted for this room")


@pytest.mark.asyncio
async def test_outbound_messenger_sends_and_logs():
    room = _room()
    acct = _acct(id=uuid.UUID(room.account_id))
    lookup_db = _FakeDB([[room]])
    acct_db = _FakeDB([[acct]])
    events_db = _FakeDB([])
    dbs = iter([lookup_db, events_db])

    class _Next:
        def __call__(self):
            return _sf(next(dbs))()

    sent = []

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def send_text(self, recipient, text):
            sent.append((recipient, text))

    from app.services import messenger_api

    with (
        patch.object(TB, "get_settings", return_value=_settings()),
        patch.object(TB, "async_session_maker", _Next()),
        patch.object(messenger_api, "MessengerAPIClient", _Client),
    ):
        ok, detail = await TB.outbound("room-1", "hello", session_factory=_sf(acct_db))
    assert ok is True, detail
    assert sent == [("psid-1", "hello")]
    assert any(getattr(e, "event_type", "") == "talk_bridge_outbound" for e in events_db.added)


@pytest.mark.asyncio
async def test_outbound_instagram_needs_recipient():
    room = _room(platform="instagram", recipient_id="")
    acct = _acct(id=uuid.UUID(room.account_id), platform="instagram")
    lookup_db = _FakeDB([[room]])
    acct_db = _FakeDB([[acct]])
    dbs = iter([lookup_db])

    class _Next:
        def __call__(self):
            return _sf(next(dbs))()

    with (
        patch.object(TB, "get_settings", return_value=_settings()),
        patch.object(TB, "async_session_maker", _Next()),
        patch.object(TB, "_ig_client", return_value=SimpleNamespace()),
    ):
        ok, detail = await TB.outbound("room-1", "hi", session_factory=_sf(acct_db))
    assert ok is False
    assert "recipient" in detail


@pytest.mark.asyncio
async def test_outbound_personal_messenger_session_inactive():
    room = _room(platform="personal_messenger")
    acct = _acct(id=uuid.UUID(room.account_id), account_type="user")
    lookup_db = _FakeDB([[room]])
    acct_db = _FakeDB([[acct]])
    dbs = iter([lookup_db])

    class _Next:
        def __call__(self):
            return _sf(next(dbs))()

    class _Bridge:
        def __init__(self, *a, **kw):
            pass

        async def ensure_session(self, platform):
            return {"status": "dead", "message": "no login"}

    from app.services import browser_bridge

    with (
        patch.object(TB, "get_settings", return_value=_settings()),
        patch.object(TB, "async_session_maker", _Next()),
        patch.object(browser_bridge, "BrowserBridgeClient", _Bridge),
    ):
        ok, detail = await TB.outbound("room-1", "hi", session_factory=_sf(acct_db))
    assert ok is False
    assert detail == "no login"


@pytest.mark.asyncio
async def test_outbound_unknown_platform():
    room = _room(platform="whatsapp")
    acct = _acct(id=uuid.UUID(room.account_id), platform="whatsapp")
    lookup_db = _FakeDB([[room]])
    acct_db = _FakeDB([[acct]])
    dbs = iter([lookup_db])

    class _Next:
        def __call__(self):
            return _sf(next(dbs))()

    with (
        patch.object(TB, "get_settings", return_value=_settings()),
        patch.object(TB, "async_session_maker", _Next()),
    ):
        ok, detail = await TB.outbound("room-1", "hi", session_factory=_sf(acct_db))
    assert ok is False
    assert "not bridged" in detail


# ── sync_rooms ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_rooms_creates_and_maps():
    acct = _acct()
    conv = {
        "thread_id": "t-9",
        "contact_name": "Bob",
        "recipient_id": "psid-9",
        "is_e2ee": False,
        "platform": "messenger",
    }
    db = _FakeDB([[acct], []])  # accounts, then _room_for → None

    async def convs(a):
        return [conv]

    async def mkroom(name, room_type=0):
        return "tok-new"

    async def post(r, text, db=None):
        return True

    with (
        patch.object(TB, "_conversation_list", convs),
        patch.object(TB, "create_room", mkroom),
        patch.object(TB, "_post", post),
    ):
        out = await TB.sync_rooms(uuid.uuid4(), session_factory=_sf(db))
    assert out == {"created": 1, "mapped": 0}
    assert db.added and db.added[0].room_token == "tok-new"


@pytest.mark.asyncio
async def test_sync_rooms_rebinds_existing():
    acct = _acct()
    room = _room(account_id=str(uuid.uuid4()), contact_name="Old", recipient_id="")
    conv = {
        "thread_id": room.thread_id,
        "contact_name": "New",
        "recipient_id": "psid-9",
        "is_e2ee": False,
        "platform": "messenger",
    }
    db = _FakeDB([[acct], [room]])

    async def convs(a):
        return [conv]

    with patch.object(TB, "_conversation_list", convs):
        out = await TB.sync_rooms(uuid.uuid4(), session_factory=_sf(db))
    assert out == {"created": 0, "mapped": 1}
    assert room.contact_name == "New"
    assert room.account_id == str(acct.id)
    assert room.recipient_id == "psid-9"


# ── fetchers ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_messages_none_account_and_dispatch():
    room = _room(platform="messenger")
    assert await TB._fetch_messages(None, room) == []

    async def fb(a, t):
        return [{"key": "1"}]

    with patch.object(TB, "_fb_page_messages", fb):
        assert await TB._fetch_messages(_acct(), room) == [{"key": "1"}]

    async def boom(a, t):
        raise RuntimeError("down")

    with patch.object(TB, "_fb_page_messages", boom):
        assert await TB._fetch_messages(_acct(), room) == []


@pytest.mark.asyncio
async def test_fb_personal_messages_drops_mismatched_thread():
    acct = _acct(account_type="user")

    class _Bridge:
        def __init__(self, *a, **kw):
            pass

        async def ensure_session(self, p):
            return {"status": "active"}

        async def get_personal_messenger_messages(self, tid, is_e2ee=False):
            return {"thread_id": "other", "messages": [{"text": "x"}]}

    from app.services import browser_bridge

    with patch.object(browser_bridge, "BrowserBridgeClient", _Bridge):
        assert await TB._fb_personal_messages(acct, "t-1", False) == []


@pytest.mark.asyncio
async def test_fb_personal_messages_normalizes_chronological():
    acct = _acct(account_type="user")

    class _Bridge:
        def __init__(self, *a, **kw):
            pass

        async def ensure_session(self, p):
            return {"status": "active"}

        async def get_personal_messenger_messages(self, tid, is_e2ee=False):
            return {
                "thread_id": "t-1",
                "messages": [
                    {"ts": "1", "sender": "Jane", "text": "first"},
                    {"ts": "2", "sender": "me", "text": "second", "is_own": True},
                ],
            }

    from app.services import browser_bridge

    with patch.object(browser_bridge, "BrowserBridgeClient", _Bridge):
        out = await TB._fb_personal_messages(acct, "t-1", False)
    assert [m["text"] for m in out] == ["second", "first"]
    assert out[0]["ours"] is True


@pytest.mark.asyncio
async def test_li_messages_wraps_keyless():
    acct = _acct(platform="linkedin", display_name="Me Myself")

    class _Sidecar:
        async def get_thread_messages(self, tid):
            return {"messages": [
                {"ts": "1", "sender": "Ann", "text": "theirs"},
                {"ts": "2", "sender": "Me Myself", "text": "mine"},
                {"ts": "3", "sender": "Third Person", "text": "group"},
            ]}

        async def get_profile(self):
            return {"name": "Me Myself"}

    from app.services import linkedin_sidecar

    TB._li_self["name"] = None  # reset cache
    with patch.object(linkedin_sidecar, "LinkedInSidecarClient", lambda: _Sidecar()):
        out = await TB._li_messages(acct, "t-1")
    assert [m["text"] for m in out] == ["group", "mine", "theirs"]
    # only the exact own-name match is ours — group members stay not-ours
    assert [m["ours"] for m in out] == [False, True, False]


@pytest.mark.asyncio
async def test_li_messages_mismatched_self_name_disables_attribution():
    """Scraped 'Alex Chen Jr.' for account 'Alex Chen' must NOT count as ours."""
    acct = _acct(platform="linkedin", display_name="Alex Chen")

    class _Sidecar:
        async def get_thread_messages(self, tid):
            return {"messages": [{"ts": "1", "sender": "Alex Chen Jr.", "text": "hi"}]}

        async def get_profile(self):
            return {"name": "Alex Chen Jr."}

    from app.services import linkedin_sidecar

    TB._li_self["name"] = None
    try:
        with patch.object(linkedin_sidecar, "LinkedInSidecarClient", lambda: _Sidecar()):
            out = await TB._li_messages(acct, "t-1")
    finally:
        TB._li_self["name"] = None
        TB._li_self["at"] = 0.0
    assert out[0]["ours"] is False


# ── _conversation_list ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_conversation_list_page():
    acct = _acct()

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def get_conversations(self, platform=None, limit=0):
            return [
                {
                    "id": "c1",
                    "participants": {"data": [{"id": "page-1", "name": "me"}, {"id": "u1", "name": "Jane"}]},
                }
            ]

    from app.services import messenger_api

    with patch.object(messenger_api, "MessengerAPIClient", _Client):
        out = await TB._conversation_list(acct)
    assert out == [
        {
            "thread_id": "c1",
            "contact_name": "Jane",
            "recipient_id": "u1",
            "is_e2ee": False,
            "platform": "messenger",
        }
    ]


@pytest.mark.asyncio
async def test_conversation_list_personal_and_error():
    acct = _acct(account_type="user")

    class _Bridge:
        def __init__(self, *a, **kw):
            pass

        async def get_personal_messenger_conversations(self):
            return {"conversations": [{"thread_id": "t9", "name": "Bob", "e2ee": True}]}

    from app.services import browser_bridge

    with patch.object(browser_bridge, "BrowserBridgeClient", _Bridge):
        out = await TB._conversation_list(acct)
    assert out[0]["platform"] == "personal_messenger"
    assert out[0]["is_e2ee"] is True

    class _Dead:
        def __init__(self, *a, **kw):
            raise RuntimeError("nope")

    with patch.object(browser_bridge, "BrowserBridgeClient", _Dead):
        assert await TB._conversation_list(acct) == []


@pytest.mark.asyncio
async def test_conversation_list_linkedin():
    acct = _acct(platform="linkedin")

    class _Sidecar:
        async def get_conversations(self):
            return {"conversations": [
                {"id": "li-1", "name": "Ann"},
                # sidecar falls back to the raw thread URL when regex misses
                {"thread_id": "https://linkedin.com/messaging/thread/2y77x/", "name": "Bob"},
            ]}

    from app.services import linkedin_sidecar

    with patch.object(linkedin_sidecar, "LinkedInSidecarClient", lambda: _Sidecar()):
        out = await TB._conversation_list(acct)
    assert out[0]["thread_id"] == "li-1"
    assert out[0]["platform"] == "linkedin"
    assert out[1]["thread_id"] == "2y77x"
    assert out[1]["recipient_id"] == "2y77x"


@pytest.mark.asyncio
async def test_conversation_list_instagram():
    acct = _acct(platform="instagram", meta_data={"access_token": "EAx", "ig_user_id": "ig-me"})

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def get_conversations(self, limit=0):
            return {
                "data": [
                    {
                        "id": "igc1",
                        "participants": {"data": [{"id": "ig-me"}, {"id": "u2", "username": "jane"}]},
                    }
                ]
            }

    from app.services import instagram_api

    with (
        patch.object(instagram_api, "InstagramAPIClient", _Client),
        patch.object(TB, "_ig_client", return_value=_Client()),
    ):
        out = await TB._conversation_list(acct)
    assert out[0]["contact_name"] == "jane"
    assert out[0]["recipient_id"] == "u2"
    assert out[0]["platform"] == "instagram"


# ── _ig_client / _ig_messages / _fb_page_messages ───────────────────


def test_ig_client_prefers_access_token_enc():
    acct = _acct(platform="instagram", access_token_enc="enc-tok", meta_data={"ig_user_id": "me"})

    from app.core import security
    from app.services import instagram_api

    captured = {}
    decrypt_args = []

    class _Client:
        def __init__(self, access_token, ig_user_id, use_business_login_api):
            captured.update(token=access_token, uid=ig_user_id, biz=use_business_login_api)

    def _dec(enc):
        decrypt_args.append(enc)
        assert isinstance(enc, bytes)
        return "dec-" + enc.decode()

    with (
        patch.object(security, "decrypt_token", _dec),
        patch.object(instagram_api, "InstagramAPIClient", _Client),
    ):
        TB._ig_client(acct)
    assert captured == {"token": "dec-enc-tok", "uid": "me", "biz": False}
    assert decrypt_args == [b"enc-tok"]


@pytest.mark.asyncio
async def test_ig_messages_normalizes():
    acct = _acct(platform="instagram", meta_data={"ig_user_id": "ig-me"})

    class _Client:
        async def get_dm_messages(self, tid, limit=0):
            return {
                "data": [
                    {
                        "id": "m1",
                        "created_time": "2026-01-01",
                        "from": {"id": "ig-me", "username": "me"},
                        "message": "ours",
                    },
                    {
                        "id": "m2",
                        "created_time": "2026-01-02",
                        "from": {"id": "u2", "username": "jane"},
                        "message": "theirs",
                    },
                ]
            }

    with patch.object(TB, "_ig_client", return_value=_Client()):
        out = await TB._ig_messages(acct, "t-1")
    assert out[0]["ours"] is True
    assert out[1]["sender"] == "jane"


@pytest.mark.asyncio
async def test_fb_page_messages_marks_ours():
    acct = _acct()

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def get_conversation_messages(self, tid, limit=0):
            return [
                {
                    "id": "m1",
                    "created_time": "t",
                    "from": {"id": "page-1", "name": "Page"},
                    "message": "out",
                    "attachments": {"data": [{"image_data": {"url": "u"}}]},
                }
            ]

    from app.services import messenger_api

    with patch.object(messenger_api, "MessengerAPIClient", _Client):
        out = await TB._fb_page_messages(acct, "t-1")
    assert out[0]["ours"] is True
    assert out[0]["attachments"] == ["u"]


# ── coverage batch 2 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_log_event_bad_account_id_uuid():
    db = _FakeDB([])
    room = _room(account_id="not-a-uuid")
    await TB._log_event(db, room, "inbound", msg={"id": "x", "text": "t"})
    assert db.added[0].social_account_id is None


def test_attachment_urls_skips_non_dict_items():
    out = TB._attachment_urls({"data": ["junk", {"file_url": "u1"}, {"image_data": {"url": "u2"}}]})
    assert out == ["u1", "u2"]


@pytest.mark.asyncio
async def test_fb_personal_inactive_and_nonlist():
    acct = _acct()

    class _B:
        def __init__(self, *a, **kw):
            pass
        async def ensure_session(self, platform):
            return {"status": "inactive"}
    with patch("app.services.browser_bridge.BrowserBridgeClient", _B):
        assert await TB._fb_personal_messages(acct, "t1", False) == []

    class _B2(_B):
        async def ensure_session(self, platform):
            return {"status": "active"}
        async def get_personal_messenger_messages(self, tid, is_e2ee=False):
            return {"messages": "not-a-list"}
    with patch("app.services.browser_bridge.BrowserBridgeClient", _B2):
        assert await TB._fb_personal_messages(acct, "t1", False) == []

    class _B3(_B2):
        async def get_personal_messenger_messages(self, tid, is_e2ee=False):
            return {"messages": ["junk", {"id": "m1", "sender": "Bob", "text": "hi"}]}
    with patch("app.services.browser_bridge.BrowserBridgeClient", _B3):
        out = await TB._fb_personal_messages(acct, "t1", False)
    assert len(out) == 1 and out[0]["sender"] == "Bob"


@pytest.mark.asyncio
async def test_ig_messages_nonlist_and_ours():
    acct = _acct(platform="instagram", meta_data={"ig_user_id": "999"})

    class _C:
        async def get_dm_messages(self, tid, limit=0):
            return {"data": "nope"}
    with patch.object(TB, "_ig_client", lambda a: _C()):
        assert await TB._ig_messages(acct, "t1") == []

    class _C2:
        async def get_dm_messages(self, tid, limit=0):
            return {"data": [{"id": "m1", "from": {"id": "999", "username": "me"}, "message": "yo"},
                             {"id": "m2", "from": {"id": "5", "name": "them"}, "message": "hi"}]}
    with patch.object(TB, "_ig_client", lambda a: _C2()):
        out = await TB._ig_messages(acct, "t1")
    assert out[0]["ours"] is True and out[1]["ours"] is False


@pytest.mark.asyncio
async def test_li_self_name_cache_hit_and_error():
    calls = []

    class _S:
        async def get_profile(self):
            calls.append(1)
            return {"name": "Me"}
    TB._li_self["name"] = None
    TB._li_self["at"] = 0.0
    try:
        assert await TB._li_self_name(_S()) == "Me"
        assert await TB._li_self_name(_S()) == "Me"  # cache hit — no second call
        assert len(calls) == 1
        TB._li_self["at"] = 0.0  # force expiry

        class _SBad:
            async def get_profile(self):
                raise RuntimeError("boom")
        assert await TB._li_self_name(_SBad()) == ""
    finally:
        TB._li_self["name"] = None
        TB._li_self["at"] = 0.0


@pytest.mark.asyncio
async def test_li_messages_nonlist_and_is_own_flag():
    acct = _acct(platform="linkedin", display_name="Me")

    class _S:
        async def get_thread_messages(self, tid):
            return {"messages": "nope"}
    from app.services import linkedin_sidecar
    with patch.object(linkedin_sidecar, "LinkedInSidecarClient", lambda: _S()):
        assert await TB._li_messages(acct, "t1") == []

    class _S2:
        async def get_thread_messages(self, tid):
            return {"messages": ["junk", {"ts": "1", "sender": "X", "text": "hi", "is_own": True}]}
        async def get_profile(self):
            return {"name": "Me"}
    TB._li_self["name"] = None
    try:
        with patch.object(linkedin_sidecar, "LinkedInSidecarClient", lambda: _S2()):
            out = await TB._li_messages(acct, "t1")
    finally:
        TB._li_self["name"] = None
        TB._li_self["at"] = 0.0
    assert len(out) == 1 and out[0]["ours"] is True


@pytest.mark.asyncio
async def test_fetch_messages_exception_returns_empty():
    acct = _acct(platform="facebook")
    room = _room(platform="messenger")
    with patch.object(TB, "_fb_page_messages", side_effect=RuntimeError("down")):
        assert await TB._fetch_messages(acct, room) == []


@pytest.mark.asyncio
async def test_conversation_list_ig_and_li_nonlist():
    acct = _acct(platform="instagram", meta={})
    class _C:
        async def get_conversations(self, limit=0):
            return {"data": "nope"}
    with patch.object(TB, "_ig_client", lambda a: _C()):
        assert await TB._conversation_list(acct) == []

    acct2 = _acct(platform="linkedin")
    class _S:
        async def get_conversations(self):
            return {"conversations": "nope"}
    from app.services import linkedin_sidecar
    with patch.object(linkedin_sidecar, "LinkedInSidecarClient", lambda: _S()):
        assert await TB._conversation_list(acct2) == []

    class _S3:
        async def get_conversations(self):
            return {"conversations": [{"name": "Ann", "id": "https://x/y/123/"}]}
    with patch.object(linkedin_sidecar, "LinkedInSidecarClient", lambda: _S3()):
        out = await TB._conversation_list(acct2)
    assert out[0]["thread_id"] == "123"


@pytest.mark.asyncio
async def test_inbound_sync_seen_all_and_no_account():
    room = _room(last_inbound_key="k1")
    acct = _acct()
    db = _FakeDB([[room], [acct]])
    with patch.object(TB, "_fetch_messages", AsyncMock(return_value=[{"id": "k1", "key": "k1", "sender": "a", "text": "t", "ours": False, "attachments": []}])):
        out = await TB.inbound_sync(uuid.uuid4(), session_factory=_sf(db))
    assert out["posted"] == 0

    room2 = _room(account_id=str(uuid.uuid4()))  # account row missing
    db2 = _FakeDB([[room2], []])
    out2 = await TB.inbound_sync(uuid.uuid4(), session_factory=_sf(db2))
    assert out2["posted"] == 0


@pytest.mark.asyncio
async def test_outbound_personal_send_rejected_and_linkedin():
    room = _room(platform="personal_messenger")
    acct = _acct(id=uuid.UUID(room.account_id))
    db = _FakeDB([[acct]])
    with (
        patch.object(TB, "get_settings", return_value=_settings()),
        patch.object(TB, "room_for_token", AsyncMock(return_value=room)),
        patch.object(TB, "async_session_maker", _sf(_FakeDB([]))),
    ):
        class _B:
            def __init__(self, *a, **kw):
                pass
            async def ensure_session(self, p):
                return {"status": "active"}
            async def send_personal_messenger_message(self, tid, text, is_e2ee=False):
                return {"status": "nope"}
        with patch("app.services.browser_bridge.BrowserBridgeClient", _B):
            ok, msg = await TB.outbound("tok", "hi", session_factory=_sf(db))
    assert ok is False and "send failed" in msg

    room2 = _room(platform="linkedin")
    acct2 = _acct(id=uuid.UUID(room2.account_id), platform="linkedin")
    db2 = _FakeDB([[acct2]])
    with (
        patch.object(TB, "get_settings", return_value=_settings()),
        patch.object(TB, "room_for_token", AsyncMock(return_value=room2)),
        patch.object(TB, "async_session_maker", _sf(_FakeDB([]))),
    ):
        class _S:
            async def send_message(self, tid, text):
                raise RuntimeError("li down")
        from app.services import linkedin_sidecar
        with patch.object(linkedin_sidecar, "LinkedInSidecarClient", lambda: _S()):
            ok2, msg2 = await TB.outbound("tok", "hi", session_factory=_sf(db2))
    assert ok2 is False and "li down" in msg2


@pytest.mark.asyncio
async def test_outbound_account_missing():
    room = _room()
    db = _FakeDB([[]])
    with (
        patch.object(TB, "get_settings", return_value=_settings()),
        patch.object(TB, "room_for_token", AsyncMock(return_value=room)),
    ):
        ok, msg = await TB.outbound("tok", "hi", session_factory=_sf(db))
    assert ok is False and msg == "account row missing"


@pytest.mark.asyncio
async def test_worker_run_disabled_and_per_team():
    from app.worker.tasks import talk_bridge as task_mod
    with patch.object(task_mod, "bridge_enabled", return_value=False):
        assert await task_mod._run() == {"skipped": "talk bridge disabled"}

    db = _FakeDB([[uuid.uuid4(), uuid.uuid4()]])  # two team ids
    with (
        patch.object(task_mod, "bridge_enabled", return_value=True),
        patch("app.worker._async.task_session", _sf(db)),
        patch.object(task_mod, "sync_rooms", AsyncMock(return_value={"created": 1, "mapped": 0})),
        patch.object(task_mod, "inbound_sync", AsyncMock(side_effect=[{"posted": 2}, RuntimeError("db down")])),
    ):
        out = await task_mod._run()
    assert out["teams"] == 2
    assert out["results"][0]["posted"] == 2
    assert "db down" in out["results"][1]["error"]


# ── coverage batch 3 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fb_page_messages_decrypts_non_ea_token():
    acct = _acct(meta_data={"page_token": "enc:secret"})

    class _C:
        def __init__(self, access_token, page_id):
            self.access_token = access_token
        async def get_conversation_messages(self, tid, limit=0):
            return [{"id": "m1", "from": {"id": "page-1", "name": "Page"}, "message": "hi"}]
    from app.services import messenger_api
    with (
        patch.object(messenger_api, "MessengerAPIClient", _C),
        patch("app.core.security.decrypt_token", return_value="EAtoken"),
    ):
        out = await TB._fb_page_messages(acct, "t1")
    assert out[0]["ours"] is True

    # decrypt raises → raw token still used
    with (
        patch.object(messenger_api, "MessengerAPIClient", _C),
        patch("app.core.security.decrypt_token", side_effect=RuntimeError("bad")),
    ):
        out = await TB._fb_page_messages(acct, "t1")
    assert out[0]["ours"] is True


@pytest.mark.asyncio
async def test_ig_client_decrypt_and_fallback_paths():
    acct = _acct(platform="instagram", access_token_enc=b"enc", meta_data={})
    from app.services import instagram_api

    class _C:
        def __init__(self, access_token, ig_user_id, use_business_login_api):
            self.access_token = access_token

    with (
        patch.object(instagram_api, "InstagramAPIClient", _C),
        patch("app.core.security.decrypt_token", side_effect=RuntimeError("bad")),
        patch.dict("os.environ", {"INSTAGRAM_USE_BUSINESS_LOGIN_API": "true"}),
    ):
        client = TB._ig_client(acct)
    assert client.access_token == ""  # decrypt failed, no meta token

    acct2 = _acct(platform="instagram", access_token_enc=None,
                  meta_data={"access_token": "opaque"})
    with (
        patch.object(instagram_api, "InstagramAPIClient", _C),
        patch("app.core.security.decrypt_token", return_value="IGAtok"),
        patch.dict("os.environ", {"INSTAGRAM_USE_BUSINESS_LOGIN_API": "true"}),
    ):
        client2 = TB._ig_client(acct2)
    assert client2.access_token == "IGAtok"


@pytest.mark.asyncio
async def test_li_messages_no_self_name_marks_none_ours():
    acct = _acct(platform="linkedin", display_name="Me")

    class _S:
        async def get_thread_messages(self, tid):
            return {"messages": [{"ts": "1", "sender": "Me", "text": "hi"}]}
        async def get_profile(self):
            raise RuntimeError("scrape down")
    from app.services import linkedin_sidecar
    TB._li_self["name"] = None
    TB._li_self["at"] = 0.0
    try:
        with patch.object(linkedin_sidecar, "LinkedInSidecarClient", lambda: _S()):
            out = await TB._li_messages(acct, "t1")
    finally:
        TB._li_self["name"] = None
        TB._li_self["at"] = 0.0
    assert out[0]["ours"] is False


@pytest.mark.asyncio
async def test_fetch_messages_dispatches_all_platforms():
    acct = _acct()
    with (
        patch.object(TB, "_fb_personal_messages", AsyncMock(return_value=[{"id": "x"}])) as pm,
        patch.object(TB, "_ig_messages", AsyncMock(return_value=[{"id": "x"}])) as ig,
        patch.object(TB, "_li_messages", AsyncMock(return_value=[{"id": "x"}])) as li,
    ):
        assert await TB._fetch_messages(acct, _room(platform="personal_messenger")) == [{"id": "x"}]
        assert await TB._fetch_messages(acct, _room(platform="instagram")) == [{"id": "x"}]
        assert await TB._fetch_messages(acct, _room(platform="linkedin")) == [{"id": "x"}]
    assert pm.called and ig.called and li.called


@pytest.mark.asyncio
async def test_conversation_list_page_decrypt_and_personal_filter():
    acct = _acct(meta_data={"page_token": "enc:t"})

    class _C:
        def __init__(self, *a, **kw):
            pass
        async def get_conversations(self, platform=None, limit=0):
            return [{"id": "c1", "participants": {"data": [{"id": "page-1"}, {"id": "u2", "name": "Ann"}]}}]
    from app.services import messenger_api
    with (
        patch.object(messenger_api, "MessengerAPIClient", _C),
        patch("app.core.security.decrypt_token", return_value="EA"),
    ):
        out = await TB._conversation_list(acct)
    assert out[0]["recipient_id"] == "u2"

    acct2 = _acct(account_type="user")

    class _B:
        def __init__(self, *a, **kw):
            pass
        async def get_personal_messenger_conversations(self):
            return {"conversations": ["junk", {"thread_id": "t9", "name": "Bob"}]}
    with patch("app.services.browser_bridge.BrowserBridgeClient", _B):
        out2 = await TB._conversation_list(acct2)
    assert out2 == [{"thread_id": "t9", "contact_name": "Bob", "recipient_id": "t9",
                     "is_e2ee": False, "platform": "personal_messenger"}]


@pytest.mark.asyncio
async def test_sync_rooms_skips_empty_thread_and_failed_create():
    acct = _acct()
    db = _FakeDB([[acct]])
    convs = [{"thread_id": "", "contact_name": "X", "recipient_id": "", "is_e2ee": False, "platform": "messenger"},
             {"thread_id": "t2", "contact_name": "Y", "recipient_id": "u", "is_e2ee": False, "platform": "messenger"}]
    with (
        patch.object(TB, "_conversation_list", AsyncMock(return_value=convs)),
        patch.object(TB, "create_room", AsyncMock(return_value="")),
    ):
        out = await TB.sync_rooms(uuid.uuid4(), session_factory=_sf(db))
    assert out == {"created": 0, "mapped": 0}


@pytest.mark.asyncio
async def test_sync_rooms_rebind_updates_contact_and_recipient():
    acct = _acct()
    room = _room(account_id="other", contact_name="Old", recipient_id="", last_inbound_key=None)
    db = _FakeDB([[acct], [room]])  # hmm: _room_for queries inside — fake returns room
    convs = [{"thread_id": "t-1", "contact_name": "New", "recipient_id": "psid-9",
              "is_e2ee": False, "platform": "messenger"}]
    with (
        patch.object(TB, "_conversation_list", AsyncMock(return_value=convs)),
        patch.object(TB, "_room_for", AsyncMock(return_value=room)),
    ):
        out = await TB.sync_rooms(uuid.uuid4(), session_factory=_sf(db))
    assert out == {"created": 0, "mapped": 1}
    assert room.contact_name == "New" and room.recipient_id == "psid-9"
    assert room.account_id == str(acct.id)


@pytest.mark.asyncio
async def test_outbound_muted_and_success_analytics_failure():
    room = _room(enabled=False)
    with (
        patch.object(TB, "get_settings", return_value=_settings()),
        patch.object(TB, "room_for_token", AsyncMock(return_value=room)),
    ):
        ok, msg = await TB.outbound("tok", "hi", session_factory=_sf(_FakeDB([])))
    assert ok is False and msg == "bridge muted for this room"

    # success path tolerates analytics write failure
    room2 = _room()
    acct = _acct(id=uuid.UUID(room2.account_id))
    db = _FakeDB([[acct]])
    sent = []

    class _C:
        def __init__(self, *a, **kw):
            pass
        async def send_text(self, recipient, text):
            sent.append(text)
    from app.services import messenger_api
    with (
        patch.object(TB, "get_settings", return_value=_settings()),
        patch.object(TB, "room_for_token", AsyncMock(return_value=room2)),
        patch.object(TB, "async_session_maker", _sf(_FakeDB([]))),
        patch.object(messenger_api, "MessengerAPIClient", _C),
        patch.object(TB, "_log_event", AsyncMock(side_effect=RuntimeError("db gone"))),
    ):
        ok2, msg2 = await TB.outbound("tok", "hi", session_factory=_sf(db))
    assert ok2 is True and msg2 == "sent" and sent == ["hi"]


# ── coverage batch 4: API endpoints + last service branches ────────




@pytest.mark.asyncio
async def test_api_status_and_rooms_list():
    rooms = [_room(platform="messenger", enabled=True),
             _room(platform="instagram", enabled=True),
             _room(platform="instagram", enabled=False)]
    db = _FakeDB([rooms])
    team = uuid.uuid4()
    with patch.object(API, "bridge_enabled", return_value=True):
        out = await API.talk_bridge_status(team_id=team, db=db, _user=None)
    assert out == {"enabled": True, "rooms": 3, "active_rooms": 2,
                   "by_platform": {"messenger": 1, "instagram": 1}}

    db2 = _FakeDB([rooms])
    lst = await API.list_rooms(team_id=team, db=db2, _user=None)
    assert len(lst) == 3 and lst[0].platform == "messenger"


@pytest.mark.asyncio
async def test_api_sync_disabled_and_enabled():
    with patch.object(API, "bridge_enabled", return_value=False):
        with pytest.raises(HTTPException) as e:
            await API.sync_now(team_id=uuid.uuid4(), _user=None)
    assert e.value.status_code == 409

    with (
        patch.object(API, "bridge_enabled", return_value=True),
        patch.object(API, "sync_rooms", AsyncMock(return_value={"created": 1, "mapped": 0})),
        patch.object(API, "inbound_sync", AsyncMock(return_value={"posted": 2})),
    ):
        out = await API.sync_now(team_id=uuid.uuid4(), _user=None)
    assert out == {"created": 1, "mapped": 0, "posted": 2}


@pytest.mark.asyncio
async def test_api_send_and_toggle():
    team = uuid.uuid4()
    # send: room missing → 404
    db = _FakeDB([[]])
    with pytest.raises(HTTPException) as e:
        await API.send_to_thread("tok", API.SendRequest(text="hi"), team_id=team, db=db, _user=None)
    assert e.value.status_code == 404

    # send: outbound fails → 502
    room = _room()
    db2 = _FakeDB([[room]])
    with patch.object(API, "outbound", AsyncMock(return_value=(False, "down"))):
        with pytest.raises(HTTPException) as e2:
            await API.send_to_thread("tok", API.SendRequest(text="hi"), team_id=team, db=db2, _user=None)
    assert e2.value.status_code == 502

    # send: ok
    db3 = _FakeDB([[room]])
    with patch.object(API, "outbound", AsyncMock(return_value=(True, "sent"))):
        out = await API.send_to_thread("tok", API.SendRequest(text="hi"), team_id=team, db=db3, _user=None)
    assert out == {"ok": True}

    # toggle: missing → 404; found → flips + commits
    db4 = _FakeDB([[]])
    with pytest.raises(HTTPException):
        await API.toggle_room("tok", team_id=team, db=db4, _user=None)
    db5 = _FakeDB([[room]])
    out2 = await API.toggle_room("tok", team_id=team, db=db5, _user=None)
    assert out2["enabled"] is False and room.enabled is False and db5.commits == 1


@pytest.mark.asyncio
async def test_fb_page_messages_nonlist_and_fetch_unknown():
    acct = _acct(meta_data={"page_token": "EAxx"})

    class _C:
        def __init__(self, *a, **kw):
            pass
        async def get_conversation_messages(self, tid, limit=0):
            return {"unexpected": True}
    from app.services import messenger_api
    with patch.object(messenger_api, "MessengerAPIClient", _C):
        assert await TB._fb_page_messages(acct, "t1") == []

    # unknown platform dispatch → []
    assert await TB._fetch_messages(acct, _room(platform="carrier_pigeon")) == []


@pytest.mark.asyncio
async def test_conversation_list_page_and_ig_nonlist():
    acct = _acct(meta_data={"page_token": "EAxx"})

    class _C:
        def __init__(self, *a, **kw):
            pass
        async def get_conversations(self, platform=None, limit=0):
            return {"conversations": {"not": "a list"}}
    from app.services import instagram_api, messenger_api
    with patch.object(messenger_api, "MessengerAPIClient", _C):
        assert await TB._conversation_list(acct) == []

    acct_ig = _acct(platform="instagram", meta_data={"access_token": "IGA"})

    class _IC:
        def __init__(self, *a, **kw):
            pass
        async def get_conversations(self, limit=0):
            return {"weird": 1}
    with patch.object(instagram_api, "InstagramAPIClient", _IC):
        assert await TB._conversation_list(acct_ig) == []


@pytest.mark.asyncio
async def test_outbound_ig_and_linkedin_success():
    room = _room(platform="instagram", recipient_id="igsid-1")
    acct = _acct(platform="instagram", id=uuid.UUID(room.account_id),
                 meta_data={"access_token": "IGA"})
    db = _FakeDB([[acct]])
    sent = []

    class _IC:
        def __init__(self, *a, **kw):
            pass
        async def send_dm(self, rid, text):
            sent.append((rid, text))
    from app.services import instagram_api, linkedin_sidecar
    with (
        patch.object(TB, "get_settings", return_value=_settings()),
        patch.object(TB, "room_for_token", AsyncMock(return_value=room)),
        patch.object(TB, "async_session_maker", _sf(_FakeDB([]))),
        patch.object(instagram_api, "InstagramAPIClient", _IC),
        patch.object(TB, "_log_event", AsyncMock()),
    ):
        ok, msg = await TB.outbound("tok", "hi", session_factory=_sf(db))
    assert (ok, msg) == (True, "sent") and sent == [("igsid-1", "hi")]

    room2 = _room(platform="linkedin")
    acct2 = _acct(platform="linkedin", id=uuid.UUID(room2.account_id))
    db2 = _FakeDB([[acct2]])
    li_sent = []

    class _LS:
        async def send_message(self, tid, text):
            li_sent.append((tid, text))
    with (
        patch.object(TB, "get_settings", return_value=_settings()),
        patch.object(TB, "room_for_token", AsyncMock(return_value=room2)),
        patch.object(TB, "async_session_maker", _sf(_FakeDB([]))),
        patch.object(linkedin_sidecar, "LinkedInSidecarClient", lambda: _LS()),
        patch.object(TB, "_log_event", AsyncMock()),
    ):
        ok2, msg2 = await TB.outbound("tok", "yo", session_factory=_sf(db2))
    assert (ok2, msg2) == (True, "sent") and li_sent == [("t-1", "yo")]


@pytest.mark.asyncio
async def test_inbound_sync_break_at_watermark_and_post_fail():
    team = uuid.uuid4()
    room = _room(last_inbound_key="k2")
    db = _FakeDB([[room], [_acct(id=uuid.UUID(room.account_id))]])
    msgs = [{"key": "k3", "id": "", "ours": False, "sender": "A", "text": "new", "attachments": []},
            {"key": "k2", "id": "", "ours": False, "sender": "A", "text": "old", "attachments": []},
            {"key": "k1", "id": "", "ours": False, "sender": "A", "text": "older", "attachments": []}]
    posted = []

    async def _ok(r, body, db=None):
        posted.append(body)
        return True
    with (
        patch.object(TB, "_fetch_messages", AsyncMock(return_value=msgs)),
        patch.object(TB, "_post", AsyncMock(side_effect=_ok)),
    ):
        out = await TB.inbound_sync(team, session_factory=_sf(db))
    assert out["posted"] == 1 and room.last_inbound_key == "k3"

    # post failure mid-list → watermark keeps last delivered
    room2 = _room(last_inbound_key=None)
    db2 = _FakeDB([[room2], [_acct(id=uuid.UUID(room2.account_id))]])
    calls = {"n": 0}

    async def _flaky(r, body, db=None):
        calls["n"] += 1
        return calls["n"] < 2
    with (
        patch.object(TB, "_fetch_messages", AsyncMock(return_value=[
            {"key": "a", "id": "", "ours": False, "sender": "A", "text": "1", "attachments": []},
            {"key": "b", "id": "", "ours": False, "sender": "A", "text": "2", "attachments": []},
        ])),
        patch.object(TB, "_post", AsyncMock(side_effect=_flaky)),
    ):
        out2 = await TB.inbound_sync(team, session_factory=_sf(db2))
    assert out2["posted"] == 1 and room2.last_inbound_key == "a"
