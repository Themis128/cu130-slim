"""Unit tests for app/api/digital_cards.py — digital business cards.

Covers vCard building, card CRUD, public token view/track counters,
WhatsApp send (direct, 24h-window template fallback, error hints,
self-number guard), Messenger send, and from-brand auto-creation with
the per-platform social-URL builder.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

import app.api.digital_cards as D


class _Res:
    def __init__(self, first=None, all=None):
        self._first = first
        self._all = all

    def scalars(self):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._all if self._all is not None else []


class _DB:
    def __init__(self, results=None, team=None):
        self._q = list(results or [])
        self._team = team if team is not None else SimpleNamespace(id=uuid.uuid4())
        self.added = []
        self.deleted = []
        self.commits = 0

    async def get(self, model, key):
        return self._team

    async def execute(self, stmt, *a):
        return self._q.pop(0) if self._q else _Res()

    def add(self, o):
        if type(o).__name__ == "DigitalCard":
            for k, v in (("id", uuid.uuid4()), ("is_active", True), ("view_count", 0), ("contact_save_count", 0), ("share_count", 0), ("click_count", 0)):
                if getattr(o, k, None) is None:
                    setattr(o, k, v)
        self.added.append(o)

    async def delete(self, o):
        self.deleted.append(o)

    async def commit(self):
        self.commits += 1

    async def refresh(self, o):
        pass


def _user():
    return SimpleNamespace(id=uuid.uuid4(), email="u@x.io")


def _card(**kw):
    c = SimpleNamespace(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        brand_id=None,
        name="Themis",
        title="Founder",
        company="Cloudless",
        tagline="tag",
        description="d",
        email="t@c.io",
        phone="+30 123 456",
        website="https://c.io",
        address="Athens",
        primary_color="#0ff",
        accent_color="#f0f",
        logo_url="/l.png",
        avatar_url="/a.png",
        social_links=[{"platform": "instagram", "url": "https://ig/x"}],
        services=[],
        share_token="tok",
        is_active=True,
        view_count=0,
        share_count=0,
        click_count=0,
        contact_save_count=0,
    )
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _http(monkeypatch, gets=None, posts=None):
    class Fake:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            self.last_get = {"url": url, **kw}
            return (gets or []).pop(0) if gets else _Resp()

        async def post(self, url, **kw):
            self.last_post = {"url": url, **kw}
            return (posts or []).pop(0) if posts else _Resp()

    class _Resp:
        status_code = 200
        text = "ok"

        def __init__(self, body=None):
            self._b = body

        def json(self):
            return self._b or {}

    fake = Fake()
    monkeypatch.setattr(D.httpx, "AsyncClient", lambda **kw: fake)
    return fake


def _resp(body=None, status=200, text="ok"):
    r = SimpleNamespace(status_code=status, text=text)
    r.json = lambda: body or {}
    return r


# ── helpers ──────────────────────────────────────────────────────────


def test_vcard_and_url_helpers(monkeypatch):
    card = _card()
    v = D._build_vcard(card)
    assert "VERSION:4.0" in v and "FN:Themis" in v
    assert "ORG:Cloudless" in v and "TITLE:Founder" in v
    assert "tel:+30123456" in v  # spaces stripped
    assert "URL;TYPE=instagram:https://ig/x" in v
    assert "LOGO;VALUE=uri:/l.png" in v and "NOTE:tag" in v
    assert v.endswith("END:VCARD")

    bare = _card(
        company=None, title=None, email=None, phone=None, website=None, address=None, tagline=None, logo_url=None, social_links=[{"platform": "x"}]
    )  # no url → skipped
    v = D._build_vcard(bare)
    assert "ORG:" not in v and "URL" not in v

    monkeypatch.setenv("FRONTEND_URL", "https://front.io")
    assert D._card_url("t1") == "https://front.io/card/t1"
    assert D._wa_digits("+30 (697) 777-7838") == "306977777838"
    assert D._wa_digits(None) == ""


# ── CRUD ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_card_crud():
    card = _card()
    out = await D.list_cards(uuid.uuid4(), _user(), _DB(results=[_Res(all=[card])]))
    assert len(out) == 1 and out[0].card_url.endswith("/card/tok")
    assert "BEGIN:VCARD" in out[0].vcard

    db = _DB()
    out = await D.create_card(D.DigitalCardCreate(name="Me", social_links=[D.SocialLinkIn(platform="ig", url="https://ig")]), uuid.uuid4(), _user(), db)
    assert out.name == "Me" and db.added[0].share_token
    assert out.vcard.startswith("BEGIN:VCARD")

    db = _DB(results=[_Res(first=card)])
    out = await D.get_card(card.id, uuid.uuid4(), _user(), db)
    assert out.name == "Themis"
    with pytest.raises(HTTPException):
        await D.get_card(uuid.uuid4(), uuid.uuid4(), _user(), _DB())

    db = _DB(results=[_Res(first=card)])
    out = await D.update_card(card.id, D.DigitalCardUpdate(name="New", is_active=False), uuid.uuid4(), _user(), db)
    assert card.name == "New" and card.is_active is False

    db = _DB(results=[_Res(first=card)])
    await D.delete_card(card.id, uuid.uuid4(), _user(), db)
    assert db.deleted == [card]

    # vcard download
    out = await D.download_vcard(card.id, uuid.uuid4(), _user(), _DB(results=[_Res(first=card)]))
    assert out.body.decode().startswith("BEGIN:VCARD")


# ── public + tracking ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_public_card_and_track():
    card = _card()
    db = _DB(results=[_Res(first=card)])
    out = await D.get_public_card("tok", db)
    assert out.name == "Themis" and card.view_count == 1

    with pytest.raises(HTTPException):
        await D.get_public_card("bad", _DB(results=[_Res()]))

    # track actions — save/share counters
    db = _DB(results=[_Res(first=card)])
    out = await D.track_card_action("tok", "save", db)
    assert card.contact_save_count == 1 and out["ok"] is True
    out = await D.track_card_action("tok", "share", _DB(results=[_Res(first=card)]))
    assert card.share_count == 1


# ── send via WhatsApp / Messenger ────────────────────────────────────


@pytest.mark.asyncio
async def test_send_card_guards():
    req = D.SendCardRequest(platform="whatsapp")
    # missing card → 404
    with pytest.raises(HTTPException):
        await D.send_card(uuid.uuid4(), req, uuid.uuid4(), _user(), _DB())
    # whatsapp without to_phone → 400
    card = _card()
    with pytest.raises(HTTPException) as ei:
        await D.send_card(card.id, req, uuid.uuid4(), _user(), _DB(results=[_Res(first=card)]))
    assert ei.value.status_code == 400
    # no whatsapp account → 400
    req2 = D.SendCardRequest(platform="whatsapp", to_phone="+30123")
    with pytest.raises(HTTPException) as ei:
        await D.send_card(card.id, req2, uuid.uuid4(), _user(), _DB(results=[_Res(first=card), _Res()]))
    assert ei.value.status_code == 400
    # unsupported platform → 400
    req3 = D.SendCardRequest(platform="carrier-pigeon", to_phone="x")
    with pytest.raises(HTTPException) as ei:
        await D.send_card(card.id, req3, uuid.uuid4(), _user(), _DB(results=[_Res(first=card)]))
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_send_card_whatsapp(monkeypatch):
    import app.core.security as SEC
    import app.services.whatsapp_api as WA
    import app.services.whatsapp_cloud_client as WCC

    monkeypatch.setattr(SEC, "decrypt_token", lambda t: "EAdecrypted")
    wa_acct = SimpleNamespace(
        account_id="waba-1", meta_data={"access_token": "enc-tok", "phone_number_id": "pn-1", "display_phone_number": "+30 111 222", "waba_id": "waba-1"}
    )
    card = _card()
    db = _DB(results=[_Res(first=card), _Res(first=wa_acct)])

    client = SimpleNamespace(
        send_text=AsyncMock(return_value={"messages": [{"id": "m1"}]}),
        _client=SimpleNamespace(access_token="EAtok"),
        send_template=AsyncMock(return_value={"messages": [{"id": "t1"}]}),
    )
    monkeypatch.setattr(WA, "WhatsAppAPIClient", lambda *a: client)

    # happy path
    req = D.SendCardRequest(platform="whatsapp", to_phone="+30999")
    out = await D.send_card(card.id, req, uuid.uuid4(), _user(), db)
    assert out.success and out.message_id == "m1"
    assert card.share_count == 1

    # self-number guard
    req = D.SendCardRequest(platform="whatsapp", to_phone="+30 (111) 222")
    out = await D.send_card(card.id, req, uuid.uuid4(), _user(), _DB(results=[_Res(first=card), _Res(first=wa_acct)]))
    assert not out.success and "itself" in out.error

    # 131047 → template fallback succeeds
    err = WCC.WhatsAppApiError(status_code=400, url="u", response_text="", error={"code": 131047, "message": "outside window"}, rate_limit=SimpleNamespace())
    client.send_text = AsyncMock(side_effect=err)
    _http(monkeypatch, gets=[_resp({"data": [{"name": "card_share", "status": "APPROVED", "language": "en"}]})])
    out = await D.send_card(
        card.id, D.SendCardRequest(platform="whatsapp", to_phone="+9"), uuid.uuid4(), _user(), _DB(results=[_Res(first=card), _Res(first=wa_acct)])
    )
    assert out.success and out.message_id == "t1"

    # 131047 + no approved template → explanatory error
    _http(monkeypatch, gets=[_resp({"data": []})])
    out = await D.send_card(
        card.id, D.SendCardRequest(platform="whatsapp", to_phone="+9"), uuid.uuid4(), _user(), _DB(results=[_Res(first=card), _Res(first=wa_acct)])
    )
    assert not out.success and "card_share" in out.error

    # other coded error → hint text
    err2 = WCC.WhatsAppApiError(status_code=400, url="u", response_text="", error={"code": 133010, "message": "not on wa"}, rate_limit=SimpleNamespace())
    client.send_text = AsyncMock(side_effect=err2)
    out = await D.send_card(
        card.id, D.SendCardRequest(platform="whatsapp", to_phone="+9"), uuid.uuid4(), _user(), _DB(results=[_Res(first=card), _Res(first=wa_acct)])
    )
    assert "not registered" in out.error


@pytest.mark.asyncio
async def test_send_card_messenger(monkeypatch):
    import app.core.security as SEC

    decrypt = Mock(return_value="EAtok")
    monkeypatch.setattr(SEC, "decrypt_token", decrypt)
    fb_page = SimpleNamespace(account_id="pg-1", meta_data={"access_token": "enc-tok"})
    card = _card()

    # no page → 400
    req = D.SendCardRequest(platform="messenger", to_phone="psid1")
    with pytest.raises(HTTPException) as ei:
        await D.send_card(card.id, req, uuid.uuid4(), _user(), _DB(results=[_Res(first=card), _Res()]))
    assert ei.value.status_code == 400

    # happy path
    _http(monkeypatch, posts=[_resp({"message_id": "mid-1"})])
    out = await D.send_card(card.id, req, uuid.uuid4(), _user(), _DB(results=[_Res(first=card), _Res(first=fb_page)]))
    assert out.success and out.message_id == "mid-1"
    assert card.share_count == 1
    decrypt.assert_called_once()

    # api error → surfaced
    _http(monkeypatch, posts=[_resp({}, status=400, text="bad")])
    out = await D.send_card(card.id, req, uuid.uuid4(), _user(), _DB(results=[_Res(first=card), _Res(first=fb_page)]))
    assert not out.success and out.error == "bad"


# ── from-brand ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_card_from_brand():
    # no brand → 400
    with pytest.raises(HTTPException) as ei:
        await D.create_card_from_brand(uuid.uuid4(), _user(), _DB())
    assert ei.value.status_code == 400

    voice = SimpleNamespace(messaging_pillars=[{"title": "T", "description": "D"}])
    visual = SimpleNamespace(primary_color="#0ff", accent_color="#f0f", logo_url="/logo.png")
    brand = SimpleNamespace(
        id=uuid.uuid4(), name="Cloudless", tagline="tag", mission="m", industry="tech", website_url="https://cloudless.gr", visual=visual, voice=voice
    )
    accounts = [
        SimpleNamespace(platform="instagram", account_type=None, username="cloudless.gr", display_name=None),
        SimpleNamespace(platform="facebook", account_type="page", username="cloudless", display_name="Page"),
        SimpleNamespace(platform="facebook", account_type="profile", username="Themis B", display_name=None),
        SimpleNamespace(platform="linkedin", account_type="organization", username="cloudless-gr", display_name=None),
        SimpleNamespace(platform="linkedin", account_type="personal", username="themis-baltzakis", display_name=None),
        SimpleNamespace(platform="whatsapp", account_type=None, username="+30123", display_name=None),
        SimpleNamespace(platform="twitter", account_type=None, username="TBaltzakis", display_name=None),
    ]
    db = _DB(results=[_Res(first=brand), _Res(all=accounts)])
    await D.create_card_from_brand(uuid.uuid4(), _user(), db)
    card = db.added[0]
    links = {s["platform"]: s["url"] for s in card.social_links}
    assert links["instagram"] == "https://instagram.com/cloudless.gr"
    # page gets a URL; personal fb profile produces None
    fb_urls = [s["url"] for s in card.social_links if s["platform"] == "facebook"]
    assert "https://facebook.com/cloudless" in fb_urls
    assert None in fb_urls
    assert links["whatsapp"] is None
    li_urls = [s["url"] for s in card.social_links if s["platform"] == "linkedin"]
    assert "https://linkedin.com/company/cloudless-gr" in li_urls
    assert "https://linkedin.com/in/themis-baltzakis" in li_urls
    assert links["twitter"] == "https://twitter.com/TBaltzakis"
    # pillars → services
    assert card.services == [{"title": "T", "description": "D"}]
    assert card.name == "Cloudless"


# ── remaining tails ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_card_serialization_and_404():
    card = _card()
    data = D.DigitalCardUpdate(
        social_links=[D.SocialLinkIn(platform="x", handle="u", url="https://x.io/u")],
        services=[D.ServiceIn(title="Audit", description="30min")],
    )
    db = _DB([_Res(first=card)])
    out = await D.update_card(card.id, data, uuid.uuid4(), _user(), db)
    assert card.social_links[0]["url"] == "https://x.io/u"
    assert card.services[0]["title"] == "Audit"
    assert out.vcard

    with pytest.raises(HTTPException) as ei:
        await D.update_card(uuid.uuid4(), D.DigitalCardUpdate(name="n"), uuid.uuid4(), _user(), _DB([_Res()]))
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_and_vcard_404s():
    with pytest.raises(HTTPException) as ei:
        await D.delete_card(uuid.uuid4(), uuid.uuid4(), _user(), _DB([_Res()]))
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException) as ei:
        await D.download_vcard(uuid.uuid4(), uuid.uuid4(), _user(), _DB([_Res()]))
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_track_save_and_404():
    card = _card(contact_save_count=1, share_count=2)
    out = await D.track_card_action("tok", "save", _DB([_Res(first=card)]))
    assert out == {"ok": True, "action": "save"}
    assert card.contact_save_count == 2 and card.share_count == 2

    out = await D.track_card_action("tok", "share", _DB([_Res(first=card)]))
    assert card.share_count == 3

    with pytest.raises(HTTPException) as ei:
        await D.track_card_action("nope", "save", _DB([_Res()]))
    assert ei.value.status_code == 404


# ── remaining tails ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_team_missing():
    db = _DB()
    db._team = None
    with pytest.raises(HTTPException) as ei:
        await D._get_team(uuid.uuid4(), db)
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_send_card_template_none_paths(monkeypatch):
    client = SimpleNamespace(
        _client=SimpleNamespace(access_token="EAtok"),
        send_template=AsyncMock(return_value={"messages": [{"id": "t1"}]}),
    )
    card = _card()

    # no waba_id → None
    wa = SimpleNamespace(meta_data={})
    assert await D._send_card_template(client, wa, "+9", card, "u") is None

    # template fetch non-200 → None
    wa.meta_data = {"waba_id": "w1"}
    _http(monkeypatch, gets=[_resp({}, status=500)])
    assert await D._send_card_template(client, wa, "+9", card, "u") is None

    # send_template raises → None
    _http(monkeypatch, gets=[_resp({"data": [{"name": D._CARD_TEMPLATE_NAME, "status": "APPROVED", "language": "en"}]})])
    client.send_template = AsyncMock(side_effect=RuntimeError("api down"))
    assert await D._send_card_template(client, wa, "+9", card, "u") is None


@pytest.mark.asyncio
async def test_send_card_whatsapp_more_edges(monkeypatch):
    import app.core.security as SEC
    import app.services.whatsapp_api as WA
    import app.services.whatsapp_cloud_client as WCC

    monkeypatch.setattr(SEC, "decrypt_token", lambda t: "EAdec")
    card = _card()
    req = D.SendCardRequest(platform="whatsapp", to_phone="+9")

    # account meta missing token/phone_number_id → 400
    bad_acct = SimpleNamespace(meta_data={"access_token": "", "phone_number_id": "pn"})
    with pytest.raises(HTTPException) as ei:
        await D.send_card(card.id, req, uuid.uuid4(), _user(), _DB(results=[_Res(first=card), _Res(first=bad_acct)]))
    assert ei.value.status_code == 400 and "access_token" in ei.value.detail

    # decrypt_token raising → raw token used, send still works
    monkeypatch.setattr(SEC, "decrypt_token", Mock(side_effect=ValueError("not encrypted")))
    wa_acct = SimpleNamespace(meta_data={"access_token": "rawtok", "phone_number_id": "pn-1", "display_phone_number": "0"})
    client = SimpleNamespace(send_text=AsyncMock(return_value={"messages": [{"id": "m9"}]}), _client=SimpleNamespace(access_token="rawtok"))
    monkeypatch.setattr(WA, "WhatsAppAPIClient", lambda *a: client)
    out = await D.send_card(card.id, req, uuid.uuid4(), _user(), _DB(results=[_Res(first=card), _Res(first=wa_acct)]))
    assert out.success and out.message_id == "m9"

    # plain WhatsAppError (no code) → error surfaced
    client.send_text = AsyncMock(side_effect=WCC.WhatsAppError("plain wa err"))
    out = await D.send_card(card.id, req, uuid.uuid4(), _user(), _DB(results=[_Res(first=card), _Res(first=wa_acct)]))
    assert not out.success and "plain wa err" in out.error

    # generic Exception → error surfaced
    client.send_text = AsyncMock(side_effect=RuntimeError("kaboom"))
    out = await D.send_card(card.id, req, uuid.uuid4(), _user(), _DB(results=[_Res(first=card), _Res(first=wa_acct)]))
    assert not out.success and "kaboom" in out.error


@pytest.mark.asyncio
async def test_send_card_messenger_edges(monkeypatch):
    import app.core.security as SEC

    card = _card()

    # missing to_phone → 400
    with pytest.raises(HTTPException) as ei:
        await D.send_card(card.id, D.SendCardRequest(platform="messenger"), uuid.uuid4(), _user(), _DB(results=[_Res(first=card)]))
    assert ei.value.status_code == 400 and "PSID" in ei.value.detail

    # page account with no token → 400
    no_tok = SimpleNamespace(account_id="pg", meta_data={})
    req = D.SendCardRequest(platform="messenger", to_phone="psid")
    with pytest.raises(HTTPException) as ei:
        await D.send_card(card.id, req, uuid.uuid4(), _user(), _DB(results=[_Res(first=card), _Res(first=no_tok)]))
    assert ei.value.status_code == 400 and "access_token" in ei.value.detail

    # decrypt fails → raw token used; http raises → error result
    monkeypatch.setattr(SEC, "decrypt_token", Mock(side_effect=ValueError("nope")))
    fb_page = SimpleNamespace(account_id="pg", meta_data={"access_token": "notEA"})

    class RaisingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            raise ConnectionError("net down")

    monkeypatch.setattr(D.httpx, "AsyncClient", lambda *a, **kw: RaisingClient())
    out = await D.send_card(card.id, req, uuid.uuid4(), _user(), _DB(results=[_Res(first=card), _Res(first=fb_page)]))
    assert not out.success and "net down" in out.error


@pytest.mark.asyncio
async def test_build_social_url_tails():
    brand = SimpleNamespace(
        id=uuid.uuid4(), name="B", tagline=None, mission=None, industry=None, website_url=None,
        visual=SimpleNamespace(primary_color=None, accent_color=None, logo_url=None),
        voice=SimpleNamespace(messaging_pillars=None),
    )
    accounts = [
        SimpleNamespace(platform="threads", account_type=None, username="cloudless.gr", display_name=None),
        SimpleNamespace(platform="tiktok", account_type=None, username="cloudless.gr", display_name=None),
        SimpleNamespace(platform="instagram", account_type=None, username=None, display_name=None),          # no handle → None
        SimpleNamespace(platform="instagram", account_type=None, username="me@x.com", display_name=None),   # email → None
        SimpleNamespace(platform="linkedin", account_type="personal", username="first.last", display_name=None),  # dot → None
        SimpleNamespace(platform="skool", account_type=None, username="comm", display_name=None),            # unknown → None
    ]
    db = _DB(results=[_Res(first=brand), _Res(all=accounts)])
    await D.create_card_from_brand(uuid.uuid4(), _user(), db)
    links = {(s["platform"], s["handle"]): s["url"] for s in db.added[0].social_links}
    assert links[("threads", "cloudless.gr")] == "https://threads.net/@cloudless.gr"
    assert links[("tiktok", "cloudless.gr")] == "https://tiktok.com/@cloudless.gr"
    assert links[("instagram", "me@x.com")] is None
    assert links[("linkedin", "first.last")] is None
    assert links[("skool", "comm")] is None
