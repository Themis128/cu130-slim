"""Coverage close-out round 3: kakkava pure helpers + espocrm_client thin wrappers."""

from unittest.mock import MagicMock

import pytest


def test_doctrine_collection():
    from app.services import kakkava as K

    assert K._doctrine_collection("abc-def") == "team_abc_def_doctrine"


def test_doc_id_stable():
    from app.services import kakkava as K

    a = K._doc_id("x/p.md", 0)
    assert a.startswith("kk:") and a.endswith(":0")
    assert a == K._doc_id("x/p.md", 0)
    assert a != K._doc_id("x/p.md", 1)


def test_html_stripper_skips_script(tmp_path):
    from app.services import kakkava as K

    p = tmp_path / "a.html"
    p.write_text("<p>keep me</p><script>drop()</script><b>bold</b>", encoding="utf-8")
    out = K._extract_html(p)
    assert "keep me" in out and "drop()" not in out and "bold" in out


def test_kakkava_enabled_disabled(monkeypatch):
    from app.services import kakkava as K

    s = MagicMock(KAKKAVA_SYNC_ENABLED=False, CHROMA_URL="http://x", KAKKAVA_DIR="/")
    monkeypatch.setattr(K, "settings", s)
    assert K.kakkava_enabled() is False


def test_kakkava_enabled_no_dir(monkeypatch):
    from app.services import kakkava as K

    s = MagicMock(KAKKAVA_SYNC_ENABLED=True, CHROMA_URL="http://x", KAKKAVA_DIR="/nonexistent-path-xyz")
    monkeypatch.setattr(K, "settings", s)
    assert K.kakkava_enabled() is False


class _R:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._p = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._p


class _C:
    def __init__(self, resp):
        self._r = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        return self._r

    async def post(self, *a, **k):
        return self._r

    async def patch(self, *a, **k):
        return self._r


def _espo_cfg(monkeypatch, base="https://espo.example", key="k"):
    s = MagicMock(ESPOCRM_BASE_URL=base, ESPOCRM_API_KEY=key)
    monkeypatch.setattr("app.services.espocrm_client.get_settings", lambda: s)


@pytest.mark.asyncio
async def test_espo_get_happy(monkeypatch):
    import app.services.espocrm_client as E

    _espo_cfg(monkeypatch)
    monkeypatch.setattr("httpx.AsyncClient", lambda **k: _C(_R(200, {"list": []})))
    assert await E.espo_get("/Lead") == {"list": []}


@pytest.mark.asyncio
async def test_espo_get_unconfigured(monkeypatch):
    import app.services.espocrm_client as E

    _espo_cfg(monkeypatch, base="", key="")
    with pytest.raises(RuntimeError, match="not configured"):
        await E.espo_get("/Lead")


@pytest.mark.asyncio
async def test_espo_get_4xx_raises(monkeypatch):
    import app.services.espocrm_client as E

    _espo_cfg(monkeypatch)
    monkeypatch.setattr("httpx.AsyncClient", lambda **k: _C(_R(404, text="nf")))
    with pytest.raises(RuntimeError, match="404"):
        await E.espo_get("/Lead")


@pytest.mark.asyncio
async def test_espo_get_list_response_wrapped(monkeypatch):
    import app.services.espocrm_client as E

    _espo_cfg(monkeypatch)
    monkeypatch.setattr("httpx.AsyncClient", lambda **k: _C(_R(200, [1, 2])))
    assert await E.espo_get("/Lead") == {"data": [1, 2]}


def test_configured_flag(monkeypatch):
    import app.services.espocrm_client as E

    _espo_cfg(monkeypatch)
    assert E.configured() is True
    s = MagicMock(ESPOCRM_BASE_URL="", ESPOCRM_API_KEY="k")
    monkeypatch.setattr("app.services.espocrm_client.get_settings", lambda: s)
    assert E.configured() is False


@pytest.mark.asyncio
async def test_espo_post_and_patch(monkeypatch):
    import app.services.espocrm_client as E

    _espo_cfg(monkeypatch)
    monkeypatch.setattr("httpx.AsyncClient", lambda **k: _C(_R(200, {"id": "x1"})))
    assert (await E.espo_post("/Lead", {"name": "n"}))["id"] == "x1"
    assert (await E.espo_patch("/Lead/x1", {"name": "n2"}))["id"] == "x1"
