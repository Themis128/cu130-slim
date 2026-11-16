"""Coverage for the LinkedIn scope-utilization lanes (PR #1986 gate).

Mocks httpx + task_session + alert lanes; no network, no DB.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import linkedin_admin as LA
from app.services import linkedin_community as LC
from app.worker.tasks import linkedin_ads_guard as AG
from app.worker.tasks import linkedin_community as LT
from app.worker.tasks import linkedin_offline_conversions as OC

# ── helpers ────────────────────────────────────────────────────────────


class _Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = str(self._body)

    def json(self):
        return self._body


class _HttpClient:
    """Queues one response per call, records (method, url, kwargs)."""

    queue: list = []
    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def _next(self, method, url, kw):
        type(self).calls.append((method, url, kw))
        return type(self).queue.pop(0)

    async def get(self, url, **kw):
        return self._next("get", url, kw)

    async def post(self, url, **kw):
        return self._next("post", url, kw)

    async def put(self, url, **kw):
        return self._next("put", url, kw)

    async def delete(self, url, **kw):
        return self._next("delete", url, kw)


@pytest.fixture
def http(monkeypatch):
    _HttpClient.queue = []
    _HttpClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", _HttpClient)
    return _HttpClient


class _Res:
    def __init__(self, first=None, all_=None):
        self._first = first
        self._all = all_ if all_ is not None else []

    def scalars(self):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._all


class _DB:
    """task_session stand-in; pops one _Res per execute() call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.added = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        if self.responses:
            return self.responses.pop(0)
        return _Res()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _org_acc():
    return SimpleNamespace(id="acc-1", team_id="team-1")


# ── linkedin_admin ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_list_no_ctx(monkeypatch):
    import app.services.linkedin_feed as LF

    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=None))
    out = await LA.list_acls()
    assert out["error"] == "no-linkedin-account"


@pytest.mark.asyncio
async def test_admin_list_ok(monkeypatch, http):
    import app.services.linkedin_feed as LF

    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=("tok", "urn:li:organization:1")))
    http.queue = [_Resp(200, {"elements": [{"role": "ADMINISTRATOR"}]})]
    out = await LA.list_acls(role="ADMINISTRATOR", state="APPROVED")
    assert out["status"] == 200
    assert out["acls"][0]["role"] == "ADMINISTRATOR"
    _, url, kw = http.calls[0]
    assert "organizationAcls" in url
    assert kw["params"]["role"] == "ADMINISTRATOR"


@pytest.mark.asyncio
async def test_admin_list_error(monkeypatch, http):
    import app.services.linkedin_feed as LF

    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=("tok", "urn:li:organization:1")))
    http.queue = [_Resp(403, {"message": "denied"})]
    out = await LA.list_acls()
    assert out["status"] == 403
    assert "denied" in out["error"]


@pytest.mark.asyncio
async def test_admin_set_role_validation():
    out = await LA.set_role("urn:li:person:1", role="SUPERUSER")
    assert "bad role" in out["error"]
    out = await LA.set_role("urn:li:person:1", state="MAYBE")
    assert "bad state" in out["error"]


@pytest.mark.asyncio
async def test_admin_set_role_ok_and_urn_norm(monkeypatch, http):
    import app.services.linkedin_feed as LF

    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=("tok", "urn:li:organization:9")))
    http.queue = [_Resp(200, {})]
    out = await LA.set_role("abc123", role="ANALYST", state="REQUESTED")
    assert out["ok"] is True
    method, url, kw = http.calls[0]
    assert method == "put"
    assert "roleAssignee:urn%3Ali%3Aperson%3Aabc123" in url or "roleAssignee" in url
    assert kw["json"]["roleAssignee"] == "urn:li:person:abc123"
    assert kw["json"]["state"] == "REQUESTED"


@pytest.mark.asyncio
async def test_admin_set_role_error_and_no_ctx(monkeypatch, http):
    import app.services.linkedin_feed as LF

    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=("tok", "urn:li:organization:9")))
    http.queue = [_Resp(400, {"message": "bad"})]
    out = await LA.set_role("urn:li:person:1")
    assert out["ok"] is False
    assert "bad" in out["error"]
    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=None))
    out = await LA.set_role("urn:li:person:1")
    assert out["error"] == "no-linkedin-account"


@pytest.mark.asyncio
async def test_admin_remove_role(monkeypatch, http):
    import app.services.linkedin_feed as LF

    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=("tok", "urn:li:organization:9")))
    http.queue = [_Resp(204, {})]
    out = await LA.remove_role("urn:li:person:1", role="CURATOR")
    assert out["ok"] is True
    method, url, _ = http.calls[0]
    assert method == "delete" and "role:CURATOR" in url
    out = await LA.remove_role("urn:li:person:1", role="NOPE")
    assert "bad role" in out["error"]
    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=None))
    out = await LA.remove_role("urn:li:person:1")
    assert out["error"] == "no-linkedin-account"


# ── linkedin_community service ─────────────────────────────────────────


def test_classify_single_bare_url_not_spam():
    # review-hardened: one URL alone falls through to praise/other
    assert LC.classify_comment("Great post, see https://example.com") != "spam"


def test_classify_two_links_and_shortener_spam():
    assert LC.classify_comment("win https://a.com https://b.com") == "spam"
    assert LC.classify_comment("click bit.ly/xyz now") == "spam"
    assert LC.classify_comment("DM me for crypto gains") == "spam"


def test_age_h():
    now_ms = 1_700_000_000_000
    assert LC.linkedin_engagement_age_h({"publishedAt": {"time": now_ms}}) is not None
    assert LC.linkedin_engagement_age_h({"publishedAt": 1700000000000}) is not None
    assert LC.linkedin_engagement_age_h({}) is None
    assert LC.linkedin_engagement_age_h({"publishedAt": "notadict"}) is None


@pytest.mark.asyncio
async def test_org_post_comments_no_ctx(monkeypatch):
    import app.services.linkedin_feed as LF

    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=None))
    out = await LC.org_post_comments()
    assert out["error"] == "no-linkedin-account"


@pytest.mark.asyncio
async def test_org_post_comments_error_and_happy(monkeypatch):
    import app.services.linkedin_feed as LF

    posts = [
        {"id": "urn:li:share:1", "publishedAt": {"time": 99999999999999}},
        {"id": "not-a-urn"},
        {"id": "urn:li:share:2"},  # no publishedAt → age None → included
    ]
    comments = {
        "urn:li:share:2": {
            "comments": [
                {
                    "commentUrn": "urn:li:comment:(urn:li:share:2,7001)",
                    "id": "7001",
                    "actor": {"sub": "urn:li:person:9"},
                    "message": {"text": "What is the pricing?"},
                    "created": {"time": 1},
                },
                {
                    "commentUrn": "urn:li:comment:(urn:li:share:2,7002)",
                    "actor": "urn:li:organization:1",  # own reply → skipped
                    "message": {"text": "thanks"},
                },
                {
                    "commentUrn": "urn:li:comment:x",
                    "id": "abc:weird",
                    "actor": "urn:li:person:8",
                    "message": {"text": "nice"},
                },
            ]
        }
    }
    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=("tok", "urn:li:organization:1")))
    monkeypatch.setattr(
        LF,
        "list_posts",
        AsyncMock(side_effect=[{"error": "boom"}, {"posts": posts, "status": 200}]),
    )
    monkeypatch.setattr(
        LF,
        "list_comments",
        AsyncMock(side_effect=lambda urn, count=50: {"comments": comments.get(urn, {}).get("comments", [])}),
    )
    out = await LC.org_post_comments()
    assert out["error"] == "boom"
    out = await LC.org_post_comments()
    assert out["posts_scanned"] == 3
    texts = [c["text"] for c in out["comments"]]
    assert "What is the pricing?" in texts and "nice" in texts
    assert "thanks" not in texts  # org actor filtered
    c0 = out["comments"][0]
    assert c0["comment_id"] == "7001"
    assert c0["actor"] == "urn:li:person:9"


@pytest.mark.asyncio
async def test_delete_comment_dry_run(monkeypatch):
    monkeypatch.setattr(LC, "COMMENT_HIDE_ENABLED", False)
    out = await LC.delete_comment("urn:li:share:1", "42")
    assert out["dry_run"] is True


@pytest.mark.asyncio
async def test_delete_comment_enabled(monkeypatch, http):
    import app.services.linkedin_feed as LF

    monkeypatch.setattr(LC, "COMMENT_HIDE_ENABLED", True)
    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=("tok", "urn:li:organization:1")))
    http.queue = [_Resp(204, {})]
    out = await LC.delete_comment("urn:li:share:1", "42")
    assert out["ok"] is True
    method, url, kw = http.calls[0]
    assert method == "delete"
    assert "socialActions" in url and "comments" in url
    assert kw["params"]["actor"] == "urn:li:organization:1"
    http.queue = [_Resp(404, {"message": "gone"})]
    out = await LC.delete_comment("urn:li:share:1", "42")
    assert out["ok"] is False and "gone" in out["error"]
    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=None))
    out = await LC.delete_comment("urn:li:share:1", "42")
    assert out["error"] == "no-linkedin-account"


# ── linkedin_community worker task ─────────────────────────────────────


@pytest.mark.asyncio
async def test_community_run_error_and_empty(monkeypatch):
    import app.services.linkedin_community as SVC

    monkeypatch.setattr(SVC, "org_post_comments", AsyncMock(return_value={"error": "x", "comments": []}))
    out = await LT._run()
    assert out["error"] == "x"
    monkeypatch.setattr(SVC, "org_post_comments", AsyncMock(return_value={"comments": [], "posts_scanned": 0}))
    db = _DB([_Res(first=None)])
    monkeypatch.setattr(LT, "task_session", lambda: db)
    out = await LT._run()
    assert out["error"] == "no-linkedin-org-account"


@pytest.mark.asyncio
async def test_community_run_full(monkeypatch):
    import app.services.linkedin_community as SVC
    import app.services.slack_notifications as SN

    comments = [
        {"comment_urn": "urn:c:1", "post_urn": "urn:li:share:1", "comment_id": "1", "actor": "p", "text": "crypto bitcoin casino"},
        {"comment_urn": "urn:c:2", "post_urn": "urn:li:share:1", "comment_id": "2", "actor": "p", "text": "what is your pricing?"},
        {"comment_urn": "urn:c:3", "post_urn": "urn:li:share:1", "comment_id": "3", "actor": "p", "text": "great work"},
        {"comment_urn": "", "post_urn": "x", "comment_id": "", "actor": "p", "text": "no urn"},
    ]
    monkeypatch.setattr(SVC, "org_post_comments", AsyncMock(return_value={"comments": comments, "posts_scanned": 2}))
    monkeypatch.setattr(SVC, "delete_comment", AsyncMock(return_value={"dry_run": True}))
    alert = AsyncMock()
    monkeypatch.setattr(SN, "send_telegram_alert", alert)
    db = _DB(
        [
            _Res(first=_org_acc()),  # SocialAccount select
            _Res(first=None),  # c1 _already_seen
            _Res(first=None),  # c2
            _Res(first=None),  # c3
            _Res(first=None),  # empty-urn comment skipped before query? no — urn check first
        ]
    )
    monkeypatch.setattr(LT, "task_session", lambda: db)
    out = await LT._run()
    assert out["comments"] == 4
    assert out["spam"] == 1 and out["delete_dry_run"] == 1
    assert out["leads"] == 1 and out["questions"] == 0
    assert out["dedup"] == 1  # empty comment_urn
    assert db.commits == 1
    alert.assert_awaited()


@pytest.mark.asyncio
async def test_community_run_dedup_and_delete_ok(monkeypatch):
    import app.services.linkedin_community as SVC
    import app.services.slack_notifications as SN

    comments = [
        {"comment_urn": "urn:c:1", "post_urn": "p", "comment_id": "1", "actor": "a", "text": "casino bonus"},
        {"comment_urn": "urn:c:2", "post_urn": "p", "comment_id": "2", "actor": "a", "text": "hello"},
    ]
    monkeypatch.setattr(SVC, "org_post_comments", AsyncMock(return_value={"comments": comments, "posts_scanned": 1}))
    monkeypatch.setattr(SVC, "delete_comment", AsyncMock(return_value={"ok": True, "status": 204}))
    monkeypatch.setattr(SN, "send_telegram_alert", AsyncMock())
    seen_row = SimpleNamespace(id="x")
    db = _DB(
        [
            _Res(first=_org_acc()),
            _Res(first=seen_row),  # c1 already seen → dedup
            _Res(first=None),  # c2 new → journal
        ]
    )
    monkeypatch.setattr(LT, "task_session", lambda: db)
    out = await LT._run()
    assert out["dedup"] == 1
    assert out["deleted"] == 0  # c1 was deduped before delete
    assert len(db.added) == 1


# ── linkedin_ads_guard ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ads_guard_no_account(monkeypatch):
    db = _DB([_Res(first=None)])
    monkeypatch.setattr(AG, "task_session", lambda: db)
    out = await AG._run()
    assert out["skip"] == "no-linkedin-org-account"


@pytest.mark.asyncio
async def test_ads_guard_paths(monkeypatch):
    import app.services.linkedin_ads as ADS

    monkeypatch.setattr(AG, "task_session", lambda: _DB([_Res(first=_org_acc())]))
    monkeypatch.setattr(ADS, "ad_accounts", AsyncMock(side_effect=RuntimeError("api down")))
    out = await AG._run()
    assert "api down" in out["error"]

    monkeypatch.setattr(ADS, "ad_accounts", AsyncMock(return_value=[]))
    out = await AG._run()
    assert out["accounts"] == 0

    # one account, one ACTIVE campaign not allowlisted → alert + journal
    monkeypatch.setattr(ADS, "ad_accounts", AsyncMock(return_value=[{"urn": "urn:li:sponsoredAccount:1", "name": "acct"}]))
    monkeypatch.setattr(
        ADS,
        "campaigns",
        AsyncMock(return_value=[{"id": "c1", "name": "Spring Push", "status": "ACTIVE"}, {"id": "c2", "name": "Old", "status": "PAUSED"}]),
    )
    alert = AsyncMock()
    monkeypatch.setattr(AG, "send_telegram_alert", alert)  # module-level import binding
    # _run: acc select; _persist_snapshot: acc select again
    monkeypatch.setattr(AG, "task_session", lambda: _DB([_Res(first=_org_acc()), _Res(first=_org_acc())]))
    monkeypatch.setattr(AG, "ALLOWED_ACTIVE", set())
    out = await AG._run()
    assert out["active"] == 1
    assert out["unexpected_active"] == ["Spring Push (acct)"]
    alert.assert_awaited()

    # allowlisted → silent
    monkeypatch.setattr(AG, "ALLOWED_ACTIVE", {"Spring Push"})
    monkeypatch.setattr(AG, "task_session", lambda: _DB([_Res(first=_org_acc())]))
    alert.reset_mock()
    out = await AG._run()
    assert out["unexpected_active"] == []
    alert.assert_not_awaited()


# ── linkedin_offline_conversions ───────────────────────────────────────


@pytest.mark.asyncio
async def test_offline_conv_gates(monkeypatch):
    import app.services.linkedin_advanced as ADV

    monkeypatch.setattr(ADV, "ENABLED", False)
    out = await OC._run()
    assert out["skip"] == "LINKEDIN_ADVANCED off"

    monkeypatch.setattr(ADV, "ENABLED", True)
    monkeypatch.setattr(OC, "_espo_won_leads", AsyncMock(side_effect=RuntimeError("espo down")))
    out = await OC._run()
    assert "espo down" in out["error"]

    monkeypatch.setattr(OC, "_espo_won_leads", AsyncMock(return_value=[]))
    monkeypatch.setattr(ADV, "resolve_conversion_rule", AsyncMock(return_value=None))
    out = await OC._run()
    assert "no lead_won conversion rule" in out["skip"]


@pytest.mark.asyncio
async def test_offline_conv_full(monkeypatch):
    import app.services.linkedin_advanced as ADV
    import app.services.slack_notifications as SN

    team = SimpleNamespace(id="team-1")
    leads = [
        {
            "id": "L1",
            "emailAddress": "a@b.com",
            "description": "LinkedIn lead-gen response urn:li:leadGenFormResponse:9",
            "amount": "1200.50",
            "amountCurrency": "EUR",
        },
        {"id": "L2", "emailAddress": "c@d.com", "description": "not linkedin", "amount": None},
        {"id": "L3", "emailAddress": "", "description": "LinkedIn x", "amount": None},
        {"id": "L4", "emailAddress": "e@f.com", "description": "LinkedIn lead-gen response urn:li:x:1", "amount": None},
    ]
    monkeypatch.setattr(ADV, "ENABLED", True)
    monkeypatch.setattr(OC, "_espo_won_leads", AsyncMock(return_value=leads))
    monkeypatch.setattr(ADV, "resolve_conversion_rule", AsyncMock(return_value="urn:li:conversionRule:1"))
    stream = AsyncMock(return_value={"ok": True, "status": 201})
    monkeypatch.setattr(ADV, "stream_conversion", stream)
    monkeypatch.setattr(SN, "post_alert_to_slack", AsyncMock())
    monkeypatch.setattr(SN, "send_telegram_alert", AsyncMock())

    db = _DB(
        [
            _Res(first=team),  # Team select
            _Res(first=_org_acc()),  # SocialAccount select
            _Res(all_=[]),  # L1 _already_sent → none → streams
            _Res(all_=[{"result": {"ok": True}}]),  # L2 already sent → dedup
            _Res(all_=[{"result": {"ok": True}}]),  # L4 already sent → dedup
        ]
    )
    monkeypatch.setattr(OC, "task_session", lambda: db)
    out = await OC._run()
    assert out["won_leads"] == 4
    assert out["sent"] == 1  # only L1 streamed
    assert out["dedup"] == 2  # L2 non-linkedin + L4 already-sent
    assert out["no_email"] == 1  # L3
    assert stream.await_count == 1
    kw = stream.await_args.kwargs
    assert kw["conversion_value"] == 1200.50
    assert kw["currency_code"] == "EUR"
    assert kw["lead_id"] == "urn:li:leadGenFormResponse:9"
    assert len(db.added) == 1  # ledger row for L1
    assert db.commits == 1


@pytest.mark.asyncio
async def test_offline_conv_no_team(monkeypatch):
    import app.services.linkedin_advanced as ADV

    monkeypatch.setattr(ADV, "ENABLED", True)
    monkeypatch.setattr(OC, "_espo_won_leads", AsyncMock(return_value=[{"id": "L", "emailAddress": "a@b"}]))
    monkeypatch.setattr(ADV, "resolve_conversion_rule", AsyncMock(return_value="rule"))
    db = _DB([_Res(first=None), _Res(first=_org_acc())])
    monkeypatch.setattr(OC, "task_session", lambda: db)
    out = await OC._run()
    assert out["error"] == "no-team-or-linkedin-account"


@pytest.mark.asyncio
async def test_offline_conv_stream_failure_retries_later(monkeypatch):
    import app.services.linkedin_advanced as ADV
    import app.services.slack_notifications as SN

    team = SimpleNamespace(id="team-1")
    leads = [{"id": "L1", "emailAddress": "a@b.com", "description": "LinkedIn resp urn:x", "amount": None}]
    monkeypatch.setattr(ADV, "ENABLED", True)
    monkeypatch.setattr(OC, "_espo_won_leads", AsyncMock(return_value=leads))
    monkeypatch.setattr(ADV, "resolve_conversion_rule", AsyncMock(return_value="rule"))
    monkeypatch.setattr(ADV, "stream_conversion", AsyncMock(return_value={"ok": False, "status": 503, "error": "down"}))
    monkeypatch.setattr(SN, "post_alert_to_slack", AsyncMock())
    monkeypatch.setattr(SN, "send_telegram_alert", AsyncMock())
    db = _DB([_Res(first=team), _Res(first=_org_acc()), _Res(all_=[])])
    monkeypatch.setattr(OC, "task_session", lambda: db)
    out = await OC._run()
    assert out["sent"] == 0
    assert out["errors"][0]["status"] == 503
    # ledger row still written with result.status=503 → _already_sent False → retry next run
    assert db.added and db.added[0].meta_data["result"]["status"] == 503


# ── residual branch/line gaps (coverage sweep) ─────────────────────────


@pytest.mark.asyncio
async def test_admin_remove_role_norm_error_and_digit(monkeypatch, http):
    import app.services.linkedin_feed as LF

    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=("tok", "urn:li:organization:9")))
    # non-urn non-digit person id → normalized to urn:li:person:… (line 111)
    http.queue = [_Resp(403, {"message": "forbidden"})]
    out = await LA.remove_role("abc123", role="ANALYST")
    assert out["ok"] is False
    assert "forbidden" in out["error"]  # line 124
    method, url, kw = http.calls[0]
    assert method == "delete"
    assert "roleAssignee:urn%3Ali%3Aperson%3Aabc123" in url
    # bare digit id → kept as-is (isdigit arc, no normalization)
    http.queue = [_Resp(200, {})]
    out = await LA.remove_role("12345")
    assert out["ok"] is True
    assert "roleAssignee:12345" in http.calls[1][1]


@pytest.mark.asyncio
async def test_admin_set_role_digit_urn(monkeypatch, http):
    import app.services.linkedin_feed as LF

    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=("tok", "urn:li:organization:9")))
    http.queue = [_Resp(204, {})]
    out = await LA.set_role("12345", role="CURATOR", state="APPROVED")
    assert out["ok"] is True
    assert http.calls[0][2]["json"]["roleAssignee"] == "12345"


@pytest.mark.asyncio
async def test_org_post_comments_old_post_and_id_fallbacks(monkeypatch):
    import app.services.linkedin_feed as LF

    old_ms = 1_000_000_000_000  # ~2001 → far older than max_age_h
    posts = [
        {"id": "urn:li:share:old", "publishedAt": {"time": old_ms}},
        {"id": "urn:li:share:new"},  # no timestamp → age None → scanned
    ]
    comments = {
        "urn:li:share:new": {
            "comments": [
                # composite commentUrn, no id → cid from URN tail ("," arc)
                {"commentUrn": "urn:li:comment:(urn:li:share:new,777)", "actor": None, "message": {"text": "hi"}},
                # no commentUrn, bare id → `or` second operand
                {"id": "888", "actor": "urn:li:person:5", "message": {"text": "yo"}},
                # neither urn nor id → empty-string tail arc
                {"actor": "urn:li:person:6", "message": {"text": "sup"}},
            ]
        }
    }
    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=("tok", "urn:li:organization:1")))
    monkeypatch.setattr(LF, "list_posts", AsyncMock(return_value={"posts": posts, "status": 200}))
    monkeypatch.setattr(
        LF,
        "list_comments",
        AsyncMock(side_effect=lambda urn, count=50: {"comments": comments.get(urn, {}).get("comments", [])}),
    )
    out = await LC.org_post_comments(max_age_h=24)
    assert out["posts_scanned"] == 2
    # old post skipped → only urn:li:share:new was comment-scanned
    assert len(out["comments"]) == 3
    assert out["comments"][0]["comment_id"] == "777"
    assert out["comments"][0]["actor"] == ""
    assert out["comments"][1]["comment_id"] == "888"
    assert out["comments"][2]["comment_urn"] == ""


@pytest.mark.asyncio
async def test_community_run_delete_ok_and_error(monkeypatch):
    import app.services.linkedin_community as SVC
    import app.services.slack_notifications as SN

    comments = [
        {"comment_urn": "urn:c:ok", "post_urn": "p", "comment_id": "1", "actor": "a", "text": "casino bonus"},
        {"comment_urn": "urn:c:err", "post_urn": "p", "comment_id": "2", "actor": "a", "text": "viagra cheap"},
    ]
    monkeypatch.setattr(SVC, "org_post_comments", AsyncMock(return_value={"comments": comments, "posts_scanned": 1}))

    async def _del(post_urn, comment_id):
        return {"ok": True, "status": 204} if comment_id == "1" else {"ok": False, "status": 500, "error": "boom"}

    monkeypatch.setattr(SVC, "delete_comment", AsyncMock(side_effect=_del))
    monkeypatch.setattr(SN, "send_telegram_alert", AsyncMock())
    db = _DB([_Res(first=_org_acc()), _Res(first=None), _Res(first=None)])
    monkeypatch.setattr(LT, "task_session", lambda: db)
    out = await LT._run()
    assert out["spam"] == 2
    assert out["deleted"] == 1
    assert out["errors"] == ["delete urn:c:err: 500 boom"]


def test_community_task_wrapper(monkeypatch):
    monkeypatch.setattr(LT, "run_async", lambda c: (c.close(), {"ok": 1})[1])
    assert LT.moderate_linkedin_community() == {"ok": 1}


@pytest.mark.asyncio
async def test_ads_guard_journal_failure_swallowed(monkeypatch):
    import app.services.linkedin_ads as ADS

    good_db = _DB([_Res(first=_org_acc())])

    class _BadCommitDB(_DB):
        async def commit(self):
            raise RuntimeError("db gone")

    bad_db = _BadCommitDB([_Res(first=_org_acc())])
    sessions = [good_db, bad_db]
    monkeypatch.setattr(AG, "task_session", lambda: sessions.pop(0))
    monkeypatch.setattr(ADS, "ad_accounts", AsyncMock(return_value=[{"urn": "urn:li:sponsoredAccount:1", "name": "acct"}]))
    monkeypatch.setattr(
        ADS,
        "campaigns",
        AsyncMock(return_value=[{"id": "c1", "name": "Push", "status": "ACTIVE"}]),
    )
    monkeypatch.setattr(AG, "send_telegram_alert", AsyncMock())
    monkeypatch.setattr(AG, "ALLOWED_ACTIVE", set())
    out = await AG._run()
    assert out["unexpected_active"] == ["Push (acct)"]
    # journal failure is best-effort — run still returns stats


def test_ads_guard_task_wrapper(monkeypatch):
    monkeypatch.setattr(AG, "run_async", lambda c: (c.close(), {"ok": 1})[1])
    assert AG.guard_linkedin_ads() == {"ok": 1}


@pytest.mark.asyncio
async def test_offline_conv_nonlinkedin_desc_and_amount_arcs(monkeypatch):
    import app.services.linkedin_advanced as ADV
    import app.services.slack_notifications as SN

    team = SimpleNamespace(id="team-1")
    leads = [
        # passes dedup but description lacks "LinkedIn" → silent dedup skip (lines 147-148)
        {"id": "L9", "emailAddress": "g@h.com", "description": "newsletter signup", "amount": None},
        # LinkedIn desc, non-numeric string amount → conversion_value None arc
        {"id": "L10", "emailAddress": "i@j.com", "description": "LinkedIn lead-gen response urn:x", "amount": "abc"},
    ]
    monkeypatch.setattr(ADV, "ENABLED", True)
    monkeypatch.setattr(OC, "_espo_won_leads", AsyncMock(return_value=leads))
    monkeypatch.setattr(ADV, "resolve_conversion_rule", AsyncMock(return_value="rule"))
    stream = AsyncMock(return_value={"ok": True, "status": 200})
    monkeypatch.setattr(ADV, "stream_conversion", stream)
    monkeypatch.setattr(SN, "post_alert_to_slack", AsyncMock())
    monkeypatch.setattr(SN, "send_telegram_alert", AsyncMock())
    db = _DB(
        [
            _Res(first=team),
            _Res(first=_org_acc()),
            _Res(all_=[]),  # L9 not yet sent
            _Res(all_=[]),  # L10 not yet sent
        ]
    )
    monkeypatch.setattr(OC, "task_session", lambda: db)
    out = await OC._run()
    assert out["dedup"] == 1  # L9 only
    assert out["sent"] == 1  # L10 streamed
    assert stream.await_args.kwargs["conversion_value"] is None


def test_offline_conv_task_wrapper(monkeypatch):
    monkeypatch.setattr(OC, "run_async", lambda c: (c.close(), {"ok": 1})[1])
    assert OC.sync_linkedin_offline_conversions() == {"ok": 1}
