"""Deliberate CI-failure probe for the Gemini auto-fix workflow.

This test intentionally fails so the gemini-ci-fix workflow_run trigger
fires end-to-end. The auto-fix agent should repair or remove it; the PR
is closed unmerged afterwards regardless.
"""


def test_gemini_probe_placeholder():
    # Deliberate failure — automation probe, not a real assertion.
    assert 1 == 2, "gemini-ci-fix probe: intentional failure"
