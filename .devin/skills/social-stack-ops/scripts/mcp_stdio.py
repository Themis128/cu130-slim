#!/usr/bin/env python3
"""Minimal MCP stdio JSON-RPC client.

Usage:
  mcp_stdio.py --cmd 'docker compose exec -T social-api python3 -m app.mcp.server' \
               --tool <name> --args '<json>'     # one-shot tool call
  mcp_stdio.py --cmd '...' --list                # list tools
  mcp_stdio.py --cmd '...' --interactive         # read JSON-RPC calls from stdin
"""
import argparse
import json
import subprocess
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cmd", required=True)
    ap.add_argument("--tool")
    ap.add_argument("--args", default="{}")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--cwd", default="/home/tbaltzakis/cu130-slim")
    ns = ap.parse_args()

    proc = subprocess.Popen(
        ns.cmd, shell=True, cwd=ns.cwd,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, bufsize=1,
    )

    _id = [0]

    def send(method, params=None):
        _id[0] += 1
        msg = {"jsonrpc": "2.0", "id": _id[0], "method": method}
        if params is not None:
            msg["params"] = params
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        return _id[0]

    def notify(method):
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        proc.stdin.flush()

    def read_resp(want_id, timeout_msgs=200):
        for _ in range(timeout_msgs):
            line = proc.stdout.readline()
            if not line:
                return {"error": "server closed stdout"}
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want_id:
                return msg
        return {"error": "timeout"}

    send("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "devin", "version": "1.0"},
    })
    init = read_resp(1)
    if "result" not in init:
        print(json.dumps(init))
        sys.exit(1)
    notify("notifications/initialized")

    if ns.list:
        rid = send("tools/list")
        resp = read_resp(rid)
        tools = resp.get("result", {}).get("tools", [])
        for t in tools:
            print(t["name"], "-", (t.get("description") or "")[:80])
    elif ns.interactive:
        # stdin lines: {"tool": name, "args": {...}}
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            call = json.loads(line)
            rid = send("tools/call", {"name": call["tool"], "arguments": call.get("args", {})})
            resp = read_resp(rid)
            content = resp.get("result", resp)
            for c in (content.get("content") or []):
                if c.get("type") == "text":
                    print(c["text"])
            if resp.get("error"):
                print("ERROR:", resp["error"])
    else:
        rid = send("tools/call", {"name": ns.tool, "arguments": json.loads(ns.args)})
        resp = read_resp(rid)
        result = resp.get("result", resp)
        for c in (result.get("content") or []):
            if c.get("type") == "text":
                print(c["text"])
        if resp.get("error"):
            print("ERROR:", resp["error"])

    proc.terminate()


if __name__ == "__main__":
    main()
