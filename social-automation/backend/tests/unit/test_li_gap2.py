# -*- coding: utf-8 -*-
"""Coverage gaps: linkedin_ads error paths, linkedin_events error paths,
linkedin_ad_library watch-run edge paths."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ── linkedin_ads ─────────────────────────────────────────────────────────


class _R:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._p = payload if payload is not None else {"elements": []}
        self.text = text

    def json(self):
        return self._p

    def raise_for_status(self):
        pass


class _C:
    def __init__(self, responses):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        return self._responses.pop(0) if self._responses else _R()


def _db(acc=None):
    res = MagicMock()
    res.scalars.return_value.first.return_value = acc
    db = MagicMock()
    db.execute = AsyncMock(return_value=res)
    return db


def _patch_ads(monkeypatch, responses):
    import app.services.linkedin_ads as L

    monkeypatch.setattr("app.core.security.decrypt_token", lambda t: "tok", raising=False)
    monkeypatch.setattr(L, "decrypt_token", lambda t: "tok", raising=False)
    monkeypatch.setattr("httpx.AsyncClient", lambda **k: _C(responses))
    return L


@pytest.mark.asyncio
async def test_campaigns_non200(monkeypatch):
    L = _patch_ads(monkeypatch, [_R(500, text="boom")])
    db = _db(MagicMock(access_token_enc="enc"))
    assert await L.campaigns(db, "urn:li:sponsoredAccount:1") == []


@pytest.mark.asyncio
async def test_snapshot_no_acc_after_accounts(monkeypatch):
    L = _patch_ads(
        monkeypatch,
        [
            _R(200, {"elements": [{"id": 1, "name": "A"}]}),   # ad_accounts
            _R(200, {"elements": []}),                          # campaigns
        ],
    )
    # _token needs an account w/ token; snapshot's later SocialAccount lookup → None
    acc_tok = MagicMock(access_token_enc="enc")
    def _res(acc):
        r = MagicMock()
        r.scalars.return_value.first.return_value = acc
        return r

    # _token (ad_accounts), _token (campaigns), snapshot SocialAccount lookup
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_res(acc_tok), _res(acc_tok), _res(None)])
    out = await L.snapshot(db)
    assert out["accounts"] == 1 and out["campaigns"] == []


@pytest.mark.asyncio
async def test_snapshot_team_scoped(monkeypatch):
    L = _patch_ads(
        monkeypatch,
        [
            _R(200, {"elements": []}),   # ad_accounts (team scoped via _token)
        ],
    )
    db = _db(MagicMock(access_token_enc="enc"))
    assert await L.snapshot(db, team_id="t1") == {"accounts": 0}


@pytest.mark.asyncio
async def test_lead_gen_forms_non200(monkeypatch):
    L = _patch_ads(monkeypatch, [_R(403, text="denied")])
    db = _db(MagicMock(access_token_enc="enc"))
    assert await L.lead_gen_forms(db, "urn:li:sponsoredAccount:1") == []


# ── linkedin_events ──────────────────────────────────────────────────────


class _RespE:
    def __init__(self, status=200, payload=None, text="", headers=None):
        self.status_code = status
        self._p = payload or {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._p


class _ClientE:
    def __init__(self, *a, response=None, **k):
        self._r = response or _RespE()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, **kw):
        return self._r

    async def get(self, url, **kw):
        return self._r


@pytest.mark.asyncio
async def test_events_error_and_skip_paths(monkeypatch):
    import app.services.linkedin_events as EV

    # no org token -> skip
    monkeypatch.setattr(EV, "_org_token", AsyncMock(return_value=None))
    assert await EV.create_org_event("n", 1, 2) == {"skip": "no-linkedin-org-account"}
    assert await EV.post_org_event(5) == {"skip": "no-linkedin-org-account"}
    assert await EV.list_org_events() == {"skip": "no-linkedin-org-account"}

    monkeypatch.setattr(EV, "_org_token", AsyncMock(return_value=("tok", "urn:li:organization:9")))

    # create_org_event non-2xx
    monkeypatch.setattr(EV.httpx, "AsyncClient", lambda *a, **k: _ClientE(response=_RespE(400, text="bad")))
    out = await EV.create_org_event("n", 1, 2, event_url="https://e")
    assert out["error"] == 400

    # post_org_event non-2xx
    out = await EV.post_org_event(7, commentary="hi")
    assert out["error"] == 400

    # list_org_events non-200 -> empty events
    out = await EV.list_org_events()
    assert out["status"] == 400 and out["events"] == []

    # list_org_events 200 with elements
    monkeypatch.setattr(EV.httpx, "AsyncClient", lambda *a, **k: _ClientE(response=_RespE(200, {"elements": [{"id": "e1"}]})))
    out = await EV.list_org_events()
    assert out["events"] == [{"id": "e1"}]

    # create_org_event 201 with body fallback id
    class _ClientBody:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, **kw):
            return _RespE(201, {"id": "urn:li:event:99"}, text="{}", headers={})

    monkeypatch.setattr(EV.httpx, "AsyncClient", _ClientBody)
    out = await EV.create_org_event("n", 1, 2)
    assert out["urn"] == "urn:li:event:99"


# ── linkedin_ad_library ──────────────────────────────────────────────────


class _ResultAL:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeDbAL:
    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, _stmt):
        return _ResultAL(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1


def _watch():
    from app.models.linkedin_intel import LinkedInIntelWatch

    return LinkedInIntelWatch(kind="keyword", value="cloud", country="GR")


@pytest.mark.asyncio
async def test_run_watch_db_none_path(monkeypatch):
    import app.services.linkedin_ad_library as AL

    inner = _FakeDbAL([[_watch()], [_watch()], [], []])

    class _CM:
        async def __aenter__(self):
            return inner

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(AL, "task_session", lambda: _CM())
    monkeypatch.setattr(AL, "_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(AL, "search_all", AsyncMock(return_value={"ads": []}))
    out = await AL.run_watch(None)
    assert "ended" in out or "new" in out


@pytest.mark.asyncio
async def test_run_watch_search_exception_and_error_result(monkeypatch):
    import app.services.linkedin_ad_library as AL
    from app.models.linkedin_intel import LinkedInAdIntel, LinkedInIntelWatch

    w1 = _watch()
    w2 = LinkedInIntelWatch(kind="advertiser", value="acme", country="US")
    stale = LinkedInAdIntel(ad_url="https://x/stale", status="active", miss_count=AL.MISS_THRESHOLD - 1)
    # order: seed check → enabled watches → per-watch ad lookups → active scan
    db = _FakeDbAL([[_watch()], [w1, w2], [stale]])

    async def fake_search(**kw):
        if kw.get("advertiser") == "acme":
            raise RuntimeError("search exploded")
        if kw.get("keyword") == "cloud":
            return {"error": 429, "body": "slow down", "ads": []}
        return {"ads": []}

    monkeypatch.setattr(AL, "_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(AL, "search_all", fake_search)

    out = await AL.run_watch(db)
    assert any("search exploded" in e for e in out["errors"])
    assert any("HTTP 429" in e for e in out["errors"])
    assert stale.status == "ended"  # miss_count hits threshold


@pytest.mark.asyncio
async def test_run_watch_miss_below_threshold(monkeypatch):
    import app.services.linkedin_ad_library as AL
    from app.models.linkedin_intel import LinkedInAdIntel

    w = _watch()
    row = LinkedInAdIntel(ad_url="https://x/miss", status="active", miss_count=0)
    seen = LinkedInAdIntel(ad_url="https://x/seen", status="active", miss_count=5)
    # seed → watches → ad lookups (none) → active scan [row, seen]
    db = _FakeDbAL([[_watch()], [w], [], [row, seen]])

    async def fake_search(**kw):
        return {"ads": [{"ad_url": "https://x/seen"}]}

    monkeypatch.setattr(AL, "_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(AL, "search_all", fake_search)

    out = await AL.run_watch(db)
    assert row.miss_count == 1 and row.status == "active"
    assert seen.miss_count == 5 and seen.status == "active"


@pytest.mark.asyncio
async def test_snapshot_team_scoped_with_accounts(monkeypatch):
    """team_id filter line in snapshot (line ~96) + acc meta write."""
    import app.services.linkedin_ads as L

    responses = [
        _R(200, {"elements": [{"id": 1, "name": "A"}]}),   # ad_accounts
        _R(200, {"elements": []}),                          # campaigns
    ]
    monkeypatch.setattr("app.core.security.decrypt_token", lambda t: "tok", raising=False)
    monkeypatch.setattr(L, "decrypt_token", lambda t: "tok", raising=False)
    monkeypatch.setattr("httpx.AsyncClient", lambda **k: _C(responses))

    def _res(acc):
        r = MagicMock()
        r.scalars.return_value.first.return_value = acc
        return r

    acc_tok = MagicMock(access_token_enc="enc")
    acc_meta = MagicMock(access_token_enc="enc", meta_data={})
    db = MagicMock()
    # _token(ad_accounts w/ team), _token(campaigns), snapshot SocialAccount lookup
    db.execute = AsyncMock(side_effect=[_res(acc_tok), _res(acc_tok), _res(acc_meta)])
    out = await L.snapshot(db, team_id="team-9")
    assert out["accounts"] == 1
    assert acc_meta.meta_data["ads"]["accounts"] == 1


@pytest.mark.asyncio
async def test_search_all_skips_unsummarizable_elements(monkeypatch):
    """ad_library search_all: element that _summarize rejects (no adUrl)."""
    import app.services.linkedin_ad_library as AL

    class _Resp:
        status_code = 200

        def json(self):
            return {"elements": [{"noAdUrl": True}, {"adUrl": "https://x/ok", "details": {}}]}

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Resp()

    monkeypatch.setattr(AL.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(AL, "_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(AL.time, "sleep", lambda s: None)
    out = await AL.search_all(keyword="k", pages=1)
    assert len(out["ads"]) == 1


@pytest.mark.asyncio
async def test_ad_library_search_page_error_and_public_search(monkeypatch):
    import app.services.linkedin_ad_library as AL

    # search_page non-200 -> {"error": ...}
    class _RespErr:
        status_code = 500
        text = "server err"

        def json(self):
            return {}

    class _ClientErr:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _RespErr()

    monkeypatch.setattr(AL.httpx, "AsyncClient", _ClientErr)
    monkeypatch.setattr(AL, "_token", AsyncMock(return_value="tok"))

    # search_all propagates page error (line ~145)
    out = await AL.search_all(keyword="k", pages=1)
    assert out["error"] == 500 and out["ads"] == []

    # search_public passthrough of error result (line ~254)
    out2 = await AL.search_ads(keyword="k", limit=5)
    assert out2["error"] == 500


@pytest.mark.asyncio
async def test_org_token_with_access_token(monkeypatch):
    """linkedin_events._org_token returns (token, urn) when acc has token."""
    import app.services.linkedin_events as EV

    acc = MagicMock(access_token_enc=b"encblob", account_id="org-1")
    res = MagicMock()
    res.scalars.return_value.first.return_value = acc
    db = MagicMock()
    db.execute = AsyncMock(return_value=res)

    class _CM:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(EV, "task_session", lambda: _CM())
    monkeypatch.setattr(EV, "decrypt_token", lambda b: "decrypted", raising=False)
    ctx = await EV._org_token()
    assert ctx == ("decrypted", "urn:li:organization:org-1")
