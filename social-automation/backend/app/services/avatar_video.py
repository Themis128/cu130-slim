"""Avatar story-video orchestrator.

Chains the proven pipeline into one call:

    avatar image (source photo → cartoon via FLUX img2img, or an existing
    avatar PNG) → Wan2.2-TI2V animated clips per scene → ffmpeg scene
    assembly (speech-bubble captions) → music bed → finished vertical mp4.

Everything runs inside the worker container: ComfyUI on
``settings.COMFYUI_URL``, ffmpeg for assembly, PIL for speech bubbles.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.services.comfyui_video import generate_video, upload_image

logger = logging.getLogger(__name__)

_NEGATIVE = "morphing, distortion, style change, realistic, blurry, extra limbs"
_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


class AvatarVideoError(RuntimeError):
    pass


@dataclass
class Scene:
    """One story beat: motion prompt + up to two caption lines + duration."""

    clip: str  # one of CLIP_MOTIONS keys or a raw Wan motion prompt
    line1: str
    line2: str = ""
    duration: float = 6.0


CLIP_MOTIONS = {
    "talk": "cute chibi cartoon mascot, huge head tiny body, flat vector style, white background, talking, mouth moving, slight bounce",
    "sad": "cute chibi cartoon mascot, huge head tiny body, flat vector style, white background, shaking head slowly, concerned sad expression",
    "happy": "cute chibi cartoon mascot, huge head tiny body, flat vector style, white background, excited, arms up cheering, big smile",
    "point": "cute chibi cartoon mascot, huge head tiny body, flat vector style, white background, pointing at viewer, emphatic gesture",
}


def _flux_img2img_graph(*, image_name: str, prompt: str, seed: int = 7, denoise: float = 0.9) -> dict:
    """FLUX schnell img2img workflow — source photo → stylized avatar."""
    return {
        "prompt": {
            "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "flux1-schnell-Q4_0.gguf"}},
            "2": {
                "class_type": "DualCLIPLoader",
                "inputs": {"clip_name1": "clip_l.safetensors", "clip_name2": "t5xxl_fp8_e4m3fn_scaled.safetensors", "type": "flux", "device": "default"},
            },
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
            "4": {"class_type": "LoadImage", "inputs": {"image": image_name}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "photorealistic, photo, skin texture, pores, blurry, deformed", "clip": ["2", 0]}},
            "7": {"class_type": "VAEEncode", "inputs": {"pixels": ["4", 0], "vae": ["3", 0]}},
            "8": {"class_type": "FluxGuidance", "inputs": {"guidance": 3.5, "conditioning": ["5", 0]}},
            "9": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": 4,
                    "cfg": 1.0,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "denoise": denoise,
                    "model": ["1", 0],
                    "positive": ["8", 0],
                    "negative": ["6", 0],
                    "latent_image": ["7", 0],
                },
            },
            "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 0]}},
            "11": {"class_type": "SaveImage", "inputs": {"filename_prefix": "avatar_img2img", "images": ["10", 0]}},
        }
    }


async def render_avatar(*, image_bytes: bytes, style_prompt: str, seed: int = 7, denoise: float = 0.9, timeout_s: float = 600.0) -> bytes:
    """Transform a source photo into a stylized avatar via FLUX img2img."""
    base = get_settings().COMFYUI_URL.rstrip("/")
    # base_url is required: upload_image posts to the relative /upload/image.
    async with httpx.AsyncClient(base_url=base, timeout=60) as client:
        name = await upload_image(client, image_bytes, "avatar_src.png")
        graph = _flux_img2img_graph(image_name=name, prompt=style_prompt, seed=seed, denoise=denoise)
        r = await client.post(f"{base}/prompt", json=graph)
        r.raise_for_status()
        prompt_id = r.json()["prompt_id"]

        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(5)
            h = await client.get(f"{base}/history/{prompt_id}")
            hist = h.json()
            if prompt_id not in hist:
                continue
            entry = hist[prompt_id]
            status = (entry.get("status") or {}).get("status_str")
            if status == "error":
                raise AvatarVideoError(f"FLUX img2img failed: {entry.get('status')}")
            if status != "success":
                continue
            for out in entry.get("outputs", {}).values():
                for img in out.get("images", []) or []:
                    fn = img["filename"]
                    r2 = await client.get(f"{base}/view", params={"filename": fn})
                    if r2.status_code == 200 and len(r2.content) > 1000:
                        return r2.content
        raise AvatarVideoError("FLUX img2img timed out")


def _speech_bubble(lines: list[str], out: Path) -> None:
    """Render a comic speech-bubble PNG (transparent bg) via PIL."""
    from PIL import Image, ImageDraw, ImageFont

    f = ImageFont.truetype(_FONT, 30)
    lines = [ln for ln in lines if ln]
    bw = int(max(f.getlength(ln) for ln in lines)) + 60
    bh = len(lines) * 48 + 50
    im = Image.new("RGBA", (bw, bh + 45), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, bw, bh], radius=28, fill="white", outline="black", width=5)
    d.polygon([(bw // 3, bh - 2), (bw // 3 + 70, bh - 2), (bw // 3 + 15, bh + 40)], fill="white", outline="black")
    d.polygon([(bw // 3 + 8, bh), (bw // 3 + 62, bh), (bw // 3 + 15, bh + 36)], fill="white")
    for i, ln in enumerate(lines):
        d.text(((bw - f.getlength(ln)) / 2, 25 + i * 48), ln, fill="black", font=f)
    im.save(out)


def _assemble(scenes: list[tuple[bytes, Scene]], width: int, height: int, out_path: Path) -> None:
    """Loop each clip to scene duration + overlay its speech bubble; concat."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        names = []
        for i, (blob, sc) in enumerate(scenes):
            clip = tmpdir / f"c{i}.mp4"
            clip.write_bytes(blob)
            bub = tmpdir / f"b{i}.png"
            _speech_bubble([sc.line1, sc.line2], bub)
            seg = tmpdir / f"s{i}.mp4"
            vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=24"
            cmd = [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-stream_loop",
                "-1",
                "-i",
                str(clip),
                "-i",
                str(bub),
                "-filter_complex",
                f"[0:v]{vf}[v];[v][1:v]overlay=(W-w)/2:60",
                "-t",
                str(sc.duration),
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-an",
                str(seg),
            ]
            r = subprocess.run(cmd, capture_output=True, timeout=300)
            if r.returncode != 0:
                raise AvatarVideoError(f"scene {i} render failed: {r.stderr.decode()[-300:]}")
            names.append(seg.name)
        listfile = tmpdir / "list.txt"
        listfile.write_text("".join(f"file '{n}'\n" for n in names))
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", str(out_path)],
            capture_output=True,
            timeout=120,
        )
        if r.returncode != 0 or not out_path.exists():
            raise AvatarVideoError(f"concat failed: {r.stderr.decode()[-300:]}")


def _mux_music(video: Path, music: bytes | None, duration: float, out: Path) -> None:
    """Attach a music bed (faded, ducked) or leave silent."""
    if not music:
        video.rename(out)
        return
    with tempfile.TemporaryDirectory() as tmp:
        track = Path(tmp) / "track.mp3"
        track.write_bytes(music)
        fade_out = max(0.0, duration - 3)
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-i",
            str(track),
            "-filter_complex",
            f"[1:a]atrim=0:{duration},volume=0.35,afade=t=in:d=1.5,afade=t=out:st={fade_out}:d=3[mus]",
            "-map",
            "0:v",
            "-map",
            "[mus]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(out),
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=180)
        if r.returncode != 0:
            raise AvatarVideoError(f"music mux failed: {r.stderr.decode()[-300:]}")


async def generate_avatar_video(
    *,
    avatar_png: bytes | None = None,
    source_photo: bytes | None = None,
    avatar_style_prompt: str = ("cute chibi cartoon mascot, huge head tiny body, flat vector, thick outlines, bold colors, clean white background, full body"),
    scenes: list[Scene] | None = None,
    music_bytes: bytes | None = None,
    width: int = 576,
    height: int = 1024,
    num_frames: int = 49,
    steps: int = 20,
) -> tuple[bytes, dict]:
    """Full pipeline: avatar → animated clips → captioned scenes → music mp4.

    Pass either ``avatar_png`` (already stylized) or ``source_photo``
    (runs FLUX img2img first). Each Scene's clip is rendered via
    Wan2.2-TI2V i2v conditioned on the avatar.
    Returns ``(mp4_bytes, meta)``.
    """
    if not scenes:
        raise AvatarVideoError("scenes required")
    if avatar_png is None:
        if source_photo is None:
            raise AvatarVideoError("avatar_png or source_photo required")
        avatar_png = await render_avatar(image_bytes=source_photo, style_prompt=avatar_style_prompt)

    # generate_video() uploads image_bytes itself for each i2v clip.
    # Render one i2v clip per unique motion (clips get looped per scene).
    needed = {sc.clip for sc in scenes}
    clips: dict[str, bytes] = {}
    for i, key in enumerate(needed):
        motion = CLIP_MOTIONS.get(key, key)
        data, meta = await generate_video(
            prompt=motion,
            negative_prompt=_NEGATIVE,
            image_bytes=avatar_png,
            width=width,
            height=height,
            num_frames=num_frames,
            frame_rate=24,
            steps=steps,
            cfg=3.5,
            filename_prefix=f"avatar_scene{i}",
        )
        clips[key] = data
        logger.info("[avatar-video] clip %s rendered (%d bytes)", key, len(data))

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        nocap = tmpdir / "nocap.mp4"
        out = tmpdir / "final.mp4"
        await asyncio.to_thread(_assemble, [(clips[s.clip], s) for s in scenes], width, height, nocap)
        total = sum(s.duration for s in scenes)
        await asyncio.to_thread(_mux_music, nocap, music_bytes, total, out)
        video = out.read_bytes()

    return video, {
        "scenes": len(scenes),
        "duration_seconds": total,
        "width": width,
        "height": height,
        "clips": sorted(clips),
        "avatar_source": "img2img" if source_photo else "provided",
    }
