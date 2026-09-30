"""Public funnel: the playbook promised by LeadCapture is actually delivered."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import playbook_email as pb

URL = "https://cloudless.gr/playbooks/cloud-migration-playbook.pdf"


def _settings(**over):
    base = dict(
        PLAYBOOK_EMAIL_ENABLED=True,
        PLAYBOOK_URL=URL,
        FRONTEND_URL="",
        EMAIL_PROVIDER="smtp",
        SMTP_HOST="smtp.resend.com",
        SMTP_USER="resend",
        SMTP_PASSWORD="pw",
        SMTP_FROM="noreply@cloudless.gr",
        CLOUDFLARE_EMAIL_API_TOKEN="",
        CLOUDFLARE_API_TOKEN="",
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeRedis:
    """Minimal async Redis: SET NX/EX + DELETE with real NX semantics."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.closed = 0

    async def aclose(self):
        self.closed += 1

    async def set(self, key, value, nx=False, ex=None):
        await asyncio.sleep(0)  # yield so concurrent callers interleave
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture
def fake_redis():
    r = _FakeRedis()

    async def _client():
        return r

    with patch.object(pb, "_redis_client", new=_client):
        yield r


class _FakeDB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


def test_email_contains_link_and_audit_cta():
    subject, text, html = pb.build_playbook_email(URL)
    assert "Cloud Migration Playbook" in subject
    assert URL in text and URL in html
    assert "https://cloudless.gr/contact" in text
    assert "https://cloudless.gr/contact" in html


def test_email_escapes_url_in_html():
    _, _, html = pb.build_playbook_email('https://x.test/a"b<c')
    assert '"b<c' not in html
    assert "&quot;b&lt;c" in html


@pytest.mark.parametrize(
    "over, expected",
    [
        ({}, True),
        ({"SMTP_PASSWORD": ""}, False),  # Resend relay without its key
        ({"SMTP_USER": "", "SMTP_PASSWORD": ""}, True),  # unauthenticated local Postfix
        ({"SMTP_HOST": ""}, False),
        ({"SMTP_FROM": ""}, False),
        ({"EMAIL_PROVIDER": "cloudflare", "SMTP_PASSWORD": ""}, False),
        ({"EMAIL_PROVIDER": "cloudflare", "CLOUDFLARE_EMAIL_API_TOKEN": "t"}, True),
    ],
)
def test_email_sender_configured(over, expected):
    assert pb.email_sender_configured(_settings(**over)) is expected


def test_delivery_disabled_without_url_or_flag():
    assert pb.playbook_delivery_enabled(_settings()) is True
    assert pb.playbook_delivery_enabled(_settings(PLAYBOOK_URL="")) is False
    assert pb.playbook_delivery_enabled(_settings(PLAYBOOK_EMAIL_ENABLED=False)) is False


@pytest.mark.asyncio
async def test_send_playbook_email_targets_submitter_only():
    with patch.object(pb, "get_settings", return_value=_settings()), patch.object(
        pb, "send_email", new=AsyncMock()
    ) as send:
        await pb.send_playbook_email("lead@example.com")
    send.assert_awaited_once()
    kwargs = send.await_args.kwargs
    assert kwargs["to_addrs"] == ["lead@example.com"]
    assert URL in kwargs["text_body"] and URL in kwargs["html_body"]


@pytest.mark.asyncio
async def test_send_playbook_email_requires_url():
    with patch.object(pb, "get_settings", return_value=_settings(PLAYBOOK_URL="")):
        with pytest.raises(RuntimeError):
            await pb.send_playbook_email("lead@example.com")


@pytest.mark.asyncio
async def test_deliver_playbook_enqueues_and_stamps_lead(fake_redis):
    db, lead = _FakeDB(), SimpleNamespace(meta_data={"form": "newsletter"})
    with patch.object(pb, "_enqueue_playbook_email") as enq:
        out = await pb.deliver_playbook(db, lead, "a@b.co", already_sent=False, settings=_settings())
    assert out == "email"
    enq.assert_called_once_with("a@b.co")
    assert lead.meta_data["form"] == "newsletter"
    assert lead.meta_data["playbook_email_queued_at"]
    assert db.commits == 1


@pytest.mark.asyncio
async def test_deliver_playbook_never_resends(fake_redis):
    db, lead = _FakeDB(), SimpleNamespace(meta_data={"playbook_email_queued_at": "x"})
    with patch.object(pb, "_enqueue_playbook_email") as enq:
        out = await pb.deliver_playbook(db, lead, "a@b.co", already_sent=True, settings=_settings())
    assert out == "already_sent"
    enq.assert_not_called()
    assert db.commits == 0


@pytest.mark.asyncio
async def test_deliver_playbook_without_sender_offers_download(fake_redis):
    db, lead = _FakeDB(), SimpleNamespace(meta_data={})
    with patch.object(pb, "_enqueue_playbook_email") as enq:
        out = await pb.deliver_playbook(
            db, lead, "a@b.co", already_sent=False, settings=_settings(SMTP_PASSWORD="")
        )
    assert out == "download"
    enq.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_playbook_without_url_is_none():
    db, lead = _FakeDB(), SimpleNamespace(meta_data={})
    out = await pb.deliver_playbook(db, lead, "a@b.co", already_sent=False, settings=_settings(PLAYBOOK_URL=""))
    assert out == "none"


@pytest.mark.asyncio
async def test_deliver_playbook_broker_down_falls_back_to_download(fake_redis):
    db, lead = _FakeDB(), SimpleNamespace(meta_data={})
    with patch.object(pb, "_enqueue_playbook_email", side_effect=ConnectionError("redis down")):
        out = await pb.deliver_playbook(db, lead, "a@b.co", already_sent=False, settings=_settings())
    assert out == "download"
    assert "playbook_email_queued_at" not in lead.meta_data
    assert db.commits == 0
    assert fake_redis.store == {}  # claim released so a retry can still send


def test_playbook_task_registered_and_routed_to_default():
    from app.worker.celery_app import celery_app

    assert "app.worker.tasks.lead_emails" in celery_app.conf.include
    import app.worker.tasks.lead_emails  # noqa: F401

    assert pb.PLAYBOOK_TASK_NAME in celery_app.tasks
    assert celery_app.conf.task_routes[pb.PLAYBOOK_TASK_NAME] == {"queue": "default"}


def test_playbook_url_defaults_to_public_site_pdf():
    from app.core.config import Settings

    # social.cloudless.gr is behind Cloudflare Access; leads need the public copy.
    default = Settings.model_fields["PLAYBOOK_URL"].default
    assert default == pb.PUBLIC_PLAYBOOK_URL == URL
    assert "social.cloudless.gr" not in default
    s = _settings(PLAYBOOK_URL=default, FRONTEND_URL="https://social.cloudless.gr/")
    assert pb.playbook_url(s) == URL
    assert pb.playbook_delivery_enabled(s) is True  # no .env change needed


def test_playbook_url_empty_falls_back_to_frontend_pdf():
    s = _settings(PLAYBOOK_URL="", FRONTEND_URL="https://social.cloudless.gr/")
    assert pb.playbook_url(s) == "https://social.cloudless.gr/playbooks/cloud-migration-playbook.pdf"
    assert pb.playbook_url(_settings(PLAYBOOK_URL="https://cdn.test/p.pdf", FRONTEND_URL="https://x")) == "https://cdn.test/p.pdf"
    assert pb.playbook_url(_settings(PLAYBOOK_URL="", FRONTEND_URL="")) == ""


def test_committed_asset_matches_default_path():
    assert pb.PLAYBOOK_PATH == "/playbooks/cloud-migration-playbook.pdf"
    # Host checkout layout: backend/tests/unit -> repo root is parents[4].
    here = Path(__file__).resolve()
    try:
        repo = here.parents[4]
    except IndexError:
        pytest.skip("not running from a repo checkout")
    if not (repo / "social-automation" / "frontend").exists():
        pytest.skip("frontend not present (container layout)")
    pdf = repo / "social-automation" / "frontend" / "public" / pb.PLAYBOOK_PATH.lstrip("/")
    src = repo / "docs" / "playbooks" / "cloud-migration-playbook.md"
    assert pdf.exists() and pdf.read_bytes()[:5] == b"%PDF-"
    assert src.exists()


@pytest.mark.asyncio
async def test_concurrent_submissions_queue_exactly_one_email(fake_redis):
    """Race from #203 review: simultaneous posts for one address -> one email."""
    leads = [SimpleNamespace(meta_data={}) for _ in range(8)]
    db = _FakeDB()
    with patch.object(pb, "_enqueue_playbook_email") as enq:
        results = await asyncio.gather(
            *(
                pb.deliver_playbook(db, lead, "Race@Example.com" if i % 2 else "race@example.com",
                                    already_sent=False, settings=_settings())
                for i, lead in enumerate(leads)
            )
        )
    assert results.count("email") == 1
    assert results.count("already_sent") == 7
    assert enq.call_count == 1
    assert len(fake_redis.store) == 1
    assert fake_redis.closed == 8


@pytest.mark.asyncio
async def test_claim_is_once_per_address(fake_redis):
    assert await pb.claim_playbook_send("a@b.co") is True
    assert await pb.claim_playbook_send("A@B.co ") is False
    assert await pb.claim_playbook_send("other@b.co") is True
    assert fake_redis.closed == 3  # every per-call client is closed (no pool leak)


@pytest.mark.asyncio
async def test_redis_down_never_sends():
    async def _boom():
        raise ConnectionError("redis down")

    db, lead = _FakeDB(), SimpleNamespace(meta_data={})
    with patch.object(pb, "_redis_client", new=_boom), patch.object(pb, "_enqueue_playbook_email") as enq:
        out = await pb.deliver_playbook(db, lead, "a@b.co", already_sent=False, settings=_settings())
    assert out == "download"
    enq.assert_not_called()


@pytest.mark.asyncio
async def test_stamped_lead_row_short_circuits(fake_redis):
    db, lead = _FakeDB(), SimpleNamespace(meta_data={"playbook_email_queued_at": "x"})
    with patch.object(pb, "_enqueue_playbook_email") as enq:
        out = await pb.deliver_playbook(db, lead, "a@b.co", already_sent=False, settings=_settings())
    assert out == "already_sent"
    enq.assert_not_called()
    assert fake_redis.store == {}
