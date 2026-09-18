#!/usr/bin/env python3
"""Docker Model Runner MCP Server.

A lightweight MCP (Model Context Protocol) server that wraps the Docker Model
Runner REST API and CLI, exposing model management, inference, and monitoring
as MCP tools.

Runs as a stdio-based MCP server. No external dependencies beyond the Python
standard library — communicates via JSON-RPC over stdin/stdout.

Usage in MCP config:
{
  "mcpServers": {
    "dmr": {
      "command": "python3",
      "args": ["/path/to/dmr-mcp-server.py"]
    }
  }
}

Tools exposed (25):
  Model management:
    - dmr_status: Check DMR health and list loaded models
    - dmr_list: List all local (pulled) models
    - dmr_pull: Pull a new model from Docker Hub or HuggingFace
    - dmr_inspect: Inspect a model's details
    - dmr_rm: Remove a local model
    - dmr_tag: Tag a model
    - dmr_push: Push a model to a registry
    - dmr_search: Search for models on Docker Hub and HuggingFace
    - dmr_purge: Remove all local models

  Inference:
    - dmr_chat: Send a chat completion request (OpenAI-compatible)
    - dmr_completion: Send a text completion request (OpenAI-compatible)
    - dmr_embed: Generate embeddings
    - dmr_vision: Send a multimodal vision request (image + text)
    - dmr_ollama_chat: Send a chat via Ollama-compatible API
    - dmr_anthropic: Send a message via Anthropic-compatible API
    - dmr_generate_image: Generate an image from a text prompt (Diffusers)

  Monitoring & management:
    - dmr_ps: List running (loaded) models
    - dmr_df: Show DMR disk usage
    - dmr_unload: Unload running models from memory
    - dmr_bench: Benchmark a model's performance
    - dmr_logs: Fetch DMR logs

  Runtime configuration (host-side, applies to live runner):
    - dmr_configure: Set context-size/keep-alive/mode/think/flags (REPLACES config)
    - dmr_configure_show: Show effective runtime config

  SocialAuto helpers:
    - dmr_vram: GPU VRAM/utilization via nvidia-smi
    - dmr_validate: Check all app-expected models are pulled
    - dmr_route: Preview platform-aware model routing decision

References:
  - https://docs.docker.com/ai/model-runner/api-reference/
  - https://github.com/docker/model-runner
  - https://deepwiki.com/docker/model-runner
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

DMR_BASE = os.environ.get("DMR_BASE", "http://localhost:12435")
DMR_TIMEOUT = int(os.environ.get("DMR_TIMEOUT", "120"))

# Platform routing — mirrors LONG_FORM_PLATFORMS / SHORT_FORM_PLATFORMS in
# social-automation/backend/app/services/dmr.py (_select_model_by_complexity).
LONG_FORM_PLATFORMS = frozenset({"linkedin", "facebook", "blog", "article"})
SHORT_FORM_PLATFORMS = frozenset({
    "instagram", "tiktok", "twitter", "x", "threads", "youtube", "pinterest",
})

# Expected models — mirrors config.py defaults; env can override.
EXPECTED_MODELS = {
    "text": os.environ.get("DMR_TEXT_MODEL", "ai/qwen3:8b-q4_K_M"),
    "mid": os.environ.get("DMR_MID_MODEL", "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M"),
    "tiny": os.environ.get("DMR_TINY_MODEL", "ai/smollm3"),
    "chatbot": os.environ.get("DMR_CHATBOT_MODEL", "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M"),
    "vision": os.environ.get("DMR_VISION_MODEL", "ai/qwen3-vl"),
    "embedding": os.environ.get("DMR_EMBEDDING_MODEL", "ai/qwen3-embedding"),
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _api_get(path: str, timeout: int | None = None) -> dict[str, Any]:
    """Make a GET request to DMR API."""
    url = f"{DMR_BASE}{path}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout or DMR_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": f"DMR API not reachable: {e}"}
    except Exception as e:
        return {"error": str(e)}


def _api_post(path: str, body: dict[str, Any], timeout: int | None = None) -> dict[str, Any]:
    """Make a POST request to DMR API."""
    url = f"{DMR_BASE}{path}"
    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout or DMR_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": f"DMR API request failed: {e}"}
    except Exception as e:
        return {"error": str(e)}


def _api_delete(path: str, timeout: int | None = None) -> dict[str, Any]:
    """Make a DELETE request to DMR API."""
    url = f"{DMR_BASE}{path}"
    try:
        req = urllib.request.Request(url, method="DELETE")
        with urllib.request.urlopen(req, timeout=timeout or DMR_TIMEOUT) as resp:
            try:
                return json.loads(resp.read().decode("utf-8"))
            except json.JSONDecodeError:
                return {"status": "deleted"}
    except urllib.error.URLError as e:
        return {"error": f"DMR API request failed: {e}"}
    except Exception as e:
        return {"error": str(e)}


def _docker_model(*args: str, timeout: int = 300) -> dict[str, Any]:
    """Run a `docker model` CLI command."""
    try:
        result = subprocess.run(
            ["docker", "model", *args],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip() or f"Command failed with code {result.returncode}"}
        # Try to parse JSON output, fall back to raw text
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"output": result.stdout.strip()}
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out ({timeout}s)"}
    except FileNotFoundError:
        return {"error": "docker CLI not found"}
    except Exception as e:
        return {"error": str(e)}


def _text_result(text: str) -> dict[str, Any]:
    """Build a standard MCP text content result."""
    return {"content": [{"type": "text", "text": text}]}


def _error_result(msg: str) -> dict[str, Any]:
    """Build an MCP error result."""
    return {"content": [{"type": "text", "text": f"Error: {msg}"}], "isError": True}


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    # --- Model management ---
    {
        "name": "dmr_status",
        "description": "Check Docker Model Runner health, backend status, and list currently loaded models. No parameters needed.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dmr_list",
        "description": "List all local (pulled) DMR models with details. No parameters needed.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dmr_pull",
        "description": "Pull a new model from Docker Hub (ai/<name>) or HuggingFace (hf.co/<org>/<model>). May take several minutes for large models.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model name (e.g. 'ai/llama3.2', 'hf.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF')"},
            },
            "required": ["model"],
        },
    },
    {
        "name": "dmr_inspect",
        "description": "Inspect a model's details (format, tags, config). Use --remote to inspect a model without pulling it first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model name to inspect"},
                "remote": {"type": "boolean", "description": "Inspect remote model without pulling", "default": False},
            },
            "required": ["model"],
        },
    },
    {
        "name": "dmr_rm",
        "description": "Remove one or more local models from disk.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "models": {"type": "array", "items": {"type": "string"}, "description": "Model names to remove"},
                "force": {"type": "boolean", "description": "Force removal even if in use", "default": False},
            },
            "required": ["models"],
        },
    },
    {
        "name": "dmr_tag",
        "description": "Tag a local model with a new name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Source model name"},
                "target": {"type": "string", "description": "Target tag (e.g. 'myorg/mymodel:latest')"},
            },
            "required": ["source", "target"],
        },
    },
    {
        "name": "dmr_push",
        "description": "Push a local model to Docker Hub or HuggingFace registry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model name to push"},
            },
            "required": ["model"],
        },
    },
    {
        "name": "dmr_search",
        "description": "Search for models on Docker Hub (ai/ namespace) and HuggingFace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term (empty for all available)"},
                "source": {"type": "string", "enum": ["all", "dockerhub", "huggingface"], "default": "all"},
                "limit": {"type": "integer", "description": "Max results (default 32)", "default": 32},
            },
            "required": [],
        },
    },
    {
        "name": "dmr_purge",
        "description": "Remove ALL local models. Use with caution.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "description": "Force removal without confirmation", "default": False},
            },
            "required": [],
        },
    },
    # --- Inference ---
    {
        "name": "dmr_chat",
        "description": "Send a chat completion request to a DMR model via OpenAI-compatible API. Models load on-demand if pulled. Supports streaming, JSON mode, and tool calling.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model identifier (e.g. 'ai/smollm3', 'ai/qwen3:8b-q4_K_M')", "default": "ai/smollm3"},
                "message": {"type": "string", "description": "The user message / prompt"},
                "system": {"type": "string", "description": "Optional system prompt"},
                "max_tokens": {"type": "integer", "description": "Max tokens to generate", "default": 512},
                "temperature": {"type": "number", "description": "Sampling temperature (0.0-2.0)", "default": 0.7},
                "top_p": {"type": "number", "description": "Nucleus sampling (0.0-1.0)", "default": 1.0},
                "json_mode": {"type": "boolean", "description": "Force JSON output format", "default": False},
                "stop": {"type": "array", "items": {"type": "string"}, "description": "Stop sequences"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "dmr_completion",
        "description": "Send a text completion request (not chat) to a DMR model via OpenAI-compatible /v1/completions endpoint.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model identifier", "default": "ai/smollm3"},
                "prompt": {"type": "string", "description": "The prompt text"},
                "max_tokens": {"type": "integer", "description": "Max tokens to generate", "default": 256},
                "temperature": {"type": "number", "description": "Sampling temperature (0.0-2.0)", "default": 0.7},
                "top_p": {"type": "number", "description": "Nucleus sampling (0.0-1.0)", "default": 1.0},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "dmr_embed",
        "description": "Generate embeddings for text using a DMR embedding model.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to embed"},
                "model": {"type": "string", "description": "Embedding model", "default": "ai/qwen3-embedding"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "dmr_vision",
        "description": "Send a multimodal vision request (image + text prompt) to a vision-capable DMR model (e.g. ai/qwen3-vl).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Vision model identifier", "default": "ai/qwen3-vl"},
                "image_path": {"type": "string", "description": "Absolute path to image file on disk"},
                "prompt": {"type": "string", "description": "Question or instruction about the image"},
                "max_tokens": {"type": "integer", "description": "Max tokens to generate", "default": 512},
            },
            "required": ["image_path", "prompt"],
        },
    },
    {
        "name": "dmr_ollama_chat",
        "description": "Send a chat via Ollama-compatible API (/api/chat). Useful for tools built for Ollama.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model identifier", "default": "ai/smollm3"},
                "message": {"type": "string", "description": "The user message"},
                "stream": {"type": "boolean", "description": "Enable streaming (default false)", "default": False},
            },
            "required": ["message"],
        },
    },
    {
        "name": "dmr_anthropic",
        "description": "Send a message via Anthropic-compatible API (/anthropic/v1/messages). Useful for tools built for Claude.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model identifier", "default": "ai/smollm3"},
                "message": {"type": "string", "description": "The user message"},
                "system": {"type": "string", "description": "Optional system prompt"},
                "max_tokens": {"type": "integer", "description": "Max tokens to generate", "default": 1024},
                "temperature": {"type": "number", "description": "Sampling temperature (0.0-1.0)", "default": 0.7},
            },
            "required": ["message"],
        },
    },
    {
        "name": "dmr_generate_image",
        "description": "Generate an image from a text prompt using the Diffusers backend (requires NVIDIA GPU on native Linux). Not available on WSL2/Docker Desktop.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Text description of the image to generate"},
                "model": {"type": "string", "description": "Model identifier (e.g. 'stable-diffusion:Q4')", "default": "stable-diffusion:Q4"},
                "size": {"type": "string", "description": "Image dimensions (e.g. '512x512')", "default": "512x512"},
            },
            "required": ["prompt"],
        },
    },
    # --- Monitoring & management ---
    {
        "name": "dmr_ps",
        "description": "List all currently running (loaded in memory) models with their backend, mode, and last-used time.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dmr_df",
        "description": "Show Docker Model Runner disk usage (model storage and backend install directory).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dmr_unload",
        "description": "Unload running models from memory to free VRAM. Can unload specific models, a backend, or all.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "all": {"type": "boolean", "description": "Unload all running models", "default": False},
                "models": {"type": "array", "items": {"type": "string"}, "description": "Specific models to unload"},
                "backend": {"type": "string", "description": "Unload all models for a specific backend (e.g. 'llama.cpp')"},
            },
            "required": [],
        },
    },
    {
        "name": "dmr_bench",
        "description": "Benchmark a model's performance at different concurrency levels, measuring tokens per second (TPS).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model to benchmark"},
                "concurrency": {"type": "array", "items": {"type": "integer"}, "description": "Concurrency levels to test", "default": [1, 2, 4, 8]},
                "duration": {"type": "string", "description": "Duration per test (e.g. '30s', '60s')", "default": "30s"},
                "prompt": {"type": "string", "description": "Custom benchmark prompt"},
            },
            "required": ["model"],
        },
    },
    {
        "name": "dmr_logs",
        "description": "Fetch Docker Model Runner logs (daemon and backend engine logs).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "no_engines": {"type": "boolean", "description": "Exclude inference engine logs", "default": False},
                "lines": {"type": "integer", "description": "Number of lines to show (default 50)", "default": 50},
            },
            "required": [],
        },
    },
    # --- Runtime configuration (host-side docker model configure) ---
    {
        "name": "dmr_configure",
        "description": (
            "Set runtime configuration for a model via `docker model configure`. "
            "WARNING: configure REPLACES the model's whole config — every desired "
            "flag must be passed in one call or the rest are wiped. hf.co GGUFs "
            "MUST get an explicit context_size (native ctx can be 262k → OOM)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model reference (full ref for hf.co models)"},
                "context_size": {"type": "integer", "description": "Context window size (e.g. 4096, 6144)"},
                "keep_alive": {"type": "string", "description": "Keep-alive duration ('5m', '30m', '0' unload, '-1' forever)"},
                "mode": {"type": "string", "description": "Runner mode (e.g. 'completion', 'embedding', 'reranking')"},
                "think": {"type": "boolean", "description": "Enable thinking/reasoning mode"},
                "speculative_draft_model": {"type": "string", "description": "Draft model for speculative decoding"},
                "runtime_flags": {"type": "array", "items": {"type": "string"}, "description": "Extra backend flags after '--' (e.g. ['--reasoning-budget', '0'])"},
            },
            "required": ["model"],
        },
    },
    {
        "name": "dmr_configure_show",
        "description": "Show the current runtime configuration for a model (or all models if omitted).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model reference; omit to show all"},
            },
            "required": [],
        },
    },
    # --- GPU / platform-aware helpers ---
    {
        "name": "dmr_vram",
        "description": "Show GPU VRAM usage (nvidia-smi): used/free/total MiB and utilization. Useful before loading large models on the 8GB card.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dmr_validate",
        "description": (
            "Check that all models the SocialAuto app expects (DMR_TEXT_MODEL, "
            "DMR_MID_MODEL, DMR_CHATBOT_MODEL, DMR_TINY_MODEL, vision, embedding) "
            "are pulled. Returns present/missing lists."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dmr_route",
        "description": (
            "Show which model the app's platform-aware router selects for a given "
            "platform/task/prompt. Mirrors _select_model_by_complexity in "
            "app/services/dmr.py — does NOT send a request."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "description": "Social platform (linkedin, instagram, tiktok, twitter/x, threads, youtube, facebook, ...)"},
                "prompt": {"type": "string", "description": "Sample prompt (length affects routing)", "default": ""},
                "schema": {"type": "boolean", "description": "Whether the request carries a JSON schema", "default": False},
            },
            "required": [],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool call and return the result."""

    # --- Model management ---

    if name == "dmr_status":
        # Try API first, fall back to CLI
        models = _api_get("/engines/v1/models")
        if "error" in models:
            # API not reachable — try CLI status
            cli_status = _docker_model("status")
            if "error" in cli_status:
                return _error_result(f"DMR offline: {models['error']}")
            return _text_result(f"DMR: ONLINE (CLI)\n{cli_status.get('output', json.dumps(cli_status, indent=2))}")
        loaded = [m.get("id", "?") for m in models.get("data", [])]
        # Also get backend status
        backends = _api_get("/inference/status")
        backend_info = ""
        if "error" not in backends:
            backend_info = f"\nBackends: {json.dumps(backends, indent=2)}"
        return _text_result(
            f"DMR: ONLINE at {DMR_BASE}\n"
            f"Loaded models: {loaded if loaded else '(none — load on demand)'}"
            f"{backend_info}"
        )

    elif name == "dmr_list":
        result = _docker_model("list", "--json")
        if "error" in result:
            # Fall back to non-JSON
            result = _docker_model("list")
            if "error" in result:
                return _error_result(result['error'])
            return _text_result(result.get("output", json.dumps(result, indent=2)))
        return _text_result(json.dumps(result, indent=2) if "output" not in result else result["output"])

    elif name == "dmr_pull":
        model = args["model"]
        result = _docker_model("pull", model, timeout=600)
        if "error" in result:
            return _error_result(f"Pull failed: {result['error']}")
        return _text_result(f"Model '{model}' pulled successfully.\n{result.get('output', '')}")

    elif name == "dmr_inspect":
        model = args["model"]
        remote = args.get("remote", False)
        cmd_args = ["inspect", model]
        if remote:
            cmd_args.append("--remote")
        result = _docker_model(*cmd_args)
        if "error" in result:
            return _error_result(result['error'])
        text = json.dumps(result, indent=2) if "output" not in result else result["output"]
        return _text_result(text)

    elif name == "dmr_rm":
        models_to_remove = args["models"]
        force = args.get("force", False)
        cmd_args = ["rm"]
        if force:
            cmd_args.append("-f")
        cmd_args.extend(models_to_remove)
        result = _docker_model(*cmd_args)
        if "error" in result:
            return _error_result(result['error'])
        return _text_result(f"Removed: {', '.join(models_to_remove)}\n{result.get('output', '')}")

    elif name == "dmr_tag":
        source = args["source"]
        target = args["target"]
        result = _docker_model("tag", source, target)
        if "error" in result:
            return _error_result(result['error'])
        return _text_result(f"Tagged {source} → {target}\n{result.get('output', '')}")

    elif name == "dmr_push":
        model = args["model"]
        result = _docker_model("push", model, timeout=600)
        if "error" in result:
            return _error_result(result['error'])
        return _text_result(f"Pushed: {model}\n{result.get('output', '')}")

    elif name == "dmr_search":
        query = args.get("query", "")
        source = args.get("source", "all")
        limit = args.get("limit", 32)
        cmd_args = ["search", "--json", "--source", source, "--limit", str(limit)]
        if query:
            cmd_args.append(query)
        result = _docker_model(*cmd_args)
        if "error" in result:
            return _error_result(result['error'])
        text = json.dumps(result, indent=2) if "output" not in result else result["output"]
        return _text_result(text)

    elif name == "dmr_purge":
        force = args.get("force", False)
        cmd_args = ["purge"]
        if force:
            cmd_args.append("-f")
        result = _docker_model(*cmd_args)
        if "error" in result:
            return _error_result(result['error'])
        return _text_result(f"All models purged.\n{result.get('output', '')}")

    # --- Inference ---

    elif name == "dmr_chat":
        model = args.get("model", "ai/smollm3")
        message = args["message"]
        system = args.get("system")
        max_tokens = args.get("max_tokens", 512)
        temperature = args.get("temperature", 0.7)
        top_p = args.get("top_p", 1.0)
        json_mode = args.get("json_mode", False)
        stop = args.get("stop")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": message})
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if stop:
            body["stop"] = stop
        result = _api_post("/engines/llama.cpp/v1/chat/completions", body)
        if "error" in result:
            return _error_result(result['error'])
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = result.get("usage", {})
        text = f"Model: {model}\nResponse: {content}"
        if usage:
            text += f"\nTokens: {usage.get('total_tokens', '?')}"
        return _text_result(text)

    elif name == "dmr_completion":
        model = args.get("model", "ai/smollm3")
        prompt = args["prompt"]
        max_tokens = args.get("max_tokens", 256)
        temperature = args.get("temperature", 0.7)
        top_p = args.get("top_p", 1.0)
        result = _api_post("/engines/llama.cpp/v1/completions", {
            "model": model,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
        })
        if "error" in result:
            return _error_result(result['error'])
        content = result.get("choices", [{}])[0].get("text", "")
        usage = result.get("usage", {})
        text = f"Model: {model}\nResponse: {content}"
        if usage:
            text += f"\nTokens: {usage.get('total_tokens', '?')}"
        return _text_result(text)

    elif name == "dmr_embed":
        text_input = args["text"]
        model = args.get("model", "ai/qwen3-embedding")
        result = _api_post("/engines/llama.cpp/v1/embeddings", {
            "model": model,
            "input": text_input,
        })
        if "error" in result:
            return _error_result(result['error'])
        emb = result.get("data", [{}])[0].get("embedding", [])
        return _text_result(f"Model: {model}\nDimensions: {len(emb)}\nFirst 5 values: {emb[:5]}")

    elif name == "dmr_vision":
        model = args.get("model", "ai/qwen3-vl")
        image_path = args["image_path"]
        prompt = args["prompt"]
        max_tokens = args.get("max_tokens", 512)
        # Read and base64-encode the image
        try:
            with open(image_path, "rb") as f:
                image_data = base64.b64encode(f.read()).decode("utf-8")
        except FileNotFoundError:
            return _error_result(f"Image not found: {image_path}")
        except Exception as e:
            return _error_result(f"Failed to read image: {e}")
        # Detect MIME type from extension
        ext = os.path.splitext(image_path)[1].lower()
        mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
        mime = mime_map.get(ext, "image/png")
        body = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_data}"}},
                    ],
                }
            ],
            "max_tokens": max_tokens,
        }
        result = _api_post("/engines/llama.cpp/v1/chat/completions", body, timeout=180)
        if "error" in result:
            return _error_result(result['error'])
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        return _text_result(f"Model: {model}\nImage: {image_path}\nResponse: {content}")

    elif name == "dmr_ollama_chat":
        model = args.get("model", "ai/smollm3")
        message = args["message"]
        stream = args.get("stream", False)
        result = _api_post("/api/chat", {
            "model": model,
            "messages": [{"role": "user", "content": message}],
            "stream": stream,
        })
        if "error" in result:
            return _error_result(result['error'])
        content = result.get("message", {}).get("content", "") or result.get("response", "")
        return _text_result(f"Model: {model}\nResponse: {content}")

    elif name == "dmr_anthropic":
        model = args.get("model", "ai/smollm3")
        message = args["message"]
        system = args.get("system")
        max_tokens = args.get("max_tokens", 1024)
        temperature = args.get("temperature", 0.7)
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": message}],
        }
        if system:
            body["system"] = system
        result = _api_post("/anthropic/v1/messages", body)
        if "error" in result:
            return _error_result(result['error'])
        content = result.get("content", [{}])[0].get("text", "")
        return _text_result(f"Model: {model}\nResponse: {content}")

    elif name == "dmr_generate_image":
        prompt = args["prompt"]
        model = args.get("model", "stable-diffusion:Q4")
        size = args.get("size", "512x512")
        result = _api_post("/engines/diffusers/v1/images/generations", {
            "model": model,
            "prompt": prompt,
            "size": size,
        }, timeout=300)
        if "error" in result:
            return _error_result(result['error'])
        b64 = result.get("data", [{}])[0].get("b64_json", "")
        return _text_result(f"Image generated.\nModel: {model}\nSize: {size}\nBase64 length: {len(b64)}")

    # --- Monitoring & management ---

    elif name == "dmr_ps":
        # Try API first, fall back to CLI
        result = _api_get("/inference/ps")
        if "error" not in result:
            return _text_result(json.dumps(result, indent=2))
        # CLI fallback
        result = _docker_model("ps")
        if "error" in result:
            return _error_result(result['error'])
        return _text_result(result.get("output", json.dumps(result, indent=2)))

    elif name == "dmr_df":
        # Try API first, fall back to CLI
        result = _api_get("/inference/df")
        if "error" not in result:
            return _text_result(json.dumps(result, indent=2))
        # CLI fallback
        result = _docker_model("df")
        if "error" in result:
            return _error_result(result['error'])
        return _text_result(result.get("output", json.dumps(result, indent=2)))

    elif name == "dmr_unload":
        unload_all = args.get("all", False)
        models = args.get("models", [])
        backend = args.get("backend")
        # Try API first
        body: dict[str, Any] = {}
        if unload_all:
            body["all"] = True
        if models:
            body["models"] = models
        if backend:
            body["backend"] = backend
        if body:
            result = _api_post("/inference/unload", body)
            if "error" not in result:
                return _text_result(f"Unloaded.\n{json.dumps(result, indent=2)}")
        # CLI fallback
        cmd_args = ["unload"]
        if unload_all:
            cmd_args.append("--all")
        elif models:
            cmd_args.extend(models)
            if backend:
                cmd_args.extend(["--backend", backend])
        elif backend:
            cmd_args.extend(["--backend", backend])
        result = _docker_model(*cmd_args)
        if "error" in result:
            return _error_result(result['error'])
        return _text_result(f"Unloaded.\n{result.get('output', '')}")

    elif name == "dmr_bench":
        model = args["model"]
        concurrency = args.get("concurrency", [1, 2, 4, 8])
        duration = args.get("duration", "30s")
        prompt = args.get("prompt")
        cmd_args = ["bench", "--json", "--concurrency"]
        cmd_args.extend(str(c) for c in concurrency)
        cmd_args.extend(["--duration", duration])
        if prompt:
            cmd_args.extend(["--prompt", prompt])
        cmd_args.append(model)
        result = _docker_model(*cmd_args, timeout=600)
        if "error" in result:
            return _error_result(result['error'])
        text = json.dumps(result, indent=2) if "output" not in result else result["output"]
        return _text_result(text)

    elif name == "dmr_logs":
        no_engines = args.get("no_engines", False)
        lines = args.get("lines", 50)
        cmd_args = ["logs"]
        if no_engines:
            cmd_args.append("--no-engines")
        result = _docker_model(*cmd_args, timeout=30)
        if "error" in result:
            return _error_result(result['error'])
        output = result.get("output", "")
        # Truncate to last N lines
        if output:
            all_lines = output.split("\n")
            if len(all_lines) > lines:
                output = "\n".join(all_lines[-lines:])
        return _text_result(output)

    # --- Runtime configuration ---

    elif name == "dmr_configure":
        model = args["model"]
        cmd_args = ["configure"]
        if "context_size" in args:
            cmd_args.append(f"--context-size={args['context_size']}")
        if args.get("keep_alive"):
            cmd_args.append(f"--keep-alive={args['keep_alive']}")
        if args.get("mode"):
            cmd_args.append(f"--mode={args['mode']}")
        if "think" in args:
            cmd_args.append(f"--think={'true' if args['think'] else 'false'}")
        if args.get("speculative_draft_model"):
            cmd_args.append(f"--speculative-draft-model={args['speculative_draft_model']}")
        cmd_args.append(model)
        if args.get("runtime_flags"):
            cmd_args.append("--")
            cmd_args.extend(str(f) for f in args["runtime_flags"])
        result = _docker_model(*cmd_args)
        if "error" in result:
            return _error_result(result["error"])
        # Follow up with configure show so the caller sees the full effective
        # config (configure replaces rather than merges).
        show = _docker_model("configure", "show", model)
        out = f"Configured {model}.\n{result.get('output', '')}"
        if "error" not in show:
            out += f"\nEffective config:\n{show.get('output', json.dumps(show, indent=2))}"
        return _text_result(out)

    elif name == "dmr_configure_show":
        model = args.get("model")
        cmd_args = ["configure", "show"] + ([model] if model else [])
        result = _docker_model(*cmd_args)
        if "error" in result:
            return _error_result(result["error"])
        return _text_result(json.dumps(result, indent=2) if "output" not in result else result["output"])

    elif name == "dmr_vram":
        try:
            proc = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=name,utilization.gpu,memory.used,memory.free,memory.total,power.draw",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                return _error_result(proc.stderr.strip() or "nvidia-smi failed")
            name, util, used, free, total, power = (p.strip() for p in proc.stdout.strip().split(","))
            return _text_result(
                f"GPU: {name}\n"
                f"VRAM: {used} MiB used / {free} MiB free / {total} MiB total\n"
                f"Utilization: {util}% | Power: {power} W"
            )
        except FileNotFoundError:
            return _error_result("nvidia-smi not found")
        except Exception as e:
            return _error_result(str(e))

    elif name == "dmr_validate":
        models = _api_get("/engines/v1/models")
        if "error" in models:
            return _error_result(f"DMR API not reachable: {models['error']}")
        present_ids = {m.get("id", "") for m in models.get("data", [])}

        def _norm(ref: str) -> str:
            # Runner reports hf.co refs as lowercase huggingface.co/...
            return ref.lower().replace("hf.co/", "huggingface.co/")

        norm_ids = {_norm(p) for p in present_ids}
        lines = [f"DMR model validation ({len(present_ids)} models pulled):"]
        missing = []
        for role, ref in EXPECTED_MODELS.items():
            n = _norm(ref)
            ok = any(p.endswith(n.split("ai/")[-1]) or n in p for p in norm_ids)
            lines.append(f"  {'OK ' if ok else 'MISSING'} {role:>9}: {ref}")
            if not ok:
                missing.append(ref)
        lines.append(f"\nResult: {'all expected models present' if not missing else f'missing {len(missing)}: {missing}'}")
        return _text_result("\n".join(lines))

    elif name == "dmr_route":
        platform = (args.get("platform") or "").strip().lower()
        prompt = args.get("prompt") or ""
        has_schema = bool(args.get("schema", False))

        if platform in SHORT_FORM_PLATFORMS:
            chosen, tier = EXPECTED_MODELS["mid"], "mid (4B instruct — short-form platform)"
        elif has_schema or platform in LONG_FORM_PLATFORMS:
            chosen, tier = EXPECTED_MODELS["text"], "text (8B — long-form/schema)"
        elif len(prompt) < 200:
            chosen, tier = EXPECTED_MODELS["tiny"], "tiny (smollm3 — short prompt)"
        else:
            chosen, tier = EXPECTED_MODELS["text"], "text (8B — default)"
        return _text_result(
            f"platform={platform or '(none)'} schema={has_schema} prompt_len={len(prompt)}\n"
            f"→ {chosen}\n  tier: {tier}\n"
            f"  (mirrors _select_model_by_complexity in app/services/dmr.py)"
        )

    return _error_result(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# MCP Protocol (JSON-RPC over stdio)
# ---------------------------------------------------------------------------

def main() -> None:
    """Main MCP server loop (JSON-RPC over stdio)."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "dmr-mcp-server", "version": "2.0.0"},
                },
            }
        elif method == "tools/list":
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS},
            }
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            result = handle_tool_call(tool_name, tool_args)
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": result,
            }
        elif method == "notifications/initialized":
            continue  # No response needed for notifications
        elif method == "ping":
            response = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        else:
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
