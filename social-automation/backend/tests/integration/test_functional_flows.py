"""Functional end-to-end coverage — real mutations with cleanup.

Complements test_docs_surfaces.py (existence + no-5xx) by exercising the
happy paths a user actually performs: register→login→refresh→delete,
post CRUD, media upload→view→delete, secrets set→get→delete, the public
lead funnel, and one real AI generation call.

Everything created is deleted in the same test (or via fixture teardown).
Nothing is ever published to a social platform.
"""

import base64
import functools
import os
import re
import uuid
from pathlib import Path

import httpx
import pytest

_in_container = Path("/.dockerenv").exists()
API_URL = os.environ.get(
    "API_URL", "http://localhost:8000" if _in_container else "http://localhost:8083"
).rstrip("/")


def _repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "docker-compose.yml").exists():
            return parent
    return None


_root = _repo_root()
_env = (_root / ".env") if _root else Path("/nonexistent")
if _env.exists():
    for line in _env.read_text().splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2).strip('"').strip("'")


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
    return r.json().get("access_token") if r.status_code == 200 else None


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# 1x1 red pixel PNG
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class TestAuthLifecycle:
    """register → login → me → refresh → delete-account → login fails."""

    def test_full_lifecycle(self):
        email = f"ci-test-{uuid.uuid4().hex[:10]}@cloudless.gr"
        password = "CiTest!x" + uuid.uuid4().hex[:8]

        r = httpx.post(
            f"{API_URL}/api/v1/auth/register",
            json={"email": email, "password": password, "name": "CI Test"},
            timeout=20,
        )
        assert r.status_code in (200, 201), f"register → {r.status_code}: {r.text[:200]}"
        tokens = r.json()
        access = tokens.get("access_token")
        refresh = tokens.get("refresh_token")
        if not access:
            # some impls require separate login after register
            r = httpx.post(
                f"{API_URL}/api/v1/auth/login",
                data={"username": email, "password": password},
                timeout=15,
            )
            assert r.status_code == 200
            access = r.json()["access_token"]
            refresh = r.json().get("refresh_token")

        me = httpx.get(f"{API_URL}/api/v1/auth/me", headers=_h(access), timeout=15)
        assert me.status_code == 200 and me.json().get("email") == email

        if refresh:
            rr = httpx.post(
                f"{API_URL}/api/v1/auth/refresh",
                json={"refresh_token": refresh},
                timeout=15,
            )
            assert rr.status_code == 200 and rr.json().get("access_token")
            access = rr.json()["access_token"]

        d = httpx.request(
            "DELETE",
            f"{API_URL}/api/v1/auth/account",
            headers=_h(access),
            json={"password": password},
            timeout=20,
        )
        assert d.status_code in (200, 204), f"delete → {d.status_code}: {d.text[:200]}"

        gone = httpx.post(
            f"{API_URL}/api/v1/auth/login",
            data={"username": email, "password": password},
            timeout=15,
        )
        assert gone.status_code in (400, 401, 403)


class TestContentCrud:
    """Draft post create → read → patch → delete → verify gone."""

    def test_post_roundtrip(self):
        token = _admin_token()
        if not token:
            pytest.skip("admin creds not configured")

        marker = f"ci-test-{uuid.uuid4().hex[:8]}"
        c = httpx.post(
            f"{API_URL}/api/v1/content/posts",
            headers=_h(token),
            json={
                "content_text": f"[{marker}] functional coverage draft — never published",
                "target_account_ids": [],
            },
            timeout=30,
        )
        assert c.status_code in (200, 201), f"create → {c.status_code}: {c.text[:300]}"
        post_id = c.json().get("id") or c.json().get("post", {}).get("id")
        assert post_id, c.text[:200]

        g = httpx.get(f"{API_URL}/api/v1/content/posts/{post_id}", headers=_h(token), timeout=15)
        assert g.status_code == 200 and marker in str(g.json())

        p = httpx.patch(
            f"{API_URL}/api/v1/content/posts/{post_id}",
            headers=_h(token),
            json={
                "content_text": f"[{marker}] updated",
                "target_account_ids": [],
            },
            timeout=15,
        )
        assert p.status_code == 200

        d = httpx.delete(f"{API_URL}/api/v1/content/posts/{post_id}", headers=_h(token), timeout=15)
        assert d.status_code in (200, 204)

        gone = httpx.get(f"{API_URL}/api/v1/content/posts/{post_id}", headers=_h(token), timeout=15)
        assert gone.status_code == 404


class TestMediaFlow:
    """Upload → view → delete a real asset."""

    def test_upload_view_delete(self):
        token = _admin_token()
        if not token:
            pytest.skip("admin creds not configured")

        up = httpx.post(
            f"{API_URL}/api/v1/media/upload",
            headers=_h(token),
            files={"file": ("ci-test.png", TINY_PNG, "image/png")},
            data={"alt_text": "ci functional test", "tags": "ci-test"},
            timeout=60,
        )
        assert up.status_code in (200, 201), f"upload → {up.status_code}: {up.text[:300]}"
        asset = up.json()
        asset_id = asset.get("id") or asset.get("asset_id") or asset.get("asset", {}).get("id")
        assert asset_id, up.text[:200]

        g = httpx.get(f"{API_URL}/api/v1/media/assets/{asset_id}", headers=_h(token), timeout=15)
        assert g.status_code == 200

        d = httpx.delete(f"{API_URL}/api/v1/media/assets/{asset_id}", headers=_h(token), timeout=15)
        assert d.status_code in (200, 204), f"delete → {d.status_code}: {d.text[:200]}"


class TestSecretsCrud:
    """Set → get → delete a secret key."""

    def test_secret_roundtrip(self):
        token = _admin_token()
        if not token:
            pytest.skip("admin creds not configured")

        key = f"CI_TEST_{uuid.uuid4().hex[:8].upper()}"
        value = uuid.uuid4().hex

        s = httpx.post(
            f"{API_URL}/api/v1/secrets/{key}",
            headers=_h(token),
            json={"value": value, "description": "ci functional test"},
            timeout=15,
        )
        assert s.status_code in (200, 201), f"set → {s.status_code}: {s.text[:200]}"

        g = httpx.get(f"{API_URL}/api/v1/secrets/{key}", headers=_h(token), timeout=15)
        assert g.status_code == 200

        d = httpx.delete(f"{API_URL}/api/v1/secrets/{key}", headers=_h(token), timeout=15)
        assert d.status_code in (200, 204), f"delete → {d.status_code}: {d.text[:200]}"


class TestLeadFunnel:
    """Public lead submit → appears in admin leads."""

    def test_public_lead_lands(self):
        token = _admin_token()
        marker = f"ci-test+{uuid.uuid4().hex[:8]}@example.invalid"

        r = httpx.post(
            f"{API_URL}/api/v1/leads/public",
            json={
                "email": marker,
                "name": "CI Functional Test",
                "site": "cloudless.gr",
                "form": "ci-test",
                "page_path": "/en/playbook",
                "consent": True,
            },
            timeout=20,
        )
        assert r.status_code in (200, 201, 202), f"lead → {r.status_code}: {r.text[:200]}"

        if not token:
            pytest.skip("admin creds not configured — submit ok, admin verify skipped")
        lst = httpx.get(f"{API_URL}/api/v1/leads", headers=_h(token), timeout=20)
        assert lst.status_code == 200
        assert marker in lst.text, "submitted lead not found in admin leads"


class TestAiGeneration:
    """One real inference call through the fallback chain (DMR → CF AI)."""

    @pytest.mark.slow
    def test_generate_content(self):
        token = _admin_token()
        if not token:
            pytest.skip("admin creds not configured")
        r = httpx.post(
            f"{API_URL}/api/v1/ai/generate-content",
            headers=_h(token),
            json={
                "prompt": "Write one short LinkedIn hook line about reducing cloud costs",
                "platform": "linkedin",
                "include_hashtags": False,
                "include_emojis": False,
            },
            timeout=180,
        )
        assert r.status_code == 200, f"generate → {r.status_code}: {r.text[:300]}"
        body = r.json()
        text = body.get("content") or body.get("text") or body.get("result") or ""
        assert len(str(text).strip()) > 10, f"empty generation: {body!r:.200}"
