#!/usr/bin/env python3
"""Enable / verify / roll back GitHub merge queue on cu130-slim master.

Usage:
    merge_queue.py status    # show protection + ruleset state
    merge_queue.py enable    # readiness check -> strict=false -> ruleset
    merge_queue.py disable   # delete ruleset -> strict=true
    merge_queue.py verify    # confirm merge_group triggers on origin/master

Requires: gh CLI authenticated with repo admin (rulesets write).
"""
from __future__ import annotations

import json
import subprocess
import sys

REPO = "Themis128/cu130-slim"
RULESET_NAME = "master-merge-queue"
REQUIRED_CHECKS = ["validate", "secret-scan", "ruff", "test", "integration-test"]
# workflow file -> must contain a merge_group: trigger on origin/master
REQUIRED_WORKFLOWS = [
    ".github/workflows/docker-compose-validation.yml",
    ".github/workflows/secret-scan.yml",
    ".github/workflows/ruff-lint.yml",
    ".github/workflows/social-automation-backend.yml",
    ".github/workflows/integration-tests.yml",
]


def gh_api(path: str, method: str = "GET", payload: dict | None = None):
    cmd = ["gh", "api", path, "-X", method]
    if payload is not None:
        cmd += ["--input", "-"]
        proc = subprocess.run(
            cmd, input=json.dumps(payload), capture_output=True, text=True
        )
    else:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"gh api {method} {path} failed:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def protection() -> dict:
    return gh_api(f"repos/{REPO}/branches/master/protection")


def ruleset() -> dict | None:
    for rs in gh_api(f"repos/{REPO}/rulesets") or []:
        if rs.get("name") == RULESET_NAME:
            return gh_api(f"repos/{REPO}/rulesets/{rs['id']}")
    return None


def owner_type() -> str:
    repo = gh_api(f"repos/{REPO}") or {}
    return (repo.get("owner") or {}).get("type", "unknown")


def cmd_status() -> None:
    otype = owner_type()
    print(f"repo owner type: {otype}")
    if otype == "User":
        print("merge queue: UNAVAILABLE — GitHub gates merge queues to "
              "organization-owned repos (verified 2026-11-22: rulesets API "
              "rejects merge_queue with HTTP 422 on this repo). Requires org transfer.")
    prot = protection()
    checks = prot.get("required_status_checks") or {}
    print(f"classic protection strict: {checks.get('strict')}")
    print(f"required checks: {sorted(c['context'] for c in checks.get('checks', []))}")
    rs = ruleset()
    if rs:
        print(f"ruleset '{RULESET_NAME}': id={rs['id']} enforcement={rs['enforcement']}")
        for rule in rs.get("rules", []):
            print(f"  rule: {rule['type']} {json.dumps(rule.get('parameters', {}))[:200]}")
    else:
        print(f"ruleset '{RULESET_NAME}': absent")


def cmd_verify() -> None:
    ok = True
    for wf in REQUIRED_WORKFLOWS:
        content = gh_api(f"repos/{REPO}/contents/{wf}?ref=master")
        import base64

        text = base64.b64decode(content["content"]).decode()
        has_mg = "merge_group:" in text
        print(f"{'OK ' if has_mg else 'MISS'} {wf}")
        ok &= has_mg
    sys.exit(0 if ok else 1)


def cmd_enable() -> None:
    if owner_type() == "User":
        raise SystemExit(
            "merge queue is unavailable on user-owned repos — GitHub "
            "restricts it to organization-owned repositories (verified "
            "2026-11-22: rulesets API rejected merge_queue with HTTP 422 "
            "on Themis128/cu130-slim). Transfer the repo to an org first; "
            "pr-autopilot + auto-merge already cover this gap meanwhile."
        )
    cmd_verify()

    # 1) strict -> false so the queue (not manual update-branch) handles freshness
    prot = protection()
    if (prot.get("required_status_checks") or {}).get("strict"):
        body = {
            "required_status_checks": {
                "strict": False,
                "checks": [
                    {"context": c} for c in REQUIRED_CHECKS
                ],
            }
        }
        gh_api(
            f"repos/{REPO}/branches/master/protection/required_status_checks",
            "PATCH",
            body,
        )
        print("classic protection strict -> false")
    else:
        print("classic protection strict already false")

    # 2) merge-queue ruleset
    if ruleset():
        print(f"ruleset '{RULESET_NAME}' already exists")
        return
    payload = {
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/heads/master"], "exclude": []}},
        "rules": [
            {
                "type": "merge_queue",
                "parameters": {
                    "merge_method": "SQUASH",
                    "grouping_strategy": "ALLGREEN",
                    "max_entries_to_build": 5,
                    "max_entries_to_merge": 5,
                    "min_entries_to_merge": 1,
                    "min_entries_to_merge_wait_minutes": 5,
                    "check_response_timeout_minutes": 60,
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "required_status_checks": [
                        {"context": c} for c in REQUIRED_CHECKS
                    ],
                    "strict_required_status_checks_policy": False,
                },
            },
        ],
    }
    rs = gh_api(f"repos/{REPO}/rulesets", "POST", payload)
    print(f"ruleset created: id={rs['id']} enforcement={rs['enforcement']}")
    cmd_status()


def cmd_disable() -> None:
    rs = ruleset()
    if rs:
        gh_api(f"repos/{REPO}/rulesets/{rs['id']}", "DELETE")
        print(f"ruleset '{RULESET_NAME}' deleted")
    prot = protection()
    checks = prot.get("required_status_checks") or {}
    if not checks.get("strict"):
        gh_api(
            f"repos/{REPO}/branches/master/protection/required_status_checks",
            "PATCH",
            {
                "required_status_checks": {
                    "strict": True,
                    "checks": [{"context": c} for c in REQUIRED_CHECKS],
                }
            },
        )
        print("classic protection strict -> true (restored)")
    cmd_status()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"status": cmd_status, "verify": cmd_verify, "enable": cmd_enable, "disable": cmd_disable}[cmd]()
