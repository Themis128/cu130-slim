from fastapi import APIRouter

from app.api import (
    accounts,
    ai,
    ai_providers,
    analytics,
    audit,
    auth,
    brand,
    cf_db,
    content,
    instagram,
    linkedin,
    media,
    media_enhance,
    ops,
    profile,
    publishing,
    secrets,
    workflows,
)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(content.router, prefix="/content", tags=["content"])
api_router.include_router(media.router, prefix="/media", tags=["media"])
api_router.include_router(media_enhance.router, prefix="/media/enhance", tags=["media-enhance"])
api_router.include_router(workflows.router, prefix="/workflows", tags=["workflows"])
api_router.include_router(accounts.router, prefix="/accounts", tags=["accounts"])
api_router.include_router(linkedin.router, prefix="/linkedin", tags=["linkedin"])
api_router.include_router(instagram.router, prefix="/instagram", tags=["instagram"])
api_router.include_router(publishing.router, prefix="/publishing", tags=["publishing"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])
api_router.include_router(ai_providers.router, prefix="/ai-providers", tags=["ai-providers"])
api_router.include_router(brand.router, prefix="/brand", tags=["brand"])
api_router.include_router(ops.router, prefix="/ops", tags=["ops"])
api_router.include_router(cf_db.router, prefix="/cf-db", tags=["cf-db"])
api_router.include_router(profile.router, prefix="/profile", tags=["profile"])
api_router.include_router(secrets.router, prefix="/secrets", tags=["secrets"])
api_router.include_router(audit.router, prefix="/audit", tags=["audit"])
