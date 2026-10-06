#!/usr/bin/env python3
"""Drive the dockerized Playwright MCP server to run LinkedIn Campaign
Manager tasks (ad-credit claim, post boost) using the logged-in session
transplanted from the LinkedIn browser sidecar (:9225).

Reads the sidecar cookie export (name -> value map), injects it into the
MCP browser context via page.context().addCookies(), then walks the
supplied campaign-manager URL and reports page state. Cookie values are
never printed.
"""
import json
import subprocess
import sys
import time

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
        raise TimeoutError(f"no response for {method}")

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def call(self, tool, arguments, timeout=180):
        resp = self.request("tools/call", {
            "name": tool, "arguments": arguments}, timeout=timeout)
        result = resp.get("result") or {}
        parts = result.get("content") or []
        text = "\n".join(p.get("text", "") for p in parts if p.get("type") == "text")
        if result.get("isError"):
            raise RuntimeError(f"{tool} failed: {text[:800]}")
        return text

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except (OSError, subprocess.SubprocessError):
            self.proc.kill()


def fetch_sidecar_cookies() -> list[dict]:
    out = subprocess.run(
        ["curl", "-s", "http://localhost:9225/debug/all-cookies"],
        capture_output=True, text=True, check=True,
    )
    raw = json.loads(out.stdout).get("cookies", {})
    http_only = {"li_at", "JSESSIONID", "bcookie", "li_rm", "liap"}
    cookies = []
    for name, value in raw.items():
        cookies.append({
            "name": name, "value": value,
            "domain": ".linkedin.com", "path": "/",
            "secure": True, "httpOnly": name in http_only,
            "sameSite": "None",
        })
    return cookies


def main():
    target_url = sys.argv[1] if len(sys.argv) > 1 else \
        "https://www.linkedin.com/campaignmanager/accounts"
    cookies = fetch_sidecar_cookies()
    print(f"transplanting {len(cookies)} linkedin cookies")

    client = McpClient()
    try:
        client.request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "li-ads-driver", "version": "1.0"},
        })
        client.notify("notifications/initialized")

        # Land on linkedin.com first so cookie writes are domain-scoped.
        client.call("browser_navigate", {"url": "https://www.linkedin.com/"})
        inject = (
            "async (page) => { await page.context().addCookies("
            + json.dumps(cookies)
            + f"); return 'added {len(cookies)}'; }}"
        )
        print(client.call("browser_run_code_unsafe", {"code": inject})[-200:])

        # Verify the session is authenticated — navigate inside unsafe code
        # so HTTP error statuses don't kill the MCP tool call.
        probe = """async (page) => {
            try {
                const resp = await page.goto('https://www.linkedin.com/feed/', {
                    waitUntil: 'domcontentloaded', timeout: 60000 });
                await page.waitForTimeout(4000);
                return JSON.stringify({
                    status: resp && resp.status(),
                    url: page.url(),
                    onLoginWall: !!await page.$('form[action*="login"]'),
                    hasNav: !!await page.$('nav.global-nav, #global-nav'),
                    title: await page.title(),
                });
            } catch (e) { return 'NAV-ERR: ' + e.message.slice(0, 200); }
        }"""
        state = client.call("browser_run_code_unsafe", {"code": probe}, timeout=120)
        print("auth state:", state[-500:])
        if "onLoginWall\":true" in state.replace(" ", ""):
            print("SESSION REJECTED — transplant failed")
            return

        # Open the campaign-manager URL and report what's on screen.
        open_target = (
            "async (page) => { try { const r = await page.goto('"
            + target_url
            + "', {waitUntil:'domcontentloaded',timeout:90000}); "
            + "await page.waitForTimeout(6000); return 'status=' + (r && r.status()) + ' url=' + page.url();"
            + " } catch(e){ return 'NAV-ERR: ' + e.message.slice(0,200); } }"
        )
        print(client.call("browser_run_code_unsafe", {"code": open_target}, timeout=150)[-300:])
        snap = client.call("browser_snapshot", {})
        print("=== SNAPSHOT ===")
        print(snap[-6000:])
    finally:
        client.close()


if __name__ == "__main__":
    main()
