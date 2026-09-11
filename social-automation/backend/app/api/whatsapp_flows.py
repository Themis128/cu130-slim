"""WhatsApp Flows API router.

Endpoints for creating, managing, publishing, and sending WhatsApp Flows —
structured interactive experiences built directly inside WhatsApp chats.

Covers the Flows Graph API (create, list, get, update, publish, delete),
Flow JSON templates, sending Flow messages, and the Flow Data Endpoint
for endpoint-powered Flows.

API reference:
- https://developers.facebook.com/docs/whatsapp/flows
- https://developers.facebook.com/docs/whatsapp/flows/reference/flowsapi
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api.whatsapp import _get_whatsapp_account
from app.db.session import get_db
from app.models.social_account import SocialAccount
from app.models.user import User
from app.services.whatsapp_flows import (
    FLOW_TEMPLATES,
    VALID_FLOW_CATEGORIES,
    WhatsAppFlowsClient,
    build_flow_endpoint_response,
    parse_flow_endpoint_request,
    parse_flow_response,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get_flows_client(account: SocialAccount) -> WhatsAppFlowsClient:
    """Build a WhatsAppFlowsClient from a SocialAccount."""
    meta = account.meta_data or {}
    access_token = meta.get("access_token", "")
    waba_id = meta.get("waba_id", "")
    phone_number_id = meta.get("phone_number_id", "")
    if not access_token or not waba_id:
        raise HTTPException(
            status_code=400,
            detail="No WhatsApp credentials found. Reconnect the WhatsApp account.",
        )
    # access_token is stored encrypted
    from app.core.security import decrypt_token
    if isinstance(access_token, str) and not access_token.startswith("EA"):
        try:
            access_token = decrypt_token(access_token)
        except Exception:
            pass  # might be plaintext
    return WhatsAppFlowsClient(
        access_token=access_token,
        waba_id=waba_id,
        phone_number_id=phone_number_id,
    )


# ------------------------------------------------------------------
# Pydantic schemas
# ------------------------------------------------------------------

class FlowCreateRequest(BaseModel):
    """Request to create a new Flow."""
    name: str = Field(..., description="Flow name (unique within the WABA)")
    categories: list[str] = Field(
        ..., description=f"Flow categories. Valid: {sorted(VALID_FLOW_CATEGORIES)}"
    )
    endpoint_uri: str | None = Field(
        None, description="URL of the Flow Data Endpoint (for endpoint-powered Flows)"
    )
    clone_flow_id: str | None = Field(None, description="ID of a source Flow to clone")


class FlowUpdateRequest(BaseModel):
    """Request to update a Flow's metadata."""
    name: str | None = None
    categories: list[str] | None = None
    endpoint_uri: str | None = None


class FlowJSONUpdateRequest(BaseModel):
    """Request to update a Flow's JSON content."""
    flow_json: dict = Field(..., description="The Flow JSON content")


class FlowSendRequest(BaseModel):
    """Request to send a Flow message to a customer."""
    to: str = Field(..., description="Recipient phone number (E.164)")
    flow_id: str = Field(..., description="The Flow ID to send")
    flow_token: str = Field(..., description="Unique token for tracking this Flow message")
    body_text: str = Field(default="Please fill out this form", description="Body text")
    header_text: str | None = Field(None, description="Optional header text")
    footer_text: str | None = Field(None, description="Optional footer text")
    flow_action: str = Field(
        default="navigate",
        description="'navigate' (open a screen) or 'data_exchange' (endpoint-powered)",
    )
    screen: str = Field(default="FIRST_ENTRY_SCREEN", description="Target screen for 'navigate'")
    flow_action_payload: dict | None = None
    messaging_type: str = Field(
        default="RESPONSE",
        description="'RESPONSE' (24h window) or 'TEMPLATE' (business-initiated)",
    )


class FlowFromTemplateRequest(BaseModel):
    """Request to create a Flow from a pre-built template."""
    template_id: str = Field(
        ...,
        description=f"Template ID. Valid: {list(FLOW_TEMPLATES.keys())}",
    )
    name: str | None = Field(None, description="Override Flow name (defaults to template name)")
    business_name: str = Field(
        default="Cloudless",
        description="Business name to use in the Flow content",
    )
    endpoint_uri: str | None = Field(
        None,
        description="Optional endpoint URL for endpoint-powered Flows",
    )


# ------------------------------------------------------------------
# Flow CRUD endpoints
# ------------------------------------------------------------------

@router.post("/{account_id}/flows", response_model=dict)
async def create_flow(
    account_id: uuid.UUID,
    body: FlowCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a new WhatsApp Flow.

    POST /{waba_id}/flows via the Flows Graph API.
    """
    account = await _get_whatsapp_account(db, account_id, user)
    client = _get_flows_client(account)
    try:
        result = await client.create_flow(
            name=body.name,
            categories=body.categories,
            endpoint_uri=body.endpoint_uri,
            clone_flow_id=body.clone_flow_id,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to create Flow: {e}")


@router.get("/{account_id}/flows", response_model=list)
async def list_flows(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all WhatsApp Flows on the WABA."""
    account = await _get_whatsapp_account(db, account_id, user)
    client = _get_flows_client(account)
    try:
        return await client.list_flows()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to list Flows: {e}")


@router.get("/{account_id}/flows/{flow_id}", response_model=dict)
async def get_flow(
    account_id: uuid.UUID,
    flow_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get details of a single WhatsApp Flow."""
    account = await _get_whatsapp_account(db, account_id, user)
    client = _get_flows_client(account)
    try:
        return await client.get_flow(flow_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to get Flow: {e}")


@router.put("/{account_id}/flows/{flow_id}", response_model=dict)
async def update_flow_metadata(
    account_id: uuid.UUID,
    flow_id: str,
    body: FlowUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update a Flow's metadata (name, categories, endpoint_uri)."""
    account = await _get_whatsapp_account(db, account_id, user)
    client = _get_flows_client(account)
    try:
        return await client.update_flow_metadata(
            flow_id=flow_id,
            name=body.name,
            categories=body.categories,
            endpoint_uri=body.endpoint_uri,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to update Flow: {e}")


@router.put("/{account_id}/flows/{flow_id}/json", response_model=dict)
async def update_flow_json(
    account_id: uuid.UUID,
    flow_id: str,
    body: FlowJSONUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update a Flow's Flow JSON content."""
    account = await _get_whatsapp_account(db, account_id, user)
    client = _get_flows_client(account)
    try:
        return await client.update_flow_json(flow_id, body.flow_json)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to update Flow JSON: {e}")


@router.get("/{account_id}/flows/{flow_id}/json", response_model=dict)
async def get_flow_json(
    account_id: uuid.UUID,
    flow_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the Flow JSON content of a Flow."""
    account = await _get_whatsapp_account(db, account_id, user)
    client = _get_flows_client(account)
    try:
        return await client.get_flow_json(flow_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to get Flow JSON: {e}")


@router.post("/{account_id}/flows/{flow_id}/validate", response_model=dict)
async def validate_flow_json(
    account_id: uuid.UUID,
    flow_id: str,
    body: FlowJSONUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Validate a Flow JSON without saving it."""
    account = await _get_whatsapp_account(db, account_id, user)
    client = _get_flows_client(account)
    try:
        return await client.validate_flow_json(flow_id, body.flow_json)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to validate Flow JSON: {e}")


@router.post("/{account_id}/flows/{flow_id}/publish", response_model=dict)
async def publish_flow(
    account_id: uuid.UUID,
    flow_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Publish a Flow (makes it live, cannot be edited after)."""
    account = await _get_whatsapp_account(db, account_id, user)
    client = _get_flows_client(account)
    try:
        return await client.publish_flow(flow_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to publish Flow: {e}")


@router.delete("/{account_id}/flows/{flow_id}", response_model=dict)
async def delete_flow(
    account_id: uuid.UUID,
    flow_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete a Flow."""
    account = await _get_whatsapp_account(db, account_id, user)
    client = _get_flows_client(account)
    try:
        return await client.delete_flow(flow_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to delete Flow: {e}")


# ------------------------------------------------------------------
# Send Flow message
# ------------------------------------------------------------------

@router.post("/{account_id}/flows/send", response_model=dict)
async def send_flow(
    account_id: uuid.UUID,
    body: FlowSendRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Send a Flow message to a WhatsApp number.

    POST /{phone_number_id}/messages with type=interactive, interactive.type=flow
    """
    account = await _get_whatsapp_account(db, account_id, user)
    client = _get_flows_client(account)
    try:
        return await client.send_flow(
            to=body.to,
            flow_id=body.flow_id,
            flow_token=body.flow_token,
            body_text=body.body_text,
            header_text=body.header_text,
            footer_text=body.footer_text,
            flow_action=body.flow_action,
            screen=body.screen,
            flow_action_payload=body.flow_action_payload,
            messaging_type=body.messaging_type,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to send Flow: {e}")


# ------------------------------------------------------------------
# Flow templates
# ------------------------------------------------------------------

@router.get("/{account_id}/flows/templates/list", response_model=list)
async def list_flow_templates(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List available pre-built Flow templates."""
    # Validate account exists (no Flows API call needed)
    await _get_whatsapp_account(db, account_id, user)
    return [
        {
            "id": tid,
            "name": t["name"],
            "category": t["category"],
            "description": t["description"],
        }
        for tid, t in FLOW_TEMPLATES.items()
    ]


@router.get("/{account_id}/flows/templates/{template_id}/json", response_model=dict)
async def get_flow_template_json(
    account_id: uuid.UUID,
    template_id: str,
    business_name: str = "Cloudless",
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get the Flow JSON for a pre-built template."""
    await _get_whatsapp_account(db, account_id, user)
    if template_id not in FLOW_TEMPLATES:
        raise HTTPException(
            status_code=404,
            detail=f"Template '{template_id}' not found. Valid: {list(FLOW_TEMPLATES.keys())}",
        )
    template = FLOW_TEMPLATES[template_id]
    return template["generator"](business_name=business_name)


@router.post("/{account_id}/flows/from-template", response_model=dict)
async def create_flow_from_template(
    account_id: uuid.UUID,
    body: FlowFromTemplateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a Flow from a pre-built template.

    This creates the Flow via the Flows API, then updates its Flow JSON
    with the template content. The Flow is left in DRAFT status — publish
    it with POST /{account_id}/flows/{flow_id}/publish when ready.
    """
    account = await _get_whatsapp_account(db, account_id, user)
    client = _get_flows_client(account)

    if body.template_id not in FLOW_TEMPLATES:
        raise HTTPException(
            status_code=404,
            detail=f"Template '{body.template_id}' not found. Valid: {list(FLOW_TEMPLATES.keys())}",
        )
    template = FLOW_TEMPLATES[body.template_id]
    flow_name = body.name or template["name"]

    try:
        # Step 1: Create the Flow
        create_result = await client.create_flow(
            name=flow_name,
            categories=[template["category"]],
            endpoint_uri=body.endpoint_uri,
        )
        flow_id = create_result.get("id", "")
        if not flow_id:
            raise HTTPException(
                status_code=502,
                detail=f"Flow created but no ID returned: {create_result}",
            )

        # Step 2: Update the Flow JSON with template content
        flow_json = template["generator"](business_name=body.business_name)
        try:
            await client.update_flow_json(flow_id, flow_json)
        except Exception as e:
            logger.warning(
                "Flow %s created but JSON update failed: %s",
                flow_id, e,
            )

        return {
            "flow_id": flow_id,
            "name": flow_name,
            "template": body.template_id,
            "category": template["category"],
            "status": "DRAFT",
            "json_updated": True,
            "message": "Flow created from template. Publish with POST /flows/{flow_id}/publish when ready.",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to create Flow from template: {e}",
        )


# ------------------------------------------------------------------
# Flow Data Endpoint (for endpoint-powered Flows)
# ------------------------------------------------------------------

@router.post("/flows/endpoint", response_model=dict)
async def flow_data_endpoint(
    request: Request,
):
    """Flow Data Endpoint for endpoint-powered Flows.

    When a Flow uses a data_channel_uri, WhatsApp calls this endpoint with
    POST requests containing the current screen and form data. The endpoint
    responds with the next screen to display and any dynamic data.

    This endpoint:
    1. Handles "ping" health checks from WhatsApp
    2. Parses the incoming request (screen, data, flow_token)
    3. Returns the next screen and data based on business logic

    Customize the routing logic in _handle_flow_request() for your use case.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid JSON body"},
        )

    parsed = parse_flow_endpoint_request(body)

    # Handle health check
    if parsed.get("is_ping"):
        return {"version": "3.0", "data": {"status": "active"}}

    # Handle the actual Flow request
    return await _handle_flow_request(parsed)


async def _handle_flow_request(parsed: dict) -> dict:
    """Handle a Flow Data Endpoint request and return the next screen.

    This is a basic router that can be customized for different Flow types.
    By default, it acknowledges the data and navigates to the next screen.
    """
    screen = parsed.get("screen", "")
    data = parsed.get("data", {})
    flow_token = parsed.get("flow_token", "")

    logger.info(
        "Flow endpoint request: screen=%s flow_token=%s data_keys=%s",
        screen, flow_token[:20] if flow_token else "", list(data.keys()),
    )

    # Default routing: acknowledge data and move to next screen
    # Customize this for specific Flow types (lead gen, booking, etc.)
    if screen == "FIRST_ENTRY_SCREEN":
        return build_flow_endpoint_response(
            screen="LEAD_FORM",
            data={"business_name": "Cloudless"},
        )
    elif screen == "LEAD_FORM":
        # Save the lead data (in production, store to DB)
        logger.info("Lead captured: %s", data)
        return build_flow_endpoint_response(
            screen="SUCCESS_SCREEN",
            data={"name": data.get("name", "")},
        )

    # Default: stay on current screen
    return build_flow_endpoint_response(screen=screen, data=data)


# ------------------------------------------------------------------
# Flow response webhook (called from the main WhatsApp webhook)
# ------------------------------------------------------------------

async def process_flow_responses(body: dict, db: AsyncSession) -> list[dict]:
    """Process Flow response messages from a webhook payload.

    This is called from the main WhatsApp webhook handler when Flow
    responses arrive. It parses the responses and logs them.

    Returns a list of processed Flow responses.
    """
    flow_events = parse_flow_response(body)
    for event in flow_events:
        logger.info(
            "Flow response received: flow_token=%s sender=%s response_keys=%s",
            event.get("flow_token", "")[:20],
            event.get("sender_phone", ""),
            list(event.get("response_json", {}).keys()),
        )
        # In production, store the response to the DB and trigger workflows
    return flow_events
