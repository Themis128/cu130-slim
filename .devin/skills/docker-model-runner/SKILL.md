# Docker Model Runner (DMR)

Local-first LLM inference via Docker Model Runner. DMR runs AI models locally
using Docker CLI commands and provides OpenAI, Anthropic, and Ollama-compatible
APIs for easy app integration. It is the **primary text inference provider** for
the Cloudless SocialAuto stack, with Cloudflare Workers AI as the only cloud
fallback.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Host (RTX 3070 8GB VRAM)                                  │
│                                                             │
│  Docker Model Runner (port 12435)                          │
│  ├── llama.cpp engine (default, GGUF quantized)            │
│  │   ├── ai/qwen3:8b-q4_K_M   (long-form + schema, ~5.1GB)│
│  │   ├── Qwen3-4B-Instruct    (short-form + chatbots, ~2.7GB)│
│  │   ├── ai/qwen3-vl          (vision, ~5GB VRAM)          │
│  │   ├── ai/qwen3-embedding   (embeddings)                 │
│  │   └── ai/smollm3           (tiny/fast, 3.1B)            │
│  └── Diffusers engine (NOT AVAILABLE on WSL2/Docker Desktop)│
│      └── ai/stable-diffusion (SDXL, 6.94GB DDUF, pulled)   │
│      └── Requires native Linux x86_64 + NVIDIA CUDA        │
│                                                             │
│  Local Diffusers container (separate, port 7860)           │
│  └── stable-diffusion-v1-5/stable-diffusion-v1-5           │
│      (primary image gen, ~2GB VRAM, fp16, WORKS on WSL2)   │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  Containers (social-api, workers)                           │
│  DMR_URL = http://host.docker.internal:12435/engines/llama.cpp/v1 │
│  LOCAL_DIFFUSERS_URL = http://local-diffusers:7860         │
└─────────────────────────────────────────────────────────────┘
```

## API endpoints

### Base URLs

| Access from | URL |
|-------------|-----|
| Host | `http://localhost:12435` |
| Containers (Docker Desktop) | `http://model-runner.docker.internal` |
| Containers (Docker Engine) | `http://host.docker.internal:12435` or `http://172.17.0.1:12435` |

> **WSL2 note**: On Docker Desktop/WSL2, the TCP port 12435 may not be
> reachable from the host. Use `docker model` CLI commands as fallback.
> All MCP tools and scripts automatically fall back to CLI when the API
> is unreachable.

### OpenAI-compatible (primary)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/engines/v1/models` | GET | List loaded models |
| `/engines/v1/models/{namespace}/{name}` | GET | Retrieve model details |
| `/engines/v1/chat/completions` | POST | Chat completion |
| `/engines/v1/completions` | POST | Text completion |
| `/engines/v1/embeddings` | POST | Generate embeddings |

Supported parameters: `model`, `messages`, `prompt`, `max_tokens`,
`temperature`, `top_p`, `stream`, `stop`, `presence_penalty`,
`frequency_penalty`, `response_format` (JSON mode), `logprobs`.

> You can optionally include the engine name in the path:
> `/engines/llama.cpp/v1/chat/completions`

### Anthropic-compatible

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/anthropic/v1/messages` | POST | Create message |
| `/anthropic/v1/messages/count_tokens` | POST | Count tokens |

Supported parameters: `model`, `messages`, `max_tokens`, `temperature`,
`top_p`, `top_k`, `stream`, `stop_sequences`, `system`.

### Ollama-compatible

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/tags` | GET | List models |
| `/api/show` | POST | Show model info |
| `/api/chat` | POST | Chat |
| `/api/generate` | POST | Generate completion |
| `/api/embeddings` | POST | Embeddings |

### Image generation (Diffusers)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/engines/diffusers/v1/images/generations` | POST | Generate image |

> **WSL2/Docker Desktop limitation**: Diffusers engine is **not available**.
> Use the `local-diffusers` container (SD 1.5) at
> `http://local-diffusers:7860/v1/images/generations` instead.

### DMR native (model management)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/models/create` | POST | Pull/create a model |
| `/models` | GET | List local models |
| `/models/{namespace}/{name}` | GET | Get model details |
| `/models/{namespace}/{name}` | DELETE | Delete a local model |
| `/models/{name}/tag` | POST | Tag a model |
| `/models/{name}/push` | POST | Push model to registry |

### Management & monitoring

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/inference/status` | GET | Backend health/status |
| `/inference/ps` | GET | List running (loaded) models |
| `/inference/df` | GET | Disk usage |
| `/inference/unload` | POST | Unload models from memory |
| `/inference/_configure` | POST | Configure model runtime |

## CLI commands

```bash
# List pulled models
docker model list

# Pull a new model
docker model pull ai/qwen3:8b-q4_K_M

# Search for models (Docker Hub + HuggingFace)
docker model search llama
docker model search --json --source huggingface qwen
docker model search --source all --limit 50

# Inspect a model
docker model inspect ai/qwen3:8b-q4_K_M
docker model inspect --remote ai/llama3.2  # without pulling

# Show model info (human-readable)
docker model show ai/qwen3:8b-q4_K_M

# Configure context size (default 4096 for llama.cpp)
docker model configure --context-size 8192 ai/qwen3:8b-q4_K_M

# Set runtime flags (llama.cpp params)
docker model configure ai/qwen3:8b-q4_K_M -- --temp 0.7 --top-p 0.9

# Run a model interactively
docker model run ai/qwen3:8b-q4_K_M

# Benchmark a model (tokens/sec at different concurrency)
docker model bench ai/qwen3:8b-q4_K_M
docker model bench --json --concurrency 1,2,4 --duration 60s ai/qwen3:8b-q4_K_M

# List running (loaded) models
docker model ps

# Show disk usage
docker model df

# Unload models from memory
docker model unload --all
docker model unload ai/qwen3:8b-q4_K_M
docker model unload --backend llama.cpp

# Fetch logs
docker model logs
docker model logs --no-engines
docker model logs -f  # follow

# Tag a model
docker model tag ai/smollm2 myorg/smollm2:latest

# Push a model to registry
docker model push myorg/mymodel:latest

# Remove a model
docker model rm ai/old-model
docker model rm -f ai/old-model  # force

# Remove ALL models
docker model purge
docker model purge -f  # force

# Fetch request/response logs
docker model requests
docker model requests -f  # follow
docker model requests --model ai/smollm2

# Show DMR version
docker model version

# Check if DMR is running
docker model status
```

## Current models

| Model | Role | VRAM | Runtime config |
|-------|------|------|----------------|
| `ai/qwen3:8b-q4_K_M` | `DMR_TEXT_MODEL` — long-form (LinkedIn/Facebook) + schema/JSON | ~5.1GB | ctx 6144, keep-alive 5m |
| `hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M` | `DMR_MID_MODEL` + `DMR_CHATBOT_MODEL` — short-form platforms + all chatbots (non-thinking, ~90 TPS) | ~2.7GB | ctx 4096, keep-alive 30m |
| `ai/qwen3-vl` | `DMR_VISION_MODEL` — alt text, smart crop, tagging | ~5GB | on-demand only |
| `ai/qwen3-embedding` | `DMR_EMBEDDING_MODEL` — Chroma vectors (4096 dims) | ~1GB | on-demand |
| `ai/smollm3` | `DMR_TINY_MODEL` — <200-char prompts | ~1.9GB | ctx 4096, keep-alive 5m, `--reasoning-budget 0` |
| `ai/smollm2` | Superseded rollback (360M) | 256MB | - |
| `ai/llama3.2` | Spare / manual selection | ~2GB | - |

## Platform-aware routing

`app/services/dmr.py::_select_model_by_complexity(prompt, schema,
model_override, platform)` picks the model per request. `platform` flows from
`call_inference(..., platform=)` → `call_dmr_chat`; generation endpoints pass
`request.platform`, chatbots pin `model_override=DMR_CHATBOT_MODEL`.

| Request shape | Model |
|---|---|
| `model_override` set | override wins |
| short-form platform (instagram, tiktok, x, threads, youtube, pinterest) — with or without schema | `DMR_MID_MODEL` (4B — `json_object` decode guarantees valid JSON) |
| long-form platform (linkedin, facebook, blog) or schema without platform | `DMR_TEXT_MODEL` (8B thinking) |
| prompt <200 chars, no platform | `DMR_TINY_MODEL` (smollm3) |
| everything else | `DMR_TEXT_MODEL` (8B) |

## Configuration

### Context size

```bash
# Set context to 8192 tokens (more memory, longer conversations)
docker model configure --context-size 8192 ai/qwen3:8b-q4_K_M

# Reset to default
docker model configure --context-size -1 ai/qwen3:8b-q4_K_M
```

| Context | Use case | Memory impact |
|---------|----------|---------------|
| 2,048 | Simple queries, short code | Low |
| 4,096 | Standard conversations (default) | Moderate |
| 8,192 | Long conversations, larger files | Higher |
| 16,384+ | Extended documents, multi-file | High |

### Runtime flags (llama.cpp)

```bash
# Deterministic (code generation)
docker model configure ai/qwen3:8b-q4_K_M -- --temp 0 --top-k 1

# Creative (storytelling)
docker model configure ai/qwen3:8b-q4_K_M -- --temp 1.2 --top-p 0.95

# Partial GPU offload (limited VRAM)
docker model configure ai/qwen3:8b-q4_K_M -- --n-gpu-layers 20

# Multi-core optimization
docker model configure ai/qwen3:8b-q4_K_M -- --threads 8 --batch-size 1024
```

### Compose integration

```yaml
models:
  llm:
    model: ai/qwen3:8b-q4_K_M
    context_size: 8192
    runtime_flags:
      - "--temp"
      - "0.7"
      - "--top-p"
      - "0.9"
```

### Config is ephemeral — watchdog reapplies it

`docker model configure` settings live **in runner memory only** — they are
wiped on EVERY `docker restart docker-model-runner`, Docker Desktop reset, or
WSL shutdown (verified: `configure show` returns `[]` after restart). They are
NOT in the models volume.

The `dmr-watchdog` compose service closes this gap: it mounts the
`docker-model` CLI plugin (`/usr/local/lib/docker/cli-plugins/docker-model`,
which resolves to the same path inside docker-desktop) and polls the runner's
`StartedAt` every 60s — on change, it reapplies the canonical configs:

```bash
docker-model configure --context-size 6144 --keep-alive 5m ai/qwen3:8b-q4_K_M
docker-model configure --context-size 4096 --keep-alive 30m hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M
docker-model configure --context-size 4096 --keep-alive 5m ai/smollm3 -- --reasoning-budget 0
```

Manual reapply: `scripts/dmr-configure.sh`. Verify: `scripts/dmr-configure.sh show`.

> **Gotcha**: `configure` REPLACES the whole per-model config — pass every
> flag in one call. And hf.co GGUFs (like the 4B) NEED explicit
> `--context-size` — their native ctx (262144) → llama.cpp tries to allocate
> a 36GB KV cache → OOM on load.

## VRAM management

The RTX 3070 has 8GB VRAM. DMR models auto-load on request and unload when idle.

| Model | VRAM when loaded |
|-------|-----------------|
| qwen3:8b-q4_K_M (ctx 6144) | ~5.1GB |
| Qwen3-4B-Instruct (ctx 4096) | ~2.7GB |
| qwen3-vl | ~5GB |
| qwen3-embedding | ~1GB |
| smollm3 | ~1.9GB |
| smollm2 (rollback) | ~256MB |
| stable-diffusion (SDXL) | ~6GB (cannot run on WSL2) |
| Local Diffusers SD 1.5 | ~2GB (works on WSL2) |

**Measured worst case**: 4B pinned (2.7GB) + 8B resident (5.1GB) ≈ **7.8GB**
of 8GB — they fit together, so keep-alive never causes OOM. Vision (~5GB) or
embeddings (~1GB) loading evicts the unpinned 8B (keep-alive 5m); the 4B
stays resident for chatbots.

**Important**: DMR models share GPU with the local-diffusers container.
Before an SD 1.5 generation run, unload DMR models
(`docker model unload --all`) if VRAM is tight.

## Inference engines

| Engine | Best for | Model format | GPU | WSL2? |
|--------|----------|--------------|-----|-------|
| llama.cpp | Local dev, resource efficiency | GGUF (quantized) | All platforms | Yes |
| vLLM | Production, high throughput | Safetensors | NVIDIA (Linux/WSL2) | Yes (Docker Desktop 4.54+) |
| Diffusers | Image generation (Stable Diffusion) | DDUF | NVIDIA (Linux only) | **No** |

### vLLM operational notes (verified Sept 2026)

- **Configure with the FULL model ref**: `docker model configure
  docker.io/ai/smollm2-vllm:latest --gpu-memory-utilization 0.25`. The short
  name (`ai/smollm2-vllm`) exits 0 but silently no-ops — verify with
  `docker model configure show <model>`.
- **Once a model has a runtime config, the API resolves ONLY the full ref**:
  `ai/smollm2-vllm` → 404, `docker.io/ai/smollm2-vllm:latest` → 200. The
  app's `dmr-vllm` provider defaults to the full ref for this reason.
- **VRAM contention**: vLLM defaults to `gpu-memory-utilization 0.92` and
  fails with `Free memory ... is less than desired GPU memory utilization`
  when llama.cpp models hold VRAM. 0.25 (~2GB) works for smollm2-vllm
  alongside llama.cpp; unload GGUF models (`docker model unload --all`)
  before loading larger vLLM models.
- Runner-level config (e.g. `docker model configure --gpu-memory-utilization`
  on the runner) is lost when `install-runner`/`reinstall-runner` recreates
  the `docker-model-runner` container — reapply per-model configs after.

## Fallback chain

```
Text:  DMR (local) → Cloudflare Workers AI (cloud, ONLY fallback)
Image: Local Diffusers (local GPU) → Cloudflare Workers AI (cloud, ONLY fallback)
```

Other cloud providers (Groq, Gemini, Mistral, Cohere, OpenRouter, NVIDIA,
HuggingFace, OpenAI, SambaNova) are in PROVIDER_CATALOG for manual selection
but NOT in the automatic fallback chain.

## Security

- DMR API is **not authenticated** — any client that can reach it can use it
- On Linux, DMR runs inside a container (isolation boundary)
- On macOS/Windows, engines run in a sandboxed environment
- No prompt content or responses are collected (privacy-preserving)

## MCP server

The DMR MCP server (`scripts/dmr-mcp-server.py`) exposes **26 tools** to AI
agents via JSON-RPC over stdio. It automatically falls back to `docker model`
CLI commands when the HTTP API is unreachable (common on WSL2).

### Tool categories

**Model management** (9 tools):
- `dmr_status` — Check DMR health, backend status, loaded models
- `dmr_list` — List all local (pulled) models
- `dmr_pull` — Pull a model from Docker Hub or HuggingFace
- `dmr_inspect` — Inspect a model's details (local or remote)
- `dmr_rm` — Remove one or more local models
- `dmr_tag` — Tag a local model with a new name
- `dmr_push` — Push a model to a registry
- `dmr_search` — Search for models on Docker Hub and HuggingFace
- `dmr_purge` — Remove ALL local models

**Inference** (7 tools):
- `dmr_chat` — OpenAI-compatible chat completion (JSON mode, tool calling)
- `dmr_completion` — OpenAI-compatible text completion
- `dmr_embed` — Generate embeddings
- `dmr_vision` — Multimodal vision (image + text prompt)
- `dmr_ollama_chat` — Ollama-compatible chat
- `dmr_anthropic` — Anthropic-compatible messages
- `dmr_generate_image` — Diffusers image generation

**Monitoring & management** (10 tools):
- `dmr_ps` — List running (loaded in memory) models
- `dmr_df` — Show disk usage
- `dmr_unload` — Unload models from memory
- `dmr_bench` — Benchmark model performance (TPS)
- `dmr_logs` — Fetch DMR logs
- `dmr_configure` — Set model runtime config (context-size, keep-alive, thinking, flags)
- `dmr_configure_show` — Show current runtime configs (remember: wiped on restart)
- `dmr_vram` — GPU name, VRAM used/free/total, utilization, power
- `dmr_validate` — Check all SocialAuto-expected models are present
- `dmr_route` — Preview platform-aware model routing (mirrors `_select_model_by_complexity`)

### MCP config

```json
{
  "mcpServers": {
    "dmr": {
      "command": "python3",
      "args": ["/home/tbaltzakis/cu130-slim/.devin/skills/docker-model-runner/scripts/dmr-mcp-server.py"]
    }
  }
}
```

Environment variables:
- `DMR_BASE` — Base URL (default: `http://localhost:12435`)
- `DMR_TIMEOUT` — Request timeout in seconds (default: 120)

## Scripts

| Script | Description |
|--------|-------------|
| `scripts/dmr-status.sh` | Check DMR status, loaded models, VRAM usage |
| `scripts/dmr-chat.sh` | Quick chat with a DMR model (OpenAI API) |
| `scripts/dmr-completion.sh` | Text completion (OpenAI /v1/completions) |
| `scripts/dmr-embed.sh` | Generate embeddings |
| `scripts/dmr-vision.sh` | Vision request (image + text prompt) |
| `scripts/dmr-ollama.sh` | Ollama-compatible chat |
| `scripts/dmr-anthropic.sh` | Anthropic-compatible messages |
| `scripts/dmr-pull.sh` | Pull a new model |
| `scripts/dmr-list.sh` | List all local models with details |
| `scripts/dmr-search.sh` | Search for models on Docker Hub and HuggingFace |
| `scripts/dmr-ps.sh` | List running (loaded) models |
| `scripts/dmr-df.sh` | Show disk usage |
| `scripts/dmr-unload.sh` | Unload models from memory |
| `scripts/dmr-bench.sh` | Benchmark a model's performance |
| `scripts/dmr-logs.sh` | Fetch DMR logs |
| `scripts/dmr-rm.sh` | Remove a local model |
| `scripts/dmr-tag.sh` | Tag a model |
| `scripts/dmr-push.sh` | Push a model to a registry |
| `scripts/dmr-configure.sh` | Apply canonical runtime configs (also `show`) — re-run after any runner restart |
| `scripts/dmr-vram.sh` | GPU VRAM, utilization, power + budget guide |
| `scripts/dmr-route.sh` | Preview platform-aware routing without sending a request |
| `scripts/dmr-validate.sh` | Check all expected models are pulled |
| `scripts/dmr-warmup.sh` | Warm the 4B + 8B hot-path models |

## Common operations

### Check DMR health

```bash
curl -sf http://localhost:12435/engines/v1/models | python3 -m json.tool
# OR
docker model status
```

### Quick chat test

```bash
curl -s http://localhost:12435/engines/llama.cpp/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"ai/smollm2","messages":[{"role":"user","content":"Hello!"}]}' | jq -r '.choices[0].message.content'
```

### Vision (multimodal)

```bash
curl -s http://localhost:12435/engines/llama.cpp/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "ai/qwen3-vl",
    "messages": [{"role": "user", "content": [
      {"type": "text", "text": "What is in this image?"},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
    ]}]
  }' | jq -r '.choices[0].message.content'
```

### Generate embeddings

```bash
curl -s http://localhost:12435/engines/llama.cpp/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"ai/qwen3-embedding","input":"test text"}' | jq '.data[0].embedding[:5]'
```

### Pull a new model from Docker Hub

```bash
docker model pull ai/llama3.2
```

### Pull from Hugging Face

```bash
docker model pull hf.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF
```

### Search for models

```bash
docker model search --json llama
docker model search --json --source huggingface qwen --limit 10
```

### Benchmark a model

```bash
docker model bench --json ai/qwen3:8b-q4_K_M
```

### Free VRAM by unloading models

```bash
docker model unload --all
docker model unload ai/qwen3:8b-q4_K_M
```

## References

- [DMR REST API reference](https://docs.docker.com/ai/model-runner/api-reference/)
- [Docker Model Runner docs](https://docs.docker.com/ai/model-runner)
- [GitHub: docker/model-runner](https://github.com/docker/model-runner)
- [DeepWiki: Model Management Endpoints](https://deepwiki.com/docker/model-runner/8.1-model-management-endpoints)
- [DeepWiki: Inference Endpoints](https://deepwiki.com/docker/model-runner/8.2-inference-endpoints)
- [DeepWiki: Management Endpoints](https://deepwiki.com/docker/model-runner/8.3-management-endpoints)
