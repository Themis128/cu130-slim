#!/usr/bin/env python3
"""Upsert Cloudless CF carousel workflow into social-api GeneratedWorkflow + PromptTemplate.

Runs inside social-api (or with DATABASE_URL). Does not print secrets.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

from sqlalchemy import select

# Ensure app imports work when executed as /tmp script
sys.path.insert(0, "/app")

from app.db.session import async_session_maker  # type: ignore
from app.models.user import Team, TeamMember, User
from app.models.workflow import GeneratedWorkflow, PromptTemplate

N8N_ID = "cloudless-cf-carousel-linkedin"
NAME = "Cloudless CF Carousel → LinkedIn Company"
WF_PATHS = [
    os.environ.get("CLOUDLESS_N8N_WORKFLOW_PATH", ""),
    "/tmp/cloudless-carousel-pipeline.json",
    "/workspace/n8n-workflows/cloudless-carousel-pipeline.json",
]


def _load_workflow() -> dict:
    for path in WF_PATHS:
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    raise SystemExit("Workflow JSON not found")


async def main() -> None:
    workflow = _load_workflow()
    admin_email = os.environ.get("SOCIAL_ADMIN_EMAIL")
    if not admin_email:
        raise SystemExit("SOCIAL_ADMIN_EMAIL not set")

    async with async_session_maker() as db:
        user = (
            await db.execute(select(User).where(User.email == admin_email))
        ).scalar_one_or_none()
        if not user:
            raise SystemExit(f"Admin user not found: {admin_email}")

        team = (
            await db.execute(
                select(Team).join(TeamMember).where(TeamMember.user_id == user.id)
            )
        ).scalars().first()
        if not team:
            raise SystemExit("No team for admin user")

        # Upsert template by name
        tmpl = (
            await db.execute(
                select(PromptTemplate).where(
                    PromptTemplate.team_id == team.id,
                    PromptTemplate.name == NAME,
                )
            )
        ).scalar_one_or_none()
        if tmpl:
            tmpl.n8n_workflow_json = workflow
            tmpl.description = (
                "Schedule/webhook → social-api CF carousel pipeline → LinkedIn Company Page"
            )
            tmpl.category = "linkedin"
            tmpl.tags = ["cloudless", "carousel", "cloudflare", "n8n"]
            tmpl.is_public = True
            tmpl.prompt_template = (
                "Publish a {{num_slides}}-slide LinkedIn carousel about {{topic}} "
                "as the cloudless.gr Company Page via Cloudflare Workers AI."
            )
        else:
            tmpl = PromptTemplate(
                id=uuid.uuid4(),
                team_id=team.id,
                user_id=user.id,
                name=NAME,
                description=(
                    "Schedule/webhook → social-api CF carousel pipeline → LinkedIn Company Page"
                ),
                prompt_template=(
                    "Publish a {{num_slides}}-slide LinkedIn carousel about {{topic}} "
                    "as the cloudless.gr Company Page via Cloudflare Workers AI."
                ),
                n8n_workflow_json=workflow,
                category="linkedin",
                tags=["cloudless", "carousel", "cloudflare", "n8n"],
                is_public=True,
            )
            db.add(tmpl)
        await db.flush()

        # Upsert generated workflow by n8n_workflow_id
        gen = (
            await db.execute(
                select(GeneratedWorkflow).where(
                    GeneratedWorkflow.team_id == team.id,
                    GeneratedWorkflow.n8n_workflow_id == N8N_ID,
                )
            )
        ).scalar_one_or_none()
        if not gen:
            gen = (
                await db.execute(
                    select(GeneratedWorkflow).where(
                        GeneratedWorkflow.team_id == team.id,
                        GeneratedWorkflow.prompt_text.ilike("%cloudless-cf-carousel%"),
                    )
                )
            ).scalars().first()

        prompt_text = (
            "cloudless-cf-carousel-linkedin: Cloudflare txt2img→img2img carousel "
            "with NLP plain-English fix, publish as cloudless.gr Company Page. "
            "Next one-shot Fri 28 Aug 03:15 Athens (CF reset); then every 2 days 19:00 + webhook"
        )
        variables = {
            "n8n_workflow_id": N8N_ID,
            "webhook": "/webhook/cloudless-carousel",
            "schedule": "next: Fri 28 Aug 03:15 Europe/Athens (CF reset); then every 2 days 19:00 Athens",
            "target_account_id": os.environ.get(
                "CLOUDLESS_LINKEDIN_ORG_ACCOUNT_ID",
                "4a8d9440-47d2-4bda-bd11-3776fd9022ba",
            ),
            "endpoint": "/api/v1/ai/run-carousel-and-publish",
        }

        if gen:
            gen.n8n_workflow_json = workflow
            gen.n8n_workflow_id = N8N_ID
            gen.status = "deployed"
            gen.prompt_text = prompt_text
            gen.template_id = tmpl.id
            gen.variables_used = variables
            action = "updated"
        else:
            gen = GeneratedWorkflow(
                id=uuid.uuid4(),
                team_id=team.id,
                user_id=user.id,
                prompt_text=prompt_text,
                n8n_workflow_json=workflow,
                n8n_workflow_id=N8N_ID,
                status="deployed",
                template_id=tmpl.id,
                variables_used=variables,
            )
            db.add(gen)
            action = "created"

        await db.commit()
        print(
            json.dumps(
                {
                    "action": action,
                    "generated_workflow_id": str(gen.id),
                    "template_id": str(tmpl.id),
                    "n8n_workflow_id": N8N_ID,
                    "status": "deployed",
                    "name": NAME,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
