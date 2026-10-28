#!/usr/bin/env python3
"""Minimal Playwright-MCP driver for one-shot browser actions against the
persistent .playwright-data profile.

Usage (steps separated by |):
    pw_mcp_drive.py "nav <url> | snapshot | click <ref> | type <ref> <text...>"
    pw_mcp_drive.py "eval <js> | shot <file.png>"
    type step:  type <ref> <text...>           (append '+' to submit)
    eval step:  eval <async (page) => {...}>
"""
import json
import subprocess
import sys

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
    "--timeout-action", "25000",
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

    def request(self, method, params=None, timeout=240):
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                    "params": params or {}})
        while True:
            msg = self._read()
            if msg.get("id") == rid:
                return msg

    def call(self, tool, args, timeout=240):
        res = self.request("tools/call", {"name": tool, "arguments": args}, timeout)
        content = res.get("result", {}).get("content", [])
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(texts), res.get("result", {}).get("isError", False)


def run_step(mcp, step):
    parts = step.split(None, 2)
    action = parts[0]
    if action == "nav":
        return mcp.call("browser_navigate", {"url": parts[1]})
    if action == "snapshot":
        return mcp.call("browser_snapshot", {})
    if action == "click":
        return mcp.call("browser_click", {"element": "element", "target": parts[1]})
    if action == "type":
        rest = step.split(None, 2)[2]
        submit = rest.endswith(" +")
        text = rest[:-2] if submit else rest
        return mcp.call("browser_type", {"element": "element", "target": parts[1],
                                       "text": text, "submit": submit})
    if action == "press":
        return mcp.call("browser_press_key", {"key": parts[1]})
    if action == "eval":
        return mcp.call("browser_evaluate", {"function": step.split(None, 1)[1]})
    if action == "evalat":
        _, target, fn = step.split(None, 2)
        return mcp.call("browser_evaluate", {"element": "element", "target": target, "function": fn})
    if action == "shot":
        args = {"filename": parts[1]}
        if len(parts) > 2:
            args.update({"element": "element", "target": parts[2]})
        return mcp.call("browser_take_screenshot", args)
    if action == "wait":
        import time
        time.sleep(float(parts[1]))
        return "slept", False
    if action == "findclick":
        import re
        snap, err = mcp.call("browser_snapshot", {})
        if err:
            return snap, err
        needle = step.split(None, 1)[1].strip().strip('"')
        ref = None
        for line in snap.splitlines():
            if needle.lower() in line.lower():
                m = re.search(r"\[ref=([A-Za-z0-9_]+)\]", line)
                if m:
                    ref = m.group(1)
                    break
        if not ref:
            return f"no ref matching '{needle}'", True
        return mcp.call("browser_click", {"element": needle, "target": ref})
    return f"unknown action {action}", True


def main():
    mcp = McpClient()
    mcp.request("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "pw-drive", "version": "0.1"},
    })
    mcp._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    if len(sys.argv) > 1 and sys.argv[1] == "repl":
        for line in sys.stdin:
            step = line.strip()
            if not step or step == "quit":
                break
            print(f"### {step}", flush=True)
            try:
                out, err = run_step(mcp, step)
            except Exception as e:
                print(f"EXCEPTION {e}", flush=True)
                continue
            print(("ERROR " if err else "") + out[:6000], flush=True)
            print("<<<STEP_DONE>>>", flush=True)
    else:
        for step in sys.argv[1].split(" | "):
            step = step.strip()
            if not step:
                continue
            print(f"### {step}")
            try:
                out, err = run_step(mcp, step)
            except Exception as e:
                print(f"EXCEPTION {e}")
                break
            print(("ERROR " if err else "") + out[:6000])
            if err:
                break
    mcp.proc.terminate()


if __name__ == "__main__":
    main()
