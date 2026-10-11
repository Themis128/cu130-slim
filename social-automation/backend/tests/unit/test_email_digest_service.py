"""Coverage for app/services/email_digest.py."""

import ssl
from types import SimpleNamespace

import pytest

import app.services.email_digest as ED


def _issue(sev, title="t", detail=""):
    return SimpleNamespace(severity=sev, title=title, detail=detail)


def _report(**kw):
    d = dict(
        issues=[],
        overview={"total_posts": 5, "published_posts": 3},
        top_posts=[],
        growth=None,
        impressions_24h=100,
        engagement_24h=10,
        days=1,
        team_name="Team",
        generated_at=__import__("datetime").datetime(2026, 1, 2, tzinfo=__import__("datetime").UTC),
        emailed=False,
        email_error=None,
        to_slack_markdown=lambda: "*md* 🟢 ❌ ⚠️",
    )
    d.update(kw)
    return SimpleNamespace(**d)


def _settings(**kw):
    d = dict(
        CLOUDFLARE_EMAIL_API_TOKEN="",
        CLOUDFLARE_API_TOKEN="",
        CLOUDFLARE_ACCOUNT_ID="",
        SMTP_HOST="smtp.local",
        SMTP_PORT=587,
        SMTP_USER="u",
        SMTP_PASSWORD="p",
        SMTP_FROM="from@x.co",
        SMTP_USE_TLS=True,
        SMTP_SSL_VERIFY=True,
        DIGEST_EMAIL_TO="a@x.co, b@x.co",
        DIGEST_EMAIL_ISSUES_ONLY=False,
        EMAIL_PROVIDER="local",
        EMAIL_SUPPRESS_ADDR_PATTERNS="",
    )
    d.update(kw)
    return SimpleNamespace(**d)


def _set(monkeypatch, **kw):
    s = _settings(**kw)
    monkeypatch.setattr(ED, "get_settings", lambda: s)
    return s


class TestHelpers:
    def test_sanitize(self):
        assert ED._sanitize_log_text("a\nb\rc") == "a\\nb\\rc"
        assert ED._sanitize_log_text("x" * 500, max_len=10) == "x" * 10
        assert ED._sanitize_log_text("a\x01b") == "ab"

    def test_email_api_token(self):
        s = _settings(CLOUDFLARE_EMAIL_API_TOKEN="  t1 ")
        assert ED._email_api_token(s) == "t1"
        s = _settings(CLOUDFLARE_API_TOKEN="t2")
        assert ED._email_api_token(s) == "t2"
        assert ED._email_api_token(_settings()) == ""

    def test_html_escape(self):
        assert ED._html_escape('<a b="c">&') == "&lt;a b=&quot;c&quot;&gt;&amp;"

    def test_subject(self):
        r = _report(issues=[_issue("error"), _issue("error"), _issue("warning")])
        assert "ERRORS (2)" in ED.digest_email_subject(r)
        r.issues = [_issue("warning")]
        assert "Warnings (1)" in ED.digest_email_subject(r)
        r.issues = []
        assert "OK" in ED.digest_email_subject(r)

    def test_plaintext(self):
        out = ED.digest_to_plaintext(_report())
        assert "*" not in out
        assert "[ERROR]" in out and "[WARN]" in out

    def test_recipients(self):
        assert ED._recipients(_settings()) == ["a@x.co", "b@x.co"]
        assert ED._recipients(_settings(DIGEST_EMAIL_TO="")) == []
        assert ED._recipients(_settings(DIGEST_EMAIL_TO="  x@y.z ,, ")) == ["x@y.z"]

    def test_filter_suppressed(self):
        s = _settings(EMAIL_SUPPRESS_ADDR_PATTERNS="*test*, bad@x.co")
        out = ED._filter_suppressed(["a@test.com", "bad@x.co", "ok@x.co"], s)
        assert out == ["ok@x.co"]
        s2 = _settings(EMAIL_SUPPRESS_ADDR_PATTERNS="")
        assert ED._filter_suppressed(["a@x"], s2) == ["a@x"]


class TestDigestToHtml:
    def test_full_report(self):
        r = _report(
            issues=[_issue("error", "E1", "d1"), _issue("warning", "W1")],
            top_posts=[{"engagement": 5, "impressions": 99, "snippet": "<b>hi</b>"}, {"engagement": 1, "impressions": 2, "platform_post_id": "pid"}],
            growth={
                "followers": [
                    {"platform": "instagram", "account": "acc", "end": 100, "delta": 5, "rate": 2.5, "prev_delta": 3, "forecast": 120},
                    {"platform": "x", "end": 50, "delta": 1},
                ],
                "non_follower_reach_pct": 42.0,
                "follower_adds": {"organic": 10, "paid": 2},
                "peak_hours": [9, 17],
                "funnel": [
                    {"platform": "ig", "impressions": 1000, "engagement": 50, "clicks": 10, "er_pct": 5.0},
                    {"platform": "x", "impressions": 500, "engagement": 10, "clicks": 0},
                ],
            },
        )
        html = ED.digest_to_html(r)
        assert "SocialAuto daily report" in html
        assert "Top posts" in html
        assert "&lt;b&gt;hi&lt;/b&gt;" in html  # escaped
        assert "@acc" in html
        assert "+5" in html and "+2.5%" in html and "prev +3" in html
        assert "non-follower reach" in html
        assert "organic" in html
        assert "9, 17" in html
        assert "ER <b>5.0%</b>" in html
        assert "<b>E1</b>" in html and "— d1" in html
        assert "<b>W1</b>" in html

    def test_minimal_report(self):
        html = ED.digest_to_html(_report())
        assert "Issues:</strong> none" in html
        assert "Top posts" not in html
        assert "Growth" not in html


class TestAttachFiles:
    def test_variants(self, tmp_path):
        from email.message import EmailMessage

        msg = EmailMessage()
        f = tmp_path / "a.png"
        f.write_bytes(b"PNGDATA")
        ED._attach_files(
            msg,
            [
                {"data": b"raw", "name": "b.png", "mime": "image/png", "cid": "img1"},
                {"data": str(f), "name": "a.png"},
                {"data": b"x", "name": "c.bin", "mime": ""},
            ],
        )
        parts = list(msg.iter_attachments())
        assert len(parts) == 3
        assert parts[0]["Content-ID"] == "<img1>"
        assert "inline" in str(parts[0]["Content-Disposition"])
        assert "attachment" in str(parts[1]["Content-Disposition"])


class _FakeSMTP:
    def __init__(self, *a, **kw):
        self.host = a[0] if a else kw.get("host")
        self.messages = []
        self.logged_in = None
        self.ehlo_calls = 0
        self.tls = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def ehlo(self):
        self.ehlo_calls += 1

    def has_extn(self, name):
        return name == "starttls"

    def starttls(self, context=None):
        self.tls = True

    def login(self, u, p):
        self.logged_in = (u, p)

    def send_message(self, msg):
        self.messages.append(msg)


class TestSendEmailSmtp:
    def test_not_configured(self, monkeypatch):
        _set(monkeypatch, SMTP_HOST="")
        with pytest.raises(RuntimeError, match="SMTP not configured"):
            ED.send_email_smtp(subject="s", text_body="t")

    def test_starttls_path(self, monkeypatch):
        _set(monkeypatch)
        smtp = _FakeSMTP()
        monkeypatch.setattr(ED.smtplib, "SMTP", lambda *a, **kw: smtp)
        ED.send_email_smtp(subject="S", text_body="T", html_body="<b>H</b>", attachments=[{"data": b"d", "name": "f.png", "mime": "image/png"}])
        assert smtp.tls
        assert smtp.logged_in == ("u", "p")
        assert len(smtp.messages) == 1
        msg = smtp.messages[0]
        assert msg["Subject"] == "S"
        assert msg["To"] == "a@x.co, b@x.co"
        assert len(list(msg.iter_attachments())) == 1

    def test_ssl_port_login(self, monkeypatch):
        _set(monkeypatch, SMTP_PORT=465)
        smtp = _FakeSMTP()
        monkeypatch.setattr(ED.smtplib, "SMTP_SSL", lambda *a, **kw: smtp)
        ED.send_email_smtp(subject="s", text_body="t", to_addrs=["one@x.co"])
        assert smtp.messages[0]["To"] == "one@x.co"

    def test_no_tls_extn_no_user(self, monkeypatch):
        _set(monkeypatch, SMTP_USER="", SMTP_USE_TLS=True)
        smtp = _FakeSMTP()
        smtp.has_extn = lambda n: False
        monkeypatch.setattr(ED.smtplib, "SMTP", lambda *a, **kw: smtp)
        ED.send_email_smtp(subject="s", text_body="t")
        assert not smtp.tls and smtp.logged_in is None

    def test_ssl_verify_off(self, monkeypatch):
        _set(monkeypatch, SMTP_SSL_VERIFY=False)
        smtp = _FakeSMTP()
        monkeypatch.setattr(ED.smtplib, "SMTP", lambda *a, **kw: smtp)
        ctx = ssl.create_default_context()
        monkeypatch.setattr(ED.ssl, "create_default_context", lambda: ctx)
        ED.send_email_smtp(subject="s", text_body="t")
        assert ctx.verify_mode == ssl.CERT_NONE


class TestSendEmailCfSmtp:
    def test_no_token(self, monkeypatch):
        _set(monkeypatch)
        with pytest.raises(RuntimeError, match="Cloudflare SMTP"):
            ED.send_email_cloudflare_smtp(subject="s", text_body="t")

    def test_happy(self, monkeypatch):
        _set(monkeypatch, CLOUDFLARE_EMAIL_API_TOKEN="tok")
        smtp = _FakeSMTP()
        monkeypatch.setattr(ED.smtplib, "SMTP_SSL", lambda *a, **kw: smtp)
        ED.send_email_cloudflare_smtp(subject="s", text_body="t", html_body="<b>h</b>")
        assert smtp.logged_in == ("api_token", "tok")


class _HTTP:
    def __init__(self, resps):
        self._resps = list(resps)
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def post(self, url, **kw):
        self.posts.append((url, kw))
        return self._resps.pop(0)


def _cf_resp(status, body, content=b"x", text=""):
    return SimpleNamespace(status_code=status, content=content, text=text, json=lambda: body)


class TestSendEmailCfVerified:
    @pytest.mark.asyncio
    async def test_missing_config(self, monkeypatch):
        _set(monkeypatch)
        with pytest.raises(RuntimeError, match="Cloudflare send needs"):
            await ED.send_email_cloudflare_verified(subject="s", text_body="t", html_body="h")

    @pytest.mark.asyncio
    async def test_rest_success(self, monkeypatch):
        _set(monkeypatch, CLOUDFLARE_ACCOUNT_ID="acc", CLOUDFLARE_EMAIL_API_TOKEN="tok")
        c = _HTTP([_cf_resp(200, {"success": True}), _cf_resp(200, {"success": True})])
        monkeypatch.setattr(ED.httpx, "AsyncClient", lambda **kw: c)
        await ED.send_email_cloudflare_verified(subject="s", text_body="t", html_body="h")
        assert len(c.posts) == 2  # one per recipient
        assert "acc" in c.posts[0][0]

    @pytest.mark.asyncio
    async def test_rest_fail_smtp_fallback_ok(self, monkeypatch):
        _set(monkeypatch, CLOUDFLARE_ACCOUNT_ID="acc", CLOUDFLARE_EMAIL_API_TOKEN="tok")
        c = _HTTP([_cf_resp(403, {"errors": [{"message": "scope"}]}, text="forbidden")])
        monkeypatch.setattr(ED.httpx, "AsyncClient", lambda **kw: c)
        smtp = _FakeSMTP()
        monkeypatch.setattr(ED.smtplib, "SMTP_SSL", lambda *a, **kw: smtp)
        await ED.send_email_cloudflare_verified(subject="s", text_body="t", html_body="h")
        assert smtp.messages  # fallback sent

    @pytest.mark.asyncio
    async def test_rest_fail_smtp_fail_raises(self, monkeypatch):
        _set(monkeypatch, CLOUDFLARE_ACCOUNT_ID="acc", CLOUDFLARE_EMAIL_API_TOKEN="tok")
        c = _HTTP([_cf_resp(500, {}, text="err")])
        monkeypatch.setattr(ED.httpx, "AsyncClient", lambda **kw: c)
        monkeypatch.setattr(ED.smtplib, "SMTP_SSL", lambda *a, **kw: (_ for _ in ()).throw(OSError("smtp down")))
        with pytest.raises(RuntimeError, match="SMTP fallback also failed"):
            await ED.send_email_cloudflare_verified(subject="s", text_body="t", html_body="h")


class TestSendEmail:
    @pytest.mark.asyncio
    async def test_all_suppressed(self, monkeypatch):
        _set(monkeypatch, EMAIL_SUPPRESS_ADDR_PATTERNS="*@test.co")
        smtp = _FakeSMTP()
        monkeypatch.setattr(ED.smtplib, "SMTP", lambda *a, **kw: smtp)
        await ED.send_email(subject="s", text_body="t", to_addrs=["x@test.co"])
        assert not smtp.messages

    @pytest.mark.asyncio
    async def test_local_provider(self, monkeypatch):
        _set(monkeypatch)
        smtp = _FakeSMTP()
        monkeypatch.setattr(ED.smtplib, "SMTP", lambda *a, **kw: smtp)
        await ED.send_email(subject="s", text_body="t")
        assert smtp.messages

    @pytest.mark.asyncio
    async def test_cf_with_attachments_goes_smtp(self, monkeypatch):
        _set(monkeypatch, EMAIL_PROVIDER="cloudflare", CLOUDFLARE_EMAIL_API_TOKEN="tok")
        smtp = _FakeSMTP()
        monkeypatch.setattr(ED.smtplib, "SMTP_SSL", lambda *a, **kw: smtp)
        await ED.send_email(subject="s", text_body="t", attachments=[{"data": b"d", "name": "f"}])
        assert smtp.messages

    @pytest.mark.asyncio
    async def test_cf_no_attachments_uses_verified(self, monkeypatch):
        _set(monkeypatch, EMAIL_PROVIDER="cf", CLOUDFLARE_ACCOUNT_ID="acc", CLOUDFLARE_EMAIL_API_TOKEN="tok")
        c = _HTTP([_cf_resp(200, {"success": True}), _cf_resp(200, {"success": True})])
        monkeypatch.setattr(ED.httpx, "AsyncClient", lambda **kw: c)
        await ED.send_email(subject="s", text_body="t")
        assert len(c.posts) == 2


class TestEmailDigest:
    @pytest.mark.asyncio
    async def test_no_recipient(self, monkeypatch):
        _set(monkeypatch, DIGEST_EMAIL_TO="")
        r = _report()
        out = await ED.email_digest(r)
        assert "not set" in out.email_error

    @pytest.mark.asyncio
    async def test_issues_only_skip(self, monkeypatch):
        _set(monkeypatch, DIGEST_EMAIL_ISSUES_ONLY=True)
        r = _report()
        out = await ED.email_digest(r)
        assert "skipped" in out.email_error

    @pytest.mark.asyncio
    async def test_local_no_smtp_host(self, monkeypatch):
        _set(monkeypatch, SMTP_HOST="")
        r = _report()
        out = await ED.email_digest(r)
        assert "SMTP_HOST not set" in out.email_error

    @pytest.mark.asyncio
    async def test_success(self, monkeypatch):
        _set(monkeypatch)
        smtp = _FakeSMTP()
        monkeypatch.setattr(ED.smtplib, "SMTP", lambda *a, **kw: smtp)
        r = _report(issues=[_issue("error", "Broke")])
        out = await ED.email_digest(r)
        assert out.emailed is True
        assert out.email_error is None
        assert "ERRORS (1)" in smtp.messages[0]["Subject"]

    @pytest.mark.asyncio
    async def test_send_failure(self, monkeypatch):
        _set(monkeypatch)
        monkeypatch.setattr(ED.smtplib, "SMTP", lambda *a, **kw: (_ for _ in ()).throw(OSError("refused")))
        r = _report()
        out = await ED.email_digest(r)
        assert "refused" in out.email_error
