"""stack-ops — idle-sleep + wake-on-connect for the SocialAuto compose stack.

Two jobs in one container:

1. TCP wake-proxy: listeners front idle-tolerant services. On connect the
   target is started (if stopped) and waited-healthy, then bytes are piped.
   Callers just repoint their base URL hostname to ``stack-ops`` — works for
   HTTP, WebSocket, SOCKS5, VNC, MCP streamable-http, anything TCP.
2. Idle sleeper: every POLL_SECONDS a managed container with no proxied
   activity for its ``idle_min`` AND CPU < CPU_PCT_MAX on two consecutive
   polls is ``docker stop``ed (restart: unless-stopped keeps it down).

Control API on :8787:
  GET  /healthz
  GET  /status            -> {name: {state, last_active, active_conns}}
  GET  /status/<name>
  POST /wake/<name>       -> start + wait healthy
  POST /sleep/<name>      -> stop now
  POST /keepawake/<name>?ttl=<sec>  -> suspend sleeping until epoch
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [stack-ops] %(message)s")
log = logging.getLogger("stack-ops")

API_PORT = int(os.environ.get("STACK_OPS_API_PORT", "8787"))
POLL_SECONDS = int(os.environ.get("STACK_OPS_POLL_SECONDS", "60"))
CPU_PCT_MAX = float(os.environ.get("STACK_OPS_CPU_MAX", "3.0"))
MIN_UPTIME_S = int(os.environ.get("STACK_OPS_MIN_UPTIME", "300"))
WAKE_TIMEOUT_S = int(os.environ.get("STACK_OPS_WAKE_TIMEOUT", "120"))
SLEEPER_ENABLED = os.environ.get("STACK_OPS_SLEEPER", "1") == "1"

CONF = json.loads((Path(__file__).parent / "services.json").read_text())
# name -> {"target": host:port, "idle_min": int, "wait_page": bool, "group": [names]}
SERVICES: dict[str, dict] = CONF["services"]
LISTENERS: dict[int, str] = CONF["listeners"]  # local_port -> service name

last_active: dict[str, float] = {}
active_conns: dict[str, int] = {}
wake_locks: dict[str, asyncio.Lock] = {}
keepawake: dict[str, float] = {}
low_cpu_streak: dict[str, int] = {}
started_at: dict[str, float] = {}


async def docker(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "docker", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace").strip()


async def container_state(name: str) -> str:
    """running | starting | stopped | missing"""
    rc, out = await docker(
        "inspect", "-f",
        "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.State.StartedAt}}",
        name,
    )
    if rc != 0:
        return "missing"
    status, health, started = (out.split("|") + [""] * 3)[:3]
    if status != "running":
        return "stopped"
    if health == "healthy" or health == "":
        return "running"
    return "starting"


async def cpu_pct(name: str) -> float:
    rc, out = await docker("stats", "--no-stream", "--format", "{{.CPUPerc}}", name)
    try:
        return float(out.rstrip("%"))
    except ValueError:
        return 99.0


async def wake(name: str) -> bool:
    """Start + wait healthy. Idempotent, deduped per service."""
    svc = SERVICES.get(name, {})
    docker_name = svc.get("container", name)
    for dep in svc.get("group", []):
        await wake(dep)
    lock = wake_locks.setdefault(docker_name, asyncio.Lock())
    async with lock:
        st = await container_state(docker_name)
        if st == "running":
            touch(docker_name)
            return True
        log.info("waking %s (was %s)", docker_name, st)
        rc, out = await docker("start", docker_name)
        if rc != 0:
            log.error("docker start %s failed: %s", docker_name, out)
            return False
        deadline = time.monotonic() + WAKE_TIMEOUT_S
        while time.monotonic() < deadline:
            st = await container_state(docker_name)
            if st == "running":
                touch(docker_name)
                log.info("woke %s", docker_name)
                return True
            await asyncio.sleep(2)
        log.error("wake %s timed out", docker_name)
        return False


async def sleep_service(name: str) -> bool:
    docker_name = SERVICES.get(name, {}).get("container", name)
    rc, out = await docker("stop", "-t", "30", docker_name)
    if rc == 0:
        log.info("slept %s", docker_name)
    else:
        log.error("docker stop %s: %s", docker_name, out)
    return rc == 0


def touch(name: str) -> None:
    last_active[name] = time.monotonic()


WAIT_PAGE = (
    b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: text/html\r\n"
    b"Retry-After: 5\r\nConnection: close\r\n\r\n"
    b"<!doctype html><meta http-equiv=refresh content=4>"
    b"<body style='font-family:system-ui;background:#0a0a0f;color:#00fff5;"
    b"display:flex;align-items:center;justify-content:center;height:100vh'>"
    b"<div><h2>Waking up the service&hellip;</h2>"
    b"<p>Idle containers are auto-slept. This reloads automatically.</p></div>"
)

HTTP_VERBS = (b"GET ", b"POST", b"PUT ", b"HEAD", b"OPTI", b"DELE", b"PATC")


async def pipe(a: asyncio.StreamReader, b: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await a.read(65536)
            if not data:
                break
            b.write(data)
            await b.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        b.close()


async def handle_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                      name: str) -> None:
    svc = SERVICES.get(name)
    if svc is None:
        writer.close()
        return
    docker_name = svc.get("container", name)
    active_conns[docker_name] = active_conns.get(docker_name, 0) + 1
    first = b""
    try:
        st = await container_state(docker_name)
        if st != "running":
            # Peek at first bytes: an HTTP verb to a UI port gets a waiting
            # page + auto-refresh; everything else just waits for the wake.
            try:
                first = await asyncio.wait_for(reader.read(6), timeout=3)
            except TimeoutError:
                first = b""
            if svc.get("wait_page") and first[:4] in HTTP_VERBS:
                asyncio.ensure_future(wake(name))
                writer.write(WAIT_PAGE)
                await writer.drain()
                return
            if not await wake(name):
                return
        touch(docker_name)
        host, port = svc["target"].rsplit(":", 1)
        try:
            up_r, up_w = await asyncio.wait_for(
                asyncio.open_connection(host, int(port)), timeout=30)
        except (OSError, TimeoutError):
            log.error("upstream %s unreachable after wake", name)
            return
        # If we peeked, replay those bytes upstream.
        if first:
            up_w.write(first)
            await up_w.drain()
        await asyncio.gather(pipe(reader, up_w), pipe(up_r, writer))
    finally:
        active_conns[docker_name] -= 1
        touch(docker_name)
        writer.close()


async def sleeper_loop() -> None:
    await asyncio.sleep(30)  # let the stack settle after boot
    while True:
        for name, svc in SERVICES.items():
            if svc.get("container", name) != name:
                continue  # alias of another managed container
            try:
                st = await container_state(name)
                if st == "stopped":
                    continue
                if st == "starting" or active_conns.get(name, 0) > 0:
                    low_cpu_streak[name] = 0
                    continue
                if time.monotonic() < keepawake.get(name, 0):
                    continue
                idle_for = time.monotonic() - last_active.get(
                    name, started_at.get(name, 0))
                if idle_for < svc["idle_min"] * 60:
                    low_cpu_streak[name] = 0
                    continue
                if await cpu_pct(name) < CPU_PCT_MAX:
                    low_cpu_streak[name] = low_cpu_streak.get(name, 0) + 1
                    if low_cpu_streak[name] >= 2:
                        await sleep_service(name)
                else:
                    low_cpu_streak[name] = 0
                    touch(name)  # busy outside the proxy — stays awake
            except Exception as e:
                log.error("sleeper check %s: %s", name, e)
        await asyncio.sleep(POLL_SECONDS)


async def api_handler(reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
    try:
        line = (await reader.readline()).decode()
        parts = line.split()
        if len(parts) < 2:
            writer.close()
            return
        method, path = parts[0], parts[1]
        # drain headers
        while await reader.readline() not in (b"\r\n", b"\n", b""):
            pass
        path_q = path.split("?", 1)
        segs = [s for s in path_q[0].split("/") if s]
        ttl = 0
        if len(path_q) > 1:
            for kv in path_q[1].split("&"):
                if kv.startswith("ttl="):
                    ttl = int(kv[4:])

        code, body = 404, {"error": "not found"}
        if segs == ["healthz"]:
            code, body = 200, {"ok": True}
        elif segs == ["status"]:
            body, code = {n: await _status_of(n) for n in SERVICES}, 200
        elif len(segs) == 2 and segs[0] == "status":
            body, code = await _status_of(segs[1]), 200
        elif len(segs) == 2 and segs[0] == "wake" and method == "POST":
            ok = await wake(segs[1])
            code, body = (200, {"woke": segs[1]}) if ok else (500, {"error": segs[1]})
        elif len(segs) == 2 and segs[0] == "sleep" and method == "POST":
            code, body = 200, {"slept": segs[1]}
            await sleep_service(segs[1])
        elif len(segs) == 2 and segs[0] == "keepawake" and method == "POST":
            keepawake[segs[1]] = time.monotonic() + (ttl or 7200)
            code, body = 200, {"keepawake": segs[1], "ttl_s": ttl or 7200}
        payload = json.dumps(body).encode()
        writer.write(
            f"HTTP/1.1 {code} X\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
            .encode() + payload)
        await writer.drain()
    except Exception as e:
        log.error("api: %s", e)
    finally:
        writer.close()


async def _status_of(name: str) -> dict:
    docker_name = SERVICES.get(name, {}).get("container", name)
    return {
        "state": await container_state(docker_name),
        "last_active_ago_s": int(time.monotonic() - last_active[docker_name])
        if docker_name in last_active else None,
        "active_conns": active_conns.get(docker_name, 0),
        "keepawake_s": max(0, int(keepawake.get(docker_name, 0) - time.monotonic())),
    }


async def main() -> None:
    now = time.monotonic()
    for name in SERVICES:
        last_active.setdefault(name, now)
        rc, out = await docker("inspect", "-f", "{{.State.StartedAt}}", name)
        if rc == 0 and out and out != "<nil>":
            started_at[name] = now  # conservative: treat boot time as now
    servers = []
    for port, name in LISTENERS.items():
        servers.append(await asyncio.start_server(
            lambda r, w, n=name: handle_conn(r, w, n), "0.0.0.0", port))
        log.info("proxy :%s -> %s (%s)", port, name, SERVICES[name]["target"])
    servers.append(await asyncio.start_server(api_handler, "0.0.0.0", API_PORT))
    log.info("control api :%s; sleeper %s", API_PORT,
             "on" if SLEEPER_ENABLED else "off")
    if SLEEPER_ENABLED:
        asyncio.ensure_future(sleeper_loop())
    await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == "__main__":
    asyncio.run(main())
