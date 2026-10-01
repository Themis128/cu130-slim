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

# These flows need the configured live stack (admin creds, webhook secrets,
# lead capture, billing provider). In bare CI the same endpoints correctly
# return not-configured responses, so the suite is skipped there.
IN_CI = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
pytestmark = pytest.mark.skipif(
    IN_CI, reason="functional flows target the live configured stack"
)


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


@pytest.fixture(scope="session", autouse=True)
def _sweep_orphaned_ci_teams():
    """Delete admin-owned `ci-*` teams left behind by killed runs.

    test_team_lifecycle creates a `ci-*` team owned by the admin; if the
    process dies before its finally-block, the orphan persists — and a
    later login JWT can claim it as the active team, silently splitting
    reads/writes across two teams (media 404s, empty calendar). Sweeping
    at session start keeps stale debris from poisoning this run.
    """
    token = _admin_token()
    if not token:
        return
    try:
        r = httpx.get(f"{API_URL}/api/v1/teams", headers=_h(token), timeout=15)
        if r.status_code != 200:
            return
        for team in r.json():
            name = str(team.get("name") or "")
            if name.startswith("ci-") and str(team.get("role") or "").lower() == "owner":
                httpx.delete(
                    f"{API_URL}/api/v1/teams/{team['id']}", headers=_h(token), timeout=15
                )
    except Exception:
        pass  # best-effort hygiene sweep — never fail the suite over cleanup


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


class TestPresignedUpload:
    """R2 presigned flow: prepare → PUT bytes → complete → view → delete."""

    def test_presigned_roundtrip(self):
        token = _admin_token()
        if not token:
            pytest.skip("admin creds not configured")

        p = httpx.post(
            f"{API_URL}/api/v1/media/upload/prepare",
            headers=_h(token),
            json={
                "filename": "ci-presign.png",
                "mime_type": "image/png",
                "size_bytes": len(TINY_PNG),
            },
            timeout=15,
        )
        assert p.status_code == 200, f"prepare → {p.status_code}: {p.text[:200]}"
        presign = p.json()
        assert presign.get("upload_url") and presign.get("key")

        up = httpx.put(
            presign["upload_url"],
            content=TINY_PNG,
            headers={"Content-Type": "image/png"},
            timeout=60,
        )
        assert up.status_code in (200, 201), f"R2 PUT → {up.status_code}: {up.text[:200]}"

        c = httpx.post(
            f"{API_URL}/api/v1/media/upload/complete",
            headers=_h(token),
            json={
                "key": presign["key"],
                "filename": "ci-presign.png",
                "mime_type": "image/png",
                "size_bytes": len(TINY_PNG),
                "alt_text": "ci presigned test",
            },
            timeout=30,
        )
        assert c.status_code in (200, 201), f"complete → {c.status_code}: {c.text[:300]}"
        asset_id = c.json().get("id") or c.json().get("asset_id") or c.json().get("asset", {}).get("id")
        assert asset_id, c.text[:200]

        g = httpx.get(f"{API_URL}/api/v1/media/assets/{asset_id}", headers=_h(token), timeout=15)
        assert g.status_code == 200

        d = httpx.delete(
            f"{API_URL}/api/v1/media/assets/{asset_id}", headers=_h(token), timeout=15
        )
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


def _register_user() -> tuple[str, str, str]:
    """Register a throwaway user; returns (email, password, access_token)."""
    email = f"ci-test-{uuid.uuid4().hex[:10]}@cloudless.gr"
    password = "CiTest!x" + uuid.uuid4().hex[:8]
    r = httpx.post(
        f"{API_URL}/api/v1/auth/register",
        json={"email": email, "password": password, "name": "CI Test"},
        timeout=20,
    )
    assert r.status_code in (200, 201), f"register → {r.status_code}: {r.text[:200]}"
    token = r.json().get("access_token")
    if not token:
        r = httpx.post(
            f"{API_URL}/api/v1/auth/login",
            data={"username": email, "password": password},
            timeout=15,
        )
        token = r.json()["access_token"]
    return email, password, token


def _delete_account(token: str, password: str) -> None:
    httpx.request(
        "DELETE",
        f"{API_URL}/api/v1/auth/account",
        headers=_h(token),
        json={"password": password},
        timeout=20,
    )


def _totp(secret: str, when: int | None = None) -> str:
    """RFC 6238 TOTP — SHA1, 6 digits, 30s period."""
    import hashlib
    import hmac
    import struct
    import time

    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = int((when or time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % 1_000_000).zfill(6)


class TestTeamsFlow:
    """Team create → rename → invite → second user accepts → delete."""

    def test_team_lifecycle(self):
        token = _admin_token()
        if not token:
            pytest.skip("admin creds not configured")

        marker = f"ci-team-{uuid.uuid4().hex[:8]}"
        c = httpx.post(
            f"{API_URL}/api/v1/teams",
            headers=_h(token),
            json={"name": marker},
            timeout=15,
        )
        assert c.status_code in (200, 201), f"create → {c.status_code}: {c.text[:200]}"
        team = c.json()
        team_id = team.get("id") or team.get("team", {}).get("id")
        assert team_id

        try:
            p = httpx.patch(
                f"{API_URL}/api/v1/teams/{team_id}",
                headers=_h(token),
                json={"name": f"{marker}-renamed"},
                timeout=15,
            )
            assert p.status_code == 200, f"rename → {p.status_code}: {p.text[:200]}"

            # Invite a throwaway user, who accepts with the returned token.
            invitee_email, invitee_pw, invitee_tok = _register_user()
            try:
                inv = httpx.post(
                    f"{API_URL}/api/v1/teams/{team_id}/invite",
                    headers=_h(token),
                    json={"email": invitee_email, "role": "editor"},
                    timeout=15,
                )
                assert inv.status_code in (200, 201), f"invite → {inv.status_code}: {inv.text[:200]}"
                invite_token = inv.json().get("token")
                if invite_token:
                    acc = httpx.post(
                        f"{API_URL}/api/v1/teams/accept-invite",
                        headers=_h(invitee_tok),
                        json={"token": invite_token},
                        timeout=15,
                    )
                    assert acc.status_code in (200, 201), f"accept → {acc.status_code}: {acc.text[:200]}"
            finally:
                _delete_account(invitee_tok, invitee_pw)
        finally:
            d = httpx.delete(f"{API_URL}/api/v1/teams/{team_id}", headers=_h(token), timeout=15)
            assert d.status_code in (200, 204), f"team delete → {d.status_code}: {d.text[:200]}"


class TestTeamScopedSandbox:
    """Brand CRUD + post scheduling inside a throwaway user's own team.

    A freshly registered user gets an auto-created personal team — that is
    the sandbox. All team-scoped mutations land there, and account deletion
    at the end cleans everything up.
    """

    def test_brand_and_schedule(self):
        _email, password, sandbox_token = _register_user()

        try:
            # Brand is a per-team singleton — create it, then a second POST
            # must 409.
            b = httpx.post(
                f"{API_URL}/api/v1/brand",
                headers=_h(sandbox_token),
                json={"name": "CI Test Brand", "tagline": "functional coverage"},
                timeout=20,
            )
            assert b.status_code in (200, 201), f"brand create → {b.status_code}: {b.text[:200]}"

            g = httpx.get(f"{API_URL}/api/v1/brand", headers=_h(sandbox_token), timeout=15)
            assert g.status_code == 200, f"brand get → {g.status_code}: {g.text[:200]}"

            dup = httpx.post(
                f"{API_URL}/api/v1/brand",
                headers=_h(sandbox_token),
                json={"name": "CI Test Brand 2"},
                timeout=15,
            )
            assert dup.status_code == 409, f"brand dup → {dup.status_code}: {dup.text[:200]}"

            u = httpx.put(
                f"{API_URL}/api/v1/brand",
                headers=_h(sandbox_token),
                json={"tagline": "updated by functional suite"},
                timeout=15,
            )
            assert u.status_code == 200, f"brand put → {u.status_code}: {u.text[:200]}"

            # Scheduled post: create → schedule → shows on calendar → delete
            marker = f"ci-sched-{uuid.uuid4().hex[:8]}"
            pc = httpx.post(
                f"{API_URL}/api/v1/content/posts",
                headers=_h(sandbox_token),
                json={"content_text": f"[{marker}] scheduled draft", "target_account_ids": []},
                timeout=20,
            )
            assert pc.status_code in (200, 201), f"post → {pc.status_code}: {pc.text[:200]}"
            post_id = pc.json().get("id") or pc.json().get("post", {}).get("id")

            future = "2099-01-01T12:00:00Z"
            sc = httpx.post(
                f"{API_URL}/api/v1/content/posts/{post_id}/schedule",
                headers=_h(sandbox_token),
                params={"scheduled_at": future},
                timeout=15,
            )
            assert sc.status_code == 200, f"schedule → {sc.status_code}: {sc.text[:200]}"

            cal = httpx.get(
                f"{API_URL}/api/v1/content/posts/calendar",
                headers=_h(sandbox_token),
                params={"start": "2098-01-01T00:00:00Z", "end": "2100-01-01T00:00:00Z"},
                timeout=15,
            )
            assert cal.status_code == 200 and marker in cal.text

            dp = httpx.delete(
                f"{API_URL}/api/v1/content/posts/{post_id}",
                headers=_h(sandbox_token),
                timeout=15,
            )
            assert dp.status_code in (200, 204)

            db_ = httpx.delete(f"{API_URL}/api/v1/brand", headers=_h(sandbox_token), timeout=15)
            assert db_.status_code in (200, 204), f"brand delete → {db_.status_code}: {db_.text[:200]}"

            rb = httpx.post(
                f"{API_URL}/api/v1/brand",
                headers=_h(sandbox_token),
                json={"name": "CI Test Brand", "tagline": "recreated"},
                timeout=20,
            )
            assert rb.status_code in (200, 201), f"brand recreate → {rb.status_code}: {rb.text[:200]}"
        finally:
            _delete_account(sandbox_token, password)


class TestAnalyticsConfigCrud:
    """Web analytics config create → list → update → delete (UUID regression)."""

    def test_config_roundtrip(self):
        token = _admin_token()
        if not token:
            pytest.skip("admin creds not configured")

        domain = f"ci-{uuid.uuid4().hex[:8]}.cloudless.gr"
        c = httpx.post(
            f"{API_URL}/api/v1/analytics/web/configs",
            headers=_h(token),
            json={"domain": domain},
            timeout=15,
        )
        assert c.status_code in (200, 201), f"create → {c.status_code}: {c.text[:200]}"
        cfg_id = c.json().get("id")
        assert cfg_id

        try:
            lst = httpx.get(
                f"{API_URL}/api/v1/analytics/web/configs", headers=_h(token), timeout=15
            )
            assert lst.status_code == 200 and domain in lst.text

            u = httpx.put(
                f"{API_URL}/api/v1/analytics/web/configs/{cfg_id}",
                headers=_h(token),
                json={"domain": domain, "ga4_enabled": False},
                timeout=15,
            )
            assert u.status_code == 200, f"update → {u.status_code}: {u.text[:200]}"
        finally:
            d = httpx.delete(
                f"{API_URL}/api/v1/analytics/web/configs/{cfg_id}",
                headers=_h(token),
                timeout=15,
            )
            assert d.status_code in (200, 204), f"delete → {d.status_code}: {d.text[:200]}"


class TestNotificationPrefs:
    """PUT notification preferences → response echoes new values."""

    def test_prefs_update(self):
        token = _admin_token()
        if not token:
            pytest.skip("admin creds not configured")

        me = httpx.get(f"{API_URL}/api/v1/auth/me", headers=_h(token), timeout=15)
        original = (me.json().get("notification_preferences") or {}) if me.status_code == 200 else {}

        marker = {"email_analytics": not original.get("email_analytics", True)}
        u = httpx.put(
            f"{API_URL}/api/v1/auth/notifications/preferences",
            headers=_h(token),
            json=marker,
            timeout=15,
        )
        assert u.status_code == 200, f"prefs → {u.status_code}: {u.text[:200]}"

        me2 = httpx.get(f"{API_URL}/api/v1/auth/me", headers=_h(token), timeout=15)
        if me2.status_code == 200 and me2.json().get("notification_preferences") is not None:
            assert me2.json()["notification_preferences"].get("email_analytics") == marker[
                "email_analytics"
            ]

        # restore
        if original:
            httpx.put(
                f"{API_URL}/api/v1/auth/notifications/preferences",
                headers=_h(token),
                json=original,
                timeout=15,
            )


class TestTwoFactorAndExport:
    """Throwaway user: GDPR export → 2FA setup → TOTP verify → disable → delete."""

    def test_2fa_and_export(self):
        email, password, token = _register_user()
        try:
            ex = httpx.get(f"{API_URL}/api/v1/auth/export-data", headers=_h(token), timeout=20)
            assert ex.status_code == 200, f"export → {ex.status_code}: {ex.text[:200]}"
            assert email in ex.text

            s = httpx.post(f"{API_URL}/api/v1/auth/2fa/setup", headers=_h(token), timeout=15)
            assert s.status_code == 200, f"2fa setup → {s.status_code}: {s.text[:200]}"
            secret = s.json()["secret"]

            v = httpx.post(
                f"{API_URL}/api/v1/auth/2fa/verify",
                headers=_h(token),
                json={"code": _totp(secret)},
                timeout=15,
            )
            assert v.status_code == 200, f"2fa verify → {v.status_code}: {v.text[:200]}"

            d = httpx.request(
                "DELETE",
                f"{API_URL}/api/v1/auth/2fa",
                headers=_h(token),
                json={"current_password": password},
                timeout=15,
            )
            assert d.status_code in (200, 204), f"2fa off → {d.status_code}: {d.text[:200]}"
        finally:
            _delete_account(token, password)


class TestInitiativeEvent:
    """POST a real initiative tracking event."""

    def test_event_ingest(self):
        token = _admin_token()
        if not token:
            pytest.skip("admin creds not configured")
        r = httpx.post(
            f"{API_URL}/api/v1/analytics/initiative-events",
            headers=_h(token),
            json={
                "initiative": f"ci-test-{uuid.uuid4().hex[:8]}",
                "event": "functional_test",
                "metadata": {"suite": "test_functional_flows"},
            },
            timeout=15,
        )
        assert r.status_code in (200, 201, 202, 422), f"event → {r.status_code}: {r.text[:200]}"
        if r.status_code == 422:
            pytest.fail(f"initiative event schema mismatch: {r.text[:300]}")


class TestAccountOps:
    """Run the connectivity test on a connected account."""

    def test_account_test_endpoint(self):
        token = _admin_token()
        if not token:
            pytest.skip("admin creds not configured")
        accs = httpx.get(f"{API_URL}/api/v1/accounts", headers=_h(token), timeout=20)
        assert accs.status_code == 200
        body = accs.json()
        accounts = body if isinstance(body, list) else body.get("accounts") or body.get("items") or []
        if not accounts:
            pytest.skip("no connected accounts")
        aid = accounts[0].get("id")
        t = httpx.post(f"{API_URL}/api/v1/accounts/{aid}/test", headers=_h(token), timeout=60)
        # The probe itself may report a dead session — that is data, not an
        # endpoint failure. Only hard server errors count as test failures.
        assert t.status_code in (200, 201, 400, 404, 422), f"test → {t.status_code}: {t.text[:200]}"
        assert t.status_code != 500


class TestAiGeneration:
    """One real inference call through the fallback chain (DMR → CF AI)."""

    @pytest.mark.slow
    def test_generate_content(self):
        token = _admin_token()
        if not token:
            pytest.skip("admin creds not configured")
        # Pre-warm the DMR model — engines unload after ~5m idle, and a cold
        # model load can exceed the request timeout or flip the endpoint to
        # its CLI fallback. A tiny warm-up call makes the real call fast.
        dmr_url = os.environ.get("DMR_URL", "http://host.docker.internal:12435/engines/llama.cpp/v1")
        dmr_model = os.environ.get("DMR_TEXT_MODEL", "ai/qwen3:8b-q4_K_M")
        try:
            warm = httpx.post(
                f"{dmr_url}/chat/completions",
                json={"model": dmr_model, "messages": [{"role": "user", "content": "ok"}], "max_tokens": 1},
                timeout=240,
            )
            if warm.status_code >= 400:
                pytest.skip(f"DMR unreachable: {warm.status_code}")
        except (httpx.HTTPError, OSError):
            pytest.skip("DMR not reachable — optional inference infra unavailable")
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
