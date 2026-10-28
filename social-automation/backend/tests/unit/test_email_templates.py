"""Coverage for app/services/email_templates.py."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.email_templates as E


def _user(**kw):
    d = dict(id=uuid.uuid4(), email="u@x.co", name="Themis")
    d.update(kw)
    return SimpleNamespace(**d)


@pytest.fixture
def wired(monkeypatch):
    send = AsyncMock()
    log = AsyncMock()
    monkeypatch.setattr(E, "send_email", send)
    monkeypatch.setattr(E, "_log_email", log)
    return send, log


class TestLogEmail:
    @pytest.mark.asyncio
    async def test_persists(self, monkeypatch):
        import app.db.session as ds
        session = SimpleNamespace(add=AsyncMock().__class__ and (lambda o: None),
                                  commit=AsyncMock())
        added = []
        session.add = lambda o: added.append(o)

        class _CM:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(ds, "async_session_maker", lambda: _CM())
        await E._log_email("r@x.co", "s", "t", user_id="u", team_id="t")
        assert len(added) == 1
        assert added[0].recipient == "r@x.co"
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_swallows_errors(self, monkeypatch):
        import app.db.session as ds

        class _CM:
            async def __aenter__(self):
                raise RuntimeError("db down")

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(ds, "async_session_maker", lambda: _CM())
        await E._log_email("r", "s", "t")  # no raise


class TestHelpers:
    def test_wrapper(self):
        html = E._html_wrapper("Title", "<p>body</p>")
        assert "<h1>Title</h1>" in html
        assert "<p>body</p>" in html
        assert "SocialAuto" in html

    def test_user_email(self):
        assert E._user_email(_user()) == "u@x.co"


class TestWelcome:
    @pytest.mark.asyncio
    async def test_named(self, wired):
        send, log = wired
        await E.send_welcome_email(_user())
        assert "Themis" in send.await_args.kwargs["subject"]
        assert log.await_args.args[2] == "welcome"

    @pytest.mark.asyncio
    async def test_anon_name_from_email(self, wired):
        send, _ = wired
        await E.send_welcome_email(_user(name=None))
        assert "u@x.co" in send.await_args.kwargs["subject"] or \
            "there" in send.await_args.kwargs["subject"]

    @pytest.mark.asyncio
    async def test_failure_logs_failed(self, wired):
        send, log = wired
        send.side_effect = RuntimeError("smtp")
        await E.send_welcome_email(_user())
        assert log.await_args.kwargs["status"] == "failed"


class TestPasswordReset:
    @pytest.mark.asyncio
    async def test_happy(self, wired):
        send, log = wired
        await E.send_password_reset_email(_user(), "http://rst")
        assert "http://rst" in send.await_args.kwargs["text_body"]
        assert log.await_args.args[2] == "password_reset"

    @pytest.mark.asyncio
    async def test_failure(self, wired):
        send, log = wired
        send.side_effect = RuntimeError("x")
        await E.send_password_reset_email(_user(), "l")
        assert log.await_args.kwargs["status"] == "failed"


class TestPostPublished:
    @pytest.mark.asyncio
    async def test_explicit_platform(self, wired):
        send, log = wired
        post = SimpleNamespace(content_text="hi", team_id="t1",
                               targets=[])
        await E.send_post_published_email(_user(), post, "linkedin")
        assert "linkedin" in send.await_args.kwargs["subject"]
        assert log.await_args.kwargs["team_id"] == "t1"

    @pytest.mark.asyncio
    async def test_targets_platforms(self, wired):
        send, _ = wired
        post = SimpleNamespace(content_text="x" * 150, team_id=None,
                               targets=[SimpleNamespace(platform="ig"),
                                        SimpleNamespace(platform="fb"),
                                        SimpleNamespace(platform=None)])
        await E.send_post_published_email(_user(), post)
        subj = send.await_args.kwargs["subject"]
        assert "ig" in subj and "fb" in subj
        assert "..." in send.await_args.kwargs["text_body"]

    @pytest.mark.asyncio
    async def test_no_targets_fallback(self, wired):
        send, _ = wired
        post = SimpleNamespace(content_text=None, team_id=None,
                               targets=[])
        await E.send_post_published_email(_user(), post)
        assert "connected accounts" in send.await_args.kwargs["subject"]

    @pytest.mark.asyncio
    async def test_targets_raise_fallback(self, wired):
        send, _ = wired

        class _Bad:
            def __iter__(self):
                raise RuntimeError("x")

        post = SimpleNamespace(content_text="c", team_id=None,
                               targets=_Bad())
        await E.send_post_published_email(_user(), post)
        assert "connected accounts" in send.await_args.kwargs["subject"]

    @pytest.mark.asyncio
    async def test_failure(self, wired):
        send, log = wired
        send.side_effect = RuntimeError("x")
        post = SimpleNamespace(content_text="c", targets=[])
        await E.send_post_published_email(_user(), post, "ig")
        assert log.await_args.kwargs["status"] == "failed"


class TestAccountConnected:
    @pytest.mark.asyncio
    async def test_happy(self, wired):
        send, log = wired
        await E.send_account_connected_email(_user(), "TikTok")
        assert "TikTok" in send.await_args.kwargs["subject"]
        assert log.await_args.args[2] == "account_connected"

    @pytest.mark.asyncio
    async def test_failure(self, wired):
        send, log = wired
        send.side_effect = RuntimeError("x")
        await E.send_account_connected_email(_user(), "p")
        assert log.await_args.kwargs["status"] == "failed"


class TestQuotaWarning:
    @pytest.mark.asyncio
    async def test_pct(self, wired):
        send, log = wired
        await E.send_quota_warning_email(_user(), "ai_calls", 80, 100)
        assert "80%" in send.await_args.kwargs["subject"]
        assert "ai calls" in send.await_args.kwargs["subject"]

    @pytest.mark.asyncio
    async def test_zero_limit(self, wired):
        send, _ = wired
        await E.send_quota_warning_email(_user(), "posts", 5, 0)
        assert "0%" in send.await_args.kwargs["subject"]

    @pytest.mark.asyncio
    async def test_failure(self, wired):
        send, log = wired
        send.side_effect = RuntimeError("x")
        await E.send_quota_warning_email(_user(), "r", 1, 2)
        assert log.await_args.kwargs["status"] == "failed"


class TestTeamEmails:
    @pytest.mark.asyncio
    async def test_invite(self, wired):
        send, log = wired
        await E.send_team_invite_email("Alice", "b@x.co", "Team", "lnk")
        assert "Alice" in send.await_args.kwargs["subject"]
        assert send.await_args.kwargs["to_addrs"] == ["b@x.co"]
        assert log.await_args.args[2] == "team_invite"

    @pytest.mark.asyncio
    async def test_invite_failure(self, wired):
        send, log = wired
        send.side_effect = RuntimeError("x")
        await E.send_team_invite_email("A", "b@x.co", "T", "l")
        assert log.await_args.kwargs["status"] == "failed"
        assert log.await_args.kwargs["error"] == "RuntimeError"

    @pytest.mark.asyncio
    async def test_added(self, wired):
        send, log = wired
        await E.send_team_added_email("Alice", "b@x.co", "Team", "lnk")
        assert "added" in send.await_args.kwargs["subject"]
        assert log.await_args.args[2] == "team_added"

    @pytest.mark.asyncio
    async def test_added_failure(self, wired):
        send, log = wired
        send.side_effect = RuntimeError("x")
        await E.send_team_added_email("A", "b@x.co", "T", "l")
        assert log.await_args.kwargs["status"] == "failed"


class TestSessionAlerts:
    @pytest.mark.asyncio
    async def test_instagram(self, wired):
        send, log = wired
        await E.send_instagram_session_alert_email(
            "o@x.co", "Owner", "cl_ig", "challenge")
        subj = send.await_args.kwargs["subject"]
        assert "@cl_ig" in subj
        assert "challenge" in send.await_args.kwargs["text_body"]
        assert log.await_args.args[2] == "instagram_session_alert"

    @pytest.mark.asyncio
    async def test_instagram_failure(self, wired):
        send, log = wired
        send.side_effect = RuntimeError("x")
        await E.send_instagram_session_alert_email("o", "", "u", "r")
        assert log.await_args.kwargs["status"] == "failed"

    @pytest.mark.asyncio
    async def test_linkedin(self, wired):
        send, log = wired
        await E.send_linkedin_session_alert_email(
            "o@x.co", "", "logout detected")
        assert "o@x.co" in send.await_args.kwargs["text_body"]
        assert log.await_args.args[2] == "linkedin_session_alert"

    @pytest.mark.asyncio
    async def test_linkedin_failure(self, wired):
        send, log = wired
        send.side_effect = RuntimeError("x")
        await E.send_linkedin_session_alert_email("o", "n", "r")
        assert log.await_args.kwargs["status"] == "failed"
