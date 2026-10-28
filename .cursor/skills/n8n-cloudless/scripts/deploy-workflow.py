#!/usr/bin/env python3
"""Import + publish Cloudless carousel workflow via n8n CLI, then restart."""

import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import die, repo_root  # noqa: E402

root = repo_root()
wf = root / "n8n-workflows" / "cloudless-carousel-pipeline.json"
wf_id = "cloudless-cf-carousel-linkedin"
if not wf.is_file():
    die(f"Missing {wf}")

subprocess.run(["docker", "cp", str(wf), "n8n:/tmp/cloudless-carousel-pipeline.json"], check=True)
subprocess.run(["docker", "exec", "n8n", "n8n", "import:workflow",
                "--input=/tmp/cloudless-carousel-pipeline.json"], check=True)
subprocess.run(["docker", "exec", "n8n", "n8n", "publish:workflow", f"--id={wf_id}"], check=True)
subprocess.run(["docker", "compose", "restart", "n8n"], cwd=root, check=True)

for _ in range(30):
    try:
        urllib.request.urlopen("http://127.0.0.1:5678/healthz", timeout=3)
        print("n8n healthy")
        break
    except urllib.error.URLError:
        time.sleep(2)

subprocess.run(["docker", "exec", "n8n", "n8n", "list:workflow", "--active=true"])
print("Deployed. Webhook: POST http://localhost:5678/webhook/cloudless-carousel")
