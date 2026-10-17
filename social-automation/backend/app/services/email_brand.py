"""Branded HTML email renderer for SocialAuto email campaigns.

Every campaign should ship in the Cloudless brand shell — elegant dark
navy card, teal accents, generous whitespace, one clear CTA. Campaigns
created with only ``text_body`` get their HTML generated here at create
and send time; campaigns that pass their own ``html_body`` keep it.

Body conversion is deterministic — no AI, no network:

- Blank-line-separated paragraphs → styled <p> blocks.
- ``1.``/``2.`` … (or ``01 ·``) list runs → numbered ghost cards.
- Lines starting with ``>`` or ``"`` … ending ``"`` / ``— Name`` →
  pull-quote blocks (first quote teal, later quotes pink accent).
- ``P.S.`` / ``P.P.S.`` paragraph → subdued postscript.
- A paragraph containing a markdown link ``[label](https://…)`` where the
  label looks like a call to action, or a line ``CTA: label | url`` →
  centered pill button.
- ``{{first_name}}`` and other ``{{merge}}`` fields pass through — the
  send path renders them per-recipient afterwards.
"""
from __future__ import annotations

import html
import re

_BG = "#0a0f1a"
_CARD = "#101a2c"
_CARD_EDGE = "#1e2c47"
_TEXT = "#d7deeb"
_MUTED = "#93a1ba"
_FAINT = "#5d6b88"
_TEAL = "#2dd4ee"
_TEAL_DEEP = "#0e7490"
_PINK = "#f0a6d8"
_GHOST = "#22304a"

_FONT = "font-family:'Work Sans','Segoe UI',Arial,Helvetica,sans-serif;"
_SERIF = "font-family:Georgia,'Times New Roman',serif;"

_NUM_RE = re.compile(r"^(\d{1,2})[.)]\s*(?:·|-)?\s*(.*)$", re.S)
_QUOTE_RE = re.compile(r'^[>"“]\s*(.*)$', re.S)
_ATTRIB_RE = re.compile(r"^[—–-]\s*(.+)$")
_CTA_LINE_RE = re.compile(r"^CTA:\s*(.*?)\s*\|\s*(https?://\S+)\s*$", re.I | re.S)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_PS_RE = re.compile(r"^P\.?P?\.?S\.?", re.I)


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _inline(text: str) -> str:
    """Escape, then restore merge fields and markdown links."""
    out = _esc(text)
    out = re.sub(
        r"\{\{(\s*[\w.]+\s*)\}\}",
        lambda m: '<b style="color:' + _TEAL + ';">{{'
        + m.group(1) + "}}</b>",
        out,
    )
    out = _MD_LINK_RE.sub(
        r'<a href="\2" style="color:' + _TEAL + r';text-decoration:underline;'
        r'text-underline-offset:3px;">\1</a>',
        out,
    )
    return out


def _p(text: str, extra: str = "") -> str:
    return (
        f'<p style="margin:0 0 18px;color:{_TEXT};font-size:16px;'
        f'line-height:1.78;{_FONT}{extra}">{_inline(text)}</p>'
    )


def _quote(text: str, attrib: str | None, pink: bool) -> str:
    bar = _PINK if pink else _TEAL
    attrib_html = ""
    if attrib:
        attrib_html = (
            f'<br/><span style="font-style:normal;{_FONT}color:{_FAINT};'
            f'font-size:13px;letter-spacing:.04em;">— {_esc(attrib)}</span>'
        )
    quote = _esc(text.strip().strip(chr(0x201C) + chr(0x201D) + chr(0x22)))
    return (
        '<table role="presentation" width="100%" cellpadding="0" '
        'cellspacing="0" style="margin:6px 0 22px;"><tr>'
        f'<td width="3" style="background:{bar};border-radius:2px;"></td>'
        f'<td style="padding:10px 4px 10px 20px;color:{_TEXT};'
        f'font-size:17px;line-height:1.7;font-style:italic;{_SERIF}">'
        f"“{quote}”{attrib_html}"
        "</td></tr></table>"
    )


def _numbered(items: list[tuple[str, str]]) -> str:
    rows = []
    for num, text in items:
        num = num.zfill(2)
        # Split "Headline. rest" — first sentence becomes the card title.
        parts = re.split(r"(?<=[.!?:])\s+", text.strip(), maxsplit=1)
        head = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        rest_html = (
            f'<br/><span style="color:{_MUTED};">{_inline(rest)}</span>'
            if rest
            else ""
        )
        rows.append(
            '<table role="presentation" width="100%" cellpadding="0" '
            'cellspacing="0" style="margin:0 0 10px;background:#0c1424;'
            f'border:1px solid {_CARD_EDGE};border-radius:12px;"><tr>'
            f'<td width="52" valign="top" style="padding:18px 0 18px 18px;'
            f'{_SERIF}font-size:26px;color:{_GHOST};line-height:1;">{num}</td>'
            f'<td style="padding:16px 20px 16px 8px;color:{_TEXT};'
            f'font-size:15px;line-height:1.65;{_FONT}">'
            f'<b style="color:{_TEAL};font-weight:600;">{_inline(head)}</b>'
            f"{rest_html}</td></tr></table>"
        )
    return "".join(rows) + '<div style="height:10px;"></div>'


def _cta(label: str, url: str) -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" '
        'cellspacing="0"><tr><td align="center" style="padding:14px 0 8px;">'
        f'<a href="{html.escape(url, quote=True)}" '
        f'style="display:inline-block;background:{_TEAL};color:{_BG};'
        f'font-size:16px;font-weight:700;text-decoration:none;'
        f'padding:15px 44px;border-radius:999px;{_FONT}letter-spacing:.01em;"'
        f'>{_esc(label)}</a></td></tr></table>'
    )


def _ps(text: str) -> str:
    return (
        f'<p style="margin:6px 0 0;color:{_MUTED};font-size:13.5px;'
        f'line-height:1.7;{_FONT}">{_inline(text)}</p>'
    )


def body_to_inner_html(text_body: str) -> str:
    """Convert plain-text campaign body into branded inner HTML blocks."""
    blocks: list[str] = []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text_body) if p.strip()]
    i = 0
    quote_idx = 0
    while i < len(paras):
        para = paras[i]

        # Explicit CTA line
        m = _CTA_LINE_RE.match(para)
        if m:
            blocks.append(_cta(m.group(1), m.group(2)))
            i += 1
            continue

        # Numbered-list run
        nums: list[tuple[str, str]] = []
        while i < len(paras):
            joined = paras[i].replace("\n", " ")
            mm = _NUM_RE.match(joined)
            if not mm:
                break
            nums.append((mm.group(1), mm.group(2)))
            i += 1
        if nums:
            blocks.append(_numbered(nums))
            continue

        # Quote (with optional "— Name" attribution line inside the para)
        qm = _QUOTE_RE.match(para)
        if qm:
            body = qm.group(1).strip()
            attrib = None
            lines = [x.strip() for x in body.split("\n") if x.strip()]
            if len(lines) > 1:
                am = _ATTRIB_RE.match(lines[-1])
                if am:
                    attrib = am.group(1)
                    body = " ".join(lines[:-1])
            blocks.append(_quote(body, attrib, pink=quote_idx > 0))
            quote_idx += 1
            i += 1
            continue

        # A paragraph that is exactly one markdown link → CTA button
        m = _MD_LINK_RE.fullmatch(para.strip())
        if m:
            blocks.append(_cta(m.group(1), m.group(2)))
            i += 1
            continue

        # Postscript
        if _PS_RE.match(para):
            blocks.append(_ps(para))
            i += 1
            continue

        blocks.append(_p(para))
        i += 1

    return "".join(blocks)


def render_branded_html(
    text_body: str,
    *,
    subject: str = "",
    from_name: str = "Cloudless",
    hero_url: str | None = None,
    preheader: str = "",
    unsubscribe_hint: bool = False,
) -> str:
    """Wrap converted body blocks in the Cloudless email shell."""
    inner = body_to_inner_html(text_body)
    pre = (
        f'<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">'
        f"{_esc(preheader)}&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;</div>"
        if preheader
        else ""
    )
    hero = (
        '<tr><td><img src="' + html.escape(hero_url, quote=True) + '" width="600" alt="" '
        'style="display:block;width:100%;max-width:600px;height:auto;"/></td></tr>'
        if hero_url
        else ""
    )
    footer_extra = (
        f'<tr><td align="center" style="padding:0 44px 30px;color:{_FAINT};'
        f'font-size:11.5px;line-height:1.6;{_FONT}">You are receiving this '
        "because you asked to hear from Cloudless.</td></tr>"
        if unsubscribe_hint
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta name="color-scheme" content="dark"/><title>{_esc(subject)}</title></head>
<body style="margin:0;padding:0;background:{_BG};">{pre}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_BG};">
<tr><td align="center" style="padding:36px 14px 40px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

<tr><td style="padding:0 8px 22px;">
<table role="presentation" cellpadding="0" cellspacing="0"><tr>
<td style="{_FONT}font-size:21px;font-weight:700;color:#eef2fa;letter-spacing:.02em;">cloudless<span style="color:{_TEAL};">.gr</span></td>
<td style="padding-left:14px;{_FONT}font-size:11px;color:{_FAINT};letter-spacing:.18em;text-transform:uppercase;">Clear skies · Zero friction</td>
</tr></table>
</td></tr>

<tr><td style="background:{_CARD};border:1px solid {_CARD_EDGE};border-radius:18px;overflow:hidden;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
{hero}
<tr><td style="padding:34px 44px 30px;">
{inner}
</td></tr>
</table>
</td></tr>

<tr><td style="padding:26px 44px 0;text-align:center;">
<div style="border-top:1px solid {_CARD_EDGE};"></div>
</td></tr>
<tr><td align="center" style="padding:18px 44px 4px;color:{_MUTED};font-size:13px;line-height:1.7;{_FONT}">
{_esc(from_name)} — <a href="https://cloudless.gr" style="color:{_TEAL};text-decoration:none;">cloudless.gr</a>
</td></tr>
{footer_extra}
</table>
</td></tr></table>
</body></html>"""
