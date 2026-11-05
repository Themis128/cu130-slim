"""Coverage for carousel_pipeline — build_ad_brand_kit + slide dedupe."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

import app.services.carousel_pipeline as CP

pytestmark = pytest.mark.asyncio


def _fake_img():
    return Image.new("RGB", (64, 64), (10, 20, 30))


async def test_build_ad_brand_kit(monkeypatch):
    import app.services.media_spellcheck as sc

    monkeypatch.setattr(sc, "correct_text", AsyncMock(side_effect=lambda t: t + "!"))
    monkeypatch.setattr(CP, "_load_brand_colors", AsyncMock(return_value={}))
    monkeypatch.setattr(CP, "_cf_generate_background", AsyncMock(return_value=b"bg"))
    monkeypatch.setattr(CP, "_qa_generated_background", AsyncMock(return_value=b"bg2"))
    monkeypatch.setattr(CP, "compose_ad_creative", lambda **kw: _fake_img())
    kept = []
    monkeypatch.setattr(CP, "persist_generated_image", AsyncMock(
        side_effect=lambda db, **kw: kept.append(kw) or SimpleNamespace(id=len(kept))))

    db = SimpleNamespace()
    out = await CP.build_ad_brand_kit(
        db, team_id="t1", user_id="u1", headline="H", subline="S", cta="C",
        stat=None, stat_label=None, image_prompt="a bg",
        sizes={"one": (64, 64), "two": (64, 64)})
    assert len(out) == 2 and len(kept) == 2


async def test_build_ad_brand_kit_no_bg(monkeypatch):
    import app.services.media_spellcheck as sc

    monkeypatch.setattr(sc, "correct_text", AsyncMock(side_effect=lambda t: t))
    monkeypatch.setattr(CP, "_load_brand_colors", AsyncMock(return_value={}))
    monkeypatch.setattr(CP, "compose_ad_creative", lambda **kw: _fake_img())
    monkeypatch.setattr(CP, "persist_generated_image", AsyncMock(return_value=SimpleNamespace(id="a")))
    out = await CP.build_ad_brand_kit(
        SimpleNamespace(), team_id="t1", user_id="u1", headline="H",
        subline="S", cta="C", stat=None, stat_label="L",
        image_prompt=None, sizes={"one": (64, 64)})
    assert len(out) == 1
