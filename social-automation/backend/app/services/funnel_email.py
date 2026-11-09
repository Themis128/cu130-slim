"""Shared branded HTML layout for every funnel email (nurture, quiet lane,
playbook, proposal + follow-ups, agency lifecycle, win-back, billing).

One email-safe shell — table layout, inline CSS only, 600px max width, no
JS — matching the approved "free tech checkup" reference: header, body card,
primary CTA button, "— Name / Org" signature, footer with a plain-link
fallback. Each step keeps its plain-text copy as the source of truth: the
same ``text_body`` is sent as the text/plain part and converted here into
the text/html part, so both alternatives always say the same thing.

Text → HTML conversion is deterministic (no AI, no network):

- blank-line-separated paragraphs → ``<p>``; hard-wrapped lines are joined;
- ``- item`` / ``1. item`` lines → table-based list rows (indent = nesting);
- ``# Heading`` → bold line, ``**bold**`` → ``<strong>``, ``---`` → divider;
- a ``— Name, Org`` line → the signature block;
- each :class:`Cta` URL found in the copy becomes a button at that spot
  (the first CTA is the filled primary, later ones are outlined); every
  other URL / ``[label](url)`` becomes a plain link.

Every piece of copy (including names/companies merged from leads) is
HTML-escaped before markup is added; only ``http(s)`` URLs are linked.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

BRAND = "Cloudless"
SITE_URL = "https://cloudless.gr"
CONSULTATION_URL = "https://cloudless.gr/en/consultation"

_BG = "#f4f6f8"
_CARD = "#ffffff"
_EDGE = "#e5e7eb"
_INK = "#0f172a"
_TEXT = "#1f2937"
_MUTED = "#6b7280"
_ACCENT = "#2563eb"
_FOOT_BG = "#f9fafb"
_FONT = "font-family:Arial,Helvetica,sans-serif;"

# Linear patterns only (no nested quantifiers) — bodies can carry lead input.
_LINK_RE = re.compile(r"\[([^\[\]\n]+)\]\((https?://[^\s()<>\"']+)\)|(https?://[^\s<>\"']+)")
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_BULLET_RE = re.compile(r"^([ \t]*)[-*•][ \t]+(\S.*)$")
_NUM_RE = re.compile(r"^([ \t]*)(\d{1,2})[.)][ \t]+(\S.*)$")
_HEADING_RE = re.compile(r"^#{1,6}[ \t]+(\S.*)$")
_SIGNATURE_RE = re.compile(r"^(?:—|–|--)[ \t]*(\S.*)$")
_RULE_RE = re.compile(r"^-{3,}$")
_GREETING_RE = re.compile(r"^(?:hi|hello|hey|dear|welcome)\b", re.I)
_TRAILING = ".,;:!?)"


@dataclass(frozen=True)
class Cta:
    """A call-to-action button: visible label + target URL."""

    label: str
    url: str


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _split_url(raw: str) -> tuple[str, str]:
    """Separate trailing sentence punctuation from a bare URL match."""
    url = raw.rstrip(_TRAILING)
    return url, raw[len(url) :]


def _link(url: str, label: str | None = None) -> str:
    return f'<a href="{_esc(url)}" target="_blank" style="color:{_ACCENT};text-decoration:underline;">{_esc(label or url)}</a>'


def _text_inline(text: str, highlight: Sequence[str]) -> str:
    out = _BOLD_RE.sub(r"<strong>\1</strong>", _esc(text))
    for phrase in highlight:
        esc = _esc(phrase)
        if esc and esc in out:
            out = out.replace(esc, f"<strong>{esc}</strong>", 1)
    return out


def _inline(text: str, highlight: Sequence[str] = ()) -> str:
    """Escape copy; turn URLs / markdown links into safe anchors."""
    parts: list[str] = []
    pos = 0
    for m in _LINK_RE.finditer(text):
        parts.append(_text_inline(text[pos : m.start()], highlight))
        if m.group(3):
            url, tail = _split_url(m.group(3))
            parts.append(_link(url) + _esc(tail))
        else:
            parts.append(_link(m.group(2), m.group(1)))
        pos = m.end()
    parts.append(_text_inline(text[pos:], highlight))
    return "".join(parts)


def _p(inner: str) -> str:
    return f'<p style="margin:0 0 16px;">{inner}</p>'


def button(cta: Cta, *, primary: bool = True) -> str:
    """Bulletproof table button (Outlook-safe: the cell carries the colour)."""
    if primary:
        cell = f"border-radius:6px;background-color:{_ACCENT};"
        link = f"display:inline-block;padding:14px 28px;font-size:16px;font-weight:bold;color:#ffffff;text-decoration:none;border-radius:6px;{_FONT}"
    else:
        cell = f"border-radius:6px;background-color:{_CARD};border:2px solid {_ACCENT};"
        link = f"display:inline-block;padding:12px 26px;font-size:16px;font-weight:bold;color:{_ACCENT};text-decoration:none;border-radius:6px;{_FONT}"
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:24px 0;"><tr>'
        f'<td align="center" style="{cell}">'
        f'<a href="{_esc(cta.url)}" target="_blank" style="{link}">{_esc(cta.label)}</a>'
        "</td></tr></table>"
    )


def _signature(raw: str) -> str:
    name, _, org = raw.partition(",")
    org_html = f'<br><span style="color:{_MUTED};">{_esc(org.strip())}</span>' if org.strip() else ""
    return f'<p style="margin:8px 0 0;">— {_esc(name.strip())}{org_html}</p>'


def _divider() -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0 24px;">'
        f'<tr><td style="border-top:1px solid {_EDGE};font-size:0;line-height:0;">&nbsp;</td></tr></table>'
    )


def _list(items: list[tuple[int, str, str]], highlight: Sequence[str]) -> str:
    rows = []
    for depth, marker, text in items:
        pad = 4 + depth * 20
        rows.append(
            f'<tr><td width="{pad + 20}" valign="top" style="padding:0 0 8px {pad}px;color:{_MUTED};">{_esc(marker)}</td>'
            f'<td valign="top" style="padding:0 0 8px;">{_inline(text, highlight)}</td></tr>'
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:0 0 16px;color:{_TEXT};font-size:16px;line-height:1.6;{_FONT}">' + "".join(rows) + "</table>"
    )


def _list_item(line: str) -> tuple[int, str, str] | None:
    num = _NUM_RE.match(line)
    if num:
        return len(num.group(1)) // 2, f"{num.group(2)}.", num.group(3)
    bullet = _BULLET_RE.match(line)
    if bullet:
        return len(bullet.group(1)) // 2, "•", bullet.group(2)
    return None


class _Renderer:
    def __init__(self, ctas: Sequence[Cta], highlight: Sequence[str]) -> None:
        self.ctas = list(ctas)
        self.used: set[int] = set()
        self.highlight = highlight
        self.blocks: list[tuple[str, str]] = []  # (kind, html)

    def _cta_index(self, url: str) -> int | None:
        for i, cta in enumerate(self.ctas):
            if i not in self.used and cta.url == url:
                return i
        return None

    def text(self, text: str) -> None:
        """Paragraph text; the first bare occurrence of each CTA URL becomes its button."""
        text = text.strip()
        if not text:
            return
        for m in _LINK_RE.finditer(text):
            url, tail = _split_url(m.group(3)) if m.group(3) else (m.group(2), "")
            idx = self._cta_index(url)
            if idx is None:
                continue
            self.used.add(idx)
            before = text[: m.start()].rstrip()
            if before:
                self.blocks.append(("p", _p(_inline(before, self.highlight))))
            self.blocks.append(("cta", button(self.ctas[idx], primary=idx == 0)))
            self.text(tail.lstrip(_TRAILING) + text[m.end() :])
            return
        heading = _HEADING_RE.match(text)
        if heading:
            self.blocks.append(("p", _p(f'<strong style="color:{_INK};">{_inline(heading.group(1), self.highlight)}</strong>')))
            return
        self.blocks.append(("p", _p(_inline(text, self.highlight))))

    def paragraph(self, para: str) -> None:
        lines = para.split("\n")
        if len(lines) == 1 and _RULE_RE.match(lines[0].strip()):
            self.blocks.append(("hr", _divider()))
            return
        sig = _SIGNATURE_RE.match(lines[0].strip())
        if sig and len(lines) == 1 and len(para) <= 80:
            self.blocks.append(("sig", _signature(sig.group(1))))
            return
        text_buf: list[str] = []
        items: list[tuple[int, str, str]] = []

        def flush_text() -> None:
            if text_buf:
                self.text(" ".join(x.strip() for x in text_buf))
                text_buf.clear()

        def flush_list() -> None:
            if items:
                self.blocks.append(("list", _list(list(items), self.highlight)))
                items.clear()

        for line in lines:
            item = _list_item(line)
            if item:
                flush_text()
                items.append(item)
            elif _HEADING_RE.match(line.strip()):
                flush_list()
                flush_text()
                self.text(line)
            else:
                flush_list()
                text_buf.append(line)
        flush_list()
        flush_text()

    def render(self, text: str) -> str:
        for para in re.split(r"\n[ \t]*\n", text.strip("\n")):
            if para.strip():
                self.paragraph(para.rstrip())
        # CTAs whose URL isn't in the copy still get their button, placed
        # before the signature so the action sits above the sign-off.
        missing = [button(c, primary=i == 0) for i, c in enumerate(self.ctas) if i not in self.used]
        if missing:
            at = next((i for i, (kind, _) in enumerate(self.blocks) if kind == "sig"), len(self.blocks))
            self.blocks[at:at] = [("cta", b) for b in missing]
        return "\n".join(b for _, b in self.blocks)


def text_to_html(text: str, *, ctas: Sequence[Cta] = (), highlight: Sequence[str] = ()) -> str:
    """Convert a plain-text funnel body into the card's inner HTML blocks."""
    return _Renderer(ctas, highlight).render(text)


def first_url(text: str) -> str | None:
    """The first http(s) URL in ``text`` — a step's main link."""
    m = _URL_RE.search(text)
    return _split_url(m.group(0))[0] if m else None


def default_preheader(text: str, limit: int = 120) -> str:
    """Inbox preview line: first non-greeting paragraph, URLs dropped."""
    for para in re.split(r"\n[ \t]*\n", text.strip()):
        flat = " ".join(_URL_RE.sub("", para).split())
        if not flat or _GREETING_RE.match(flat) and len(flat) < 40 or _SIGNATURE_RE.match(flat):
            continue
        flat = flat.replace("**", "")
        return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"
    return ""


def render_layout(
    inner_html: str,
    *,
    title: str,
    preheader: str = "",
    fallback_url: str | None = None,
    footer_note: str | None = None,
    brand: str = BRAND,
) -> str:
    """Wrap pre-rendered body blocks in the branded email shell."""
    pre = (
        '<div style="display:none;max-height:0;max-width:0;overflow:hidden;opacity:0;mso-hide:all;">'
        f"{_esc(preheader)}&#8203;&nbsp;&#8203;&nbsp;&#8203;&nbsp;</div>"
        if preheader
        else ""
    )
    foot: list[str] = []
    if fallback_url:
        foot.append(f"Button not working? Copy this link: {_link(fallback_url)}")
    if footer_note:
        foot.append(_inline(" ".join(footer_note.split())))
    if not foot:
        foot.append(f'{_esc(brand)} · <a href="{SITE_URL}" target="_blank" style="color:{_ACCENT};">cloudless.gr</a>')
    footer = "<br><br>".join(foot)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>{_esc(title)}</title>
</head>
<body style="margin:0;padding:0;background-color:{_BG};{_FONT}">
{pre}<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:{_BG};">
  <tr>
    <td align="center" style="padding:32px 16px;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
        style="max-width:600px;width:100%;background-color:{_CARD};border-radius:8px;">
        <tr>
          <td style="padding:24px 32px;border-bottom:1px solid {_EDGE};">
            <span style="font-size:20px;font-weight:bold;color:{_INK};{_FONT}">{_esc(brand)}</span>
          </td>
        </tr>
        <tr>
          <td style="padding:32px;color:{_TEXT};font-size:16px;line-height:1.6;{_FONT}">
{inner_html}
          </td>
        </tr>
        <tr>
          <td style="padding:16px 32px;background-color:{_FOOT_BG};border-top:1px solid {_EDGE};
            border-radius:0 0 8px 8px;font-size:12px;color:{_MUTED};line-height:1.5;{_FONT}">
            {footer}
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>
"""


_BTN_RE = re.compile(r'<a href="([^"]*)" class="btn">([^<]*)</a>')
_TAG_STYLES = {
    "p": "margin:0 0 16px;",
    "h1": f"margin:0 0 16px;font-size:22px;line-height:1.3;color:{_INK};",
    "ul": "margin:0 0 16px;padding:0 0 0 24px;",
    "ol": "margin:0 0 16px;padding:0 0 0 24px;",
    "li": "margin:0 0 8px;",
    "code": "font-family:Consolas,Menlo,monospace;font-size:14px;background-color:#f3f4f6;padding:1px 4px;border-radius:3px;",
}
_BARE_TAG_RE = re.compile(r"<(p|h1|ul|ol|li|code)>")
_PLAIN_LINK_RE = re.compile(r'<a href=(?="[^"]*"(?! class="btn"))')


def inline_fragment(fragment: str) -> tuple[str, str | None]:
    """Inline-style a trusted HTML fragment (``<p>``/lists/``class="btn"``).

    ``class="btn"`` anchors become bulletproof buttons (first = primary).
    Returns ``(html, first_button_url)`` for the footer fallback link.
    """
    urls: list[str] = []

    def _btn(m: re.Match[str]) -> str:
        url = html.unescape(m.group(1))
        urls.append(url)
        return button(Cta(html.unescape(m.group(2)).strip(), url), primary=len(urls) == 1)

    out = _PLAIN_LINK_RE.sub(f'<a style="color:{_ACCENT};text-decoration:underline;" href=', fragment)
    out = _BTN_RE.sub(_btn, out)
    out = _BARE_TAG_RE.sub(lambda m: f'<{m.group(1)} style="{_TAG_STYLES[m.group(1)]}">', out)
    return out, (urls[0] if urls else None)


def render_funnel_html(
    text_body: str,
    *,
    subject: str,
    cta_label: str | None = None,
    ctas: Iterable[Cta] = (),
    preheader: str | None = None,
    footer_note: str | None = None,
    highlight: Sequence[str] = (),
    brand: str = BRAND,
) -> str:
    """Branded HTML alternative for a plain-text funnel email.

    ``ctas`` pins buttons explicitly; otherwise ``cta_label`` turns the
    body's first URL (its main link) into the primary button. Copy without
    a link renders without a button.
    """
    buttons = list(ctas)
    if not buttons and cta_label:
        url = first_url(text_body)
        if url:
            buttons = [Cta(cta_label, url)]
    return render_layout(
        text_to_html(text_body, ctas=buttons, highlight=highlight),
        title=subject,
        preheader=default_preheader(text_body) if preheader is None else preheader,
        fallback_url=buttons[0].url if buttons else None,
        footer_note=footer_note,
        brand=brand,
    )
