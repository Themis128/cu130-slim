"""Unit coverage for the Nextcloud Tables/Deck sync surface.

The sync (services/nextcloud_sync.py) is fire-and-forget from the worker —
these tests pin the wire contract (object-keyed row data, selection option
ids, post:<uuid> card markers, move fallback, stale-card archive).
"""

import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest

from app.services import nextcloud_sync as nc

DAV = "https://cloud.cloudless.gr/remote.php/dav/files/tester"
BASE = "https://cloud.cloudless.gr"

COLUMNS = [
    {"id": 5, "title": "Date", "type": "datetime", "subtype": "date"},
    {
        "id": 15,
        "title": "Platform",
        "type": "selection",
        "selectionOptions": [{"id": 1, "label": "linkedin"}, {"id": 2, "label": "facebook"}],
    },
    {"id": 6, "title": "Metric", "type": "text", "subtype": "line"},
    {"id": 7, "title": "Value", "type": "number"},
    {"id": 8, "title": "Delta", "type": "number"},
    {"id": 9, "title": "Notes", "type": "text", "subtype": "line"},
]

STACKS = [
    {"id": 5, "title": "Ideas", "cards": []},
    {"id": 6, "title": "Drafting", "cards": []},
    {"id": 7, "title": "In Review", "cards": []},
    {"id": 8, "title": "Scheduled", "cards": []},
    {"id": 9, "title": "Published", "cards": []},
    {"id": 10, "title": "Needs Attention", "cards": []},
]


@pytest.fixture()
def nc_settings(monkeypatch):
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_SYNC_ENABLED", True)
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_DAV_URL", DAV)
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_BASE_URL", BASE)
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_USERNAME", "tester")
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_APP_PASSWORD", "app-pass")
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_TABLE_ID", 2)
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_DECK_BOARD_ID", 2)
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_SYNC_TEAM_ID", "")
    return nc.settings


def _mock_client(monkeypatch, handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def _factory():
        return client

    monkeypatch.setattr(nc, "_client", _factory)
    return client


class _Rows:
    """Result for the metrics path — service calls .all() directly."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Scalars:
    """Result for the deck path — service calls .scalars().all()."""

    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return list(self._items)


class _FakeDB:
    """Returns canned query results in the order the service asks."""

    def __init__(self, results):
        self._results = list(results)

    async def execute(self, _q):
        return self._results.pop(0)


def _post(status, text="Hello world", post_id=None, **kw):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=post_id or uuid.uuid4(),
        status=SimpleNamespace(value=status),
        content_text=text,
        scheduled_at=kw.get("scheduled_at"),
        published_at=kw.get("published_at"),
        failed_at=kw.get("failed_at"),
        failure_reason=kw.get("failure_reason"),
    )


@pytest.mark.asyncio
async def test_sync_disabled_short_circuits(nc_settings, monkeypatch):
    monkeypatch.setattr(nc.settings, "NEXTCLOUD_SYNC_ENABLED", False)
    assert await nc.sync_all(None) == {"skipped": "disabled"}  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_follower_metrics_object_keyed_rows(nc_settings, monkeypatch):
    """Row data must be {"<colId>": value}, selection cells the option id,
    and a first-ever snapshot (prev=0) keeps a real delta."""
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/columns"):
            return httpx.Response(200, json=COLUMNS)
        if request.url.path.endswith("/rows") and request.method == "GET":
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/rows") and request.method == "POST":
            captured.append(json.loads(request.content))
            return httpx.Response(200, json={"id": 99})
        return httpx.Response(404)

    _mock_client(monkeypatch, handler)
    rows = [
        ("linkedin", 853, datetime(2026, 10, 18, tzinfo=UTC), 852, "acct-user", "LinkedIn"),
        ("tiktok", 4, datetime(2026, 10, 18, tzinfo=UTC), None, "cloudless.gr", None),
        # prev=0 must NOT collapse the delta to zero
        ("facebook", 12, datetime(2026, 10, 18, tzinfo=UTC), 0, "fb-page", None),
    ]
    out = await nc.sync_follower_metrics(_FakeDB([_Rows(rows)]))  # type: ignore[arg-type]

    assert out == {"inserted": 2, "existing": 0, "updated": 0}  # tiktok: no Platform option
    assert captured[0]["data"] == {
        "5": "2026-10-18",
        "15": 1,
        "6": "followers",
        "7": 853,
        "8": 1,
        "9": "acct-user",
    }
    assert captured[1]["data"]["8"] == 12  # real growth from zero


@pytest.mark.asyncio
async def test_follower_metrics_dedupes_and_updates(nc_settings, monkeypatch):
    """Same-day rows update Value/Delta when the latest snapshot moves."""
    existing_row = {
        "id": 1,
        "data": [
            {"columnId": 5, "value": "2026-10-18"},
            {"columnId": 15, "value": 1},
            {"columnId": 6, "value": "followers"},
            {"columnId": 7, "value": 850},
            {"columnId": 8, "value": 0},
            {"columnId": 9, "value": "acct-user"},
        ],
    }
    calls: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/columns"):
            return httpx.Response(200, json=COLUMNS)
        if request.method == "GET":
            return httpx.Response(200, json=[existing_row])
        body = json.loads(request.content)
        calls.append((request.method, request.url.path, body))
        return httpx.Response(200, json={"id": 1})

    _mock_client(monkeypatch, handler)
    rows = [("linkedin", 853, datetime(2026, 10, 18, tzinfo=UTC), 850, "acct-user", None)]
    out = await nc.sync_follower_metrics(_FakeDB([_Rows(rows)]))  # type: ignore[arg-type]
    assert out == {"inserted": 0, "existing": 0, "updated": 1}
    assert calls[0][0] == "PUT" and calls[0][1].endswith("/rows/1")
    assert calls[0][2]["data"]["7"] == 853


@pytest.mark.asyncio
async def test_follower_metrics_skips_unchanged(nc_settings, monkeypatch):
    existing_row = {
        "id": 1,
        "data": [
            {"columnId": 5, "value": "2026-10-18"},
            {"columnId": 15, "value": 1},
            {"columnId": 6, "value": "followers"},
            {"columnId": 7, "value": 853},
            {"columnId": 8, "value": 1},
            {"columnId": 9, "value": "acct-user"},
        ],
    }
    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/columns"):
            return httpx.Response(200, json=COLUMNS)
        if request.method == "GET":
            return httpx.Response(200, json=[existing_row])
        posts.append(json.loads(request.content))
        return httpx.Response(200, json={"id": 2})

    _mock_client(monkeypatch, handler)
    rows = [("linkedin", 853, datetime(2026, 10, 18, tzinfo=UTC), 852, "acct-user", None)]
    out = await nc.sync_follower_metrics(_FakeDB([_Rows(rows)]))  # type: ignore[arg-type]
    assert out == {"inserted": 0, "existing": 1, "updated": 0}
    assert posts == []


@pytest.mark.asyncio
async def test_deck_creates_cards_with_post_marker(nc_settings, monkeypatch):
    created: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stacks") and request.method == "GET":
            return httpx.Response(200, json=STACKS)
        if request.url.path.endswith("/cards") and request.method == "POST":
            body = json.loads(request.content)
            created.append(body)
            return httpx.Response(200, json={"id": 42, **body})
        return httpx.Response(404, json={"url": str(request.url)})

    _mock_client(monkeypatch, handler)
    post = _post("draft", "Draft headline text")
    out = await nc.sync_deck_pipeline(_FakeDB([_Scalars([post])]))  # type: ignore[arg-type]

    assert out["created"] == 1
    assert created[0]["title"].startswith("[draft] Draft headline")
    assert f"post:{post.id}" in created[0]["description"]


@pytest.mark.asyncio
async def test_deck_moves_card_on_status_change_and_refreshes(nc_settings, monkeypatch):
    post_id = uuid.uuid4()
    stacks = json.loads(json.dumps(STACKS))
    stacks[1]["cards"] = [  # Drafting holds a card whose post is now scheduled
        {"id": 50, "title": "[draft] x", "description": f"post:{post_id}", "order": 1}
    ]
    moves: list[dict] = []
    updates: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stacks") and request.method == "GET":
            return httpx.Response(200, json=stacks)
        if request.url.path.endswith("/reorder"):
            body = json.loads(request.content)
            moves.append(body)
            card = stacks[1]["cards"].pop(0)
            card["stackId"] = body["stackId"]
            stacks[3]["cards"].append(card)
            return httpx.Response(200, json=card)
        if request.method == "PUT" and "/cards/" in request.url.path:
            updates.append(json.loads(request.content))
            return httpx.Response(200, json={"id": 50})
        return httpx.Response(404, json={"url": str(request.url)})

    _mock_client(monkeypatch, handler)
    post = _post("scheduled", "x", post_id=post_id)
    out = await nc.sync_deck_pipeline(_FakeDB([_Scalars([post])]))  # type: ignore[arg-type]

    assert moves and moves[0]["stackId"] == 8  # Scheduled stack id
    assert updates and updates[0]["title"] == "[scheduled] x"
    assert out["moved"] == 1


@pytest.mark.asyncio
async def test_deck_archives_cards_for_untracked_posts(nc_settings, monkeypatch):
    """Cards whose post vanished (deleted/archived/aged out) get archived."""
    stale_id = uuid.uuid4()
    stacks = json.loads(json.dumps(STACKS))
    stacks[4]["cards"] = [  # Published stack holds a card for a gone post
        {"id": 77, "title": "[published] old", "description": f"post:{stale_id}", "order": 1}
    ]
    archives: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stacks") and request.method == "GET":
            return httpx.Response(200, json=stacks)
        if request.url.path.endswith("/archive"):
            archives.append(request.url.path)
            return httpx.Response(200, json={"id": 77, "archived": True})
        if request.url.path.endswith("/cards") and request.method == "POST":
            return httpx.Response(200, json={"id": 88})
        return httpx.Response(404, json={"url": str(request.url)})

    _mock_client(monkeypatch, handler)
    post = _post("draft", "fresh draft")
    out = await nc.sync_deck_pipeline(_FakeDB([_Scalars([post])]))  # type: ignore[arg-type]

    assert out["archived"] == 1
    assert "/cards/77/archive" in archives[0]
