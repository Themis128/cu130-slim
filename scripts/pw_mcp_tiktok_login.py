#!/usr/bin/env python3
"""One-shot TikTok.com credential login via the Docker Playwright MCP
browser. Fills email + password, submits once, reports the outcome —
success (forYou feed), captcha wall, or wrong-credentials message.
Never prints the password.
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
        raise RuntimeError("timeout waiting for MCP response")

    def tool(self, name, args=None, timeout=180):
        resp = self.request("tools/call", {"name": name, "arguments": args or {}}, timeout)
        if resp is None:
            raise RuntimeError(f"no response for {name}")
        if "error" in resp:
            return {"error": resp["error"]}
        texts = [c.get("text", "") for c in resp.get("result", {}).get("content", [])
                 if c.get("type") == "text"]
        return {"text": "\n".join(texts)}

    def eval_js(self, expr, timeout=90):
        out = self.tool("browser_run_code_unsafe",
                        {"code": f"async (page) => {expr}"}, timeout=timeout)
        t = out.get("text", "")
        if "### Result" in t:
            t = t.split("### Result", 1)[1]
        return t.strip()

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except (OSError, subprocess.SubprocessError):
            self.proc.kill()


def load_env():
    env = {}
    with open(f"{REPO}/.env") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main():
    env = load_env()
    email = env.get("TIKTOK_DEV_EMAIL", "")
    password = env.get("TIKTOK_DEV_PASSWORD", "")
    if not email or not password:
        print("ERROR: TIKTOK_DEV_* missing")
        sys.exit(1)

    mcp = McpClient()
    try:
        mcp.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "tt-login", "version": "1.0"},
        })
        mcp._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        mcp.tool("browser_navigate", {"url": "https://www.tiktok.com/login/phone-or-email/email"})
        time.sleep(6)

        # Fill email + password via JS (React controlled inputs need
        # the native setter + input event).
        fill_code = (
            "{const r = await page.evaluate(([em_, pw_]) => {"
            "const setV=(el,v)=>{const s=Object.getOwnPropertyDescriptor("
            "HTMLInputElement.prototype,'value').set;s.call(el,v);"
            "el.dispatchEvent(new Event('input',{bubbles:true}));};"
            "const inputs=[...document.querySelectorAll('input')].filter(i=>i.offsetParent);"
            "const em=inputs.find(i=>/email|phone|username/i.test((i.name||'')+(i.placeholder||'')));"
            "const pw=inputs.find(i=>i.type==='password');"
            "if(em) setV(em, em_);"
            "if(pw) setV(pw, pw_);"
            "return {email:!!em, pw:!!pw};}, "
            "[" + json.dumps(email) + ", " + json.dumps(password) + "]);"
            "return JSON.stringify(r);}"
        )
        print("fill:", mcp.eval_js(fill_code))
        time.sleep(1.5)

        # Submit once — find the enabled submit/login button.
        submit_code = (
            "{const info = await page.evaluate(() => {"
            "const b=[...document.querySelectorAll('button')].find(e=>e.offsetParent"
            " && !e.disabled && /log in|σύνδεση/i.test(e.innerText||''));"
            "if(!b) return null;"
            "const r=b.getBoundingClientRect();"
            "return {x:r.x+r.width/2, y:r.y+r.height/2, t:b.innerText.trim()};});"
            "if(!info) return 'no-btn';"
            "await page.mouse.click(info.x, info.y);"
            "return 'clicked:'+info.t;}"
        )
        print("submit:", mcp.eval_js(submit_code))
        time.sleep(10)

        state = mcp.eval_js(
            "{return JSON.stringify({url: page.url(), "
            "body:(await page.locator('body').innerText()).slice(0,500)})}"
        )
        print("state:", state[:600])
    finally:
        mcp.close()


if __name__ == "__main__":
    main()
