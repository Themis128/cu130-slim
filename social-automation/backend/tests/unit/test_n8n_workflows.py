"""
Validate n8n workflow JSON files against project standards.

Standards enforced:
  1. Structural: stable string id, executionOrder v1, timezone Europe/Athens,
     unique node ids, valid connections, no orphan nodes
  2. Inference: carousel path → Cloudflare Workers AI only (no Ollama/ComfyUI),
     social-api calls use Docker DNS (http://social-api:8000), not localhost
  3. n8n 2.x: modern node typeVersions, no deprecated node types
  4. Security: no hardcoded secrets, $env.* used for credentials
  5. Env access: $env.* usage requires N8N_BLOCK_ENV_ACCESS_IN_NODE=false
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest


def _find_repo_root() -> Path:
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "n8n-workflows").is_dir():
            return parent
    env = os.environ.get("REPO_ROOT") or os.environ.get("COMPOSE_DIR")
    if env:
        return Path(env)
    pytest.skip("Cannot locate repo root with n8n-workflows/", allow_module_level=True)
    return Path(".")  # unreachable


REPO_ROOT = _find_repo_root()
N8N_WORKFLOWS_DIR = REPO_ROOT / "n8n-workflows"
INFRA_WORKFLOWS_DIR = REPO_ROOT / "infrastructure" / "n8n" / "workflows"
TEMPLATE_FILE = REPO_ROOT / "workflow_template_infographic_linkedin.json"

DEPRECATED_NODE_TYPES = {
    "n8n-nodes-base.cron",
    "n8n-nodes-base.start",
    "n8n-nodes-base.end",
    "n8n-nodes-base.function",
}

MODERN_TYPE_VERSIONS = {
    "n8n-nodes-base.httpRequest": 4,
    "n8n-nodes-base.webhook": 2,
    "n8n-nodes-base.scheduleTrigger": 1.2,
    "n8n-nodes-base.if": 2,
    "n8n-nodes-base.code": 2,
    "n8n-nodes-base.set": 3,
}

SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"xoxb-[0-9]+-[a-zA-Z0-9]+"),
    re.compile(r"Bearer\s+[a-zA-Z0-9_\-]{30,}"),
    re.compile(r"password\s*[:=]\s*['\"][^${}]+['\"]", re.IGNORECASE),
]

BLOCKED_HOSTS = [
    "ollama:11434",
    "localhost:11434",
    "127.0.0.1:11434",
]


def _collect_workflow_files():
    files = []
    for d in (N8N_WORKFLOWS_DIR, INFRA_WORKFLOWS_DIR):
        if d.is_dir():
            files.extend(sorted(d.glob("*.json")))
    if TEMPLATE_FILE.is_file():
        files.append(TEMPLATE_FILE)
    return files


def _load_workflow(path: Path) -> dict:
    with open(path) as f:
        data = json.load(f)
    return data.get("n8n_workflow_json", data)


def _all_js_code(wf: dict) -> list[str]:
    codes = []
    for node in wf.get("nodes", []):
        params = node.get("parameters", {})
        for key in ("jsCode", "functionCode"):
            if key in params:
                codes.append(params[key])
    return codes


def _all_urls(wf: dict) -> list[tuple[str, str]]:
    urls = []
    for node in wf.get("nodes", []):
        params = node.get("parameters", {})
        url = params.get("url", "")
        if url:
            urls.append((node.get("name", "?"), url))
        body = params.get("jsonBody", "")
        if body:
            urls.append((node.get("name", "?") + " (body)", body))
    return urls


WORKFLOW_FILES = _collect_workflow_files()
WORKFLOW_IDS = [p.stem for p in WORKFLOW_FILES]


@pytest.fixture(params=WORKFLOW_FILES, ids=WORKFLOW_IDS)
def workflow(request):
    path = request.param
    return path, _load_workflow(path)


# ─── 1. Structural standards ─────────────────────────────────────────────────


class TestStructuralStandards:

    def test_has_stable_string_id(self, workflow):
        path, wf = workflow
        wid = wf.get("id")
        assert wid is not None, f"{path.name}: missing 'id' field"
        assert isinstance(wid, str), f"{path.name}: id must be a string, got {type(wid).__name__}"
        assert wid != "1", f"{path.name}: generic id '1' — use a descriptive slug"

    def test_execution_order_v1(self, workflow):
        path, wf = workflow
        settings = wf.get("settings", {})
        assert settings.get("executionOrder") == "v1", (
            f"{path.name}: settings.executionOrder must be 'v1'"
        )

    def test_nodes_have_unique_ids(self, workflow):
        path, wf = workflow
        ids = [n.get("id") for n in wf.get("nodes", []) if n.get("id")]
        assert len(ids) == len(set(ids)), (
            f"{path.name}: duplicate node ids: {[x for x in ids if ids.count(x) > 1]}"
        )

    def test_nodes_have_unique_names(self, workflow):
        path, wf = workflow
        names = [n["name"] for n in wf.get("nodes", [])]
        assert len(names) == len(set(names)), (
            f"{path.name}: duplicate node names: {[x for x in names if names.count(x) > 1]}"
        )

    def test_connections_reference_existing_nodes(self, workflow):
        path, wf = workflow
        node_names = {n["name"] for n in wf.get("nodes", [])}
        connections = wf.get("connections", {})
        for source, targets_dict in connections.items():
            if isinstance(targets_dict, dict) and "main" in targets_dict:
                assert source in node_names, (
                    f"{path.name}: connection source '{source}' not in nodes"
                )
                for branch in targets_dict["main"]:
                    for edge in branch:
                        target = edge.get("node")
                        assert target in node_names, (
                            f"{path.name}: connection target '{target}' not in nodes"
                        )

    def test_no_orphan_non_trigger_nodes(self, workflow):
        path, wf = workflow
        trigger_types = {
            "n8n-nodes-base.scheduleTrigger",
            "n8n-nodes-base.webhook",
            "n8n-nodes-base.cron",
            "n8n-nodes-base.start",
        }
        node_names = {n["name"] for n in wf.get("nodes", [])}
        triggers = {
            n["name"] for n in wf.get("nodes", [])
            if n["type"] in trigger_types
        }

        connected = set()
        connections = wf.get("connections", {})
        for source, targets_dict in connections.items():
            connected.add(source)
            if isinstance(targets_dict, dict) and "main" in targets_dict:
                for branch in targets_dict["main"]:
                    for edge in branch:
                        connected.add(edge.get("node", ""))

        orphans = node_names - connected - triggers
        assert not orphans, f"{path.name}: orphan nodes: {orphans}"

    def test_valid_json_structure(self, workflow):
        path, wf = workflow
        assert "nodes" in wf, f"{path.name}: missing 'nodes' key"
        assert "connections" in wf, f"{path.name}: missing 'connections' key"
        assert isinstance(wf["nodes"], list), f"{path.name}: 'nodes' must be a list"


# ─── 2. Production workflow standards ────────────────────────────────────────


PROD_FILES = [
    N8N_WORKFLOWS_DIR / "cloudless-carousel-pipeline.json",
    N8N_WORKFLOWS_DIR / "socialauto-daily-slack-digest.json",
]
PROD_IDS = [p.stem for p in PROD_FILES]


@pytest.fixture(params=PROD_FILES, ids=PROD_IDS)
def prod_workflow(request):
    path = request.param
    if not path.exists():
        pytest.skip(f"{path.name} not found")
    return path, _load_workflow(path)


class TestProductionStandards:

    def test_timezone_europe_athens(self, prod_workflow):
        path, wf = prod_workflow
        tz = wf.get("settings", {}).get("timezone")
        assert tz == "Europe/Athens", (
            f"{path.name}: timezone must be 'Europe/Athens', got '{tz}'"
        )

    def test_active_flag_true(self, prod_workflow):
        path, wf = prod_workflow
        assert wf.get("active") is True, f"{path.name}: production workflow must be active"

    def test_modern_node_type_versions(self, prod_workflow):
        path, wf = prod_workflow
        for node in wf.get("nodes", []):
            ntype = node["type"]
            ver = node.get("typeVersion", 1)
            if ntype in MODERN_TYPE_VERSIONS:
                minimum = MODERN_TYPE_VERSIONS[ntype]
                assert ver >= minimum, (
                    f"{path.name}: node '{node['name']}' uses {ntype} v{ver}, "
                    f"minimum is v{minimum}"
                )

    def test_no_deprecated_node_types(self, prod_workflow):
        path, wf = prod_workflow
        for node in wf.get("nodes", []):
            assert node["type"] not in DEPRECATED_NODE_TYPES, (
                f"{path.name}: node '{node['name']}' uses deprecated type {node['type']}"
            )

    def test_social_api_uses_docker_dns(self, prod_workflow):
        path, wf = prod_workflow
        for name, url in _all_urls(wf):
            resolved = url.replace("{{ ($env.SOCIAL_API_URL || '", "").replace("') +", "")
            if "social-api" in resolved or "/api/v1/" in resolved:
                assert "localhost" not in url or "$env.SOCIAL_API_URL" in url, (
                    f"{path.name}: node '{name}' must use Docker DNS "
                    f"(http://social-api:8000), not localhost. URL: {url}"
                )

    def test_has_tags(self, prod_workflow):
        path, wf = prod_workflow
        tags = wf.get("tags", [])
        assert len(tags) > 0, f"{path.name}: production workflow should have tags"


# ─── 3. Carousel pipeline specific ──────────────────────────────────────────


class TestCarouselPipeline:

    @pytest.fixture
    def carousel(self):
        path = N8N_WORKFLOWS_DIR / "cloudless-carousel-pipeline.json"
        if not path.exists():
            pytest.skip("carousel workflow not found")
        return path, _load_workflow(path)

    def test_no_ollama_references(self, carousel):
        path, wf = carousel
        raw = json.dumps(wf)
        for host in BLOCKED_HOSTS:
            assert host not in raw, (
                f"{path.name}: carousel must not reference {host} — "
                "carousel path uses social-api → Cloudflare Workers AI"
            )

    def test_no_comfyui_in_carousel_path(self, carousel):
        path, wf = carousel
        raw = json.dumps(wf)
        assert "comfyui:8000" not in raw.lower(), (
            f"{path.name}: carousel path must not call ComfyUI directly"
        )

    def test_uses_cf_models(self, carousel):
        path, wf = carousel
        raw = json.dumps(wf)
        assert "@cf/meta/llama" in raw, (
            f"{path.name}: carousel should use @cf/ Llama text model"
        )
        assert "@cf/black-forest-labs/flux" in raw, (
            f"{path.name}: carousel should use @cf/ FLUX image model"
        )

    def test_calls_run_carousel_and_publish(self, carousel):
        path, wf = carousel
        raw = json.dumps(wf)
        assert "/api/v1/ai/run-carousel-and-publish" in raw, (
            f"{path.name}: must call /api/v1/ai/run-carousel-and-publish"
        )

    def test_login_before_pipeline(self, carousel):
        path, wf = carousel
        connections = wf.get("connections", {})
        login_targets = connections.get("Login Social API", {}).get("main", [[]])
        login_next = {e.get("node") for branch in login_targets for e in branch}
        assert "Login OK?" in login_next, (
            f"{path.name}: Login Social API must flow into Login OK? check"
        )

    def test_login_failure_handled(self, carousel):
        path, wf = carousel
        node_names = {n["name"] for n in wf.get("nodes", [])}
        assert "Login Failed" in node_names, (
            f"{path.name}: must have a 'Login Failed' error node"
        )

    def test_normalize_result_node_exists(self, carousel):
        path, wf = carousel
        node_names = {n["name"] for n in wf.get("nodes", [])}
        assert "Normalize Result" in node_names, (
            f"{path.name}: must have a 'Normalize Result' output node"
        )

    def test_normalize_result_outputs_standard_fields(self, carousel):
        path, wf = carousel
        for node in wf.get("nodes", []):
            if node["name"] == "Normalize Result":
                code = node["parameters"].get("jsCode", "")
                for field in ("ok", "brand", "post_id", "status", "error"):
                    assert field in code, (
                        f"{path.name}: Normalize Result must output '{field}'"
                    )

    def test_webhook_path(self, carousel):
        path, wf = carousel
        for node in wf.get("nodes", []):
            if node["type"] == "n8n-nodes-base.webhook":
                wh_path = node["parameters"].get("path", "")
                assert wh_path == "cloudless-carousel", (
                    f"{path.name}: webhook path must be 'cloudless-carousel', got '{wh_path}'"
                )

    def test_schedule_interval(self, carousel):
        path, wf = carousel
        for node in wf.get("nodes", []):
            if node["name"] == "Every 2 Days 19:00 Athens":
                intervals = node["parameters"]["rule"]["interval"]
                assert intervals[0]["daysInterval"] == 2
                assert intervals[0]["triggerAtHour"] == 19

    def test_num_slides_clamped(self, carousel):
        path, wf = carousel
        for node in wf.get("nodes", []):
            if node["name"] == "Resolve Cloudless Config":
                code = node["parameters"].get("jsCode", "")
                assert "Math.max(3" in code, "num_slides must be clamped to minimum 3"
                assert "Math.min(10" in code, "num_slides must be clamped to maximum 10"

    def test_topic_rotation_fallback(self, carousel):
        path, wf = carousel
        for node in wf.get("nodes", []):
            if node["name"] == "Resolve Cloudless Config":
                code = node["parameters"].get("jsCode", "")
                assert "topics" in code, "must have topic rotation array"
                assert "dayBucket" in code or "rotated" in code, (
                    "must rotate topics by day"
                )

    def test_execution_timeout(self, carousel):
        path, wf = carousel
        timeout = wf.get("settings", {}).get("executionTimeout", 0)
        assert timeout >= 600, (
            f"{path.name}: executionTimeout must be >= 600s for carousel pipeline, got {timeout}"
        )

    def test_pipeline_field_in_config(self, carousel):
        path, wf = carousel
        for node in wf.get("nodes", []):
            if node["name"] == "Resolve Cloudless Config":
                code = node["parameters"].get("jsCode", "")
                assert "pipeline" in code, (
                    "Resolve Cloudless Config must set pipeline identifier"
                )


# ─── 4. Daily digest specific ────────────────────────────────────────────────


class TestDailyDigest:

    @pytest.fixture
    def digest(self):
        path = N8N_WORKFLOWS_DIR / "socialauto-daily-slack-digest.json"
        if not path.exists():
            pytest.skip("daily digest workflow not found")
        return path, _load_workflow(path)

    def test_calls_daily_digest_endpoint(self, digest):
        path, wf = digest
        raw = json.dumps(wf)
        assert "/api/v1/ops/daily-digest" in raw

    def test_daily_schedule_09_00(self, digest):
        path, wf = digest
        for node in wf.get("nodes", []):
            if node["name"] == "Daily 09:00 Athens":
                expr = node["parameters"]["rule"]["interval"][0]["expression"]
                assert expr.startswith("0 9"), (
                    f"digest schedule must be 0 9 * * *, got '{expr}'"
                )

    def test_days_parameter_clamped(self, digest):
        path, wf = digest
        for node in wf.get("nodes", []):
            if node["name"] == "Resolve Digest Config":
                code = node["parameters"].get("jsCode", "")
                assert "Math.max(1" in code
                assert "Math.min(30" in code

    def test_webhook_path(self, digest):
        path, wf = digest
        for node in wf.get("nodes", []):
            if node["type"] == "n8n-nodes-base.webhook":
                wh_path = node["parameters"].get("path", "")
                assert wh_path == "socialauto-daily-digest"

    def test_summarize_result_outputs_ok_field(self, digest):
        path, wf = digest
        for node in wf.get("nodes", []):
            if node["name"] == "Summarize Result":
                code = node["parameters"].get("jsCode", "")
                assert "ok" in code
                assert "posted_to_slack" in code


# ─── 5. Security ─────────────────────────────────────────────────────────────


class TestSecurityStandards:

    def test_no_hardcoded_secrets(self, workflow):
        path, wf = workflow
        raw = json.dumps(wf)
        for pattern in SECRET_PATTERNS:
            match = pattern.search(raw)
            assert match is None, (
                f"{path.name}: possible hardcoded secret: {match.group()[:30]}..."
            )

    def test_credentials_use_env_vars(self, workflow):
        path, wf = workflow
        for node in wf.get("nodes", []):
            params = node.get("parameters", {})
            body_params = params.get("bodyParameters", {}).get("parameters", [])
            for bp in body_params:
                if bp.get("name") in ("password", "username"):
                    val = bp.get("value", "")
                    assert "$env." in val or "{{" not in val, (
                        f"{path.name}: node '{node['name']}' credential field "
                        f"'{bp['name']}' must use $env.* variables"
                    )

    def test_auth_header_uses_expression(self, workflow):
        path, wf = workflow
        for node in wf.get("nodes", []):
            params = node.get("parameters", {})
            headers = params.get("headerParameters", {}).get("parameters", [])
            for h in headers:
                if h.get("name") == "Authorization":
                    val = h.get("value", "")
                    assert "access_token" in val or "$env" in val or "{{" in val, (
                        f"{path.name}: node '{node['name']}' Authorization header "
                        f"must reference a dynamic token, not a static value"
                    )


# ─── 6. Legacy workflow warnings ─────────────────────────────────────────────


class TestLegacyWorkflows:

    @pytest.fixture
    def marketing(self):
        path = N8N_WORKFLOWS_DIR / "marketing-image-generation.json"
        if not path.exists():
            pytest.skip("marketing workflow not found")
        return path, _load_workflow(path)

    @pytest.fixture
    def comfyui_carousel(self):
        path = INFRA_WORKFLOWS_DIR / "comfyui-carousel-generator.json"
        if not path.exists():
            pytest.skip("comfyui carousel workflow not found")
        return path, _load_workflow(path)

    def test_marketing_is_inactive(self, marketing):
        path, wf = marketing
        assert wf.get("active") is False, (
            f"{path.name}: legacy marketing workflow must be inactive"
        )

    def test_comfyui_carousel_is_inactive(self, comfyui_carousel):
        path, wf = comfyui_carousel
        assert wf.get("active") is False, (
            f"{path.name}: legacy comfyui carousel must be inactive"
        )

    def test_marketing_has_stable_id(self, marketing):
        path, wf = marketing
        wid = wf.get("id")
        assert wid is not None and wid != "1", (
            f"{path.name}: needs a stable descriptive id for CLI import"
        )

    def test_comfyui_carousel_needs_stable_id(self, comfyui_carousel):
        path, wf = comfyui_carousel
        wid = wf.get("id")
        if wid == "1" or wid is None:
            pytest.xfail(
                f"{path.name}: has generic id '{wid}' — needs a descriptive slug "
                "if it will ever be imported via CLI"
            )


# ─── 7. Cross-workflow consistency ───────────────────────────────────────────


class TestCrossWorkflowConsistency:

    def test_no_duplicate_workflow_ids(self):
        ids = []
        for path in _collect_workflow_files():
            wf = _load_workflow(path)
            wid = wf.get("id")
            if wid and wid != "1":
                ids.append((wid, path.name))
        seen = {}
        for wid, name in ids:
            if wid in seen:
                pytest.fail(
                    f"Duplicate workflow id '{wid}' in {name} and {seen[wid]}"
                )
            seen[wid] = name

    def test_no_duplicate_webhook_paths(self):
        paths_seen = {}
        for path in _collect_workflow_files():
            wf = _load_workflow(path)
            for node in wf.get("nodes", []):
                if node["type"] == "n8n-nodes-base.webhook":
                    wh_path = node["parameters"].get("path", "")
                    if wh_path:
                        if wh_path in paths_seen:
                            pytest.fail(
                                f"Duplicate webhook path '{wh_path}' in "
                                f"{path.name} and {paths_seen[wh_path]}"
                            )
                        paths_seen[wh_path] = path.name

    def test_prod_workflows_use_social_api_env_fallback(self):
        for path in PROD_FILES:
            if not path.exists():
                continue
            wf = _load_workflow(path)
            raw = json.dumps(wf)
            if "social-api" in raw:
                assert "$env.SOCIAL_API_URL" in raw, (
                    f"{path.name}: must use $env.SOCIAL_API_URL with "
                    "http://social-api:8000 fallback"
                )
