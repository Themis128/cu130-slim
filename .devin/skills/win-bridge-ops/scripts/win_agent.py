#!/usr/bin/env python3
"""win_agent.py — stdlib-only HTTP agent on Windows, port 8765.

Run once: `python win_agent.py` (leave open). Lets WSL sidecars reach
Windows without interop: exec, launch debug Chrome, adb, screenshot.
"""
import http.server
import json
import os
import subprocess
import sys

PORT = int(os.environ.get("WIN_AGENT_PORT", "8765"))
TOKEN = os.environ.get("WIN_AGENT_TOKEN", "")
CHROME = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
ADB = [r"C:\Users\tbaltzakis\Downloads\platform-tools\adb.exe", "adb.exe"]


def _find(cands):
    for c in cands:
        if c == "adb.exe" or os.path.exists(c):
            return c
    return None


def _run(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, shell=isinstance(cmd, str))
        return {"rc": p.returncode, "out": (p.stdout + p.stderr)[:8000]}
    except Exception as e:
        return {"rc": -1, "out": f"{type(e).__name__}: {e}"}


class H(http.server.BaseHTTPRequestHandler):
    def _ok(self):
        h = self.headers.get("X-Agent-Token", "")
        if TOKEN and h != TOKEN:
            return False
        return True

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        if not self._ok():
            return self._json(403, {"ok": False})
        if self.path == "/health":
            return self._json(200, {"ok": True, "py": sys.version})
        if self.path == "/screenshot":
            try:
                from PIL import ImageGrab
                import io
                buf = io.BytesIO()
                ImageGrab.grab().save(buf, "PNG")
                b = buf.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            except Exception as e:
                self._json(500, {"ok": False, "err": str(e)})
            return
        self._json(404, {"ok": False})

    def do_POST(self):
        if not self._ok():
            return self._json(403, {"ok": False})
        b = self._body()
        if self.path == "/exec":
            return self._json(200, _run(b.get("cmd", ""), b.get("timeout", 60)))
        if self.path == "/launch-chrome":
            exe = _find(CHROME)
            if not exe:
                return self._json(500, {"ok": False, "err": "chrome not found"})
            subprocess.Popen([exe, "--remote-debugging-port=9222",
                              r"--user-data-dir=C:\tmp\chrome-debug"])
            return self._json(200, {"ok": True})
        if self.path == "/adb":
            adb = _find(ADB)
            if not adb:
                return self._json(500, {"ok": False, "err": "adb not found"})
            return self._json(200, _run([adb] + [str(a) for a in b.get("args", [])]))
        self._json(404, {"ok": False})


if __name__ == "__main__":
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), H)
    print(f"win-agent on :{PORT}")
    srv.serve_forever()
