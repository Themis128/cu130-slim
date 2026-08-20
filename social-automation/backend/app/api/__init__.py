from fastapi import APIRouter
from app.api import auth, content, media, workflows, accounts, publishing, analytics, ai

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(content.router, prefix="/content", tags=["content"])
api_router.include_router(media.router, prefix="/media", tags=["media"])
api_router.include_router(workflows.router, prefix="/workflows", tags=["workflows"])
api_router.include_router(accounts.router, prefix="/accounts", tags=["accounts"])
api_router.include_router(publishing.router, prefix="/publishing", tags=["publishing"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["analytics"])
api_router.include_router(ai.router, prefix="/ai", tags=["ai"])