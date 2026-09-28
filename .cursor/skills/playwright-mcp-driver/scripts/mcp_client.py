"""Minimal stdio MCP client for the dockerized Playwright MCP server.

Spawns `docker run -i mcr.microsoft.com/playwright/mcp` with the repo's
config + persistent profile, speaks newline-delimited JSON-RPC, and exposes
tool(name, **args) -> result. Usage:

    from mcp_client import PlaywrightMCP
    with PlaywrightMCP() as mcp:
        mcp.tool("browser_navigate", url="https://example.com")
        snap = mcp.tool("browser_snapshot")
"""

import json
import subprocess
import sys
import threading
import time

CMD = [
    "docker", "run", "-i", "--rm", "--init",
    "--network", "host",
    "--shm-size=2g",
    "-v", "/home/tbaltzakis/cu130-slim:/workspace",
    "-v", "/home/tbaltzakis/cu130-slim/.playwright-data:/home/pwuser/.playwright-data",
    "-w", "/workspace",
    "mcr.microsoft.com/playwright/mcp:latest",
    "--config", "/workspace/.playwright-data/pw-mcp-config.json",
    "--user-data-dir", "/home/pwuser/.playwright-data/profile",
    "--browser", "chromium",
    "--init-script", "/workspace/.playwright-data/init-script.js",
    "--ignore-https-errors",
    "--timeout-action", "15000",
    "--timeout-navigation", "120000",
    "--timeout-settle", "1500",
]


class PlaywrightMCP:
    def __init__(self):
        self._id = 0
        self._pending = {}
        self._lock = threading.Lock()
        self.proc = subprocess.Popen(
            CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=open("/tmp/pw-mcp.err", "w"), text=True, bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pw-driver", "version": "1.0"},
        })
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _read_loop(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in msg:
                with self._lock:
                    self._pending[msg["id"]] = msg

    def _call(self, method, params=None, timeout=150):
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                    "params": params or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if rid in self._pending:
                    return self._pending.pop(rid)
            time.sleep(0.05)
        raise TimeoutError(f"MCP call {method} timed out")

    def tools(self):
        resp = self._call("tools/list")
        return [t["name"] for t in resp.get("result", {}).get("tools", [])]

    def tool(self, name, timeout=150, **arguments):
        resp = self._call("tools/call", {"name": name, "arguments": arguments},
                          timeout=timeout)
        result = resp.get("result", {})
        # MCP returns content blocks; join text payloads
        texts = [c.get("text", "") for c in result.get("content", [])
                 if c.get("type") == "text"]
        if result.get("isError"):
            raise RuntimeError("\n".join(texts) or json.dumps(result))
        return "\n".join(texts)

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


if __name__ == "__main__":
    with PlaywrightMCP() as mcp:
        print("tools:", mcp.tools())
