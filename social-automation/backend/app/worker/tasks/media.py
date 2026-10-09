"""Celery tasks for the media library."""
import asyncio
import logging
import uuid

from celery import shared_task

from app.services import comfyui_video, gpu_arbiter, media_ai
from app.services.db_sync import sync_after_worker_task
from app.services.media_storage import save_uploaded_media
from app.worker._async import get_session_factory, run_async, task_session

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app

logger = logging.getLogger(__name__)


celery_app.set_default()
celery_app.set_current()


@shared_task
def auto_tag_asset_task(asset_id: str) -> None:
    """Run AI auto-tagging for a media asset."""
    try:
        asset_uuid = uuid.UUID(asset_id)
    except ValueError:
        logger.warning("auto_tag_asset_task: invalid asset_id %s", asset_id)
        return

    async def _run() -> None:
        # NullPool: run_async() creates a fresh loop per task; pooled
        # connections from the shared engine bind to the creating loop.
        await media_ai.auto_tag_asset(
            asset_uuid, session_factory=get_session_factory()
        )

    try:
        run_async(_run())
        # Push worker writes (media_assets) to D1 primary
        run_async(sync_after_worker_task(["media_assets"]))
    except Exception as exc:
        logger.warning("auto_tag_asset_task failed for %s: %s", asset_id, exc)


@shared_task
def generate_video_asset_task(team_id: str, user_id: str, prompt: str, options: dict | None = None) -> str:
    """Generate a video via ComfyUI (LTX-Video 2B GGUF) and store a MediaAsset.

    Returns the new media asset id as a string so the API can hand it back
    through the Celery result backend.
    """
    options = options or {}

    async def _run() -> str:
        # Expand the terse prompt into a detailed scene description BEFORE
        # the GPU lock — the call runs on the warm DMR mid model.
        expanded_prompt = await media_ai.expand_visual_prompt(prompt, media_type="video")
        async with task_session() as db:
                num_frames = int(options.get("num_frames") or 81)
                frame_rate = int(options.get("frame_rate") or 24)
                # Long-form path: explicit shot list, or duration_seconds split
                # into per-segment prompts (Creator Rewards needs 60s+).
                scene_prompts = options.get("scene_prompts") or []
                duration = int(options.get("duration_seconds") or 0)
                if not scene_prompts and duration > 0:
                    seg_len = num_frames / float(frame_rate)
                    n = max(1, round(duration / seg_len))
                    scene_prompts = [expanded_prompt] * n
                model = options.get("model") or "wan22"
                image_bytes = None
                image_asset_id = options.get("image_asset_id")
                if image_asset_id:
                    from app.models.content import MediaAsset
                    src = await db.get(MediaAsset, uuid.UUID(image_asset_id))
                    if not src:
                        raise ValueError(f"image_asset_id {image_asset_id} not found")
                    image_bytes = await media_ai._load_image_bytes(src)
                    if not image_bytes:
                        raise ValueError(f"could not load bytes for asset {image_asset_id}")

                # One-model-in-VRAM: hold the GPU lock for the render so
                # DMR can't load a model mid-job (8GB card).
                async with gpu_arbiter.media_gpu_lock():
                    if scene_prompts:
                        data, meta = await comfyui_video.generate_video_segments(
                            prompts=[p if (p or "").strip() else expanded_prompt for p in scene_prompts],
                            negative_prompt=options.get("negative_prompt"),
                            width=int(options.get("width") or 704),
                            height=int(options.get("height") or 1280),
                            num_frames=num_frames,
                            frame_rate=frame_rate,
                            steps=int(options.get("steps") or 20),
                            cfg=float(options.get("cfg_scale") or 5.0),
                            seed=options.get("seed"),
                            filename_prefix="socialauto",
                            model=model,
                        )
                    else:
                        data, meta = await comfyui_video.generate_video(
                            prompt=expanded_prompt,
                            negative_prompt=options.get("negative_prompt"),
                            width=int(options.get("width") or 704),
                            height=int(options.get("height") or 1280),
                            num_frames=num_frames,
                            frame_rate=frame_rate,
                            steps=int(options.get("steps") or 20),
                            cfg=float(options.get("cfg_scale") or 5.0),
                            seed=options.get("seed"),
                            filename_prefix="socialauto",
                            model=model,
                            image_bytes=image_bytes,
                            i2v_strength=float(options.get("i2v_strength") or 1.0),
                        )
                # Semantic QA on frame 0 — DMR vision, AFTER the GPU lock was
                # released (the vision model reloads through the arbiter).
                qa = {"match": True, "caption": None, "reason": "not run"}
                try:
                    import subprocess
                    import tempfile
                    from pathlib import Path

                    with tempfile.TemporaryDirectory() as td:
                        src_mp4 = Path(td) / "clip.mp4"
                        frame_png = Path(td) / "f0.png"
                        src_mp4.write_bytes(data)
                        proc = await asyncio.to_thread(
                            subprocess.run,
                            ["ffmpeg", "-y", "-i", str(src_mp4), "-frames:v", "1",
                             str(frame_png)],
                            capture_output=True, timeout=60,
                        )
                        if proc.returncode == 0 and frame_png.exists():
                            qa = await media_ai.verify_media_semantics(
                                frame_png.read_bytes(), prompt, media_type="video")
                            if not qa["match"]:
                                logger.warning("[video-task] QA mismatch (%s) — retrying once",
                                               qa.get("reason"))
                                retry_prompt = (
                                    f"{expanded_prompt}. Depict: {prompt[:200]}. "
                                    f"Avoid: {qa.get('reason', 'wrong subject')}"
                                )
                                async with gpu_arbiter.media_gpu_lock():
                                    data, meta = await comfyui_video.generate_video(
                                        prompt=retry_prompt,
                                        negative_prompt=options.get("negative_prompt"),
                                        width=int(options.get("width") or 704),
                                        height=int(options.get("height") or 1280),
                                        num_frames=num_frames, frame_rate=frame_rate,
                                        steps=int(options.get("steps") or 20),
                                        cfg=float(options.get("cfg_scale") or 5.0),
                                        seed=None, filename_prefix="socialauto",
                                        model=model,
                                        image_bytes=image_bytes,
                                        i2v_strength=float(options.get("i2v_strength") or 1.0),
                                    )
                                src_mp4.write_bytes(data)
                                proc = await asyncio.to_thread(
                                    subprocess.run,
                                    ["ffmpeg", "-y", "-i", str(src_mp4), "-frames:v", "1",
                                     str(frame_png)],
                                    capture_output=True, timeout=60,
                                )
                                if proc.returncode == 0 and frame_png.exists():
                                    qa = await media_ai.verify_media_semantics(
                                        frame_png.read_bytes(), prompt, media_type="video")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[video-task] semantic QA skipped: %s", type(exc).__name__)

                asset = await save_uploaded_media(
                    db,
                    team_id=uuid.UUID(team_id),
                    user_id=uuid.UUID(user_id),
                    original_filename=meta["filename"],
                    content=data,
                    mime_type="video/mp4",
                    alt_text=options.get("alt_text") or f"AI-generated video: {prompt[:120]}",
                    tags=options.get("tags") or ["comfyui", meta.get("model", "wan22"), "generated-video"],
                    width=meta["width"],
                    height=meta["height"],
                )
                asset.source = f"comfyui-{meta.get('model', 'wan22')}"
                asset.generation_prompt = prompt
                asset.duration_seconds = int(round(meta["duration_seconds"]))
                meta_dict = dict(asset.meta_data or {})
                meta_dict["semantic_qa"] = qa
                if expanded_prompt != prompt:
                    meta_dict["expanded_prompt"] = expanded_prompt
                asset.meta_data = meta_dict
                await db.commit()
                return str(asset.id)

    asset_id = run_async(_run())
    run_async(sync_after_worker_task(["media_assets"]))
    return asset_id
