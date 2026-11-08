"""Research workspace API — Papers-AI-style full-context research."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import TeamId
from app.services import research_workspace as rw

router = APIRouter(prefix="/research", tags=["research"])


class IngestReq(BaseModel):
    project: str
    folder: str


class AskReq(BaseModel):
    question: str
    n: int = 6


@router.post("/ingest")
async def ingest(req: IngestReq, team_id: TeamId):
    return await rw.ingest_nextcloud_folder(str(team_id), req.project, req.folder)


@router.post("/ask")
async def ask(req: AskReq, team_id: TeamId):
    try:
        return await rw.ask(str(team_id), req.question, req.n)
    except Exception as e:
        raise HTTPException(502, f"research query failed: {e}") from e


@router.get("/status")
async def status(team_id: TeamId):
    return {"collection": f"research_{team_id}", "source": "nextcloud+webdav"}
