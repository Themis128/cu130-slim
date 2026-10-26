"""Edge/branch coverage for services/nextcloud_sync.py.

Complements test_nextcloud_sync.py (wire contract): team scoping, base-url
derivation, same-stack refresh, move-verification recreate fallback with
label/assignee carry-over, and the sync_all orchestration.
"""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from app.services import nextcloud_sync as nc

BASE = "https://cloud.example"
COLUMNS = [
    {"id": 5, "title": "Date"},
    {"id": 15, "title": "Platform", "selectionOptions": [{"id": 1, "label": "LinkedIn"}]},
    {"id": 6, "title": "Metric"},
    {"id": 7, "title": "Value"},
    {"id": 8, "title": "Delta"},
    {"id": 9, "title": "Notes"},
]


def _stacks():
    return [
        {"id": 6, "title": "Drafting", "cards": []},
        {"id": 8, "title": "Scheduled", "cards": []},
        {"id": 9, "title": "Published", "cards": []},
        {"id": 10, "title": "Needs Attention", "cards": []},
    ]


@pytest.fixture()
def nc_settings(monkeypatch):
    for k, v in {
        "NEXTCLOUD_SYNC_ENABLED": True,
        "NEXTCLOUD_DAV_URL": "",
        "NEXTCLOUD_BASE_URL": BASE + "/",
        "NEXTCLOUD_USERNAME": "tester",
        "NEXTCLOUD_APP_PASSWORD": "app-pass",
        "NEXTCLOUD_TABLE_ID": 2,
        "NEXTCLOUD_DECK_BOARD_ID": 3,
        "NEXTCLOUD_SYNC_TEAM_ID": "",
    }.items():
        monkeypatch.setattr(nc.settings, k, v)
    return nc.settings


def _mock_client(monkeypatch, handler):
    calls: list[httpx.Request] = []

    def _h(req):
        calls.append(req)
        return handler(req)

    monkeypatch.setattr(nc, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(_h)))
    return calls


class _DB:
    def __init__(self, results):
        self.results = list(results)
        self.queries = []

    async def execute(self, q):
        self.queries.append(q)
        r = self.results.pop(0)
        return SimpleNamespace(all=lambda: list(r), scalars=lambda: SimpleNamespace(all=lambda: list(r)))


def _post(status, text="Hello", post_id=None, **kw):
    return SimpleNamespace(
        id=post_id or uuid.uuid4(),
        status=SimpleNamespace(value=status),
        content_text=text,
        scheduled_at=kw.get("scheduled_at"),
        published_at=kw.get("published_at"),
        failed_at=None,
        failure_reason=kw.get("failure_reason"),
    )


# ── config helpers ─────────────────────────────────────────────────────


def test_team_filter_variants(nc_settings, monkeypatch):
    assert nc._team_filter() is None
    tid = uuid.uuid4()
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_SYNC_TEAM_ID", f" {tid} ")
    assert nc._team_filter() == tid
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_SYNC_TEAM_ID", "not-a-uuid")
    assert nc._team_filter() is None


def test_base_url_from_dav_or_empty(nc_settings, monkeypatch):
    assert nc._base_url() == BASE
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_BASE_URL", " ")
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_DAV_URL", "https://dav.example/sub/remote.php/dav/files/u")
    assert nc._base_url() == "https://dav.example/sub"
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_DAV_URL", "https://dav.example/webdav")
    assert nc._base_url() == ""
    assert nc.nextcloud_sync_enabled() is False


@pytest.mark.asyncio
async def test_client_carries_app_password_and_ocs_header(nc_settings):
    async with nc._client() as c:
        assert c.headers["OCS-APIRequest"] == "true"
        assert c.auth is not None
        assert c.timeout.connect == 10.0


# ── Tables ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_metrics_table_unset(nc_settings, monkeypatch):
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_TABLE_ID", 0)
    assert await nc.sync_follower_metrics(_DB([])) == {"skipped": "NEXTCLOUD_TABLE_ID unset"}


@pytest.mark.asyncio
async def test_metrics_team_scoped_empty(nc_settings, monkeypatch):
    tid = uuid.uuid4()
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_SYNC_TEAM_ID", str(tid))
    db = _DB([[]])
    assert await nc.sync_follower_metrics(db) == {"inserted": 0, "existing": 0, "updated": 0}
    assert "team_id" in str(db.queries[0])


@pytest.mark.asyncio
async def test_metrics_dict_rows_label_platform_and_fallback_account(nc_settings, monkeypatch):
    existing = {"id": 4, "data": {"5": "2026-10-18T00:00", "15": "linkedin", "6": "followers", "7": 10, "9": "x"}}

    def handler(req):
        if req.url.path.endswith("/columns"):
            return httpx.Response(200, json=COLUMNS)
        if req.method == "GET":
            return httpx.Response(200, json=[existing])
        return httpx.Response(200, json={"id": 5})

    calls = _mock_client(monkeypatch, handler)
    rows = [("linkedin", 11, datetime(2026, 10, 18, tzinfo=UTC), None, None, None)]
    out = await nc.sync_follower_metrics(_DB([rows]))
    # account falls back to platform → key differs from the "x" row → insert
    assert out == {"inserted": 1, "existing": 0, "updated": 0}
    body = json.loads(calls[-1].content)
    assert body["data"]["9"] == "linkedin" and body["data"]["8"] == 0


def test_row_key_without_platform_options():
    cols = {"Date": {"id": 1}, "Notes": {"id": 2}, "Metric": {"id": 3}}
    row = {"data": [{"columnId": 1, "value": "2026-10-01"}, {"columnId": 3}]}
    assert nc._row_key(cols, row) == "2026-10-01|||None"
    cols["Platform"] = {"id": 4, "selectionOptions": [{"id": 9, "label": "x"}]}
    row["data"].append({"columnId": 4, "value": 7})
    assert nc._row_key(cols, row).split("|")[1] == "7"


# ── Deck helpers ───────────────────────────────────────────────────────


def test_card_title_description_duedate():
    sched = datetime(2026, 10, 20, 9, tzinfo=UTC)
    p = _post("failed", "", scheduled_at=sched, published_at=sched, failure_reason="boom" * 100)
    assert nc._card_title(p) == f"[failed] {p.id.hex[:8]}"
    d = nc._card_description(p)
    assert "scheduled_at: " in d and "published_at: " in d
    assert "failure: " + ("boom" * 75) in d
    assert nc._card_duedate(p) == sched.isoformat()


def test_duedate_differs():
    assert nc._duedate_differs("x", None) is False
    assert nc._duedate_differs(None, "2026-10-01T00:00:00+00:00") is True
    assert nc._duedate_differs("2026-10-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00") is False
    assert nc._duedate_differs("2026-10-01T00:00:00+00:00", "2026-10-02T00:00:00+00:00") is True
    assert nc._duedate_differs("garbage", "2026-10-02") is True
    assert nc._duedate_differs("same", "same") is False


def test_owner_uid(nc_settings):
    assert nc._owner_uid({"owner": {"uid": "ana"}}) == "ana"
    assert nc._owner_uid({"owner": {"primaryKey": "pk"}}) == "pk"
    assert nc._owner_uid({"owner": {}}) == ""
    assert nc._owner_uid({"owner": "bob"}) == "bob"
    assert nc._owner_uid({}) == "tester"


@pytest.mark.asyncio
async def test_create_card_carries_labels_and_assignees(nc_settings):
    seen: list[tuple[str, str, dict]] = []

    def handler(req):
        body = json.loads(req.content) if req.content else {}
        seen.append((req.method, req.url.path, body))
        if req.url.path.endswith("/assignLabel") and body.get("labelId") == 2:
            return httpx.Response(500)
        if req.url.path.endswith("/assignUser") and body.get("userId") == "bad":
            return httpx.Response(403)
        return httpx.Response(200, json={"id": 77})

    source = {
        "duedate": "2026-11-01T00:00:00+00:00",
        "labels": [{"id": 1}, {"id": 2}, {"no": "id"}],
        "assignedUsers": [{"participant": {"uid": "ana"}}, {"id": "bad"}, {"participant": {}}],
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        out = await nc._create_card(c, 3, 8, _post("scheduled"), source=source)
    assert out == {"id": 77}
    assert seen[0][2]["duedate"] == "2026-11-01T00:00:00+00:00"
    labels = [b["labelId"] for m, p, b in seen if p.endswith("/assignLabel")]
    users = [b["userId"] for m, p, b in seen if p.endswith("/assignUser")]
    assert labels == [1, 2]
    assert users == ["ana", "bad"]


# ── Deck sync ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deck_board_unset(nc_settings, monkeypatch):
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_DECK_BOARD_ID", 0)
    assert await nc.sync_deck_pipeline(_DB([])) == {"skipped": "NEXTCLOUD_DECK_BOARD_ID unset"}


@pytest.mark.asyncio
async def test_deck_same_stack_refresh_and_noop(nc_settings, monkeypatch):
    tid = uuid.uuid4()
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_SYNC_TEAM_ID", str(tid))
    sched = datetime(2026, 10, 20, tzinfo=UTC)
    changed = _post("scheduled", "new title", scheduled_at=sched)
    same = _post("draft", "same")
    unknown = _post("weird")
    stacks = _stacks()
    stacks[1]["cards"] = [
        {"id": 1, "title": "old", "description": f"post:{changed.id}", "owner": {"uid": "ana"}},
        {"id": 2, "archived": True, "description": "post:ignored"},
        {"id": 3, "description": None},
    ]
    stacks[0]["cards"] = [
        {"id": 4, "title": nc._card_title(same), "description": nc._card_description(same)},
    ]
    puts: list[dict] = []

    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, json=stacks)
        puts.append(json.loads(req.content))
        return httpx.Response(200, json={})

    _mock_client(monkeypatch, handler)
    db = _DB([[changed, same, unknown]])
    out = await nc.sync_deck_pipeline(db)
    assert out == {"created": 0, "moved": 0, "archived": 0, "tracked": 3}
    assert "team_id" in str(db.queries[0])
    assert len(puts) == 1
    assert puts[0]["title"] == "[scheduled] new title"
    assert puts[0]["owner"] == "ana"
    assert puts[0]["duedate"] == sched.isoformat()


@pytest.mark.asyncio
async def test_deck_move_not_landed_recreates(nc_settings, monkeypatch):
    p_ok = _post("published", "went out")
    p_fail = _post("failed", "broke")
    p_stay = _post("draft", "fine")
    p_new = _post("draft", "brand new")
    stacks = _stacks()
    stacks[1]["cards"] = [
        {"id": 11, "description": f"post:{p_ok.id}", "labels": [{"id": 5}], "order": 2},
        {"id": 12, "description": f"post:{p_fail.id}"},
    ]
    stacks[0]["cards"] = [{"id": 13, "description": f"intro\npost:{p_stay.id}"}]
    after = _stacks()  # Deck ignored stackId → cards still in Scheduled
    after[1]["cards"] = [
        {"id": 11, "description": f"note\npost:{p_ok.id}", "labels": [{"id": 5}]},
        {"id": 12, "description": f"post:{p_fail.id}"},
        {"id": 99, "archived": True, "description": "post:x"},
        {"id": 98},
    ]
    after[0]["cards"] = [{"id": 13, "description": f"post:{p_stay.id}"}]
    gets = iter([stacks, after])
    log: list[tuple[str, str]] = []

    def handler(req):
        log.append((req.method, req.url.path))
        if req.method == "GET":
            return httpx.Response(200, json=next(gets))
        if req.method == "POST":
            # recreate for the failed post returns no id → source kept
            body = json.loads(req.content)
            return httpx.Response(200, json={} if "[failed]" in body["title"] else {"id": 500})
        return httpx.Response(200, json={})

    _mock_client(monkeypatch, handler)
    out = await nc.sync_deck_pipeline(_DB([[p_ok, p_fail, p_stay, p_new]]))
    assert out["moved"] == 3  # 2 moves + 1 successful recreate
    assert out["created"] == 1
    deletes = [p for m, p in log if m == "DELETE"]
    assert deletes == ["/index.php/apps/deck/api/v1.1/boards/3/stacks/8/cards/11"]
    assert any(p.endswith("/cards/500/assignLabel") for m, p in log)


@pytest.mark.asyncio
async def test_sync_all_runs_both_surfaces(nc_settings, monkeypatch):
    async def _m(db):
        return {"inserted": 1}

    async def _d(db):
        return {"created": 2}

    monkeypatch.setattr(nc, "sync_follower_metrics", _m)
    monkeypatch.setattr(nc, "sync_deck_pipeline", _d)

    class _CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    out = await nc.sync_all(lambda: _CM())
    assert out == {"metrics": {"inserted": 1}, "deck": {"created": 2}}
