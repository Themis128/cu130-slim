# ai-video-ops

AI avatar/story video generation on the SocialAuto stack — Google Photos
source → FLUX cartoon/mascot avatar → Wan2.2-TI2V animated clips →
speech-bubble captions → music → `media_assets`.

## One-call task (production)

```python
from celery import current_app
current_app.send_task(
    "app.worker.tasks.avatar_video.generate_avatar_story",
    kwargs={
        "scenes": [
            {"clip": "sad",   "line1": "A founder spent €40,000",
             "line2": "on a website that did nothing.", "duration": 7},
            {"clip": "happy", "line1": "3x more leads",
             "line2": "in 4 weeks.", "duration": 9},
        ],
        "filename": "story.mp4",
        "source_photo_asset_id": "<media_assets uuid>",  # or avatar_asset_id
        "music_url": "https://incompetech.com/...mp3",   # optional
        "width": 576, "height": 1024,
    },
)
# result: {"asset_id": "...", "bytes": N, "meta": {...}}
```

Runs on the **media** queue. Scene keys: `clip` (motion name or raw Wan
prompt), `line1`, `line2`, `duration` (s, clips loop to fill).

## Motion library (proven)

| Key | Motion |
|---|---|
| `talk` | mouth moving, slight bounce |
| `sad` | head shake, concerned |
| `happy` | arms up, big smile, cheering |
| `point` | pointing at viewer, emphatic |

Any other string is passed through as the Wan motion prompt — keep the
avatar description in it ("cute chibi cartoon mascot … white background").

## Pipeline internals

- `app/services/avatar_video.py` — `render_avatar()` (FLUX img2img),
  `generate_avatar_video()` (orchestrator), `_speech_bubble()`,
  `_assemble()` (loop+overlay+concat), `_mux_music()`.
- `app/services/comfyui_video.py` — `generate_video()` (Wan2.2 TI2V, i2v
  via `image_bytes`), `upload_image()`, `build_*_prompt()` builders.
- `app/services/comfyui_image.py` — FLUX t2i for standalone images.
- Google Photos source: `google-photos` MCP server (Picker API) —
  creds `GOOGLE_PHOTOS_*` in `.env`. See `google-photos-ops` skill.

## Model inventory (ComfyUI @ settings.COMFYUI_URL)

- `flux1-schnell-Q4_0.gguf` (unet) — avatar img2img (denoise 0.9) + t2i
- `Wan2.2-TI2V-5B-Q4_K_M.gguf` (unet) — animated clips, T2V + I2V
- `ltx-video-2b-v0.9-Q8_0.gguf` (unet) — LTX b-roll (alt tier)
- `clip_l` + `t5xxl_fp8_e4m3fn_scaled` (DualCLIPLoader, type=flux)
- `umt5_xxl_fp8_e4m3fn_scaled` (CLIPLoader, type=wan)
- VAEs: `ae.safetensors` (flux), `wan2.2_vae.safetensors`, `LTX-Video-VAE-BF16`

## Gotchas (learned the hard way)

- **GPU is 8 GB** — Wan2.2 clips take ~2–8 min each at 576×1024×49f; the
  queue processes serially. Don't batch more than ~4 jobs.
- **Wake-proxy drops queued jobs** — prompts accepted but never started
  vanish if ComfyUI sleeps/restarts. Check `/queue` then `/history/<id>`;
  if history is empty and queue is empty, resubmit.
- **FP8+GGUF mixing** — `DualCLIPLoaderGGUF` rejects fp8 safetensors;
  use regular `DualCLIPLoader` (safetensors) + GGUF unet.
- **drawtext colons** — `text='2:14'` breaks ffmpeg drawtext; use
  `textfile=` (what `_assemble` does) to skip escaping entirely.
- **Music licensing** — Kevin MacLeod tracks (incompetech.com) are CC-BY;
  the publish caption must carry the attribution line.
- **Media rules** — every post needs correct media; videos must have
  audio (attach-audio rule). `_mux_music` only runs when `music_url` is
  given: without it the output is **silent and must not be published** —
  attach a track first.

## CLI helper

`scripts/gen-avatar-video.sh <scenes.json> [filename.mp4]` — submits the
task to the media worker and tails until the asset lands.

## Publishing the output

Use the normal posts API / publish_queue with the returned `asset_id`.
Portrait 576×1024 fits IG Reels/TikTok/Threads/Shorts; LinkedIn accepts
it natively. X needs working OAuth (currently gated — see BACKLOG).
