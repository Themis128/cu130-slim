#!/usr/bin/env python3
"""Audit n8n workflow JSONs for silent-failure HTTP-node patterns.

Two production pitfalls burned 2026-10-15 (see omv-nextcloud-ops SKILL.md):

1. httpRequest typeVersion >= 4.2 silently ignores `contentType:'json'` —
   the body ships EMPTY (no error, remote API 400s). Correct contract is
   `specifyBody:'json'` + `jsonBody` (or 'form-urlencoded'/keypair modes
   via `bodyParameters`).

2. `httpBasicAuth` credentials attached to nodes can silently ship a
   bad/missing Authorization header in production (seen 2026-10-15: node
   got NC guest login HTML while an identical in-container request with
   the same creds worked — credential store vs node-wiring is opaque and
   un-debuggable from the outside). Note: some workflows DO authenticate
   fine with them (nextcloud-drop-to-draft runs cleanly), so this is a
   reliability warning, not a hard failure. Proven-reliable pattern:
   build `Authorization` headers explicitly (`Basic <base64>`/`Bearer
   <token>`) from $env in a Code node — used by the digest workflow's
   IntraVox nodes.

Usage: audit-http-nodes.py [workflow-dir]   (default: ./n8n-workflows)
Exit 1 if any findings — usable as a CI/pre-deploy gate.
"""

import json
import sys
from pathlib import Path

FINDINGS = []


def audit(path: Path) -> None:
    try:
        wf = json.loads(path.read_text())
    except Exception as exc:
        FINDINGS.append((path.name, "-", f"unparseable JSON: {exc}"))
        return
    for node in wf.get("nodes", []):
        if node.get("type") != "n8n-nodes-base.httpRequest":
            continue
        p = node.get("parameters", {})
        name = node.get("name", node.get("id", "?"))
        tv = node.get("typeVersion", 0)

        # Pattern 1: contentType json with no body source
        if p.get("sendBody") and p.get("contentType") == "json" and not p.get("specifyBody") and not p.get("jsonBody"):
            FINDINGS.append((
                path.name, name,
                f"contentType:'json' without specifyBody/jsonBody (v{tv}) — body will ship EMPTY",
            ))
        if p.get("specifyBody") == "json" and not p.get("jsonBody"):
            FINDINGS.append((
                path.name, name,
                "specifyBody:'json' with empty jsonBody — body will ship EMPTY",
            ))

        # Pattern 2: basic-auth credential reliance
        creds = node.get("credentials") or {}
        if "httpBasicAuth" in creds or p.get("authentication") == "genericCredentialType" and p.get("genericAuthType") == "httpBasicAuth":
            FINDINGS.append((
                path.name, name,
                "httpBasicAuth credential — verify auth actually lands in prod; explicit Authorization header is the proven pattern",
            ))
        # pattern 2b: authentication selected but no header params + no creds = likely broken
        if p.get("authentication") in ("predefinedCredentialType", "genericCredentialType") and not creds:
            FINDINGS.append((
                path.name, name,
                f"authentication={p.get('authentication')} but no credentials attached — request goes out unauthenticated",
            ))


def main() -> int:
    wf_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "n8n-workflows")
    files = sorted(wf_dir.glob("*.json"))
    if not files:
        print(f"no workflow JSONs under {wf_dir}")
        return 0
    for f in files:
        audit(f)
    if not FINDINGS:
        print(f"OK — {len(files)} workflow(s), no HTTP-node pitfalls found")
        return 0
    for wf, node, msg in FINDINGS:
        print(f"{wf}: [{node}] {msg}")
    print(f"\n{len(FINDINGS)} finding(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
