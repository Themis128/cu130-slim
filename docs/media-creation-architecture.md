# Media Creation Architecture

How SocialAuto produces, normalizes, stores, and serves media for social posts.
For the model/engine benchmark layer see `dmr-media-generation-architecture.md`.
Workstation GPU/RAM limits: [`CODEMAP.md` § Workstation](CODEMAP.md#workstation-office--wsl).

```
Entry points                          Generation                     Persistence                     Serving
────────────                          ──────────                     ───────────                     ────────
POST /api/v1/ai/generate-image    ┐
POST /api/v1/ai/generate-content  │   images: local-diffusers    ┐   persist_generated_image      ┐   GET /api/v1/media/view?path=
POST /api/v1/brand/logo|favicon   │   (SD 1.5 GPU :7860)         │   ─────────────────────────    │   (public, unauthenticated —
POST /api/v1/ai/generate-carousel │        ↓ fallback            │   1. downscale (max_edge)      │    mirrors /api/v1/uploads)
POST /api/v1/ai/generate-carousel-│   Cloudflare Workers AI      ├──▶ 2. detect real format       ├──▶ GET /api/v1/uploads/*
    pipeline (CF-only)            │   (ONLY cloud fallback)      │    from bytes                  │   R2 public URL
POST /api/v1/ai/run-carousel-and- │                                3. convert to requested fmt    │   (pub-*.r2.dev)
    publish (n8n)                 │   text:   DMR :12435         │    (JPEG default; PNG for      │
POST /api/v1/media/upload         │        ↓ fallback            │    transparency, PDF for       │
POST /api/v1/media/generate-video │   Cloudflare Workers AI      ┘    carousels)                   │
    (+ /{task_id} poll)           │                                4. matching extension + MIME
worker task media_enhance         ┘   video:  ComfyUI :8000          5. storage backend write
                                      (Wan2.2 TI2V-5B GGUF, GPU)     6. media_assets row
                                           ↓ multi-segment
                                      ffmpeg concat (60s+ clips)
```

## Generation paths

| Path | Function | Notes |
|---|---|---|
| Single image | `POST /ai/generate-image` → provider chain | local-diffusers primary → CF Workers AI fallback. Returns a persisted `media_assets` row. |
| Text (captions, NLP, titles) | `call_inference` | DMR primary → CF fallback. `ai/qwen3:8b-q4_K_M` long-form, `Qwen3-4B-Instruct` mid/chatbot, `ai/smollm3` tiny, `ai/llama3.2` carousel NLP/title. |
| Brand logo / favicon | `POST /brand/logo`, `POST /brand/favicon` | Pass `extension=".png"` explicitly to preserve alpha. |
| LinkedIn carousel | `run_cloudless_carousel_pipeline` (`carousel_pipeline.py`) | Copy (CF) → NLP fix (DMR `ai/llama3.2`) → spellcheck → per-slide CF txt2img background → `compose_branded_slide` (PIL brand canvas) → single multi-page PDF. Creates a draft Post + PostTarget for the Company Page; `publish=true` schedules it. |
| Media enhance | worker task `app.worker.tasks.media_enhance` | Upscales/restyles existing assets. |
| Text-to-video | `POST /media/generate-video` → `generate_video_asset_task` (media queue) → `services/comfyui_video.py` | Wan2.2 TI2V-5B GGUF (Q4_K_M) on ComfyUI (`COMFYUI_URL`, GPU) — the only video model (beat LTXV/Wan2.1 on verified A/B). Async: returns `{task_id, poll_url}`; poll `GET /media/generate-video/{task_id}` → `{status, asset_id}`. Output H.264/yuv420p MP4. Options: `width`/`height` (multiples of 16 — default 704×1280 = Wan2.2 native, TikTok 9:16), `num_frames` (4n+1, default 81), `frame_rate` (24), `steps` (20), `cfg_scale` (5.0), `seed`, `negative_prompt`, `duration_seconds` (≤60), `scene_prompts` (≤40), `image_asset_id` (native I2V). Nonzero `duration_seconds` or a `scene_prompts` shot list runs `generate_video_segments` — sequential per-scene jobs stitched via the ffmpeg concat demuxer (60s+ Creator-Rewards clips). `source=comfyui-wan22`, `duration_seconds` recorded on the asset. |

## Video model notes (benchmarked 2026-10-09, RTX 3070 8GB, lowvram profile)

- **Only model: `Wan2.2-TI2V-5B-Q4_K_M.gguf`** (QuantStack) via `UnetLoaderGGUF` + `umt5_xxl_fp8` CLIP + `wan2.2_vae` + `Wan22ImageToVideoLatent` + `KSampler` (uni_pc/simple, cfg 5) + `VAEDecodeTiled` + `VHS_VideoCombine`. ~25s/step → ~10 min sampling + ~5 min tiled decode for an 81-frame 704×1280 clip. Requires `ComfyUI-GGUF` + `ComfyUI-VideoHelperSuite`.
- **Native res is 704×1280** (per HF model card) — rendering at 480×832 produces dark/blurry output (out-of-distribution); always render at native res and downscale if needed.
- **Use the official Wan2.2 negative prompt** (`WAN22_NEGATIVE`) — targets overexposed/blurry/gray failure modes; the generic DEFAULT_NEGATIVE produced blown-out mush.
- **Tiled VAE decode is mandatory** — the 16×16×4 temporal VAE stalls 25min+/OOMs on a single-pass 720p decode on 8GB.
- LTXV (`ltx-video-2b-v0.9-Q8_0.gguf`) and Wan2.1 (`wan2.1_t2v_1.3B_fp16`) are retired — builders kept for reference but unreachable via the API (`VIDEO_MODELS = ("wan22",)`).
- Long-form = N sequential segments stitched by ffmpeg (`generate_video_segments`).
- Model weights live on the ComfyUI models volume (`storage-models/`, gitignored), not in the repo.
- n8n `tiktok-video-post` workflow drives the pipeline every 2 days 19:00 Athens (webhook `/webhook/tiktok-video-post`): DMR caption + scene prompt → this endpoint → post with `media_ids` → `MEDIA_UPLOAD` (scheduled) or `DIRECT_POST` (explicit). `publish:false` = draft-only dry run.

## Persistence — `persist_generated_image` (`services/media_storage.py`)

The single compatibility boundary for all generated bytes:

1. **Downscale** to `max_edge` (default `MEDIA_MAX_EDGE`, 768) — keeps platform limits safe.
2. **Format detection** (`_detect_image_format`) reads magic bytes — never trusts the requested extension.
3. **Conversion**: when JPEG is requested/defaulted, `_ensure_jpeg_bytes` normalizes `RGBA`/`LA`/`P` through RGBA and composites onto a white RGB background using the real alpha channel (palette transparency included). On conversion failure it falls back to the detected format/MIME — never mislabels bytes.
4. **Extension + MIME** always match the stored payload (`.jpg`/`image/jpeg`, `.png`/`image/png`, `.pdf`, …).
5. **Storage write** via the fallback chain below; `storage_path` key layout `YYYY/MM/DD/[<folder>/]<rand><ext>`.
6. **DB row** in `media_assets` with accurate `mime_type`, `filename`, `public_url`, `tags`/`generation_prompt`/`ai_caption`.

## Storage fallback chain

R2 (Cloudflare, cloud, `pub-4bf29dd71b1e4f038eeafd32411ce220.r2.dev`) → MinIO (local S3; container `:9000/:9001`, **host** `127.0.0.1:9100/9101`) → local disk (`/app/uploads`).

## Serving

- `GET /api/v1/media/view?path=<storage_path>` — **unauthenticated by design** (mirrors the public `/api/v1/uploads` mount). Resolves any backend: local disk, R2, MinIO.
- `_media_public_url(storage_path)` (used by platforms that fetch media server-side, e.g. TikTok `PULL_FROM_URL`, Meta `image_url`):
  1. `MEDIA_PUBLIC_BASE_URL` (`https://social.cloudless.gr`) + `/api/v1/media/view?path=…`
  2. `/run/tunnel/url` (ad-hoc Cloudflare tunnel) + same path
  3. `R2_PUBLIC_URL` + `storage_path` (direct R2 public URL)
- **Access requirement**: `media/view` must be publicly reachable — Cloudflare Access bypass app `socialauto-media-view` covers `social.cloudless.gr/api/v1/media/view`. Without it platform fetches get a 302 SSO redirect.
- **TikTok `PULL_FROM_URL` requirement**: the media URL's domain must be a TikTok-verified property. `social.cloudless.gr` is covered by the verified `cloudless.gr` property; `pub-*.r2.dev` is NOT — do not point `MEDIA_PUBLIC_BASE_URL` at R2 for TikTok photo posts.

## Platform media constraints

| Platform | Constraint |
|---|---|
| Instagram | `image_url` must be a real JPEG (`image/jpeg`) — PNG-labeled JPEGs are rejected with "image format is not supported". |
| TikTok | Photo posts: `PULL_FROM_URL` only (verified domain). Video: `FILE_UPLOAD` or `PULL_FROM_URL`. |
| LinkedIn | `.pdf` media → `create_document_post` (native carousel). Images → multi-image post. |
| Threads / Facebook | Public `image_url` fetch. |

## Attach model

`posts.media_ids` (UUID[]) references `media_assets` rows. At publish time
`_resolve_media_paths` produces local paths (fetching from R2/MinIO to temp
files when absent) and `_media_public_url` produces fetchable URLs — the
platform handler picks what it needs.
