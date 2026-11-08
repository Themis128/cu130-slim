"""Coverage for content_sanitizer — demojize/dedupe/quality/slop/voice."""

import app.services.content_sanitizer as CS


def test_demojize_and_collapse():
    assert CS._demojize("hi :nonexistent_code: there") == "hi there"


def test_dedupe_connectives():
    out = CS.sanitize_post_text("Furthermore, x is true. Furthermore, y follows. Also, z.")
    assert out.count("Furthermore") == 1 and "Also, z." in out


def test_quality_gate_paths():
    assert CS.content_quality_gate("   ", []) == (False, "empty")
    assert CS.content_quality_gate("ab", []) == (True, "ok")
    base = "the quick brown fox jumps over the lazy dog repeatedly"
    assert CS.content_quality_gate(base, ["totally unrelated text"]) == (True, "ok")
    ok, reason = CS.content_quality_gate(base, [base])
    assert ok is False and "near-duplicate" in reason


def test_slop_and_voice():
    hits = CS.slop_scan("in today's landscape we delve into synergy")
    assert "delve" in hits and "synergy" in hits and any(h.startswith("opener:") for h in hits)
    ok, reason = CS.voice_check("delve synergy leverage paradigm unlock")
    assert ok is False and reason.startswith("slop:")
    long_s = " ".join(["word"] * 40) + "."
    ok, reason = CS.voice_check(long_s)
    assert ok is False and "run-on" in reason
    assert CS.voice_check("short and clean.") == (True, "ok")
