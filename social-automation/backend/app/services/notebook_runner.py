"""Notebook-driven reporting — execute report notebooks via papermill.

Report notebooks live in ``/notebooks/reports/*.ipynb`` (host ``./notebooks``,
the same directory mounted into the Jupyter container at ``work/`` so the
same files are editable in the Jupyter UI). Papermill executes them inside
the worker container, so the kernel inherits the worker environment —
``DATABASE_URL``, AI/Slack secrets, and ``/app`` imports all work.

Contract between a report notebook and :func:`run_report_notebook`:

- The notebook has a cell tagged ``parameters``; papermill injects
  ``output_dir`` and any extra params there.
- The notebook writes its deliverables into ``output_dir`` and finally a
  manifest JSON ``<name>-<run_id>.manifest.json``::

      {"subject": "…", "html_file": "…", "text_file": "…",
       "attachments": [{"file": "chart.png", "cid": "pulse"}]}

- ``cid`` lets the HTML reference images inline as ``<img src="cid:pulse">``
  (renders in Gmail, unlike base64 data-URIs).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

NOTEBOOKS_DIR = Path("/notebooks")
REPORTS_DIR = NOTEBOOKS_DIR / "reports"
OUTPUT_DIR = NOTEBOOKS_DIR / "output"


def run_report_notebook(
    name: str,
    parameters: dict[str, Any] | None = None,
    *,
    cwd: str = "/app",
) -> dict[str, Any]:
    """Execute ``reports/<name>.ipynb`` with papermill and read its manifest.

    Returns ``{"notebook": str, "manifest": dict, "duration_s": float}``.
    Raises FileNotFoundError if the notebook is missing, RuntimeError with the
    papermill error if execution fails.
    """
    import papermill as pm

    src = REPORTS_DIR / f"{name}.ipynb"
    if not src.exists():
        raise FileNotFoundError(f"report notebook not found: {src}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    run_id = time.strftime("%Y%m%d-%H%M%S")
    out_nb = OUTPUT_DIR / f"{name}-{run_id}.ipynb"
    manifest_path = OUTPUT_DIR / f"{name}-{run_id}.manifest.json"

    t0 = time.monotonic()
    pm.execute_notebook(
        str(src),
        str(out_nb),
        parameters={
            "output_dir": str(OUTPUT_DIR),
            "run_id": run_id,
            "manifest_path": str(manifest_path),
            **(parameters or {}),
        },
        cwd=cwd,  # kernels see /app on sys.path → `import app.services.*` works
        progress_bar=False,
        log_output=False,
    )
    duration = round(time.monotonic() - t0, 1)

    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        logger.warning("Notebook %s produced no manifest at %s", name, manifest_path)

    logger.info(
        "Notebook report %s executed in %.1fs → %s", name, duration, out_nb
    )
    return {
        "notebook": str(out_nb),
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "duration_s": duration,
    }
