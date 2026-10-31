#!/usr/bin/env python3
"""dmr_gateway.py — a Model-Serving-style gateway over the local DMR.

Callers hit logical endpoints (content-gen/embed/eval); the gateway routes to
concrete DMR models with traffic splits, skips models marked unhealthy, and
logs per-request usage — the Databricks endpoint+AI-Gateway pattern."""
import json
import random
import sqlite3
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

DMR = "http://localhost:12435/engines/v1"
PORT = 12440

# Named endpoints → [(model, weight)] — the "served entities + traffic" layer
ENDPOINTS = {
    "content-gen": [("ai/llama3.2", 90), ("docker.io/ai/qwen3:8b-q4_K_M", 10)],
    "eval":        [("ai/llama3.2", 100)],
    "classify":    [("ai/llama3.2", 100)],
    "embed":       [("docker.io/ai/qwen3-embedding:latest", 100)],
}
UNHEALTHY: set = set()
DB = "/tmp/dmr_gateway_usage.db"


def _pick(models):
    ok = [(m, w) for m, w in models if m not in UNHEALTHY] or models
    return random.choices([m for m, _ in ok], weights=[w for _, w in ok])[0]


def _log(ep, model, status, ms):
    try:
        db = sqlite3.connect(DB)
        db.execute("CREATE TABLE IF NOT EXISTS usage(ts REAL, ep TEXT, model TEXT, status INT, ms INT)")
        db.execute("INSERT INTO usage VALUES(?,?,?,?,?)", (time.time(), ep, model, status, int(ms*1000)))
        db.commit(); db.close()
    except Exception:
        pass


def _forward(path: str, body: dict, model_field: str, models):
    model = _pick(models)
    body = dict(body); body[model_field] = model
    t0 = time.time()
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            DMR + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}), timeout=120)
        out = json.loads(r.read())
        _log(body.get("_ep","?"), model, r.status, time.time()-t0)
        return out
    except Exception:
        UNHEALTHY.add(model)
        _log(body.get("_ep","?"), model, 0, time.time()-t0)
        return {"error": "model-unavailable", "model": model}


class H(BaseHTTPRequestHandler):
    def _j(self):
        n = int(self.headers.get("content-length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_POST(self):
        p = self.path
        if p.startswith("/v1/ep/"):  # /v1/ep/<endpoint>/chat or /embed
            ep, _, op = p[7:].partition("/")
            models = ENDPOINTS.get(ep)
            if not models:
                self.send_response(404); self.end_headers(); return
            body = self._j()
            path = "/embeddings" if op == "embed" else "/chat/completions"
            body["_ep"]=ep; out = _forward(path, body, "model", models)
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(json.dumps(out).encode())
        elif p == "/v1/status":
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps({"endpoints": {k: [m for m, _ in v] for k, v in ENDPOINTS.items()},
                                         "unhealthy": sorted(UNHEALTHY)}).encode())
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, *a): pass


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "serve":
        print(f"dmr-gateway on :{PORT}")
        HTTPServer(("127.0.0.1", PORT), H).serve_forever()
    else:
        print(json.dumps({"endpoints": {k: v for k, v in ENDPOINTS.items()}}, indent=1))
