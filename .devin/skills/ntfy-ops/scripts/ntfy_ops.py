#!/usr/bin/env python3
"""ntfy_ops.py — server-side ops for the k3s ntfy on github-omv:30080.

    ntfy_ops.py users
    ntfy_ops.py grant <user> <topic> <ro|wo|rw>
    ntfy_ops.py passwd <user>            # needs NTFY_PASSWORD env
    ntfy_ops.py token <user>             # non-expiring Bearer token
    ntfy_ops.py publish <topic> <msg> --token tk_...
    ntfy_ops.py audit                    # find ntfy URLs in repo + probe each
"""
import argparse
import os
import re
import subprocess
import sys
import urllib.request

HOST = "github-omv"
NS = "ntfy"
HTTP = "http://github-omv:30080"


def ntfy_cli(*args: str, env: dict | None = None) -> str:
    prefix = ""
    if env:
        prefix = "env " + " ".join(f"{k}={v}" for k, v in env.items()) + " "
    cmd = (f"ssh -o ConnectTimeout=10 {HOST} "
           f"\"sudo k3s kubectl -n {NS} exec -i deploy/ntfy -- {prefix}ntfy {' '.join(args)}\"")
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise SystemExit(f"ntfy {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def publish(topic: str, msg: str, token: str, title: str = "ntfy-ops") -> None:
    req = urllib.request.Request(
        f"{HTTP}/{topic}", data=msg.encode(), method="POST",
        headers={"Title": title, "Priority": "default"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    r = urllib.request.urlopen(req, timeout=10)
    print(f"publish {topic}: {r.status}")


def audit() -> int:
    """Find ntfy URLs referenced in the repo and probe each one."""
    root = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        root = os.path.dirname(root)
        if os.path.isdir(os.path.join(root, ".git")):
            break
    hits = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in
                   ("node_modules", ".git", "__pycache__", ".venv", "vendor")]
        for fn in files:
            if not fn.endswith((".py", ".sh", ".yml", ".yaml", ".env", ".md", ".json")):
                continue
            p = os.path.join(dirpath, fn)
            try:
                txt = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for m in re.finditer(r'https?://[^\s"\'<>]*ntfy[^\s"\'<>]*', txt):
                hits.setdefault(m.group(0).rstrip('"\','), []).append(
                    os.path.relpath(p, root))
    if not hits:
        print("no ntfy URLs found in repo")
        return 0
    bad = 0
    for url, files in sorted(hits.items()):
        try:
            req = urllib.request.Request(url, method="GET")
            urllib.request.urlopen(req, timeout=8)
            status = "ok"
        except urllib.error.HTTPError as e:
            status = f"HTTP {e.code}" + (" (ACCESS-GATED)" if e.code == 302 else "")
            bad += 1
        except Exception as e:
            status = f"unreachable ({type(e).__name__})"
            bad += 1
        marker = "  " if status == "ok" else "!!"
        print(f"{marker} {url:55s} {status}   [{files[0]}{'…' if len(files) > 1 else ''}]")
    if bad:
        print("\nFix: machine publishers must use tailnet/LAN endpoint + auth —")
        print(f"     {HTTP}/<topic> with a Bearer token (see SKILL.md).")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("users")
    g = sub.add_parser("grant")
    g.add_argument("user"); g.add_argument("topic")
    g.add_argument("perm", choices=["ro", "wo", "rw", "read", "write", "read-write"])
    p = sub.add_parser("passwd"); p.add_argument("user")
    t = sub.add_parser("token"); t.add_argument("user")
    pb = sub.add_parser("publish")
    pb.add_argument("topic"); pb.add_argument("msg")
    pb.add_argument("--token", default=os.environ.get("NTFY_TOKEN", ""))
    pb.add_argument("--title", default="ntfy-ops")
    sub.add_parser("audit")
    a = ap.parse_args()

    if a.cmd == "users":
        print(ntfy_cli("user", "list"))
    elif a.cmd == "grant":
        perm = {"read": "ro", "write": "wo", "read-write": "rw"}.get(a.perm, a.perm)
        print(ntfy_cli("access", a.user, a.topic, perm))
    elif a.cmd == "passwd":
        pw = os.environ.get("NTFY_PASSWORD", "")
        if not pw:
            raise SystemExit("set NTFY_PASSWORD env (never pass on argv)")
        print(ntfy_cli("user", "change-pass", a.user, env={"NTFY_PASSWORD": pw}))
    elif a.cmd == "token":
        print(ntfy_cli("token", "add", a.user))
    elif a.cmd == "publish":
        publish(a.topic, a.msg, a.token, a.title)
    elif a.cmd == "audit":
        return audit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
