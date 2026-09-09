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
│  Docker Model Runner (port 12434)                          │
│  ├── llama.cpp engine (default, GGUF quantized)            │
│  │   ├── ai/qwen3:8b-q4_K_M   (text, ~5GB VRAM)           │
│  │   ├── ai/qwen3-vl          (vision, ~5GB VRAM)          │
│  │   ├── ai/qwen3-embedding   (embeddings)                 │
│  │   └── ai/smollm2           (tiny/fast, 360M)            │
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
│  DMR_URL = http://host.docker.internal:12434/engines/llama.cpp/v1 │
│  LOCAL_DIFFUSERS_URL = http://local-diffusers:7860         │
└─────────────────────────────────────────────────────────────┘
```

## API endpoints

### Base URLs

| Access from | URL |
|-------------|-----|
| Host | `http://localhost:12434` |
| Containers (Docker Desktop) | `http://model-runner.docker.internal` |
| Containers (Docker Engine) | `http://host.docker.internal:12434` or `http://172.17.0.1:12434` |

> **WSL2 note**: On Docker Desktop/WSL2, the TCP port 12434 may not be
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

| Model | Purpose | VRAM | Quantization |
|-------|---------|------|--------------|
| `ai/qwen3:8b-q4_K_M` | General text inference (primary) | ~5GB | Q4_K_M |
| `ai/qwen3-vl` | Vision (alt text, smart crop, tagging) | ~5GB | Q4_K_M |
| `ai/qwen3-embedding` | Chroma vector embeddings | low | - |
| `ai/smollm2` | Tiny/fast tasks (360M) | 256MB | IQ2_XXS/Q4_K_M |

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

## VRAM management

The RTX 3070 has 8GB VRAM. DMR models auto-load on request and unload when idle.

| Model | VRAM when loaded |
|-------|-----------------|
| qwen3:8b-q4_K_M | ~5GB |
| qwen3-vl | ~5GB |
| qwen3-embedding | ~1GB |
| smollm2 | ~256MB |
| stable-diffusion (SDXL) | ~6GB (cannot run on WSL2) |
| Local Diffusers SD 1.5 | ~2GB (works on WSL2) |

**Important**: DMR models share GPU with the local-diffusers container. When
qwen3:8b and SD 1.5 are both loaded, total VRAM usage is ~7GB (fits in 8GB).
DMR auto-unloads models after idle, so simultaneous loading is rare.

## Inference engines

| Engine | Best for | Model format | GPU | WSL2? |
|--------|----------|--------------|-----|-------|
| llama.cpp | Local dev, resource efficiency | GGUF (quantized) | All platforms | Yes |
| vLLM | Production, high throughput | Safetensors | NVIDIA (Linux/WSL2) | Yes (Docker Desktop 4.54+) |
| Diffusers | Image generation (Stable Diffusion) | DDUF | NVIDIA (Linux only) | **No** |

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

The DMR MCP server (`scripts/dmr-mcp-server.py`) exposes **20 tools** to AI
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

**Monitoring & management** (4 tools):
- `dmr_ps` — List running (loaded in memory) models
- `dmr_df` — Show disk usage
- `dmr_unload` — Unload models from memory
- `dmr_bench` — Benchmark model performance (TPS)
- `dmr_logs` — Fetch DMR logs

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
- `DMR_BASE` — Base URL (default: `http://localhost:12434`)
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

## Common operations

### Check DMR health

```bash
curl -sf http://localhost:12434/engines/v1/models | python3 -m json.tool
# OR
docker model status
```

### Quick chat test

```bash
curl -s http://localhost:12434/engines/llama.cpp/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"ai/smollm2","messages":[{"role":"user","content":"Hello!"}]}' | jq -r '.choices[0].message.content'
```

### Vision (multimodal)

```bash
curl -s http://localhost:12434/engines/llama.cpp/v1/chat/completions \
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
curl -s http://localhost:12434/engines/llama.cpp/v1/embeddings \
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
