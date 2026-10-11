import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import meta_growth as MG
from app.api.meta_growth import (
    META_PLATFORMS,
    OrganicCampaignRequest,
)


def test_organic_campaign_requires_media():
    with pytest.raises(ValidationError, match="media asset"):
        OrganicCampaignRequest(content_text="A useful Cloudless tip")


def test_organic_campaign_rejects_paid_metadata():
    with pytest.raises(ValidationError, match="Paid promotion"):
        OrganicCampaignRequest(
            content_text="A useful Cloudless tip",
            media_ids=[uuid.uuid4()],
            metadata={"boost": True},
        )


def test_organic_campaign_accepts_media_and_normalizes_text():
    request = OrganicCampaignRequest(
        content_text="  A useful Cloudless tip  ",
        media_ids=[uuid.uuid4()],
    )

    assert request.content_text == "A useful Cloudless tip"
    assert META_PLATFORMS == {"facebook", "instagram", "threads"}


# ---------- route functions ----------


class _Res:
    def __init__(self, all_=None):
        self._all = all_ or []

    def scalars(self):
        return self

    def all(self):
        return self._all


class _DB:
    """Feeds results in call order; records added objects."""

    def __init__(self, results):
        self.results = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, stmt):
        return self.results.pop(0)

    def add(self, o):
        self.added.append(o)

    async def flush(self):
        for o in self.added:
            if getattr(o, "id", None) is None:
                try:
                    o.id = uuid.uuid4()
                except Exception:
                    pass

    async def commit(self):
        self.commits += 1

    async def refresh(self, o):
        pass


def _account(platform="instagram", status="active", **kw):
    base = dict(id=uuid.uuid4(), platform=platform, status=status,
                username="cloudless.gr", display_name="Cloudless",
                account_type="business", is_business=True)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_readiness_lists_accounts():
    accs = [_account("instagram"), _account("facebook", username=None)]
    db = _DB([_Res(accs)])
    out = await MG.get_meta_growth_readiness(
        team_id=uuid.uuid4(), current_user=SimpleNamespace(), db=db)
    assert len(out.accounts) == 2
    assert out.accounts[0]["platform"] == "instagram"
    assert "schedule_valid_media_posts" in out.free_actions
    assert any(a["action"] == "facebook_page_invitations"
               for a in out.unavailable_actions)


@pytest.mark.asyncio
async def test_organic_campaign_no_accounts_409(monkeypatch):
    monkeypatch.setattr(MG, "check_quota", AsyncMock())
    db = _DB([_Res([])])  # account query → empty
    req = OrganicCampaignRequest(content_text="x", media_ids=[uuid.uuid4()])
    with pytest.raises(HTTPException) as e:
        await MG.create_organic_campaign(
            request=req, team_id=uuid.uuid4(),
            current_user=SimpleNamespace(id=uuid.uuid4()), db=db)
    assert e.value.status_code == 409


@pytest.mark.asyncio
async def test_organic_campaign_partial_targets_404(monkeypatch):
    monkeypatch.setattr(MG, "check_quota", AsyncMock())
    acc = _account()
    missing = uuid.uuid4()
    db = _DB([_Res([acc])])
    req = OrganicCampaignRequest(content_text="x", media_ids=[uuid.uuid4()],
                                 target_account_ids=[acc.id, missing])
    with pytest.raises(HTTPException) as e:
        await MG.create_organic_campaign(
            request=req, team_id=uuid.uuid4(),
            current_user=SimpleNamespace(id=uuid.uuid4()), db=db)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_organic_campaign_unowned_media_404(monkeypatch):
    monkeypatch.setattr(MG, "check_quota", AsyncMock())
    acc = _account()
    media_id = uuid.uuid4()
    db = _DB([
        _Res([acc]),               # accounts
        _Res([(uuid.uuid4(),)]),   # owned media ≠ requested
    ])
    req = OrganicCampaignRequest(content_text="x", media_ids=[media_id])
    with pytest.raises(HTTPException) as e:
        await MG.create_organic_campaign(
            request=req, team_id=uuid.uuid4(),
            current_user=SimpleNamespace(id=uuid.uuid4()), db=db)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_organic_campaign_creates_post_and_targets(monkeypatch):
    monkeypatch.setattr(MG, "check_quota", AsyncMock())
    acc1, acc2 = _account("instagram"), _account("facebook")
    media_id = uuid.uuid4()
    db = _DB([
        _Res([acc1, acc2]),
        _Res([(media_id,)]),
    ])
    req = OrganicCampaignRequest(
        content_text="x", media_ids=[media_id],
        scheduled_at=datetime(2026, 6, 1, 10, 0),  # naive → tz added
        hashtags=["a"], link_url="https://x",
        metadata={"note": "ok"})
    out = await MG.create_organic_campaign(
        request=req, team_id=uuid.uuid4(),
        current_user=SimpleNamespace(id=uuid.uuid4()), db=db)
    assert out.status == MG.PostStatus.SCHEDULED
    assert out.scheduled_at.tzinfo is not None  # naive → UTC
    assert len(out.targets) == 2
    # 1 Post + 2 PostTargets added
    assert len(db.added) == 3
    post = next(o for o in db.added if o.__class__.__name__ == "Post")
    assert post.meta_data["organic_meta_campaign"] is True
    assert db.commits == 1
