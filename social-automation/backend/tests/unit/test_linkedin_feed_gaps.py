"""Coverage for linkedin_feed reads/writes paths not hit by the main suite."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import linkedin_feed as LF


class _AsyncCM:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *a):
        return False


class _Resp:
    def __init__(self, status=200, body=None, headers=None, text=None):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}
        self.text = text if text is not None else str(self._body)

    def json(self):
        return self._body


def _scalars_first(value):
    return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: value))


class _DB:
    def __init__(self, account=None):
        self._account = account

    async def execute(self, stmt):
        return _scalars_first(self._account)


def _patch_ctx(monkeypatch, ctx=("tok", "urn:li:person:1")):
    monkeypatch.setattr(LF, "member_ctx", AsyncMock(return_value=ctx))
    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=ctx))


def _patch_http(monkeypatch, responses):
    calls = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def _next(self, method, *a, **k):
            calls.append((method, a, k))
            return responses.pop(0)

        async def get(self, *a, **k):
            return self._next("get", *a, **k)

        async def post(self, *a, **k):
            return self._next("post", *a, **k)

        async def delete(self, *a, **k):
            return self._next("delete", *a, **k)

    monkeypatch.setattr(LF.httpx, "AsyncClient", _Client)
    return calls


# ── _account_ctx / member_ctx / org_ctx real DB path ────────────────────


@pytest.mark.asyncio
async def test_member_ctx_resolves_account(monkeypatch):
    acc = SimpleNamespace(access_token_enc=b"enc", account_id="12345")
    monkeypatch.setattr(LF, "task_session", lambda: _AsyncCM(_DB(acc)))
    monkeypatch.setattr(LF, "decrypt_token", lambda b: "decrypted")
    token, urn = await LF.member_ctx()
    assert token == "decrypted"
    assert urn == "urn:li:person:12345"


@pytest.mark.asyncio
async def test_org_ctx_resolves_organization_urn(monkeypatch):
    acc = SimpleNamespace(access_token_enc=b"enc", account_id="999")
    monkeypatch.setattr(LF, "task_session", lambda: _AsyncCM(_DB(acc)))
    monkeypatch.setattr(LF, "decrypt_token", lambda b: "tok")
    token, urn = await LF.org_ctx()
    assert urn == "urn:li:organization:999"


@pytest.mark.asyncio
async def test_ctx_none_without_account_or_token(monkeypatch):
    monkeypatch.setattr(LF, "task_session", lambda: _AsyncCM(_DB(None)))
    assert await LF.member_ctx() is None
    acc = SimpleNamespace(access_token_enc=None, account_id="1")
    monkeypatch.setattr(LF, "task_session", lambda: _AsyncCM(_DB(acc)))
    assert await LF.org_ctx() is None


# ── reads: social_metadata + list_comments ──────────────────────────────


@pytest.mark.asyncio
async def test_social_metadata_ok_and_empty(monkeypatch):
    _patch_ctx(monkeypatch)
    _patch_http(monkeypatch, [_Resp(200, {"results": {"urn:li:share:1": {"totalSocialActivityCounts": {}}}})])
    out = await LF.social_metadata(["urn:li:share:1"])
    assert out["status"] == 200
    assert "urn:li:share:1" in out["metadata"]

    out = await LF.social_metadata([])
    assert out == {"metadata": {}}


@pytest.mark.asyncio
async def test_social_metadata_no_ctx_and_error(monkeypatch):
    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=None))
    out = await LF.social_metadata(["urn:li:share:1"])
    assert out["error"] == "no-linkedin-account"

    _patch_ctx(monkeypatch)
    _patch_http(monkeypatch, [_Resp(500, {}, text="server error")])
    out = await LF.social_metadata(["urn:li:share:1"])
    assert out["status"] == 500


@pytest.mark.asyncio
async def test_list_comments_ok_and_error(monkeypatch):
    _patch_ctx(monkeypatch)
    _patch_http(
        monkeypatch,
        [
            _Resp(200, {"elements": [{"id": "c1"}]}),
            _Resp(404, {}, text="gone"),
        ],
    )
    out = await LF.list_comments("urn:li:share:1")
    assert out["status"] == 200
    assert out["comments"][0]["id"] == "c1"
    out = await LF.list_comments("urn:li:share:2")
    assert out["status"] == 404

    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=None))
    out = await LF.list_comments("urn:li:share:3")
    assert out["error"] == "no-linkedin-account"


# ── writes: react/comment/delete_reaction enabled paths ─────────────────


@pytest.mark.asyncio
async def test_react_enabled_failure_and_no_ctx(monkeypatch):
    _patch_ctx(monkeypatch)
    monkeypatch.setattr(LF, "WRITES_ENABLED", True)
    _patch_http(monkeypatch, [_Resp(403, {}, text="forbidden")])
    out = await LF.react("organization", "urn:li:share:9", "LIKE")
    assert out["ok"] is False
    assert out["status"] == 403
    assert "forbidden" in out["error"]

    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=None))
    monkeypatch.setattr(LF, "member_ctx", AsyncMock(return_value=None))
    out = await LF.react("member", "urn:li:share:9", "LIKE")
    assert out["error"] == "no-linkedin-account"


@pytest.mark.asyncio
async def test_comment_enabled_success_and_parent(monkeypatch):
    _patch_ctx(monkeypatch)
    monkeypatch.setattr(LF, "WRITES_ENABLED", True)
    calls = _patch_http(
        monkeypatch,
        [
            _Resp(201, {"commentUrn": "urn:li:comment:1"}, {"x-restli-id": "cid-1"}),
            _Resp(500, {}, text="boom"),
        ],
    )
    out = await LF.comment("member", "urn:li:share:1", "nice post", parent_comment="urn:li:comment:0")
    assert out["ok"] is True
    assert out["commentUrn"] == "urn:li:comment:1"
    assert out["id"] == "cid-1"
    assert calls[0][2]["json"]["parentComment"] == "urn:li:comment:0"

    out = await LF.comment("member", "urn:li:share:1", "again")
    assert out["ok"] is False
    assert "boom" in out["error"]


@pytest.mark.asyncio
async def test_delete_reaction_ok_and_missing_ctx(monkeypatch):
    _patch_ctx(monkeypatch)
    calls = _patch_http(monkeypatch, [_Resp(204, {})])
    out = await LF.delete_reaction("urn:li:person:1", "urn:li:share:1")
    assert out["ok"] is True
    assert calls[0][0] == "delete"

    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=None))
    out = await LF.delete_reaction("urn:li:person:1", "urn:li:share:1")
    assert out["error"] == "no-linkedin-account"


@pytest.mark.asyncio
async def test_has_reaction_and_comment_no_ctx(monkeypatch):
    monkeypatch.setattr(LF, "org_ctx", AsyncMock(return_value=None))
    monkeypatch.setattr(LF, "member_ctx", AsyncMock(return_value=None))
    assert await LF.has_reaction("urn:li:person:1", "urn:li:share:1") is None
    out = await LF.comment("member", "urn:li:share:1", "hi")
    assert out["error"] == "no-linkedin-account"


@pytest.mark.asyncio
async def test_comment_enabled_bad_json_body(monkeypatch):
    _patch_ctx(monkeypatch)
    monkeypatch.setattr(LF, "WRITES_ENABLED", True)

    class _BadResp:
        status_code = 201
        headers = {}
        text = "not-json"

        def json(self):
            raise ValueError("bad json")

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _BadResp()

    monkeypatch.setattr(LF.httpx, "AsyncClient", _Client)
    out = await LF.comment("member", "urn:li:share:1", "hi")
    assert out["ok"] is True
