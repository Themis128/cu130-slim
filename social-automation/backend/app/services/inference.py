"""Unified inference service — routes to Ollama (local) or any OpenAI-compatible cloud API."""
import json
import re
import time
import uuid
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decrypt_token
from app.models.ai_provider import AIProvider
from app.models.user import Team, TeamMember
from app.services import usage_tracker
from app.services.cf_models import (
    CF_TEXT_FREE,
    CF_TXT2IMG_FREE,
    GROQ_API_BASE,
    GROQ_TEXT_FALLBACK,
    HF_API_BASE,
    HF_IMG2IMG_FALLBACK,
    HF_TEXT_FALLBACK,
    HF_TXT2IMG_FALLBACK,
    PIXAZO_TXT2IMG_URL,
    TOGETHER_API_BASE,
    TOGETHER_TXT2IMG_FALLBACK,
)

settings = get_settings()


def _ai_token() -> str:
    """Return the Workers AI token, preferring CLOUDFLARE_AI_API_TOKEN over CLOUDFLARE_API_TOKEN."""
    return (settings.CLOUDFLARE_AI_API_TOKEN or "").strip() or (settings.CLOUDFLARE_API_TOKEN or "").strip()

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
        "default_model": "nvidia/nemotron-3-nano-30b-a3b",
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
        "base_url": "https://router.huggingface.co/v1",
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
        "default_model": "qwen/qwen3.6-27b",
        "requires_key": True,
        "description": "Ultra-fast inference — best Ollama drop-in for speed",
        "model_examples": [
            "qwen/qwen3.6-27b",
            "groq/compound",
            "openai/gpt-oss-20b",
            "openai/gpt-oss-120b",
        ],
    },
    {
        "name": "together",
        "display_name": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "default_model": "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        "requires_key": True,
        "description": "Together AI — pay-per-token open model hosting",
        "model_examples": [
            "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
            "meta-llama/Llama-3.1-70B-Instruct-Turbo",
            "mistralai/Mixtral-8x22B-Instruct-v0.1",
            "Qwen/Qwen2.5-72B-Instruct-Turbo",
        ],
    },
    {
        "name": "gemini",
        "display_name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-2.5-flash",
        "requires_key": True,
        "description": "Google Gemini API — multimodal models with a recurring free tier",
        "model_examples": ["gemini-3.6-flash", "gemini-3-flash-preview"],
    },
    {
        "name": "openrouter",
        "display_name": "OpenRouter Free",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openrouter/free",
        "requires_key": True,
        "description": "OpenRouter — automatic routing across currently available free models",
        "model_examples": ["openrouter/free", "meta-llama/llama-3.2-3b-instruct:free"],
    },
    {
        "name": "sambanova",
        "display_name": "SambaNova Cloud",
        "base_url": "https://api.sambanova.ai/v1",
        "default_model": "gpt-oss-120b",
        "requires_key": True,
        "description": "SambaNova Cloud — fast OpenAI-compatible inference with a free tier",
        "model_examples": ["gpt-oss-120b", "Meta-Llama-3.3-70B-Instruct", "DeepSeek-V3.2"],
    },
    {
        "name": "mistral",
        "display_name": "Mistral AI",
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-small-latest",
        "requires_key": True,
        "description": "Mistral AI Free Mode — experimentation and prototyping inference",
        "model_examples": ["mistral-small-latest", "mistral-medium-latest", "codestral-latest"],
    },
    {
        "name": "cohere",
        "display_name": "Cohere",
        "base_url": "https://api.cohere.ai/compatibility/v1",
        "default_model": "command-r7b-12-2024",
        "requires_key": True,
        "description": "Cohere evaluation API — free limited chat, embeddings, and reranking",
        "model_examples": ["command-a-03-2025", "command-r-plus-08-2024", "command-r7b-12-2024"],
    },
    {
        "name": "nvidia-flux",
        "display_name": "NVIDIA FLUX.1-Kontext-dev",
        "base_url": "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-kontext-dev",
        "default_model": "flux.1-kontext-dev",
        "requires_key": True,
        "description": "NVIDIA hosted FLUX.1-Kontext-dev image-to-image editing (no local GPU needed)",
        "model_examples": ["flux.1-kontext-dev"],
    },
    {
        "name": "nvidia-flux-dev",
        "display_name": "NVIDIA FLUX.1-dev",
        "base_url": "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-dev",
        "default_model": "flux.1-dev",
        "requires_key": True,
        "description": "NVIDIA hosted FLUX.1-dev text-to-image generation (no local GPU needed)",
        "model_examples": ["flux.1-dev"],
    },
    {
        "name": "local-sd35",
        "display_name": "Local Stable Diffusion 3.5",
        "base_url": "",  # Will be set from environment variable
        "default_model": "stable-diffusion-3.5-large",
        "requires_key": False,
        "description": "Local NVIDIA NIM for Stable Diffusion 3.5 (requires local GPU)",
        "model_examples": ["stable-diffusion-3.5-large"],
    },
    {
        "name": "cloudflare",
        "display_name": "Cloudflare Workers AI",
        "base_url": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/",
        "default_model": "@cf/meta/llama-3.2-3b-instruct",
        "requires_key": True,
        "description": "Cloudflare Workers AI — LLMs (Llama/Qwen/GLM/GPT-OSS), Whisper/Nova STT, FLUX images. Full live catalog browsable in this panel.",
        "model_examples": [
            "@cf/meta/llama-3.2-1b-instruct",
            "@cf/meta/llama-3.2-3b-instruct",
            "@cf/openai/whisper",
            "@cf/openai/whisper-large-v3-turbo",
            "@cf/deepgram/nova-3",
            "@cf/meta/llama-3.1-8b-instruct-fp8",
            "@cf/black-forest-labs/flux-1-schnell",
            "@cf/runwayml/stable-diffusion-v1-5-img2img",
            "@cf/stabilityai/stable-diffusion-xl-base-1.0",
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

    if provider_name == "local-sd35":
        # Use environment variable for local NIM URL
        local_nim_url = getattr(settings, 'LOCAL_NIM_URL', 'http://host.docker.internal:8000/v1/infer')
        return local_nim_url, "stable-diffusion-3.5-large", None

    db_base_url: str | None = None
    db_model: str | None = None

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
            db_key = decrypt_token(record.api_key_enc) if record.api_key_enc else None
            # If the DB record has a key, use it. Otherwise fall through to the
            # env-var fallback below so providers enabled via the panel (without
            # a stored key) still work when the key is in .env.
            if db_key:
                return _resolve_base_url(provider_name, record.base_url), record.default_model, db_key
            db_base_url = record.base_url
            db_model = record.default_model

    # Fallback to catalog defaults with env var API keys
    catalog = next((c for c in PROVIDER_CATALOG if c["name"] == provider_name), None)
    if not catalog:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider_name}")

    # Get API key from environment variables
    env_key_map = {
        "groq": settings.GROQ_API_KEY,
        "together": settings.TOGETHER_API_KEY,
        "nvidia": settings.NVIDIA_API_KEY,
        "huggingface": settings.HUGGINGFACE_API_KEY,
        "gemini": settings.GEMINI_API_KEY,
        "openrouter": settings.OPENROUTER_API_KEY,
        "sambanova": settings.SAMBANOVA_API_KEY,
        "mistral": settings.MISTRAL_API_KEY,
        "cohere": settings.COHERE_API_KEY,
        "openai": settings.OPENAI_API_KEY,
        "nvidia-flux": settings.NVIDIA_API_KEY,
        "nvidia-flux-dev": settings.NVIDIA_API_KEY,
        "cloudflare": _ai_token(),
    }
    api_key = env_key_map.get(provider_name, "")
    api_key = api_key if api_key else None

    # Prefer DB-stored base_url/model (from the enabled provider record) over
    # catalog defaults, but use the env-var key when the DB has none.
    base_url = db_base_url if db_base_url else str(catalog["base_url"])
    model = db_model if db_model else str(catalog["default_model"])
    return _resolve_base_url(provider_name, base_url), model, api_key


def _resolve_base_url(provider_name: str, base_url: str) -> str:
    """Substitute Cloudflare account ID (or other placeholders) into a provider base URL."""
    if provider_name == "cloudflare" and "{account_id}" in base_url:
        return base_url.replace("{account_id}", settings.CLOUDFLARE_ACCOUNT_ID)
    return base_url


# Speech-to-text model identifiers available on Cloudflare Workers AI
STT_MODELS: dict[str, str] = {
    "whisper": "@cf/openai/whisper",
    "whisper-large-v3-turbo": "@cf/openai/whisper-large-v3-turbo",
    "whisper-tiny-en": "@cf/openai/whisper-tiny-en",
    "wav2vec2": "@cf/facebook/wav2vec2-base-960h",
    "speechbrain": "@cf/speechbrain/asr-cnn-transformer",
    "nova-3": "@cf/deepgram/nova-3",
    "flux": "@cf/deepgram/flux",
}


async def list_workers_ai_models() -> list[dict]:
    """Fetch the live Workers AI model catalog for the configured account.

    Returns a normalized ``[{id, task, description}]`` covering every model the
    account can run — LLMs, speech-to-text, text-to-speech, image generation,
    embeddings, etc. Uses the paginated ``/ai/models/search`` endpoint.
    """
    account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
    api_key = _ai_token()
    if not account_id or not api_key:
        raise HTTPException(
            status_code=400,
            detail="CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_AI_API_TOKEN (or CLOUDFLARE_API_TOKEN) must be configured to list Workers AI models.",
        )

    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/models/search"
    headers = {"Authorization": f"Bearer {api_key}"}
    models: list[dict] = []

    async with httpx.AsyncClient(timeout=60.0) as client:
        page = 1
        while True:
            resp = await client.get(url, headers=headers, params={"per_page": 100, "page": page})
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Cloudflare models listing error {resp.status_code}: {resp.text[:400]}",
                )
            payload = resp.json()
            for m in payload.get("result") or []:
                task = m.get("task")
                if isinstance(task, dict):
                    task = task.get("name")
                models.append(
                    {
                        "id": m.get("name"),
                        "task": task,
                        "description": (m.get("description") or "")[:200],
                    }
                )
            info = payload.get("result_info") or {}
            total_pages = info.get("total_pages") or 1
            if page >= int(total_pages):
                break
            page += 1

    return models


async def transcribe_workers_ai(
    audio_bytes: bytes,
    content_type: str,
    model: str = "@cf/openai/whisper",
    api_key: str | None = None,
) -> dict:
    """Transcribe raw audio with a Cloudflare Workers AI speech-to-text model.

    Workers AI's STT models expect the audio bytes posted directly to
    ``/accounts/{account_id}/ai/run/{model}`` with a binary content type,
    returning ``{"result": {"text": ...}}``.
    """
    account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
    api_key = (api_key or _ai_token() or "").strip()

    if not account_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "CLOUDFLARE_ACCOUNT_ID is not configured. Add it to the root "
                "`.env` (or Settings → AI Providers) and restart social-api."
            ),
        )
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="CLOUDFLARE_AI_API_TOKEN (or CLOUDFLARE_API_TOKEN) is not configured for Cloudflare Workers AI.",
        )

    model = _validate_workers_ai_model(model)

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/"
        f"{model}"
    )
    # Workers AI STT models validate the Content-Type header — generic values
    # like ``application/octet-stream`` make e.g. whisper-large-v3-turbo fail
    # with "Invalid input (8001)". Normalize to a real audio type.
    normalized_type = (
        content_type if content_type and content_type.startswith("audio/") else "audio/wav"
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": normalized_type,
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, headers=headers, content=audio_bytes)

    if resp.status_code != 200:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text[:400]
        raise HTTPException(
            status_code=502,
            detail=(
                f"Cloudflare Workers AI transcription error {resp.status_code}: "
                f"{detail}"
            ),
        )

    data = resp.json()
    # Handle both new direct format and legacy envelope format
    # New format: {"text": "...", "language": "..."}
    # Legacy format: {"success": true, "result": {"text": "..."}}
    if "text" in data:
        result = data
    else:
        result = data.get("result") or {}

    text = (result.get("text") or "").strip()
    return {
        "text": text,
        **{k: v for k, v in result.items() if k != "text"},
    }


async def _call_workers_ai_chat(
    prompt: str,
    model: str,
    api_key: str,
    schema: dict | None = None,
    max_tokens: int | None = None,
) -> dict:
    """Call a Cloudflare Workers AI text-generation model.

    Workers AI does NOT expose OpenAI's ``/chat/completions``. It uses
    ``POST /accounts/{account_id}/ai/run/{model}`` with ``{"messages": [...]}``
    and returns ``{"result": {"response": ...}}``.
    """
    account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
    api_key = (api_key or "").strip() or _ai_token()

    if not account_id:
        raise HTTPException(
            status_code=400,
            detail="CLOUDFLARE_ACCOUNT_ID is not configured. Add it to the root `.env` and restart social-api.",
        )
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="CLOUDFLARE_AI_API_TOKEN (or CLOUDFLARE_API_TOKEN) is not configured for Cloudflare Workers AI.",
        )

    model = _validate_workers_ai_model(model)

    system = "You are a helpful assistant. When asked to return JSON, output only valid JSON — no markdown, no explanation."
    user_msg = prompt
    if schema:
        user_msg += "\n\nIMPORTANT: Return ONLY valid JSON matching the requested structure. No markdown code blocks."

    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
    payload: dict = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ]
    }
    if schema and not max_tokens:
        # Structured outputs (carousels, content JSON) are token-hungry. The
        # Workers AI default completion cap (~256 tokens) truncates the JSON
        # mid-object, producing unparseable responses ("Provider returned
        # invalid JSON"). Request a generous ceiling unless the caller set one.
        payload["max_tokens"] = WORKERS_AI_DEFAULT_STRUCTURED_MAX_TOKENS
    elif max_tokens:
        payload["max_tokens"] = max_tokens
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Cloudflare Workers AI error {resp.status_code}: {resp.text[:400]}")

    data = resp.json()
    # Handle both new direct format and legacy envelope format
    # New format: {"response": "...", "usage": {...}}
    # Legacy format: {"success": true, "result": {"response": "..."}}
    if "response" in data:
        raw = data.get("response", "")
    else:
        raw = (data.get("result") or {}).get("response", "")

    if isinstance(raw, dict):
        # Structured-output models return the parsed JSON object directly
        # (e.g. {"content": ..., "hashtags": [...]}).
        return raw
    response_text = str(raw or "").strip()
    if schema:
        return _parse_json_response(response_text)
    return {"text": response_text}


# ---------------------------------------------------------------------------
# Workers AI Image Generation (SDXL / FLUX image models)
# ---------------------------------------------------------------------------

# Completion ceiling used for structured (schema) requests when the caller
# does not specify one — see _call_workers_ai_chat.
WORKERS_AI_DEFAULT_STRUCTURED_MAX_TOKENS = 4096

# Keywords that identify image-generation models on Workers AI.  Text models
# like ``@cf/meta/llama-3.1-8b-instruct`` do *not* match any of these, so the
# heuristic is safe for routing.
_WORKERS_AI_IMAGE_KEYWORDS = (
    "stabilityai",
    "stable-diffusion",
    "flux",
    "runwayml",
    "kohya",
    "deepai",
    "craiy",
)

# Model identifiers are ``@author/name[/subname]`` (e.g. ``@cf/meta/llama-3.2-3b-instruct``).
_WORKERS_AI_MODEL_RE = re.compile(r"^@[\w./-]+$")


def _validate_workers_ai_model(model: str) -> str:
    """Validate and normalize a Workers AI model identifier before using it in a URL."""
    if not isinstance(model, str) or not model:
        raise HTTPException(status_code=400, detail="Model identifier is required")
    model = model.strip()
    if not _WORKERS_AI_MODEL_RE.fullmatch(model):
        raise HTTPException(status_code=400, detail="Invalid Workers AI model identifier")
    if any(part in (".", "..") for part in model.split("/")):
        raise HTTPException(status_code=400, detail="Invalid Workers AI model identifier")
    # Percent-encode the model to ensure any unexpected characters cannot change the URL structure.
    return quote(model, safe="@/")


def _is_workers_ai_image_model(model: str) -> bool:
    """Heuristic: detect whether a Workers AI model identifier targets image generation."""
    normalized = (model or "").lower()
    return any(kw in normalized for kw in _WORKERS_AI_IMAGE_KEYWORDS)


async def _call_workers_ai_image(
    prompt: str,
    model: str,
    api_key: str | None = None,
    negative_prompt: str = "",
    width: int = 1024,
    height: int = 1024,
    steps: int = 20,
    cfg_scale: float = 3.5,
) -> dict:
    """Generate an image via a Cloudflare Workers AI image model (SDXL / FLUX).

    Workers AI image models expect a ``{"prompt": ...}`` payload — *not* the
    ``{"messages": [...]}`` used by text models.  Depending on the model, the
    response body is either raw binary image bytes (SDXL returns
    ``image/png``) or a JSON envelope with base64 data under ``result.image``
    (FLUX).  Both shapes are handled here.

    FLUX models only accept a narrow schema (``prompt`` + optional
    ``steps``). SDXL-family models accept width/height/guidance/num_steps.
    """
    account_id, key = _workers_ai_credentials(api_key)
    model = _validate_workers_ai_model(model)
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
    normalized = (model or "").lower()
    is_flux = "flux" in normalized and "deepgram" not in normalized

    if is_flux:
        # @cf/black-forest-labs/flux-* reject width/height/guidance/num_steps;
        # optional field is ``steps`` (max 8).
        payload: dict = {
            "prompt": prompt,
            "steps": max(1, min(steps, 8)),
        }
    else:
        payload = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "num_steps": steps,
            "guidance": cfg_scale,
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Cloudflare Workers AI image error {resp.status_code}: {resp.text[:400]}",
            )

        import base64

        content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type.startswith("image/"):
            # Raw binary image bytes in the response body (e.g. SDXL → image/png).
            return {
                "image_base64": base64.b64encode(resp.content).decode("utf-8"),
                "format": "base64",
                "prompt": prompt,
            }

        # Handle both new direct format and legacy envelope format
        # New format: {"image": "<base64>"}
        # Legacy format: {"success": true, "result": {"image": "<base64>"}}
        data = resp.json()
        if "image" in data or "base64" in data:
            result = data
        else:
            result = data.get("result") or {}

        image_b64 = result.get("image") or result.get("base64") or ""
        if not image_b64:
            raise HTTPException(
                status_code=502,
                detail="Cloudflare Workers AI image model returned no image data.",
            )
        return {
            "image_base64": image_b64,
            "format": "base64",
            "prompt": prompt,
        }


async def _call_workers_ai_img2img(
    prompt: str,
    image_bytes: bytes,
    model: str = "@cf/runwayml/stable-diffusion-v1-5-img2img",
    api_key: str | None = None,
    negative_prompt: str = "blurry, low quality, watermark, text, logo, letters",
    strength: float = 0.45,
    steps: int = 15,
    cfg_scale: float = 7.5,
    width: int = 512,
    height: int = 512,
    max_retries: int = 4,
    allow_fallback: bool = True,
) -> dict:
    """Enhance / transform an image via Cloudflare Workers AI img2img.

    ``@cf/runwayml/stable-diffusion-v1-5-img2img`` accepts ``image_b64`` + ``prompt``
    + optional ``strength`` / ``num_steps`` / ``guidance``.
    """
    import asyncio
    import base64
    import io

    from PIL import Image

    account_id, key = _workers_ai_credentials(api_key)
    model = _validate_workers_ai_model(model)
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"

    # SD1.5 img2img works best around 512² — downscale for the model, caller can upscale after.
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = img.resize((width, height), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    image_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    payload = {
        "prompt": prompt,
        "image_b64": image_b64,
        "strength": max(0.05, min(float(strength), 1.0)),
        "num_steps": max(1, min(int(steps), 20)),
        "guidance": cfg_scale,
        "width": width,
        "height": height,
    }
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last_error = ""
    async with httpx.AsyncClient(timeout=300.0) as client:
        for attempt in range(max_retries):
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 429:
                last_error = resp.text[:300]
                await asyncio.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Cloudflare Workers AI img2img error {resp.status_code}: {resp.text[:400]}",
                )

            content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type.startswith("image/"):
                return {
                    "image_base64": base64.b64encode(resp.content).decode("utf-8"),
                    "format": "base64",
                    "prompt": prompt,
                    "model": model,
                }

            data = resp.json()
            result = data if ("image" in data or "base64" in data) else (data.get("result") or {})
            out_b64 = result.get("image") or result.get("base64") or ""
            if not out_b64:
                raise HTTPException(
                    status_code=502,
                    detail="Cloudflare Workers AI img2img returned no image data.",
                )
            return {
                "image_base64": out_b64,
                "format": "base64",
                "prompt": prompt,
                "model": model,
            }

    # CF img2img exhausted all retries — try HF failover only when allowed
    if allow_fallback:
        hf_key = (settings.HUGGINGFACE_API_KEY or "").strip()
        if hf_key:
            print(f"[inference] CF img2img quota/retries exhausted — falling back to HF {HF_IMG2IMG_FALLBACK}", flush=True)
            try:
                return await _call_hf_img2img(
                    prompt=prompt,
                    image_b64=image_b64,
                    model=HF_IMG2IMG_FALLBACK,
                    api_key=hf_key,
                    negative_prompt=negative_prompt,
                    strength=strength,
                    steps=min(steps, 20),
                    width=width,
                    height=height,
                )
            except HTTPException:
                pass  # HF also failed — fall through to original error

    raise HTTPException(
        status_code=502,
        detail=f"Cloudflare Workers AI img2img capacity exceeded after retries: {last_error}",
    )


async def _call_workers_ai_flux2_edit(
    prompt: str,
    image_bytes: bytes,
    model: str = "@cf/black-forest-labs/flux-2-klein-4b",
    api_key: str | None = None,
    width: int = 1024,
    height: int = 1024,
    max_retries: int = 5,
) -> dict:
    """Image edit / enhance via FLUX.2 klein multipart reference input.

    Reference images must be ≤512×512. Output can be up to 1024×1024.
    """
    import asyncio
    import base64
    import io

    from PIL import Image

    account_id, key = _workers_ai_credentials(api_key)
    model = _validate_workers_ai_model(model)
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((512, 512), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    ref_bytes = buf.getvalue()

    headers = {"Authorization": f"Bearer {key}"}
    last_error = ""
    async with httpx.AsyncClient(timeout=300.0) as client:
        for attempt in range(max_retries):
            files: dict = {
                "prompt": (None, prompt),
                "width": (None, str(width)),
                "height": (None, str(height)),
                "input_image_0": ("ref.png", ref_bytes, "image/png"),
            }
            resp = await client.post(url, headers=headers, files=files)  # type: ignore[arg-type]
            if resp.status_code == 429:
                last_error = resp.text[:300]
                await asyncio.sleep(min(30, 3 * (2 ** attempt)))
                continue
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Cloudflare FLUX.2 edit error {resp.status_code}: {resp.text[:400]}",
                )

            content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type.startswith("image/"):
                return {
                    "image_base64": base64.b64encode(resp.content).decode("utf-8"),
                    "format": "base64",
                    "prompt": prompt,
                    "model": model,
                }

            data = resp.json()
            result = data if ("image" in data or "base64" in data) else (data.get("result") or {})
            out_b64 = result.get("image") or result.get("base64") or ""
            if not out_b64:
                raise HTTPException(
                    status_code=502,
                    detail="Cloudflare FLUX.2 edit returned no image data.",
                )
            return {
                "image_base64": out_b64,
                "format": "base64",
                "prompt": prompt,
                "model": model,
            }

    raise HTTPException(
        status_code=502,
        detail=f"Cloudflare FLUX.2 edit capacity exceeded after retries: {last_error}",
    )


async def _call_cf_image_pipeline(
    prompt: str,
    *,
    enhance_prompt: str | None = None,
    txt2img_model: str = CF_TXT2IMG_FREE,
    img2img_model: str | None = None,
    api_key: str | None = None,
    strength: float = 0.45,
    txt2img_steps: int = 4,
    img2img_steps: int = 8,
    allow_fallback: bool = True,
) -> dict:
    """Cloudflare-only pipeline: text-to-image draft, optionally enhanced via img2img.

    When img2img_model is None (default), the enhance step is skipped entirely.
    SD img2img is blocked on the current CF plan so callers should omit it.
    """
    import base64
    import io

    from PIL import Image

    enhance = enhance_prompt or (
        f"{prompt}. Improve image quality, sharper details, richer lighting, "
        "professional LinkedIn carousel background, no readable text, no logos"
    )

    pixazo_key = (settings.PIXAZO_API_KEY or "").strip()
    together_key = (settings.TOGETHER_API_KEY or "").strip()
    hf_key = (settings.HUGGINGFACE_API_KEY or "").strip()
    try:
        draft = await _call_workers_ai_image(
            prompt=prompt,
            model=txt2img_model,
            api_key=api_key,
            steps=txt2img_steps,
        )
    except HTTPException as exc:
        if not _is_cf_quota_error(exc):
            raise
        if not allow_fallback:
            raise
        draft = None
        if pixazo_key:
            print("[cf-pipeline] CF txt2img quota — falling back to Pixazo FLUX Schnell", flush=True)
            try:
                draft = await _call_pixazo_txt2img(prompt=prompt, api_key=pixazo_key, width=1024, height=1024)
            except HTTPException:
                print("[cf-pipeline] Pixazo also failed", flush=True)
        if draft is None and together_key:
            print(f"[cf-pipeline] Falling back to Together {TOGETHER_TXT2IMG_FALLBACK}", flush=True)
            try:
                draft = await _call_together_txt2img(
                    prompt=prompt, model=TOGETHER_TXT2IMG_FALLBACK, api_key=together_key,
                    width=1024, height=1024, steps=txt2img_steps,
                )
            except HTTPException:
                print("[cf-pipeline] Together txt2img also failed", flush=True)
        if draft is None and hf_key:
            print(f"[cf-pipeline] Falling back to HF {HF_TXT2IMG_FALLBACK}", flush=True)
            draft = await _call_hf_txt2img(
                prompt=prompt, model=HF_TXT2IMG_FALLBACK, api_key=hf_key,
                width=1024, height=1024, steps=txt2img_steps,
            )
        if draft is None:
            raise
    assert draft is not None
    draft_bytes = base64.b64decode(draft["image_base64"])

    if img2img_model:
        enhance_model_used = img2img_model
        try:
            if "flux-2" in img2img_model.lower():
                enhanced = await _call_workers_ai_flux2_edit(
                    prompt=enhance,
                    image_bytes=draft_bytes,
                    model=img2img_model,
                    api_key=api_key,
                )
            else:
                enhanced = await _call_workers_ai_img2img(
                    prompt=enhance,
                    image_bytes=draft_bytes,
                    model=img2img_model,
                    api_key=api_key,
                    strength=strength,
                    steps=img2img_steps,
                    max_retries=5,
                    allow_fallback=allow_fallback,
                )
        except HTTPException as first_exc:
            print(f"[cf-pipeline] enhance failed, using draft: {first_exc.detail}", flush=True)
            enhance_model_used = "draft-only"
            enhanced = draft
    else:
        enhance_model_used = "draft-only"
        enhanced = draft

    assert enhanced is not None
    enhanced_bytes = base64.b64decode(enhanced["image_base64"])

    # Normalize to 1024 for slide composition.
    out = Image.open(io.BytesIO(enhanced_bytes)).convert("RGB")
    if out.size != (1024, 1024):
        out = out.resize((1024, 1024), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        final_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    else:
        final_b64 = enhanced["image_base64"]

    return {
        "image_base64": final_b64,
        "draft_base64": draft["image_base64"],
        "prompt": prompt,
        "enhance_prompt": enhance,
        "models": {"txt2img": txt2img_model, "img2img": enhance_model_used},
    }


# ---------------------------------------------------------------------------
# Workers AI Batch Inference (queueRequest=true)
# ---------------------------------------------------------------------------

def _workers_ai_credentials(api_key: str | None = None) -> tuple[str, str]:
    """Return ``(account_id, api_key)`` for Workers AI, raising 400 if unset."""
    account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
    key = (api_key or "").strip() or _ai_token()
    if not account_id:
        raise HTTPException(
            status_code=400,
            detail="CLOUDFLARE_ACCOUNT_ID is not configured. Add it to the root `.env` and restart social-api.",
        )
    if not key:
        raise HTTPException(
            status_code=400,
            detail="CLOUDFLARE_AI_API_TOKEN (or CLOUDFLARE_API_TOKEN) is not configured for Cloudflare Workers AI.",
        )
    return account_id, key


async def submit_workers_ai_batch(
    model: str,
    requests: list[dict],
    api_key: str | None = None,
) -> dict:
    """Submit a batch of inference requests to a Workers AI model queue.

    POSTs ``{"requests": [...]}`` to
    ``/accounts/{account_id}/ai/run/{model}?queueRequest=true``. Each item is a
    model-specific payload and may carry an optional ``external_reference``
    echoed back in the batch response. Returns
    ``{"request_id", "status", "model"}`` on successful queueing.
    """
    if not requests:
        raise HTTPException(status_code=400, detail="Batch request must contain at least one item.")
    account_id, key = _workers_ai_credentials(api_key)
    model = _validate_workers_ai_model(model)

    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}?queueRequest=true"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, headers=headers, json={"requests": requests})

    if resp.status_code not in (200, 202):
        raise HTTPException(
            status_code=502,
            detail=f"Cloudflare Workers AI batch submission error {resp.status_code}: {resp.text[:400]}",
        )

    data = resp.json()
    # Handle both new direct format and legacy envelope format
    # Batch API typically uses envelope format, but we should be defensive
    if "success" in data and not data.get("success", True):
        raise HTTPException(status_code=502, detail=f"Cloudflare Workers AI batch submission failed: {data.get('errors')}")

    # Check for direct format first (request_id, status, model at top level)
    if "request_id" in data or "status" in data:
        result = data
    else:
        result = data.get("result") or {}

    return {
        "request_id": result.get("request_id"),
        "status": result.get("status") or "queued",
        "model": result.get("model") or model,
    }


async def retrieve_workers_ai_batch(
    model: str,
    request_id: str,
    api_key: str | None = None,
) -> dict:
    """Poll/retrieve the results of a previously submitted batch request.

    POSTs ``{"request_id": ...}`` to the same ``?queueRequest=true`` endpoint.
    Returns ``{"status", "responses": [...], "usage": {...}, "model"}``. While
    still processing, ``responses`` is empty/absent — poll again later.
    """
    account_id, key = _workers_ai_credentials(api_key)
    model = _validate_workers_ai_model(model)

    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}?queueRequest=true"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, headers=headers, json={"request_id": request_id})

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Cloudflare Workers AI batch retrieval error {resp.status_code}: {resp.text[:400]}",
        )

    data = resp.json()
    # Handle both new direct format and legacy envelope format
    # Batch API typically uses envelope format, but we should be defensive
    if "success" in data and not data.get("success", True):
        raise HTTPException(status_code=502, detail=f"Cloudflare Workers AI batch retrieval failed: {data.get('errors')}")

    # Check for direct format first (status, responses, usage at top level)
    if "status" in data or "responses" in data:
        result = data
    else:
        result = data.get("result") or {}

    return {
        "status": result.get("status"),
        "responses": result.get("responses") or [],
        "usage": result.get("usage"),
        "model": result.get("model") or model,
    }


# ---------------------------------------------------------------------------
# Cloudflare quota / rate-limit detection
# ---------------------------------------------------------------------------

def _is_cf_quota_error(exc: HTTPException) -> bool:
    """Return True when the HTTPException signals a CF neuron-budget or rate-limit."""
    if exc.status_code == 429:
        return True
    if exc.status_code == 503:
        return True
    if exc.status_code in (403, 502):
        lower = str(exc.detail or "").lower()
        return any(kw in lower for kw in ("neuron", "quota", "budget", "limit", "rate", "exceed", "capacity"))
    return False


# ---------------------------------------------------------------------------
# Hugging Face Inference API — free-tier callables
# ---------------------------------------------------------------------------

async def _call_hf_chat(
    prompt: str,
    model: str,
    api_key: str,
    schema: dict | None = None,
    max_tokens: int | None = None,
) -> dict:
    """HF Serverless Inference — per-model OpenAI-compat chat completions."""
    system = "You are a helpful assistant. When asked to return JSON, output only valid JSON — no markdown, no explanation."
    user_msg = prompt
    if schema:
        user_msg += "\n\nIMPORTANT: Return ONLY valid JSON matching the requested structure. No markdown code blocks."

    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.7,
        "max_tokens": max_tokens or (4096 if schema else 1024),
    }
    if schema:
        payload["response_format"] = {"type": "json_object"}

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"{HF_API_BASE}/v1/chat/completions"

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"HF text fallback error {resp.status_code}: {resp.text[:300]}",
            )

    content = resp.json()["choices"][0]["message"].get("content", "")
    if schema:
        return _parse_json_response(content)
    return {"text": content}


async def _call_hf_txt2img(
    prompt: str,
    model: str,
    api_key: str,
    negative_prompt: str = "",
    width: int = 1024,
    height: int = 1024,
    steps: int = 4,
) -> dict:
    """HF Serverless Inference — text-to-image (raw binary PNG response)."""
    import base64

    is_flux = "flux" in model.lower()
    parameters: dict = {
        "num_inference_steps": max(1, min(steps, 50)),
        "width": width,
        "height": height,
    }
    if negative_prompt and not is_flux:
        parameters["negative_prompt"] = negative_prompt
    if not is_flux:
        parameters["guidance_scale"] = 7.5

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(
            f"{HF_API_BASE}/hf-inference/models/{model}",
            headers=headers,
            json={"inputs": prompt, "parameters": parameters},
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"HF txt2img fallback error {resp.status_code}: {resp.text[:300]}",
            )

    return {
        "image_base64": base64.b64encode(resp.content).decode("utf-8"),
        "format": "base64",
        "prompt": prompt,
        "provider": "huggingface",
        "model": model,
    }


async def _call_hf_img2img(
    prompt: str,
    image_b64: str,
    model: str,
    api_key: str,
    negative_prompt: str = "",
    strength: float = 0.6,
    steps: int = 20,
    width: int = 512,
    height: int = 512,
) -> dict:
    """HF Serverless Inference — image-to-image (instruct-pix2pix style)."""
    import base64

    parameters: dict = {
        "prompt": prompt,
        "strength": max(0.1, min(float(strength), 1.0)),
        "num_inference_steps": max(1, min(steps, 50)),
        "target_size": {"width": width, "height": height},
    }
    if negative_prompt:
        parameters["negative_prompt"] = negative_prompt

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(
            f"{HF_API_BASE}/hf-inference/models/{model}",
            headers=headers,
            json={"inputs": image_b64, "parameters": parameters},
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"HF img2img fallback error {resp.status_code}: {resp.text[:300]}",
            )

    return {
        "image_base64": base64.b64encode(resp.content).decode("utf-8"),
        "format": "base64",
        "prompt": prompt,
        "provider": "huggingface",
        "model": model,
    }


# ---------------------------------------------------------------------------
# Pixazo — free FLUX Schnell txt2img (60 RPM, no daily cap)
# ---------------------------------------------------------------------------

async def _call_pixazo_txt2img(
    prompt: str,
    api_key: str,
    width: int = 1024,
    height: int = 1024,
) -> dict:
    """Pixazo free FLUX Schnell — returns image URL, we download and base64-encode it."""
    import base64

    headers = {
        "Content-Type": "application/json",
        "Ocp-Apim-Subscription-Key": api_key,
    }
    payload: dict = {"prompt": prompt}
    if width != 1024 or height != 1024:
        payload["width"] = width
        payload["height"] = height

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(PIXAZO_TXT2IMG_URL, headers=headers, json=payload)
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Pixazo txt2img error {resp.status_code}: {resp.text[:300]}",
            )
        data = resp.json()
        image_url = data.get("output") or data.get("url") or data.get("image_url") or ""
        if not image_url:
            raise HTTPException(status_code=502, detail="Pixazo returned no image URL")

        img_resp = await client.get(image_url, timeout=60.0)
        if img_resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Pixazo image download failed: {img_resp.status_code}",
            )

    return {
        "image_base64": base64.b64encode(img_resp.content).decode("utf-8"),
        "format": "base64",
        "prompt": prompt,
        "provider": "pixazo",
        "model": "flux-1-schnell",
    }


# ---------------------------------------------------------------------------
# Groq — free-tier text fallback (14.4K req/day, 30 RPM)
# ---------------------------------------------------------------------------

async def _call_groq_chat(
    prompt: str,
    model: str,
    api_key: str,
    schema: dict | None = None,
    max_tokens: int | None = None,
) -> dict:
    """Groq — OpenAI-compatible chat completions."""
    system = "You are a helpful assistant. When asked to return JSON, output only valid JSON — no markdown, no explanation."
    user_msg = prompt
    if schema:
        user_msg += "\n\nIMPORTANT: Return ONLY valid JSON matching the requested structure. No markdown code blocks."

    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.7,
        "max_tokens": max_tokens or (4096 if schema else 1024),
    }
    if schema:
        payload["response_format"] = {"type": "json_object"}

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"{GROQ_API_BASE}/chat/completions"

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Groq text fallback error {resp.status_code}: {resp.text[:300]}",
            )

    content = resp.json()["choices"][0]["message"].get("content", "")
    if schema:
        return _parse_json_response(content)
    return {"text": content}


# ---------------------------------------------------------------------------
# Together AI — free FLUX.1-schnell txt2img fallback
# ---------------------------------------------------------------------------

async def _call_together_txt2img(
    prompt: str,
    model: str,
    api_key: str,
    width: int = 1024,
    height: int = 1024,
    steps: int = 4,
) -> dict:
    """Together AI — text-to-image via their images/generations endpoint."""

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "prompt": prompt,
        "width": width,
        "height": height,
        "steps": max(1, min(steps, 8)),
        "n": 1,
        "response_format": "b64_json",
    }

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(
            f"{TOGETHER_API_BASE}/images/generations",
            headers=headers,
            json=payload,
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Together txt2img fallback error {resp.status_code}: {resp.text[:300]}",
            )

    data = resp.json()
    img_b64 = data["data"][0]["b64_json"]
    return {
        "image_base64": img_b64,
        "format": "base64",
        "prompt": prompt,
        "provider": "together",
        "model": model,
    }


async def _do_call_inference(
    prompt: str,
    provider_name: str = "cloudflare",
    db: AsyncSession | None = None,
    team_id: uuid.UUID | None = None,
    schema: dict | None = None,
    model_override: str | None = None,
    max_tokens: int | None = None,
    allow_fallback: bool = True,
) -> dict:
    """Call the requested inference provider and return parsed JSON or text dict."""
    if provider_name == "ollama":
        return await _call_ollama(prompt, schema=schema, model_override=model_override, max_tokens=max_tokens)

    base_url, model, api_key = await _get_provider_config(provider_name, team_id, db)
    if model_override:
        model = model_override
    # Cloudflare: auto-select the correct model per task type so callers never
    # have to specify one.  Text/structured tasks → CF_TEXT_FREE; if the
    # configured default_model happens to be an image model, silently correct it.
    if provider_name == "cloudflare" and not model_override:
        if schema or not _is_workers_ai_image_model(model):
            if _is_workers_ai_image_model(model):
                model = CF_TEXT_FREE  # image model misconfigured as default — fix it
            elif not model:
                model = CF_TEXT_FREE
        # image tasks with no schema keep whatever model was resolved (caller controls)
    # Cloudflare credentials (CLOUDFLARE_AI_API_TOKEN / CLOUDFLARE_API_TOKEN /
    # CLOUDFLARE_ACCOUNT_ID) are environment-level, not stored per-team.
    # _call_workers_ai_chat already falls back to the env var and validates it,
    # so we must not reject a missing per-team key here for Cloudflare (same
    # pattern as local-sd35).
    if not api_key and provider_name not in ("local-sd35", "cloudflare"):
        raise HTTPException(
            status_code=400,
            detail=f"No API key configured for provider '{provider_name}'. Add it in Settings → AI Providers.",
        )
    if provider_name == "cloudflare":
        if _is_workers_ai_image_model(model):
            if schema:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"The model '{model}' is a Workers AI image-generation model and "
                        f"cannot produce structured text/JSON output. Select a text-generation "
                        f"model such as '@cf/meta/llama-3.1-8b-instruct' for content tasks."
                    ),
                )
            try:
                return await _call_workers_ai_image(prompt, model=model, api_key=api_key)
            except HTTPException as exc:
                if not _is_cf_quota_error(exc):
                    raise
                if not allow_fallback:
                    raise
                # CF txt2img exhausted → Pixazo → Together → HF
                pixazo_key = (settings.PIXAZO_API_KEY or "").strip()
                if pixazo_key:
                    print("[inference] CF txt2img quota — falling back to Pixazo FLUX Schnell", flush=True)
                    try:
                        return await _call_pixazo_txt2img(prompt, api_key=pixazo_key, width=1024, height=1024)
                    except HTTPException:
                        print("[inference] Pixazo also failed — trying Together", flush=True)
                together_key = (settings.TOGETHER_API_KEY or "").strip()
                if together_key:
                    print(f"[inference] Falling back to Together {TOGETHER_TXT2IMG_FALLBACK}", flush=True)
                    try:
                        return await _call_together_txt2img(
                            prompt, model=TOGETHER_TXT2IMG_FALLBACK, api_key=together_key,
                            width=1024, height=1024, steps=4,
                        )
                    except HTTPException:
                        print("[inference] Together txt2img also failed — trying HF", flush=True)
                hf_key = (settings.HUGGINGFACE_API_KEY or "").strip()
                if hf_key:
                    print(f"[inference] Falling back to HF {HF_TXT2IMG_FALLBACK}", flush=True)
                    return await _call_hf_txt2img(
                        prompt, model=HF_TXT2IMG_FALLBACK, api_key=hf_key,
                        width=1024, height=1024, steps=4,
                    )
                raise
        try:
            return await _call_workers_ai_chat(
                prompt, model=model, api_key=api_key or "", schema=schema, max_tokens=max_tokens
            )
        except HTTPException as exc:
            if not _is_cf_quota_error(exc):
                raise
            if not allow_fallback:
                raise
            # CF text exhausted → Groq → HF
            groq_key = (settings.GROQ_API_KEY or "").strip()
            if groq_key:
                print(f"[inference] CF text quota — falling back to Groq {GROQ_TEXT_FALLBACK}", flush=True)
                try:
                    return await _call_groq_chat(
                        prompt, model=GROQ_TEXT_FALLBACK, api_key=groq_key,
                        schema=schema, max_tokens=max_tokens,
                    )
                except HTTPException as groq_exc:
                    print(f"[inference] Groq failed: {getattr(groq_exc, 'detail', groq_exc)!s:.200s} — trying HF", flush=True)
            hf_key = (settings.HUGGINGFACE_API_KEY or "").strip()
            if hf_key:
                print(f"[inference] Falling back to HF {HF_TEXT_FALLBACK}", flush=True)
                return await _call_hf_chat(
                    prompt, model=HF_TEXT_FALLBACK, api_key=hf_key,
                    schema=schema, max_tokens=max_tokens,
                )
            raise
    return await _call_openai_compat(
        prompt, base_url=base_url, model=model, api_key=api_key or "", schema=schema, max_tokens=max_tokens
    )


async def _text_provider_chain(
    provider_name: str,
    team_id: uuid.UUID | None,
    db: AsyncSession | None,
) -> list[str]:
    priority = ["cloudflare", "groq", "gemini", "mistral", "cohere", "openrouter", "nvidia"]
    credentials = {
        "cloudflare": bool(_ai_token() and settings.CLOUDFLARE_ACCOUNT_ID),
        "groq": bool(settings.GROQ_API_KEY),
        "gemini": bool(settings.GEMINI_API_KEY),
        "mistral": bool(settings.MISTRAL_API_KEY),
        "cohere": bool(settings.COHERE_API_KEY),
        "openrouter": bool(settings.OPENROUTER_API_KEY),
        "nvidia": bool(settings.NVIDIA_API_KEY),
    }
    enabled = set(priority)
    if db and team_id:
        result = await db.execute(
            select(AIProvider.name).where(
                AIProvider.team_id == team_id,
                AIProvider.is_enabled.is_(True),
            )
        )
        enabled = set(result.scalars().all())

    cloud = [name for name in priority if credentials[name] and name in enabled]
    if provider_name != "ollama" and provider_name not in cloud:
        cloud.insert(0, provider_name)
    elif provider_name in cloud:
        cloud.remove(provider_name)
        cloud.insert(0, provider_name)
    return [*cloud, "ollama"]


async def call_inference(
    prompt: str,
    provider_name: str = "cloudflare",
    db: AsyncSession | None = None,
    team_id: uuid.UUID | None = None,
    schema: dict | None = None,
    model_override: str | None = None,
    max_tokens: int | None = None,
    allow_fallback: bool = True,
    *,
    endpoint: str = "",
) -> dict:
    """Public inference entry point with usage tracking.

    ``_do_call_inference`` performs the actual request. This wrapper records the
    provider, model, latency and success/failure to ``AIUsageLog``.
    """
    # Resolve the intended model for logging (best effort; _do_call_inference may
    # override it for provider-specific reasons).
    tracked_model = model_override
    if not tracked_model and db is not None:
        try:
            _, configured_model, _ = await _get_provider_config(provider_name, team_id, db)
            tracked_model = configured_model
        except Exception:
            tracked_model = None

    if not tracked_model and provider_name == "cloudflare":
        tracked_model = CF_TXT2IMG_FREE if (schema is None and _is_workers_ai_image_model(model_override or CF_TEXT_FREE)) else CF_TEXT_FREE
    if not tracked_model:
        tracked_model = model_override or provider_name

    start = time.perf_counter()
    used_provider = provider_name
    is_image_request = provider_name in {
        "nvidia-flux",
        "nvidia-flux-dev",
        "local-sd35",
        "nvidia-flux-pipeline",
    } or (provider_name == "cloudflare" and _is_workers_ai_image_model(model_override or ""))
    attempt_providers = [provider_name]
    if allow_fallback and not is_image_request:
        attempt_providers = await _text_provider_chain(provider_name, team_id, db)

    try:
        last_error: HTTPException | None = None
        for candidate in attempt_providers:
            try:
                result = await _do_call_inference(
                    prompt,
                    provider_name=candidate,
                    db=db,
                    team_id=team_id,
                    schema=schema,
                    model_override=model_override if candidate == provider_name else None,
                    max_tokens=max_tokens,
                    allow_fallback=False,
                )
                used_provider = candidate
                break
            except HTTPException as exc:
                last_error = exc
        else:
            if last_error:
                raise last_error
            raise HTTPException(status_code=502, detail="No inference provider is available")

        latency_ms = int((time.perf_counter() - start) * 1000)
        await usage_tracker.track_inference(
            provider=used_provider,
            model=tracked_model,
            prompt=prompt,
            team_id=team_id,
            endpoint=endpoint or None,
            latency_ms=latency_ms,
            success=True,
            meta_data={"schema": bool(schema)},
        )
        return result
    except HTTPException as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        await usage_tracker.track_inference(
            provider=provider_name,
            model=tracked_model,
            prompt=prompt,
            team_id=team_id,
            endpoint=endpoint or None,
            latency_ms=latency_ms,
            success=False,
            error=f"HTTP {exc.status_code}: {exc.detail}"[:500],
        )
        raise
    except Exception as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        await usage_tracker.track_inference(
            provider=provider_name,
            model=tracked_model,
            prompt=prompt,
            team_id=team_id,
            endpoint=endpoint or None,
            latency_ms=latency_ms,
            success=False,
            error=str(exc)[:500],
        )
        raise


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


def _extract_json_object(text: str) -> str | None:
    """Return the first *balanced* ``{...}`` object in ``text`` (string-aware)."""
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    return text[start : i + 1]
    return None


def _parse_json_response(text: str) -> dict:
    """Parse a provider response into a dict, tolerating markdown fences,
    surrounding prose, or trailing commentary around the JSON object."""
    candidates: list[str] = []
    stripped = (text or "").strip()
    if stripped:
        candidates.append(stripped)
        # Markdown-fenced block: ```json ... ``` / ``` ... ```
        fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL)
        if fence:
            candidates.insert(0, fence.group(1).strip())
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    # Balanced-brace extraction handles prose before/after the object and
    # nested braces better than a greedy regex.
    extracted = _extract_json_object(stripped)
    if extracted:
        try:
            return json.loads(extracted)
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    raise HTTPException(
        status_code=500,
        detail=(
            "Provider returned invalid JSON — the model likely hit its output "
            "token limit or ignored the format instruction. Try again, pick a "
            "smaller slide count, or switch to a stronger text model."
        ),
    )


async def _call_nvidia_flux(
    prompt: str,
    base_url: str,
    api_key: str,
    image: str = "",  # Required for image-to-image (base64 data URI or example_id)
    cfg_scale: float = 3.5,
    seed: int = 0,
    steps: int = 30,
) -> bytes:
    """Call NVIDIA's FLUX.1-Kontext-dev API for image-to-image editing.
    Returns binary image data (PNG).
    """
    payload = {
        "prompt": prompt,
        "cfg_scale": cfg_scale,
        "seed": seed,
        "steps": steps,
        "aspect_ratio": "match_input_image",
    }
    if image:
        payload["image"] = image

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(base_url, headers=headers, json=payload)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"NVIDIA FLUX API error {resp.status_code}: {resp.text[:400]}")

    # API returns JSON with base64-encoded image
    import base64
    response_data = resp.json()
    image_b64 = response_data.get("image", "")
    if not image_b64:
        raise HTTPException(status_code=502, detail="NVIDIA FLUX API returned no image data")

    return base64.b64decode(image_b64)


async def _call_nvidia_flux_dev(
    prompt: str,
    base_url: str,
    api_key: str,
    negative_prompt: str = "",
    cfg_scale: float = 5.0,
    seed: int = 0,
    steps: int = 50,
    width: int = 1024,
    height: int = 1024,
    mode: str = "base",
    samples: int = 1,
) -> bytes:
    """Call NVIDIA's FLUX.1-dev API for text-to-image generation.
    Returns binary image data (PNG).
    """
    payload = {
        "prompt": prompt,
        "cfg_scale": cfg_scale,
        "seed": seed,
        "steps": steps,
        "width": width,
        "height": height,
        "mode": mode,
    }
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(base_url, headers=headers, json=payload)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"NVIDIA FLUX.1-dev API error {resp.status_code}: {resp.text[:400]}")

    # API returns JSON with base64-encoded image
    import base64
    response_data = resp.json()
    image_b64 = response_data.get("image", "")
    if not image_b64:
        raise HTTPException(status_code=502, detail="NVIDIA FLUX.1-dev API returned no image data")

    return base64.b64decode(image_b64)


async def _call_local_sd35(
    prompt: str,
    base_url: str,
    negative_prompt: str = "",
    cfg_scale: float = 5.0,
    seed: int = 0,
    steps: int = 30,
    mode: str = "base",
) -> bytes:
    """Call local Stable Diffusion 3.5 NIM for text-to-image generation.
    Returns binary image data (PNG).
    """
    payload = {
        "prompt": prompt,
        "mode": mode,
        "seed": seed,
        "steps": steps,
    }

    if negative_prompt:
        payload["negative_prompt"] = negative_prompt
    if cfg_scale:
        payload["cfg_scale"] = cfg_scale

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(base_url, headers=headers, json=payload)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Local SD3.5 NIM error {resp.status_code}: {resp.text[:400]}")

    # Local NIM returns JSON with artifacts array containing base64 data
    import base64
    response_data = resp.json()
    artifacts = response_data.get("artifacts", [])
    if not artifacts:
        raise HTTPException(status_code=502, detail="Local SD3.5 NIM returned no artifacts")

    image_b64 = artifacts[0].get("base64", "")
    if not image_b64:
        raise HTTPException(status_code=502, detail="Local SD3.5 NIM artifact has no base64 data")

    return base64.b64decode(image_b64)


async def _call_nvidia_flux_pipeline(
    prompt: str,
    flux_dev_url: str,
    flux_dev_key: str,
    flux_kontext_url: str,
    flux_kontext_key: str,
    negative_prompt: str = "",
    cfg_scale: float = 5.0,
    seed: int = 0,
    steps: int = 50,
    width: int = 1024,
    height: int = 1024,
    enhance_prompt: str = "Enhance image quality, improve details, fix artifacts, professional photography",
    enhance_cfg_scale: float = 3.5,
    enhance_steps: int = 30,
) -> bytes:
    """Full pipeline: FLUX.1-dev (text-to-image) -> FLUX.1-Kontext-dev (image-to-image enhancement).
    Returns final enhanced binary image data (PNG).
    """
    # Step 1: Generate initial image with FLUX.1-dev
    initial_image = await _call_nvidia_flux_dev(
        prompt=prompt,
        base_url=flux_dev_url,
        api_key=flux_dev_key,
        negative_prompt=negative_prompt,
        cfg_scale=cfg_scale,
        seed=seed,
        steps=steps,
        width=width,
        height=height,
    )

    # Step 2: Enhance with FLUX.1-Kontext-dev (image-to-image)
    # Note: NVIDIA FLUX.1-Kontext-dev requires image in format: data:image/png;example_id,{0-2}
    # For generated images, we need to use the NVCF asset upload or example_id format
    # Using example_id=0 as a placeholder - in production, upload via NVCF
    import base64
    initial_b64 = base64.b64encode(initial_image).decode('utf-8')

    payload = {
        "text_prompts": [{"text": enhance_prompt, "weight": 1}],
        "image": f"data:image/png;base64,{initial_b64}",  # Using base64 data URI
        "aspect_ratio": "match_input_image",
        "cfg_scale": enhance_cfg_scale,
        "seed": seed,
        "steps": enhance_steps,
    }

    headers = {
        "Authorization": f"Bearer {flux_kontext_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(flux_kontext_url, headers=headers, json=payload)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"NVIDIA FLUX Kontext API error {resp.status_code}: {resp.text[:400]}")

    # API returns JSON with base64-encoded image
    response_data = resp.json()
    image_b64 = response_data.get("image", "")
    if not image_b64:
        raise HTTPException(status_code=502, detail="NVIDIA FLUX Kontext API returned no image data")

    return base64.b64decode(image_b64)


async def get_team_id_for_user(user_id: uuid.UUID, db: AsyncSession) -> uuid.UUID | None:
    result = await db.execute(select(Team).join(TeamMember).where(TeamMember.user_id == user_id))
    team = result.scalars().first()
    return team.id if team else None
