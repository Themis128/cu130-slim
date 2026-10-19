"""The CI coverage gate script must enforce lines and branches separately."""

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_coverage_gates",
    Path(__file__).resolve().parents[2] / "scripts" / "check_coverage_gates.py",
)
gates = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gates)


def _totals(lines=(998, 1000), branches=(96, 100)):
    return {
        "covered_lines": lines[0],
        "num_statements": lines[1],
        "covered_branches": branches[0],
        "num_branches": branches[1],
    }


def test_passes_when_both_meet_gate():
    ok, msg = gates.evaluate(_totals(), 99.8, 96.0)
    assert ok
    assert "line coverage 99.80%" in msg and "branch coverage 96.00%" in msg


def test_branch_drop_fails_even_if_lines_are_high():
    ok, msg = gates.evaluate(_totals(lines=(1000, 1000), branches=(95, 100)), 99.8, 96.0)
    assert not ok and msg.endswith("FAIL")


def test_line_drop_fails_even_if_branches_are_high():
    ok, _ = gates.evaluate(_totals(lines=(997, 1000), branches=(100, 100)), 99.8, 96.0)
    assert not ok


def test_missing_branch_data_fails_loudly():
    ok, msg = gates.evaluate(_totals(branches=(0, 0)), 0, 0)
    assert not ok and "--cov-branch" in msg


def test_main_exit_codes(tmp_path, capsys):
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"totals": _totals()}))
    assert gates.main([str(report), "--lines", "99.8", "--branches", "96"]) == 0
    assert gates.main([str(report), "--lines", "99.9", "--branches", "96"]) == 1
    assert "FAIL" in capsys.readouterr().out
