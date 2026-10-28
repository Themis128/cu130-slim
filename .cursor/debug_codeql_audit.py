#!/usr/bin/env python3
"""Debug diagnostic: verify CodeQL alert sites on HEAD (session d5a1cf)."""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

LOG = Path("/home/tbaltzakis/cu130-slim/.cursor/debug-d5a1cf.log")
ROOT = Path("/home/tbaltzakis/cu130-slim")


def log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    payload = {
        "sessionId": "d5a1cf",
        "runId": "codeql-audit-1",
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def main() -> None:
    run_id = "post-fix"
    def log2(hypothesis_id: str, location: str, message: str, data: dict) -> None:
        payload = {
            "sessionId": "d5a1cf",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")

    global log
    log = log2  # type: ignore[misc]

    head = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    log("A", "git:HEAD", "Current HEAD", {"head": head})

    # Hyp A/C: secret_store no longer logs keys
    ss = read("social-automation/backend/app/services/secret_store.py")
    logs_key = bool(re.search(r"logger\.(warning|error|exception)\([^)]*\bkey\b", ss))
    has_safe_helper = "_log_secret_failure" in ss and "type(exc).__name__" in ss
    log(
        "C",
        "secret_store.py",
        "secret_store logging posture",
        {"logs_key_name": logs_key, "has_safe_helper": has_safe_helper},
    )

    # Hyp C: sidecar temp under /data not os.tmpdir
    fb = read("facebook-browser-sidecar/server.js")
    li = read("linkedin-browser-sidecar/server.js")
    log(
        "C",
        "sidecars",
        "temp file helpers",
        {
            "fb_uses_tmpdir": "os.tmpdir()" in fb,
            "li_uses_tmpdir": "os.tmpdir()" in li,
            "fb_mkdtemp": "mkdtempSync" in fb,
            "li_mkdtemp": "mkdtempSync" in li,
            "fb_temp_root_data": "TEMP_ROOT" in fb and "/data/" in fb,
            "li_temp_root_data": "TEMP_ROOT" in li and "/data/" in li,
        },
    )

    # Hyp C: exception exposure
    profile = read("social-automation/backend/app/api/profile.py")
    mcp = read("social-automation/backend/app/api/mcp.py")
    accounts = read("social-automation/backend/app/api/accounts.py")
    log(
        "C",
        "api-exceptions",
        "client-facing exception text",
        {
            "profile_generic": '"Session check failed"' in profile and "Session check failed: {" not in profile.replace('"Session check failed"', ""),
            "mcp_internal_error": mcp.count('"Internal error"') >= 3,
            "mcp_str_e_remaining": bool(re.search(r'"error":\s*str\(e\)', mcp)),
            "accounts_network_generic": '"Network error"' in accounts and "Network error: {" not in accounts,
        },
    )

    # Hyp B: never-fixed sites
    inference = read("social-automation/backend/app/services/inference.py")
    used_assign = len(re.findall(r"used_provider\s*=", inference))
    me = read("social-automation/backend/app/worker/tasks/media_enhance.py")
    mime_init = 'mime_type = "image/jpeg"' in me
    ig = read("social-automation/backend/app/services/instagram_private_api.py")
    open_list_comp = "file_objs = [open(fp" in ig
    uses_exit_stack = "ExitStack" in ig and "enter_context(open" in ig
    script = read("social-automation/backend/app/scripts/instagram_profile_update.py")
    get_json = script[script.find("def _get_json") : script.find("def check_session")]
    get_has_urlerror = "URLError" in get_json
    has_die = "def _die" in script and "NoReturn" in script
    base = read("social-automation/backend/app/services/platforms/base.py")
    follower_same_line = "async def get_follower_count(self, account: SocialAccount) -> int: ..." in base
    log(
        "B",
        "never-fixed-sites",
        "HEAD quality findings after remediation",
        {
            "inference_used_provider_assigns": used_assign,
            "media_enhance_mime_default": mime_init,
            "instagram_open_without_with": open_list_comp,
            "instagram_uses_exit_stack": uses_exit_stack,
            "get_json_missing_urlerror": not get_has_urlerror,
            "has_die_noreturn": has_die,
            "follower_same_line_ellipsis": follower_same_line,
            "expected_clean": (
                used_assign == 1
                and not mime_init
                and not open_list_comp
                and uses_exit_stack
                and get_has_urlerror
                and has_die
                and follower_same_line
            ),
        },
    )

    # Hyp A: compare to last known CodeQL analysis sha
    log(
        "A",
        "codeql-staleness",
        "Alerts attributed to 98e918e while HEAD moved",
        {"alert_sha": "98e918e", "head": head, "head_ahead_of_alert_sha": True},
    )


if __name__ == "__main__":
    main()
    print("wrote", LOG)
