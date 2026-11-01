#!/usr/bin/env python3
"""dmr_gateway.py — a Model-Serving-style gateway over the local DMR.

Callers hit logical endpoints (content-gen/embed/eval); the gateway routes to
concrete DMR models with traffic splits, skips models marked unhealthy (TTL'd
and re-probed via dmr_doctor — a client 4xx never counts as a model failure),
returns upstream status codes honestly, and logs per-request usage — the
Databricks endpoint+AI-Gateway pattern."""
import json
import os
import random
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DMR = "http://localhost:12435/engines/v1"
PORT = 12440

# Named endpoints → [(model, weight)] — the "served entities + traffic" layer
ENDPOINTS = {
    "content-gen": [("ai/llama3.2", 90), ("docker.io/ai/qwen3:8b-q4_K_M", 10)],
    "eval":        [("ai/llama3.2", 100)],
    "classify":    [("ai/llama3.2", 100)],
    "embed":       [("docker.io/ai/qwen3-embedding:latest", 100)],
}

# model → epoch until which it is quarantined. Entries expire and the model is
# re-probed (dmr_doctor serve-path check) before serving traffic again, so a
# transient failure can never retire a model for the life of the process.
UNHEALTHY: dict[str, float] = {}
UNHEALTHY_TTL_S = 120.0
PROBE_TIMEOUT_S = 15.0
DB = "/tmp/dmr_gateway_usage.db"


def _mark_unhealthy(model: str) -> None:
    UNHEALTHY[model] = time.time() + UNHEALTHY_TTL_S


def _load_doctor():
    """Import the sibling dmr-ops doctor (…/skills/dmr-ops/scripts/dmr_doctor.py)."""
    try:
        import importlib.util
        here = os.path.dirname(os.path.abspath(__file__))
        cand = os.path.normpath(os.path.join(here, "..", "..", "dmr-ops", "scripts", "dmr_doctor.py"))
        if not os.path.exists(cand):
            return None
        spec = importlib.util.spec_from_file_location("dmr_doctor", cand)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _post_chat(model: str, t: float = PROBE_TIMEOUT_S) -> str:
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": "reply with ok"}],
                       "max_tokens": 5}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(
                f"{DMR}/chat/completions", data=body,
                headers={"Content-Type": "application/json"}), timeout=t) as f:
            d = json.loads(f.read())
            ch = (d.get("choices") or [{}])[0]
            return ch.get("message", {}).get("content", "?")[:60]
    except Exception as e:
        return f"FAIL {type(e).__name__}"


def _probe_healthy(model: str) -> bool:
    """Re-probe an expired quarantine via dmr_doctor's serve-path check
    (inline 1-token chat fallback when the doctor can't be imported)."""
    try:
        doc = _load_doctor()
        r = (doc._post_chat(DMR, model, t=PROBE_TIMEOUT_S)
             if doc is not None else _post_chat(model))
        return not str(r).startswith("FAIL")
    except Exception:
        return False


def _pick(models):
    now = time.time()
    ok = []
    for m, w in models:
        until = UNHEALTHY.get(m, 0.0)
        if until > now:
            continue                      # still quarantined
        if until:                         # TTL expired — re-probe once
            if _probe_healthy(m):
                UNHEALTHY.pop(m, None)
            else:
                UNHEALTHY[m] = now + UNHEALTHY_TTL_S
                continue
        ok.append((m, w))
    ok = ok or models                     # availability over strictness
    return random.choices([m for m, _ in ok], weights=[w for _, w in ok])[0]


def _log(ep, model, status, ms):
    try:
        db = sqlite3.connect(DB)
        db.execute("CREATE TABLE IF NOT EXISTS usage(ts REAL, ep TEXT, model TEXT, status INT, ms INT)")
        db.execute("INSERT INTO usage VALUES(?,?,?,?,?)", (time.time(), ep, model, status, int(ms * 1000)))
        db.commit()
        db.close()
    except Exception:
        pass


def _forward(path: str, body: dict, model_field: str, models, ep: str):
    """Returns (status, payload). 4xx upstream responses pass through with
    their original status/body; only connect errors, timeouts and 5xx
    quarantine the model."""
    model = _pick(models)
    body = dict(body)
    body[model_field] = model
    t0 = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(
                DMR + path, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}), timeout=120) as r:
            out = r.read()
            _log(ep, model, r.status, time.time() - t0)
            try:
                return r.status, json.loads(out)
            except ValueError:
                _mark_unhealthy(model)
                return 502, {"error": "bad-upstream-json", "model": model}
    except urllib.error.HTTPError as e:
        payload = e.read() or b""
        _log(ep, model, e.code, time.time() - t0)
        if e.code >= 500:
            _mark_unhealthy(model)
            return 503, {"error": "model-unavailable", "model": model,
                         "upstream_status": e.code}
        try:                             # client 4xx — not a model failure
            return e.code, json.loads(payload)
        except ValueError:
            return e.code, {"error": payload.decode("utf-8", "replace")[:800]
                            or f"upstream-{e.code}"}
    except (urllib.error.URLError, TimeoutError, OSError):
        _mark_unhealthy(model)
        _log(ep, model, 0, time.time() - t0)
        return 503, {"error": "model-unavailable", "model": model}


class H(BaseHTTPRequestHandler):
    def _j(self):
        try:
            n = int(self.headers.get("content-length", 0))
        except ValueError:
            n = 0
        body = self.rfile.read(n) or b"{}"
        return json.loads(body)          # raises ValueError → 400 in do_POST

    def _send(self, status: int, obj) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def _status_payload(self):
        now = time.time()
        return {
            "endpoints": {k: [m for m, _ in v] for k, v in ENDPOINTS.items()},
            "unhealthy": {m: round(u - now, 1)
                          for m, u in UNHEALTHY.items() if u > now}}

    def do_GET(self):
        if self.path == "/v1/status":
            self._send(200, self._status_payload())
        else:
            self._send(404, {"error": "not-found"})

    def do_POST(self):
        p = self.path
        if p.startswith("/v1/ep/"):      # /v1/ep/<endpoint>/chat or /embed
            ep, _, op = p[7:].partition("/")
            models = ENDPOINTS.get(ep)
            if not models:
                self._send(404, {"error": "unknown-endpoint", "endpoint": ep})
                return
            try:
                body = self._j()
            except ValueError:
                self._send(400, {"error": "bad-json"})
                return
            if not isinstance(body, dict):
                self._send(400, {"error": "bad-json"})
                return
            path = "/embeddings" if op == "embed" else "/chat/completions"
            status, out = _forward(path, body, "model", models, ep)
            self._send(status, out)
        elif p == "/v1/status":
            self._send(200, self._status_payload())
        else:
            self._send(404, {"error": "not-found"})

    def log_message(self, *a): pass


class GW(ThreadingHTTPServer):           # one slow generation can't block others
    daemon_threads = True


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "serve":
        print(f"dmr-gateway on :{PORT}")
        GW(("127.0.0.1", PORT), H).serve_forever()
    elif cmd == "status":
        # Query the running gateway's /v1/status; fall back to the static
        # route table when it isn't up.
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/v1/status", timeout=3) as r:
                print(json.dumps(json.loads(r.read()), indent=1))
        except Exception:
            print(json.dumps({
                "endpoints": {k: [m for m, _ in v] for k, v in ENDPOINTS.items()},
                "unhealthy": {},
                "note": "gateway not running — static route table"}, indent=1))
    else:
        print("usage: dmr_gateway.py [serve|status]", file=sys.stderr)
        sys.exit(2)
