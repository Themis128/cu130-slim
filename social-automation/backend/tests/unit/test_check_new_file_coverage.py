"""The PR-added-file coverage gate must name files below the floor."""

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_new_file_coverage",
    Path(__file__).resolve().parents[2] / "scripts" / "check_new_file_coverage.py",
)
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)


def _files(entries):
    """entries: {path: (covered_lines, num_statements)}"""
    return {
        path: {"summary": {"covered_lines": c, "num_statements": s}}
        for path, (c, s) in entries.items()
    }


def test_all_targets_meet_floor():
    files = _files({"app/services/x.py": (90, 100)})
    ok, failures, checked = gate.evaluate(files, ["app/services/x.py"], 80.0)
    assert ok and not failures and checked == ["app/services/x.py"]


def test_low_coverage_file_fails_and_is_named():
    files = _files({"app/services/x.py": (10, 100), "app/api/y.py": (95, 100)})
    ok, failures, _ = gate.evaluate(files, ["app/services/x.py", "app/api/y.py"], 80.0)
    assert not ok
    assert failures == ["app/services/x.py: 10.0% (gate 80.0%)"]


def test_file_missing_from_report_counts_as_zero():
    ok, failures, _ = gate.evaluate({}, ["app/new_module.py"], 80.0)
    assert not ok and "app/new_module.py: 0.0%" in failures[0]


def test_empty_statement_file_is_fully_covered():
    files = _files({"app/hub.py": (0, 0)})
    ok, _, _ = gate.evaluate(files, ["app/hub.py"], 80.0)
    assert ok


def test_main_filters_prefix_and_writes_report(tmp_path, capsys):
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"files": _files({"app/services/x.py": (50, 100)})}))
    listing = tmp_path / "files.txt"
    listing.write_text(
        "social-automation/backend/app/services/x.py\n"
        "social-automation/backend/tests/unit/test_x.py\n"
        "docs/readme.md\n"
        "\n"
    )
    assert gate.main([str(report), str(listing), "--min-lines", "80"]) == 1
    err = capsys.readouterr().err
    assert "app/services/x.py: 50.0%" in err
    assert "test_x" not in err

    listing.write_text("social-automation/backend/app/services/x.py\n")
    report.write_text(json.dumps({"files": _files({"app/services/x.py": (95, 100)})}))
    assert gate.main([str(report), str(listing), "--min-lines", "80"]) == 0
    assert "new-file coverage gate OK (1 added" in capsys.readouterr().out
