"""ComfyUI text-to-video generation on the local GPU node.

Primary pipeline: LTX-Video 2B (GGUF Q8) — the fastest model that runs
cleanly on the RTX 3070 8GB under the lowvram ComfyUI profile
(~1 it/s sampling, ~25s for a 41-frame 480x832 clip).

Constraint notes (verified live 2026-10-02):
- LTXV latent: width/height must be multiples of 32, num_frames = 8n+1.
- The ltxv-2b-0.9.8-distilled safetensors checkpoint produces noise on
  ComfyUI 0.38 + current ComfyUI-LTXVideo pack (LTX-2.x era) — the GGUF
  build via UnetLoaderGGUF + core KSampler is the working path.
- Requires custom nodes on the ComfyUI instance: ComfyUI-GGUF,
  ComfyUI-LTXVideo (LTXVConditioning/EmptyLTXVLatentVideo),
  ComfyUI-VideoHelperSuite (VHS_VideoCombine).

Model files on the ComfyUI models volume:
- models/unet/ltx-video-2b-v0.9-Q8_0.gguf        (city96/LTX-Video-gguf)
- models/text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors
- models/vae/LTX-Video-VAE-BF16.safetensors      (city96/LTX-Video-gguf)
"""

from __future__ import annotations

import asyncio
import logging
import secrets

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

LTXV_UNET = "ltx-video-2b-v0.9-Q8_0.gguf"
LTXV_CLIP = "t5xxl_fp8_e4m3fn_scaled.safetensors"
LTXV_VAE = "LTX-Video-VAE-BF16.safetensors"

DEFAULT_NEGATIVE = (
    "worst quality, inconsistent motion, blurry, jittery, distorted, "
    "watermark, text, lowres, bad anatomy"
)


class ComfyUIVideoError(Exception):
    """ComfyUI job failed or timed out."""


def build_t2v_prompt(
    *,
    prompt: str,
    negative_prompt: str | None = None,
    width: int = 480,
    height: int = 832,
    num_frames: int = 41,
    frame_rate: int = 25,
    steps: int = 25,
    cfg: float = 3.0,
    seed: int | None = None,
    filename_prefix: str = "socialauto",
) -> dict:
    """Build the ComfyUI API-format prompt for LTX-Video 2B GGUF T2V."""
    if width % 32 or height % 32:
        raise ValueError("LTX-Video dimensions must be multiples of 32")
    if num_frames % 8 != 1:
        raise ValueError("LTX-Video frame count must be 8n+1 (e.g. 25, 33, 41, 97)")
    if not 1 <= steps <= 60:
        raise ValueError("steps must be 1..60")

    return {
        "prompt": {
            "1": {
                "class_type": "UnetLoaderGGUF",
                "inputs": {"unet_name": LTXV_UNET},
            },
            "2": {
                "class_type": "CLIPLoader",
                "inputs": {"clip_name": LTXV_CLIP, "type": "ltxv"},
            },
            "3": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": LTXV_VAE},
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["2", 0]},
            },
            "5": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative_prompt or DEFAULT_NEGATIVE, "clip": ["2", 0]},
            },
            "6": {
                "class_type": "LTXVConditioning",
                "inputs": {
                    "positive": ["4", 0],
                    "negative": ["5", 0],
                    "frame_rate": float(frame_rate),
                },
            },
            "14": {
                "class_type": "EmptyLTXVLatentVideo",
                "inputs": {
                    "width": width,
                    "height": height,
                    "length": num_frames,
                    "batch_size": 1,
                },
            },
            "15": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["6", 0],
                    "negative": ["6", 1],
                    "latent_image": ["14", 0],
                    "seed": seed if seed is not None else secrets.randbits(63),
                    "steps": steps,
                    "cfg": cfg,
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "denoise": 1.0,
                },
            },
            "13": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["15", 0], "vae": ["3", 0]},
            },
            "12": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "frame_rate": frame_rate,
                    "loop_count": 0,
                    "filename_prefix": filename_prefix,
                    "format": "video/h264-mp4",
                    "pix_fmt": "yuv420p",
                    "crf": 19,
                    "save_metadata": False,
                    "pingpong": False,
                    "save_output": True,
                    "images": ["13", 0],
                },
            },
        }
    }


async def generate_video(
    *,
    prompt: str,
    negative_prompt: str | None = None,
    width: int = 480,
    height: int = 832,
    num_frames: int = 41,
    frame_rate: int = 25,
    steps: int = 25,
    cfg: float = 3.0,
    seed: int | None = None,
    filename_prefix: str = "socialauto",
    timeout_s: float = 900.0,
    poll_s: float = 5.0,
) -> tuple[bytes, dict]:
    """Submit a T2V job to ComfyUI, poll until done, return (mp4_bytes, meta).

    meta: {"prompt_id", "filename", "subfolder", "width", "height",
           "frame_rate", "num_frames", "duration_seconds"}
    """
    base = get_settings().COMFYUI_URL.rstrip("/")
    graph = build_t2v_prompt(
        prompt=prompt,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        num_frames=num_frames,
        frame_rate=frame_rate,
        steps=steps,
        cfg=cfg,
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
            raise ComfyUIVideoError(f"ComfyUI rejected the graph: {node_errors}")
        if not prompt_id:
            raise ComfyUIVideoError(f"ComfyUI returned no prompt_id: {resp}")

        logger.info("[comfyui-video] submitted prompt_id=%s (%dx%d, %df @%dfps)",
                    prompt_id, width, height, num_frames, frame_rate)

        deadline = asyncio.get_event_loop().time() + timeout_s
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
                raise ComfyUIVideoError(f"ComfyUI execution failed: {msgs or status}")
            outputs = entry.get("outputs") or {}
            vids = (outputs.get("12") or {}).get("gifs") or []
            if vids:
                meta_out = vids[0]
                params = {
                    "filename": meta_out["filename"],
                    "type": meta_out.get("type", "output"),
                    "subfolder": meta_out.get("subfolder", ""),
                }
                d = await client.get("/view", params=params, timeout=300.0)
                d.raise_for_status()
                if len(d.content) < 1024:
                    raise ComfyUIVideoError("ComfyUI returned a suspiciously small video")
                return d.content, {
                    "prompt_id": prompt_id,
                    "filename": meta_out["filename"],
                    "subfolder": meta_out.get("subfolder", ""),
                    "width": width,
                    "height": height,
                    "frame_rate": frame_rate,
                    "num_frames": num_frames,
                    "duration_seconds": round(num_frames / float(frame_rate), 2),
                }

        raise ComfyUIVideoError(f"ComfyUI video job timed out after {timeout_s:.0f}s")
