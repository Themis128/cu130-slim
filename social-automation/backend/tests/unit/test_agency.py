"""Coverage for the autonomous-agency pipeline:
agency_catalog (classifier/pricing), site_audit (the Architect),
agency_pipeline (proposal render, PM milestone logic, gated send)."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import app.services.site_audit as SA
from app.services import agency_catalog as C
from app.services import agency_pipeline as P
from app.services.url_safety import UnsafeUrlError

# ── agency_catalog.classify_services ────────────────────────────────────────

def test_classify_cloud_migration_lead():
    ranked = C.classify_services(
        "We need to migrate our on-premise servers to the cloud, hosting costs are killing us"
    )
    slugs = [s for s, _sc, _h in ranked]
    assert slugs[0] == "cloud"
    assert "serverless" not in slugs or slugs.index("cloud") < slugs.index("serverless")


def test_classify_marketing_lead():
    ranked = C.classify_services("Looking for SEO help and a Google Ads campaign for our shop")
    assert ranked[0][0] == "marketing"


def test_classify_analytics_lead():
    ranked = C.classify_services("We need dashboards and reporting on our warehouse KPIs")
    assert ranked[0][0] == "analytics"


def test_classify_greek_lead():
    ranked = C.classify_services("Θέλουμε διαφήμιση και μάρκετινγκ για το site μας")
    assert ranked[0][0] == "marketing"


def test_classify_empty_returns_nothing():
    assert C.classify_services("hello, I have a question") == []
    assert C.classify_services("") == []


def test_classify_is_explainable():
    ranked = C.classify_services("migrate our kubernetes cluster")
    _slug, _score, hits = ranked[0]
    assert hits  # matched signals are recorded for the triage note


# ── agency_catalog.extract_domain ───────────────────────────────────────────

def test_extract_domain_prefers_explicit_url():
    assert C.extract_domain("Check our site https://www.acme-shop.gr/about please") == "acme-shop.gr"


def test_extract_domain_bare_domain():
    assert C.extract_domain("our company site is acme-shop.gr") == "acme-shop.gr"


def test_extract_domain_skips_own_and_freemail():
    # own domain and free-mail hosts are never auditable leads
    assert C.extract_domain("mail me at someone@gmail.com about cloudless.gr") is None
    assert C.extract_domain("https://cloudless.gr") is None
    assert C.extract_domain(None, "") is None


def test_extract_domain_skips_social_profiles():
    # Leads paste their LinkedIn/Facebook profile URL as "website" — that is
    # not their company site and must never become an audit target.
    assert C.extract_domain("find me at https://linkedin.com/in/maria-k") is None
    assert C.extract_domain("fb page facebook.com/acme") is None
    assert C.extract_domain("https://instagram.com/acme or acme.gr") == "acme.gr"


# ── agency_catalog.price_range ──────────────────────────────────────────────

def test_price_range_bundle():
    low, high = C.price_range(list(C.SERVICES))
    assert (low, high) == (C.BUNDLE_PRICE_EUR, C.BUNDLE_PRICE_EUR)


def test_price_range_partial_sums():
    low, high = C.price_range(["cloud", "analytics"])
    assert low == C.SERVICES["cloud"].price_low_eur + C.SERVICES["analytics"].price_low_eur
    assert high == C.SERVICES["cloud"].price_high_eur + C.SERVICES["analytics"].price_high_eur


def test_price_range_empty():
    assert C.price_range([]) == (0, 0)


# ── site_audit (Architect) ──────────────────────────────────────────────────


class _Resp:
    def __init__(self, status=200, headers=None, text="", url="https://acme.example/"):
        self.status_code = status
        self.headers = headers or {}
        self.text = text
        self.content = text.encode()
        self.url = url
        self.is_redirect = status in (301, 302, 303, 307, 308)


class _FakeClient:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self.calls.append(str(url))
        key = str(url).rstrip("/")
        resp = self.routes.get(key) or self.routes.get(str(url))
        if isinstance(resp, Exception):
            raise resp
        return resp or _Resp(status=404, url=str(url))


def _healthy_page():
    html = """<html lang="en"><head>
    <title>Acme Corp — Industrial widgets</title>
    <meta name="description" content="Acme builds industrial widgets for European factories across the continent.">
    <meta name="viewport" content="width=device-width">
    <link rel="canonical" href="https://acme.example/">
    <script src="https://www.googletagmanager.com/gtag/js?id=G-123"></script>
    </head><body><h1>Acme</h1></body></html>"""
    return _Resp(
        status=200,
        headers={
            "content-type": "text/html; charset=utf-8",
            "strict-transport-security": "max-age=63072000",
            "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
            "x-content-type-options": "nosniff",
            "referrer-policy": "strict-origin-when-cross-origin",
            "content-encoding": "gzip",
            "server": "cloudflare",
        },
        text=html,
    )


@pytest.mark.asyncio
async def test_audit_healthy_site_scores_high(monkeypatch):
    monkeypatch.setattr(SA, "validate_public_http_url", lambda u: u)
    routes = {
        "https://acme.example": _healthy_page(),
        "https://acme.example/robots.txt": _Resp(200, {}, "User-agent: *"),
        "https://acme.example/sitemap.xml": _Resp(200, {}, "<urlset/>"),
    }
    monkeypatch.setattr(SA.httpx, "AsyncClient", lambda **kw: _FakeClient(routes))
    result = await SA.run_site_audit("https://acme.example")
    assert result["score"] >= 90
    assert "ga4" in result["analytics"] or "google-tag-manager" in result["analytics"]
    assert "server:cloudflare" in result["tech"]
    assert not [f for f in result["findings"] if f["severity"] == "critical"]


@pytest.mark.asyncio
async def test_audit_flags_missing_security_and_seo(monkeypatch):
    monkeypatch.setattr(SA, "validate_public_http_url", lambda u: u)
    html = "<html><head></head><body><p>bare</p></body></html>"
    routes = {"http://acme.example": _Resp(200, {"content-type": "text/html"}, html * 100)}
    monkeypatch.setattr(SA.httpx, "AsyncClient", lambda **kw: _FakeClient(routes))
    result = await SA.run_site_audit("http://acme.example")
    checks = {f["check"] for f in result["findings"]}
    assert "https" in checks
    assert "hdr_hsts" in checks and "hdr_csp" in checks
    assert "seo_title" in checks and "seo_desc" in checks
    assert result["score"] < 70
    assert "cloud" in result["recommended_services"]
    assert "marketing" in result["recommended_services"]


@pytest.mark.asyncio
async def test_audit_unreachable_raises_runtimeerror(monkeypatch):
    import httpx as _hx

    monkeypatch.setattr(SA, "validate_public_http_url", lambda u: u)
    routes = {"https://dead.example": _hx.ConnectError("nope")}
    monkeypatch.setattr(SA.httpx, "AsyncClient", lambda **kw: _FakeClient(routes))
    with pytest.raises(RuntimeError, match="unreachable"):
        await SA.run_site_audit("https://dead.example")


@pytest.mark.asyncio
async def test_audit_rejects_private_url():
    with pytest.raises(UnsafeUrlError):
        await SA.run_site_audit("http://127.0.0.1/internal")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    ["http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8000/admin", "http://localhost/"],
)
async def test_audit_refuses_redirect_to_internal_target(monkeypatch, location):
    real_validate = SA.validate_public_http_url

    def _validate(u):
        # The lead host stands in for a public site; everything else goes
        # through the real SSRF guard.
        return u if "acme.example" in u else real_validate(u)

    monkeypatch.setattr(SA, "validate_public_http_url", _validate)
    routes = {
        "https://acme.example": _Resp(302, {"location": location}),
        location.rstrip("/"): _Resp(200, {"content-type": "text/html"}, "<title>secret</title>"),
    }
    seen_kw = {}
    fake = _FakeClient(routes)

    def _client(**kw):
        seen_kw.update(kw)
        return fake

    monkeypatch.setattr(SA.httpx, "AsyncClient", _client)
    result = await SA.run_site_audit("https://acme.example")
    assert seen_kw.get("follow_redirects") is False
    assert not any(c.startswith(location.rstrip("/")) for c in fake.calls)
    assert result["http_status"] == 302


@pytest.mark.asyncio
async def test_audit_follows_public_redirect(monkeypatch):
    monkeypatch.setattr(SA, "validate_public_http_url", lambda u: u)
    routes = {
        "https://acme.example": _Resp(301, {"location": "https://www.acme.example/"}),
        "https://www.acme.example": _healthy_page(),
    }
    fake = _FakeClient(routes)
    monkeypatch.setattr(SA.httpx, "AsyncClient", lambda **kw: fake)
    result = await SA.run_site_audit("https://acme.example")
    assert "https://www.acme.example/" in fake.calls
    assert result["http_status"] == 200


# ── agency_pipeline — proposal render + PM logic ────────────────────────────


def _audit(**kw):
    d = dict(
        url="https://acme.example", score=54,
        findings=[{"check": "hdr_csp", "severity": "warning", "finding": "Missing CSP",
                   "detail": "", "service": "cloud"}],
        action_plan="Detected stack: wordpress\n\nFindings & recommended actions:\n- **Missing CSP** — hdr_csp",
        recommended_services=["cloud", "marketing"],
    )
    d.update(kw)
    return SimpleNamespace(**d)


def test_render_proposal_fixed_scope():
    subject, body = P.render_proposal(
        audit=_audit(), recipient_name="Maria", services=["cloud", "marketing"]
    )
    assert "acme.example" in subject or "audit" in subject.lower()
    assert "Maria" in body
    assert "Cloud Architecture & Migration" in body
    assert "€" in body
    assert "Day 14" in body and "handoff" in body.lower()
    assert C.AUDIT_CTA_URL in body


def test_render_proposal_bundle_uses_flat_price():
    _s, body = P.render_proposal(
        audit=_audit(), recipient_name=None, services=list(C.SERVICES)
    )
    assert f"€{C.BUNDLE_PRICE_EUR}/month" in body


def test_project_milestones_shape():
    ms = P.project_milestones()
    assert [m["day"] for m in ms] == [1, 5, 10, 14]
    assert all(m["done"] is False and m["done_at"] is None for m in ms)


def test_days_in_counts_from_kickoff():
    proj = SimpleNamespace(kickoff_at=datetime.now(UTC) - timedelta(days=4))
    assert P._days_in(proj, datetime.now(UTC)) == 5  # kickoff day = day 1


def test_draft_update_names_milestone():
    proj = SimpleNamespace(name="Acme migration")
    text = P._draft_update(proj, {"day": 5, "key": "architecture", "label": "Architecture draft ready"})
    assert "Day 5" in text and "Acme migration" in text and "Architecture draft" in text


def test_render_handoff_lists_done_milestones():
    proj = SimpleNamespace(
        name="Acme", kickoff_at=datetime.now(UTC) - timedelta(days=14),
        milestones=[{"day": 1, "key": "kickoff", "label": "Kickoff", "done": True, "done_at": "x"},
                    {"day": 5, "key": "arch", "label": "Arch", "done": False, "done_at": None}],
    )
    md = P._render_handoff(proj)
    assert "Kickoff" in md and "Acme" in md


# ── send_proposal — the only path that emails a lead ────────────────────────


class _FakeDB:
    def __init__(self):
        self.committed = False

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_send_proposal_refuses_non_draft():
    proposal = SimpleNamespace(
        status="sent", recipient_email="a@b.c", espo_lead_id=None, meta_data={},
    )
    result = await P.send_proposal(_FakeDB(), proposal)
    assert result["ok"] is False and "not draft" in result["error"]


@pytest.mark.asyncio
async def test_send_proposal_requires_recipient():
    proposal = SimpleNamespace(
        status="draft", recipient_email=None, espo_lead_id=None, meta_data={},
    )
    result = await P.send_proposal(_FakeDB(), proposal)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_send_proposal_marks_sent(monkeypatch):
    sent = {}

    async def fake_send_email(**kw):
        sent.update(kw)

    monkeypatch.setattr("app.services.email_digest.send_email", fake_send_email)
    proposal = SimpleNamespace(
        status="draft", recipient_email="maria@acme.example",
        subject="Audit + plan", body_md="Hi", espo_lead_id=None, meta_data={},
        services=["cloud"], price_low_eur=1200, price_high_eur=2400,
        approved_at=None, sent_at=None,
    )
    db = _FakeDB()
    result = await P.send_proposal(db, proposal, approver="admin@x")
    assert result["ok"] is True
    assert proposal.status == "sent"
    assert proposal.sent_at is not None
    assert sent["to_addrs"] == ["maria@acme.example"]
    assert db.committed


# ── celery registration ─────────────────────────────────────────────────────

def test_agency_beat_tasks_registered():
    from app.worker.celery_app import celery_app

    celery_app.loader.import_default_modules()
    for name in (
        "app.worker.tasks.agency.run_agency_triage_sweep",
        "app.worker.tasks.agency.run_agency_audit_sweep",
        "app.worker.tasks.agency.run_agency_proposal_sweep",
        "app.worker.tasks.agency.run_agency_pm_sweep",
    ):
        assert name in celery_app.tasks


def test_agency_workers_have_governance_contracts():
    import scripts.workforce_audit as WA

    named = {w.name: w for w in WA.WORKFORCE}
    for worker in ("agency-front-desk", "agency-architect", "agency-proposals", "agency-pm"):
        w = named[worker]
        assert w.goal and w.guardrails and w.fallback
        assert w.task in {
            "app.worker.tasks.agency.run_agency_triage_sweep",
            "app.worker.tasks.agency.run_agency_audit_sweep",
            "app.worker.tasks.agency.run_agency_proposal_sweep",
            "app.worker.tasks.agency.run_agency_pm_sweep",
        }


# ── Engineer agent (Joshi 2025 agentic-DevOps: plan → CI gate → human merge) ──

def test_render_engineering_plan_has_gates_and_rollback():
    proposal = SimpleNamespace(
        id=__import__("uuid").uuid4(), services=["cloud", "analytics"],
        recipient_name="Acme Corp", recipient_email="ops@acme.example",
    )
    audit = _audit(findings=[{"finding": "Missing CSP", "check": "hdr_csp",
                              "severity": "warning", "detail": "", "service": "cloud"}])
    plan = P.render_engineering_plan(proposal, audit)
    assert "agency/acme-corp-" in plan
    assert "Cloud Architecture" in plan and "Data Analytics" in plan
    assert "Missing CSP" in plan          # findings carry into the plan
    assert "pytest" in plan and "rollback" in plan.lower()  # CI gate + rollback mandatory


def test_render_engineering_plan_without_audit():
    proposal = SimpleNamespace(
        id=__import__("uuid").uuid4(), services=["marketing"],
        recipient_name=None, recipient_email="x@y.z", audit_id=None,
    )
    plan = P.render_engineering_plan(proposal, None)
    assert "Digital Marketing" in plan and "Branch" in plan


# ── SAT (Yang et al. 2025): client reports carry verifiable evidence ─────────

def test_draft_update_carries_evidence_line():
    proj = SimpleNamespace(name="Acme migration")
    text = P._draft_update(proj, {"day": 5, "key": "architecture",
                                  "label": "Arch draft", "evidence": "PR #12"})
    assert "Verify: PR #12" in text
    text2 = P._draft_update(proj, {"day": 5, "key": "a", "label": "Arch"})
    assert "Verify:" in text2  # never a bare status claim


def test_handoff_has_verify_section():
    proj = SimpleNamespace(name="Acme", kickoff_at=datetime.now(UTC) - timedelta(days=14),
                           milestones=[{"day": 1, "key": "k", "label": "Kickoff",
                                        "done": True, "done_at": "x"}])
    assert "Verify it yourself" in P._render_handoff(proj)


def test_agency_engineer_registered_and_governed():
    import scripts.workforce_audit as WA
    from app.worker.celery_app import celery_app

    celery_app.loader.import_default_modules()
    assert "app.worker.tasks.agency.run_agency_engineer_sweep" in celery_app.tasks
    w = {x.name: x for x in WA.WORKFORCE}["agency-engineer"]
    assert w.goal and w.guardrails and w.fallback and w.autonomy == "review"


def test_classify_linkedin_b2b_lead():
    r = C.classify_services("we need linkedin b2b lead generation and social selling for our sales pipeline")
    slugs = [s for s, _score, _h in r]
    assert slugs[0] == "linkedin_growth"          # strongest signal wins
    assert "linkedin_growth" in slugs


def test_price_range_bundle_all_five():
    low, high = C.price_range(list(C.SERVICES))
    assert (low, high) == (C.BUNDLE_PRICE_EUR, C.BUNDLE_PRICE_EUR)


def test_render_proposal_linkedin_phases():
    audit = _audit(url="https://acme.example")
    _s, body = P.render_proposal(audit=audit, recipient_name="Maria",
                                services=["linkedin_growth"])
    assert "LinkedIn B2B Growth" in body
    assert "Crawl — organic foundation" in body
    assert "Walk — boost validation" in body
    assert "Run — campaign scale" in body
    assert "€900" in body and "€1800" in body


# ── Cheng et al. 2024 — influencer-entrepreneurship stage model ─────────────

def test_influence_stage_progression():
    from app.services.influence_stage import classify_stage

    assert classify_stage(followers=0).key == "potential"
    assert classify_stage(followers=500, engagement_rate=0.05).key == "formulation"
    assert classify_stage(followers=2500).key == "formulation"
    assert classify_stage(followers=15_000).key == "competence"
    assert classify_stage(followers=500, engagement_rate=0.08).key == "competence"
    assert classify_stage(followers=80_000, paid_collabs=5).key == "monetization"
    assert classify_stage(followers=80_000, recurring_offers=1).key == "monetization"
    assert classify_stage(followers=200_000, own_products=True).key == "reward"
    # every stage carries actions + brand guidance
    for s in __import__("app.services.influence_stage", fromlist=["STAGES"]).STAGES:
        assert s.actions and s.brand_relation and s.attentional_capital
