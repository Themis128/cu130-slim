#!/usr/bin/env python3
"""Enqueue a ComfyUI prompt from payload.json and wait for the output image.
Usage: generate_image.py"""

import json
import sys
import time
import urllib.request
from pathlib import Path

PAYLOAD_FILE = Path("payload.json")
COMFYUI_URL = "http://localhost:8000"


def post(path: str, body) -> dict:
    req = urllib.request.Request(
        f"{COMFYUI_URL}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def get(path: str) -> dict:
    return json.loads(
        urllib.request.urlopen(f"{COMFYUI_URL}{path}", timeout=15).read())


response = post("/prompt", json.loads(PAYLOAD_FILE.read_text()))
prompt_id = response["prompt_id"]
print(f"Enqueued prompt with ID: {prompt_id}")

for attempt in range(1, 7):
    time.sleep(5)
    history = get(f"/history/{prompt_id}")
    if history:
        outputs = history.get(prompt_id, {}).get("outputs", {})
        images = (outputs.get("3", {}) or {}).get("images", [])
        if images:
            filename = images[0].get("filename", "")
            print(f"Generated image filename: {filename}")
            out = Path(f"storage-user/output/{filename}")
            if out.is_file():
                print(f"Image successfully generated and saved to {out}")
                sys.exit(0)
            print(f"Error: File not found in {out}")
            sys.exit(1)
    print(f"Attempt {attempt}: Waiting for image to be generated...")

print("Timeout: Image generation did not complete in the expected time.")
sys.exit(1)
