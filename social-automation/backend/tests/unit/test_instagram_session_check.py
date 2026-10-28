"""Unit tests for the Instagram session health-check Celery task.

Covers:
- decrypt_field on enc:-prefixed (encrypted) session IDs
- decrypt_field on legacy plaintext session IDs
- decrypt_field on None / missing values
- sidecar unreachable → account marked expired
- sidecar returns 401 → account marked expired
- sidecar returns 200 → account stays active
- no session_id in meta_data → account skipped
- alert cooldown prevents duplicate emails
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.worker.tasks.instagram_session_check as T
from app.core.security import decrypt_field, encrypt_field
from app.worker.tasks.instagram_session_check import (
    _check_sidecar_health,
    _check_sidecar_session,
    _send_alert,
)


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class _FakeAsyncClient:
    """Minimal httpx.AsyncClient stand-in."""

    def __init__(self, response: _FakeResponse = None, exc: Exception = None):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, *args, **kwargs):
        if self._exc:
            raise self._exc
        return self._response


# ─── decrypt_field tests ────────────────────────────────────────────


class TestDecryptField:
    def test_enc_prefixed_value_roundtrips(self):
        """encrypt_field → decrypt_field returns the original value."""
        original = "sessionid_abc123"
        encrypted = encrypt_field(original)
        assert encrypted.startswith("enc:")
        assert decrypt_field(encrypted) == original

    def test_legacy_plaintext_passes_through(self):
        """decrypt_field returns plaintext values that lack the enc: prefix."""
        assert decrypt_field("plain_session_id") == "plain_session_id"

    def test_none_returns_none(self):
        assert decrypt_field(None) is None

    def test_empty_string_returns_empty(self):
        assert decrypt_field("") == ""


# ─── sidecar session check tests ────────────────────────────────────


class TestCheckSidecarSession:
    @pytest.mark.asyncio
    async def test_200_returns_true(self):
        with patch(
            "app.worker.tasks.instagram_session_check.httpx.AsyncClient",
            return_value=_FakeAsyncClient(response=_FakeResponse(200)),
        ):
            result = await _check_sidecar_session("sid", "http://sidecar:8000")
        assert result is True

    @pytest.mark.asyncio
    async def test_401_returns_false(self):
        with patch(
            "app.worker.tasks.instagram_session_check.httpx.AsyncClient",
            return_value=_FakeAsyncClient(response=_FakeResponse(401)),
        ):
            result = await _check_sidecar_session("sid", "http://sidecar:8000")
        assert result is False

    @pytest.mark.asyncio
    async def test_connection_error_returns_false(self):
        with patch(
            "app.worker.tasks.instagram_session_check.httpx.AsyncClient",
            return_value=_FakeAsyncClient(exc=ConnectionError("refused")),
        ):
            result = await _check_sidecar_session("sid", "http://sidecar:8000")
        assert result is False


class TestCheckSidecarHealth:
    @pytest.mark.asyncio
    async def test_200_returns_true(self):
        with patch(
            "app.worker.tasks.instagram_session_check.httpx.AsyncClient",
            return_value=_FakeAsyncClient(response=_FakeResponse(200)),
        ):
            result = await _check_sidecar_health("http://sidecar:8000")
        assert result is True

    @pytest.mark.asyncio
    async def test_connection_error_returns_false(self):
        with patch(
            "app.worker.tasks.instagram_session_check.httpx.AsyncClient",
            return_value=_FakeAsyncClient(exc=ConnectionError("down")),
        ):
            result = await _check_sidecar_health("http://sidecar:8000")
        assert result is False


# ─── alert cooldown tests ───────────────────────────────────────────


class TestSendAlertCooldown:
    @pytest.mark.asyncio
    async def test_alert_skipped_within_cooldown(self):
        """If an alert was sent in the last 24h, skip sending."""
        owner = MagicMock()
        owner.email = "owner@example.com"
        owner.name = "Owner"
        owner.id = "user-123"

        # Mock the cooldown DB query to report 1 recent alert.
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 1

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)

        mock_factory = MagicMock()
        mock_factory.return_value = mock_db
        mock_factory.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "app.worker.tasks.instagram_session_check.task_session",
            return_value=mock_factory,
        ), patch(
            "app.services.email_templates.send_instagram_session_alert_email",
            new_callable=AsyncMock,
        ) as mock_send, patch(
            "app.worker.tasks.instagram_session_check.post_alert_to_slack",
            new_callable=AsyncMock,
        ) as mock_slack:
            await _send_alert(owner, "testuser", "session rejected")

        mock_send.assert_not_called()
        mock_slack.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_sent_when_no_recent_alert(self):
        """If no recent alert exists, send the email via email_templates."""
        owner = MagicMock()
        owner.email = "owner@example.com"
        owner.name = "Owner"
        owner.id = "user-123"

        # Cooldown check returns 0 → alert should be sent.
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)

        mock_factory = MagicMock()
        mock_factory.return_value = mock_db
        mock_factory.__aenter__ = AsyncMock(return_value=mock_db)
        mock_factory.__aexit__ = AsyncMock(return_value=None)

        with patch(
            "app.worker.tasks.instagram_session_check.task_session",
            return_value=mock_factory,
        ), patch(
            "app.services.email_templates.send_instagram_session_alert_email",
            new_callable=AsyncMock,
        ) as mock_send, patch(
            "app.worker.tasks.instagram_session_check.post_alert_to_slack",
            new_callable=AsyncMock,
        ) as mock_slack:
            await _send_alert(owner, "testuser", "session rejected")

        mock_send.assert_called_once()
        mock_slack.assert_awaited_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["owner_email"] == owner.email
        assert "testuser" in kwargs["account_username"]
        assert kwargs["reason"] == "session rejected"


# ─── coverage append: _check_graph_token, _run_check, wrapper ───────


class _Session:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


def _scalars(items):
    return SimpleNamespace(scalars=lambda: SimpleNamespace(
        all=lambda: items))


def _scalar(val):
    return SimpleNamespace(scalar_one_or_none=lambda: val)


def _acct(**kw):
    d = dict(id=uuid.uuid4(), team_id=uuid.uuid4(), platform="instagram",
             status="active", username="cl_ig", access_token_enc=b"e",
             meta_data={})
    d.update(kw)
    return SimpleNamespace(**d)


class TestCheckGraphToken:
    @pytest.mark.asyncio
    async def test_decrypt_fails(self, monkeypatch):
        monkeypatch.setattr(T, "decrypt_token",
                            lambda e: (_ for _ in ()).throw(
                                RuntimeError("bad")))
        assert await T._check_graph_token(_acct()) is False

    @pytest.mark.asyncio
    async def test_empty_token(self, monkeypatch):
        monkeypatch.setattr(T, "decrypt_token", lambda e: "")
        assert await T._check_graph_token(_acct()) is False

    @pytest.mark.asyncio
    async def test_valid(self, monkeypatch):
        monkeypatch.setattr(T, "decrypt_token", lambda e: "tok")
        monkeypatch.setattr(T.httpx, "AsyncClient",
                            lambda **kw: _FakeAsyncClient(
                                response=_FakeResponse(200)))
        assert await T._check_graph_token(_acct()) is True

    @pytest.mark.asyncio
    async def test_rejected_and_error(self, monkeypatch):
        monkeypatch.setattr(T, "decrypt_token", lambda e: "tok")
        monkeypatch.setattr(T.httpx, "AsyncClient",
                            lambda **kw: _FakeAsyncClient(
                                response=_FakeResponse(401)))
        assert await T._check_graph_token(_acct()) is False
        monkeypatch.setattr(T.httpx, "AsyncClient",
                            lambda **kw: _FakeAsyncClient(
                                exc=RuntimeError("net")))
        assert await T._check_graph_token(_acct()) is False


class _WireDB:
    def __init__(self, results):
        self._it = iter(results)
        self.commit = AsyncMock()

    async def execute(self, *a, **kw):
        return next(self._it)


def _wire_run(monkeypatch, accounts, db_extra=(), *, sidecar_up=True,
              graph=True, session=True):
    monkeypatch.setattr(T, "get_settings", lambda: SimpleNamespace(
        INSTAGRAM_PRIVATE_API_URL="http://sc"))
    monkeypatch.setattr(T, "_check_sidecar_health",
                        AsyncMock(return_value=sidecar_up))
    monkeypatch.setattr(T, "_check_graph_token",
                        AsyncMock(return_value=graph))
    monkeypatch.setattr(T, "_check_sidecar_session",
                        AsyncMock(return_value=session))
    db = _WireDB([_scalars(accounts), *db_extra])
    monkeypatch.setattr(T, "_worker_db", lambda: _Session(db))
    return db


class TestRunCheck:
    @pytest.mark.asyncio
    async def test_no_accounts(self, monkeypatch):
        _wire_run(monkeypatch, [])
        out = await T._run_check()
        assert out == {"checked": 0, "healthy": 0, "expired": 0,
                       "sidecar_up": True}

    @pytest.mark.asyncio
    async def test_sidecar_down(self, monkeypatch):
        acct = _acct(meta_data={"private_api_session_id": "s"})
        _wire_run(monkeypatch, [acct], [_scalar(None)],
                  sidecar_up=False, session=False)
        out = await T._run_check()
        assert out["sidecar_up"] is False
        assert out["expired"] == 1
        assert acct.status == "expired"

    @pytest.mark.asyncio
    async def test_healthy_sidecar_account(self, monkeypatch):
        acct = _acct(meta_data={"private_api_session_id": "s"})
        _wire_run(monkeypatch, [acct])
        out = await T._run_check()
        assert out["healthy"] == 1
        assert acct.status == "active"

    @pytest.mark.asyncio
    async def test_restore_expired_to_active(self, monkeypatch):
        acct = _acct(status="expired",
                     meta_data={"private_api_session_id": "s"})
        db = _wire_run(monkeypatch, [acct])
        await T._run_check()
        assert acct.status == "active"
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_business_login_graph_ok(self, monkeypatch):
        acct = _acct(meta_data={"login_type": "business_login",
                                "private_api_session_id": "s"})
        _wire_run(monkeypatch, [acct], graph=True)
        out = await T._run_check()
        assert out["healthy"] == 1
        T._check_graph_token.assert_awaited_once()
        T._check_sidecar_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_business_login_fallback_to_sidecar(self, monkeypatch):
        acct = _acct(meta_data={"login_type": "business_login",
                                "private_api_session_id": "s"})
        _wire_run(monkeypatch, [acct], graph=False, session=True)
        out = await T._run_check()
        assert out["healthy"] == 1
        T._check_sidecar_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_business_login_expired_alerts(self, monkeypatch):
        acct = _acct(meta_data={"login_type": "business_login"})
        owner = SimpleNamespace(email="o@x.co", name="O")
        alert = AsyncMock()
        monkeypatch.setattr(T, "_send_alert", alert)
        _wire_run(monkeypatch, [acct], [_scalar(owner)],
                  graph=False)
        out = await T._run_check()
        assert out["expired"] == 1
        assert acct.status == "expired"
        assert "OAuth token" in alert.await_args.args[2]

    @pytest.mark.asyncio
    async def test_no_session_no_check(self, monkeypatch):
        acct = _acct(meta_data={})
        _wire_run(monkeypatch, [acct])
        out = await T._run_check()
        assert out["checked"] == 0

    @pytest.mark.asyncio
    async def test_expired_no_owner(self, monkeypatch):
        acct = _acct(meta_data={"private_api_session_id": "s"})
        alert = AsyncMock()
        monkeypatch.setattr(T, "_send_alert", alert)
        _wire_run(monkeypatch, [acct], [_scalar(None)],
                  session=False)
        out = await T._run_check()
        assert out["expired"] == 1
        alert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_session_rejected_reason(self, monkeypatch):
        acct = _acct(meta_data={"private_api_session_id": "s"})
        owner = SimpleNamespace(email="o", name="")
        alert = AsyncMock()
        monkeypatch.setattr(T, "_send_alert", alert)
        _wire_run(monkeypatch, [acct], [_scalar(owner)],
                  sidecar_up=True, session=False)
        await T._run_check()
        assert alert.await_args.args[2] == "session rejected"


class TestSendAlertException:
    @pytest.mark.asyncio
    async def test_swallowed(self, monkeypatch):
        monkeypatch.setattr(T, "task_session", lambda: _Session(
            SimpleNamespace(execute=AsyncMock(
                side_effect=RuntimeError("db")))))
        await _send_alert(SimpleNamespace(email="o", name=""),
                          "u", "r")


def test_wrapper(monkeypatch):
    monkeypatch.setattr(T, "run_async",
                        lambda c: c.close() or {"done": 1})
    assert T.check_instagram_sessions() == {"done": 1}
