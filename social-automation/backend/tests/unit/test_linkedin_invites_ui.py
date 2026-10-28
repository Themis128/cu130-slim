"""Regression tests for the multi-surface invite-dialog opener.

LinkedIn keeps moving the "Invite connections" entry point — the task must
try every documented surface and fail closed (no stray clicks) when the
control genuinely isn't there, including when the session is logged out.
"""
import pytest

from app.services import linkedin_invites as inv
from app.services.browser_bridge import BrowserBridgeError


class _FakeClient:
    """Scriptable stand-in for BrowserBridgeClient."""

    def __init__(self, *, button_on: set[str] | None = None, menu_on: set[str] | None = None, login_wall: bool = False):
        # URLs (by substring) where the Invite control exists as a direct
        # button vs inside the ⋯ overflow menu.
        self.button_on = button_on or set()
        self.menu_on = menu_on or set()
        self.login_wall = login_wall
        self.navigated: list[str] = []
        self.clicks: int = 0
        self.menu_clicks: int = 0

    async def navigate(self, url: str):
        self.navigated.append(url)
        if "unreachable" in url:
            raise BrowserBridgeError(500, "nav failed")

    async def evaluate(self, expression: str):
        if "session_key" in expression:
            return {"result": str(self.login_wall).lower()}
        if inv._RESULTS_CONTAINER.strip("'") in expression:
            # Dialog opens once a real Invite control was clicked.
            return {"result": str(self.clicks + self.menu_clicks > 0).lower()}
        if "aria-label" in expression and "menuitem" not in expression:
            # ⋯ overflow opener exists only where the menu item would too.
            return {"result": "true" if any(s in self.navigated[-1] for s in self.menu_on) else "false"}
        if "menuitem" in expression:
            if self.menu_clicks:
                return {"result": "false"}
            self.menu_clicks += 1
            return {"result": "true"}
        if "querySelectorAll('button')" in expression:
            if any(s in self.navigated[-1] for s in self.button_on):
                return {"result": '{"x": 10, "y": 20}'}
            return {"result": "null"}
        return {"result": "null"}

    async def mouse_click(self, x: int, y: int):
        self.clicks += 1


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _instant(_):
        return None

    monkeypatch.setattr(inv.asyncio, "sleep", _instant)


@pytest.mark.asyncio
async def test_opens_on_first_surface():
    c = _FakeClient(button_on={"admin/page-posts"})
    assert await inv._open_invite_dialog(c) is True
    assert c.navigated == [inv._PAGE_POSTS_URL]
    assert c.clicks == 1


@pytest.mark.asyncio
async def test_falls_through_surfaces_to_menu():
    # Button absent on page-posts; public page offers it via the ⋯ menu.
    c = _FakeClient(menu_on={"cloudless"})
    assert await inv._open_invite_dialog(c) is True
    assert c.navigated == [inv._PAGE_POSTS_URL, inv._PAGE_PUBLIC_URL]
    assert c.clicks == 0 and c.menu_clicks == 1


@pytest.mark.asyncio
async def test_fails_closed_when_control_missing():
    c = _FakeClient(button_on={"never"})
    assert await inv._open_invite_dialog(c) is False
    assert len(c.navigated) == 3
    assert c.clicks == 0  # nothing unrelated was clicked


@pytest.mark.asyncio
async def test_stops_immediately_on_login_wall():
    c = _FakeClient(login_wall=True, button_on={"page-posts"})
    assert await inv._open_invite_dialog(c) is False
    assert c.navigated == [inv._PAGE_POSTS_URL]
    assert c.clicks == 0 and c.menu_clicks == 0


@pytest.mark.asyncio
async def test_navigate_error_skips_surface():
    c = _FakeClient(button_on={"admin/dashboard"})

    orig_nav = c.navigate

    async def flaky(url):
        if "page-posts" in url or "cloudless" in url:
            raise BrowserBridgeError(500, "boom")
        await orig_nav(url)

    c.navigate = flaky
    assert await inv._open_invite_dialog(c) is True
    assert inv._PAGE_ADMIN_URL in c.navigated


def test_client_click_is_async_mock_compatible():
    # _open_invite_dialog no longer calls client.click() — guard the removal
    # so a regression back to the stale selector path is caught.
    assert "client.click" not in open(inv.__file__).read()
