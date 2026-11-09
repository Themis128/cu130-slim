"""Unit coverage for the extended Nextcloud sync surfaces.

``nextcloud_extras`` fans out CRM→Tables, deploy/check watch → Talk,
failed publishes → Talk, scheduled posts → CalDAV, and Forms intake →
EspoCRM. All paths are non-fatal by contract — these tests pin the
idempotency cursors, dedupe rules, and wire shapes with mocked HTTP/DB.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.services import nextcloud_extras as ncx

DAV = "https://cloud.cloudless.gr"


@pytest.fixture()
def nc_settings(monkeypatch):
    monkeypatch.setattr(ncx.settings, "NEXTCLOUD_SYNC_ENABLED", True)
    monkeypatch.setattr(ncx.settings, "NEXTCLOUD_BASE_URL", DAV)
    monkeypatch.setattr(ncx.settings, "NEXTCLOUD_DAV_URL", f"{DAV}/remote.php/dav/files/tester")
    monkeypatch.setattr(ncx.settings, "NEXTCLOUD_USERNAME", "tester")
    monkeypatch.setattr(ncx.settings, "NEXTCLOUD_APP_PASSWORD", "app-pass")
    monkeypatch.setattr(ncx.settings, "NEXTCLOUD_TABLE_ID", 2)
    monkeypatch.setattr(ncx.settings, "REDIS_URL", "redis://localhost:6379/0")
    return ncx.settings


@pytest.fixture()
def redis_store(monkeypatch):
    store: dict[str, str] = {}

    class _FakeRedis:
        async def get(self, key):
            return store.get(key)

        async def set(self, key, value, ex=None):
            store[key] = value

        async def aclose(self):
            return None

    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: _FakeRedis())
    return store


def _mock_nc(monkeypatch, handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(ncx, "_client", lambda: client)
    return client


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def scalars(self):
        return self


class _FakeDB:
    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _q):
        return self._results.pop(0) if self._results else _Result([])


def _post(**kw):
    return SimpleNamespace(
        id=kw.get("id") or uuid.uuid4(),
        status=kw.get("status"),
        content_text=kw.get("content_text", "Hello world"),
        scheduled_at=kw.get("scheduled_at"),
        published_at=kw.get("published_at"),
        failed_at=kw.get("failed_at"),
        failure_reason=kw.get("failure_reason"),
        targets=kw.get("targets", []),
    )


# ── cursors ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cursor_roundtrip(redis_store):
    assert await ncx._cursor("k") is None
    await ncx._set_cursor("k", "v1")
    assert await ncx._cursor("k") == "v1"
    assert redis_store["ncx:k"] == "v1"


@pytest.mark.asyncio
async def test_cursor_failures_are_nonfatal(monkeypatch):
    import redis.asyncio as aioredis

    def _boom(*a, **k):
        raise RuntimeError("no redis")

    monkeypatch.setattr(aioredis, "from_url", _boom)
    assert await ncx._cursor("k") is None
    await ncx._set_cursor("k", "v")  # must not raise


# ── Tables helpers ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_table_returns_existing_and_adds_missing_columns(monkeypatch, nc_settings):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/tables"):
            return httpx.Response(200, json=[{"id": 7, "title": "CRM Pipeline"}])
        if request.url.path.endswith("/columns") and request.method == "GET":
            return httpx.Response(200, json=[{"id": 1, "title": "LeadId"}])
        return httpx.Response(200, json={"id": 99})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tid = await ncx._ensure_table(client, "CRM Pipeline", [{"title": "LeadId"}, {"title": "Name"}])
    assert tid == 7
    assert ("POST", "/index.php/apps/tables/api/1/tables/7/columns") in calls


@pytest.mark.asyncio
async def test_ensure_table_creates_when_missing(monkeypatch, nc_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tables") and request.method == "GET":
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/tables") and request.method == "POST":
            return httpx.Response(200, json={"id": 42})
        return httpx.Response(200, json={"id": 1})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tid = await ncx._ensure_table(client, "New Table", [{"title": "A"}])
    assert tid == 42


@pytest.mark.asyncio
async def test_existing_lead_ids_reads_leadid_column(monkeypatch, nc_settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"data": [{"columnId": 3, "value": "lead-1"}, {"columnId": 4, "value": "x"}]},
                {"data": [{"columnId": 3, "value": "lead-2"}]},
                {"data": [{"columnId": 4, "value": "no-leadid"}]},
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    seen = await ncx._existing_lead_ids(client, 7, {"LeadId": 3})
    assert seen == {"lead-1", "lead-2"}
    # no LeadId column → empty set without reading rows
    assert await ncx._existing_lead_ids(client, 7, {}) == set()


# ── sync_crm_pipeline ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crm_pipeline_inserts_new_leads(monkeypatch, nc_settings, redis_store):
    from app.services import espocrm_client as espo

    monkeypatch.setattr(espo, "configured", lambda: True)

    async def _list_all(entity, **kw):
        return [
            {"id": "l1", "name": "Ann", "emailAddress": "a@x.gr", "status": "New", "source": "Web", "createdAt": "2026-11-01T10:00:00Z"},
            {"id": "l2", "name": "Bob", "emailAddress": "", "status": "", "source": "", "createdAt": ""},
        ]

    monkeypatch.setattr(espo, "list_all", _list_all)
    posted_rows = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tables") and request.method == "GET":
            return httpx.Response(200, json=[{"id": 7, "title": "CRM Pipeline"}])
        if request.url.path.endswith("/columns") and request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "title": "LeadId"},
                    {"id": 2, "title": "Name"},
                    {"id": 3, "title": "Email"},
                    {"id": 4, "title": "Status"},
                    {"id": 5, "title": "Source"},
                    {"id": 6, "title": "Created"},
                ],
            )
        if request.url.path.endswith("/rows") and request.method == "GET":
            return httpx.Response(200, json=[{"data": [{"columnId": 1, "value": "l1"}]}])
        if request.url.path.endswith("/rows") and request.method == "POST":
            import json as _j

            posted_rows.append(_j.loads(request.content))
            return httpx.Response(200, json={"id": 1})
        return httpx.Response(200, json={})

    _mock_nc(monkeypatch, handler)
    out = await ncx.sync_crm_pipeline(None)  # type: ignore[arg-type]
    assert out["inserted"] == 1
    assert out["total_leads"] == 2
    # JSON object keys serialize as strings; l2 has no createdAt → no Created cell
    assert posted_rows[0]["data"]["1"] == "l2"
    assert posted_rows[0]["data"]["2"] == "Bob"
    assert "6" not in posted_rows[0]["data"]


@pytest.mark.asyncio
async def test_crm_pipeline_skips_when_unconfigured(monkeypatch):
    from app.services import espocrm_client as espo

    monkeypatch.setattr(espo, "configured", lambda: False)
    assert await ncx.sync_crm_pipeline(None) == {"skipped": "espocrm not configured"}  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_crm_pipeline_empty_and_error_paths(monkeypatch, nc_settings, redis_store):
    from app.services import espocrm_client as espo

    monkeypatch.setattr(espo, "configured", lambda: True)

    async def _empty(*a, **k):
        return []

    monkeypatch.setattr(espo, "list_all", _empty)
    assert await ncx.sync_crm_pipeline(None) == {"leads": 0}  # type: ignore[arg-type]

    async def _rows(*a, **k):
        return [{"id": "l1"}]

    monkeypatch.setattr(espo, "list_all", _rows)
    monkeypatch.setattr(
        ncx,
        "_client",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    out = await ncx.sync_crm_pipeline(None)  # type: ignore[arg-type]
    assert "error" in out


# ── deploy / check watch ───────────────────────────────────────────────


_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _patch_gh(monkeypatch, commits_map, check_runs=None):
    """Route all httpx.AsyncClient constructions to MockTransport handlers."""

    def factory(*args, **kwargs):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            for label_url, commits in commits_map.items():
                if url == label_url:
                    return httpx.Response(200, json=commits)
            if "/check-runs" in url:
                return httpx.Response(200, json={"check_runs": check_runs or []})
            return httpx.Response(404, json={})

        return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ncx.httpx, "AsyncClient", factory)


@pytest.mark.asyncio
async def test_deploy_events_seed_cursor_then_announce(monkeypatch, nc_settings, redis_store):
    from app.services import nextcloud_talk as talk

    monkeypatch.setattr(talk, "talk_enabled", lambda: True)
    sent = []

    async def _talk(text, purpose=None):
        sent.append(text)
        return True

    monkeypatch.setattr(talk, "post_to_talk", _talk)
    commits = [
        {"sha": "aaa1111bbbb", "commit": {"message": "first\nsecond"}},
        {"sha": "bbb2222cccc", "commit": {"message": "older"}},
    ]
    _patch_gh(monkeypatch, {u: commits for _l, u in ncx._GH_COMMITS})

    # first run seeds cursor silently
    out1 = await ncx.sync_deploy_events()
    assert out1["announced"] == []
    assert sent == []

    # second run with a new head commit announces only the delta
    new_commits = [{"sha": "ddd3333eeee", "commit": {"message": "new deploy"}}] + commits
    _patch_gh(monkeypatch, {u: new_commits for _l, u in ncx._GH_COMMITS})
    out2 = await ncx.sync_deploy_events()
    assert any("ddd3333" in a for a in out2["announced"])
    assert any("ddd3333" in t for t in sent)


@pytest.mark.asyncio
async def test_deploy_events_talk_disabled_and_fetch_failure(monkeypatch, nc_settings, redis_store):
    from app.services import nextcloud_talk as talk

    monkeypatch.setattr(talk, "talk_enabled", lambda: False)
    _patch_gh(monkeypatch, {ncx._GH_COMMITS[0][1]: [{"sha": "x" * 40, "commit": {"message": "m"}}], ncx._GH_COMMITS[1][1]: "not-a-list"})
    out = await ncx.sync_deploy_events()
    assert isinstance(out["announced"], list)


@pytest.mark.asyncio
async def test_check_status_alerts_on_failed_checks(monkeypatch, nc_settings, redis_store):
    from app.services import nextcloud_talk as talk

    monkeypatch.setattr(talk, "talk_enabled", lambda: True)
    sent = []

    async def _talk(text, purpose=None):
        sent.append(text)
        return True

    monkeypatch.setattr(talk, "post_to_talk", _talk)
    sha = "f" * 40
    commits = [{"sha": sha, "commit": {"message": "m"}}]
    _patch_gh(
        monkeypatch,
        {u: commits for _l, u in ncx._GH_COMMITS},
        check_runs=[
            {"name": "test", "status": "completed", "conclusion": "failure"},
            {"name": "lint", "status": "completed", "conclusion": "success"},
        ],
    )
    out = await ncx.sync_check_status()
    assert out["check_alerts"]
    assert any("checks failed" in t for t in sent)
    # cursor written — second run is silent
    out2 = await ncx.sync_check_status()
    assert out2["check_alerts"] == []


@pytest.mark.asyncio
async def test_check_status_skips_when_talk_disabled(monkeypatch):
    from app.services import nextcloud_talk as talk

    monkeypatch.setattr(talk, "talk_enabled", lambda: False)
    assert await ncx.sync_check_status() == {"skipped": "talk disabled"}


@pytest.mark.asyncio
async def test_check_status_pending_runs_no_cursor(monkeypatch, nc_settings, redis_store):
    from app.services import nextcloud_talk as talk

    monkeypatch.setattr(talk, "talk_enabled", lambda: True)
    sha = "e" * 40
    _patch_gh(
        monkeypatch,
        {u: [{"sha": sha}] for _l, u in ncx._GH_COMMITS},
        check_runs=[{"name": "test", "status": "in_progress"}],
    )
    out = await ncx.sync_check_status()
    assert out["check_alerts"] == []
    assert not any(k.startswith("ncx:check:") for k in redis_store)


# ── failed publishes ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_failed_publishes_seed_then_alert(monkeypatch, nc_settings, redis_store):
    from app.services import nextcloud_talk as talk

    monkeypatch.setattr(talk, "talk_enabled", lambda: True)
    sent = []

    async def _talk(text, purpose=None):
        sent.append(text)
        return True

    monkeypatch.setattr(talk, "post_to_talk", _talk)

    t1 = SimpleNamespace(social_account=SimpleNamespace(platform="linkedin"), error_message="boom")
    p1 = _post(status=SimpleNamespace(value="failed"), failed_at=datetime(2026, 11, 8, 10, tzinfo=UTC), content_text="First post", targets=[t1])
    t2 = SimpleNamespace(social_account=None, error_message=None)
    p2 = _post(status=SimpleNamespace(value="failed"), failed_at=datetime(2026, 11, 8, 11, tzinfo=UTC), content_text="Second post", targets=[t2])
    db = _FakeDB([_Result([p1, p2])])

    # first run seeds cursor, sends nothing
    out1 = await ncx.sync_failed_publishes(db)
    assert out1 == {"alerts": 0, "seeded": 2}
    assert sent == []

    # second run with a newer failure alerts
    p3 = _post(status=SimpleNamespace(value="failed"), failed_at=datetime(2026, 11, 8, 12, tzinfo=UTC), content_text="Third", targets=[t1])
    db2 = _FakeDB([_Result([p1, p2, p3])])
    out2 = await ncx.sync_failed_publishes(db2)
    assert out2["alerts"] == 1
    assert any("Third" in t for t in sent)


@pytest.mark.asyncio
async def test_failed_publishes_empty_and_disabled(monkeypatch, nc_settings):
    from app.services import nextcloud_talk as talk

    db = _FakeDB([_Result([])])
    monkeypatch.setattr(talk, "talk_enabled", lambda: False)
    assert await ncx.sync_failed_publishes(db) == {"skipped": "talk disabled"}
    monkeypatch.setattr(talk, "talk_enabled", lambda: True)
    db2 = _FakeDB([_Result([])])
    assert await ncx.sync_failed_publishes(db2) == {"alerts": 0}


# ── calendar ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ics_for_post_shape():
    post = _post(
        scheduled_at=datetime(2026, 11, 9, 9, 30, tzinfo=UTC),
        content_text="Line one\nLine two",
    )
    ics = ncx._ics_for_post(post, "linkedin")
    assert "BEGIN:VCALENDAR" in ics and "END:VCALENDAR" in ics
    assert "DTSTART:20261109T093000Z" in ics
    assert f"UID:post-{post.id}@socialauto" in ics
    assert "SUMMARY:[linkedin] Line one Line two" in ics


@pytest.mark.asyncio
async def test_calendar_posts_writes_events(monkeypatch, nc_settings):
    when = datetime.now(UTC) + timedelta(hours=2)
    post = _post(scheduled_at=when, content_text="Scheduled post")
    db = _FakeDB([_Result([(post, "linkedin")])])
    puts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            return httpx.Response(
                207,
                text="<d:response><d:href>/remote.php/dav/calendars/tester/personal/</d:href></d:response>",
            )
        if request.method == "PUT":
            puts.append(request.url.path)
            return httpx.Response(201)
        return httpx.Response(404)

    _mock_nc(monkeypatch, handler)
    out = await ncx.sync_calendar_posts(db)
    assert out == {"events": 1, "calendar": "personal"}
    assert puts and puts[0].endswith(f"post-{post.id}.ics")


@pytest.mark.asyncio
async def test_calendar_posts_no_posts_and_no_calendar(monkeypatch, nc_settings):
    assert await ncx.sync_calendar_posts(_FakeDB([_Result([])])) == {"events": 0}

    when = datetime.now(UTC) + timedelta(hours=1)
    post = _post(scheduled_at=when)
    db = _FakeDB([_Result([(post, "x")])])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PROPFIND":
            return httpx.Response(207, text="<html>nothing</html>")
        return httpx.Response(404)

    _mock_nc(monkeypatch, handler)
    assert await ncx.sync_calendar_posts(db) == {"error": "no calendar found"}


# ── forms intake ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_forms_intake_imports_new_submissions(monkeypatch, nc_settings, redis_store):
    from app.services import espocrm_client as espo

    monkeypatch.setattr(espo, "configured", lambda: True)
    upserted = []

    async def _upsert(payload):
        upserted.append(payload)
        return "lead-1"

    monkeypatch.setattr(espo, "upsert_lead", _upsert)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/forms/api/v3/forms" in url:
            return httpx.Response(200, json={"ocs": {"data": [{"id": "f1"}]}})
        if "/submission/f1" in url:
            return httpx.Response(
                200,
                json={
                    "ocs": {
                        "data": {
                            "submissions": [
                                {
                                    "id": "s2",
                                    "answers": [
                                        {"questionId": "1", "text": "Ann"},
                                        {"questionId": "2", "text": "ann@x.gr"},
                                    ],
                                },
                                {
                                    "id": "s3",
                                    "answers": [{"questionId": "2", "text": "no-email-here"}],
                                },
                            ]
                        }
                    }
                },
            )
        return httpx.Response(404)

    _mock_nc(monkeypatch, handler)
    out = await ncx.sync_forms_intake()
    assert out == {"imported": 1}
    assert upserted[0]["emailAddress"] == "ann@x.gr"
    assert upserted[0]["name"] == "Ann"
    assert redis_store["ncx:form:f1"] == "s3"


@pytest.mark.asyncio
async def test_forms_intake_dedupes_and_skips_unconfigured(monkeypatch, nc_settings, redis_store):
    from app.services import espocrm_client as espo

    monkeypatch.setattr(espo, "configured", lambda: False)
    assert await ncx.sync_forms_intake() == {"skipped": "espocrm not configured"}

    monkeypatch.setattr(espo, "configured", lambda: True)
    redis_store["ncx:form:f1"] = "s9"
    upserted = []

    async def _upsert(payload):
        upserted.append(payload)
        return "x"

    monkeypatch.setattr(espo, "upsert_lead", _upsert)

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/forms/api/v3/forms" in url:
            return httpx.Response(200, json={"ocs": {"data": [{"id": "f1"}]}})
        if "/submission/f1" in url:
            return httpx.Response(
                200,
                json={"ocs": {"data": {"submissions": [{"id": "s5", "answers": [{"questionId": "1", "text": "a@b.c"}]}]}}},
            )
        return httpx.Response(404)

    _mock_nc(monkeypatch, handler)
    out = await ncx.sync_forms_intake()
    assert out == {"imported": 0}  # "s5" <= cursor "s9" → deduped
    assert upserted == []


@pytest.mark.asyncio
async def test_forms_intake_http_error_and_exception(monkeypatch, nc_settings, redis_store):
    from app.services import espocrm_client as espo

    monkeypatch.setattr(espo, "configured", lambda: True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    _mock_nc(monkeypatch, handler)
    out = await ncx.sync_forms_intake()
    assert "error" in out

    monkeypatch.setattr(
        ncx,
        "_client",
        lambda: (_ for _ in ()).throw(RuntimeError("down")),
    )
    out2 = await ncx.sync_forms_intake()
    assert "error" in out2


# ── sync_extras dispatcher ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_extras_disabled(monkeypatch):
    from app.services import nextcloud_sync as ncs

    monkeypatch.setattr(ncs, "nextcloud_sync_enabled", lambda: False)
    assert await ncx.sync_extras(None) == {"skipped": "disabled"}  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_sync_extras_runs_all_lanes(monkeypatch, nc_settings, redis_store):
    results = {}

    async def _lane(name):
        async def _f(*a, **k):
            results[name] = True
            return {"ok": name}

        return _f

    monkeypatch.setattr(ncx, "sync_crm_pipeline", await _lane("crm"))
    monkeypatch.setattr(ncx, "sync_deploy_events", await _lane("deploys"))
    monkeypatch.setattr(ncx, "sync_check_status", await _lane("checks"))
    monkeypatch.setattr(ncx, "sync_failed_publishes", await _lane("failed"))
    monkeypatch.setattr(ncx, "sync_calendar_posts", await _lane("calendar"))
    monkeypatch.setattr(ncx, "sync_forms_intake", await _lane("forms"))
    out = await ncx.sync_extras(object())  # type: ignore[arg-type]
    assert set(results) == {"crm", "deploys", "checks", "failed", "calendar", "forms"}
    assert out["crm"] == {"ok": "crm"}
