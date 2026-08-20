from celery import shared_task
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from datetime import datetime, UTC
import httpx
import json

from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
async def execute_workflow(self, workflow_id: str, input_data: dict):
    """Execute an n8n workflow"""
    async with async_session() as db:
        from app.models.workflow import Workflow, WorkflowExecution
        
        workflow_result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
        workflow = workflow_result.scalar_one_or_none()
        if not workflow:
            return {"success": False, "error": "Workflow not found"}

        execution = WorkflowExecution(
            workflow_id=workflow.id,
            status="running",
            input_data=input_data,
            started_at=datetime.now(UTC),
        )
        db.add(execution)
        await db.commit()

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                # Trigger n8n workflow
                response = await client.post(
                    f"{settings.N8N_URL}/api/v1/workflows/{workflow.n8n_workflow_id}/execute",
                    headers={"X-N8N-API-KEY": settings.N8N_API_KEY},
                    json=input_data,
                )
                response.raise_for_status()
                result = response.json()

            execution.status = "completed"
            execution.output_data = result
            execution.completed_at = datetime.now(UTC)
            await db.commit()

            return {"success": True, "result": result}

        except Exception as e:
            execution.status = "failed"
            execution.error_message = str(e)
            execution.completed_at = datetime.now(UTC)
            await db.commit()
            
            raise self.retry(exc=e)


@shared_task
async def deploy_workflow(workflow_id: str):
    """Deploy a workflow to n8n"""
    async with async_session() as db:
        from app.models.workflow import Workflow
        
        workflow_result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
        workflow = workflow_result.scalar_one_or_none()
        if not workflow:
            return {"success": False, "error": "Workflow not found"}

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                if workflow.n8n_workflow_id:
                    # Update existing workflow
                    response = await client.put(
                        f"{settings.N8N_URL}/api/v1/workflows/{workflow.n8n_workflow_id}",
                        headers={"X-N8N-API-KEY": settings.N8N_API_KEY},
                        json=workflow.definition,
                    )
                else:
                    # Create new workflow
                    response = await client.post(
                        f"{settings.N8N_URL}/api/v1/workflows",
                        headers={"X-N8N-API-KEY": settings.N8N_API_KEY},
                        json=workflow.definition,
                    )
                response.raise_for_status()
                result = response.json()

            workflow.n8n_workflow_id = result.get("id")
            workflow.status = "active"
            await db.commit()

            return {"success": True, "n8n_workflow_id": workflow.n8n_workflow_id}

        except Exception as e:
            workflow.status = "error"
            await db.commit()
            return {"success": False, "error": str(e)}