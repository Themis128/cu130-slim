"""Coverage gaps: linkedin_api member groups, v2 fallbacks, comment/delete ids."""

from __future__ import annotations

import pytest

from app.services import linkedin_api as api


class _Resp:
    def __init__(self, status=200, body=None, headers=None, text=""):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = text or str(self._body)

    def json(self):
        return self._body


class _Client:
    """Queues responses per instantiation; records calls."""

    _queue: list = []
    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _next(self, method, url, **kw):
        type(self).calls.append({"method": method, "url": url, **kw})
        return type(self)._queue.pop(0) if type(self)._queue else _Resp(500)

    async def get(self, url, **kw):
        return await self._next("GET", url, **kw)

    async def post(self, url, **kw):
        return await self._next("POST", url, **kw)

    async def delete(self, url, **kw):
        return await self._next("DELETE", url, **kw)


@pytest.fixture
def client():
    return api.LinkedInAPIClient(access_token="tok")


@pytest.fixture(autouse=True)
def _reset():
    _Client._queue = []
    _Client.calls = []
    yield
    _Client._queue = []
    _Client.calls = []


def _use(*responses):
    _Client._queue = list(responses)


@pytest.fixture(autouse=True)
def _patch_httpx(monkeypatch):
    monkeypatch.setattr(api.httpx, "AsyncClient", _Client)


# ── get_member_organizations arcs (name_field dict / non-200 org) ────────


@pytest.mark.asyncio
async def test_member_orgs_dict_name_and_org_404(client):
    _use(
        _Resp(200, {"elements": [{"organization": "urn:li:organization:7", "role": "ADMINISTRATOR"}]}),
        _Resp(404),  # REST org lookup fails -> v2
        _Resp(200, {"localizedName": {"localized": {"en_US": "Org Seven"}}, "vanityName": "org7"}),
    )
    orgs = await client.get_member_organizations()
    assert len(orgs) == 1 and orgs[0].name == "Org Seven" and orgs[0].vanity_name == "org7"


@pytest.mark.asyncio
async def test_member_orgs_bad_urn_skipped(client):
    _use(
        _Resp(
            200,
            {
                "elements": [
                    {"organization": "not-an-org-urn"},
                    {"organizationalTarget": "urn:li:organization:8"},
                ]
            },
        ),
        _Resp(200, {"name": {"localized": {"fr_FR": "Huit"}}}),
    )
    orgs = await client.get_member_organizations()
    assert len(orgs) == 1 and orgs[0].name == "Huit"


# ── get_member_groups ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_member_groups_success_and_fallback(client):
    _use(
        _Resp(200, {"sub": "person-1"}),  # userinfo
        _Resp(400),  # REST memberships fail
        _Resp(200, {"elements": [{"group": "urn:li:group:1", "name": "G1"}]}),
    )
    out = await client.get_member_groups()
    assert out == [{"urn": "urn:li:group:1", "name": "G1"}]
    assert "member=urn%3Ali%3Aperson%3Aperson-1" in _Client.calls[-1]["url"] or "member=" in _Client.calls[-1]["url"]


@pytest.mark.asyncio
async def test_member_groups_userinfo_fails_and_all_400(client):
    _use(
        _Resp(500),  # userinfo fails
        _Resp(400),
        _Resp(400),  # both membership urls fail
    )
    assert await client.get_member_groups() == []


# ── get_follower_count: list response + all-fail ─────────────────────────


@pytest.mark.asyncio
async def test_follower_count_list_response(client):
    _use(_Resp(200, [{"firstDegreeSize": 55}]))
    assert await client.get_follower_count("urn:li:organization:1") == 55


@pytest.mark.asyncio
async def test_follower_count_all_fail_returns_zero(client):
    _use(_Resp(403), _Resp(403))
    assert await client.get_follower_count("urn:li:organization:1") == 0


# ── create_post v2 fallback success ──────────────────────────────────────


@pytest.mark.asyncio
async def test_create_post_v2_fallback_success(client):
    _use(
        _Resp(403, {"error": "gated"}),
        _Resp(201, {"id": "urn:li:ugcPost:99"}),
    )
    out = await client.create_post("urn:li:person:1", "hello")
    assert out.success is True and out.platform_post_id == "urn:li:ugcPost:99"


# ── create_comment: id fallbacks ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_comment_id_from_json_and_location(client):
    # no x-restli-id header -> json id
    _use(_Resp(200, {"id": "comment-1"}))
    out = await client.create_comment("urn:li:share:1", "nice", creator_urn="urn:li:person:1")
    assert out.success and out.platform_post_id == "comment-1"

    # neither header nor json id -> location header rsplit
    _use(_Resp(200, {}, headers={"location": "https://api.linkedin.com/rest/socialActions/urn:li:comment:77"}))
    out = await client.create_comment("urn:li:share:1", "nice", creator_urn="urn:li:person:1")
    assert out.platform_post_id == "urn:li:comment:77"


@pytest.mark.asyncio
async def test_create_comment_v2_fallback(client):
    _use(_Resp(403), _Resp(201, {}, headers={"x-restli-id": "urn:li:comment:5"}))
    out = await client.create_comment("urn:li:share:1", "x", creator_urn="urn:li:person:1")
    assert out.success and out.platform_post_id == "urn:li:comment:5"


@pytest.mark.asyncio
async def test_create_comment_group_post_thread_urn_retry(client):
    """Group posts 400 with 'actual threadUrn: urn:li:groupPost:…' — retry once."""
    bad = (
        '{"message":"Provided threadUrn: urn:li:activity:111 is not the same as '
        'the actual threadUrn: urn:li:groupPost:961087-222","status":400}'
    )
    _use(
        _Resp(400, {}, text=bad),
        _Resp(200, {"id": "comment-9"}),
    )
    out = await client.create_comment("urn:li:activity:111", "nice", creator_urn="urn:li:person:1")
    assert out.success and out.platform_post_id == "comment-9"
    assert len(_Client.calls) == 2
    assert "groupPost" in _Client.calls[1]["url"]
    assert _Client.calls[1]["json"]["object"] == "urn:li:groupPost:961087-222"


@pytest.mark.asyncio
async def test_create_comment_group_post_retry_still_fails(client):
    bad = '{"message":"actual threadUrn: urn:li:groupPost:961087-222","status":400}'
    _use(_Resp(400, {}, text=bad), _Resp(400, {}, text="still bad"))
    out = await client.create_comment("urn:li:activity:111", "x", creator_urn="urn:li:person:1")
    assert out.success is False
    assert len(_Client.calls) == 2  # one correction retry, then give up


@pytest.mark.asyncio
async def test_create_comment_400_without_thread_hint_no_retry(client):
    # 400 with no "actual threadUrn" hint, and a hint naming the same URN: no retry.
    _use(_Resp(400, {}, text="bad request"))
    out = await client.create_comment("urn:li:activity:111", "x", creator_urn="urn:li:person:1")
    assert out.success is False and len(_Client.calls) == 1

    _Client.calls.clear()
    same = '{"message":"actual threadUrn: urn:li:activity:111","status":400}'
    _use(_Resp(400, {}, text=same))
    out = await client.create_comment("urn:li:activity:111", "x", creator_urn="urn:li:person:1")
    assert out.success is False and len(_Client.calls) == 1


@pytest.mark.asyncio
async def test_create_comment_thread_hint_failing_validation_no_retry(client, monkeypatch):
    real = api._validate_urn

    def _strict(urn):
        if "groupPost" in urn:
            raise ValueError("rejected")
        return real(urn)

    monkeypatch.setattr(api, "_validate_urn", _strict)
    bad = '{"message":"actual threadUrn: urn:li:groupPost:961087-222","status":400}'
    _use(_Resp(400, {}, text=bad))
    out = await client.create_comment("urn:li:activity:111", "x", creator_urn="urn:li:person:1")
    assert out.success is False and len(_Client.calls) == 1


# ── delete_post v2 fallback ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_post_v2_fallback(client):
    _use(_Resp(403), _Resp(204))
    out = await client.delete_post("urn:li:share:abc")
    assert out.success is True


@pytest.mark.asyncio
async def test_delete_post_v2_fallback_still_fails(client):
    _use(_Resp(426), _Resp(500, "err"))
    out = await client.delete_post("urn:li:share:abc")
    assert out.success is False


# ── get_post_analytics arcs ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_analytics_unknown_kind_and_empty(client):
    # urn with no recognizable kind -> tries ugcPosts then shares, both 404 -> {}
    _use(_Resp(404), _Resp(404))
    out = await client.get_post_analytics("urn:li:unknown:1", "urn:li:organization:2")
    assert out == {}


@pytest.mark.asyncio
async def test_post_analytics_5xx_raises(client):
    _use(_Resp(503, "down"))
    with pytest.raises(api.LinkedInAPIError):
        await client.get_post_analytics("urn:li:share:1", "urn:li:organization:2")


# ── remaining arcs ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_member_orgs_all_acl_urls_empty(client):
    # every ACL URL returns 200 with empty elements -> no break, orgs []
    _use(
        _Resp(200, {"elements": []}),
        _Resp(200, {"elements": []}),
        _Resp(200, {"elements": []}),
    )
    assert await client.get_member_organizations() == []


@pytest.mark.asyncio
async def test_member_orgs_acl_non200_then_ok(client):
    _use(
        _Resp(500),
        _Resp(200, {"elements": [{"organization": "urn:li:organization:9"}]}),
        _Resp(200, {"name": "Nine"}),
    )
    orgs = await client.get_member_organizations()
    assert len(orgs) == 1 and orgs[0].name == "Nine"


@pytest.mark.asyncio
async def test_member_orgs_dict_name_empty_localized(client):
    # name_field dict, localized dict empty -> inner if False -> default name
    _use(
        _Resp(200, {"elements": [{"organization": "urn:li:organization:10"}]}),
        _Resp(200, {"localizedName": {"localized": {}}, "vanityName": "v10"}),
    )
    orgs = await client.get_member_organizations()
    assert orgs[0].name.startswith("LinkedIn Page")
    assert orgs[0].vanity_name == "v10"


class _BadJsonResp(_Resp):
    def json(self):
        raise ValueError("not json")


@pytest.mark.asyncio
async def test_member_groups_userinfo_json_raises(client):
    _use(
        _BadJsonResp(200, {}),  # userinfo 200 but json() raises -> except: pass
        _Resp(400),
        _Resp(400),
    )
    assert await client.get_member_groups() == []


@pytest.mark.asyncio
async def test_member_groups_get_raises_in_loop(client, monkeypatch):
    # first memberships GET raises -> except: continue -> second also raises -> []
    _use(_Resp(200, {"sub": "p1"}))

    async def _boom(self, url, **kw):
        raise RuntimeError("conn reset")

    monkeypatch.setattr(_Client, "get", _boom)
    assert await client.get_member_groups() == []


@pytest.mark.asyncio
async def test_follower_count_empty_list_then_dict(client):
    # first URL 200 with scalar json -> neither dict nor list -> next URL dict
    _use(_Resp(200, "str"), _Resp(200, {"firstDegreeSize": 33}))
    assert await client.get_follower_count("urn:li:organization:1") == 33


@pytest.mark.asyncio
async def test_member_orgs_no_name_fields(client):
    # localizedName and name both absent -> name_field None -> elif False arc
    _use(
        _Resp(200, {"elements": [{"organization": "urn:li:organization:11"}]}),
        _Resp(200, {"vanityName": "noname"}),
    )
    orgs = await client.get_member_organizations()
    assert orgs[0].name.startswith("LinkedIn Page")
