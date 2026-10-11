"""Coverage for app/api/linkedin.py router."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

import app.api.linkedin as L
from app.services.linkedin_api import LinkedInAPIError


class _Res:
    def __init__(self, scalar=None, all_items=None, scalars_all=None):
        self._scalar = scalar
        self._all = all_items or []
        self._scalars = scalars_all if scalars_all is not None else []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: self._scalars)

    def all(self):
        return self._all


class _DB:
    def __init__(self, team=None, results=None):
        self._team = team
        self._q = list(results or [])
        self.commits = 0

    async def get(self, model, key):
        return self._team

    async def execute(self, *a, **kw):
        return self._q.pop(0)

    async def commit(self):
        self.commits += 1


_TEAM = SimpleNamespace(id=uuid.uuid4())


def _user():
    return SimpleNamespace(id=uuid.uuid4(), email="a@x.co")


def _account(**kw):
    d = dict(
        id=uuid.uuid4(), team_id=_TEAM.id, status="active", platform="linkedin", access_token_enc=b"e", account_id="123", username="cloudless", meta_data={}
    )
    d.update(kw)
    return SimpleNamespace(**d)


def _err(code=400):
    return LinkedInAPIError(code, "bad stuff", "url")


# ── AI generation endpoints ─────────────────────────────────────────────────


class TestAIGeneration:
    @pytest.mark.asyncio
    async def test_team_missing(self):
        db = _DB(team=None)
        with pytest.raises(HTTPException) as ei:
            await L.linkedin_generate_post(L.GeneratePostRequest(topic="t"), _TEAM.id, _user(), db)
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_generate_post(self, monkeypatch):
        gen = AsyncMock(return_value={"caption": "c", "content": "x", "hashtags": ["a"], "topic": "t", "tone": "p", "length": "m"})
        monkeypatch.setattr(L, "generate_linkedin_post", gen)
        out = await L.linkedin_generate_post(L.GeneratePostRequest(topic="t"), _TEAM.id, _user(), _DB(team=_TEAM))
        assert out.caption == "c"
        assert gen.await_args.kwargs["team_id"] == _TEAM.id

    @pytest.mark.asyncio
    async def test_generate_article(self, monkeypatch):
        monkeypatch.setattr(
            L,
            "generate_linkedin_article",
            AsyncMock(
                return_value={
                    "title": "T",
                    "subtitle": "s",
                    "sections": [{"heading": "h", "body": "b"}],
                    "body": "B",
                    "takeaways": ["t"],
                    "cta": "c",
                    "topic": "t",
                    "tone": "p",
                }
            ),
        )
        out = await L.linkedin_generate_article(L.GenerateArticleRequest(topic="t"), _TEAM.id, _user(), _DB(team=_TEAM))
        assert out.title == "T" and out.sections[0].heading == "h"

    @pytest.mark.asyncio
    async def test_generate_hashtags(self, monkeypatch):
        monkeypatch.setattr(L, "generate_linkedin_hashtags", AsyncMock(return_value=["#a", "#b"]))
        out = await L.linkedin_generate_hashtags(L.GenerateHashtagsRequest(content="c"), _TEAM.id, _user(), _DB(team=_TEAM))
        assert out.hashtags == ["#a", "#b"]

    @pytest.mark.asyncio
    async def test_improve_post(self, monkeypatch):
        monkeypatch.setattr(L, "improve_linkedin_post", AsyncMock(return_value={"improved_content": "better", "changes": ["x"], "hashtags": ["h"]}))
        out = await L.linkedin_improve_post(L.ImprovePostRequest(content="c"), _TEAM.id, _user(), _DB(team=_TEAM))
        assert out.improved_content == "better"

    @pytest.mark.asyncio
    async def test_generate_comment(self, monkeypatch):
        monkeypatch.setattr(L, "generate_linkedin_comment", AsyncMock(return_value="nice take"))
        out = await L.linkedin_generate_comment(L.GenerateCommentRequest(post_text="p"), _TEAM.id, _user(), _DB(team=_TEAM))
        assert out.comment == "nice take"


class TestBestTime:
    @pytest.mark.asyncio
    async def test_typed_accounts_with_samples(self, monkeypatch):
        acct_id = uuid.uuid4()
        samples = [(datetime.now(UTC), 0.05), (datetime.now(UTC), 0.1)]
        db = _DB(
            team=_TEAM,
            results=[
                _Res(scalars_all=[acct_id]),  # typed accounts
                _Res(all_items=samples),  # engagement samples
            ],
        )
        monkeypatch.setattr(L, "rank_best_time_windows", lambda s: [{"hour": 9, "sample_size": len(s)}])
        out = await L.linkedin_best_time(_TEAM.id, "organization", _user(), db)
        assert out.source == "analytics"
        assert out.posts_analyzed == 2
        assert out.best_times[0]["hour"] == 9

    @pytest.mark.asyncio
    async def test_no_typed_falls_back_to_all(self, monkeypatch):
        db = _DB(
            team=_TEAM,
            results=[
                _Res(scalars_all=[]),  # typed → empty
                _Res(scalars_all=[uuid.uuid4()]),  # all accounts
                _Res(all_items=[]),  # no samples
            ],
        )
        monkeypatch.setattr(L, "suggest_best_time_to_post", AsyncMock(return_value=[{"hour": 10}]))
        out = await L.linkedin_best_time(_TEAM.id, "person", _user(), db)
        assert out.source == "defaults"
        assert out.posts_analyzed == 0

    @pytest.mark.asyncio
    async def test_no_accounts_defaults(self, monkeypatch):
        db = _DB(team=_TEAM, results=[_Res(scalars_all=[]), _Res(scalars_all=[])])
        monkeypatch.setattr(L, "suggest_best_time_to_post", AsyncMock(return_value=[{"hour": 8}]))
        out = await L.linkedin_best_time(_TEAM.id, "org", _user(), db)
        assert out.source == "defaults"

    @pytest.mark.asyncio
    async def test_windows_without_sample_size(self, monkeypatch):
        db = _DB(
            team=_TEAM,
            results=[
                _Res(scalars_all=[uuid.uuid4()]),
                _Res(all_items=[(datetime.now(UTC), 0.1)]),
            ],
        )
        monkeypatch.setattr(L, "rank_best_time_windows", lambda s: [{"hour": 9}])  # no sample_size key
        out = await L.linkedin_best_time(_TEAM.id, "org", _user(), db)
        assert out.source == "defaults"
        assert out.posts_analyzed == 1


# ── Account helpers ──────────────────────────────────────────────────────────


class TestAccountHelpers:
    @pytest.mark.asyncio
    async def test_missing_404(self):
        db = _DB(results=[_Res(scalar=None)])
        with pytest.raises(HTTPException) as ei:
            await L._load_linkedin_account(db, uuid.uuid4(), _user())
        assert ei.value.status_code == 404

    @pytest.mark.asyncio
    async def test_inactive_400(self):
        db = _DB(results=[_Res(scalar=_account(status="revoked"))])
        with pytest.raises(HTTPException) as ei:
            await L._load_linkedin_account(db, uuid.uuid4(), _user())
        assert ei.value.status_code == 400

    def test_author_urn(self):
        assert L._author_urn_for_account(_account(meta_data={"author_urn": "urn:li:person:X"})) == "urn:li:person:X"
        assert L._author_urn_for_account(_account(meta_data={"account_type": "organization"})) == "urn:li:organization:123"
        assert L._author_urn_for_account(_account(meta_data={"account_type": "company"})) == "urn:li:organization:123"
        assert L._author_urn_for_account(_account()) == "urn:li:person:123"

    def test_org_urn(self):
        assert L._org_urn_for_account(_account(meta_data={"author_urn": "urn:li:organization:9"})) == "urn:li:organization:9"
        assert L._org_urn_for_account(_account(meta_data={"author_urn": "urn:li:person:9"})) == "urn:li:organization:123"
        assert L._org_urn_for_account(_account()) == "urn:li:organization:123"


def _li_client(monkeypatch, **methods):
    monkeypatch.setattr(L, "decrypt_token", lambda e: "tok")
    fake = SimpleNamespace(**{k: AsyncMock(return_value=v) for k, v in methods.items()})
    monkeypatch.setattr(L, "LinkedInAPIClient", lambda **kw: fake)
    return fake


class TestValidate:
    @pytest.mark.asyncio
    async def test_happy(self, monkeypatch):
        org = SimpleNamespace(urn="urn:li:organization:1", id="1", name="Co", vanity_name="co", role="ADMIN")
        _li_client(monkeypatch, validate_token={"sub": "me"}, get_member_organizations=[org])
        acct = _account()
        db = _DB(results=[_Res(scalar=acct)])
        out = await L.validate_linkedin_account(acct.id, _user(), db)
        assert out["valid"] is True
        assert out["organizations"][0]["vanity_name"] == "co"

    @pytest.mark.asyncio
    async def test_token_error(self, monkeypatch):
        fake = _li_client(monkeypatch)
        fake.validate_token = AsyncMock(side_effect=_err(401))
        db = _DB(results=[_Res(scalar=_account())])
        with pytest.raises(HTTPException) as ei:
            await L.validate_linkedin_account(uuid.uuid4(), _user(), db)
        assert ei.value.status_code == 401


class TestFollowersAnalytics:
    @pytest.mark.asyncio
    async def test_followers(self, monkeypatch):
        _li_client(monkeypatch, get_follower_count=42)
        acct = _account()
        db = _DB(results=[_Res(scalar=acct)])
        out = await L.linkedin_follower_count(acct.id, _user(), db)
        assert out["followers"] == 42
        assert out["org_urn"] == "urn:li:organization:123"

    @pytest.mark.asyncio
    async def test_post_analytics(self, monkeypatch):
        _li_client(monkeypatch, get_post_analytics={"likes": 5})
        db = _DB(results=[_Res(scalar=_account())])
        out = await L.linkedin_post_analytics("urn:li:share:1", uuid.uuid4(), _user(), db)
        assert out["stats"]["likes"] == 5

    @pytest.mark.asyncio
    async def test_post_analytics_error(self, monkeypatch):
        fake = _li_client(monkeypatch)
        fake.get_post_analytics = AsyncMock(side_effect=_err(403))
        db = _DB(results=[_Res(scalar=_account())])
        with pytest.raises(HTTPException) as ei:
            await L.linkedin_post_analytics("u", uuid.uuid4(), _user(), db)
        assert ei.value.status_code == 403

    @pytest.mark.asyncio
    async def test_post_analytics_empty_404(self, monkeypatch):
        _li_client(monkeypatch, get_post_analytics=None)
        db = _DB(results=[_Res(scalar=_account())])
        with pytest.raises(HTTPException) as ei:
            await L.linkedin_post_analytics("u", uuid.uuid4(), _user(), db)
        assert ei.value.status_code == 404

    @pytest.mark.asyncio
    async def test_org_analytics(self, monkeypatch):
        _li_client(monkeypatch, get_organization_lifetime_stats={"followers": 100})
        db = _DB(results=[_Res(scalar=_account())])
        out = await L.linkedin_organization_analytics(uuid.uuid4(), _user(), db)
        assert out["stats"]["followers"] == 100

    @pytest.mark.asyncio
    async def test_org_analytics_error(self, monkeypatch):
        fake = _li_client(monkeypatch)
        fake.get_organization_lifetime_stats = AsyncMock(side_effect=_err(500))
        db = _DB(results=[_Res(scalar=_account())])
        with pytest.raises(HTTPException):
            await L.linkedin_organization_analytics(uuid.uuid4(), _user(), db)

    @pytest.mark.asyncio
    async def test_org_analytics_empty_404(self, monkeypatch):
        _li_client(monkeypatch, get_organization_lifetime_stats={})
        db = _DB(results=[_Res(scalar=_account())])
        with pytest.raises(HTTPException) as ei:
            await L.linkedin_organization_analytics(uuid.uuid4(), _user(), db)
        assert ei.value.status_code == 404


class TestPublishComment:
    @pytest.mark.asyncio
    async def test_publish(self, monkeypatch):
        # dataclasses.asdict needs a real dataclass
        import dataclasses

        @dataclasses.dataclass
        class _R:
            success: bool = True
            platform_post_id: str = "p1"
            platform_url: str = "u1"
            error: str | None = None

        fake = _li_client(monkeypatch, create_post=_R())
        db = _DB(results=[_Res(scalar=_account())])
        req = L.PublishPostRequest(account_id=uuid.uuid4(), commentary="hi", link_url="http://x")
        out = await L.linkedin_publish_post(req, _user(), db)
        assert out.success and out.platform_post_id == "p1"
        assert fake.create_post.await_args.kwargs["link_url"] == "http://x"
        assert fake.create_post.await_args.kwargs["author_urn"] == ("urn:li:person:123")

    @pytest.mark.asyncio
    async def test_comment(self, monkeypatch):
        import dataclasses

        @dataclasses.dataclass
        class _R:
            success: bool = True
            platform_post_id: str = "c1"
            platform_url: str | None = None
            error: str | None = None

        fake = _li_client(monkeypatch, create_comment=_R())
        db = _DB(results=[_Res(scalar=_account())])
        req = L.CommentRequest(account_id=uuid.uuid4(), post_urn="urn:x", text="nice")
        out = await L.linkedin_post_comment(req, _user(), db)
        assert out.platform_post_id == "c1"
        assert fake.create_comment.await_args.kwargs["creator_urn"] == "urn:li:person:123"


class TestCompanyPageUrl:
    @pytest.mark.asyncio
    async def test_vanity(self):
        acct = _account(meta_data={"vanity_name": "cloudless-gr"})
        db = _DB(results=[_Res(scalar=acct)])
        out = await L.linkedin_company_page_url(uuid.uuid4(), _user(), db)
        assert out["url"] == ("https://www.linkedin.com/company/cloudless-gr")

    @pytest.mark.asyncio
    async def test_username_fallback(self):
        db = _DB(results=[_Res(scalar=_account(meta_data=None))])
        out = await L.linkedin_company_page_url(uuid.uuid4(), _user(), db)
        assert "cloudless" in out["url"]

    @pytest.mark.asyncio
    async def test_no_vanity(self):
        db = _DB(results=[_Res(scalar=_account(username=None, meta_data=None))])
        out = await L.linkedin_company_page_url(uuid.uuid4(), _user(), db)
        assert out["url"] is None


class TestDmAutoReply:
    @pytest.mark.asyncio
    async def test_get_defaults(self):
        db = _DB(results=[_Res(scalar=_account(meta_data=None))])
        out = await L.get_linkedin_dm_auto_reply(uuid.uuid4(), _user(), db)
        assert out.enabled is False
        assert out.cooldown_seconds == 300

    @pytest.mark.asyncio
    async def test_get_saved(self):
        acct = _account(meta_data={"linkedin_auto_reply": {"enabled": True, "max_tokens": 100, "fallback_text": "custom"}})
        db = _DB(results=[_Res(scalar=acct)])
        out = await L.get_linkedin_dm_auto_reply(uuid.uuid4(), _user(), db)
        assert out.enabled is True
        assert out.max_tokens == 100
        assert out.fallback_text == "custom"

    @pytest.mark.asyncio
    async def test_update_disabled_no_quota(self, monkeypatch):
        monkeypatch.setattr("sqlalchemy.orm.attributes.flag_modified", lambda *a: None)
        acct = _account()
        db = _DB(results=[_Res(scalar=acct)])
        body = L.LinkedInAutoReplyConfig(enabled=False)
        await L.update_linkedin_dm_auto_reply(uuid.uuid4(), body, _user(), db)
        assert db.commits == 1
        assert acct.meta_data["linkedin_auto_reply"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_update_enabled_checks_quota(self, monkeypatch):
        monkeypatch.setattr("sqlalchemy.orm.attributes.flag_modified", lambda *a: None)
        acct = _account()
        db = _DB(results=[_Res(scalar=acct)])
        import app.api.deps as deps

        check = AsyncMock()
        monkeypatch.setattr(deps, "check_plan_feature", check)
        body = L.LinkedInAutoReplyConfig(enabled=True)
        await L.update_linkedin_dm_auto_reply(uuid.uuid4(), body, _user(), db)
        check.assert_awaited_once()
        assert db.commits == 1


def _sidecar(monkeypatch, **methods):
    import app.services.linkedin_sidecar as sc

    fake = SimpleNamespace(**{k: AsyncMock(return_value=v) for k, v in methods.items()})
    monkeypatch.setattr(sc, "LinkedInSidecarClient", lambda: fake)
    return fake


def _sc_err(code=500):
    import app.services.linkedin_sidecar as sc

    return sc.LinkedInSidecarError(code, "sidecar fail")


class TestDmSidecar:
    @pytest.mark.asyncio
    async def test_conversations(self, monkeypatch):
        _sidecar(monkeypatch, get_conversations={"conversations": []})
        db = _DB(results=[_Res(scalar=_account())])
        out = await L.list_linkedin_dm_conversations(uuid.uuid4(), _user(), db)
        assert out == {"conversations": []}

    @pytest.mark.asyncio
    async def test_conversations_error(self, monkeypatch):
        fake = _sidecar(monkeypatch)
        fake.get_conversations = AsyncMock(side_effect=_sc_err(503))
        db = _DB(results=[_Res(scalar=_account())])
        with pytest.raises(HTTPException) as ei:
            await L.list_linkedin_dm_conversations(uuid.uuid4(), _user(), db)
        assert ei.value.status_code == 503

    @pytest.mark.asyncio
    async def test_thread(self, monkeypatch):
        _sidecar(monkeypatch, get_thread_messages={"messages": []})
        db = _DB(results=[_Res(scalar=_account())])
        out = await L.read_linkedin_dm_thread(uuid.uuid4(), "t1", _user(), db)
        assert out == {"messages": []}

    @pytest.mark.asyncio
    async def test_thread_error(self, monkeypatch):
        fake = _sidecar(monkeypatch)
        fake.get_thread_messages = AsyncMock(side_effect=_sc_err(404))
        db = _DB(results=[_Res(scalar=_account())])
        with pytest.raises(HTTPException):
            await L.read_linkedin_dm_thread(uuid.uuid4(), "t", _user(), db)

    @pytest.mark.asyncio
    async def test_send(self, monkeypatch):
        fake = _sidecar(monkeypatch, send_message={"ok": True})
        db = _DB(results=[_Res(scalar=_account())])
        out = await L.send_linkedin_dm(uuid.uuid4(), "t1", {"text": "hi"}, _user(), db)
        assert out == {"ok": True}
        fake.send_message.assert_awaited_once_with("t1", "hi")

    @pytest.mark.asyncio
    async def test_send_no_text(self, monkeypatch):
        db = _DB(results=[_Res(scalar=_account())])
        with pytest.raises(HTTPException) as ei:
            await L.send_linkedin_dm(uuid.uuid4(), "t", {}, _user(), db)
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_send_error(self, monkeypatch):
        fake = _sidecar(monkeypatch)
        fake.send_message = AsyncMock(side_effect=_sc_err(500))
        db = _DB(results=[_Res(scalar=_account())])
        with pytest.raises(HTTPException):
            await L.send_linkedin_dm(uuid.uuid4(), "t", {"text": "x"}, _user(), db)


class TestAdsControl:
    @pytest.mark.asyncio
    async def test_queued(self, monkeypatch):
        import app.worker.tasks.linkedin_ads_control as mod

        task = Mock()
        monkeypatch.setattr(mod, "linkedin_ads_control", task)
        out = await L.linkedin_ads_control(L.AdsControlRequest(action=" PAUSE "), _user())
        assert out == {"ok": True, "queued": "pause"}
        task.delay.assert_called_once_with(action="pause", response_url=None, user="a@x.co")

    @pytest.mark.asyncio
    async def test_bad_action(self):
        with pytest.raises(HTTPException) as ei:
            await L.linkedin_ads_control(L.AdsControlRequest(action="explode"), _user())
        assert ei.value.status_code == 400
