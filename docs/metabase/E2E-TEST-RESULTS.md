# Marketing AI Image Generation Stack — E2E Test Results

**Date:** 2026-09-02
**Status:** Pipeline structurally validated; full image generation requires model downloads

## Test Artifacts

### 1. Storage Layout
- `storage-user/metadata/` — shared log directory (mounted in n8n, ComfyUI, Metabase)
- `storage-user/output/` — ComfyUI image output
- `storage-user/input/` — ComfyUI input images
- `storage-models/models/` — model storage (checkpoints, upscale, bg-removal)
- Broken `./storage/models/checkpoints` mount removed from docker-compose.yml

### 2. ComfyUI Workflow (API format)
- File: `comfyui-workflows/marketing-pipeline.json`
- Pipeline: CheckpointLoaderSimple → txt2img (KSampler) → VAEDecode → VAEEncode → img2img (KSampler, denoise=0.45) → VAEDecode → SaveImage
- Format: ComfyUI API format (keyed by node ID strings)
- Negative prompt: hardcoded in node "3"
- Positive prompt: injected by `inject_prompt.js` into node "2"
- Validation: submitted to ComfyUI `/prompt` — passes structural validation, fails only on missing checkpoint model

### 3. n8n Workflow
- File: `n8n-workflows/marketing-image-generation.json`
- Triggers: Cron (every 6 hours) + Webhook (manual)
- Pipeline: Ollama Prompt Enhance → Inject Prompt (node /scripts/inject_prompt.js) → Read Injected Workflow (Function node) → Call ComfyUI /prompt → Poll /history → Fetch Image → Wait for Approval → Is Approved? → Post to Twitter / Log Run
- Manual Approval: Wait node with webhook resume
- Log path: `/home/node/.n8n/storage/metadata/run_log.csv` (shared volume)

### 4. Helper Script
- File: `scripts/inject_prompt.js` (Node.js — n8n container has no Python)
- Also: `scripts/inject_prompt.py` (Python — for host-side use)
- Both support UI-format and API-format workflows
- Tested inside n8n container: successfully reads `/workflows/marketing-pipeline.json`, injects prompt, writes `/tmp/marketing_workflow_injected.json`

### 5. Docker Compose Mounts
- n8n: `./comfyui-workflows:/workflows:ro`, `./scripts:/scripts:ro`, `./storage-user/metadata:/home/node/.n8n/storage/metadata`
- ComfyUI: `./storage-user/metadata:/home/user/ComfyUI/metadata`, removed broken `./storage/models/checkpoints`
- Metabase: `./storage-user/metadata:/storage/metadata:ro`

### 6. CSV Log
- File: `storage-user/metadata/run_log.csv`
- Verified readable by Metabase container at `/storage/metadata/run_log.csv`
- Columns: timestamp, prompt, enhanced_prompt, status, posted

## What's Needed Before Full Generation

1. **Download a checkpoint model** to `storage-models/models/checkpoints/` (e.g. `juggernaut_xl.safetensors`)
2. **Optional: Download upscale model** to `storage-models/models/upscale_models/` and add upscale node to workflow
3. **Optional: Download background removal model** to `storage-models/models/background_removal/` and add RemoveBackground node to workflow
4. **Import n8n workflow** via n8n UI: Import → select `n8n-workflows/marketing-image-generation.json`
5. **Configure Twitter API** credentials in `.env` if auto-posting is desired

## Service Health
- ComfyUI: HTTP 200 on port 8000
- n8n: HTTP 200 on port 5678
- Metabase: HTTP 200 on port 3000
