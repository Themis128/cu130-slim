"""Shared branded funnel email layout (app/services/funnel_email.py)."""

from __future__ import annotations

import re

import pytest

from app.services import funnel_email as fe
from app.services.funnel_email import Cta

URL = "https://cloudless.gr/en/consultation"


def _body(html: str) -> str:
    return html.split('line-height:1.6;font-family:Arial,Helvetica,sans-serif;">', 1)[1].split("</td>", 1)[0]


def test_layout_is_email_safe_and_matches_reference_shell():
    html = fe.render_funnel_html(f"Hi Ana,\n\nslots here: {URL}\n\n— Themistoklis, Cloudless", subject="S", cta_label="See slots")
    assert html.startswith("<!DOCTYPE html>")
    assert 'width="600"' in html and "max-width:600px" in html
    assert 'role="presentation"' in html
    assert "<script" not in html.lower() and "<style" not in html.lower() and "class=" not in html
    assert "background-color:#f4f6f8" in html and "background-color:#2563eb" in html
    assert ">Cloudless</span>" in html
    assert "— Themistoklis<br><span" in html and ">Cloudless</span></p>" in html
    assert f'Button not working? Copy this link: <a href="{URL}"' in html
    assert html.count(f'href="{URL}"') == 2  # button + footer fallback
    assert ">See slots</a>" in html


def test_main_link_becomes_button_in_place_and_tail_kept():
    inner = fe.text_to_html(f"Book here: {URL}. Thanks!", ctas=[Cta("Book", URL)])
    blocks = inner.split("\n")
    assert blocks[0] == '<p style="margin:0 0 16px;">Book here:</p>'
    assert ">Book</a>" in blocks[1]
    assert blocks[2] == '<p style="margin:0 0 16px;">Thanks!</p>'


def test_second_cta_is_outlined_and_missing_cta_goes_above_signature():
    inner = fe.text_to_html(
        "Get it: https://a.test/x\n\n— Me",
        ctas=[Cta("One", "https://a.test/x"), Cta("Two", "https://b.test/y")],
    )
    assert "background-color:#2563eb" in inner.split(">One<")[0]
    two = inner.split(">One<")[1]
    assert "border:2px solid #2563eb" in two and ">Two</a>" in two
    assert inner.index(">Two</a>") < inner.index("— Me")


def test_missing_cta_appended_at_end_without_signature():
    inner = fe.text_to_html("No link here.", ctas=[Cta("Go", "https://a.test")])
    assert inner.rstrip().endswith("</table>") and ">Go</a>" in inner


def test_no_link_means_no_button_and_brand_footer():
    html = fe.render_funnel_html("Hi,\n\nreply to this mail.", subject="S", cta_label="Ignored")
    assert "Button not working" not in html
    assert "padding:14px 28px" not in html
    assert "cloudless.gr</a>" in html


def test_user_values_escaped_and_merge_tokens_preserved():
    text = 'Hi <script>alert(1)</script> "Bob" & co,\n\n{contactfield=firstname} {{first_name}} **bold**'
    html = fe.render_funnel_html(text, subject="<b>x</b>")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "&quot;Bob&quot; &amp; co" in html
    assert "{contactfield=firstname} {{first_name}}" in html
    assert "<strong>bold</strong>" in html
    assert "<title>&lt;b&gt;x&lt;/b&gt;</title>" in html


def test_urls_with_quotes_cannot_break_attributes():
    html = fe.render_funnel_html("x", subject="s", ctas=[Cta('Go "now"', 'https://x.test/a"b<c')])
    assert 'href="https://x.test/a&quot;b&lt;c"' in html
    assert "Go &quot;now&quot;" in html
    assert 'a"b<c' not in html


def test_inline_links_markdown_links_and_trailing_punctuation():
    inner = fe.text_to_html("See https://a.test/p, or [docs](https://d.test/x).")
    assert '<a href="https://a.test/p"' in inner and "</a>, or" in inner
    assert '<a href="https://d.test/x"' in inner and ">docs</a>." in inner


def test_lists_headings_dividers_and_soft_wraps():
    inner = fe.text_to_html("# Plan\nline one\nline two\n- a\n  - nested\n1. first\n\n---\n\n## Next\n\nafter")
    assert '<strong style="color:#0f172a;">Plan</strong>' in inner
    assert ">line one line two</p>" in inner
    assert "•</td>" in inner and "padding:0 0 8px 24px" in inner
    assert "1.</td>" in inner
    assert "border-top:1px solid #e5e7eb" in inner
    assert ">Next</strong>" in inner and ">after</p>" in inner


def test_heading_line_after_list_inside_paragraph():
    inner = fe.text_to_html("- a\n## H\ntext")
    assert inner.index("•") < inner.index(">H</strong>") < inner.index(">text</p>")


def test_highlight_phrase_bolded_once():
    inner = fe.text_to_html("free tech checkup and free tech checkup", highlight=("free tech checkup", ""))
    assert inner.count("<strong>free tech checkup</strong>") == 1


def test_long_dash_line_is_not_signature():
    inner = fe.text_to_html("— " + "x" * 100)
    assert "<br><span" not in inner and "— " + "x" * 100 in inner


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hi Ana,\n\nThe real point here https://x.test is this.\n\n— Me", "The real point here is this."),
        ("Hi,\n\n— Me", ""),
        ("Hello there, this is a long greeting line that goes beyond forty chars", "Hello there, this is a long greeting line that goes beyond forty chars"),
        ("**" + "y" * 200, "y" * 119 + "…"),
    ],
)
def test_default_preheader(text, expected):
    assert fe.default_preheader(text) == expected


def test_preheader_rendered_hidden_and_overridable():
    html = fe.render_funnel_html("Hi,\n\nBody copy.", subject="s", preheader="Peek <x>")
    assert "display:none" in html and "Peek &lt;x&gt;" in html
    assert "display:none" not in fe.render_funnel_html("Hi,", subject="s")


def test_first_url():
    assert fe.first_url("a https://x.test/p. b https://y.test") == "https://x.test/p"
    assert fe.first_url("none") is None


def test_inline_fragment_buttons_and_styles():
    out, url = fe.inline_fragment(
        '<h1>T</h1><p>x <a href="https://l.test">l</a></p><ul><li>a</li></ul><ol><li>b</li></ol><code>c</code>'
        '<a href="https://a.test/?a=1&amp;b=2" class="btn">Go &amp; see</a><a href="https://b.test" class="btn">Two</a>'
    )
    assert url == "https://a.test/?a=1&b=2"
    assert 'href="https://a.test/?a=1&amp;b=2"' in out and ">Go &amp; see</a>" in out
    assert "border:2px solid" in out.split(">Go &amp; see<")[1]
    assert not re.search(r"<(p|h1|ul|ol|li|code)>", out)
    assert '<a style="color:#2563eb;text-decoration:underline;" href="https://l.test">' in out
    assert fe.inline_fragment("<p>no button</p>")[1] is None


def test_render_layout_footer_note_with_fallback():
    html = fe.render_layout("<p>x</p>", title="t", fallback_url="https://a.test", footer_note="Note  at\nhttps://n.test")
    foot = html.split("Button not working?")[1]
    assert "<br><br>Note at " in foot and 'href="https://n.test"' in foot


def test_body_helper_extracts_card():
    assert "Hi" in _body(fe.render_funnel_html("Hi", subject="s"))


def test_repeated_cta_url_and_url_first_and_extra_blank_lines():
    inner = fe.text_to_html(f"{URL}\n\n\n\nagain {URL}", ctas=[Cta("Book", URL)])
    assert inner.count(">Book</a>") == 1
    assert inner.startswith("<table")
    assert f'again <a href="{URL}"' in inner


# ── funnel call sites render through the shared layout ─────────────────────


def test_every_quiet_and_nurture_step_with_a_link_gets_its_button():
    from app.services.nurture_sequence import QUIET_STEPS, STEPS

    for step in (*STEPS, *QUIET_STEPS):
        body = step["body"].format(name="Ana", company="Acme")
        url = fe.first_url(body)
        html = fe.render_funnel_html(body, subject="s", cta_label=step.get("cta"))
        if url:
            assert step.get("cta") and f">{step['cta']}</a>" in html, step["key"]
        else:
            assert "Button not working" not in html


def test_playbook_html_uses_shared_layout_with_two_ctas():
    from app.services import playbook_email as pb

    _, text, html = pb.build_playbook_email("https://x.test/p.pdf", name="<b>Eve</b> Smith")
    assert "max-width:600px" in html and 'role="presentation"' in html
    assert ">Download the playbook (PDF)</a>" in html and ">Book the free audit</a>" in html
    assert "border:2px solid" in html.split(">Download the playbook (PDF)<")[1]
    assert "&lt;b&gt;Eve&lt;/b&gt;" in html and "<b>Eve" not in html
    assert "— Themistoklis Baltzakis" in html and "You received this" in html.split("Button not working?")[1]
    assert "You received this" in text


def test_proposal_followup_bump_cta_label():
    from types import SimpleNamespace

    from app.services import proposal_followup as pf

    steps = pf._steps(SimpleNamespace(subject="Plan", recipient_name="Ana", services=[], price_low_eur=None, price_high_eur=None))
    bump = next(s for s in steps if s["key"] == "bump")
    html = fe.render_funnel_html(bump["body"], subject=bump["subject"], cta_label=bump["cta"])
    assert ">Grab a free 30-minute slot</a>" in html


def test_mautic_winback_bodies_are_branded_and_keep_tokens():
    import importlib
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    setup = importlib.import_module("mautic_winback_setup")
    bodies = setup.build_bodies()
    assert len(bodies) == 3
    for (text, html), label in zip(bodies, ("Book the audit", "Book the audit", "Grab a slot"), strict=True):
        assert "{contactfield=firstname}" in text and "{contactfield=firstname}" in html
        assert f">{label}</a>" in html and "max-width:600px" in html
        assert "— Cloudless" in html


@pytest.mark.asyncio
async def test_billing_owner_mail_is_branded(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from app.api import billing

    owner = MagicMock(email="o@x.test")
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=owner)))
    send = AsyncMock()
    monkeypatch.setattr("app.services.email_digest.send_email", send)
    await billing._notify_team_owner(db, MagicMock(owner_id=1, id=2), "SocialAuto payment failed", "Your payment failed.", "tpl")
    kw = send.await_args.kwargs
    assert kw["text_body"] == "Your payment failed." and kw["to_addrs"] == ["o@x.test"]
    assert ">SocialAuto</span>" in kw["html_body"] and "Your payment failed." in kw["html_body"]
