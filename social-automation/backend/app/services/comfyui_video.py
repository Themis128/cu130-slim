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
import subprocess
import tempfile
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.services import stack_ops

logger = logging.getLogger(__name__)

LTXV_UNET = "ltx-video-2b-v0.9-Q8_0.gguf"
LTXV_CLIP = "t5xxl_fp8_e4m3fn_scaled.safetensors"
LTXV_VAE = "LTX-Video-VAE-BF16.safetensors"

# Wan2.1 1.3B fp16 — quality tier (core nodes only, ~7.3s/step on the 8GB
# card; staged workflow: comfyui-workflows/tiktok-video-wan21.json).
WAN21_UNET = "wan2.1_t2v_1.3B_fp16.safetensors"
WAN21_CLIP = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
WAN21_VAE = "wan_2.1_vae.safetensors"

# Wan2.2 TI2V-5B GGUF Q4_K_M — new quality tier. ~4x the params of Wan2.1
# 1.3B, native hybrid T2V+I2V (start_image on Wan22ImageToVideoLatent), and
# the official ComfyUI docs target 8GB VRAM with native offloading. Graph
# per the official video_wan2_2_5B_ti2v template: uni_pc/simple, cfg 5,
# 20 steps, 24fps, Wan2.2 high-compression VAE.
WAN22_UNET = "Wan2.2-TI2V-5B-Q4_K_M.gguf"
WAN22_CLIP = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
WAN22_VAE = "wan2.2_vae.safetensors"

VIDEO_MODELS = ("ltxv", "wan21", "wan22")

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
    for name, dim in (("width", width), ("height", height)):
        if not 128 <= dim <= 1216:
            raise ValueError(f"{name} must be 128..1216 (LTX-Video 2B range)")
    if num_frames % 8 != 1:
        raise ValueError("LTX-Video frame count must be 8n+1 (e.g. 25, 33, 41, 97)")
    if not 9 <= num_frames <= 257:
        raise ValueError("num_frames must be 9..257 (~0.3-10s at 25fps)")
    if not 1 <= steps <= 60:
        raise ValueError("steps must be 1..60")
    if not 1 <= frame_rate <= 60:
        raise ValueError("frame_rate must be 1..60")

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


def build_wan21_prompt(
    *,
    prompt: str,
    negative_prompt: str | None = None,
    width: int = 480,
    height: int = 832,
    num_frames: int = 49,
    frame_rate: int = 24,
    steps: int = 8,
    cfg: float = 6.0,
    seed: int | None = None,
    filename_prefix: str = "socialauto",
) -> dict:
    """Build the ComfyUI API-format prompt for Wan2.1 1.3B T2V (quality tier).

    Constraints differ from LTXV: dimensions multiple of 16, frames 4n+1,
    uni_pc/simple sampler, cfg ~6. Wan is trained at 16fps — the VHS combine
    plays it back at ``frame_rate`` (24 keeps ~2s of a 49f clip for TikTok).
    """
    if width % 16 or height % 16:
        raise ValueError("Wan2.1 dimensions must be multiples of 16")
    for name, dim in (("width", width), ("height", height)):
        if not 128 <= dim <= 1280:
            raise ValueError(f"{name} must be 128..1280 (Wan2.1 1.3B range)")
    if num_frames % 4 != 1:
        raise ValueError("Wan2.1 frame count must be 4n+1 (e.g. 33, 49, 81)")
    if not 17 <= num_frames <= 129:
        raise ValueError("num_frames must be 17..129")
    if not 1 <= steps <= 60:
        raise ValueError("steps must be 1..60")
    if not 16 <= frame_rate <= 60:
        raise ValueError("frame_rate must be 16..60 for Wan2.1 output")

    return {
        "prompt": {
            "1": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": WAN21_UNET, "weight_dtype": "default"},
            },
            "2": {
                "class_type": "CLIPLoader",
                "inputs": {"clip_name": WAN21_CLIP, "type": "wan"},
            },
            "3": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": WAN21_VAE},
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["2", 0]},
            },
            "5": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative_prompt or DEFAULT_NEGATIVE, "clip": ["2", 0]},
            },
            "14": {
                "class_type": "EmptyHunyuanLatentVideo",
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
                    "positive": ["4", 0],
                    "negative": ["5", 0],
                    "latent_image": ["14", 0],
                    "seed": seed if seed is not None else secrets.randbits(63),
                    "steps": steps,
                    "cfg": cfg,
                    "sampler_name": "uni_pc",
                    "scheduler": "simple",
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


def build_wan22_prompt(
    *,
    prompt: str,
    image_name: str | None = None,
    negative_prompt: str | None = None,
    width: int = 480,
    height: int = 832,
    num_frames: int = 81,
    frame_rate: int = 24,
    steps: int = 20,
    cfg: float = 5.0,
    seed: int | None = None,
    filename_prefix: str = "socialauto",
) -> dict:
    """Build the ComfyUI API-format prompt for Wan2.2 TI2V-5B GGUF.

    Hybrid model: T2V when ``image_name`` is omitted, I2V when a
    ``LoadImage`` node feeds ``start_image`` on Wan22ImageToVideoLatent.
    Wan2.2-5B constraints (official template video_wan2_2_5B_ti2v):
    dims multiple of 16, frames 4n+1, uni_pc/simple @ cfg 5, 24fps.
    """
    if width % 16 or height % 16:
        raise ValueError("Wan2.2 dimensions must be multiples of 16")
    for name, dim in (("width", width), ("height", height)):
        if not 128 <= dim <= 1280:
            raise ValueError(f"{name} must be 128..1280 (Wan2.2 5B range)")
    if num_frames % 4 != 1:
        raise ValueError("Wan2.2 frame count must be 4n+1 (e.g. 49, 81, 121)")
    if not 17 <= num_frames <= 161:
        raise ValueError("num_frames must be 17..161 (~0.7-6.7s at 24fps)")
    if not 1 <= steps <= 60:
        raise ValueError("steps must be 1..60")
    if not 16 <= frame_rate <= 60:
        raise ValueError("frame_rate must be 16..60 for Wan2.2 output")

    nodes: dict[str, dict] = {
        "1": {
            "class_type": "UnetLoaderGGUF",
            "inputs": {"unet_name": WAN22_UNET},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": WAN22_CLIP, "type": "wan"},
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": WAN22_VAE},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["2", 0]},
        },
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative_prompt or DEFAULT_NEGATIVE, "clip": ["2", 0]},
        },
        "14": {
            "class_type": "Wan22ImageToVideoLatent",
            "inputs": {
                "vae": ["3", 0],
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
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["14", 0],
                "seed": seed if seed is not None else secrets.randbits(63),
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "uni_pc",
                "scheduler": "simple",
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
    if image_name:
        nodes["20"] = {
            "class_type": "LoadImage",
            "inputs": {"image": image_name},
        }
        nodes["14"]["inputs"]["start_image"] = ["20", 0]
    return {"prompt": nodes}


def build_i2v_prompt(
    *,
    prompt: str,
    image_name: str,
    negative_prompt: str | None = None,
    width: int = 480,
    height: int = 832,
    num_frames: int = 41,
    frame_rate: int = 25,
    steps: int = 25,
    cfg: float = 3.0,
    strength: float = 1.0,
    seed: int | None = None,
    filename_prefix: str = "socialauto",
) -> dict:
    """Build the LTX-Video image-to-video graph.

    ``image_name`` is a file already uploaded to ComfyUI's input dir via
    ``POST /upload/image`` (see ``upload_image``). LTXVImgToVideoConditionOnly
    conditions the first latent frame on the image — it is resized to the
    latent resolution, so the source should already match ``width/height``.
    """
    if not (image_name or "").strip():
        raise ValueError("image_name is required for i2v")
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
    nodes = graph["prompt"]
    nodes["20"] = {
        "class_type": "LoadImage",
        "inputs": {"image": image_name},
    }
    nodes["21"] = {
        "class_type": "LTXVImgToVideoConditionOnly",
        "inputs": {
            "vae": ["3", 0],
            "image": ["20", 0],
            "latent": ["14", 0],
            "strength": strength,
        },
    }
    # Sampler consumes the conditioned latent instead of the empty one.
    nodes["15"]["inputs"]["latent_image"] = ["21", 0]
    return graph


async def upload_image(client: httpx.AsyncClient, image_bytes: bytes, name: str) -> str:
    """Upload an image into ComfyUI's input dir; returns the server-side name."""
    files = {"image": (name, image_bytes, "image/png")}
    r = await client.post("/upload/image", files=files, timeout=60.0)
    r.raise_for_status()
    resp = r.json()
    image_name = resp.get("name")
    if not image_name:
        raise ComfyUIVideoError(f"ComfyUI image upload failed: {resp}")
    return image_name


async def _cancel_prompt(client: httpx.AsyncClient, prompt_id: str) -> None:
    """Best-effort ComfyUI cleanup for a prompt we no longer wait on.

    Only calls ``/interrupt`` when *our* prompt is the running job —
    interrupting blindly would kill another tenant's job. Otherwise the
    prompt is still queued and a ``/queue`` delete removes it.
    """
    try:
        q = await client.get("/queue", timeout=10)
        q.raise_for_status()
        running = {item[1] for item in (q.json() or {}).get("queue_running", []) if len(item) > 1}
        if prompt_id in running:
            await client.post("/interrupt", timeout=10)
        else:
            await client.post("/queue", json={"delete": [prompt_id]}, timeout=10)
    except Exception as exc:  # noqa: BLE001 — cleanup must never mask the real error
        logger.warning("[comfyui-video] cancel of prompt %s failed: %s", prompt_id, exc)


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
    model: str = "ltxv",
    image_bytes: bytes | None = None,
    i2v_strength: float = 1.0,
    timeout_s: float = 900.0,
    poll_s: float = 5.0,
) -> tuple[bytes, dict]:
    """Submit a T2V/I2V job to ComfyUI, poll until done, return (mp4_bytes, meta).

    ``model``: "ltxv" (fast tier, default), "wan21" (mid quality tier —
    slower, different frame/latent constraints; callers passing LTXV
    defaults are normalized to Wan-friendly values), or "wan22" (top
    quality tier — Wan2.2 TI2V-5B GGUF, T2V + native I2V).
    ``image_bytes``: when set (LTXV or Wan2.2), runs image-to-video — the
    first frame is conditioned on the image at ``i2v_strength``.

    meta: {"prompt_id", "filename", "subfolder", "width", "height",
           "frame_rate", "num_frames", "duration_seconds", "model"}
    """
    if model not in VIDEO_MODELS:
        raise ValueError(f"model must be one of {VIDEO_MODELS}")
    if model == "wan21" and image_bytes is not None:
        raise ValueError("image-to-video is only supported on the ltxv and wan22 models")
    if model == "wan21":
        # Normalize LTXV-shaped defaults to Wan2.1 constraints (4n+1 frames,
        # 16fps-trained model, uni_pc @ cfg 6, ~8 steps is its sweet spot).
        if num_frames % 4 != 1:
            num_frames = 49
        if frame_rate == 25:
            frame_rate = 24
        if steps == 25:
            steps = 8
        if cfg == 3.0:
            cfg = 6.0
    if model == "wan22":
        # Normalize to Wan2.2-5B constraints (4n+1 frames, 24fps-trained,
        # uni_pc/simple @ cfg 5, 20 steps per the official template).
        if num_frames % 4 != 1:
            num_frames = 81
        if frame_rate == 25:
            frame_rate = 24
        if steps == 25:
            steps = 20
        if cfg == 3.0:
            cfg = 5.0

    base = get_settings().COMFYUI_URL.rstrip("/")
    image_name: str | None = None
    if image_bytes is not None:
        async with httpx.AsyncClient(base_url=base, timeout=60.0) as up:
            image_name = await upload_image(up, image_bytes, f"i2v-{secrets.token_hex(6)}.png")
    if image_name is not None and model == "wan22":
        graph = build_wan22_prompt(
            prompt=prompt,
            image_name=image_name,
            negative_prompt=negative_prompt,
            width=width, height=height, num_frames=num_frames,
            frame_rate=frame_rate, steps=steps, cfg=cfg,
            seed=seed, filename_prefix=filename_prefix,
        )
    elif image_name is not None:
        graph = build_i2v_prompt(
            prompt=prompt,
            image_name=image_name,
            negative_prompt=negative_prompt,
            width=width, height=height, num_frames=num_frames,
            frame_rate=frame_rate, steps=steps, cfg=cfg,
            strength=i2v_strength, seed=seed, filename_prefix=filename_prefix,
        )
    elif model == "wan22":
        graph = build_wan22_prompt(
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width, height=height, num_frames=num_frames,
            frame_rate=frame_rate, steps=steps, cfg=cfg,
            seed=seed, filename_prefix=filename_prefix,
        )
    elif model == "wan21":
        graph = build_wan21_prompt(
            prompt=prompt,
            negative_prompt=negative_prompt,
            width=width, height=height, num_frames=num_frames,
            frame_rate=frame_rate, steps=steps, cfg=cfg,
            seed=seed, filename_prefix=filename_prefix,
        )
    else:
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

    # Pin ComfyUI awake for the render — the stack-ops sleeper only sees
    # proxied connections + CPU%, and a GPU-bound job can look "idle".
    await stack_ops.keepawake("comfyui", ttl_s=int(timeout_s) + 600)

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
                        "model": model,
                    }
        except asyncio.CancelledError:
            # Celery revoke / worker shutdown — don't leave the GPU job running.
            await _cancel_prompt(client, prompt_id)
            raise
        # Timeout — the prompt may still be queued or running in ComfyUI.
        await _cancel_prompt(client, prompt_id)
        raise ComfyUIVideoError(f"ComfyUI video job timed out after {timeout_s:.0f}s")


def _concat_mp4s(blobs: list[bytes], frame_rate: int) -> bytes:
    """Concatenate MP4 segments (same codec/params) via the ffmpeg concat demuxer."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        names = []
        for i, blob in enumerate(blobs):
            name = f"seg_{i:03d}.mp4"
            (tmpdir / name).write_bytes(blob)
            names.append(name)
        listfile = tmpdir / "list.txt"
        listfile.write_text("".join(f"file '{n}'\n" for n in names))
        out = tmpdir / "out.mp4"
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(listfile), "-c", "copy", str(out),
            ],
            capture_output=True, timeout=120,
        )
        if proc.returncode != 0 or not out.exists():
            raise ComfyUIVideoError(
                f"ffmpeg concat failed: {proc.stderr.decode(errors='replace')[-400:]}"
            )
        return out.read_bytes()


async def generate_video_segments(
    *,
    prompts: list[str],
    negative_prompt: str | None = None,
    width: int = 480,
    height: int = 832,
    num_frames: int = 41,
    frame_rate: int = 25,
    steps: int = 25,
    cfg: float = 3.0,
    seed: int | None = None,
    filename_prefix: str = "socialauto",
    model: str = "ltxv",
    per_segment_timeout_s: float = 900.0,
) -> tuple[bytes, dict]:
    """Generate N video segments sequentially and stitch them into one MP4.

    Used for long-form output (e.g. 60s+ Creator-Rewards clips): each segment
    gets its own prompt (shot list) or repeats the base prompt. Segments run
    one at a time — the GPU can only sample one job at a time anyway.
    """
    if not prompts:
        raise ValueError("prompts must contain at least one scene prompt")

    blobs: list[bytes] = []
    metas: list[dict] = []
    for i, scene_prompt in enumerate(prompts):
        data, meta = await generate_video(
            prompt=scene_prompt,
            negative_prompt=negative_prompt,
            width=width, height=height,
            num_frames=num_frames, frame_rate=frame_rate,
            steps=steps, cfg=cfg,
            seed=None if seed is None else seed + i,
            filename_prefix=f"{filename_prefix}_seg{i:03d}",
            model=model,
            timeout_s=per_segment_timeout_s,
        )
        blobs.append(data)
        metas.append(meta)
        logger.info("[comfyui-video] segment %d/%d done (%s, %d bytes)",
                    i + 1, len(prompts), meta["filename"], len(data))

    if len(blobs) == 1:
        return blobs[0], metas[0]

    combined = await asyncio.to_thread(_concat_mp4s, blobs, frame_rate)
    seg_duration = num_frames / float(frame_rate)
    return combined, {
        "prompt_id": metas[0]["prompt_id"],
        "filename": f"{filename_prefix}_long.mp4",
        "subfolder": "",
        "width": width,
        "height": height,
        "frame_rate": frame_rate,
        "num_frames": num_frames * len(blobs),
        "segments": len(blobs),
        "duration_seconds": round(seg_duration * len(blobs), 2),
    }
