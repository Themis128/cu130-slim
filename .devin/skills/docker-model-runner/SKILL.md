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

### OpenAI-compatible (primary)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/engines/v1/models` | GET | List loaded models |
| `/engines/v1/chat/completions` | POST | Chat completion |
| `/engines/v1/completions` | POST | Text completion |
| `/engines/v1/embeddings` | POST | Generate embeddings |

### Anthropic-compatible

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/anthropic/v1/messages` | POST | Create message |
| `/anthropic/v1/messages/count_tokens` | POST | Count tokens |

### Ollama-compatible

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/tags` | GET | List models |
| `/api/chat` | POST | Chat |
| `/api/generate` | POST | Generate |
| `/api/embeddings` | POST | Embeddings |

### Image generation (Diffusers)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/engines/diffusers/v1/images/generations` | POST | Generate image from prompt |

> **WSL2/Docker Desktop limitation**: The Diffusers engine is **not available** on
> Docker Desktop/WSL2. `docker model status` shows `diffusers: Not Installed`.
> The `ai/stable-diffusion` model (SDXL, 6.94 GB DDUF) can be pulled and cached
> but cannot run — requests return `503: diffusers is not available on this platform`.
> The Diffusers engine requires **native Linux x86_64 with NVIDIA CUDA**.
>
> **For local GPU image generation on WSL2**, use the `local-diffusers` container
> (SD 1.5) at `http://local-diffusers:7860/v1/images/generations` instead. This
> is the working primary image generation path in SocialAuto.

### DMR native (model management)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/models/create` | POST | Pull/create a model |
| `/models` | GET | List local models |
| `/models/{namespace}/{name}` | GET | Get model details |
| `/models/{namespace}/{name}` | DELETE | Delete a local model |

## CLI commands

```bash
# List pulled models
docker model list

# Pull a new model
docker model pull ai/qwen3:8b-q4_K_M

# Inspect a model
docker model inspect ai/qwen3:8b-q4_K_M

# Configure context size (default 4096 for llama.cpp)
docker model configure --context-size 8192 ai/qwen3:8b-q4_K_M

# Set runtime flags (llama.cpp params)
docker model configure ai/qwen3:8b-q4_K_M -- --temp 0.7 --top-p 0.9

# Run a model interactively
docker model run ai/qwen3:8b-q4_K_M

# Benchmark a model
docker model bench ai/qwen3:8b-q4_K_M

# Show disk usage
docker model df

# Delete a model
docker model rm ai/qwen3:8b-q4_K_M
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

**SDXL note**: The `ai/stable-diffusion` model (6.94 GB DDUF) is pulled and
cached on disk but cannot load into VRAM on WSL2/Docker Desktop — the Diffusers
engine is not installed. It would require ~6GB VRAM if it could run on native
Linux. On WSL2, Local Diffusers (SD 1.5, ~2GB) is the working image gen path.

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

## Scripts

- `scripts/dmr-status.sh` — Check DMR status, loaded models, VRAM usage
- `scripts/dmr-chat.sh` — Quick chat with a DMR model
- `scripts/dmr-pull.sh` — Pull a new model
- `scripts/dmr-embed.sh` — Generate embeddings
- `scripts/dmr-list.sh` — List all local models with details

## Common operations

### Check DMR health

```bash
curl -sf http://localhost:12434/engines/v1/models | python3 -m json.tool
```

### Quick chat test

```bash
curl -s http://localhost:12434/engines/llama.cpp/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"ai/smollm2","messages":[{"role":"user","content":"Hello!"}]}' | jq -r '.choices[0].message.content'
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
