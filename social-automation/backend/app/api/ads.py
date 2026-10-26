"""LinkedIn Ads API endpoints — status + snapshot for the gate UI."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ads", tags=["ads"])


@router.get("/linkedin/status")
async def linkedin_ads_status(db: AsyncSession = Depends(get_db)):
    try:
        from app.services import linkedin_ads
        accs = await linkedin_ads.ad_accounts(db)
        return {"accounts": len(accs), "detail": accs[:5]}
    except Exception as e:
        raise HTTPException(502, f"ads api: {e}")


@router.post("/linkedin/snapshot")
async def trigger_snapshot(db: AsyncSession = Depends(get_db)):
    from app.services import linkedin_ads
    snap = await linkedin_ads.snapshot(db)
    await db.commit()
    return snap
