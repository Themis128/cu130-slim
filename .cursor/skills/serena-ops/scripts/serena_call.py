#!/usr/bin/env python3
"""Serena facade bridge for the :9123 project server.

Usage:
    serena_call.py <facade> <method> [args-json] [kwargs-json]

Examples:
    serena_call.py fs search_for_pattern '["Auto-reply"]'
    serena_call.py fs get_symbols_overview '["path/to/file.py"]'
    serena_call.py lsp find_symbol '["MyClass"]'

Facades (NO `s.` prefix — `s.fs` errors "Unknown facade"):
    cfg, fs, edit, mem, shell, lsp

Caveats:
- Responses are pickled serena.repl objects that cannot be unpickled without
  serena on PYTHONPATH. This bridge extracts file paths / strings from the
  payload instead; full object access requires the :9121 MCP REPL.
- Serena indexes ~/cu130-slim (the shared checkout, which churns branches).
  Pushed branches and worktrees are truth when the index disagrees.
"""
import json, re, sys, urllib.request

ENDPOINT = "http://127.0.0.1:9123/call_facade_method"
TOKEN = "Bearer fdab942c-eca4-41ae-ad34-2563152d3613"
PROJECT = "cu130-slim"


def call(facade, method, args=None, kwargs=None):
    body = json.dumps({
        "project_name": PROJECT,
        "facade_name": facade,
        "method_name": method,
        "args": args or [],
        "kwargs": kwargs or {},
    }).encode()
    req = urllib.request.Request(
        ENDPOINT, data=body,
        headers={"Authorization": TOKEN, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read()[:800]!r}"
    txt = data.decode("utf-8", "replace")
    # Pickled result — extract navigable strings (paths, identifiers).
    paths = re.findall(r"[a-zA-Z0-9_./-]+\.(?:py|md|yml|yaml|json|toml|ts|tsx|sh)", txt)
    seen, out = set(), []
    for p in paths:
        if p not in seen and len(p) > 4:
            seen.add(p)
            out.append(p)
    return "\n".join(out[:50]) if out else txt[:3000]


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    _facade, _method = sys.argv[1], sys.argv[2]
    _args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else []
    _kwargs = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {}
    print(call(_facade, _method, _args, _kwargs))
