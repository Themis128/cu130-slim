import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models.user import Team, TeamMember, User
from app.models.workflow import GeneratedWorkflow, PromptTemplate

router = APIRouter()
settings = get_settings()


class PromptTemplateCreate(BaseModel):
    name: str
    description: str | None = None
    prompt_template: str
    n8n_workflow_json: dict
    category: str | None = None
    tags: list[str] = []
    is_public: bool = False


class PromptTemplateResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    user_id: uuid.UUID | None
    name: str
    description: str | None
    prompt_template: str
    n8n_workflow_json: dict
    category: str | None
    tags: list[str]
    is_public: bool
    usage_count: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WorkflowGenerateRequest(BaseModel):
    prompt: str
    template_id: uuid.UUID | None = None


class WorkflowGenerateResponse(BaseModel):
    n8n_workflow_json: dict
    variables_used: dict
    template_id: uuid.UUID | None


class GeneratedWorkflowResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    user_id: uuid.UUID | None
    prompt_text: str
    n8n_workflow_json: dict
    n8n_workflow_id: str | None
    status: str
    template_id: uuid.UUID | None
    variables_used: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=list[GeneratedWorkflowResponse])
async def list_workflows(
    status: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        return []

    query = select(GeneratedWorkflow).where(GeneratedWorkflow.team_id == team.id)
    if status:
        query = query.where(GeneratedWorkflow.status == status)
    query = query.order_by(GeneratedWorkflow.created_at.desc())

    rows = await db.execute(query)
    return rows.scalars().all()


@router.get("/templates", response_model=list[PromptTemplateResponse])
async def list_templates(
    category: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        return []

    query = select(PromptTemplate).where(PromptTemplate.team_id == team.id)
    if category:
        query = query.where(PromptTemplate.category == category)
    query = query.order_by(PromptTemplate.usage_count.desc())

    result = await db.execute(query)
    return result.scalars().all()


@router.post("/templates", response_model=PromptTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    template_data: PromptTemplateCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    template = PromptTemplate(
        team_id=team.id,
        user_id=current_user.id,
        name=template_data.name,
        description=template_data.description,
        prompt_template=template_data.prompt_template,
        n8n_workflow_json=template_data.n8n_workflow_json,
        category=template_data.category,
        tags=template_data.tags,
        is_public=template_data.is_public,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)

    return template


@router.post("/generate", response_model=WorkflowGenerateResponse)
async def generate_workflow(
    request: WorkflowGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # TODO: Implement actual AI-powered workflow generation using Ollama
    # For now, return a basic template if available

    template = None
    if request.template_id:
        result = await db.execute(select(PromptTemplate).where(PromptTemplate.id == request.template_id))
        template = result.scalar_one_or_none()

    # Placeholder: return a basic n8n workflow structure
    n8n_workflow = {
        "name": f"Generated: {request.prompt[:50]}",
        "nodes": [
            {
                "name": "Start",
                "type": "n8n-nodes-base.start",
                "typeVersion": 1,
                "position": [250, 300],
            }
        ],
        "connections": {},
        "settings": {"executionOrder": "v1"},
    }

    variables_used = {}

    if template:
        # Simple variable substitution
        import re
        vars_found = re.findall(r"{{(\w+)}}", template.prompt_template)
        for var in vars_found:
            variables_used[var] = f"<{var}>"

        n8n_workflow = template.n8n_workflow_json.copy()
        n8n_workflow["name"] = f"Generated: {request.prompt[:50]}"

    # Save generated workflow
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()

    gen_workflow = GeneratedWorkflow(
        team_id=team.id if team else uuid.uuid4(),
        user_id=current_user.id,
        prompt_text=request.prompt,
        n8n_workflow_json=n8n_workflow,
        template_id=request.template_id,
        variables_used=variables_used,
    )
    db.add(gen_workflow)
    await db.commit()

    return WorkflowGenerateResponse(
        n8n_workflow_json=n8n_workflow,
        variables_used=variables_used,
        template_id=request.template_id,
    )


@router.post("/import-cloudless-carousel")
async def import_cloudless_carousel(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Register the Cloudless CF→LinkedIn n8n workflow in app templates + workflows."""
    import json
    import os

    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    n8n_id = "cloudless-cf-carousel-linkedin"
    name = "Cloudless CF Carousel → LinkedIn Company"
    paths = [
        os.environ.get("CLOUDLESS_N8N_WORKFLOW_PATH", ""),
        "/app/n8n-workflows/cloudless-carousel-pipeline.json",
        "/tmp/cloudless-carousel-pipeline.json",
    ]
    workflow = None
    for path in paths:
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                workflow = json.load(f)
            break
    if workflow is None:
        raise HTTPException(
            status_code=500,
            detail="Workflow JSON not found in container; copy to /tmp or set CLOUDLESS_N8N_WORKFLOW_PATH",
        )

    tmpl = (
        await db.execute(
            select(PromptTemplate).where(
                PromptTemplate.team_id == team.id,
                PromptTemplate.name == name,
            )
        )
    ).scalar_one_or_none()
    prompt_tpl = (
        "Publish a {{num_slides}}-slide LinkedIn carousel about {{topic}} "
        "as the cloudless.gr Company Page via Cloudflare Workers AI."
    )
    desc = "Schedule/webhook → social-api CF carousel → LinkedIn Company Page"
    if tmpl:
        tmpl.n8n_workflow_json = workflow
        tmpl.description = desc
        tmpl.prompt_template = prompt_tpl
        tmpl.category = "linkedin"
        tmpl.tags = ["cloudless", "carousel", "cloudflare", "n8n"]
        tmpl.is_public = True
    else:
        tmpl = PromptTemplate(
            team_id=team.id,
            user_id=current_user.id,
            name=name,
            description=desc,
            prompt_template=prompt_tpl,
            n8n_workflow_json=workflow,
            category="linkedin",
            tags=["cloudless", "carousel", "cloudflare", "n8n"],
            is_public=True,
        )
        db.add(tmpl)
    await db.flush()

    gen = (
        await db.execute(
            select(GeneratedWorkflow).where(
                GeneratedWorkflow.team_id == team.id,
                GeneratedWorkflow.n8n_workflow_id == n8n_id,
            )
        )
    ).scalar_one_or_none()
    prompt_text = (
        "cloudless-cf-carousel-linkedin: Cloudflare txt2img→img2img carousel "
        "with NLP plain-English fix, publish as cloudless.gr Company Page."
    )
    variables = {
        "n8n_workflow_id": n8n_id,
        "webhook": "/webhook/cloudless-carousel",
        "schedule": "next: Fri 28 Aug 03:15 Europe/Athens (CF reset); then every 2 days 19:00 Athens",
        "target_account_id": "4a8d9440-47d2-4bda-bd11-3776fd9022ba",
        "endpoint": "/api/v1/ai/run-carousel-and-publish",
    }
    if gen:
        gen.n8n_workflow_json = workflow
        gen.status = "deployed"
        gen.prompt_text = prompt_text
        gen.template_id = tmpl.id
        gen.variables_used = variables
        action = "updated"
    else:
        gen = GeneratedWorkflow(
            team_id=team.id,
            user_id=current_user.id,
            prompt_text=prompt_text,
            n8n_workflow_json=workflow,
            n8n_workflow_id=n8n_id,
            status="deployed",
            template_id=tmpl.id,
            variables_used=variables,
        )
        db.add(gen)
        action = "created"
    await db.commit()
    await db.refresh(gen)

    return {
        "action": action,
        "generated_workflow_id": str(gen.id),
        "template_id": str(tmpl.id),
        "n8n_workflow_id": n8n_id,
        "status": gen.status,
        "name": name,
    }


@router.post("/deploy/{workflow_id}")
async def deploy_workflow(
    workflow_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(GeneratedWorkflow).where(GeneratedWorkflow.id == workflow_id))
    workflow = result.scalar_one_or_none()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Deploy to n8n
    async with httpx.AsyncClient() as client:
        headers = {"X-N8N-API-KEY": settings.N8N_API_KEY}
        resp = await client.post(
            f"{settings.N8N_API_URL}/api/v1/workflows",
            headers=headers,
            json=workflow.n8n_workflow_json,
        )
        if resp.status_code not in (200, 201):
            raise HTTPException(status_code=500, detail=f"n8n deploy failed: {resp.text}")

        n8n_workflow = resp.json()
        workflow.n8n_workflow_id = n8n_workflow.get("id")
        workflow.status = "deployed"
        await db.commit()

    return {"message": "Workflow deployed", "n8n_workflow_id": workflow.n8n_workflow_id}


@router.post("/execute/{workflow_id}")
async def execute_workflow(
    workflow_id: uuid.UUID,
    data: dict = {},
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(GeneratedWorkflow).where(GeneratedWorkflow.id == workflow_id))
    workflow = result.scalar_one_or_none()
    if not workflow or not workflow.n8n_workflow_id:
        raise HTTPException(status_code=404, detail="Workflow not found or not deployed")

    async with httpx.AsyncClient() as client:
        headers = {"X-N8N-API-KEY": settings.N8N_API_KEY}
        resp = await client.post(
            f"{settings.N8N_API_URL}/api/v1/workflows/{workflow.n8n_workflow_id}/execute",
            headers=headers,
            json={"data": data},
        )
        if resp.status_code not in (200, 201):
            raise HTTPException(status_code=500, detail=f"n8n execute failed: {resp.text}")

        execution = resp.json()
        workflow.workflow_run_id = execution.get("id")
        await db.commit()

    return {"message": "Execution started", "execution_id": execution.get("id")}
