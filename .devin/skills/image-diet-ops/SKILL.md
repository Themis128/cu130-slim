# image-diet-ops

Audit and slim the cu130-slim Docker images — dep reachability analysis plus
image/tag reporting. Use when slimming an image, splitting extras, checking
whether a dep can move to `worker`, or finding duplicate images that should
share one build.

## Architecture (post-2026-10-31)

- `social-automation/backend/Dockerfile` stages: `builder` (full venv,
  `-e .[worker]`), `builder-api` (slim venv, `-e .` core only),
  `runtime-common` (apt+code+user, no venv) → targets `base` (full, workers)
  and `api` (slim, ~0.5-0.9GB smaller).
- Images: `social-backend-latest` (workers+beat), `social-backend-api-latest`
  (api), `social-sidecar-base-latest` (fastapi/uvicorn/httpx/pydantic for
  messenger-sidecar, env-manager/backend, browser-novnc via `FROM ${SIDECAR_BASE}`).
- Legacy aliases still publish: `social-api-latest`, `social-worker-latest`,
  `social-worker-publishing-latest` — old checkouts/rollbacks keep resolving.

## Rules for moving a dep to `worker` extras

Safe ONLY if: zero top-level imports under `app/api/`/`app/main.py`, AND no
unguarded lazy `import` inside functions reachable from api routes. Guarded
imports (`try/except ImportError`, `contextlib.suppress`) degrade safely.

Watch transitive parents: `pymupdf>=2.0`→onnxruntime, `tweety-ns`→captcha
solvers+python-magic, `twscrape`→aiosqlite/fake-useragent. Moving a parent
removes its transitives; pinning a version can too (PyMuPDF<2.0 drops the
onnxruntime chain).

## Tool

`scripts/image_audit.py deps` — table of every dep: api top-level hits,
lazy hits, keep/move verdict. `images` — cu130-slim image sizes + which
services share each tag (catches duplicate builds of identical content).

## Workflow

1. `image_audit.py deps` → candidates for the `worker` extra.
2. Grep each candidate's import sites for unguarded api-reachability.
3. Move to `[project.optional-dependencies] worker`; keep api-reachable
   packages in core (e.g. PyMuPDF — research ingest + kakkava doctrine).
4. Rebuild `--target api`, compare `docker images` sizes, boot-test
   `from app.main import app` with env vars.
5. CI mirrors: matrix entries build `base` + `api`; publish canonical tags +
   legacy aliases; update docker-compose-validation.yml expectations.
