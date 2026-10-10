"""Unit tests for app.api.threads — Threads profile, insights, quota,
posts, replies, DM auto-reply + browser-bridge DM endpoints.

Route functions are invoked directly with a fake AsyncSession;
ThreadsAPIClient, httpx, and the browser bridge are monkeypatched.
"""
import uuid

import pytest
from fastapi import HTTPException

from app.api import threads
from app.core.security import encrypt_token
from app.models.social_account import SocialAccount
from app.services.threads_api import ThreadsAPIError

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Scalars:
    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return list(self._items)


class _Result:
    def __init__(self, value):
        self._v = value

    def scalar_one_or_none(self):
        if isinstance(self._v, list):
            return self._v[0] if self._v else None
        return self._v

    def scalars(self):
        return _Scalars(self._v if isinstance(self._v, list) else [])


class FakeDB:
    def __init__(self, accounts=()):
        self.accounts = list(accounts)
        self.commits = 0

    async def execute(self, stmt):
        cparams = dict(stmt.compile().params)
        res = list(self.accounts)
        ids = [v for k, v in cparams.items() if k == "id" or k.startswith("id_")]
        if ids:
            res = [a for a in res if a.id in ids]
        teams = [v for k, v in cparams.items() if k == "team_id" or k.startswith("team_id_")]
        if teams:
            res = [a for a in res if a.team_id in teams]
        plats = [v for k, v in cparams.items()
                 if k == "platform" or k.startswith("platform_")]
        if plats:
            res = [a for a in res if a.platform in plats]
        return _Result(res)

    async def commit(self):
        self.commits += 1


def _account(**kw) -> SocialAccount:
    return SocialAccount(
        id=kw.get("id", uuid.uuid4()),
        team_id=kw.get("team_id", uuid.uuid4()),
        platform="threads",
        account_id=kw.get("account_id", "123456"),
        username=kw.get("username", "cloudless.gr"),
        status="active", account_type="person",
        access_token_enc=kw.get("access_token_enc", encrypt_token("th-tok")),
        scopes=[], meta_data=kw.get("meta_data", {}),
    )


class _ThreadsClient:
    profile = {"id": "123456", "username": "cloudless.gr", "name": "Cloudless",
               "threads_biography": "bio", "is_verified": True}
    insights = {"data": [{"name": "views", "total_value": {"value": 10}}]}
    error = None
    delete_ok = True

    def __init__(self, access_token=None, user_id=None):
        self.access_token = access_token
        self.user_id = user_id

    async def get_profile(self):
        if type(self).error:
            raise type(self).error
        return type(self).profile

    async def get_insights(self, metric="views"):
        if type(self).error:
            raise type(self).error
        return type(self).insights

    async def delete_post(self, media_id):
        if type(self).error:
            raise type(self).error
        return type(self).delete_ok


class _Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code = status
        self._data = data or {}
        self.text = text or str(self._data)

    def json(self):
        return self._data


class _HTTP:
    responses = []
    error = None
    calls = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def _next(self):
        if type(self).error:
            raise type(self).error
        if type(self).responses:
            return type(self).responses.pop(0)
        return _Resp()

    async def get(self, url, **kw):
        type(self).calls.append(("GET", url, kw))
        return self._next()

    async def post(self, url, **kw):
        type(self).calls.append(("POST", url, kw))
        return self._next()


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    import httpx
    monkeypatch.setattr(threads, "ThreadsAPIClient", _ThreadsClient)
    monkeypatch.setattr(httpx, "AsyncClient", _HTTP)
    _ThreadsClient.error = None
    _ThreadsClient.delete_ok = True
    _HTTP.responses = []
    _HTTP.error = None
    _HTTP.calls = []
    yield


# ---------------------------------------------------------------------------
# Account + client resolution
# ---------------------------------------------------------------------------

class TestResolution:
    pytestmark = pytest.mark.asyncio

    async def test_account_404(self):
        with pytest.raises(HTTPException) as e:
            await threads._get_threads_account(FakeDB(), uuid.uuid4(), uuid.uuid4())
        assert e.value.status_code == 404

    async def test_account_found(self):
        a = _account()
        out = await threads._get_threads_account(FakeDB(accounts=[a]), a.team_id, a.id)
        assert out is a

    async def test_wrong_platform_404(self):
        a = _account()
        a.platform = "instagram"
        with pytest.raises(HTTPException) as e:
            await threads._get_threads_account(FakeDB(accounts=[a]), a.team_id, a.id)
        assert e.value.status_code == 404

    async def test_client_no_token_400(self):
        a = _account(access_token_enc=None)
        with pytest.raises(HTTPException) as e:
            await threads._get_threads_client(FakeDB(accounts=[a]), a.team_id, a.id)
        assert "no access token" in e.value.detail

    async def test_client_no_user_id_400(self):
        a = _account(account_id=None)
        with pytest.raises(HTTPException) as e:
            await threads._get_threads_client(FakeDB(accounts=[a]), a.team_id, a.id)
        assert "no user ID" in e.value.detail

    async def test_client_ok(self):
        a = _account()
        acct, client = await threads._get_threads_client(
            FakeDB(accounts=[a]), a.team_id, a.id,
        )
        assert acct is a and client.user_id == "123456"
        assert client.access_token == "th-tok"


# ---------------------------------------------------------------------------
# Profile + insights
# ---------------------------------------------------------------------------

class TestProfileAndInsights:
    pytestmark = pytest.mark.asyncio

    async def test_get_profile(self):
        a = _account()
        out = await threads.get_threads_profile(a.team_id, a.id, FakeDB(accounts=[a]))
        assert out.username == "cloudless.gr" and out.is_verified is True

    async def test_get_profile_api_error_maps(self):
        _ThreadsClient.error = ThreadsAPIError(429, "rate", "https://g")
        a = _account()
        with pytest.raises(HTTPException) as e:
            await threads.get_threads_profile(a.team_id, a.id, FakeDB(accounts=[a]))
        assert e.value.status_code == 429

    async def test_update_profile_no_username_400(self):
        a = _account(username=None)
        with pytest.raises(HTTPException) as e:
            await threads.update_threads_profile(
                threads.ThreadsProfileUpdateRequest(biography="x"),
                a.team_id, a.id, FakeDB(accounts=[a]),
            )
        assert e.value.status_code == 400

    async def test_update_profile_bridge_error_503(self, monkeypatch):
        from app.services.browser_bridge import BrowserBridgeError

        class Bridge:
            def __init__(self, *a, **kw):
                pass

            async def update_threads_profile(self, *a, **kw):
                raise BrowserBridgeError(503, "bridge down")

        monkeypatch.setattr(threads, "_get_browser_bridge_client", lambda: Bridge())
        a = _account()
        with pytest.raises(HTTPException) as e:
            await threads.update_threads_profile(
                threads.ThreadsProfileUpdateRequest(biography="x"),
                a.team_id, a.id, FakeDB(accounts=[a]),
            )
        assert e.value.status_code == 503

    async def test_update_profile_syncs_bio_meta(self, monkeypatch):
        class Bridge:
            def __init__(self, *a, **kw):
                pass

            async def update_threads_profile(self, *a, **kw):
                return {"status": "updated", "updated_fields": ["biography"],
                        "ignored_fields": []}

        monkeypatch.setattr(threads, "_get_browser_bridge_client", lambda: Bridge())
        a = _account(meta_data={"k": 1})
        db = FakeDB(accounts=[a])
        out = await threads.update_threads_profile(
            threads.ThreadsProfileUpdateRequest(biography="new bio"),
            a.team_id, a.id, db,
        )
        assert out.success is True and out.updated_fields == ["biography"]
        assert a.meta_data["biography"] == "new bio" and a.meta_data["k"] == 1
        assert db.commits == 1

    async def test_insights_happy(self):
        a = _account()
        out = await threads.get_threads_insights(a.team_id, a.id, "views",
                                                 FakeDB(accounts=[a]))
        assert out.metric == "views" and len(out.values) == 1

    async def test_insights_error_returns_empty(self):
        _ThreadsClient.error = ThreadsAPIError(403, "no scope", "https://g")
        a = _account()
        out = await threads.get_threads_insights(a.team_id, a.id, "views",
                                                 FakeDB(accounts=[a]))
        assert out.values == []


# ---------------------------------------------------------------------------
# Post insights / quota / list / reply / delete / followers
# ---------------------------------------------------------------------------

class TestPostsAndQuota:
    pytestmark = pytest.mark.asyncio

    async def test_post_insights_bad_media_id_400(self):
        a = _account()
        with pytest.raises(HTTPException) as e:
            await threads.get_threads_post_insights(
                "../bad", a.team_id, a.id, "views", FakeDB(accounts=[a]),
            )
        assert e.value.status_code == 400

    async def test_post_insights_happy(self):
        # NOTE: response model declares metrics: dict — the route passes
        # data["data"] through verbatim
        _HTTP.responses = [_Resp(200, {"data": {"views": {"value": 10}}})]
        a = _account()
        out = await threads.get_threads_post_insights(
            "12345", a.team_id, a.id, "views", FakeDB(accounts=[a]),
        )
        assert out.metrics == {"views": {"value": 10}}
        assert "graph.threads.net/v1.0/12345/insights" in _HTTP.calls[0][1]

    async def test_post_insights_error_status(self):
        _HTTP.responses = [_Resp(403, {}, "denied")]
        a = _account()
        with pytest.raises(HTTPException) as e:
            await threads.get_threads_post_insights(
                "12345", a.team_id, a.id, "views", FakeDB(accounts=[a]),
            )
        assert e.value.status_code == 403

    async def test_quota_parses_publish_count(self):
        _HTTP.responses = [_Resp(200, {"data": [{
            "quota_usage": [{"metric": "publish_count", "value": 37}],
            "config": {"quota_total": 250},
        }]})]
        a = _account()
        out = await threads.get_threads_quota(a.team_id, a.id, FakeDB(accounts=[a]))
        assert out.used == 37 and out.remaining == 213 and out.total == 250

    async def test_quota_error_returns_full(self):
        _HTTP.error = ConnectionError("down")
        a = _account()
        out = await threads.get_threads_quota(a.team_id, a.id, FakeDB(accounts=[a]))
        assert out.remaining == 250 and out.used == 0

    async def test_list_posts(self):
        _HTTP.responses = [_Resp(200, {
            "data": [{"id": "p1", "text": "hi", "media_type": "TEXT",
                      "permalink": "https://t/p1"}],
            "paging": {"next": "cursor"},
        })]
        a = _account()
        out = await threads.list_threads_posts(a.team_id, a.id, 25, None,
                                               FakeDB(accounts=[a]))
        assert out.posts[0].permalink == "https://t/p1"
        assert out.paging["next"] == "cursor"
        assert "after" not in _HTTP.calls[0][2]["params"]

    async def test_list_posts_after_param(self):
        _HTTP.responses = [_Resp(200, {"data": []})]
        a = _account()
        await threads.list_threads_posts(a.team_id, a.id, 10, "CUR",
                                         FakeDB(accounts=[a]))
        assert _HTTP.calls[0][2]["params"]["after"] == "CUR"

    async def test_list_posts_error(self):
        _HTTP.responses = [_Resp(500, {}, "oops")]
        a = _account()
        with pytest.raises(HTTPException) as e:
            await threads.list_threads_posts(a.team_id, a.id, 25, None,
                                             FakeDB(accounts=[a]))
        assert e.value.status_code == 500

    async def test_reply_empty_text_400(self):
        a = _account()
        with pytest.raises(HTTPException) as e:
            await threads.reply_to_thread(
                "m1", a.team_id, threads.ThreadsReplyRequest(text="  "),
                a.id, FakeDB(accounts=[a]),
            )
        assert e.value.status_code == 400

    async def test_reply_two_step_chain(self):
        _HTTP.responses = [_Resp(200, {"id": "creation-9"}),
                           _Resp(200, {"id": "published-9"})]
        a = _account()
        out = await threads.reply_to_thread(
            "m1", a.team_id, threads.ThreadsReplyRequest(text="reply!"),
            a.id, FakeDB(accounts=[a]),
        )
        assert out.media_id == "published-9"
        urls = [c[1] for c in _HTTP.calls]
        assert any("threads" in u and "publish" not in u for u in urls)
        assert any("threads_publish" in u for u in urls)
        # reply_to_id + 500-char cap sent in create payload
        create_data = _HTTP.calls[0][2]["data"]
        assert create_data["reply_to_id"] == "m1"

    async def test_reply_create_error(self):
        _HTTP.responses = [_Resp(400, {}, "bad request")]
        a = _account()
        with pytest.raises(HTTPException) as e:
            await threads.reply_to_thread(
                "m1", a.team_id, threads.ThreadsReplyRequest(text="x"),
                a.id, FakeDB(accounts=[a]),
            )
        assert "reply creation failed" in e.value.detail

    async def test_reply_no_creation_id_500(self):
        _HTTP.responses = [_Resp(200, {})]
        a = _account()
        with pytest.raises(HTTPException) as e:
            await threads.reply_to_thread(
                "m1", a.team_id, threads.ThreadsReplyRequest(text="x"),
                a.id, FakeDB(accounts=[a]),
            )
        assert e.value.status_code == 500

    async def test_reply_publish_error(self):
        _HTTP.responses = [_Resp(200, {"id": "c1"}), _Resp(400, {}, "pub fail")]
        a = _account()
        with pytest.raises(HTTPException) as e:
            await threads.reply_to_thread(
                "m1", a.team_id, threads.ThreadsReplyRequest(text="x"),
                a.id, FakeDB(accounts=[a]),
            )
        assert "reply publish failed" in e.value.detail

    async def test_delete_post(self):
        a = _account()
        out = await threads.delete_threads_post("m1", a.team_id, a.id,
                                                FakeDB(accounts=[a]))
        assert out.success is True

    async def test_delete_post_api_error(self):
        _ThreadsClient.error = ThreadsAPIError(403, "no perm", "https://g")
        a = _account()
        with pytest.raises(HTTPException) as e:
            await threads.delete_threads_post("m1", a.team_id, a.id,
                                              FakeDB(accounts=[a]))
        assert e.value.status_code == 403

    async def test_delete_post_false_400(self):
        _ThreadsClient.delete_ok = False
        a = _account()
        with pytest.raises(HTTPException) as e:
            await threads.delete_threads_post("m1", a.team_id, a.id,
                                              FakeDB(accounts=[a]))
        assert e.value.status_code == 400

    async def test_followers_happy(self):
        _HTTP.responses = [_Resp(200, {"data": [{"name": "followers_count",
                                                 "total_value": {"value": 9}}]})]
        a = _account()
        out = await threads.get_threads_followers(a.team_id, a.id,
                                                  FakeDB(accounts=[a]))
        assert out.values[0]["name"] == "followers_count"

    async def test_followers_error_empty(self):
        _HTTP.error = RuntimeError("down")
        a = _account()
        out = await threads.get_threads_followers(a.team_id, a.id,
                                                  FakeDB(accounts=[a]))
        assert out.values == []


# ---------------------------------------------------------------------------
# DM auto-reply + bridge DM endpoints
# ---------------------------------------------------------------------------

class TestDM:
    pytestmark = pytest.mark.asyncio

    async def test_dm_auto_reply_get_defaults(self):
        a = _account()
        out = await threads.get_threads_dm_auto_reply(a.id, a.team_id,
                                                      FakeDB(accounts=[a]))
        assert out.enabled is False and out.max_tokens == 250

    async def test_dm_auto_reply_update(self):
        a = _account()
        db = FakeDB(accounts=[a])
        cfg = threads.ThreadsAutoReplyConfig(enabled=False, model="ai/x")
        out = await threads.update_threads_dm_auto_reply(a.id, cfg, a.team_id, db)
        assert out.model == "ai/x"
        assert a.meta_data["threads_auto_reply"]["model"] == "ai/x"
        assert db.commits == 1

    async def test_dm_auto_reply_enabled_plan_check(self, monkeypatch):
        seen = []

        async def chk(feat, tid, db):
            seen.append(feat)

        monkeypatch.setattr("app.api.deps.check_plan_feature", chk)
        a = _account()
        cfg = threads.ThreadsAutoReplyConfig(enabled=True)
        await threads.update_threads_dm_auto_reply(a.id, cfg, a.team_id,
                                                   FakeDB(accounts=[a]))
        assert seen == ["dm_auto_reply"]

    def _bridge(self, monkeypatch, **methods):
        class Bridge:
            def __init__(self, *a, **kw):
                pass

        for name, fn in methods.items():
            setattr(Bridge, name, fn)
        monkeypatch.setattr(threads, "_get_browser_bridge_client", lambda: Bridge())

    async def test_dm_conversations(self, monkeypatch):
        async def convs(self):
            return {"threads": []}
        self._bridge(monkeypatch, get_threads_dm_conversations=convs)
        a = _account()
        out = await threads.list_threads_dm_conversations(
            a.id, a.team_id, FakeDB(accounts=[a]),
        )
        assert out == {"threads": []}

    async def test_dm_read_thread(self, monkeypatch):
        async def msgs(self, tid):
            return {"messages": [{"text": "hi"}], "tid": tid}
        self._bridge(monkeypatch, get_threads_dm_messages=msgs)
        a = _account()
        out = await threads.read_threads_dm_thread(
            a.id, "t1", a.team_id, FakeDB(accounts=[a]),
        )
        assert out["tid"] == "t1"

    async def test_dm_send_requires_text(self):
        a = _account()
        with pytest.raises(HTTPException) as e:
            await threads.send_threads_dm(a.id, "t1", {}, a.team_id,
                                          FakeDB(accounts=[a]))
        assert e.value.status_code == 400

    async def test_dm_send_ok(self, monkeypatch):
        async def send(self, tid, text):
            return {"sent": True, "tid": tid, "text": text}
        self._bridge(monkeypatch, send_threads_dm_message=send)
        a = _account()
        out = await threads.send_threads_dm(
            a.id, "t1", {"text": "yo"}, a.team_id, FakeDB(accounts=[a]),
        )
        assert out["sent"] is True

    async def test_dm_bridge_error_maps_status(self, monkeypatch):
        from app.services.browser_bridge import BrowserBridgeError

        async def convs(self):
            raise BrowserBridgeError(503, "bridge err")
        self._bridge(monkeypatch, get_threads_dm_conversations=convs)
        a = _account()
        with pytest.raises(HTTPException) as e:
            await threads.list_threads_dm_conversations(
                a.id, a.team_id, FakeDB(accounts=[a]),
            )
        assert e.value.status_code == 503


# ---------------------------------------------------------------------------
# Session helpers (DM login + status)
# ---------------------------------------------------------------------------

class TestSessionHelpers:
    pytestmark = pytest.mark.asyncio

    async def test_dm_login_navigates(self, monkeypatch):
        navs = []

        class Bridge:
            def __init__(self, *a, **kw):
                pass

            async def navigate(self, url):
                navs.append(url)

        monkeypatch.setattr(threads, "_get_browser_bridge_client", lambda: Bridge())
        a = _account()
        out = await threads.threads_dm_login(a.id, a.team_id, FakeDB(accounts=[a]))
        assert out["status"] == "ok" and navs == ["https://www.threads.com/login"]

    async def test_session_status_full(self, monkeypatch):
        import asyncio

        async def no_sleep(*a, **k):
            return None
        monkeypatch.setattr(asyncio, "sleep", no_sleep)

        class Bridge:
            def __init__(self, *a, **kw):
                pass

            async def session_status(self):
                return {"state": "active"}

            async def navigate(self, url):
                pass

            async def evaluate(self, js):
                return {"url": "https://www.threads.com/", "logged_in": True}

        monkeypatch.setattr(threads, "_get_browser_bridge_client", lambda: Bridge())
        a = _account()
        out = await threads.threads_dm_session_status(
            a.id, a.team_id, FakeDB(accounts=[a]),
        )
        assert out["browser_session"]["state"] == "active"
        assert out["threads_session"]["logged_in"] is True

    async def test_session_status_inner_failure(self, monkeypatch):
        class Bridge:
            def __init__(self, *a, **kw):
                pass

            async def session_status(self):
                return {"state": "active"}

            async def navigate(self, url):
                raise RuntimeError("nav fail")

        monkeypatch.setattr(threads, "_get_browser_bridge_client", lambda: Bridge())
        a = _account()
        out = await threads.threads_dm_session_status(
            a.id, a.team_id, FakeDB(accounts=[a]),
        )
        assert out["threads_session"]["logged_in"] is False
        assert "error" in out["threads_session"]

    async def test_session_status_bridge_down(self, monkeypatch):
        class Bridge:
            def __init__(self, *a, **kw):
                pass

            async def session_status(self):
                raise ConnectionError("dead")

        monkeypatch.setattr(threads, "_get_browser_bridge_client", lambda: Bridge())
        a = _account()
        out = await threads.threads_dm_session_status(
            a.id, a.team_id, FakeDB(accounts=[a]),
        )
        assert out["threads_session"]["logged_in"] is False
        assert "error" in out["browser_session"]
