"""Tests for app/scripts/instagram_settings_checklist.py — bridge HTTP + checklist CLI."""
from __future__ import annotations

import io
import json
import sys
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

import app.scripts.instagram_settings_checklist as sc


class _RespCM:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


def _urlopen_ok(payload):
    def _open(req, timeout=0):
        return _RespCM(payload)
    return _open


def test_get_json_ok_and_error(monkeypatch):
    monkeypatch.setattr(sc, "urlopen", _urlopen_ok({"a": 1}))
    assert sc._get_json("/x") == {"a": 1}

    def raise_http(req, timeout=0):
        raise HTTPError("u", 500, "err", {}, io.BytesIO(b"b"))
    monkeypatch.setattr(sc, "urlopen", raise_http)
    assert "error" in sc._get_json("/x")

    def raise_url(req, timeout=0):
        raise URLError("down")
    monkeypatch.setattr(sc, "urlopen", raise_url)
    assert "error" in sc._get_json("/x")


def test_evaluate_js_ok_and_error(monkeypatch):
    captured = {}

    def _open(req, timeout=0):
        captured["body"] = json.loads(req.data.decode())
        return _RespCM({"result": "LOGGED_IN"})

    monkeypatch.setattr(sc, "urlopen", _open)
    assert sc._evaluate_js("1+1") == {"result": "LOGGED_IN"}
    assert captured["body"]["expression"] == "1+1"

    def raise_http(req, timeout=0):
        raise HTTPError("u", 400, "err", {}, io.BytesIO(b"b"))
    monkeypatch.setattr(sc, "urlopen", raise_http)
    assert "error" in sc._evaluate_js("x")


def test_check_current_settings_no_session(monkeypatch):
    seq = iter([
        {"status": "done"},            # /session/navigate
        {"status": "waiting_for_login"}  # /session/status — "waiting" in status
    ])
    monkeypatch.setattr(sc, "urlopen", lambda req, timeout=0: _RespCM(next(seq)))
    out = sc.check_current_settings()
    assert "error" in out and "No active browser session" in out["error"]


def test_check_current_settings_not_logged_in(monkeypatch):
    seq = iter([
        {"status": "done"},  # navigate
        {"status": "done"},  # status
    ])
    monkeypatch.setattr(sc, "urlopen", lambda req, timeout=0: _RespCM(next(seq)))
    monkeypatch.setattr(sc, "_evaluate_js", lambda e: {"result": "NOT_LOGGED_IN"})
    out = sc.check_current_settings()
    assert "Not logged in" in out["error"]


def test_check_current_settings_logged_in(monkeypatch):
    seq = iter([
        {"status": "done"},     # navigate
        {"status": "done"},     # status
        {"biography": "bio"},   # /profile/instagram
    ])
    monkeypatch.setattr(sc, "urlopen", lambda req, timeout=0: _RespCM(next(seq)))
    monkeypatch.setattr(sc, "_evaluate_js", lambda e: {"result": "LOGGED_IN"})
    out = sc.check_current_settings()
    assert out["profile"] == {"biography": "bio"}
    assert out["session"] == {"status": "done"}


def test_print_checklist(capsys):
    sc.print_checklist()
    out = capsys.readouterr().out
    assert "Instagram Personal Account Settings Checklist" in out
    # every category rendered
    for item in sc.RECOMMENDED_SETTINGS:
        assert item["setting"] in out


def _run(argv):
    with patch.object(sys, "argv", ["prog"] + argv):
        sc.main()


def test_main_list(capsys):
    _run(["--list"])
    assert "Checklist" in capsys.readouterr().out


def test_main_check(monkeypatch, capsys):
    monkeypatch.setattr(sc, "check_current_settings", lambda: {"profile": {}})
    _run(["--check"])
    assert "profile" in capsys.readouterr().out


def test_main_check_setting_found_and_missing(capsys):
    _run(["--check-setting", "two-factor"])
    out = capsys.readouterr().out
    assert "setting" in out.lower() or "Two-Factor" in out

    with pytest.raises(SystemExit):
        _run(["--check-setting", "nonexistent-xyz"])
    assert "not found" in capsys.readouterr().out


def test_main_default_prints_checklist(capsys):
    _run([])
    assert "Checklist" in capsys.readouterr().out
