"""LinkedIn Ads API endpoints — status + snapshot for the gate UI."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.session import get_db
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ads", tags=["ads"])


@router.get("/linkedin/status")
async def linkedin_ads_status(db: AsyncSession = Depends(get_db), _u: User = Depends(get_current_user)):
    try:
        from app.services import linkedin_ads

        accs = await linkedin_ads.ad_accounts(db, getattr(_u, "team_id", None))
        return {"accounts": len(accs), "detail": accs[:5]}
    except Exception as e:
        raise HTTPException(502, f"ads api: {e}")


@router.post("/linkedin/snapshot")
async def trigger_snapshot(db: AsyncSession = Depends(get_db), _u: User = Depends(get_current_user)):
    from app.services import linkedin_ads

    snap = await linkedin_ads.snapshot(db, getattr(_u, "team_id", None))
    await db.commit()
    return snap
