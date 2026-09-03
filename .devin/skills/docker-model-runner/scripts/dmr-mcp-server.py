#!/usr/bin/env python3
"""Docker Model Runner MCP Server.

A lightweight MCP (Model Context Protocol) server that wraps the Docker Model
Runner REST API, exposing model management and inference as MCP tools.

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

Tools exposed:
  - dmr_status: Check DMR health and list loaded models
  - dmr_list: List all local (pulled) models
  - dmr_chat: Send a chat completion request
  - dmr_embed: Generate embeddings
  - dmr_pull: Pull a new model from Docker Hub or HuggingFace
  - dmr_inspect: Inspect a model's details
  - dmr_configure: Configure a model (context size, runtime flags)
  - dmr_generate_image: Generate an image via Diffusers backend
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

DMR_BASE = "http://localhost:12434"


def _api_get(path: str) -> dict[str, Any]:
    """Make a GET request to DMR API."""
    url = f"{DMR_BASE}{path}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": f"DMR API not reachable: {e}"}
    except Exception as e:
        return {"error": str(e)}


def _api_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """Make a POST request to DMR API."""
    url = f"{DMR_BASE}{path}"
    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": f"DMR API request failed: {e}"}
    except Exception as e:
        return {"error": str(e)}


def _docker_model(*args: str) -> dict[str, Any]:
    """Run a `docker model` CLI command."""
    try:
        result = subprocess.run(
            ["docker", "model", *args],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip() or f"Command failed with code {result.returncode}"}
        # Try to parse JSON output, fall back to raw text
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"output": result.stdout.strip()}
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out (300s)"}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# MCP Protocol (JSON-RPC over stdio)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "dmr_status",
        "description": "Check Docker Model Runner health and list currently loaded models. No parameters needed.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dmr_list",
        "description": "List all local (pulled) DMR models with details. No parameters needed.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dmr_chat",
        "description": "Send a chat completion request to a DMR model via OpenAI-compatible API. Models load on-demand if pulled.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model identifier (e.g. 'ai/smollm2', 'ai/qwen3:8b-q4_K_M')", "default": "ai/smollm2"},
                "message": {"type": "string", "description": "The user message / prompt"},
                "system": {"type": "string", "description": "Optional system prompt"},
                "max_tokens": {"type": "integer", "description": "Max tokens to generate", "default": 512},
                "temperature": {"type": "number", "description": "Sampling temperature (0.0-2.0)", "default": 0.7},
            },
            "required": ["message"],
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
        "description": "Inspect a model's details (format, tags, config).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model name to inspect"},
            },
            "required": ["model"],
        },
    },
    {
        "name": "dmr_configure",
        "description": "Configure a model's context size or runtime flags. Context size determines max tokens per request. Runtime flags are llama.cpp parameters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model name to configure"},
                "context_size": {"type": "integer", "description": "Context size in tokens (e.g. 4096, 8192). Use -1 to reset to default."},
                "runtime_flags": {"type": "array", "items": {"type": "string"}, "description": "llama.cpp runtime flags (e.g. ['--temp', '0.7', '--top-p', '0.9'])"},
            },
            "required": ["model"],
        },
    },
    {
        "name": "dmr_generate_image",
        "description": "Generate an image from a text prompt using the Diffusers backend (requires NVIDIA GPU on Linux).",
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
]


def handle_tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool call and return the result."""
    if name == "dmr_status":
        models = _api_get("/engines/v1/models")
        if "error" in models:
            return {"content": [{"type": "text", "text": f"DMR OFFLINE: {models['error']}"}]}
        loaded = [m.get("id", "?") for m in models.get("data", [])]
        text = f"DMR: ONLINE at {DMR_BASE}\nLoaded models: {loaded if loaded else '(none — load on demand)'}"
        return {"content": [{"type": "text", "text": text}]}

    elif name == "dmr_list":
        result = _docker_model("list")
        if "error" in result:
            return {"content": [{"type": "text", "text": f"Error: {result['error']}"}]}
        text = result.get("output", json.dumps(result, indent=2))
        return {"content": [{"type": "text", "text": text}]}

    elif name == "dmr_chat":
        model = args.get("model", "ai/smollm2")
        message = args["message"]
        system = args.get("system")
        max_tokens = args.get("max_tokens", 512)
        temperature = args.get("temperature", 0.7)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": message})
        result = _api_post("/engines/llama.cpp/v1/chat/completions", {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        })
        if "error" in result:
            return {"content": [{"type": "text", "text": f"Error: {result['error']}"}]}
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = result.get("usage", {})
        text = f"Model: {model}\nResponse: {content}"
        if usage:
            text += f"\nTokens: {usage.get('total_tokens', '?')}"
        return {"content": [{"type": "text", "text": text}]}

    elif name == "dmr_embed":
        text_input = args["text"]
        model = args.get("model", "ai/qwen3-embedding")
        result = _api_post("/engines/llama.cpp/v1/embeddings", {
            "model": model,
            "input": text_input,
        })
        if "error" in result:
            return {"content": [{"type": "text", "text": f"Error: {result['error']}"}]}
        emb = result.get("data", [{}])[0].get("embedding", [])
        text = f"Model: {model}\nDimensions: {len(emb)}\nFirst 5 values: {emb[:5]}"
        return {"content": [{"type": "text", "text": text}]}

    elif name == "dmr_pull":
        model = args["model"]
        result = _docker_model("pull", model)
        if "error" in result:
            return {"content": [{"type": "text", "text": f"Pull failed: {result['error']}"}]}
        text = f"Model '{model}' pulled successfully.\n{result.get('output', '')}"
        return {"content": [{"type": "text", "text": text}]}

    elif name == "dmr_inspect":
        model = args["model"]
        result = _docker_model("inspect", model)
        if "error" in result:
            return {"content": [{"type": "text", "text": f"Error: {result['error']}"}]}
        text = json.dumps(result, indent=2) if "output" not in result else result["output"]
        return {"content": [{"type": "text", "text": text}]}

    elif name == "dmr_configure":
        model = args["model"]
        context_size = args.get("context_size")
        runtime_flags = args.get("runtime_flags", [])
        cmd_args = []
        if context_size is not None:
            cmd_args.extend(["--context-size", str(context_size)])
        if runtime_flags:
            cmd_args.append("--")
            cmd_args.extend(runtime_flags)
        result = _docker_model("configure", model, *cmd_args) if cmd_args else {"error": "No configuration provided"}
        if "error" in result:
            return {"content": [{"type": "text", "text": f"Error: {result['error']}"}]}
        text = f"Model '{model}' configured.\n{result.get('output', '')}"
        return {"content": [{"type": "text", "text": text}]}

    elif name == "dmr_generate_image":
        prompt = args["prompt"]
        model = args.get("model", "stable-diffusion:Q4")
        size = args.get("size", "512x512")
        result = _api_post("/engines/diffusers/v1/images/generations", {
            "model": model,
            "prompt": prompt,
            "size": size,
        })
        if "error" in result:
            return {"content": [{"type": "text", "text": f"Error: {result['error']}"}]}
        b64 = result.get("data", [{}])[0].get("b64_json", "")
        text = f"Image generated.\nModel: {model}\nSize: {size}\nBase64 length: {len(b64)}"
        return {"content": [{"type": "text", "text": text}]}

    return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}]}


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
                    "serverInfo": {"name": "dmr-mcp-server", "version": "1.0.0"},
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
