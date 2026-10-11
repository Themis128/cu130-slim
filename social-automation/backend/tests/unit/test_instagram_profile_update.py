"""Tests for app/scripts/instagram_profile_update.py — bridge HTTP helpers + CLI."""
from __future__ import annotations

import io
import json
import sys
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

import app.scripts.instagram_profile_update as pu


class _RespCM:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


def _ok(payload):
    def _open(req, timeout=0):
        return _RespCM(payload)
    return _open


def _http_error(req, timeout=0):
    raise HTTPError("u", 500, "err", {}, io.BytesIO(b"body"))


def _url_error(req, timeout=0):
    raise URLError("down")


@pytest.mark.parametrize("helper,args", [
    (pu._post_json, ("/p", {"a": 1})),
    (pu._patch_json, ("/p", {"a": 1})),
    (pu._get_json, ("/p",)),
])
def test_http_helpers_ok(monkeypatch, helper, args):
    monkeypatch.setattr(pu, "urlopen", _ok({"ok": True}))
    assert helper(*args) == {"ok": True}


@pytest.mark.parametrize("helper,args", [
    (pu._post_json, ("/p", {})),
    (pu._patch_json, ("/p", {})),
    (pu._get_json, ("/p",)),
])
def test_http_helpers_http_error(monkeypatch, helper, args, capsys):
    monkeypatch.setattr(pu, "urlopen", _http_error)
    with pytest.raises(SystemExit):
        helper(*args)
    assert "ERROR 500" in capsys.readouterr().err


@pytest.mark.parametrize("helper,args", [
    (pu._post_json, ("/p", {})),
    (pu._patch_json, ("/p", {})),
    (pu._get_json, ("/p",)),
])
def test_http_helpers_url_error(monkeypatch, helper, args, capsys):
    monkeypatch.setattr(pu, "urlopen", _url_error)
    with pytest.raises(SystemExit):
        helper(*args)
    assert "Connection error" in capsys.readouterr().err


def test_check_session_and_read_profile(monkeypatch):
    monkeypatch.setattr(pu, "urlopen", _ok({"status": "done"}))
    assert pu.check_session() == {"status": "done"}
    assert pu.read_profile() == {"status": "done"}


def test_update_profile_payload_and_empty(monkeypatch, capsys):
    captured = {}

    def _open(req, timeout=0):
        captured["data"] = json.loads(req.data.decode())
        return _RespCM({"ok": 1})

    monkeypatch.setattr(pu, "urlopen", _open)
    out = pu.update_profile(bio="b", full_name="f", website="w")
    assert captured["data"] == {"biography": "b", "full_name": "f", "external_url": "w"}
    assert out == {"ok": 1}

    # partial payload
    pu.update_profile(bio="only")
    assert captured["data"] == {"biography": "only"}

    with pytest.raises(SystemExit):
        pu.update_profile()
    assert "No fields to update" in capsys.readouterr().out


def test_verify_bio(monkeypatch, capsys):
    monkeypatch.setattr(pu.time, "sleep", lambda s: None)
    monkeypatch.setattr(pu, "read_profile", lambda: {"biography": "🚀 Founder @ Cloudless"})
    assert pu.verify_bio("Founder") is True

    monkeypatch.setattr(pu, "read_profile", lambda: {"biography": "different"})
    assert pu.verify_bio("Founder") is False
    assert "verification failed" in capsys.readouterr().out


def _run(argv):
    with patch.object(sys, "argv", ["prog"] + argv):
        pu.main()


def test_main_check_session(monkeypatch, capsys):
    monkeypatch.setattr(pu, "check_session", lambda: {"status": "done"})
    _run(["--check-session"])
    assert "done" in capsys.readouterr().out


def test_main_verify_bio_exits(monkeypatch):
    monkeypatch.setattr(pu, "verify_bio", lambda e: True)
    with pytest.raises(SystemExit) as e1:
        _run(["--verify-bio", "x"])
    assert e1.value.code == 0
    monkeypatch.setattr(pu, "verify_bio", lambda e: False)
    with pytest.raises(SystemExit) as e2:
        _run(["--verify-bio", "x"])
    assert e2.value.code == 1


def test_main_read(monkeypatch, capsys):
    monkeypatch.setattr(pu, "read_profile", lambda: {"biography": "bio"})
    _run(["--read"])
    assert "bio" in capsys.readouterr().out


def test_main_update(monkeypatch):
    called = {}
    monkeypatch.setattr(pu, "update_profile", lambda **kw: called.update(kw))
    _run(["--bio", "new bio", "--website", "https://x.io"])
    assert called == {"bio": "new bio", "full_name": None, "website": "https://x.io"}


def test_main_help(capsys):
    _run(["--username", "u"])
    assert "usage:" in capsys.readouterr().out
