"""Documentation-coverage suite — every API surface that docs/CODEMAP.md and
docs/application-architecture.md claim as live must (a) exist in the app's
OpenAPI schema and (b) respond without a 5xx on the running stack.

Runs against the live compose stack (API_URL env, default http://localhost:8083).
Set SOCIAL_ADMIN_EMAIL / SOCIAL_ADMIN_PASSWORD (repo .env is auto-loaded) for
the authenticated probes; unauthenticated checks run regardless.
"""

import functools
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


@functools.lru_cache(maxsize=1)
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

    def test_every_route_is_inventoried(self):
        """Reverse coverage: every live route must appear in
        docs/api-surface-inventory.md (regenerate when adding routes)."""
        inv = REPO_ROOT / "docs" / "api-surface-inventory.md"
        if not inv.exists():
            pytest.fail("docs/api-surface-inventory.md missing — regenerate it")
        listed = set(re.findall(r"`(/api/v1/[^`\s]+|/health)`", inv.read_text()))
        missing = [
            p for p in _openapi_paths()
            if p not in listed
        ]
        assert not missing, f"routes missing from inventory: {missing}"


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
        if not (os.environ.get("SOCIAL_ADMIN_EMAIL") and os.environ.get("SOCIAL_ADMIN_PASSWORD")):
            pytest.skip("admin creds not configured")
        assert token, "admin login failed — check SOCIAL_ADMIN_* in .env"

    @pytest.mark.parametrize("path", AUTHED_GETS)
    def test_authed_get_no_5xx(self, path: str):
        token = _admin_token()
        if not token:
            pytest.skip("admin creds not configured")
        r = httpx.get(
            f"{API_URL}{path}",
            headers={"Authorization": f"Bearer {token}"},
            # inbox aggregates across sidecars — can be slow
            timeout=120,
        )
        assert r.status_code < 500, f"{path} → {r.status_code}: {r.text[:200]}"


# ---------------------------------------------------------------------------
# Full-route sweep — every GET in the OpenAPI schema gets a real request.
# ---------------------------------------------------------------------------

# GETs that legitimately cause work or external calls — excluded from the sweep
# but still checked for existence via the schema itself.
SWEEP_SKIP = re.compile(
    r"oauth/.*/(authorize|callback)$|instagram2?/authorize|instagram2/callback|"
    r"export-data|reports/export|generate-image|webhook|data-deletion"
)

# GET endpoints that must respond WITHOUT auth (everything else must 401/403).
KNOWN_PUBLIC_GET = {
    "/health",
    "/api/v1/health",
    "/api/v1/media/view",  # public media serving — Access bypass by design
    "/api/v1/cf-db/health",  # documented ops probe (tokens_available counts, no secrets)
    "/api/v1/ai-providers/catalog",  # deliberately public — static provider metadata
    "/api/v1/openapi.json",  # if served under /api/v1
}

DUMMY_ID = "00000000-0000-0000-0000-000000000000"


def _fill_params(path: str, ids: dict[str, str]) -> str:
    """Substitute {param} path params with real IDs where we have them."""
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name == "platform":
            return "facebook"
        if name in ids:
            return ids[name]
        # heuristic: {x_id} / {xId} → look up by suffix or return dummy
        for k, v in ids.items():
            if name.endswith(k.removesuffix("_id")) or k.endswith(name):
                return v
        return DUMMY_ID
    return re.sub(r"\{([^}]+)\}", repl, path)


def _seed_ids(token: str | None) -> dict[str, str]:
    """Pull one real ID per resource type so param'd routes hit real rows."""
    ids: dict[str, str] = {}
    if not token:
        return ids
    headers = {"Authorization": f"Bearer {token}"}
    sources = {
        "account_id": "/api/v1/accounts",
        "team_id": "/api/v1/teams",
        "post_id": "/api/v1/content/posts?limit=1",
        "asset_id": "/api/v1/media?limit=1",
        "queue_id": "/api/v1/publishing/queue?limit=1",
        "collection_id": "/api/v1/media/collections",
    }
    for key, url in sources.items():
        try:
            r = httpx.get(f"{API_URL}{url}", headers=headers, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            items = data if isinstance(data, list) else data.get(
                "items") or data.get("accounts") or data.get("posts") or data.get("assets") or data.get("teams") or data.get("results") or []
            if items:
                first = items[0]
                ids[key] = str(first.get("id") or first.get("account_id") or first.get("team_id") or DUMMY_ID)
        except Exception:
            continue
    return ids


@pytest.fixture(scope="module")
def api_client():
    with httpx.Client(base_url=API_URL, timeout=45) as c:
        yield c


@pytest.fixture(scope="module")
def admin_token():
    t = _admin_token()
    if not t:
        pytest.skip("admin creds not configured")
    return t


@pytest.fixture(scope="module")
def get_routes():
    paths = _openapi_paths()
    return [
        p for p, ops in paths.items()
        if "get" in ops and not SWEEP_SKIP.search(p)
    ]


class TestFullGetSweep:
    """Every GET route in the schema gets a real request — none may 5xx."""

    def test_all_gets_respond_under_500(self, api_client, admin_token, get_routes):
        ids = _seed_ids(admin_token)
        headers = {"Authorization": f"Bearer {admin_token}"}
        failures = []
        for p in get_routes:
            url = _fill_params(p, ids)
            try:
                r = api_client.get(url, headers=headers)
            except httpx.HTTPError as e:
                failures.append(f"{p} → {type(e).__name__}")
                continue
            if r.status_code >= 500:
                failures.append(f"{p} → {r.status_code}: {r.text[:120]}")
        assert not failures, "5xx GETs:\n" + "\n".join(failures)

    def test_no_unintentionally_public_gets(self, api_client, get_routes):
        """Security net: GET without auth must 401/403 unless allowlisted."""
        leaks = []
        for p in get_routes:
            url = _fill_params(p, {})
            try:
                r = api_client.get(url)
            except httpx.HTTPError:
                continue
            if r.status_code == 200 and p not in KNOWN_PUBLIC_GET:
                leaks.append(f"{p} → 200 without auth")
        assert not leaks, "unauthenticated 200s (potential data leak):\n" + "\n".join(leaks)


class TestPostFlows:
    """Key POST surfaces — negative + validation paths, no live mutations."""

    WEBHOOK_ROUTES = [
        "/api/v1/messenger/webhook",
        "/api/v1/telegram/webhook/",
        "/api/v1/whatsapp/webhook",
        "/api/v1/billing/polar-webhook",
        "/api/v1/auth/data-deletion",
    ]

    @pytest.mark.parametrize("path", WEBHOOK_ROUTES)
    def test_webhook_rejects_unsigned_post(self, path: str):
        """Webhooks must reject unsigned/forged bodies — never 200/5xx.

        Only meaningful when the platform secrets are configured — a bare
        CI deployment accepts/drops payloads differently by design, so this
        check is gated to the live stack.
        """
        if os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.skip("webhook secrets not configured in CI — signature checks are off")
        r = httpx.post(
            f"{API_URL}{path}",
            json={"forged": True},
            timeout=15,
        )
        assert r.status_code not in (200, 201), (
            f"{path} accepted an unsigned webhook → {r.status_code}"
        )
        assert r.status_code < 500, f"{path} → {r.status_code}: {r.text[:120]}"

    def test_login_rejects_bad_credentials(self):
        r = httpx.post(
            f"{API_URL}/api/v1/auth/login",
            data={"username": "nobody@example.invalid", "password": "wrong"},
            timeout=15,
        )
        assert r.status_code in (400, 401, 403, 429)

    def test_leads_public_rejects_empty(self):
        r = httpx.post(f"{API_URL}/api/v1/leads/public", json={}, timeout=15)
        assert r.status_code in (400, 401, 403, 422)
        assert r.status_code < 500
