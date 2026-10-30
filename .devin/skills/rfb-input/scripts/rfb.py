"""RFB input injection into the engagement-browser Xvfb (:99, x11vnc :5900).

Sends real X-level input — trusted events, indistinguishable from a physical
mouse/keyboard. Bypasses JS/synthetic-input detection that ignores element
clicks and keyboard events (TikTok OTP fields, X OAuth consent, etc.).

Usage:
  rfb.py click X Y          pointer move + left click at screen coords
  rfb.py type TEXT          type ASCII text (each char as KeyEvent)
  rfb.py key KEYSYM         press one key (e.g. 0xff0d = Return)

Run from the HOST — copies itself into the container and executes there.
Requires the engagement-browser container running with x11vnc on :5900.
"""
import base64
import shlex
import subprocess
import sys

PAYLOAD = r'''
import socket, struct, sys
mode = sys.argv[1]
s = socket.create_connection(("localhost", 5900), 10)
s.recv(12); s.sendall(b"RFB 003.008\n")
sec = s.recv(16); s.sendall(bytes([sec[1]])); s.recv(4)
s.sendall(bytes([1]))
hdr = s.recv(24); nl = struct.unpack(">I", hdr[20:24])[0]; s.recv(nl)
def ptr(mask, x, y): s.sendall(struct.pack(">BBHH", 5, mask, x, y))
def key(flag, k): s.sendall(struct.pack(">BBxxI", 4, flag, k))
if mode == "click":
    x, y = int(sys.argv[2]), int(sys.argv[3])
    ptr(0, x, y); ptr(1, x, y); ptr(0, x, y)
elif mode == "type":
    for ch in sys.argv[2]:
        key(1, ord(ch)); key(0, ord(ch))
elif mode == "key":
    key(1, int(sys.argv[2], 0)); key(0, int(sys.argv[2], 0))
s.close()
'''

def main():
    if sys.argv[1] == "click":
        args = f"click {sys.argv[2]} {sys.argv[3]}"
    elif sys.argv[1] == "type":
        args = f"type {sys.argv[2]}"
    else:
        args = f"key {sys.argv[2]}"
    b64 = base64.b64encode(PAYLOAD.encode()).decode()
    r = subprocess.run(
        ["docker", "exec", "engagement-browser", "sh", "-c",
         "echo %s | base64 -d | python3 - %s" % (b64, shlex.quote(args))],
        capture_output=True, text=True, timeout=30)
    print(r.stdout.strip() or "done", r.stderr.strip()[:200])

main()
