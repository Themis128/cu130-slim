#!/usr/bin/env python3
"""Print health of Cloudless social stack services (no secrets).
Usage: stack-status.py"""

import json
import subprocess
import urllib.request

print("== containers ==")
r = subprocess.run(
    ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"],
    capture_output=True, text=True)
for line in r.stdout.splitlines():
    if any(k in line for k in ("NAMES", "social", "n8n", "redis", "comfy",
                               "ollama", "chroma", "postgres")):
        print(line)


def probe(name: str, url: str) -> None:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:
        code = "fail"
    print(f"{name}  {code}  {url}")


print("\n== http probes ==")
import urllib.error  # noqa: E402
probe("n8n_healthz", "http://127.0.0.1:5678/healthz")
probe("social_openapi", "http://127.0.0.1:8083/openapi.json")
probe("social_frontend", "http://127.0.0.1:8082/")

print("\n== carousel endpoint present? ==")
try:
    with urllib.request.urlopen("http://127.0.0.1:8083/openapi.json",
                                timeout=5) as r:
        paths = json.loads(r.read()).get("paths", {})
    print("run-carousel-and-publish",
          "/api/v1/ai/run-carousel-and-publish" in paths)
    print("generate-carousel-pipeline",
          "/api/v1/ai/generate-carousel-pipeline" in paths)
except Exception:
    print("(social-api openapi unreachable)")

print("\n== n8n active workflows ==")
r = subprocess.run(["docker", "exec", "n8n", "n8n", "list:workflow",
                    "--active=true"], capture_output=True, text=True)
print(r.stdout if r.returncode == 0 else "(n8n CLI unavailable)")
