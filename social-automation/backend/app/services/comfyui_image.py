"""ComfyUI text-to-image generation on the local GPU node (FLUX.1-schnell GGUF).

Primary local image pipeline — replaces the retired local-diffusers SD 1.5
path (2026-10-05). FLUX.1-schnell is Apache-2.0 (commercial-safe, unlike
FLUX.1-dev) and 4-step distilled, so a render lands in ~15-40s on the
RTX 3070 8GB under the `flux` lowvram profile (t5 fp8 + Q4 unet page
between VRAM and system RAM).

Model files on the ComfyUI models volume:
- models/unet/flux1-schnell-Q4_0.gguf            (city96/FLUX.1-schnell-gguf)
- models/text_encoders/clip_l.safetensors      (comfyanonymous/flux_text_encoders)
- models/text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors
- models/vae/ae.safetensors                     (ffxvs/vae-flux mirror)

GPU serialization: callers must hold ``gpu_arbiter.media_gpu_lock()`` so
DMR models are evicted before the render — on 8GB, FLUX + a resident
llama-server will OOM.
"""

from __future__ import annotations

import asyncio
import logging
import secrets

import httpx

from app.core.config import get_settings
from app.services.comfyui_video import _cancel_prompt

logger = logging.getLogger(__name__)

FLUX_UNET = "flux1-schnell-Q4_0.gguf"
FLUX_CLIP_L = "clip_l.safetensors"
FLUX_T5 = "t5xxl_fp8_e4m3fn_scaled.safetensors"
FLUX_VAE = "ae.safetensors"


class ComfyUIImageError(Exception):
    """ComfyUI image job failed or timed out."""


def build_flux_prompt(
    *,
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    steps: int = 4,
    seed: int | None = None,
    filename_prefix: str = "socialauto",
) -> dict:
    """Build the ComfyUI API-format graph for FLUX.1-schnell GGUF txt2img."""
    for name, dim in (("width", width), ("height", height)):
        if dim % 16:
            raise ValueError(f"{name} must be a multiple of 16")
        if not 256 <= dim <= 1440:
            raise ValueError(f"{name} must be 256..1440 on the 8GB card")
    if width * height > 1440 * 1440:
        raise ValueError("width*height exceeds the 2MP practical limit on 8GB")
    if not 1 <= steps <= 20:
        raise ValueError("steps must be 1..20 (schnell is 4-step distilled)")

    return {
        "prompt": {
            "1": {
                "class_type": "UnetLoaderGGUF",
                "inputs": {"unet_name": FLUX_UNET},
            },
            "2": {
                "class_type": "DualCLIPLoader",
                "inputs": {
                    "clip_name1": FLUX_CLIP_L,
                    "clip_name2": FLUX_T5,
                    "type": "flux",
                },
            },
            "3": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": FLUX_VAE},
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["2", 0]},
            },
            # schnell is guidance-distilled and runs at cfg 1.0 — the
            # negative input is required by KSampler but unused.
            "5": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "", "clip": ["2", 0]},
            },
            "6": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": width, "height": height, "batch_size": 1},
            },
            "7": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["4", 0],
                    "negative": ["5", 0],
                    "latent_image": ["6", 0],
                    "seed": seed if seed is not None else secrets.randbits(63),
                    "steps": steps,
                    "cfg": 1.0,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "denoise": 1.0,
                },
            },
            "8": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["7", 0], "vae": ["3", 0]},
            },
            "9": {
                "class_type": "SaveImage",
                "inputs": {
                    "filename_prefix": filename_prefix,
                    "images": ["8", 0],
                },
            },
        }
    }


async def generate_image(
    *,
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    steps: int = 4,
    seed: int | None = None,
    filename_prefix: str = "socialauto",
    timeout_s: float = 300.0,
    poll_s: float = 2.0,
) -> tuple[bytes, dict]:
    """Submit a txt2img job to ComfyUI, poll until done, return (png_bytes, meta)."""
    base = get_settings().COMFYUI_URL.rstrip("/")
    graph = build_flux_prompt(
        prompt=prompt,
        width=width,
        height=height,
        steps=steps,
        seed=seed,
        filename_prefix=filename_prefix,
    )

    async with httpx.AsyncClient(base_url=base, timeout=60.0) as client:
        r = await client.post("/prompt", json=graph)
        r.raise_for_status()
        resp = r.json()
        prompt_id = resp.get("prompt_id")
        node_errors = resp.get("node_errors") or {}
        if node_errors:
            raise ComfyUIImageError(f"ComfyUI rejected the graph: {node_errors}")
        if not prompt_id:
            raise ComfyUIImageError(f"ComfyUI returned no prompt_id: {resp}")

        logger.info("[comfyui-image] submitted prompt_id=%s (%dx%d)", prompt_id, width, height)

        deadline = asyncio.get_event_loop().time() + timeout_s
        try:
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(poll_s)
                h = await client.get(f"/history/{prompt_id}")
                h.raise_for_status()
                entry = (h.json() or {}).get(prompt_id)
                if not entry:
                    continue
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    msgs = [m[1].get("exception_message", "?")
                            for m in status.get("messages", []) if m[0] == "execution_error"]
                    raise ComfyUIImageError(f"ComfyUI execution failed: {msgs or status}")
                outputs = entry.get("outputs") or {}
                imgs = (outputs.get("9") or {}).get("images") or []
                if imgs:
                    meta_out = imgs[0]
                    params = {
                        "filename": meta_out["filename"],
                        "type": meta_out.get("type", "output"),
                        "subfolder": meta_out.get("subfolder", ""),
                    }
                    d = await client.get("/view", params=params, timeout=120.0)
                    d.raise_for_status()
                    if len(d.content) < 1024:
                        raise ComfyUIImageError("ComfyUI returned a suspiciously small image")
                    return d.content, {
                        "prompt_id": prompt_id,
                        "filename": meta_out["filename"],
                        "subfolder": meta_out.get("subfolder", ""),
                        "width": width,
                        "height": height,
                        "model": "flux1-schnell-Q4_0",
                        "provider": "comfyui-flux",
                    }
        except asyncio.CancelledError:
            await _cancel_prompt(client, prompt_id)
            raise
        await _cancel_prompt(client, prompt_id)
        raise ComfyUIImageError(f"ComfyUI image job timed out after {timeout_s:.0f}s")
