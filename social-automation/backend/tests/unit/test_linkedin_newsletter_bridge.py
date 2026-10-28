"""Tests for app/services/linkedin_newsletter_bridge.py.

The browser bridge is replaced by a scripted fake: every ``evaluate`` call is
routed by a distinctive fragment of the JS expression to a per-test answer,
so each editor step (mount, paste, SEO, schedule, publish) and its failure
branches run without a browser, LinkedIn, or real sleeps.
"""

import base64
import json
from types import SimpleNamespace

import pytest

import app.services.linkedin_newsletter_bridge as L
from app.services.browser_bridge import BrowserBridgeError

# (fragment, key) — first match wins, so more specific fragments go first.
ROUTES = [
    ("pm:!!document.querySelector", "editor_state"),
    ('document.execCommand("delete")', "clear"),
    ('ClipboardEvent("paste"', "paste"),
    ("words:t.split", "verify"),
    (".article-editor-manage-menu button", "manage"),
    ('==="Settings"', "settings"),
    ("map(e=>e.id)", "seo_ids"),
    ("Object.getOwnPropertyDescriptor", "seo_set"),
    ('==="Save"', "save"),
    (".filter(d=>d.offsetParent).length", "open_dlgs"),
    ('"Schedule post"', "sched_btn"),
    ('!!document.getElementById("share-post__scheduled-date")', "picker"),
    ("data-calendar-day", "day"),
    ('share-post__scheduled-date").value', "date_val"),
    ("Expand timepicker", "expand"),
    ('share-post__scheduled-time").value', "time_val"),
    ("[role=option],", "time"),
    ("media-editor-modal", "media_next"),
    ('startsWith("Next")', "publish_next"),
    ('==="Next")', "sched_next"),
    ('slice(0,300):"none"', "confirm"),
    ("d.innerText.slice(0,500)", "dialogs"),
    ("insertText", "announce"),
    ('==="Publish"', "final"),
    ('==="Schedule"', "final"),
]

PUBLISH_DIALOG = json.dumps(["Publish this edition of your newsletter"])


def _defaults():
    return {
        "editor_state": json.dumps({"u": "x", "pm": True}),
        "clear": "cleared",
        "paste": "ok",
        "verify": json.dumps({"words": 42, "sample": "Hello — world"}),
        "manage": "clicked",
        "settings": "clicked",
        "seo_ids": json.dumps(["t1", "d1"]),
        "seo_set": 5,
        "save": "clicked",
        "open_dlgs": "0",
        "sched_btn": "clicked",
        "picker": True,
        "day": "ok",
        "date_val": "10/21/2026",
        "expand": True,
        "time": "ok",
        "time_val": "9:05 AM",
        "media_next": "closed",
        "publish_next": "clicked",
        "sched_next": "clicked",
        "confirm": "Posting Oct 21 at 9:05 AM",
        "dialogs": PUBLISH_DIALOG,
        "announce": 12,
        "final": "clicked",
        "href": "https://www.linkedin.com/article/edit/123/",
    }


class FakeClient:
    def __init__(self, **overrides):
        self.answers = _defaults()
        self.answers.update(overrides)
        self.evals: list[tuple[str, str]] = []
        self.navigations: list[str] = []
        self.fills: list[tuple[str, str]] = []
        self.uploads: list[dict] = []
        self.sessions: list[dict] = []
        self.upload_result = {"uploaded": True}

    def _answer(self, key):
        val = self.answers[key]
        if isinstance(val, list):  # sequence: pop until the last, then sticky
            val = val.pop(0) if len(val) > 1 else val[0]
        if isinstance(val, Exception):
            raise val
        return val

    async def evaluate(self, expression):
        if expression == "location.href":
            key = "href"
        else:
            key = next((k for frag, k in ROUTES if frag in expression), None)
            assert key is not None, f"unrouted expression: {expression[:120]}"
        self.evals.append((key, expression))
        val = self._answer(key)
        return {} if val is None else {"result": val}

    async def start_session(self, platform, **kw):
        self.sessions.append({"platform": platform, **kw})

    async def navigate(self, url):
        self.navigations.append(url)

    async def fill(self, selector, value):
        self.fills.append((selector, value))

    async def upload_file(self, selector, **kw):
        self.uploads.append({"selector": selector, **kw})
        return self.upload_result

    def keys(self):
        return [k for k, _ in self.evals]


@pytest.fixture(autouse=True)
def fast_clock(monkeypatch):
    """Sleeps advance a fake monotonic clock instead of blocking."""
    clock = {"t": 0.0}

    async def sleep(s):
        clock["t"] += s

    monkeypatch.setattr(L, "asyncio", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(L, "time", SimpleNamespace(monotonic=lambda: clock["t"]))
    return clock


def _bridge(**overrides):
    client = FakeClient(**overrides)
    return L.LinkedInNewsletterBridge(client=client), client


class TestHelpers:
    def test_js_str_escapes(self):
        assert L._js_str('a"b\\c\nd\re') == 'a\\"b\\\\c\\nde'

    def test_default_client_uses_settings(self, monkeypatch):
        made = {}

        class Dummy:
            def __init__(self, url, **kw):
                made.update(url=url, **kw)

        monkeypatch.setattr(L, "BrowserBridgeClient", Dummy)
        monkeypatch.setattr(L, "get_settings", lambda: SimpleNamespace(BROWSER_BRIDGE_URL="http://bridge:9"))
        L.LinkedInNewsletterBridge()
        assert made == {"url": "http://bridge:9", "timeout": 90.0, "platform": "linkedin"}

    @pytest.mark.asyncio
    async def test_eval_without_result_raises(self):
        b, _ = _bridge(clear=None)
        with pytest.raises(L.LinkedInNewsletterBridgeError, match="no result"):
            await b._eval('document.execCommand("delete")')


class TestWaitEditor:
    @pytest.mark.asyncio
    async def test_polls_through_errors_until_mounted(self):
        b, c = _bridge(
            editor_state=[
                BrowserBridgeError(500, "context destroyed"),
                "not-json",
                json.dumps({"pm": False}),
                json.dumps({"pm": True}),
            ]
        )
        assert await b._wait_editor(60) is True
        assert c.keys().count("editor_state") == 4

    @pytest.mark.asyncio
    async def test_times_out(self, fast_clock):
        b, _ = _bridge(editor_state=json.dumps({"pm": False}))
        assert await b._wait_editor(10) is False
        assert fast_clock["t"] >= 10


class TestPasteBody:
    @pytest.mark.asyncio
    async def test_utf8_payload_and_word_count(self):
        b, c = _bridge()
        body = "<p>Καλημέρα — ünïcode</p>"
        assert await b._paste_body(body) == 42
        paste = next(e for k, e in c.evals if k == "paste")
        b64 = base64.b64encode(body.encode("utf-8")).decode()
        assert f'atob("{b64}")' in paste and 'TextDecoder("utf-8")' in paste
        assert c.keys()[:2] == ["clear", "paste"]  # cleared before pasting

    @pytest.mark.asyncio
    async def test_retries_once_when_model_reverts(self):
        b, c = _bridge(verify=[json.dumps({"words": 0}), json.dumps({"words": 7, "sample": "ok"})])
        assert await b._paste_body("<p>x</p>") == 7
        assert c.keys().count("paste") == 2

    @pytest.mark.asyncio
    async def test_empty_after_retries_raises(self):
        b, c = _bridge(verify=json.dumps({"words": 0}))
        with pytest.raises(L.LinkedInNewsletterBridgeError, match="body paste failed"):
            await b._paste_body("<p>x</p>")
        assert c.keys().count("paste") == 2

    @pytest.mark.asyncio
    async def test_no_editor_raises(self):
        b, _ = _bridge(verify=json.dumps({"err": "NO-PM"}))
        with pytest.raises(L.LinkedInNewsletterBridgeError, match="NO-PM"):
            await b._paste_body("x")

    @pytest.mark.asyncio
    async def test_mojibake_refused(self):
        b, _ = _bridge(verify=json.dumps({"words": 3, "sample": 'Hello â€" world'}))
        with pytest.raises(L.LinkedInNewsletterBridgeError, match="mojibake"):
            await b._paste_body("x")


class TestSeo:
    @pytest.mark.asyncio
    async def test_sets_truncated_title_and_escaped_description(self):
        b, c = _bridge(settings=["notfound", "clicked"], open_dlgs=["1", "0"])
        await b._set_seo("T" * 80, 'Say "hi"')
        sets = [e for k, e in c.evals if k == "seo_set"]
        assert len(sets) == 2
        assert 'getElementById("t1")' in sets[0] and f'"{"T" * 60}"' in sets[0]
        assert "T" * 61 not in sets[0]
        assert 'getElementById("d1")' in sets[1] and 'Say \\"hi\\"' in sets[1]

    @pytest.mark.asyncio
    async def test_skips_empty_values(self):
        b, c = _bridge()
        await b._set_seo("Title", "")
        assert c.keys().count("seo_set") == 1

    @pytest.mark.asyncio
    async def test_settings_item_missing(self):
        b, c = _bridge(settings="notfound")
        with pytest.raises(L.LinkedInNewsletterBridgeError, match="Settings menu item"):
            await b._set_seo("t", "d")
        assert c.keys().count("settings") == 6

    @pytest.mark.asyncio
    async def test_dialog_fields_missing(self):
        b, _ = _bridge(seo_ids=json.dumps(["only-one"]))
        with pytest.raises(L.LinkedInNewsletterBridgeError, match="fields not found"):
            await b._set_seo("t", "d")

    @pytest.mark.asyncio
    async def test_dialog_never_closes(self):
        b, _ = _bridge(open_dlgs="1")
        with pytest.raises(L.LinkedInNewsletterBridgeError, match="did not close"):
            await b._set_seo("t", "d")


class TestSchedule:
    @pytest.mark.asyncio
    async def test_naive_iso_is_athens_time(self):
        b, c = _bridge()
        await b._set_schedule("2026-10-21T09:05:00")
        day = next(e for k, e in c.evals if k == "day")
        assert 'lbl="Wednesday, October 21, 2026"' in day
        time_expr = next(e for k, e in c.evals if k == "time")
        assert '==="9:05 AM"' in time_expr

    @pytest.mark.asyncio
    async def test_aware_iso_converted_to_athens_pm(self):
        # 14:30 UTC == 17:30 Athens (EEST, UTC+3)
        b, c = _bridge(time_val="5:30 PM")
        await b._set_schedule("2026-10-21T14:30:00+00:00")
        assert '==="5:30 PM"' in next(e for k, e in c.evals if k == "time")

    @pytest.mark.asyncio
    async def test_midnight_and_noon_labels(self):
        b, c = _bridge(time_val="12:00 AM", date_val="10/22/2026")
        await b._set_schedule("2026-10-22T00:00:00")
        assert '==="12:00 AM"' in next(e for k, e in c.evals if k == "time")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "overrides,match",
        [
            ({"sched_btn": "no-schedule"}, "Schedule post button"),
            ({"picker": False}, "picker did not open"),
            ({"day": "no-day"}, "calendar day not found"),
            ({"date_val": "10/20/2026"}, "calendar pick failed"),
            ({"time": "no-option"}, "time option 9:05 AM not found"),
            ({"time_val": "9:00 AM"}, "timepick failed"),
            ({"sched_next": "no-next"}, "schedule Next"),
            ({"confirm": "none"}, "schedule not committed"),
        ],
    )
    async def test_failures(self, overrides, match):
        b, _ = _bridge(**overrides)
        with pytest.raises(L.LinkedInNewsletterBridgeError, match=match):
            await b._set_schedule("2026-10-21T09:05:00")

    @pytest.mark.asyncio
    async def test_invalid_iso(self):
        b, _ = _bridge()
        with pytest.raises(L.LinkedInNewsletterBridgeError, match="invalid schedule_iso"):
            await b._set_schedule("next tuesday")

    @pytest.mark.asyncio
    async def test_picker_opens_after_polling(self):
        b, c = _bridge(picker=[False, False, True])
        await b._set_schedule("2026-10-21T09:05:00")
        assert c.keys().count("picker") == 3


class TestPublishIssue:
    @pytest.mark.asyncio
    async def test_full_publish_with_cover_seo_and_announcement(self):
        b, c = _bridge(href="https://www.linkedin.com/pulse/issue-3/")
        out = await b.publish_issue(
            title="Build Log #3",
            html_body="<p>Body</p>",
            cover_base64="Zm9v",
            seo_title="SEO",
            seo_description="Desc",
            post_description="Line one\nLine two",
        )
        assert out == {
            "published": True,
            "scheduled": False,
            "article_url": "https://www.linkedin.com/pulse/issue-3/",
            "steps": out["steps"],
            "via": "browser-bridge",
        }
        assert c.sessions == [{"platform": "linkedin", "force": True, "interactive": True, "contention_retries": 6}]
        assert c.navigations == [L.FEED_URL, L.EDITOR_URL]  # feed warmed first
        assert c.fills == [(".article-editor-headline__textarea", "Build Log #3")]
        assert c.uploads[0]["file_base64"] == "Zm9v" and c.uploads[0]["file_name"] == "newsletter-cover.png"
        announce = next(e for k, e in c.evals if k == "announce")
        assert "Line one Line two" in announce  # Quill gets plain one-line text
        assert '==="Publish"' in next(e for k, e in c.evals if k == "final")
        for step in ("feed warmed", "editor mounted", "cover uploaded", "seo set", "publish dialog open", "announcement text set", "published"):
            assert step in out["steps"]

    @pytest.mark.asyncio
    async def test_scheduled_publish_clicks_schedule(self):
        b, c = _bridge()
        out = await b.publish_issue(title="T", html_body="<p>b</p>", schedule_iso="2026-10-21T09:05:00")
        assert out["published"] is False and out["scheduled"] is True
        assert "schedule confirmed" in out["steps"] and "scheduled" in out["steps"]
        assert '==="Schedule"' in next(e for k, e in c.evals if k == "final")

    @pytest.mark.asyncio
    async def test_dry_run_stops_before_publish(self):
        b, c = _bridge()
        out = await b.publish_issue(title="T", html_body="b", dry_run=True)
        assert out["published"] is False and out["article_url"] is None
        assert out["steps"][-1].startswith("dry-run")
        assert "final" not in c.keys()

    @pytest.mark.asyncio
    async def test_editor_retry_then_mount(self):
        # first wait (180s) never mounts; warm retry mounts
        states = [json.dumps({"pm": False})] * 46 + [json.dumps({"pm": True})]
        b, c = _bridge(editor_state=states)
        out = await b.publish_issue(title="T", html_body="b", dry_run=True)
        assert c.navigations.count(L.EDITOR_URL) == 2
        assert "editor not mounted after attempt 1 — retrying warm" in out["steps"]

    @pytest.mark.asyncio
    async def test_editor_never_mounts(self):
        b, _ = _bridge(editor_state=json.dumps({"pm": False}))
        with pytest.raises(L.LinkedInNewsletterBridgeError, match="did not mount"):
            await b.publish_issue(title="T", html_body="b")

    @pytest.mark.asyncio
    async def test_cover_upload_failure(self):
        b, c = _bridge()
        c.upload_result = {"uploaded": False, "error": "no input"}
        with pytest.raises(L.LinkedInNewsletterBridgeError, match="cover upload failed"):
            await b.publish_issue(title="T", html_body="b", cover_base64="eA==")

    @pytest.mark.asyncio
    async def test_reloads_editor_when_dialog_missing_then_succeeds(self):
        b, c = _bridge(dialogs=[json.dumps([])] * 10 + [PUBLISH_DIALOG])
        out = await b.publish_issue(title="T", html_body="b")
        assert "no publish dialog — reloading editor" in out["steps"]
        assert c.navigations[-1] == "https://www.linkedin.com/article/edit/123/"
        assert c.keys().count("publish_next") == 2

    @pytest.mark.asyncio
    async def test_reload_editor_fails_to_remount(self):
        states = [json.dumps({"pm": True})] + [json.dumps({"pm": False})] * 200
        b, _ = _bridge(dialogs=json.dumps([]), editor_state=states)
        with pytest.raises(L.LinkedInNewsletterBridgeError, match="did not remount"):
            await b.publish_issue(title="T", html_body="b")

    @pytest.mark.asyncio
    async def test_dialog_missing_on_new_article_url_raises(self):
        b, _ = _bridge(dialogs=json.dumps(["Something else"]), href="https://www.linkedin.com/article/new/")
        with pytest.raises(L.LinkedInNewsletterBridgeError, match="publish dialog not found"):
            await b.publish_issue(title="T", html_body="b")

    @pytest.mark.asyncio
    async def test_dialog_found_after_reload_via_subscribers_text(self):
        b, _ = _bridge(dialogs=[json.dumps([])] * 10 + [json.dumps(["Post to Anyone + Subscribers"])])
        out = await b.publish_issue(title="T", html_body="b", dry_run=True)
        assert "publish dialog open" in out["steps"]

    @pytest.mark.asyncio
    async def test_publish_button_missing(self):
        b, _ = _bridge(final="no-publish")
        with pytest.raises(L.LinkedInNewsletterBridgeError, match="Publish button: no-publish"):
            await b.publish_issue(title="T", html_body="b")
