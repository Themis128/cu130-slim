"""Skool community sync + posting via the shared browser bridge.

Skool has no public write API — posts go through the browser-novnc shared
session (same bridge session_healer uses for session recovery). All human
posts stay behind the engagement human-gate.
"""

import json
import os
from typing import Any

import httpx

BRIDGE_URL = os.getenv("BROWSER_BRIDGE_URL", "http://browser-novnc:9223")
PLATFORM = "skool"


async def _bridge_call(path: str, body: dict | None = None, timeout: float = 60.0) -> tuple[int, dict]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(
            "POST" if body is not None else "GET",
            f"{BRIDGE_URL}{path}",
            json=body,
            headers={"X-Platform": PLATFORM},
        )
        try:
            return resp.status_code, resp.json()
        except Exception:
            return resp.status_code, {}


async def _bridge_nav(url: str) -> None:
    """Navigate the shared browser to `url` under the skool platform tag."""
    await _bridge_call("/session/navigate", {"url": url}, timeout=90.0)


async def _bridge_eval(expression: str) -> Any:
    """Evaluate JS in the shared browser, returning the response payload
    (callers inspect ``result``/``url`` keys)."""
    code, data = await _bridge_call("/session/evaluate", {"expression": expression}, timeout=30.0)
    if code == 200:
        return data
    return None


# ---------------------------------------------------------------- posting ---
COMMUNITY = "https://www.skool.com/sofia-kakkava-coaching"

OPEN_COMPOSER_JS = """(() => {
  const c = [...document.querySelectorAll('div,button,[role=button],input')]
    .find(e => /share something|write something|start a post|new post/i.test(
      (e.innerText || e.getAttribute('placeholder') || '')));
  if (c) { c.click(); return 'composer-open'; }
  return 'no-composer';
})()"""

SUBMIT_JS = """(() => {
  const b = [...document.querySelectorAll('button')]
    .find(x => /^(post|share|publish)$/i.test((x.innerText||'').trim()));
  if (b && !b.disabled) { b.click(); return 'posted'; }
  return 'no-submit';
})()"""

TYPE_JS = """(async (title, body) => {
  const titleIn = document.querySelector(
    'input[placeholder*="title" i], input[name*=title i]');
  const bodyEd = document.querySelector(
    'textarea, [contenteditable=true]');
  if (!bodyEd) return 'no-editor';
  if (titleIn) { titleIn.value = title;
    titleIn.dispatchEvent(new Event('input', {bubbles:true})); }
  bodyEd.focus();
  if (bodyEd.tagName === 'TEXTAREA') {
    bodyEd.value = body;
    bodyEd.dispatchEvent(new Event('input', {bubbles:true}));
  } else {
    document.execCommand('insertText', false, body);
  }
  return 'typed';
})"""


async def trend_scan() -> dict:
    """Community feed hot topics — top posts by engagement markers."""
    await _bridge_nav(COMMUNITY)
    res = await _bridge_eval("""(() => {
      const all = [...document.querySelectorAll('div')];
      const c = all.filter(e => {
        const t = e.innerText || '';
        return t.includes('\\u2022') && t.length > 60 && t.length < 2500;
      });
      const o = c.filter(e => !c.some(x => x !== e && e.contains(x)
        && (x.innerText || '').length > (e.innerText || '').length * 0.6));
      return JSON.stringify(o.slice(0,15).map(e => ({
        t: (e.innerText||'').slice(0,140),
        eng: ((e.innerText||'').match(/\\d+/g)||['0'])[0]})));
    })()""")
    try:
        raw = res.get("url") or res.get("result") or "[]"
        return {"top": json.loads(raw)}
    except Exception:
        return {"top": []}


async def post_to_community(title: str, body: str) -> dict:
    """Post to the Skool community via the shared browser session.
    Human action — gated by the caller (engagement gate)."""
    await _bridge_nav(COMMUNITY)
    opened = await _bridge_eval(OPEN_COMPOSER_JS)
    if "no-composer" in str(opened):
        return {"ok": False, "error": "composer not found — session expired?"}
    import json as _json

    typed = await _bridge_eval(f"{TYPE_JS}({_json.dumps(title[:140])}, {_json.dumps(body[:4000])})")
    if "no-editor" in str(typed):
        return {"ok": False, "error": "editor not found"}
    submitted = await _bridge_eval(SUBMIT_JS)
    return {"ok": "posted" in str(submitted), "error": None if "posted" in str(submitted) else "submit failed"}


async def draft_community_post(topic: str = "") -> dict:
    """Trend-informed draft: community hot posts + web trends → 3C post."""
    import os

    import httpx

    feed = await trend_scan()
    trends = [t.get("t", "") for t in feed.get("top", [])[:5]]
    dmr = os.environ.get("DMR_BASE_URL", "http://dmr:12434")
    prompt = (
        "Draft one Skool community post for the Sofia Kakkava Visibility Era "
        "community (marketing/visibility coaching, friendly + practical).\n"
        f"Topic: {topic or 'current community momentum — latest hot posts below'}\n"
        f"Community's recent top posts:\n"
        + "\n".join(f"- {t}" for t in trends if t)
        + "\n\nRules: plain, warm, one concrete tip or question, under 150 words. "
        "End with a discussion question. No hashtags spam, no links."
    )
    try:
        r = await httpx.AsyncClient(timeout=60).post(
            f"{dmr}/engines/llamacpp/v1/chat/completions", json={"model": "ai/llama3.2", "messages": [{"role": "user", "content": prompt}]}
        )
        text = r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        text = ""
    return {"draft": text, "trends_seen": len(trends)}
