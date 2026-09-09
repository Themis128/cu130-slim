#!/usr/bin/env python3
"""Evidence log for CodeQL tip alerts (session d5a1cf)."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

LOG = Path("/home/tbaltzakis/cu130-slim/.cursor/debug-d5a1cf.log")
ROOT = Path("/home/tbaltzakis/cu130-slim")


def emit(hid: str, loc: str, msg: str, data: dict, run_id: str = "fix-batch") -> None:
    with LOG.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "sessionId": "d5a1cf",
                    "runId": run_id,
                    "hypothesisId": hid,
                    "location": loc,
                    "message": msg,
                    "data": data,
                    "timestamp": int(time.time() * 1000),
                }
            )
            + "\n"
        )


def main() -> None:
    dmr = (ROOT / "social-automation/backend/app/services/dmr.py").read_text()
    ai = (ROOT / "social-automation/backend/app/api/ai.py").read_text()
    ig = (ROOT / "social-automation/backend/app/services/instagram_private_api.py").read_text()
    script = (ROOT / "social-automation/backend/app/scripts/instagram_profile_update.py").read_text()

    # H1 log injection: user/model strings in logger calls
    log_lines = []
    for i, line in enumerate(dmr.splitlines(), 1):
        if "logger." in line and any(x in line for x in ("model", "exc", "keep_alive", "draft")):
            log_lines.append({"line": i, "snippet": line.strip()[:120]})
    emit("H1", "dmr.py", "logger sinks with tainted-looking args", {"count": len(log_lines), "samples": log_lines[:12]})

    emoji_log = [ln for ln in ai.splitlines() if "Generated" in ln and "concept" in ln]
    emit("H1", "ai.py", "emoji generate log", {"lines": emoji_log[:3]})

    # H2 file close
    emit(
        "H2",
        "instagram_private_api.py",
        "album open pattern",
        {
            "exit_stack_listcomp": "enter_context(open(fp" in ig,
            "explicit_for_open": "for fp in file_paths:" in ig and "open(fp" in ig,
        },
    )

    # H3 mixed returns
    emit(
        "H3",
        "instagram_profile_update.py",
        "die/noreturn pattern",
        {
            "has_die": "def _die" in script,
            "post_has_raise_after_die": bool(
                re.search(r"_die\([^\)]*\)\s*\n\s*raise ", script)
            ),
            "inline_systemexit": "raise SystemExit" in script and "_die" not in script.split("def _post_json")[1][:400],
        },
    )

    # H4 dual import
    emit(
        "H4",
        "ai.py:dmr_warmup",
        "import style",
        {
            "has_import_module": "import app.services.dmr as dmr_mod" in ai,
            "has_from_import_warmup": "from app.services.dmr import warmup_models" in ai
            or "from app.services.dmr import" in ai and "warmup_models" in ai,
        },
    )

    # H5 unused global
    emit(
        "H5",
        "dmr.py",
        "global keyword usage",
        {
            "global_stmts": len(re.findall(r"^\s*global ", dmr, flags=re.M)),
            "uses_state_object": "class _DmrRuntime" in dmr or "_runtime." in dmr,
        },
    )


if __name__ == "__main__":
    main()
    print("wrote", LOG)
