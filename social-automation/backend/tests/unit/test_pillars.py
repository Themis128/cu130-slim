"""Unit tests for app.services.pillars — heuristic classification."""

import uuid
from types import SimpleNamespace

from app.services.pillars import classify_pillar


def _pillar(name: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), name=name, sort_order=0)


PILLARS = [_pillar("Proof"), _pillar("Testing"), _pillar("Perspective")]


def _name(result) -> str | None:
    return result.name if result else None


def test_testing_keywords_win():
    text = "Honest log: I tried the new pipeline this week — it broke twice, cost €40, results inside."
    assert _name(classify_pillar(text, PILLARS)) == "Testing"


def test_proof_keywords_win():
    text = "Here's how I approach free-tier CI: a step-by-step guide and the framework I use."
    assert _name(classify_pillar(text, PILLARS)) == "Proof"


def test_perspective_keywords_win():
    text = "You don't need AWS budgets. Most people overpay — visibility is a system, not motivation."
    assert _name(classify_pillar(text, PILLARS)) == "Perspective"


def test_no_match_returns_none():
    assert classify_pillar("a", PILLARS) is None
    assert classify_pillar("", PILLARS) is None
    assert classify_pillar(None, PILLARS) is None


def test_unknown_pillar_names_never_match():
    only_custom = [_pillar("Announcements")]
    assert classify_pillar("I tested this experiment", only_custom) is None


def test_case_insensitive_matching():
    assert _name(classify_pillar("I TRIED THIS AND IT BROKE", PILLARS)) == "Testing"
