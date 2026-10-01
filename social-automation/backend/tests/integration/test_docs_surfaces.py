"""Documentation-coverage suite — every API surface that docs/CODEMAP.md and
docs/application-architecture.md claim as live must (a) exist in the app's
OpenAPI schema and (b) respond without a 5xx on the running stack.

Runs against the live compose stack (API_URL env, default http://localhost:8083).
Set SOCIAL_ADMIN_EMAIL / SOCIAL_ADMIN_PASSWORD (repo .env is auto-loaded) for
the authenticated probes; unauthenticated checks run regardless.
"""

import os
import re
from pathlib import Path

import httpx
import pytest

# Host runs hit the mapped port 8083; inside the social-api container the app
# listens on 8000 directly.
_in_container = Path("/.dockerenv").exists()
API_URL = os.environ.get(
    "API_URL", "http://localhost:8000" if _in_container else "http://localhost:8083"
).rstrip("/")


def _find_repo_root() -> Path | None:
    """Locate the repo root (has docs/CODEMAP.md).

    Search order: DOC_ROOT env (used when docs are staged into a container),
    then walk up from this file — <repo>/social-automation/backend/tests/
    integration/ on the host. Inside social-api the repo docs aren't mounted,
    so the doc-coverage class skips while live probes still run.
    """
    if os.environ.get("DOC_ROOT") and (Path(os.environ["DOC_ROOT"]) / "docs" / "CODEMAP.md").exists():
        return Path(os.environ["DOC_ROOT"])
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "docs" / "CODEMAP.md").exists():
            return parent
    return None


REPO_ROOT = _find_repo_root()
DOC_FILES = ["docs/CODEMAP.md", "docs/application-architecture.md", "AGENTS.md", "README.md"]

# Load repo .env for admin creds without printing anything.
_env = (REPO_ROOT / ".env") if REPO_ROOT else Path("/nonexistent")
if _env.exists():
    for line in _env.read_text().splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2).strip('"').strip("'")


def _documented_api_paths() -> set[str]:
    """Pull every `/api/v1/...` or `/health` path mentioned in the docs."""
    found: set[str] = set()
    if REPO_ROOT is None:
        return found
    for rel in DOC_FILES:
        f = REPO_ROOT / rel
        if not f.exists():
            continue
        for m in re.findall(r"(/api/v1/[a-zA-Z0-9_/{},\-]+/?|/health)", f.read_text()):
            found.add(m.rstrip(".,)"))
    return found


def _normalize(doc_path: str) -> str:
    """`{facebook,instagram}` or trailing `/` in docs → openapi-style prefix."""
    p = re.sub(r"\{[^}]*\}", "{x}", doc_path)
    return p.rstrip("/")


def _openapi_paths() -> dict:
    r = httpx.get(f"{API_URL}/openapi.json", timeout=15)
    r.raise_for_status()
    return r.json()["paths"]


def _admin_token() -> str | None:
    email = os.environ.get("SOCIAL_ADMIN_EMAIL")
    password = os.environ.get("SOCIAL_ADMIN_PASSWORD")
    if not email or not password:
        return None
    r = httpx.post(
        f"{API_URL}/api/v1/auth/login",
        data={"username": email, "password": password},
        timeout=15,
    )
    if r.status_code != 200:
        return None
    return r.json().get("access_token")


@pytest.mark.skipif(REPO_ROOT is None, reason="repo docs not mounted (running in container)")
class TestDocsCoverRealRoutes:
    """Every endpoint the docs claim must exist in the live OpenAPI schema."""

    def test_api_reachable(self):
        r = httpx.get(f"{API_URL}/health", timeout=10)
        assert r.status_code == 200

    def test_all_documented_paths_exist(self):
        schema_paths = {_normalize(p) for p in _openapi_paths()}
        missing = []
        for doc_path in sorted(_documented_api_paths()):
            # Multi-platform brace lists in docs (`{facebook,instagram}`) →
            # check the concrete first variant.
            probe_path = re.sub(r"\{([^,}]+)[^}]*\}", r"\1", doc_path)
            norm = _normalize(doc_path)
            # docs often give prefixes (/api/v1/auth/oauth/) — any schema path
            # starting with the normalized prefix counts as covered.
            if any(
                sp == norm or sp.startswith(norm + "/") or norm.startswith(sp + "/")
                for sp in schema_paths
            ):
                continue
            # Not in schema: may be hidden (include_in_schema=False like
            # /api/v1/health) or a platform-param route. Live-probe — anything
            # but 404/5xx proves the route exists.
            try:
                r = httpx.get(f"{API_URL}{probe_path}", timeout=15)
            except httpx.HTTPError:
                missing.append(f"{doc_path} (unreachable)")
                continue
            if r.status_code == 404 or r.status_code >= 500:
                missing.append(f"{doc_path} → {r.status_code}")
        assert not missing, f"documented but not served: {missing}"


class TestLiveProbes:
    """Smoke the documented live surfaces — none may 5xx."""

    # Public/unauthenticated surfaces claimed by the docs.
    PUBLIC_GETS = ["/health", "/api/v1/health"]

    # Authenticated GET surfaces documented in CODEMAP/application-architecture.
    AUTHED_GETS = [
        "/api/v1/accounts",
        "/api/v1/teams",
        "/api/v1/usage",
        "/api/v1/usage/history",
        "/api/v1/brand",
        "/api/v1/inbox/inbox",
        "/api/v1/cf-db/health",
        "/api/v1/cf-db/tables",
        "/api/v1/auth/me",
    ]

    @pytest.mark.parametrize("path", PUBLIC_GETS)
    def test_public_get(self, path: str):
        r = httpx.get(f"{API_URL}{path}", timeout=15)
        assert r.status_code < 500, f"{path} → {r.status_code}"
        assert r.status_code == 200, f"{path} → {r.status_code}"

    def test_admin_login(self):
        token = _admin_token()
        assert token, "admin login failed — check SOCIAL_ADMIN_* in .env"

    @pytest.mark.parametrize("path", AUTHED_GETS)
    def test_authed_get_no_5xx(self, path: str):
        token = _admin_token()
        if not token:
            pytest.skip("admin creds not configured")
        r = httpx.get(
            f"{API_URL}{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        assert r.status_code < 500, f"{path} → {r.status_code}: {r.text[:200]}"
