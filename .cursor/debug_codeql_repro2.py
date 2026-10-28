#!/usr/bin/env python3
"""Runtime evidence: alert SHAs vs HEAD + tip code posture (session d5a1cf)."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

LOG = Path("/home/tbaltzakis/cu130-slim/.cursor/debug-d5a1cf.log")
ROOT = Path("/home/tbaltzakis/cu130-slim")


def emit(hypothesis_id: str, location: str, message: str, data: dict, run_id: str = "repro-2") -> None:
    with LOG.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "sessionId": "d5a1cf",
                    "runId": run_id,
                    "hypothesisId": hypothesis_id,
                    "location": location,
                    "message": message,
                    "data": data,
                    "timestamp": int(time.time() * 1000),
                }
            )
            + "\n"
        )


def main() -> None:
    head = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    # GitHub alert sample via gh
    out = subprocess.check_output(
        [
            "gh",
            "api",
            "repos/Themis128/cu130-slim/code-scanning/alerts?state=open&ref=refs/heads/master&per_page=100",
            "--jq",
            '[.[] | select(.number == 7196 or .number == 7195 or .number == 7076) | {n:.number, sha:.most_recent_instance.commit_sha[0:7]}]',
        ],
        cwd=ROOT,
        text=True,
    )
    emit("A", "github-alerts", "Sample open alert SHAs", {"head": head, "sample": json.loads(out)})

    # Tip vs alert-sha for secret_store logging
    alert_blob = subprocess.check_output(
        ["git", "show", "98e918e:social-automation/backend/app/services/secret_store.py"],
        cwd=ROOT,
        text=True,
    )
    tip = (ROOT / "social-automation/backend/app/services/secret_store.py").read_text()
    emit(
        "C",
        "secret_store.py",
        "Alert SHA logs key names; tip does not",
        {
            "alert_sha_logs_key": "_safe_log_key(key)" in alert_blob,
            "tip_logs_key": "_safe_log_key" in tip or "for %s" in tip.split("def _log_secret_failure", 1)[-1][:400],
            "tip_type_only": 'logger.warning("%s (%s)", message, type(exc).__name__)' in tip,
        },
    )

    fb_alert = subprocess.check_output(
        ["git", "show", "98e918e:facebook-browser-sidecar/server.js"],
        cwd=ROOT,
        text=True,
    )
    fb_tip = (ROOT / "facebook-browser-sidecar/server.js").read_text()
    emit(
        "C",
        "facebook-browser-sidecar",
        "Temp file API on alert SHA vs tip",
        {
            "alert_uses_os_tmpdir": "os.tmpdir()" in fb_alert and "fb-sidecar-" in fb_alert,
            "tip_uses_os_tmpdir": "os.tmpdir()" in fb_tip,
            "tip_uses_mkdtemp": "mkdtempSync" in fb_tip and "TEMP_ROOT" in fb_tip,
        },
    )

    run = subprocess.check_output(
        [
            "gh",
            "run",
            "view",
            "34391883961",
            "--json",
            "status,conclusion,headSha,jobs",
            "-q",
            '{status,conclusion,sha:.headSha[0:7],codeql:[.jobs[]|select(.name|test("sast"))|{name,status,conclusion}]}',
        ],
        cwd=ROOT,
        text=True,
    )
    emit("E", "security.yml", "In-flight CodeQL on tip", json.loads(run))


if __name__ == "__main__":
    main()
    print("ok", LOG)
