"""API endpoints for the Cloudflare-first secret store.

Provides CRUD for service and account credentials, with local PostgreSQL
and .env failover.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.models.user import User
from app.services.secret_store import secret_store

router = APIRouter()


class SecretValue(BaseModel):
    value: str = Field(..., min_length=1)
    description: str | None = Field(default=None, max_length=500)


class SecretUpdate(BaseModel):
    value: str = Field(..., min_length=1)
    description: str | None = Field(default=None, max_length=500)


class SecretResponse(BaseModel):
    key: str
    value: str  # masked in list, raw in get
    sources: dict[str, bool] | None = None


class SecretListResponse(BaseModel):
    secrets: list[dict[str, Any]]


def _require_authenticated(current_user: User) -> None:
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )


@router.get("", response_model=SecretListResponse)
async def list_secrets(current_user: User = Depends(get_current_user)):
    """List all secret keys (values masked)."""
    _require_authenticated(current_user)
    keys = await secret_store.list_keys()
    return {"secrets": keys}


@router.get("/{key}")
async def get_secret(key: str, current_user: User = Depends(get_current_user)):
    """Get the raw value of a secret."""
    _require_authenticated(current_user)
    value = await secret_store.get(key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"Secret not found: {key}")
    return {"key": key, "value": value}


@router.post("/{key}", response_model=SecretResponse)
async def set_secret(
    key: str,
    data: SecretValue,
    current_user: User = Depends(get_current_user),
):
    """Set a secret value."""
    _require_authenticated(current_user)
    sources = await secret_store.set(key, data.value, data.description)
    return {"key": key, "value": "***", "sources": sources}


@router.put("/{key}", response_model=SecretResponse)
async def update_secret(
    key: str,
    data: SecretUpdate,
    current_user: User = Depends(get_current_user),
):
    """Update a secret value."""
    _require_authenticated(current_user)
    sources = await secret_store.set(key, data.value, data.description)
    return {"key": key, "value": "***", "sources": sources}


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(key: str, current_user: User = Depends(get_current_user)):
    """Delete a secret from all stores."""
    _require_authenticated(current_user)
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Secret deletion is not yet supported",
    )
