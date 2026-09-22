# Docker Model Runner (DMR) Architecture

## Overview

DMR is the **primary local text/vision/embedding inference engine** for the
SocialAuto stack. It runs as a host-level Docker engine container
(`docker-model-runner`, image `local/model-runner:vllm-cuda-fixed`) with GPU
access on the RTX 3070 (8 GB VRAM, WSL2). Cloudflare Workers AI is the **only**
cloud fallback; every other provider in `PROVIDER_CATALOG` is manual-selection
only.

Workstation CPU/RAM/disk/Docker snapshot (measured): [`CODEMAP.md` § Workstation (OFFICE / WSL)](CODEMAP.md#workstation-office--wsl). Do not duplicate the HW table here.

Two inference backends are active:

- **llama.cpp** — default engine, GGUF quantized models, full GPU offload
  (`-ngl 999`). Serves all production traffic.
- **vLLM 0.27.1** — experimental, safetensors models only
  (`docker.io/ai/smollm2-vllm:latest` at `gpu-memory-utilization 0.25`).
  Provider `dmr-vllm`, manual selection only, never in the auto chain.
- **Diffusers** — `Not Installed` on WSL2/Docker Desktop. Image generation is
  handled by the separate `local-diffusers` container (SD 1.5, port 7860).

## Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│ Host: Windows/WSL2 · RTX 3070 Laptop GPU (8 GB)                      │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ docker-model-runner  (local/model-runner:vllm-cuda-fixed)      │  │
│  │ 127.0.0.1:12435 · RestartPolicy=always                         │  │
│  │ volume: docker-model-runner-models                             │  │
│  │                                                                │  │
│  │  llama.cpp 72874f559 ── Running                                │  │
│  │  ├─ ai/qwen3:8b-q4_K_M   text+chatbot  ~5 GB VRAM              │  │
│  │  ├─ ai/qwen3-vl          vision        ~5 GB VRAM              │  │
│  │  ├─ ai/qwen3-embedding   embeddings    ~1 GB VRAM              │  │
│  │  ├─ ai/smollm3           tiny/fast     ~1.9 GB                 │  │
│  │  └─ ai/llama3.2          legacy        ~2 GB                   │  │
│  │                                                                │  │
│  │  vllm 0.27.1 ── Running (experimental, manual only)            │  │
│  │  └─ docker.io/ai/smollm2-vllm:latest  gpu-mem-util 0.25        │  │
│  │                                                                │  │
│  │  diffusers ── Not Installed (WSL2 unsupported)                 │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ local-diffusers (compose)  SD 1.5 · port 7860 · ~2 GB VRAM     │  │
│  │ Primary local image generation                                 │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
              ▲ host.docker.internal:12435
              │
┌─────────────┴────────────────────────────────────────────────────────┐
│ Compose network (cu130-slim)                                         │
│                                                                      │
│  social-api ──────────────── app/services/dmr.py (unified client)    │
│  social-worker-publishing ── DMR_URL → /engines/llama.cpp/v1         │
│  social-worker-media ──────── DMR_VLLM_URL → /engines/vllm/v1        │
│  social-worker-default ────── semaphore: DMR_MAX_CONCURRENCY=4       │
│  social-worker-messenger                                             │
│  messenger-sidecar ────────── DMR_BASE_URL → :12435 (no /engines)    │
│                                                                      │
│  dmr-watchdog ── wget /engines/v1/models every 60s,                  │
│                  docker restart docker-model-runner after 3 fails    │
│  celery-beat ──── dmr_health.check_dmr_health every 5 min            │
│                   → Redis key dmr:status (TTL 15 min)                │
└──────────────────────────────────────────────────────────────────────┘
              │ circuit breaker (3 failures → 60s open)
              ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Cloudflare Workers AI — ONLY automatic cloud fallback                │
│ Text: @cf/meta/llama-3.1-8b-instruct (and catalog equivalents)       │
└──────────────────────────────────────────────────────────────────────┘
```

## Mermaid

```mermaid
flowchart TB
    subgraph Host["Host — WSL2 · RTX 3070 8GB"]
        subgraph Runner["docker-model-runner :12435"]
            LC["llama.cpp<br/>qwen3:8b · qwen3-vl<br/>qwen3-embedding · smollm3 · llama3.2"]
            VL["vLLM 0.27.1 (experimental)<br/>smollm2-vllm @ 0.25 gpu-mem"]
            DF["diffusers — Not Installed"]
        end
        LD["local-diffusers :7860<br/>SD 1.5 (image gen)"]
    end

    subgraph Compose["cu130-slim compose"]
        API["social-api<br/>app/services/dmr.py"]
        WK["4 workers<br/>(publishing·media·default·messenger)"]
        MS["messenger-sidecar<br/>DMR_BASE_URL"]
        WD["dmr-watchdog<br/>restart on 3 fails"]
        CB["celery-beat<br/>dmr_health → Redis dmr:status"]
    end

    CF["Cloudflare Workers AI<br/>(only auto fallback)"]

    API -->|"DMR_URL :12435/engines/llama.cpp/v1"| Runner
    WK --> Runner
    MS -->|"DMR_BASE_URL :12435"| Runner
    WD -->|"wget /engines/v1/models"| Runner
    WD -.->|"docker restart"| Runner
    CB --> Runner
    API -->|"circuit breaker"| CF
    API -->|"image gen"| LD
```

## Models

| Model | Backend | Role | VRAM | Runtime config |
|-------|---------|------|------|----------------|
| `ai/qwen3:8b-q4_K_M` | llama.cpp | Long-form + schema (`DMR_TEXT_MODEL`) — LinkedIn/Facebook posts, carousel outlines, nested JSON | ~5.1 GB | `context-size 6144`, `keep-alive 5m`, thinking enabled |
| `hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M` | llama.cpp | Mid-tier (`DMR_MID_MODEL` + `DMR_CHATBOT_MODEL`) — short-form copy (Instagram/TikTok/X/Threads/YouTube) + all chatbots. Non-thinking instruct → direct content, ~2× faster than the 8B | ~2.7 GB | `context-size 4096`, `keep-alive 30m` (pinned warm — chatbots are latency-critical) |
| `ai/qwen3-vl` | llama.cpp | Vision (`DMR_VISION_MODEL`) — alt text, smart crop, tagging | ~5 GB | defaults (load on demand only) |
| `ai/qwen3-embedding` | llama.cpp | Embeddings (`DMR_EMBEDDING_MODEL`) for Chroma — 4096 dims | ~1 GB | defaults |
| `ai/smollm3` | llama.cpp | Tiny/fast (`DMR_TINY_MODEL`) — prompts <200 chars, no platform hint | ~1.9 GB | `context-size 4096`, `keep-alive 5m`, `--reasoning-budget 0` |
| `hf.co/Qwen/Qwen3-0.6B-GGUF` | llama.cpp | Speculative-draft candidate for qwen3:8b | ~0.6 GB | **Do not attach** — crashes llama.cpp (`vector::_M_range_check` on draft load, takes target offline) |
| `ai/smollm2` | llama.cpp | Superseded by smollm3 | ~256 MB | kept pulled as rollback |
| `ai/llama3.2` | llama.cpp | Legacy / spare | ~2 GB | defaults |
| `docker.io/ai/smollm2-vllm:latest` | vLLM | Experimental | 0.25 GPU util | **Full ref required** — short name 404s once runtime config exists |
| `ai/stable-diffusion` | diffusers | — | — | Pulled (6.94 GB DDUF) but **cannot run on WSL2** |

## Endpoints

| Access | URL |
|--------|-----|
| Host | `http://localhost:12435` (bound to 127.0.0.1) |
| Containers | `http://host.docker.internal:12435` |
| OpenAI API | `/engines/v1/chat/completions`, `/engines/v1/models`, `/engines/v1/embeddings` |
| Engine-pinned | `/engines/llama.cpp/v1/...`, `/engines/vllm/v1/...` |
| Anthropic API | `/anthropic/v1/messages` |
| Ollama API | `/api/chat`, `/api/tags` |
| Native mgmt | `/models`, `/inference/status`, `/inference/ps`, `/inference/unload` |

## App wiring

| Env var | Value | Consumers |
|---------|-------|-----------|
| `DMR_URL` | `http://host.docker.internal:12435/engines/llama.cpp/v1` | social-api, all 4 workers |
| `DMR_VLLM_URL` | `http://host.docker.internal:12435/engines/vllm/v1` | social-api, workers (manual provider `dmr-vllm`) |
| `DMR_BASE_URL` | `http://host.docker.internal:12435` | messenger-sidecar (no `/engines` suffix) |
| `DMR_TEXT_MODEL` | `ai/qwen3:8b-q4_K_M` | long-form + schema/JSON routing |
| `DMR_MID_MODEL` | `hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M` | short-form platform copy |
| `DMR_CHATBOT_MODEL` | `hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M` | Messenger/WhatsApp/Telegram bots (model_override) |
| `DMR_VISION_MODEL` | `ai/qwen3-vl` | image_enhance, media_ai |
| `DMR_EMBEDDING_MODEL` | `ai/qwen3-embedding` | chroma_client |
| `DMR_TINY_MODEL` | `ai/smollm3` | short-prompt routing in dmr.py |
| `DMR_MAX_CONCURRENCY` | `4` | semaphore inside `app/services/dmr.py` |

`app/services/dmr.py` is the single client for all DMR traffic: shared httpx
pool (loop-aware for Celery prefork), health-check cache, cold-start retry,
model warm-up, platform-aware per-request routing, streaming, tool calling,
VRAM-aware loading, and CLI fallback when HTTP is unreachable.

### Platform-aware routing

`_select_model_by_complexity(prompt, schema, model_override, platform)` picks
the model per request. `platform` flows from `call_inference(..., platform=)`
through `_do_call_inference` → `_call_dmr_chat` → `call_dmr_chat`; generation
endpoints pass `request.platform`, LinkedIn services pass `"linkedin"`, and
chatbots pin the mid model via `model_override=DMR_CHATBOT_MODEL`.

| Request shape | Model | Why |
|---|---|---|
| `model_override` set | override | explicit wins |
| short-form platform (instagram, tiktok, twitter/x, threads, youtube, pinterest) — with or without schema | `DMR_MID_MODEL` (4B) | non-thinking instruct is ~2× faster and stays warm; `json_object` mode constrains decode so the 4B cannot malform flat caption/hashtag schemas |
| long-form platform (linkedin, facebook, blog) or any schema without a platform hint | `DMR_TEXT_MODEL` (8B) | thinking model for professional long copy + nested schemas (carousel outlines) |
| prompt <200 chars, no platform | `DMR_TINY_MODEL` (smollm3) | cheapest route for classification-style tasks |
| everything else | `DMR_TEXT_MODEL` (8B) | safe default |

Chatbots (Messenger, WhatsApp, Telegram) bypass this table entirely — they
always send `model_override=DMR_CHATBOT_MODEL` so the latency-critical reply
path stays on the pinned 4B.

## Runtime configuration

`docker model configure` **replaces** the model's whole runtime config — it
does not merge. Always pass every flag in one call:

```bash
docker model configure --context-size 6144 --keep-alive 5m ai/qwen3:8b-q4_K_M
docker model configure --context-size 4096 --keep-alive 30m hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M
docker model configure --context-size 4096 --keep-alive 5m ai/smollm3 -- --reasoning-budget 0
docker model configure show <model>   # verify
```

Applied configs (verify with `configure show`):

- `ai/qwen3:8b-q4_K_M` → `context-size 6144`, `keep-alive 5m`. Short
  keep-alive now that chatbots moved to the 4B — pinning the 8B would waste
  ~5 GB between content-gen bursts. ctx 6144 still covers the largest
  carousel/schema prompts (~2-3k in, ~1.5k out) and frees KV headroom.
- `hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M` → `context-size 4096`,
  `keep-alive 30m`. Pinned warm for chatbots + short-form copy — the
  latency-critical paths. **ctx must be set explicitly**: the GGUF advertises
  262144 ctx, and llama.cpp would try to allocate a 36 GB KV cache → OOM.
  Instruct-2507 is a non-thinking model — direct `content`, no reasoning
  budget needed.
- `ai/smollm3` → `context-size 4096`, `keep-alive 5m`, `--reasoning-budget 0`.
  SmolLM3 is a thinking model; without the budget flag it burns tokens on
  `reasoning_content` and returns empty `content`.

Important gaps:

- **No `docker` CLI inside containers.** `apply_best_practice_configs()` and
  `configure_speculative_decoding()` in `dmr.py` shell out to `docker model
  configure`, which only exists on the host — they silently no-op in
  containers. Runtime config must be applied from the host.
- **Speculative decoding is currently broken.** Attaching
  `hf.co/Qwen/Qwen3-0.6B-GGUF` as `--speculative-draft-model` for qwen3:8b
  crashes llama.cpp b9879/72874f559 (`vector::_M_range_check` on draft load)
  and takes the target model offline until the draft config is removed.
  The draft model is pulled for a future retry after a runner update.
- **VRAM budget**: pinned 4B ≈ 2.7 GB + 8B @ 6k ctx ≈ 5.1 GB ≈ **7.8 GB
  resident worst-case** (measured 7.76 GB) — ~400 MB headroom on the 8 GB
  card. The 8B unloads ~5 min after each content burst, so steady state is
  just the ~2.7 GB 4B. Vision/embedding loads evict the unpinned 8B as needed.
  local-diffusers needs ~2 GB — overlap with the 8B is bounded by its short
  keep-alive. Unload with `docker model unload --all` if the diffusers path
  OOMs.
- **ComfyUI coexistence**: `social-media-comfyui-gpu` on host `:8000` holds a CUDA context
  even when idle. Measured free VRAM with ComfyUI up can be **<1 GB** — unload or stop
  ComfyUI before loading `qwen3-vl` / dual 8B+vision workloads. Prefer DMR mid/tiny models
  when ComfyUI must stay up.
- **Model list normalization**: `validate_dmr_models()` matches expected refs
  against `/engines/v1/models`, which reports `huggingface.co/...` lowercase
  for `hf.co` refs — the matcher normalizes both sides before suffix-matching.

## Fallback & resilience

- **Text inference**: DMR → Cloudflare Workers AI (only auto fallback).
- **Chatbots**: DMR → Cloudflare Workers AI → static reply.
- **Images**: local-diffusers (SD 1.5) → Cloudflare Workers AI.
- **Circuit breaker**: 3 DMR failures → route to Cloudflare for 60 s.
- **dmr-watchdog** (compose, `docker:27-cli` + host socket): polls
  `/engines/v1/models` every 60 s, restarts the runner after 3 consecutive
  failures. Handles "container up, engine wedged".
- **dmr_health Celery task** (every 5 min, default queue): probes runner,
  validates expected models, publishes to Redis `dmr:status` (TTL 15 min).
- **Persistence**: named volume `docker-model-runner-models`;
  `RestartPolicy=always`.
- **Recovery script**: `scripts/dmr/gpu-runner-recreate.sh` recreates the
  container if Docker Desktop resets it.

## vLLM runner specifics (WSL2)

The GPU runner was created with
`docker model install-runner --backend vllm --gpu cuda --port 12435`, then
patched:

- torch `+cpu` → `+cu126` in `/opt/vllm-env` (baked into the committed image
  `local/model-runner:vllm-cuda-fixed`).
- `VLLM_WSL2_ENABLE_PIN_MEMORY=1` — WSL2 disables pinned memory.
- `VLLM_USE_FLASHINFER_SAMPLER=0` — image lacks `ninja` for FlashInfer JIT.
- Per-model: `docker model configure docker.io/ai/smollm2-vllm:latest
  --gpu-memory-utilization 0.25` — **full ref required**; short names silently
  no-op. Reapply after `install-runner`/`reinstall-runner` recreates the
  container.

## Operations

```bash
docker model status                                  # backends
docker model list                                    # pulled models
docker model ps                                      # loaded in VRAM
curl -sf http://localhost:12435/engines/v1/models    # API health
nvidia-smi --query-gpu=memory.used,memory.total --format=csv
docker model configure --context-size 8192 ai/qwen3:8b-q4_K_M
docker model unload --all                            # free VRAM
scripts/dmr/gpu-runner-recreate.sh                   # heal runner
```

MCP server: `dmr` (`.devin/mcp_config.json`) — status, list, chat, embed,
pull, inspect, configure, generate_image. Skill:
`.devin/skills/docker-model-runner/` with per-operation shell scripts.

## Security

- No authentication on the DMR API — anything that can reach port 12435 can
  run inference. Bound to 127.0.0.1 on the host; containers reach it via
  `host.docker.internal`.
- No prompts or responses leave the machine (privacy-preserving); only the
  Cloudflare fallback path sends data off-box.
