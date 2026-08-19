# Docker Container Stack Workflow Design

**Date:** 2026-08-20  
**Topic:** Marketing-focused AI Image Generation Stack  
**Approach:** ComfyUI-Centric with n8n Triggering  

## 1. Architecture Overview
```
+-------------------+       +------------------+       +-------------------+
|   n8n (5678)      |<----->|   ComfyUI (8000) |<----->|   Shared Storage  |
|  (Scheduler,      |       |  (Generation,    |       |  (/home/user/…    |
|   Approval Gate,  |       |   Img2Img, Upsc, |       |   models, output, |
|   Posting)        |       |   BG Remove)     |       |   input, etc.)    |
+-------------------+       +------------------+       +-------------------+
         ^                         ^                         ^
         |                         |                         |
+--------+--------+    +----------+----------+    +--------+--------+
| Ollama (11435)  |    | Chroma (8001)       |    | Metabase (3000) |
| (LLM for prompt |    | (Vector DB for      |    | (Analytics/Dash)|
|  enhancement,   |    |  prompt embeddings, |    |  – monitor usage,|
|  copywriting)   |    |  style vectors)     |    |  success rates)  |
+-------------------+    +---------------------+    +-------------------+
```
*All services communicate via HTTP/APIs and share data through the mounted storage volumes.*

## 2. Component Responsibilities
| Container | Role in Workflow | Key Details |
|-----------|------------------|-------------|
| **n8n** | Orchestrator, scheduler, approval gate, posting connector | • Triggers workflows on a schedule (cron) or via webhook<br>• Calls ComfyUI API to run a workflow JSON<br>• Waits for completion (polls `/history`)<br>• Presents a manual approval node (you review the generated image in n8n UI)<br>• On approval, calls social‑media APIs (or saves to a queue for later scheduling) |
| **ComfyUI** | Core image generation & post‑processing | • Receives a workflow JSON from n8n<br>• Executes: **Text‑to‑Image → (optional) Image‑to‑Image → UltimateSDUpscale → Background Removal**<br>• Writes intermediate/final outputs to the shared storage (`/home/user/ComfyUI/output`)<br>• Exposes `/prompt` and `/history` REST endpoints for n8n |
| **Ollama** | LLM assistant for prompt refinement | • n8n can send a raw marketing brief to Ollama (e.g., `llama3`) to generate a polished prompt or suggest variations<br>• The refined prompt is then passed to ComfyUI |
| **Chroma** | Vector store for prompt/style embeddings | • Stores embeddings of successful prompts, brand style vectors, or product‑image features<br>• n8n can query Chroma to retrieve similar prompts or style conditioning for IP‑Adapter/ControlNet |
| **Metabase** | Analytics & monitoring | • Connects to the shared storage (or a small SQLite/Postgres log) to track: generation counts, success/failure rates, average processing time, prompt usage<br>• Provides dashboards for marketing performance insights |

## 3. Data Flow (Hybrid Automation)
1. **Schedule / Trigger** – n8n fires (cron or webhook).  
2. **Prompt Preparation** – Optional: n8n calls Ollama to enhance/refine the prompt; optionally queries Chroma for similar‑prompt embeddings.  
3. **ComfyUI Execution** – n8n POSTs the workflow JSON to `http://comfyui:8000/prompt`. ComfyUI runs the full pipeline (txt2img → img2img → upscale → bg‑remove) and writes the final image to `storage/output/`.  
4. **Result Retrieval** – n8n polls `/history/<prompt_id>` until outputs appear, then reads the filename from the shared volume.  
5. **Manual Approval Gate** – n8n displays the image (via a base64 preview or a file‑serve node) and waits for your manual “Approve”/“Reject” action.  
6. **Posting / Scheduling** – On approval, n8n either:  
   * Directly posts to connected social platforms (Twitter, Instagram, Facebook, LinkedIn) using built‑in nodes, **or**  
   * Saves the image and caption to a queue/database for later scheduling (still manual trigger for actual posting).  
7. **Logging & Analytics** – n8n logs each run (timestamp, prompt, outcome) to a simple CSV/JSON file in storage; Metabase reads this file to update dashboards.

## 4. Error Handling & Retry Logic
| Step | Error Detection | Response |
|------|----------------|----------|
| Ollama call | Non‑200 response or empty output | Fall back to original prompt; log warning |
| ComfyUI API | HTTP error or no prompt_id returned | Retry up to 3× with exponential backoff; then alert via n8n email/Slack node |
| Workflow execution | ComfyUI returns error in `/history` (e.g., OOM, missing node) | Mark as failed, notify, do not proceed to approval |
| File retrieval | Output file not found after timeout | Retry polling; after max attempts, fail and notify |
| Approval timeout | No action within configurable window (e.g., 2 h) | Auto‑expire, log as pending, send reminder |
| Posting failure | Social‑media API error | Retry 2×; if still failing, save to “failed posts” queue for manual intervention |
| General | Uncaught exception in n8n workflow | n8n’s built‑in error workflow triggers (alert + dead‑letter queue) |

All containers have `restart: unless-stopped` in docker‑compose, so transient crashes are auto‑recovered.

## 5. Testing & Validation Strategy
| Layer | Test Type | How |
|-------|-----------|-----|
| **Unit** | Individual nodes (Ollama prompt enhancement, Chroma query) | Run n8n workflow fragments with mock data; verify outputs |
| **Integration** | End‑to‑end flow (schedule → ComfyUI → approval → dummy post) | Use a test social‑media webhook (e.g., webhook.site) to capture POST; validate image exists in storage |
| **Performance** | Load test (multiple concurrent prompts) | Use n8n’s concurrent execution limit; monitor GPU/CPU usage via Metabase |
| **Chaos** | Stop one container mid‑flow | Verify restart behavior and that n8n retries/resumes correctly |
| **User Acceptance** | Manual approval gate sanity check | Confirm image preview is clear, approve/reject buttons work as expected |

## 6. Success Criteria
- **Automation level**: Generation + post‑processing fully automatic; requires only one manual click to approve before any posting.
- **Latency**: End‑to‑end (trigger → approved image) ≤ 5 minutes for a typical 1024×1024 pipeline on an RTX 3070.
- **Reliability**: ≥ 95% of scheduled runs produce a valid image (no silent failures).
- **Observability**: Metabase dashboard shows generation counts, failure reasons, average time per step.
- **Scalability**: Adding more prompts or adjusting schedule only requires changing n8n cron; no container rebuild needed.