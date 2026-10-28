"""Coverage round 5: kakkava chunk/iter helpers + espocrm list_all pagination + auto_gates off-path."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def test_chunk_text_paragraph_breaks():
    from app.services import kakkava as K

    text = "Para one sentence here. " * 30 + "\n\n" + "Para two more text. " * 30
    chunks = K.chunk_text(text, size=200, overlap=50)
    assert len(chunks) > 2
    assert all(c.strip() for c in chunks)


def test_chunk_text_empty_and_short():
    from app.services import kakkava as K

    assert K.chunk_text("") == []
    assert K.chunk_text("   \n\n  ") == []
    assert K.chunk_text("short") == ["short"]


def test_iter_corpus_files(tmp_path):
    from app.services import kakkava as K

    (tmp_path / "a.md").write_text("x")
    (tmp_path / "b.pdf").write_text("x")
    (tmp_path / "c.exe").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "d.txt").write_text("x")
    files = K.iter_corpus_files(tmp_path)
    names = {f.name for f in files}
    assert names == {"a.md", "b.pdf", "d.txt"}


def test_extract_text_dispatch(tmp_path):
    from app.services import kakkava as K

    md = tmp_path / "a.md"
    md.write_text("hello md", encoding="utf-8")
    assert K.extract_text(md) == "hello md"
    html = tmp_path / "b.html"
    html.write_text("<p>x</p>", encoding="utf-8")
    assert "x" in K.extract_text(html)
    exe = tmp_path / "c.exe"
    exe.write_text("x")
    assert K.extract_text(exe) == ""


class _R:
    def __init__(self, payload):
        self.status_code = 200
        self._p = payload

    def json(self):
        return self._p

    @property
    def text(self):
        return ""


class _C:
    def __init__(self, resp):
        self._r = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        return self._r


@pytest.mark.asyncio
async def test_list_all_paginates_and_stops(monkeypatch):
    import app.services.espocrm_client as E

    monkeypatch.setattr("app.services.espocrm_client.get_settings", lambda: MagicMock(ESPOCRM_BASE_URL="https://e", ESPOCRM_API_KEY="k"))
    import json as _j

    calls = []

    async def fake_get(path, params=None):
        search = _j.loads(params["searchParams"])
        calls.append(search["offset"])
        page = [{"id": i} for i in range(search["offset"], min(search["offset"] + E._PAGE, 250))]
        return {"list": page}

    monkeypatch.setattr(E, "espo_get", fake_get)
    items = await E.list_all("Lead", max_items=250)
    assert len(items) == 250
    expected = list(range(0, 250, E._PAGE))
    assert calls == expected


@pytest.mark.asyncio
async def test_list_all_empty(monkeypatch):
    import app.services.espocrm_client as E

    monkeypatch.setattr("app.services.espocrm_client.get_settings", lambda: MagicMock(ESPOCRM_BASE_URL="https://e", ESPOCRM_API_KEY="k"))
    monkeypatch.setattr(E, "espo_get", AsyncMock(return_value={"list": []}))
    assert await E.list_all("Contact") == []


@pytest.mark.asyncio
async def test_post_autoapprove_env_off(monkeypatch):
    from app.worker.tasks import auto_gates

    monkeypatch.delenv("AUTO_APPROVE_POSTS", raising=False)
    out = await auto_gates._post_autoapprove()
    assert out == {"approved": 0, "off": True}
