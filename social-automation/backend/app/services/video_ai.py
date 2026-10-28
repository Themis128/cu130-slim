"""Video analysis for the media library.

Uploads accept mp4/webm/mov, but the AI tagging path was image-only —
videos got no caption, no tags, no quality score, no poster frame, and no
probed metadata (duration/resolution stayed NULL, which platform
validators need for TikTok duration caps and IG reel specs).

This module extends the existing DMR-first vision pipeline to video via
ffmpeg/ffprobe (already installed in the api and worker images):

- ``probe_video`` — ffprobe metadata: duration, resolution, fps, codecs.
- ``extract_sample_frames`` — deterministic evenly-spaced frames sampled
  at 15%/50%/85% of duration (skips head/tail fades and intro logos);
  falls back to ffmpeg's ``thumbnail`` filter when duration is unknown.
- ``pick_poster_frame`` — reuses ``score_image_quality`` to pick the
  sharpest, best-exposed frame; persisted beside the video as
  ``<storage_path>.poster.jpg`` and referenced from
  ``meta_data.poster_path`` / ``poster_url``.
- ``auto_tag_video_asset`` — captions the poster frame and tags it with
  the shared DMR qwen3-vl → Cloudflare vision chain, merges tags across
  sampled frames, stores a per-frame quality rollup in
  ``meta_data.quality_score``, and Chroma-indexes the caption+tags like
  image assets so videos are searchable.

Best-effort throughout: failures are logged, partial results (probe data,
quality scores) are still persisted, and nothing raises.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.core.path_utils import safe_resolve
from app.db.session import async_session_maker
from app.models.content import MediaAsset
from app.services import chroma_client, minio_storage, r2_storage

logger = logging.getLogger(__name__)
settings = get_settings()

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/app/uploads"))

POSTER_SUFFIX = ".poster.jpg"
_FRAME_TIMEOUT = 60
_PROBE_TIMEOUT = 30
# Skip head/tail — fades, title cards, and logos live there. Three samples
# is enough signal for caption/tags/quality on social-length clips.
_SAMPLE_POSITIONS = (0.15, 0.5, 0.85)


async def _run(cmd: list[str], timeout: int = _FRAME_TIMEOUT) -> subprocess.CompletedProcess:
    """Run a subprocess off the event loop. Single seam for tests."""
    return await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=timeout)


async def _run_op(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    """`_run` for the video-edit ops: a missing ffmpeg binary or a timeout
    becomes a failed CompletedProcess (rc 127/124) so callers return None
    instead of raising."""
    try:
        return await _run(cmd, timeout=timeout)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(cmd, 127, b"", str(exc).encode())
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, b"", b"timeout")


def _parse_fps(raw: str | None) -> float | None:
    """Parse ffprobe's rational fps ('30000/1001') into a float."""
    if not raw or raw == "0/0":
        return None
    try:
        num, _, den = raw.partition("/")
        fps = float(num) / float(den or "1")
        return round(fps, 3) if fps > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


async def probe_video(video_bytes: bytes) -> dict[str, Any]:
    """Extract video metadata via ffprobe.

    Returns ``{duration_seconds, width, height, fps, video_codec,
    has_audio, bitrate}`` — missing keys absent on failure. Never raises.
    """
    try:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "clip"
            src.write_bytes(video_bytes)
            proc = await _run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(src),
                ],
                timeout=_PROBE_TIMEOUT,
            )
        if proc.returncode != 0:
            logger.warning("ffprobe failed rc=%s: %s", proc.returncode, proc.stderr[-200:])
            return {}
        data = json.loads(proc.stdout.decode("utf-8", errors="replace") or "{}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("ffprobe unavailable (%s)", type(exc).__name__)
        return {}

    streams = data.get("streams") or []
    vstream = next((s for s in streams if s.get("codec_type") == "video"), {})
    fmt = data.get("format") or {}
    out: dict[str, Any] = {}
    try:
        duration = float(fmt.get("duration") or vstream.get("duration") or 0)
        if duration > 0:
            out["duration_seconds"] = int(round(duration))
    except (TypeError, ValueError):
        pass
    if vstream.get("width"):
        out["width"] = int(vstream["width"])
    if vstream.get("height"):
        out["height"] = int(vstream["height"])
    fps = _parse_fps(vstream.get("avg_frame_rate") or vstream.get("r_frame_rate"))
    if fps:
        out["fps"] = fps
    if vstream.get("codec_name"):
        out["video_codec"] = vstream["codec_name"]
    if any(s.get("codec_type") == "audio" for s in streams):
        out["has_audio"] = True
    try:
        bitrate = int(fmt.get("bit_rate") or 0)
        if bitrate:
            out["bitrate"] = bitrate
    except (TypeError, ValueError):
        pass
    return out


async def _extract_frame_at(src: Path, at_seconds: float, dst: Path) -> bytes | None:
    """Extract one JPEG frame at a timestamp. ``-ss`` before ``-i`` = fast seek."""
    proc = await _run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{at_seconds:.3f}",
            "-i",
            str(src),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(dst),
        ]
    )
    if proc.returncode == 0 and dst.is_file() and dst.stat().st_size:
        return dst.read_bytes()
    return None


async def extract_sample_frames(
    video_bytes: bytes,
    *,
    duration_seconds: float | None = None,
    max_frames: int = 3,
) -> list[bytes]:
    """Extract representative JPEG frames from video bytes.

    Evenly-spaced samples at 15%/50%/85% when duration is known. When it
    isn't (corrupt container, missing probe), falls back to ffmpeg's
    ``thumbnail`` filter which histogram-picks a representative frame.
    """
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "clip"
        src.write_bytes(video_bytes)
        frames: list[bytes] = []

        if duration_seconds and duration_seconds > 0:
            positions = _SAMPLE_POSITIONS[:max_frames]
            for i, pos in enumerate(positions):
                frame = await _extract_frame_at(src, duration_seconds * pos, Path(td) / f"f{i}.jpg")
                if frame:
                    frames.append(frame)
            if frames:
                return frames

        # Fallback: thumbnail filter on the first ~4s of frames.
        dst = Path(td) / "thumb.jpg"
        proc = await _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-vf",
                "thumbnail=90",
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(dst),
            ]
        )
        if proc.returncode == 0 and dst.is_file() and dst.stat().st_size:
            frames.append(dst.read_bytes())
        return frames


def pick_poster_frame(frames: list[bytes]) -> bytes | None:
    """Pick the highest-quality frame as the video poster.

    Reuses the deterministic image scorer (sharpness/brightness/contrast)
    so posters avoid blurry or black frames. Returns None when empty.
    """
    if not frames:
        return None
    from app.services.image_enhance import score_image_quality

    def _score(f: bytes) -> int:
        try:
            return score_image_quality(f).overall
        except Exception:  # noqa: BLE001
            return -1

    return max(frames, key=_score)


async def _store_poster(asset: MediaAsset, poster_bytes: bytes) -> dict[str, str] | None:
    """Persist the poster beside the video on the same storage backend.

    Returns ``{"path": ..., "url": ...}`` for ``meta_data``, or None.
    """
    poster_path = f"{asset.storage_path}{POSTER_SUFFIX}"
    try:
        if asset.storage_backend == "minio":
            res = await minio_storage.upload_object(poster_path, poster_bytes, content_type="image/jpeg")
            return {"path": poster_path, "url": res.get("public_url") or ""}
        if asset.storage_backend == "r2":
            res = await r2_storage.upload_object(poster_path, poster_bytes, content_type="image/jpeg")
            return {"path": poster_path, "url": res.get("public_url") or ""}
        target = safe_resolve(str(UPLOAD_DIR), poster_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(poster_bytes)
        base = (settings.MEDIA_PUBLIC_BASE_URL or "").rstrip("/")
        url = f"{base}/api/v1/media/view?path={poster_path}" if base else f"/api/v1/media/view?path={poster_path}"
        return {"path": poster_path, "url": url}
    except Exception as exc:  # noqa: BLE001
        logger.warning("poster persist failed for %s: %s", asset.id, exc)
        return None


def _score_frames(frames: list[bytes]) -> dict[str, Any]:
    """Quality rollup across sampled frames — same shape as image assets."""
    from app.services.image_enhance import score_image_quality

    per_frame = []
    for f in frames:
        try:
            s = score_image_quality(f)
            per_frame.append(
                {
                    "overall": s.overall,
                    "sharpness": s.sharpness,
                    "brightness": s.brightness,
                    "contrast": s.contrast,
                    "blur_detected": s.blur_detected,
                }
            )
        except Exception:  # noqa: BLE001
            continue
    if not per_frame:
        return {}
    return {
        "overall": min(f["overall"] for f in per_frame),
        "sharpness": min(f["sharpness"] for f in per_frame),
        "brightness": int(sum(f["brightness"] for f in per_frame) / len(per_frame)),
        "contrast": int(sum(f["contrast"] for f in per_frame) / len(per_frame)),
        "blur_detected": all(f["blur_detected"] for f in per_frame),
        "frames_sampled": len(per_frame),
    }


async def auto_tag_video_asset(asset: MediaAsset, db) -> None:
    """Caption, tag, quality-score, and poster a video asset.

    Called from ``media_ai.auto_tag_asset`` when the asset mime is
    ``video/*``. Reuses the image vision chain (DMR qwen3-vl → Cloudflare)
    on extracted frames — no new dependencies. Never raises.
    """
    from app.services import media_ai
    from app.services.media_spellcheck import correct_tags, correct_text

    video_bytes = await media_ai._load_image_bytes(asset)
    if not video_bytes:
        return

    probe = await probe_video(video_bytes)
    changed = False
    if asset.duration_seconds is None and probe.get("duration_seconds"):
        asset.duration_seconds = probe["duration_seconds"]
        changed = True
    if asset.width is None and probe.get("width"):
        asset.width = probe["width"]
        asset.height = probe.get("height")
        changed = True
    if probe:
        meta = dict(asset.meta_data or {})
        meta["video"] = probe
        asset.meta_data = meta
        flag_modified(asset, "meta_data")
        changed = True

    frames = await extract_sample_frames(video_bytes, duration_seconds=probe.get("duration_seconds"))
    if not frames:
        if changed:
            await db.commit()
        return

    quality = _score_frames(frames)
    poster = pick_poster_frame(frames) or frames[0]

    poster_info = await _store_poster(asset, frame_to_poster_jpeg(poster))
    if poster_info:
        meta = dict(asset.meta_data or {})
        meta["poster_path"] = poster_info["path"]
        if poster_info.get("url"):
            meta["poster_url"] = poster_info["url"]
        asset.meta_data = meta
        flag_modified(asset, "meta_data")

    if quality:
        meta = dict(asset.meta_data or {})
        meta["quality_score"] = quality
        meta["quality_failed"] = quality["sharpness"] < settings.MIN_IMAGE_SHARPNESS or quality["overall"] < settings.MIN_IMAGE_QUALITY_SCORE
        asset.meta_data = meta
        flag_modified(asset, "meta_data")

    # Caption the quality-picked poster frame; merge tags across up to 2
    # sampled frames so a mid-clip scene change still contributes keywords.
    resized, mime = media_ai._resize_for_vision(poster)
    caption = await media_ai._caption_image(media_ai._data_uri(resized, mime))
    tags: list[str] = []
    seen: set[str] = set()
    for frame in frames[:2]:
        resized, mime = media_ai._resize_for_vision(frame)
        for t in await media_ai._tag_image(media_ai._data_uri(resized, mime)):
            if t not in seen:
                seen.add(t)
                tags.append(t)

    if caption:
        asset.ai_caption = await correct_text(caption)
    if tags:
        asset.ai_tags = await correct_tags(tags[:10])

    embedding_id = str(asset.id)
    asset.embedding_id = embedding_id
    await db.commit()

    text = f"{asset.ai_caption or ''} {' '.join(asset.ai_tags or [])}".strip()
    if text:
        await chroma_client.add_content(str(asset.team_id), embedding_id, text)


async def tag_video_by_id(asset_id: uuid.UUID | str, session_factory=None) -> None:
    """Standalone entry for the Celery task / manual re-tag of a video."""
    if isinstance(asset_id, str):
        asset_id = uuid.UUID(asset_id)
    factory = session_factory or async_session_maker
    async with factory() as db:
        result = await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
        asset = result.scalar_one_or_none()
        if not asset:
            logger.warning("tag_video_by_id: asset %s not found", asset_id)
            return
        await auto_tag_video_asset(asset, db)


def frame_to_poster_jpeg(frame_bytes: bytes, max_edge: int = 720) -> bytes:
    """Normalize a poster frame to web JPEG (RGB, capped edge)."""
    img = Image.open(io.BytesIO(frame_bytes))
    img.load()
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_edge:
        scale = max_edge / float(max(w, h))
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue()


# ── Video editing ops — trim/convert/audio mux for the media library ─────────
# ffmpeg is already in the image. Byte-in/byte-out like the image ops so the
# media_enhance API can stream results; temp files live in a TemporaryDirectory
# scoped per call. Best-effort like the analysis path — log, don't raise to the
# caller beyond an empty result they convert to a 422.

_VIDEO_OP_TIMEOUT = 300


def _suffix_for_mime(mime: str) -> str:
    return {
        "video/quicktime": ".mov",
        "video/webm": ".webm",
        "video/x-matroska": ".mkv",
        "video/x-msvideo": ".avi",
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/aac": ".aac",
        "audio/ogg": ".ogg",
        "audio/mp4": ".m4a",
        "audio/flac": ".flac",
    }.get(mime, ".mp4")


async def trim_video(video_bytes: bytes, start: float, end: float, *, mime: str = "video/mp4") -> bytes | None:
    """Cut [start,end] seconds; re-encodes audio+video for clean keyframe cuts."""
    if end <= start or start < 0:
        return None
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"in{_suffix_for_mime(mime)}"
        dst = Path(td) / "out.mp4"
        src.write_bytes(video_bytes)
        r = await _run_op(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{start:.3f}",
                "-i",
                str(src),
                "-t",
                f"{end - start:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(dst),
            ],
            timeout=_VIDEO_OP_TIMEOUT,
        )
        if r.returncode != 0 or not dst.exists():
            logger.warning(f"[video/trim] ffmpeg rc={r.returncode}: {r.stderr.decode(errors='replace')[-300:]}")
            return None
        return dst.read_bytes()


async def convert_video(video_bytes: bytes, fmt: str, *, mime: str = "video/mp4") -> bytes | None:
    """Transcode to mp4|webm|mov (webm keeps vp9+opus for platform limits)."""
    fmt = fmt.lower().lstrip(".")
    if fmt not in ("mp4", "webm", "mov"):
        return None
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"in{_suffix_for_mime(mime)}"
        dst = Path(td) / f"out.{fmt}"
        src.write_bytes(video_bytes)
        cmd = ["ffmpeg", "-y", "-i", str(src)]
        if fmt == "webm":
            cmd += ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-c:a", "libopus"]
        else:
            cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "aac", "-b:a", "128k"]
            if fmt == "mp4":
                cmd += ["-movflags", "+faststart"]
        cmd.append(str(dst))
        r = await _run_op(cmd, timeout=_VIDEO_OP_TIMEOUT)
        if r.returncode != 0 or not dst.exists():
            logger.warning(f"[video/convert] ffmpeg rc={r.returncode}: {r.stderr.decode(errors='replace')[-300:]}")
            return None
        return dst.read_bytes()


async def attach_audio(
    video_bytes: bytes,
    audio_bytes: bytes,
    *,
    video_mime: str = "video/mp4",
    audio_mime: str = "audio/mpeg",
    loop_audio: bool = True,
) -> bytes | None:
    """Mux an audio track onto video — the 'post must carry sound/music' op.

    Shortest-stream wins; -stream_loop -1 on the audio input so short music
    loops under a longer video. Replaces any existing audio track.
    """
    with tempfile.TemporaryDirectory() as td:
        vsrc = Path(td) / f"v{_suffix_for_mime(video_mime)}"
        asrc = Path(td) / f"a{_suffix_for_mime(audio_mime)}"
        dst = Path(td) / "out.mp4"
        vsrc.write_bytes(video_bytes)
        asrc.write_bytes(audio_bytes)
        cmd = ["ffmpeg", "-y", "-i", str(vsrc)]
        if loop_audio:
            cmd += ["-stream_loop", "-1"]
        cmd += [
            "-i",
            str(asrc),
            "-c:v",
            "copy",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(dst),
        ]
        r = await _run_op(cmd, timeout=_VIDEO_OP_TIMEOUT)
        if r.returncode != 0 or not dst.exists():
            logger.warning(f"[video/attach-audio] ffmpeg rc={r.returncode}: {r.stderr.decode(errors='replace')[-300:]}")
            return None
        return dst.read_bytes()


async def mute_video(video_bytes: bytes, *, mime: str = "video/mp4") -> bytes | None:
    """Strip all audio tracks (stream copy — fast)."""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"in{_suffix_for_mime(mime)}"
        dst = Path(td) / "out.mp4"
        src.write_bytes(video_bytes)
        r = await _run_op(
            ["ffmpeg", "-y", "-i", str(src), "-c:v", "copy", "-an", "-movflags", "+faststart", str(dst)],
            timeout=_VIDEO_OP_TIMEOUT,
        )
        if r.returncode != 0 or not dst.exists():
            logger.warning(f"[video/mute] ffmpeg rc={r.returncode}: {r.stderr.decode(errors='replace')[-300:]}")
            return None
        return dst.read_bytes()


async def extract_audio(video_bytes: bytes, *, mime: str = "video/mp4") -> bytes | None:
    """Pull the audio track out as mp3 — for reuse/replace workflows."""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"in{_suffix_for_mime(mime)}"
        dst = Path(td) / "out.mp3"
        src.write_bytes(video_bytes)
        r = await _run_op(
            ["ffmpeg", "-y", "-i", str(src), "-vn", "-c:a", "libmp3lame", "-q:a", "4", str(dst)],
            timeout=_VIDEO_OP_TIMEOUT,
        )
        if r.returncode != 0 or not dst.exists():
            logger.warning(f"[video/extract-audio] ffmpeg rc={r.returncode}: {r.stderr.decode(errors='replace')[-300:]}")
            return None
        return dst.read_bytes()
