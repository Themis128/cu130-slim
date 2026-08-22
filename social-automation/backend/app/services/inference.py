"""Unified inference service — routes to Ollama (local) or any OpenAI-compatible cloud API."""
import json
import re
import uuid

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt_token
from app.models.ai_provider import AIProvider
from app.models.user import Team, TeamMember

settings = get_settings()

# Pre-configured provider catalog (UI uses this to show provider cards)
PROVIDER_CATALOG = [
    {
        "name": "ollama",
        "display_name": "Local (Ollama)",
        "base_url": "",  # filled from env at runtime
        "default_model": "llama3",
        "requires_key": False,
        "description": "Local GPU inference — no API key needed",
        "model_examples": ["llama3", "llama3.1", "mistral", "phi3", "gemma2"],
    },
    {
        "name": "nvidia",
        "display_name": "NVIDIA Build",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "default_model": "meta/llama-3.1-70b-instruct",
        "requires_key": True,
        "description": "NVIDIA's cloud inference — free tier 1000 req/month",
        "model_examples": [
            "meta/llama-3.1-70b-instruct",
            "meta/llama-3.1-405b-instruct",
            "nvidia/nemotron-4-340b-instruct",
            "mistralai/mixtral-8x22b-instruct-v0.1",
            "google/gemma-2-27b-it",
        ],
    },
    {
        "name": "huggingface",
        "display_name": "Hugging Face",
        "base_url": "https://api-inference.huggingface.co/v1",
        "default_model": "meta-llama/Llama-3.1-70B-Instruct",
        "requires_key": True,
        "description": "HuggingFace Serverless Inference API",
        "model_examples": [
            "meta-llama/Llama-3.1-70B-Instruct",
            "meta-llama/Meta-Llama-3-8B-Instruct",
            "mistralai/Mixtral-8x7B-Instruct-v0.1",
            "Qwen/Qwen2.5-72B-Instruct",
        ],
    },
    {
        "name": "openai",
        "display_name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "requires_key": True,
        "description": "OpenAI GPT-4o, o1",
        "model_examples": ["gpt-4o", "gpt-4o-mini", "o1-mini", "o1"],
    },
    {
        "name": "groq",
        "display_name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "requires_key": True,
        "description": "Ultra-fast inference — best Ollama drop-in for speed",
        "model_examples": [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768",
            "gemma2-9b-it",
        ],
    },
    {
        "name": "together",
        "display_name": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "default_model": "meta-llama/Llama-3.1-70B-Instruct-Turbo",
        "requires_key": True,
        "description": "Together AI — pay-per-token open model hosting",
        "model_examples": [
            "meta-llama/Llama-3.1-70B-Instruct-Turbo",
            "mistralai/Mixtral-8x22B-Instruct-v0.1",
            "Qwen/Qwen2.5-72B-Instruct-Turbo",
        ],
    },
]


async def _get_provider_config(
    provider_name: str,
    team_id: uuid.UUID | None,
    db: AsyncSession | None,
) -> tuple[str, str, str | None]:
    """Return (base_url, model, api_key) for the requested provider."""
    if provider_name == "ollama":
        return settings.OLLAMA_URL, settings.OLLAMA_DEFAULT_MODEL, None

    if db and team_id:
        result = await db.execute(
            select(AIProvider).where(
                AIProvider.team_id == team_id,
                AIProvider.name == provider_name,
                AIProvider.is_enabled.is_(True),
            )
        )
        record = result.scalar_one_or_none()
        if record:
            api_key = decrypt_token(record.api_key_enc) if record.api_key_enc else None
            return record.base_url, record.default_model, api_key

    # Fallback to catalog defaults (key must be set via UI first)
    catalog = next((c for c in PROVIDER_CATALOG if c["name"] == provider_name), None)
    if not catalog:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider_name}")
    return catalog["base_url"], catalog["default_model"], None


async def call_inference(
    prompt: str,
    provider_name: str = "ollama",
    db: AsyncSession | None = None,
    team_id: uuid.UUID | None = None,
    schema: dict | None = None,
    model_override: str | None = None,
    max_tokens: int | None = None,
) -> dict:
    """Call the requested inference provider and return parsed JSON or text dict."""
    if provider_name == "ollama":
        return await _call_ollama(prompt, schema=schema, model_override=model_override, max_tokens=max_tokens)

    base_url, model, api_key = await _get_provider_config(provider_name, team_id, db)
    if model_override:
        model = model_override
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail=f"No API key configured for provider '{provider_name}'. Add it in Settings → AI Providers.",
        )
    return await _call_openai_compat(prompt, base_url=base_url, model=model, api_key=api_key, schema=schema, max_tokens=max_tokens)


async def _call_ollama(prompt: str, schema: dict | None = None, model_override: str | None = None, max_tokens: int | None = None) -> dict:
    model = model_override or settings.OLLAMA_DEFAULT_MODEL
    payload: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": max_tokens or 4096},
    }
    if schema:
        # Ollama 0.3.4+ accepts the JSON schema directly as the format value
        # for constrained/structured generation
        payload["format"] = schema

    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(f"{settings.OLLAMA_URL}/api/generate", json=payload)
        if resp.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Ollama error: {resp.text}")

    response_text = resp.json().get("response", "")
    if schema:
        return _parse_json_response(response_text)
    return {"text": response_text}


async def _call_openai_compat(
    prompt: str,
    base_url: str,
    model: str,
    api_key: str,
    schema: dict | None = None,
    max_tokens: int | None = None,
) -> dict:
    system = "You are a helpful assistant. When asked to return JSON, output only valid JSON — no markdown, no explanation."
    user_msg = prompt
    if schema:
        user_msg += "\n\nIMPORTANT: Return ONLY valid JSON matching the requested structure. No markdown code blocks."

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user_msg}]
    payload: dict = {"model": model, "messages": messages}
    # Reasoning models (e.g. openai/gpt-oss-120b) reject temperature > 0
    if "gpt-oss" not in model and "o1" not in model:
        payload["temperature"] = 0.7
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if schema:
        payload["response_format"] = {"type": "json_object"}

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Provider error {resp.status_code}: {resp.text[:400]}")

    msg = resp.json()["choices"][0]["message"]
    # Reasoning models may return content=null and put the answer in reasoning_content
    content = msg.get("content") or msg.get("reasoning_content") or ""
    if schema:
        return _parse_json_response(content)
    return {"text": content}


def _parse_json_response(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    raise HTTPException(status_code=500, detail="Provider returned invalid JSON")


async def get_team_id_for_user(user_id: uuid.UUID, db: AsyncSession) -> uuid.UUID | None:
    result = await db.execute(select(Team).join(TeamMember).where(TeamMember.user_id == user_id))
    team = result.scalars().first()
    return team.id if team else None
