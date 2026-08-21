import os
import uuid
from datetime import datetime

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.session import get_db
from app.models.content import MediaAsset
from app.models.user import Team, TeamMember, User

router = APIRouter()

UPLOAD_DIR = "/app/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


class MediaAssetResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    filename: str | None
    mime_type: str | None
    size_bytes: int | None
    storage_path: str
    width: int | None
    height: int | None
    duration_seconds: int | None
    alt_text: str | None
    tags: list[str]
    source: str
    generation_prompt: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class MediaListResponse(BaseModel):
    assets: list[MediaAssetResponse]
    total: int
    page: int
    page_size: int


@router.post("/upload", response_model=MediaAssetResponse, status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile = File(...),
    alt_text: str = Form(None),
    tags: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    content = await file.read()
    file_ext = os.path.splitext(file.filename)[1] if file.filename else ""
    filename = f"{uuid.uuid4()}{file_ext}"
    storage_path = os.path.join(UPLOAD_DIR, filename)

    async with aiofiles.open(storage_path, "wb") as f:
        await f.write(content)

    asset = MediaAsset(
        team_id=team.id,
        user_id=current_user.id,
        filename=file.filename,
        mime_type=file.content_type,
        size_bytes=len(content),
        storage_path=storage_path,
        alt_text=alt_text,
        tags=tags.split(",") if tags else [],
        source="upload",
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)

    return asset


@router.get("/assets", response_model=MediaListResponse)
async def list_media(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    source: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        return MediaListResponse(assets=[], total=0, page=page, page_size=page_size)

    query = select(MediaAsset).where(MediaAsset.team_id == team.id)
    if source:
        query = query.where(MediaAsset.source == source)

    from sqlalchemy import func
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    query = query.order_by(MediaAsset.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    assets = result.scalars().all()

    return MediaListResponse(assets=assets, total=total, page=page, page_size=page_size)


@router.get("/assets/{asset_id}", response_model=MediaAssetResponse)
async def get_media(asset_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MediaAsset).where(MediaAsset.id == asset_id)
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_media(asset_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    # Delete file
    if os.path.exists(asset.storage_path):
        os.remove(asset.storage_path)

    await db.delete(asset)
    await db.commit()


@router.post("/generate-image", response_model=MediaAssetResponse)
async def generate_image(
    prompt: str = Form(...),
    workflow_json: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # TODO: Call ComfyUI API to generate image
    # For now, return a placeholder
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    asset = MediaAsset(
        team_id=team.id,
        user_id=current_user.id,
        filename=f"generated_{uuid.uuid4().hex[:8]}.png",
        mime_type="image/png",
        size_bytes=0,
        storage_path="",
        alt_text=prompt,
        tags=["ai-generated"],
        source="comfyui",
        generation_prompt=prompt,
        comfyui_workflow_json=eval(workflow_json) if workflow_json else None,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)

    return asset


# Need to import User
