"""Research workspace API — Papers-AI-style full-context research."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.models.user import User
from app.services import research_workspace as rw

router = APIRouter(prefix="/research", tags=["research"])


class IngestReq(BaseModel):
    project: str
    folder: str


class AskReq(BaseModel):
    question: str
    n: int = 6


@router.post("/ingest")
async def ingest(req: IngestReq, u: User = Depends(get_current_user)):
    return await rw.ingest_nextcloud_folder(str(u.team_id), req.project, req.folder)


@router.post("/ask")
async def ask(req: AskReq, u: User = Depends(get_current_user)):
    try:
        return await rw.ask(str(u.team_id), req.question, req.n)
    except Exception as e:
        raise HTTPException(502, f"research query failed: {e}") from e


@router.get("/status")
async def status(u: User = Depends(get_current_user)):
    return {"collection": f"research_{u.team_id}", "source": "nextcloud+webdav"}
