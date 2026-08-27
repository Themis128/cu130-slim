#!/usr/bin/env python3
"""Import / publish the Cloudless CF carousel n8n workflow.

Preferred path (n8n 2.x): CLI inside the n8n container — works without API key.
  docker cp n8n-workflows/cloudless-carousel-pipeline.json n8n:/tmp/
  docker exec n8n n8n import:workflow --input=/tmp/cloudless-carousel-pipeline.json
  docker exec n8n n8n publish:workflow --id=cloudless-cf-carousel-linkedin
  docker compose restart n8n

API path (needs a valid key from Settings → n8n API):
  Reads N8N_API_URL + N8N_API_KEY from the environment.
  Does not print secrets.

Online notes (n8n docs / community):
- Auth header must be X-N8N-API-KEY (not Bearer) against /api/v1/...
- Keys are created in the UI; there is no N8N_API_KEY env that auto-provisions them.
- Owner recreate / encryption-key change invalidates old keys → 401 unauthorized.
- n8n 2.0+ uses publish/unpublish instead of active/inactive.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

WORKFLOW_NAME = "Cloudless CF Carousel → LinkedIn Company"
WORKFLOW_ID = "cloudless-cf-carousel-linkedin"
WORKFLOW_PATH = os.environ.get(
    "CLOUDLESS_N8N_WORKFLOW_PATH",
    "/workspace/n8n-workflows/cloudless-carousel-pipeline.json",
)


def _resolve_path() -> str:
    path = WORKFLOW_PATH
    if os.path.isfile(path):
        return path
    alt = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "n8n-workflows",
        "cloudless-carousel-pipeline.json",
    )
    if os.path.isfile(alt):
        return alt
    raise SystemExit(f"Workflow file not found: {path}")


def deploy_via_cli(path: str) -> dict:
    """Import + publish via n8n CLI (recommended for Docker self-host)."""
    container = os.environ.get("N8N_CONTAINER", "n8n")
    remote = "/tmp/cloudless-carousel-pipeline.json"
    subprocess.run(["docker", "cp", path, f"{container}:{remote}"], check=True)
    subprocess.run(
        ["docker", "exec", container, "n8n", "import:workflow", f"--input={remote}"],
        check=True,
    )
    subprocess.run(
        ["docker", "exec", container, "n8n", "publish:workflow", f"--id={WORKFLOW_ID}"],
        check=True,
    )
    return {
        "method": "cli",
        "workflow_id": WORKFLOW_ID,
        "name": WORKFLOW_NAME,
        "note": "Restart n8n if triggers were not picked up: docker compose restart n8n",
    }


def _req(method: str, url: str, api_key: str, body: dict | None = None) -> dict | list:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "X-N8N-API-KEY": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"n8n API {method} {url} -> {exc.code}: {detail[:500]}") from exc


def deploy_via_api(path: str) -> dict:
    api_url = (os.environ.get("N8N_API_URL") or "http://n8n:5678").rstrip("/")
    api_key = os.environ.get("N8N_API_KEY") or ""
    if not api_key:
        raise SystemExit(
            "N8N_API_KEY is not set. Create one in n8n UI: Settings → n8n API, "
            "or run with --cli instead."
        )

    with open(path, encoding="utf-8") as f:
        workflow = json.load(f)

    payload = {
        "name": workflow.get("name") or WORKFLOW_NAME,
        "nodes": workflow["nodes"],
        "connections": workflow["connections"],
        "settings": workflow.get("settings") or {"executionOrder": "v1"},
    }

    listed = _req("GET", f"{api_url}/api/v1/workflows", api_key)
    items = listed.get("data", listed) if isinstance(listed, dict) else listed
    existing_id = None
    for item in items or []:
        if isinstance(item, dict) and item.get("name") == payload["name"]:
            existing_id = item.get("id")
            break

    if existing_id:
        updated = _req("PUT", f"{api_url}/api/v1/workflows/{existing_id}", api_key, payload)
        wf_id = updated.get("id") or existing_id
        action = "updated"
    else:
        created = _req("POST", f"{api_url}/api/v1/workflows", api_key, payload)
        wf_id = created.get("id")
        action = "created"
        if not wf_id:
            raise SystemExit(f"Create succeeded but no id returned: {created}")

    # n8n 2.x: activate endpoint may still exist; publish is the source of truth
    try:
        activated = _req("POST", f"{api_url}/api/v1/workflows/{wf_id}/activate", api_key, {})
        active = bool(activated.get("active", True))
    except SystemExit:
        active = None

    return {
        "method": "api",
        "action": action,
        "workflow_id": wf_id,
        "name": payload["name"],
        "active": active,
        "webhook_path": "cloudless-carousel",
        "schedule": "every 2 days at 19:00 Europe/Athens",
    }


def main() -> None:
    path = _resolve_path()
    use_cli = "--cli" in sys.argv or os.environ.get("CLOUDLESS_N8N_DEPLOY", "cli") == "cli"
    result = deploy_via_cli(path) if use_cli else deploy_via_api(path)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
