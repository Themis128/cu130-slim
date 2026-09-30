#!/usr/bin/env python3
"""Post a comment on a public Facebook Page post via the Playwright MCP browser.

Requires a live FB session in the MCP profile — if missing, transplant from the
facebook sidecar first (see session-transplant skill / transplant_fb.py).

Usage:
    python3 fb_comment.py --url https://www.facebook.com/Cloudflare \
        --text "Your comment" [--post-index 0] [--verify-snippet "Your comment"]

Posts to the comment composer of the Nth post on the page (default: first post
with a visible "Comment as" composer). Verifies by checking the page DOM
contains the comment snippet after submit.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                     / "../playwright-mcp-driver/scripts"))
from mcp_client import PlaywrightMCP  # noqa: E402

COMMENT_JS = """async (page) => {
  const SEL = 'div[contenteditable="true"][aria-label*="Comment as"]';
  let boxes = page.locator(SEL);
  let n = await boxes.count();
  let box = n > %(idx)d ? boxes.nth(%(idx)d) : boxes.first();
  if (n === 0 || !(await box.isVisible().catch(() => false))) {
    const btn = page.locator('div[role="button"][aria-label*="Comment on"]').nth(%(idx)d);
    await btn.scrollIntoViewIfNeeded();
    await btn.click();
    await page.waitForTimeout(1200);
    box = page.locator(SEL).nth(%(idx)d);
  }
  await box.scrollIntoViewIfNeeded();
  await box.click();
  await page.waitForTimeout(800);
  await page.keyboard.type(%(text)s, {delay: 25});
  await page.waitForTimeout(600);
  await page.keyboard.press('Enter');
  await page.waitForTimeout(3500);
  const found = await page.evaluate(
    (t) => document.body.innerText.includes(t), %(verify)s);
  return JSON.stringify({postedInDom: found, url: page.url()});
}"""


def comment(url: str, text: str, post_index: int = 0,
            verify_snippet: str | None = None) -> dict:
    verify = (verify_snippet or text[:45])
    with PlaywrightMCP() as mcp:
        mcp.tool("browser_navigate", url=url)
        time.sleep(4)
        out = mcp.tool("browser_run_code_unsafe",
                       code=COMMENT_JS % {
                           "idx": post_index,
                           "text": json.dumps(text),
                           "verify": json.dumps(verify),
                       },
                       timeout=90)
        tail = out.split("### Result")[-1].strip()
        try:
            return json.loads(tail.strip('"'))
        except json.JSONDecodeError:
            return {"raw": tail[:300]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--post-index", type=int, default=0)
    ap.add_argument("--verify-snippet")
    args = ap.parse_args()
    res = comment(args.url, args.text, args.post_index, args.verify_snippet)
    print(json.dumps(res, indent=1))
    sys.exit(0 if res.get("postedInDom") else 1)


if __name__ == "__main__":
    main()
