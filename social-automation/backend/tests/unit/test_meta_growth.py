import uuid

import pytest
from pydantic import ValidationError

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
