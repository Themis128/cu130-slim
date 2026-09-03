"""Lightweight OpenAI-compatible Diffusers text-to-image server.

Exposes:
  GET  /health                    — liveness probe
  GET  /v1/models                 — list loaded model
  POST /v1/images/generations     — OpenAI-compatible image generation

Model is loaded lazily on first request (or on startup if LOAD_ON_STARTUP=true).
Runs on CUDA in fp16 for minimal VRAM usage (~2GB for SD 1.5).
"""
from __future__ import annotations

import base64
import io
import logging
import os
import time

import torch
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("diffusers-server")

MODEL_ID = os.environ.get("MODEL_ID", "stable-diffusion-v1-5/stable-diffusion-v1-5")
HF_TOKEN = os.environ.get("HF_TOKEN", "")
TORCH_DTYPE = os.environ.get("TORCH_DTYPE", "float16")
DEVICE = os.environ.get("DEVICE", "cuda")
LOAD_ON_STARTUP = os.environ.get("LOAD_ON_STARTUP", "false").lower() in ("1", "true", "yes")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "7860"))

_dtype_map = {"float16": torch.float16, "bfloat16": torch.bfloat16, "float32": torch.float32}
DTYPE = _dtype_map.get(TORCH_DTYPE, torch.float16)

app = FastAPI(title="Local Diffusers Image API", version="1.0.0")
_pipeline = None
_load_lock = torch.Lock() if hasattr(torch, "Lock") else None


def _load_pipeline():
    """Load the Diffusers pipeline into VRAM."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    from diffusers import AutoPipelineForText2Image

    logger.info("Loading model %s on %s with %s...", MODEL_ID, DEVICE, TORCH_DTYPE)
    t0 = time.time()
    kwargs = {
        "torch_dtype": DTYPE,
        "safety_checker": None,
        "requires_safety_checker": False,
    }
    if HF_TOKEN:
        kwargs["token"] = HF_TOKEN

    _pipeline = AutoPipelineForText2Image.from_pretrained(MODEL_ID, **kwargs)
    _pipeline = _pipeline.to(DEVICE)
    # Reduce VRAM fragmentation
    if hasattr(_pipeline, "enable_vae_slicing"):
        _pipeline.enable_vae_slicing()
    elapsed = time.time() - t0
    logger.info("Model loaded in %.1fs — VRAM: %s", elapsed, _vram_info())
    return _pipeline


def _vram_info() -> str:
    if not torch.cuda.is_available():
        return "CPU mode"
    allocated = torch.cuda.memory_allocated() / 1024**3
    reserved = torch.cuda.memory_reserved() / 1024**3
    return f"{allocated:.2f}GB allocated, {reserved:.2f}GB reserved"


# ── Request/Response models ───────────────────────────────────────────────────


class ImageGenerationRequest(BaseModel):
    prompt: str
    model: str | None = None
    n: int = 1
    size: str = "512x512"
    steps: int = 20
    negative_prompt: str = ""
    guidance_scale: float = 7.5
    seed: int = 0
    response_format: str = "b64_json"  # b64_json | url


class ImageData(BaseModel):
    b64_json: str | None = None
    url: str | None = None


class ImageGenerationResponse(BaseModel):
    created: int
    data: list[ImageData]


# ── Endpoints ─────────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    loaded = _pipeline is not None
    return {"status": "ok", "model_loaded": loaded, "model_id": MODEL_ID, "vram": _vram_info()}


@app.get("/v1/models")
async def list_models():
    return {"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]}


@app.post("/v1/images/generations", response_model=ImageGenerationResponse)
async def generate_image(req: ImageGenerationRequest):
    """Generate image(s) via local Diffusers pipeline."""
    # Parse size
    try:
        w, h = (int(x) for x in req.size.split("x"))
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid size '{req.size}'. Use 'WxH' e.g. '512x512'.")

    # Clamp to reasonable limits for SD 1.5 (max 1024)
    w = max(64, min(w, 1024))
    h = max(64, min(h, 1024))

    # Load pipeline if not yet loaded
    try:
        pipe = _load_pipeline()
    except Exception as e:
        logger.error("Failed to load model: %s", e)
        raise HTTPException(status_code=503, detail=f"Model load failed: {e}")

    # Generate
    generator = None
    if req.seed > 0:
        generator = torch.Generator(device=DEVICE).manual_seed(req.seed)

    images: list[Image.Image] = []
    for _ in range(max(1, min(req.n, 4))):
        try:
            result = pipe(
                prompt=req.prompt,
                num_inference_steps=max(1, min(req.steps, 80)),
                guidance_scale=req.guidance_scale,
                negative_prompt=req.negative_prompt or None,
                width=w,
                height=h,
                generator=generator,
            )
            images.extend(result.images)
        except Exception as e:
            logger.error("Generation failed: %s", e)
            raise HTTPException(status_code=500, detail=f"Image generation failed: {e}")

    # Encode to base64
    data: list[ImageData] = []
    for img in images:
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        data.append(ImageData(b64_json=b64))

    logger.info("Generated %d image(s) %dx%d in %d steps — %s", len(data), w, h, req.steps, _vram_info())
    return ImageGenerationResponse(created=int(time.time()), data=data)


@app.on_event("startup")
async def _startup():
    if LOAD_ON_STARTUP:
        logger.info("LOAD_ON_STARTUP=true — preloading model...")
        try:
            _load_pipeline()
        except Exception as e:
            logger.error("Startup model load failed (will retry on first request): %s", e)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
