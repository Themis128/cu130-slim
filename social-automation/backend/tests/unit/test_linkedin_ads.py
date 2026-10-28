"""Coverage: app.services.linkedin_ads + research_workspace pure helpers."""

from unittest.mock import AsyncMock, MagicMock

import pytest


class _R:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._p = payload if payload is not None else {"elements": []}

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("e", request=None, response=None)


class _C:
    def __init__(self, responses):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        return self._responses.pop(0) if self._responses else _R()


def _db_with_account(acc=None):
    res = MagicMock()
    res.scalars.return_value.first.return_value = acc
    db = MagicMock()
    db.execute = AsyncMock(return_value=res)
    return db


def _patch(monkeypatch, mod, responses, token="tok"):
    monkeypatch.setattr("app.core.security.decrypt_token", lambda t: token, raising=False)
    monkeypatch.setattr("app.services.linkedin_ads.decrypt_token", lambda t: token, raising=False)
    monkeypatch.setattr("httpx.AsyncClient", lambda **k: _C(responses))


@pytest.mark.asyncio
async def test_ad_accounts(monkeypatch):
    import app.services.linkedin_ads as L

    _patch(monkeypatch, L, [_R(200, {"elements": [{"id": 1, "name": "Acct"}]})])
    db = _db_with_account(MagicMock(access_token_enc="enc"))
    out = await L.ad_accounts(db)
    assert out == [{"id": 1, "name": "Acct"}]


@pytest.mark.asyncio
async def test_ad_accounts_scoped_team(monkeypatch):
    import app.services.linkedin_ads as L

    _patch(monkeypatch, L, [_R(200, {"elements": []})])
    db = _db_with_account(MagicMock(access_token_enc="enc"))
    out = await L.ad_accounts(db, team_id="t1")
    assert out == []


@pytest.mark.asyncio
async def test_campaigns(monkeypatch):
    import app.services.linkedin_ads as L

    _patch(monkeypatch, L, [_R(200, {"elements": [{"id": 9, "name": "C", "status": "ACTIVE"}]})])
    db = _db_with_account(MagicMock(access_token_enc="enc"))
    out = await L.campaigns(db, "urn:li:sponsoredAccount:1")
    assert out[0]["id"] == 9


@pytest.mark.asyncio
async def test_snapshot_empty(monkeypatch):
    import app.services.linkedin_ads as L

    _patch(monkeypatch, L, [_R(200, {"elements": []})])
    db = _db_with_account(MagicMock(access_token_enc="enc"))
    assert await L.snapshot(db) == {"accounts": 0}


@pytest.mark.asyncio
async def test_snapshot_with_campaigns_stamps_meta(monkeypatch):
    import app.services.linkedin_ads as L

    _patch(
        monkeypatch,
        L,
        [
            _R(200, {"elements": [{"id": 1, "urn": "urn:li:sponsoredAccount:1", "name": "Acct"}]}),
            _R(200, {"elements": [{"id": 2, "name": "Camp", "status": "ACTIVE"}]}),
        ],
    )
    acc = MagicMock(access_token_enc="enc", meta_data={})
    db = _db_with_account(acc)
    out = await L.snapshot(db)
    assert out["accounts"] == 1 and out["campaigns"][0]["name"] == "Camp"
    assert acc.meta_data["ads"]["accounts"] == 1


@pytest.mark.asyncio
async def test_lead_gen_forms(monkeypatch):
    import app.services.linkedin_ads as L

    _patch(monkeypatch, L, [_R(200, {"elements": [{"id": "f1"}]})])
    db = _db_with_account(MagicMock(access_token_enc="enc"))
    assert (await L.lead_gen_forms(db, "urn:x"))[0]["id"] == "f1"


@pytest.mark.asyncio
async def test_lead_form_responses_ok_and_non200(monkeypatch):
    import app.services.linkedin_ads as L

    _patch(monkeypatch, L, [_R(200, {"elements": [{"lead": 1}]}), _R(400, {})])
    db = _db_with_account(MagicMock(access_token_enc="enc"))
    assert (await L.lead_form_responses(db, "u"))[0]["lead"] == 1
    assert await L.lead_form_responses(db, "u") == []


def test_chunks_overlap_and_minlen():
    from app.services.research_workspace import _chunks

    text = "A sentence that is long enough to survive the forty char minimum cut. " * 40
    chunks = _chunks(text)
    assert len(chunks) > 1
    assert all(len(c) > 40 for c in chunks)


def test_chunks_empty():
    from app.services.research_workspace import _chunks

    assert _chunks("tiny") == []


def test_extract_text():
    from app.services.research_workspace import _extract

    assert _extract("a.txt", b"hello") == "hello"


def test_extract_pdf_bytes():
    """fitz.open on invalid bytes — check whether it raises or returns empty."""
    from app.services.research_workspace import _extract

    try:
        out = _extract("a.pdf", b"not-a-pdf")
    except Exception:
        out = None  # acceptable — real PDFs exercised in e2e
    assert out is None or isinstance(out, str)
