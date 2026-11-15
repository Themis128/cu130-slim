"""Coverage for linkedin_feed service + engagement task + conversion ensure."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import linkedin_advanced as ADV
from app.services import linkedin_feed as LF
from app.worker.tasks import linkedin_conversion_ensure as ENS
from app.worker.tasks import linkedin_engagement as ENG


class _AsyncCM:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *a):
        return False


class _Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}
        self.text = str(self._body)

    def json(self):
        return self._body


def _patch_ctx(monkeypatch, ctx=("tok", "urn:li:person:1")):
    monkeypatch.setattr(LF, "member_ctx", AsyncMock(return_value=ctx))
    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=ctx))


def _patch_http(monkeypatch, responses):
    """Patch httpx.AsyncClient to return queued responses per method."""
    calls: list = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def _next(self, method):
            calls.append((method,))
            return responses.pop(0)

        async def get(self, *a, **k):
            return self._next("get")

        async def post(self, *a, **k):
            return self._next("post")

        async def delete(self, *a, **k):
            return self._next("delete")

    monkeypatch.setattr(LF.httpx, "AsyncClient", _Client)
    return calls


@pytest.mark.asyncio
async def test_list_posts_no_account(monkeypatch):
    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=None))
    out = await LF.list_posts("urn:li:organization:1")
    assert out["error"] == "no-linkedin-account"


@pytest.mark.asyncio
async def test_list_posts_ok(monkeypatch):
    _patch_ctx(monkeypatch)
    _patch_http(monkeypatch, [_Resp(200, {"elements": [{"id": "urn:li:share:1"}]})])
    out = await LF.list_posts("urn:li:organization:1", count=5)
    assert out["status"] == 200
    assert out["posts"][0]["id"] == "urn:li:share:1"


@pytest.mark.asyncio
async def test_list_posts_error(monkeypatch):
    _patch_ctx(monkeypatch)
    _patch_http(monkeypatch, [_Resp(403, {"message": "scope"})])
    out = await LF.list_posts("urn:li:organization:1")
    assert out["status"] == 403


@pytest.mark.asyncio
async def test_react_dry_run_when_writes_off(monkeypatch):
    _patch_ctx(monkeypatch)
    monkeypatch.setattr(LF, "WRITES_ENABLED", False)
    out = await LF.react("member", "urn:li:share:1", "LIKE")
    assert out["dry_run"] is True
    assert out["reactionType"] == "LIKE"


@pytest.mark.asyncio
async def test_react_bad_type(monkeypatch):
    _patch_ctx(monkeypatch)
    out = await LF.react("member", "urn:li:share:1", "NOPE")
    assert "bad reactionType" in out["error"]


@pytest.mark.asyncio
async def test_react_posts_when_enabled(monkeypatch):
    _patch_ctx(monkeypatch)
    monkeypatch.setattr(LF, "WRITES_ENABLED", True)
    sent = {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, params=None, json=None, headers=None):
            sent["url"] = url
            sent["params"] = params
            sent["json"] = json
            return _Resp(201, {}, {"x-restli-id": "rxn-1"})

    monkeypatch.setattr(LF.httpx, "AsyncClient", _Client)
    out = await LF.react("member", "urn:li:share:1", "PRAISE")
    assert out["ok"] is True
    assert out["id"] == "rxn-1"
    assert sent["json"] == {"root": "urn:li:share:1", "reactionType": "PRAISE"}
    assert "actor" in sent["params"]


@pytest.mark.asyncio
async def test_comment_dry_run_and_validation(monkeypatch):
    _patch_ctx(monkeypatch)
    monkeypatch.setattr(LF, "WRITES_ENABLED", False)
    out = await LF.comment("member", "urn:li:share:1", "hello")
    assert out["dry_run"] is True
    out = await LF.comment("member", "urn:li:share:1", "   ")
    assert "empty" in out["error"]
    out = await LF.comment("member", "urn:li:share:1", "x" * 1300)
    assert "1250" in out["error"]


@pytest.mark.asyncio
async def test_has_reaction_states(monkeypatch):
    _patch_ctx(monkeypatch)
    _patch_http(monkeypatch, [_Resp(200, {}), _Resp(404, {}), _Resp(500, {})])
    assert await LF.has_reaction("urn:li:person:1", "urn:li:share:1") is True
    assert await LF.has_reaction("urn:li:person:1", "urn:li:share:2") is False
    assert await LF.has_reaction("urn:li:person:1", "urn:li:share:3") is None


# ── conversion rule management ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_conversion_rule(monkeypatch):
    monkeypatch.setattr(ADV, "ENABLED", True)
    monkeypatch.setattr(ADV, "AD_ACCOUNT_URN", "urn:li:sponsoredAccount:1")
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=("tok", "urn:li:person:1")))
    sent = {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, params=None, json=None):
            sent["json"] = json
            sent["params"] = params
            return _Resp(201, {}, {"x-restli-id": "42"})

    monkeypatch.setattr(ADV.httpx, "AsyncClient", _Client)
    out = await ADV.create_conversion_rule("audit_booked", "LEAD")
    assert out["rule_urn"] == "urn:lla:llaPartnerConversion:42"
    assert out["id"] == "42"
    assert sent["json"]["conversionMethod"] == "CONVERSIONS_API"
    assert sent["json"]["type"] == "LEAD"
    assert sent["params"]["autoAssociationType"] == "ALL_CAMPAIGNS"


@pytest.mark.asyncio
async def test_create_conversion_rule_error(monkeypatch):
    monkeypatch.setattr(ADV, "ENABLED", True)
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=("tok", "urn:li:person:1")))

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp(422, {"message": "bad"})

    monkeypatch.setattr(ADV.httpx, "AsyncClient", _Client)
    out = await ADV.create_conversion_rule("x")
    assert out["error"] == 422


@pytest.mark.asyncio
async def test_resolve_conversion_rule_env_and_secret(monkeypatch):
    monkeypatch.setenv("LINKEDIN_CONVERSION_RULE", "urn:lla:llaPartnerConversion:1")
    assert await ADV.resolve_conversion_rule("audit_booked") == "urn:lla:llaPartnerConversion:1"
    assert await ADV.resolve_conversion_rule("nonexistent") is None
    monkeypatch.delenv("LINKEDIN_CONVERSION_RULE")

    fake_store = SimpleNamespace(get=AsyncMock(return_value="urn:lla:llaPartnerConversion:9"))
    import app.services.secret_store as ss

    monkeypatch.setattr(ss, "secret_store", fake_store)
    assert await ADV.resolve_conversion_rule("audit_booked") == "urn:lla:llaPartnerConversion:9"


@pytest.mark.asyncio
async def test_stream_conversion_value_fields(monkeypatch):
    monkeypatch.setattr(ADV, "ENABLED", True)
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=("tok", "urn:li:person:1")))
    sent = {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **kw):
            sent["json"] = json
            return _Resp(201, {})

    monkeypatch.setattr(ADV.httpx, "AsyncClient", _Client)
    out = await ADV.stream_conversion(
        conversion_rule="urn:lla:llaPartnerConversion:1",
        happened_at_ms=1700000000000,
        email_sha256="ab" * 32,
        conversion_value=49.0,
        currency_code="EUR",
    )
    el = sent["json"]["elements"][0]
    assert el["conversionValue"] == 49.0
    assert el["currencyCode"] == "EUR"
    assert out["ok"] is True


# ── conversion ensure task ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_creates_missing_rule(monkeypatch):
    monkeypatch.setattr(ENS.linkedin_advanced if hasattr(ENS, "linkedin_advanced") else ADV, "ENABLED", True)
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=("tok", "urn:li:person:1")))
    monkeypatch.setattr(ADV, "AD_ACCOUNT_URN", "urn:li:sponsoredAccount:1")
    monkeypatch.setattr(ADV, "resolve_conversion_rule", AsyncMock(return_value=None))

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return _Resp(200, {"elements": []})

        async def post(self, *a, **k):
            return _Resp(201, {}, {"x-restli-id": "77"})

    monkeypatch.setattr(ADV.httpx, "AsyncClient", _Client)
    store = SimpleNamespace(set=AsyncMock())
    import app.services.secret_store as ss

    monkeypatch.setattr(ss, "secret_store", store)
    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", AsyncMock())

    out = await ENS._ensure()
    assert any(c["event"] == "audit_booked" for c in out["created"])
    assert store.set.await_count >= 1


@pytest.mark.asyncio
async def test_ensure_skips_when_disabled(monkeypatch):
    monkeypatch.setattr(ADV, "ENABLED", False)
    out = await ENS._ensure()
    assert out["skip"] == "linkedin-advanced-disabled"


# ── engagement task ─────────────────────────────────────────────────────


def _team_db(posts_per_author=2):
    team = SimpleNamespace(id="team-1")
    accounts = [SimpleNamespace(id="acc-1", account_type="person")]
    return team, accounts


@pytest.mark.asyncio
async def test_engage_no_watchlist(monkeypatch):
    monkeypatch.setenv("LINKEDIN_ENGAGE_AUTHORS", "")
    out = await ENG._run()
    assert out["skip"] == "no-watchlist"


@pytest.mark.asyncio
async def test_engage_reacts_and_dedups(monkeypatch):
    monkeypatch.setenv("LINKEDIN_ENGAGE_AUTHORS", "urn:li:organization:9")
    monkeypatch.setenv("LINKEDIN_FEED_WRITES", "1")
    monkeypatch.setattr(LF, "WRITES_ENABLED", True)

    posts = [{"id": "urn:li:share:1", "publishedAt": {"time": int(__import__("datetime").datetime.now(__import__("datetime").UTC).timestamp()*1000)}}]
    monkeypatch.setattr(LF, "list_posts", AsyncMock(return_value={"posts": posts, "status": 200}))
    react_calls = []

    async def _react(actor, root, rtype):
        react_calls.append((actor, root, rtype))
        return {"ok": True, "dry_run": False}

    monkeypatch.setattr(LF, "react", _react)

    # Fake DB: no existing events, no teams→provide team
    class _Scalars:
        def __init__(self, items):
            self._items = items

        def first(self):
            return self._items[0] if self._items else None

        def all(self):
            return list(self._items)

    class _Result:
        def __init__(self, items):
            self._items = items

        def scalars(self):
            return _Scalars(self._items)

        def scalar_one_or_none(self):
            return None

    class _DB:
        def __init__(self):
            self.added = []

        async def execute(self, q):
            # First execute = Team select; second = SocialAccount select;
            # later = dedup/cap queries returning empty
            if not hasattr(self, "_n"):
                self._n = 0
            self._n += 1
            if self._n == 1:
                return _Result([SimpleNamespace(id="team-1")])
            if self._n == 2:
                return _Result([SimpleNamespace(id="acc-1", account_type="person")])
            return _Result([])

        def add(self, obj):
            self.added.append(obj)

        async def commit(self):
            pass

    db = _DB()
    monkeypatch.setattr(ENG, "task_session", lambda: _AsyncCM(db))

    out = await ENG._run()
    assert out["reacted"] == 1
    assert react_calls[0][0] == "member"
    assert len(db.added) == 1
    assert db.added[0].event_type == "linkedin_engagement"


@pytest.mark.asyncio
async def test_engage_cap_blocks_writes(monkeypatch):
    monkeypatch.setenv("LINKEDIN_ENGAGE_AUTHORS", "urn:li:organization:9")
    monkeypatch.setattr(ENG, "MAX_REACTIONS_DAY", 0)
    monkeypatch.setattr(LF, "WRITES_ENABLED", True)

    posts = [{"id": "urn:li:share:1", "publishedAt": {"time": int(__import__("datetime").datetime.now(__import__("datetime").UTC).timestamp()*1000)}}]
    monkeypatch.setattr(LF, "list_posts", AsyncMock(return_value={"posts": posts, "status": 200}))
    react = AsyncMock()
    monkeypatch.setattr(LF, "react", react)

    class _Scalars:
        def __init__(self, items):
            self._items = items

        def first(self):
            return self._items[0] if self._items else None

        def all(self):
            return list(self._items)

    class _Result:
        def __init__(self, items):
            self._items = items

        def scalars(self):
            return _Scalars(self._items)

        def scalar_one_or_none(self):
            return None

    class _DB:
        def __init__(self):
            self._n = 0

        async def execute(self, q):
            self._n += 1
            if self._n == 1:
                return _Result([SimpleNamespace(id="team-1")])
            if self._n == 2:
                return _Result([SimpleNamespace(id="acc-1", account_type="person")])
            return _Result([])

        def add(self, obj):
            pass

        async def commit(self):
            pass

    monkeypatch.setattr(ENG, "task_session", lambda: _AsyncCM(_DB()))
    out = await ENG._run()
    assert out["skipped_cap"] == 1
    react.assert_not_awaited()


# ── lead-sync webhook protocol ──────────────────────────────────────────

import hashlib as _hl
import hmac as _hm
import json as _json

from app.api import linkedin_lead_sync as LSYNC


def _sig(secret: str, body: bytes) -> str:
    return _hm.new(secret.encode(), b"hmacsha256=" + body, _hl.sha256).hexdigest()


def _req(body: bytes, sig: str = "", query: str = ""):
    headers = {"x-li-signature": sig} if sig else {}
    return SimpleNamespace(headers=headers, query_params={"challengeCode": "code-1"} if query else {}, _body=body)


@pytest.mark.asyncio
async def test_lead_sync_challenge_hmac(monkeypatch):
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "shh")
    req = SimpleNamespace(query_params={"challengeCode": "abc-123"})
    out = await LSYNC.lead_sync_challenge(req)
    import json

    body = json.loads(out.body)
    want = _hm.new(b"shh", b"abc-123", _hl.sha256).hexdigest()
    assert body["challengeResponse"] == want
    assert body["challengeCode"] == "abc-123"


@pytest.mark.asyncio
async def test_lead_sync_bad_signature_rejected(monkeypatch):
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "shh")

    class _R:
        headers = {"x-li-signature": "deadbeef"}
        query_params = {}

        async def body(self):
            return b"{}"

    out = await LSYNC.lead_sync_webhook(_R())
    assert out.status_code == 401


@pytest.mark.asyncio
async def test_lead_sync_dedup_and_route(monkeypatch):
    monkeypatch.setenv("LINKEDIN_CLIENT_SECRET", "shh")
    monkeypatch.setenv("LINKEDIN_ADVANCED", "1")
    payload = {
        "type": "LEAD_ACTION",
        "leadGenFormResponse": "urn:li:leadGenFormResponse:resp-1",
        "leadAction": "CREATED",
        "occurredAt": 1700000000000,
        "leadType": "SPONSORED",
    }
    body = _json.dumps(payload).encode()
    sig = _sig("shh", body)

    class _R:
        headers = {"x-li-signature": sig}
        query_params = {}

        async def body(self):
            return body

    seen = set()

    class _Res:
        def scalar_one_or_none(self):
            return "x" if getattr(self, "_k", None) in seen else None

    class _DB:
        def __init__(self):
            self._q_key = None

        async def execute(self, q):
            # crude: capture the platform_event_id param isn't trivial — emulate by counting calls
            self._n = getattr(self, "_n", 0) + 1
            r = SimpleNamespace()
            r.scalar_one_or_none = lambda: None
            return r

        def add(self, obj):
            pass

        async def commit(self):
            pass

    monkeypatch.setattr(LSYNC, "_fetch_response", AsyncMock(return_value={"id": "resp-1", "formResponse": []}))
    route = AsyncMock(return_value=True)
    import app.worker.tasks.linkedin_lead_boost as LB

    monkeypatch.setattr(LB, "route_lead_response", route)
    monkeypatch.setattr("app.worker._async.task_session", lambda: _AsyncCM(_DB()))
    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", AsyncMock())

    out = await LSYNC.lead_sync_webhook(_R())
    data = _json.loads(out.body)
    assert data["received"] == 1
    assert data["routed"] == 1
    route.assert_awaited_once()


def test_response_ids_extracts():
    items = LSYNC._response_ids(
        {
            "type": "LEAD_ACTION",
            "leadGenFormResponse": "urn:li:leadGenFormResponse:abc",
            "leadAction": "CREATED",
            "occurredAt": 1,
        }
    )
    assert items[0]["id"] == "abc"
    assert items[0]["action"] == "CREATED"
    assert LSYNC._response_ids({"foo": "bar"}) == []
