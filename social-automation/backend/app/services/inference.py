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
        "default_model": "@cf/black-forest-labs/flux-1",
        "requires_key": True,
        "description": "Cloudflare Workers AI — free tier with LLM, FLUX image and Whisper speech-to-text models",
        "model_examples": [
            "@cf/openai/whisper",
            "@cf/facebook/wav2vec2-base-960h",
            "@cf/speechbrain/asr-cnn-transformer",
            "@cf/meta/llama-3.1-8b-instruct",
            "@cf/black-forest-labs/flux-1",
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
            return _resolve_base_url(provider_name, record.base_url), record.default_model, api_key

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
        "openai": settings.OPENAI_API_KEY,
        "nvidia-flux": settings.NVIDIA_API_KEY,
        "nvidia-flux-dev": settings.NVIDIA_API_KEY,
        "cloudflare": settings.CLOUDFLARE_API_TOKEN,
    }
    api_key = env_key_map.get(provider_name, "")
    api_key = api_key if api_key else None

    return _resolve_base_url(provider_name, catalog["base_url"]), catalog["default_model"], api_key


def _resolve_base_url(provider_name: str, base_url: str) -> str:
    """Substitute Cloudflare account ID (or other placeholders) into a provider base URL."""
    if provider_name == "cloudflare" and "{account_id}" in base_url:
        return base_url.replace("{account_id}", settings.CLOUDFLARE_ACCOUNT_ID)
    return base_url


# Speech-to-text model identifiers available on Cloudflare Workers AI
STT_MODELS: dict[str, str] = {
    "whisper": "@cf/openai/whisper",
    "wav2vec2": "@cf/facebook/wav2vec2-base-960h",
    "speechbrain": "@cf/speechbrain/asr-cnn-transformer",
}


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
    api_key = (api_key or settings.CLOUDFLARE_API_TOKEN or "").strip()

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
            detail="CLOUDFLARE_API_TOKEN is not configured for Cloudflare Workers AI.",
        )

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/"
        f"{model}"
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": content_type or "application/octet-stream",
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
    api_key = (api_key or "").strip() or (settings.CLOUDFLARE_API_TOKEN or "").strip()

    if not account_id:
        raise HTTPException(
            status_code=400,
            detail="CLOUDFLARE_ACCOUNT_ID is not configured. Add it to the root `.env` and restart social-api.",
        )
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="CLOUDFLARE_API_TOKEN is not configured for Cloudflare Workers AI.",
        )

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
    if max_tokens:
        payload["max_tokens"] = max_tokens
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Cloudflare Workers AI error {resp.status_code}: {resp.text[:400]}")

    response_text = ((resp.json().get("result") or {}).get("response", "") or "").strip()
    if schema:
        return _parse_json_response(response_text)
    return {"text": response_text}


async def call_inference(
    prompt: str,
    provider_name: str = "groq",
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
    if not api_key and provider_name != "local-sd35":
        raise HTTPException(
            status_code=400,
            detail=f"No API key configured for provider '{provider_name}'. Add it in Settings → AI Providers.",
        )
    if provider_name == "cloudflare":
        return await _call_workers_ai_chat(prompt, model=model, api_key=api_key, schema=schema, max_tokens=max_tokens)
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
