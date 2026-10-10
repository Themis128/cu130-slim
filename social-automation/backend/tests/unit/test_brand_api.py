"""Unit tests for app/api/brand.py — brand identity router.

Covers team/brand guards, CRUD, voice/visual/guidelines, assets, ad-kit,
AI extract/analyze/compliance/logo/favicon, monitoring (mentions,
competitors, health), autopilot/trends, vCard building, and the digital
business card (auth + public-token paths).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.brand as B


class _Res:
    def __init__(self, first=None, all=None, scalar=None):
        self._first = first
        self._all = all if all is not None else ([first] if first else [])
        self._scalar = scalar

    def scalars(self):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._all

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._first


class _DB:
    def __init__(self, results=None, team=None):
        self._q = list(results or [])
        self._team = team
        self.added = []
        self.deleted = []
        self.commits = 0
        self.refreshed = []

    async def get(self, model, key):
        return self._team

    async def execute(self, stmt, *a):
        return self._q.pop(0) if self._q else _Res()

    def add(self, o):
        self.added.append(o)

    async def delete(self, o):
        self.deleted.append(o)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1

    async def refresh(self, o):
        self.refreshed.append(o)


def _team():
    return SimpleNamespace(id=uuid.uuid4())


def _brand(**kw):
    b = SimpleNamespace(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        name="Cloudless",
        industry="tech",
        positioning_statement="pos",
        mission="m",
        values=["v1"],
        tagline="tag",
        target_audience={"who": "smb"},
        website_url="https://cloudless.gr",
        voice=None,
        visual=None,
        guidelines=None,
        assets=[],
    )
    for k, v in kw.items():
        setattr(b, k, v)
    return b


def _user():
    return SimpleNamespace(id=uuid.uuid4(), email="u@x.io")


def _voice():
    return SimpleNamespace(
        tone_dimensions={"warm": 4},
        messaging_pillars=[{"p": 1}],
        banned_phrases=["bad"],
        preferred_phrases=["good"],
        example_content="ex",
        voice_signature={"sig": 1},
    )


def _visual():
    return SimpleNamespace(
        primary_color="#00fff5",
        accent_color="#ff00ff",
        neutral_colors=["#000"],
        font_heading="H",
        font_body="B",
        type_scale={},
        logo_url="/logo.png",
        logo_variants={},
        image_style="cyber",
        photography_direction="dir",
    )


# ── guards + CRUD ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_team_and_brand_guards():
    db = _DB(team=None)
    with pytest.raises(HTTPException) as ei:
        await B._get_team(uuid.uuid4(), db)
    assert ei.value.status_code == 404

    db = _DB(results=[_Res()], team=_team())  # no brand row
    with pytest.raises(HTTPException) as ei:
        await B._get_brand(uuid.uuid4(), db)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_get_brand_returns_none_on_missing():
    # _get_brand 404 → endpoint returns None for onboarding
    out = await B.get_brand(uuid.uuid4(), _user(), _DB(team=_team()))
    assert out is None


@pytest.mark.asyncio
async def test_create_and_update_brand():
    team = _team()
    # exists → 409
    db = _DB(results=[_Res(first=_brand())], team=team)
    with pytest.raises(HTTPException) as ei:
        await B.create_brand(B.BrandCreate(name="X"), uuid.uuid4(), _user(), db)
    assert ei.value.status_code == 409

    # happy path: brand + voice + visual added, reloaded
    brand = _brand()
    db = _DB(results=[_Res(), _Res(first=brand)], team=team)
    out = await B.create_brand(B.BrandCreate(name="Cloudless", industry="tech"), uuid.uuid4(), _user(), db)
    assert out is brand
    assert len(db.added) == 3  # brand + voice + visual

    # update fields
    brand = _brand()
    db = _DB(results=[_Res(first=brand)], team=team)
    out = await B.update_brand(B.BrandUpdate(name="New Name", tagline="new tag"), uuid.uuid4(), _user(), db)
    assert brand.name == "New Name" and brand.tagline == "new tag"

    # delete
    db = _DB(results=[_Res(first=brand)], team=team)
    await B.delete_brand(uuid.uuid4(), _user(), db)
    assert db.deleted == [brand]


@pytest.mark.asyncio
async def test_voice_and_visual():
    brand = _brand()
    # no voice → creates one
    db = _DB(results=[_Res(first=brand)], team=_team())
    await B.update_brand_voice(B.BrandVoiceUpdate(tone_dimensions={"bold": 5}), uuid.uuid4(), _user(), db)
    assert len(db.added) == 1

    # existing voice → setattr path
    brand.voice = _voice()
    db = _DB(results=[_Res(first=brand)], team=_team())
    await B.update_brand_voice(B.BrandVoiceUpdate(banned_phrases=["x"]), uuid.uuid4(), _user(), db)
    assert brand.voice.banned_phrases == ["x"]

    # get voice missing → 404
    brand2 = _brand()
    with pytest.raises(HTTPException):
        await B.get_brand_voice(uuid.uuid4(), _user(), _DB(results=[_Res(first=brand2)], team=_team()))

    # visual get missing → 404; update creates
    with pytest.raises(HTTPException):
        await B.get_brand_visual(uuid.uuid4(), _user(), _DB(results=[_Res(first=brand2)], team=_team()))
    db = _DB(results=[_Res(first=brand2)], team=_team())
    await B.update_brand_visual(B.BrandVisualUpdate(primary_color="#fff"), uuid.uuid4(), _user(), db)
    assert len(db.added) == 1


@pytest.mark.asyncio
async def test_guidelines():
    brand = _brand()
    # get missing → 404
    with pytest.raises(HTTPException):
        await B.get_brand_guidelines(uuid.uuid4(), _user(), _DB(results=[_Res(first=brand)], team=_team()))

    # compile new → version 1
    brand.voice = _voice()
    brand.visual = _visual()
    compiled = SimpleNamespace(content={}, version=1)
    db = _DB(results=[_Res(first=brand), _Res(first=compiled)], team=_team())
    await B.compile_brand_guidelines(uuid.uuid4(), _user(), db)
    assert len(db.added) == 1  # new BrandGuidelines row

    # compile existing → version bumps, no add
    g = SimpleNamespace(content={}, version=3, updated_at=None)
    brand.guidelines = g
    db = _DB(results=[_Res(first=brand), _Res(first=g)], team=_team())
    await B.compile_brand_guidelines(uuid.uuid4(), _user(), db)
    assert g.version == 4 and not db.added
    assert g.content["voice"]["banned_phrases"] == ["bad"]
    assert g.content["visual"]["primary_color"] == "#00fff5"

    # share-token lookup
    db = _DB(results=[_Res(first=g)])
    out = await B.get_brand_guidelines_by_token("tok", db)
    assert out is g
    with pytest.raises(HTTPException):
        await B.get_brand_guidelines_by_token("bad", _DB(results=[_Res()]))


@pytest.mark.asyncio
async def test_assets():
    brand = _brand(assets=[SimpleNamespace(id=1)])
    db = _DB(results=[_Res(first=brand)], team=_team())
    out = await B.list_brand_assets(uuid.uuid4(), _user(), db)
    assert len(out) == 1

    db = _DB(results=[_Res(first=brand)], team=_team())
    await B.create_brand_asset(B.BrandAssetCreate(name="logo", file_url="/f.png"), uuid.uuid4(), _user(), db)
    assert len(db.added) == 1

    # delete: found + missing
    asset = SimpleNamespace(id=uuid.uuid4())
    db = _DB(results=[_Res(first=brand), _Res(first=asset)], team=_team())
    await B.delete_brand_asset(asset.id, uuid.uuid4(), _user(), db)
    assert db.deleted == [asset]
    db = _DB(results=[_Res(first=brand), _Res()], team=_team())
    with pytest.raises(HTTPException):
        await B.delete_brand_asset(uuid.uuid4(), uuid.uuid4(), _user(), db)


# ── AI surfaces ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ad_kit(monkeypatch):
    import app.services.carousel_pipeline as CP

    kit = [SimpleNamespace(id=uuid.uuid4(), filename="f.png", public_url="/u", width=1200, height=627)]
    monkeypatch.setattr(CP, "build_ad_brand_kit", AsyncMock(return_value=kit))
    brand = _brand()
    db = _DB(results=[_Res(first=brand)], team=_team())
    out = await B.generate_ad_brand_kit(B.AdKitRequest(headline="H"), uuid.uuid4(), _user(), db)
    assert out == kit
    assert len(db.added) == 1  # BrandAsset registered


@pytest.mark.asyncio
async def test_extract_and_analyze(monkeypatch):
    import app.services.brand_extractor as BE
    import app.services.brand_voice as BV
    from app.services.url_safety import UnsafeUrlError

    monkeypatch.setattr(BE, "extract_brand_from_url", AsyncMock(return_value={"brand": "kit"}))
    out = await B.extract_brand_kit(B.ExtractRequest(url="https://x.io"), _user())
    assert out == {"brand": "kit"}

    monkeypatch.setattr(BE, "extract_brand_from_url", AsyncMock(side_effect=UnsafeUrlError("ssrf")))
    with pytest.raises(HTTPException) as ei:
        await B.extract_brand_kit(B.ExtractRequest(url="http://169.254.x"), _user())
    assert ei.value.status_code == 400

    monkeypatch.setattr(BE, "extract_brand_from_url", AsyncMock(side_effect=RuntimeError("net")))
    with pytest.raises(HTTPException) as ei:
        await B.extract_brand_kit(B.ExtractRequest(url="https://x.io"), _user())
    assert ei.value.status_code == 500

    monkeypatch.setattr(BV, "analyze_brand_voice", AsyncMock(return_value={"tone": "warm"}))
    out = await B.analyze_voice(B.AnalyzeVoiceRequest(samples=["a"]), _user())
    assert out == {"tone": "warm"}


@pytest.mark.asyncio
async def test_compliance(monkeypatch):
    import app.services.brand_compliance as BC

    spy = AsyncMock(return_value={"score": 5})
    monkeypatch.setattr(BC, "score_brand_compliance", spy)
    brand = _brand(voice=_voice())
    db = _DB(results=[_Res(first=brand)], team=_team())
    out = await B.score_compliance(B.ComplianceRequest(content="c", platform="linkedin"), uuid.uuid4(), _user(), db)
    assert out == {"score": 5}
    kw = spy.await_args.kwargs
    assert kw["brand"]["name"] == "Cloudless"
    assert kw["voice"]["banned_phrases"] == ["bad"]


@pytest.mark.asyncio
async def test_generate_logo_and_favicon(monkeypatch):
    import app.services.inference as INF
    import app.services.media_storage as MS

    monkeypatch.setattr(INF, "_get_provider_config", AsyncMock(return_value=("u", "@cf/black-forest-labs/flux", "k")))
    monkeypatch.setattr(INF, "_is_workers_ai_image_model", lambda m: True)
    img = AsyncMock(return_value={"image_base64": "aGk="})
    monkeypatch.setattr(INF, "_call_workers_ai_image", img)
    asset = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(MS, "persist_generated_image", AsyncMock(return_value=asset))

    brand = _brand(visual=_visual())
    db = _DB(results=[_Res(first=brand)], team=_team())
    req = B.GenerateLogoRequest(description="minimal mark")
    out = await B.generate_brand_logo(req, uuid.uuid4(), _user(), db)
    assert out.logo_url == f"/api/v1/media/view/{asset.id}"
    assert brand.visual.logo_url == out.logo_url
    assert "Cloudless" in img.await_args.kwargs["prompt"]
    assert "minimal mark" in img.await_args.kwargs["prompt"]

    # brand with no visual → creates BrandVisual
    brand2 = _brand()
    db = _DB(results=[_Res(first=brand2)], team=_team())
    await B.generate_brand_logo(req, uuid.uuid4(), _user(), db)
    assert len(db.added) == 1

    # image call failure → 502
    img.side_effect = RuntimeError("cf down")
    with pytest.raises(HTTPException) as ei:
        await B.generate_brand_logo(req, uuid.uuid4(), _user(), _DB(results=[_Res(first=brand)], team=_team()))
    assert ei.value.status_code == 502
    img.side_effect = None

    # favicon: no logo → 400
    brand3 = _brand(visual=SimpleNamespace(logo_url=None))
    with pytest.raises(HTTPException) as ei:
        await B.generate_brand_favicon(uuid.uuid4(), _user(), _DB(results=[_Res(first=brand3)], team=_team()))
    assert ei.value.status_code == 400

    # favicon happy path → logo_variants updated
    brand.visual.logo_variants = {}
    db = _DB(results=[_Res(first=brand)], team=_team())
    out = await B.generate_brand_favicon(uuid.uuid4(), _user(), db)
    assert brand.visual.logo_variants["favicon"] == out.favicon_url


# ── monitoring / autopilot / trends ──────────────────────────────────


@pytest.mark.asyncio
async def test_monitoring_and_agents(monkeypatch):
    brand = _brand()

    def db(*rs):
        return _DB(results=[_Res(first=brand), *rs], team=_team())

    mentions = [SimpleNamespace(id=1)]
    monkeypatch.setattr(B, "collect_mentions", AsyncMock(return_value=mentions))
    out = await B.collect_brand_mentions(uuid.uuid4(), _user(), db())
    assert out == mentions

    db2 = _DB(results=[_Res(first=brand), _Res(all=["s1", "s2"])], team=_team())
    out = await B.list_competitor_snapshots(uuid.uuid4(), _user(), db2)
    assert out == ["s1", "s2"]

    snap = SimpleNamespace(id=2)
    monkeypatch.setattr(B, "snapshot_competitor", AsyncMock(return_value=snap))
    out = await B.take_competitor_snapshot("acme", uuid.uuid4(), "twitter", _user(), db())
    assert out is snap

    # health: mentions + competitors + post count executes
    monkeypatch.setattr(B, "calculate_health_score", lambda **kw: {"score": 88, **kw})
    db3 = _DB(results=[_Res(first=brand), _Res(all=["m"]), _Res(all=["c"]), _Res(scalar=7)], team=_team())
    out = await B.get_brand_health(uuid.uuid4(), _user(), db3)
    assert out["score"] == 88

    # autopilot + trends delegate
    monkeypatch.setattr(B, "run_autopilot", AsyncMock(return_value={"drafts": 3}))
    out = await B.run_brand_autopilot(uuid.uuid4(), 7, 4, _user(), db())
    assert out == {"drafts": 3}

    monkeypatch.setattr(B, "scout_trends", AsyncMock(return_value={"trends": []}))
    out = await B.get_trends(uuid.uuid4(), _user(), db())
    assert out == {"trends": []}


# ── vCard + digital card ─────────────────────────────────────────────


def test_build_vcard():
    brand = _brand()
    v = B._build_vcard(brand, _visual(), "a@b.c", "+30 123 456", "https://x.io", [{"platform": "linkedin", "url": "https://li/x"}])
    assert "BEGIN:VCARD" in v and "VERSION:4.0" in v
    assert "FN:Cloudless" in v and "TITLE:tech" in v
    assert "EMAIL;TYPE=work;PREF=1:a@b.c" in v
    assert "tel:+30123456" in v  # spaces stripped
    assert "URL;TYPE=linkedin:https://li/x" in v
    assert "LOGO;VALUE=uri:/logo.png" in v
    assert v.endswith("END:VCARD")

    # minimal brand — optional lines absent
    bare = _brand(industry=None, tagline=None)
    v = B._build_vcard(bare, None, None, None, None, [])
    assert "EMAIL" not in v and "LOGO" not in v and "TITLE" not in v


@pytest.mark.asyncio
async def test_digital_card():
    acct = SimpleNamespace(platform="instagram", username="cloudless.gr", display_name="Cloudless", account_type="business")
    g = SimpleNamespace(share_token="tok-1")
    brand = _brand(visual=_visual(), voice=_voice(), guidelines=g)
    db = _DB(results=[_Res(first=brand), _Res(all=[acct])], team=_team())
    out = await B.get_digital_card(uuid.uuid4(), _user(), db)
    assert out.brand_name == "Cloudless"
    assert out.card_url.endswith("/card/tok-1")
    assert out.socials[0]["url"] == "https://instagram.com/cloudless.gr"
    assert out.pillars == [{"p": 1}]
    assert "BEGIN:VCARD" in out.vcard

    # public: bad token → 404
    with pytest.raises(HTTPException):
        await B.get_digital_card_public("bad", _DB(results=[_Res()]))

    # public: token → resolves via guidelines.brand
    g.brand = brand
    db = _DB(results=[_Res(first=g), _Res(all=[acct])])
    out = await B.get_digital_card_public("tok-1", db)
    assert out.brand_name == "Cloudless"
