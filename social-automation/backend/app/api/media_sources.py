"""Media-source endpoints — browse Nextcloud/Google Drive, import picks.

    GET  /media-sources/nextcloud/files?path=Photos     → folder listing
    POST /media-sources/nextcloud/import  {paths: []}   → media_assets rows
    GET  /media-sources/gdrive/connect-url              → OAuth consent URL
    POST /media-sources/gdrive/connect    {code}        → stores refresh token
    GET  /media-sources/gdrive/files?query=             → drive listing
    POST /media-sources/gdrive/import     {file_ids: []} → media_assets rows
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.services import media_sources
from app.services.media_storage import save_uploaded_media

router = APIRouter()


def _media_only(files):
    return [f for f in files if f.is_dir or f.mime_type.startswith(("image/", "video/"))]


@router.get("/nextcloud/files")
async def nextcloud_files(path: str = "", user: User = Depends(get_current_user)):
    try:
        files = _media_only(await media_sources.nextcloud_list(path))
    except media_sources.MediaSourceError as e:
        raise HTTPException(502, str(e))
    return {"path": path, "files": [f.__dict__ for f in files]}


class NcImport(BaseModel):
    paths: list[str]
    tags: str = "nextcloud,import"


@router.post("/nextcloud/import")
async def nextcloud_import(body: NcImport,
                           db: AsyncSession = Depends(get_db),
                           user: User = Depends(get_current_user)):
    team_id = getattr(user, "team_id", None)
    if team_id is None:
        from sqlalchemy import select

        from app.models.user import TeamMember
        tm = (await db.execute(
            select(TeamMember).where(TeamMember.user_id == user.id))).scalars().first()
        if tm is None:
            raise HTTPException(400, "user has no team")
        team_id = tm.team_id
    imported, errors = [], []
    for p in body.paths:
        try:
            data, mime = await media_sources.nextcloud_download(p)
            asset = await save_uploaded_media(
                db, team_id=team_id, user_id=user.id, content=data,
                original_filename=p.rsplit("/", 1)[-1], mime_type=mime,
                alt_text=f"Nextcloud import — {p}", tags=body.tags,
                width=0, height=0)
            imported.append(str(asset.id))
        except Exception as e:  # per-file, don't fail the batch
            errors.append({"path": p, "error": str(e)[:200]})
    await db.commit()
    return {"imported": imported, "errors": errors}


class GdConnectUrl(BaseModel):
    redirect_uri: str = "http://localhost:4180"


@router.post("/gdrive/connect-url")
async def gdrive_connect_url(body: GdConnectUrl,
                             user: User = Depends(get_current_user)):
    """Consent URL covering drive.readonly + the existing photos scopes.

    The returned refresh token must be stored as GOOGLE_DRIVE_REFRESH_TOKEN
    (or replace GOOGLE_PHOTOS_REFRESH_TOKEN — it carries all scopes).
    """
    return {"url": media_sources.gdrive_connect_url(body.redirect_uri)}


class GdConnect(BaseModel):
    code: str
    redirect_uri: str = "http://localhost:4180"


@router.post("/gdrive/connect")
async def gdrive_connect(body: GdConnect,
                         user: User = Depends(get_current_user)):
    """Exchange the consent code → refresh token (return it for .env storage)."""
    try:
        rt = await media_sources.gdrive_exchange_code(body.code, body.redirect_uri)
    except media_sources.MediaSourceError as e:
        raise HTTPException(400, str(e))
    return {"refresh_token": rt,
            "hint": "store as GOOGLE_DRIVE_REFRESH_TOKEN in .env"}


@router.get("/gdrive/files")
async def gdrive_files(query: str = "", folder_id: str | None = None,
                       user: User = Depends(get_current_user)):
    try:
        files = await media_sources.gdrive_list(query=query, folder_id=folder_id)
    except media_sources.MediaSourceError as e:
        raise HTTPException(502, str(e))
    return {"files": [f.__dict__ for f in files]}


class GdImport(BaseModel):
    file_ids: list[str]
    tags: str = "gdrive,import"


@router.post("/gdrive/import")
async def gdrive_import(body: GdImport,
                        db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    team_id = getattr(user, "team_id", None)
    if team_id is None:
        from sqlalchemy import select

        from app.models.user import TeamMember
        tm = (await db.execute(
            select(TeamMember).where(TeamMember.user_id == user.id))).scalars().first()
        if tm is None:
            raise HTTPException(400, "user has no team")
        team_id = tm.team_id
    imported, errors = [], []
    meta_by_id: dict[str, str] = {}
    try:
        files = await media_sources.gdrive_list(page_size=200)
        meta_by_id = {f.id: f.name for f in files}
    except Exception:
        pass
    for fid in body.file_ids:
        try:
            data, mime = await media_sources.gdrive_download(fid)
            name = meta_by_id.get(fid, fid)
            asset = await save_uploaded_media(
                db, team_id=team_id, user_id=user.id, content=data,
                original_filename=name, mime_type=mime,
                alt_text=f"Google Drive import — {name}", tags=body.tags,
                width=0, height=0)
            imported.append(str(asset.id))
        except Exception as e:
            errors.append({"file_id": fid, "error": str(e)[:200]})
    await db.commit()
    return {"imported": imported, "errors": errors}
