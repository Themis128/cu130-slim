from datetime import UTC, datetime

import httpx
from celery import shared_task
from sqlalchemy import select

from app.core.config import settings
from app.models.workflow import GeneratedWorkflow
from app.services.db_sync import sync_after_worker_task
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()



@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def execute_workflow(self, workflow_id: str, input_data: dict) -> dict:  # type: ignore[no-untyped-def]
    result = run_async(_execute_workflow_async(workflow_id, input_data))
    # Push worker writes (generated_workflows) to D1 primary
    run_async(sync_after_worker_task(["generated_workflows"]))
    return result


async def _execute_workflow_async(workflow_id: str, input_data: dict) -> dict:
    async with task_session() as db:
        result = await db.execute(select(GeneratedWorkflow).where(GeneratedWorkflow.id == workflow_id))
        workflow = result.scalar_one_or_none()
        if not workflow:
            return {"success": False, "error": "Workflow not found"}

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                headers = {"X-N8N-API-KEY": settings.N8N_API_KEY}
                response = await client.post(
                    f"{settings.N8N_API_URL}/api/v1/workflows/{workflow.n8n_workflow_id}/execute",
                    headers=headers,
                    json=input_data,
                )
                response.raise_for_status()
                result_data = response.json()

            workflow.status = "active"
            workflow.updated_at = datetime.now(UTC)
            await db.commit()

            return {"success": True, "result": result_data}

        except Exception as e:
            await db.commit()
            return {"success": False, "error": str(e)}


@shared_task
def deploy_workflow(workflow_id: str) -> dict:
    result = run_async(_deploy_workflow_async(workflow_id))
    # Push worker writes (generated_workflows) to D1 primary
    run_async(sync_after_worker_task(["generated_workflows"]))
    return result


async def _deploy_workflow_async(workflow_id: str) -> dict:
    async with task_session() as db:
        result = await db.execute(select(GeneratedWorkflow).where(GeneratedWorkflow.id == workflow_id))
        workflow = result.scalar_one_or_none()
        if not workflow:
            return {"success": False, "error": "Workflow not found"}

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                headers = {"X-N8N-API-KEY": settings.N8N_API_KEY}
                if workflow.n8n_workflow_id:
                    response = await client.put(
                        f"{settings.N8N_API_URL}/api/v1/workflows/{workflow.n8n_workflow_id}",
                        headers=headers,
                        json=workflow.n8n_workflow_json,
                    )
                else:
                    response = await client.post(
                        f"{settings.N8N_API_URL}/api/v1/workflows",
                        headers=headers,
                        json=workflow.n8n_workflow_json,
                    )
                response.raise_for_status()
                n8n_result = response.json()

            workflow.n8n_workflow_id = n8n_result.get("id")
            workflow.status = "active"
            workflow.updated_at = datetime.now(UTC)
            await db.commit()

            return {"success": True, "n8n_workflow_id": workflow.n8n_workflow_id}

        except Exception as e:
            workflow.status = "draft"
            workflow.updated_at = datetime.now(UTC)
            await db.commit()
            return {"success": False, "error": str(e)}
