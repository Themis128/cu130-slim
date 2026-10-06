"""Live verification of the 8GB GPU architecture — run inside social-api.

Proves, in order:
1. DMR warm load (baseline latency + model resident in /api/ps)
2. Arbitration: media_gpu_lock + FLUX 1080x1350 render while a DMR chat
   queues behind gpu:media_busy instead of OOMing the card
3. /api/ps empties while the media job owns the GPU
4. Inline semantic QA on real output: match=True vs the real prompt,
   match=False vs a deliberately wrong subject (retry trigger path)
"""
import asyncio
import json
import time
import urllib.request

from app.core.config import get_settings
from app.services import comfyui_image, dmr, media_ai
from app.services.gpu_arbiter import media_gpu_busy, media_gpu_lock


def _ps():
    base = get_settings().DMR_URL.split("/engines/")[0]
    with urllib.request.urlopen(f"{base}/api/ps", timeout=10) as r:
        return [m["model"] for m in json.load(r)["models"]]


async def main():
    t0 = time.time()

    # 1. Warm DMR — proves baseline inference + makes a model resident
    t = time.time()
    warm = await dmr.call_dmr_chat("Reply with exactly: OK", max_tokens=8)
    warm_s = time.time() - t
    print(f"[1] warm chat {warm_s:.0f}s -> {str(warm)[:60]}")
    print(f"    resident before lock: {[m.split('/')[-1] for m in _ps()]}")

    resident_during: list[str] = []
    busy_during: bool | None = None
    img_bytes: bytes | None = None

    async def media_job():
        nonlocal img_bytes
        async with media_gpu_lock():
            return await comfyui_image.generate_image(
                prompt=(
                    "flat vector illustration of a laptop on a desk with "
                    "deploy pipeline icons above it, neon cyan accents on "
                    "dark background, clean minimal style"
                ),
                width=1080,
                height=1350,
            )

    async def text_job():
        t = time.time()
        r = await dmr.call_dmr_chat("Reply with exactly: DONE", max_tokens=8)
        return time.time() - t, r

    # 2+3. Media job owns the GPU; text request must queue, not crash.
    mj = asyncio.create_task(media_job())
    await asyncio.sleep(5)  # let the lock acquire and unload DMR
    busy_during = await media_gpu_busy()
    try:
        resident_during = _ps()
    except Exception as exc:
        resident_during = [f"ps-error:{type(exc).__name__}"]
    print(f"[3] media_busy during render: {busy_during}")
    print(f"    resident during render: {[m.split('/')[-1] for m in resident_during]}")

    tj = asyncio.create_task(text_job())
    img_bytes = await mj
    text_s, text_r = await tj
    print(f"[2] FLUX 1080x1350 -> {len(img_bytes)} bytes")
    print(f"    queued chat completed in {text_s:.0f}s -> {str(text_r)[:60]}")

    # 4. Semantic QA — both branches against the real render
    ok = await media_ai.verify_media_semantics(
        img_bytes,
        "a laptop on a desk with deploy pipeline icons, flat illustration",
        media_type="image",
    )
    bad = await media_ai.verify_media_semantics(
        img_bytes,
        "a photograph of a red sports car on a mountain road",
        media_type="image",
    )
    print(f"[4] QA match (real prompt):    {ok}")
    print(f"    QA match (wrong subject):  {bad}")

    print(f"\nTOTAL {time.time()-t0:.0f}s")
    verdict = {
        "busy_during": busy_during,
        "resident_during_empty": len(resident_during) == 0,
        "image_bytes": len(img_bytes) > 50_000,
        "text_queued_not_failed": text_s > 5,
        "qa_true_positive": ok.get("match") is True,
        "qa_true_negative": bad.get("match") is False,
    }
    print("VERDICT:", json.dumps(verdict))
    assert all(v for v in verdict.values()), "SOME CHECKS FAILED"
    print("ALL CHECKS PASSED")


asyncio.run(main())
