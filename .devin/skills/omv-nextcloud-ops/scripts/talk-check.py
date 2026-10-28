#!/usr/bin/env python3
"""External Nextcloud Talk connectivity check (no omv access needed).

Probes, from wherever it runs:
  1. HPB signaling: GET https://signal.cloudless.gr/api/v1/welcome
  2. STUN: real RFC 5389 Binding Requests to the configured STUN servers
  3. Nextcloud status page reachability

Usage: talk-check.py [--json]
"""

import json
import socket
import ssl
import struct
import sys
import urllib.request

SIGNALING = "https://signal.cloudless.gr/api/v1/welcome"
STATUS = "https://cloud.cloudless.gr/status.php"
STUN_SERVERS = [
    ("stun.nextcloud.com", 443),
    ("stun.cloudflare.com", 3478),
]
TIMEOUT = 6
MAGIC = 0x2112A442


def http_probe(url: str) -> dict:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "talk-check/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return {"ok": r.status == 200, "status": r.status,
                    "body": r.read(300).decode(errors="replace")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def stun_probe(host: str, port: int) -> dict:
    """Send a Binding Request, parse XOR-MAPPED-ADDRESS from the response."""
    tid = bytes(range(1, 13))
    req = struct.pack("!HHI12s", 0x0001, 0, MAGIC, tid)
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)
        addr = infos[0][4]
    except OSError as e:
        return {"ok": False, "error": f"dns: {e}"}
    for use_tcp in (False, True):
        try:
            if use_tcp:
                s = socket.create_connection(addr, timeout=TIMEOUT)
                s.sendall(req)
                data = s.recv(2048)
            else:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(TIMEOUT)
                s.sendto(req, addr)
                data, _ = s.recvfrom(2048)
            s.close()
        except OSError:
            continue
        if len(data) < 20:
            continue
        mtype, mlen, magic = struct.unpack("!HHI", data[:8])
        if mtype != 0x0101 or magic != MAGIC:
            continue
        # Walk attributes for XOR-MAPPED-ADDRESS (0x0020)
        off = 20
        while off + 4 <= len(data):
            atype, alen = struct.unpack("!HH", data[off:off + 4])
            val = data[off + 4:off + 4 + alen]
            if atype == 0x0020 and len(val) >= 8 and val[1] == 0x01:
                xport, xip = struct.unpack("!HI", val[2:8])
                ip = ".".join(
                    str(b) for b in struct.pack("!I", xip ^ MAGIC))
                return {"ok": True, "transport": "tcp" if use_tcp else "udp",
                        "mapped_port": xport ^ (MAGIC >> 16), "mapped_ip": ip}
            off += 4 + alen + ((4 - alen % 4) % 4)
        return {"ok": True, "transport": "tcp" if use_tcp else "udp",
                "note": "binding response without XOR-MAPPED-ADDRESS"}
    return {"ok": False, "error": "no binding response (udp+tcp)"}


def main() -> None:
    result = {
        "signaling": http_probe(SIGNALING),
        "nextcloud_status": http_probe(STATUS),
        "stun": {f"{h}:{p}": stun_probe(h, p) for h, p in STUN_SERVERS},
        "turn_note": ("external TURN intentionally absent — LAN-only behind "
                      "CGNAT; needs ISP public IPv4 (see SKILL.md)"),
    }
    if "--json" in sys.argv:
        print(json.dumps(result, indent=2))
        return
    print("=== Talk connectivity ===")
    sig = result["signaling"]
    print(f"signaling  {'OK ' if sig['ok'] else 'FAIL'} "
          f"{sig.get('status') or sig.get('error')}")
    nc = result["nextcloud_status"]
    print(f"nextcloud  {'OK ' if nc['ok'] else 'FAIL'} "
          f"{nc.get('status') or nc.get('error')}")
    for name, r in result["stun"].items():
        extra = (f"mapped {r['mapped_ip']}:{r['mapped_port']} via {r['transport']}"
                 if r.get("mapped_ip") else r.get("error") or r.get("note"))
        print(f"stun {name:26} {'OK ' if r['ok'] else 'FAIL'} {extra}")
    print(f"turn       INFO {result['turn_note']}")


if __name__ == "__main__":
    main()
