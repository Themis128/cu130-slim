"""Tests for app/scripts/instagram_bio_generator.py — Unicode fonts, bio styles, CLI."""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

import app.scripts.instagram_bio_generator as gen


def test_to_sans_bold():
    out = gen.to_sans_bold("Cloudless Az09!")
    # letters mapped into Mathematical Sans-Serif Bold block
    assert out != "Cloudless Az09!"
    assert "C" not in out
    # digits and non-letters pass through unchanged
    assert out.endswith("!")
    # a -> 𝗮 (0x1D5EE)
    assert chr(0x1D5EE) in gen.to_sans_bold("a")
    # A -> 𝗔
    assert chr(0x1D5D6) in gen.to_sans_bold("A")


def test_to_italic():
    out = gen.to_italic("abcA")
    assert chr(0x1D44E) in out  # 𝑎
    assert chr(0x1D434) in out  # 𝐴
    assert "b" not in out and "A" not in out
    # non-letters pass through
    assert gen.to_italic("!1") == "!1"


def test_to_small_caps():
    out = gen.to_small_caps("cloud")
    assert "ᴄ" in out and "ʟ" in out
    # non-mapped chars pass through
    assert gen.to_small_caps("!") == "!"


@pytest.mark.parametrize("style", list(gen.STYLES))
def test_generate_bio_all_styles(style):
    bio = gen.generate_bio(
        name="Cloudless", title="Founder @ ",
        skills="Azure · AWS", experience="15+ yrs",
        location="Athens", links="cloudless.gr", style=style,
    )
    assert "cloudless.gr" in bio
    assert len(bio) <= 150


def test_generate_bio_unknown_style():
    with pytest.raises(ValueError, match="Unknown style"):
        gen.generate_bio("n", "t", "s", "e", "l", "x", style="nope")


def test_generate_bio_location_arrow_variants():
    # no arrow -> appends "→ Worldwide"
    bio = gen.generate_bio("N", "T", "S", "E", "Athens", "x", style="clean-arrows")
    assert "Athens → Worldwide" in bio
    # already has arrow -> unchanged
    bio2 = gen.generate_bio("N", "T", "S", "E", "Athens → GR", "x", style="clean-arrows")
    assert "Athens → GR" in bio2 and "Worldwide" not in bio2


def test_generate_bio_over_limit_shortenings(capsys):
    long_loc = "A" * 60 + " → Worldwide"
    bio = gen.generate_bio(
        name="N" * 40, title="T", skills="S" * 60,
        experience="building systems and more", location=long_loc,
        links="a | b", style="clean-arrows",
    )
    # exercises the shortening chain; may still warn if >150
    assert isinstance(bio, str)
    # force the warning path with fields that can't be shortened enough
    gen.generate_bio(
        name="N" * 100, title="T" * 30, skills="S" * 100,
        experience="E" * 100, location="L" * 60, links="x", style="minimal-bullets",
    )
    err = capsys.readouterr().err
    assert "limit 150" in err


def test_check_bio():
    out = gen.check_bio("🚀 Founder @ Cloudless\n☁️ Azure AWS architect")
    assert out["length"] == len("🚀 Founder @ Cloudless\n☁️ Azure AWS architect")
    assert out["lines"] == 2
    assert "cloudless" in out["seo_keywords"]
    assert "founder" in out["seo_keywords"]
    assert out["over_limit"] is False
    # plain_english is available in-container -> nlp_issues is a list
    assert isinstance(out["nlp_issues"], list)


def test_check_bio_nlp_import_error(monkeypatch):
    # plain_english unavailable (outside the app container) -> nlp_issues []
    monkeypatch.setitem(sys.modules, "app.services.plain_english", None)
    out = gen.check_bio("Founder @ Cloudless")
    assert out["nlp_issues"] == []


def test_check_bio_over_limit_and_no_keywords():
    out = gen.check_bio("x" * 200)
    assert out["over_limit"] is True
    assert out["seo_keywords"] == []


def _run(argv):
    with patch.object(sys, "argv", ["prog"] + argv):
        gen.main()


def test_main_list_styles(capsys):
    _run(["--list-styles"])
    out = capsys.readouterr().out
    assert "bold-brand" in out


def test_main_check(capsys):
    _run(["--check", "Founder @ Cloudless"])
    out = capsys.readouterr().out
    assert "Length:" in out and "SEO keywords" in out


def test_main_check_with_nlp_issues(capsys):
    _run(["--check", "We leverage synergy and robust seamless cutting-edge solutions to empower stakeholders"])
    out = capsys.readouterr().out
    assert "NLP issues:" in out or "NLP: Clean" in out


def test_main_linkedin_about(capsys):
    _run(["--linkedin-about", "We are Cloudless, cloud architects"])
    out = capsys.readouterr().out
    assert "Length:" in out


def test_main_name_flow(capsys):
    _run(["--name", "Cloudless", "--skills", "Azure", "--experience", "15y",
          "--location", "Athens", "--links", "cloudless.gr"])
    out = capsys.readouterr().out
    assert "SEO keywords" in out


def test_main_help(capsys):
    _run([])
    out = capsys.readouterr().out
    assert "Generate stylish" in out or "usage:" in out
