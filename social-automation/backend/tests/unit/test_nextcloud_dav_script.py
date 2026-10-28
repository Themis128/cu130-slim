"""Tests for the nextcloud-dav.py ops CLI (cloud.cloudless.gr WebDAV/OCS).

Loads the skill script by path — it lives outside the backend package.
Only seam needing a fake is _req (urllib); everything else is verified
against the real argument parsing, env precedence, path quoting, MKCOL
tolerance, MOVE Destination header, and OCS share form/URL extraction.
"""

from __future__ import annotations

import importlib.util
import os
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

_REL = ".devin/skills/omv-nextcloud-ops/nextcloud-integration/scripts/nextcloud-dav.py"


def _find_script() -> Path:
    if os.environ.get("NEXTCLOUD_DAV_SCRIPT"):
        return Path(os.environ["NEXTCLOUD_DAV_SCRIPT"])
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        cand = parent / _REL
        if cand.exists():
            return cand
    raise FileNotFoundError(f"nextcloud-dav.py not found above {here}")


try:
    SCRIPT = _find_script()
except FileNotFoundError:
    pytest.skip(
        "nextcloud-dav.py lives outside the backend tree — mount the repo root",
        allow_module_level=True,
    )

spec = importlib.util.spec_from_file_location("nextcloud_dav", SCRIPT)
assert spec is not None and spec.loader is not None
dav = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dav)

DAV_URL = "https://cloud.cloudless.gr/remote.php/dav/files/tester"


@pytest.fixture()
def env_creds(monkeypatch):
    monkeypatch.setenv("NEXTCLOUD_DAV_URL", DAV_URL)
    monkeypatch.setenv("NEXTCLOUD_USERNAME", "u")
    monkeypatch.setenv("NEXTCLOUD_APP_PASSWORD", "pw")


def _args(**kw):
    base = dict(dav_url=None, user=None, password=None, verbose=False, label=None, expire=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_q_quoting():
    assert dav._q("a b/c#d.png") == "a%20b/c%23d.png"
    assert dav._q("") == ""


def test_creds_precedence(env_creds, monkeypatch):
    url, user, pw = dav._creds(_args())
    assert url == DAV_URL and user == "u" and pw == "pw"
    # flags beat env
    url2, user2, _ = dav._creds(_args(dav_url=DAV_URL + "/", user="flag"))
    assert url2 == DAV_URL and user2 == "flag"


def test_creds_missing_exits(monkeypatch):
    monkeypatch.delenv("NEXTCLOUD_DAV_URL", raising=False)
    monkeypatch.delenv("NEXTCLOUD_USERNAME", raising=False)
    monkeypatch.delenv("NEXTCLOUD_APP_PASSWORD", raising=False)
    monkeypatch.setattr(dav, "_load_env_file", lambda: {})
    with pytest.raises(SystemExit):
        dav._creds(_args())


def test_req_sends_basic_auth(monkeypatch):
    captured = {}

    class _R:
        status = 200

        def read(self):
            return b"body"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(req, timeout):
        captured["auth"] = req.headers["Authorization"]
        return _R()

    monkeypatch.setattr(dav.urllib.request, "urlopen", _urlopen)
    code, body = dav._req("http://x", "GET", "u", "pw")
    assert code == 200 and body == b"body"
    assert captured["auth"].startswith("Basic ")


def test_req_http_error_returns_code(monkeypatch):
    def _urlopen(req, timeout):
        raise dav.urllib.error.HTTPError("u", 404, "nf", {}, None)

    monkeypatch.setattr(dav.urllib.request, "urlopen", _urlopen)
    code, _ = dav._req("http://x", "GET", "u", "pw")
    assert code == 404


def test_mkdirs_tolerates_405(monkeypatch, env_creds):
    calls = []

    def fake_req(url, method, user, pw, **kw):
        calls.append((method, url))
        return (405 if url.endswith("/a") else 201), b""

    monkeypatch.setattr(dav, "_req", fake_req)
    dav._mkdirs(DAV_URL, "u", "pw", "a/b/c")
    assert [c[0] for c in calls] == ["MKCOL"] * 3


def test_mkdirs_fatal_status_exits(monkeypatch, env_creds):
    monkeypatch.setattr(dav, "_req", lambda *a, **k: (500, b""))
    with pytest.raises(SystemExit):
        dav._mkdirs(DAV_URL, "u", "pw", "a")


PROPFIND_BODY = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
 <d:response>
  <d:href>/remote.php/dav/files/tester/SocialAuto/</d:href>
  <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype>
   <d:getcontentlength/></d:prop></d:propstat>
 </d:response>
 <d:response>
  <d:href>/remote.php/dav/files/tester/SocialAuto/pic%20one.png</d:href>
  <d:propstat><d:prop><d:resourcetype/>
   <d:getcontentlength>1234</d:getcontentlength></d:prop></d:propstat>
 </d:response>
</d:multistatus>"""


def test_cmd_list_parses_207(monkeypatch, env_creds, capsys):
    monkeypatch.setattr(dav, "_req", lambda *a, **k: (207, PROPFIND_BODY))
    dav.cmd_list(_args(dir="SocialAuto"))
    out = capsys.readouterr().out
    assert "dir " in out and "file" in out and "pic one.png" in out


def test_cmd_list_non207_exits(monkeypatch, env_creds):
    monkeypatch.setattr(dav, "_req", lambda *a, **k: (404, b"nf"))
    with pytest.raises(SystemExit):
        dav.cmd_list(_args(dir="x"))


def test_cmd_upload_and_download(monkeypatch, env_creds, tmp_path, capsys):
    local = tmp_path / "f.png"
    local.write_bytes(b"data")
    reqs = []

    def fake_req(url, method, user, pw, **kw):
        reqs.append((method, url, kw.get("data")))
        return (200 if method == "GET" else 201), b"file-bytes"

    monkeypatch.setattr(dav, "_req", fake_req)
    dav.cmd_upload(_args(local=str(local), remote="dir/sub/f.png"))
    assert reqs[-1][0] == "PUT" and reqs[-1][1].endswith("/dir/sub/f.png")
    assert "uploaded 4 bytes" in capsys.readouterr().out

    dest = tmp_path / "out.png"
    dav.cmd_download(_args(remote="dir/sub/f.png", local=str(dest)))
    assert dest.read_bytes() == b"file-bytes"
    assert reqs[-1][0] == "GET"


def test_cmd_move_destination_header(monkeypatch, env_creds, capsys):
    reqs = []

    def fake_req(url, method, user, pw, **kw):
        reqs.append((method, url, kw.get("headers")))
        return (201 if method == "MKCOL" else 204), b""

    monkeypatch.setattr(dav, "_req", fake_req)
    dav.cmd_move(_args(src="a/f.png", dest="b/f.png"))
    move = next(r for r in reqs if r[0] == "MOVE")
    assert move[2]["Destination"] == f"{DAV_URL}/b/f.png"
    assert "moved" in capsys.readouterr().out


def test_cmd_delete(monkeypatch, env_creds, capsys):
    monkeypatch.setattr(dav, "_req", lambda *a, **k: (204, b""))
    dav.cmd_delete(_args(remote="old.png"))
    assert "deleted" in capsys.readouterr().out
    monkeypatch.setattr(dav, "_req", lambda *a, **k: (404, b""))
    with pytest.raises(SystemExit):
        dav.cmd_delete(_args(remote="x"))


def test_cmd_share_form_and_url(monkeypatch, env_creds, capsys):
    seen = {}
    body = b"<ocs><data><url>https://cloud.cloudless.gr/index.php/s/abc</url><token>abc</token></data></ocs>"

    def fake_req(url, method, user, pw, **kw):
        seen["url"] = url
        seen["headers"] = kw.get("headers")
        seen["form"] = urllib.parse.parse_qs((kw.get("data") or b"").decode())
        return 200, body

    monkeypatch.setattr(dav, "_req", fake_req)
    dav.cmd_share(_args(remote="SocialAuto/media/x.png", password="pw2", label="L", expire="2026-12-31", verbose=True))
    assert seen["url"].endswith("/ocs/v2.php/apps/files_sharing/api/v1/shares")
    assert seen["form"]["path"] == ["/SocialAuto/media/x.png"]
    assert seen["form"]["shareType"] == ["3"]
    assert seen["form"]["permissions"] == ["1"]
    assert seen["form"]["expireDate"] == ["2026-12-31"]
    out = capsys.readouterr()
    assert "index.php/s/abc" in out.out
    assert "token: abc" in out.err


def test_cmd_share_no_url_exits(monkeypatch, env_creds):
    monkeypatch.setattr(dav, "_req", lambda *a, **k: (200, b"<ocs/>"))
    with pytest.raises(SystemExit):
        dav.cmd_share(_args(remote="x.png"))


def test_cmd_mkdir_and_upload_failures(monkeypatch, env_creds):
    monkeypatch.setattr(dav, "_req", lambda *a, **k: (201, b""))
    dav.cmd_mkdir(_args(dir="new/dir"))
    monkeypatch.setattr(dav, "_req", lambda *a, **k: (500, b""))
    with pytest.raises(SystemExit):
        dav.cmd_mkdir(_args(dir="new/dir"))
