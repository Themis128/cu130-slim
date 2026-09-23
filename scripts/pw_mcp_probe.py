#!/usr/bin/env python3
"""Generic Docker Playwright-MCP probe: inject sidecar cookies for a
platform, navigate, and report whether the session is authenticated.

Usage: pw_mcp_probe.py linkedin|twitter|facebook
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = "/home/tbaltzakis/cu130-slim"
IMAGE = "mcr.microsoft.com/playwright/mcp:latest"
DOCKER_ARGS = [
    "docker", "run", "-i", "--rm", "--init",
    "--network", "host", "--shm-size=2g",
    "-v", f"{REPO}:/workspace",
    "-v", f"{REPO}/.playwright-data:/home/pwuser/.playwright-data",
    "-w", "/workspace",
    IMAGE,
    "--config", "/workspace/.playwright-data/pw-mcp-config.json",
    "--user-data-dir", "/home/pwuser/.playwright-data/profile",
    "--ignore-https-errors",
    "--timeout-action", "20000",
    "--timeout-navigation", "120000",
]

SIDECARS = {"linkedin": 9225, "facebook": 9226, "tiktok": 9224}
DOMAINS = {
    "linkedin": ("linkedin.com", "https://www.linkedin.com/feed/", "/feed"),
    "twitter": ("x.com", "https://x.com/home", "SideNav_AccountSwitcher"),
    "tiktok": ("tiktok.com", "https://www.tiktok.com/foryou", "/foryou"),
}


class McpClient:
    def __init__(self):
        self.proc = subprocess.Popen(
            DOCKER_ARGS, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        self._id = 0

    def _send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _read(self):
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("MCP server closed stdout")
        return json.loads(line)

    def request(self, method, params=None, timeout=180):
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                    "params": params or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = self._read()
            if resp.get("id") == rid:
                return resp
        raise RuntimeError("timeout waiting for MCP response")

    def tool(self, name, args=None, timeout=180):
        resp = self.request("tools/call", {"name": name, "arguments": args or {}}, timeout)
        if resp is None:
            raise RuntimeError(f"no response for {name}")
        if "error" in resp:
            return {"error": resp["error"]}
        texts = [c.get("text", "") for c in resp.get("result", {}).get("content", [])
                 if c.get("type") == "text"]
        return {"text": "\n".join(texts), "raw": resp.get("result", {})}

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except (OSError, subprocess.SubprocessError):
            self.proc.kill()


def sidecar_cookies(platform):
    port = SIDECARS[platform]
    try:
        with urllib.request.urlopen(
            f"http://localhost:{port}/debug/all-cookies", timeout=30
        ) as r:
            d = json.loads(r.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        print(f"sidecar:{port} cookie export failed: {e}")
        return []
    cookies = d.get("cookies", {})
    return [
        {"name": n, "value": v, "domain": f".{DOMAINS[platform][0]}",
         "path": "/", "httpOnly": True, "secure": True, "sameSite": "Lax"}
        for n, v in cookies.items()
    ]


def main():
    platform = sys.argv[1]
    target_url = sys.argv[2] if len(sys.argv) > 2 else DOMAINS[platform][1]
    cookies = sidecar_cookies(platform) if platform in SIDECARS else []
    print(f"{platform}: {len(cookies)} cookies from sidecar")

    mcp = McpClient()
    try:
        mcp.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "probe", "version": "1.0"},
        })
        mcp._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        if cookies:
            code = (
                "async (page) => { await page.context().addCookies("
                + json.dumps(cookies)
                + "); return 'added:' + (await page.context().cookies()).length; }"
            )
            out = mcp.tool("browser_run_code_unsafe", {"code": code})
            print("inject:", str(out.get("text"))[:200])

        nav = mcp.tool("browser_navigate", {"url": target_url}, timeout=150)
        print("nav:", str(nav.get("text"))[-300:])
        time.sleep(6)
        code = (
            "async (page) => JSON.stringify({url: page.url(), "
            "title: await page.title(), "
            "body: (await page.locator('body').innerText()).slice(0, 400)})"
        )
        out = mcp.tool("browser_run_code_unsafe", {"code": code})
        print("state:", str(out.get("text"))[:700])
    finally:
        mcp.close()


if __name__ == "__main__":
    main()
