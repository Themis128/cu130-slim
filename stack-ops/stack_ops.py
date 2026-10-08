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
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [stack-ops] %(message)s")
log = logging.getLogger("stack-ops")

API_PORT = int(os.environ.get("STACK_OPS_API_PORT", "8787"))
POLL_SECONDS = int(os.environ.get("STACK_OPS_POLL_SECONDS", "60"))
CPU_PCT_MAX = float(os.environ.get("STACK_OPS_CPU_MAX", "3.0"))
WAKE_TIMEOUT_S = int(os.environ.get("STACK_OPS_WAKE_TIMEOUT", "120"))
SLEEPER_ENABLED = os.environ.get("STACK_OPS_SLEEPER", "1") == "1"
# Extra low-CPU polls required before sleeping a service that still has open
# proxied connections (idle SSE/MCP streams, abandoned noVNC tabs).
CONN_STREAK = int(os.environ.get("STACK_OPS_CONN_STREAK", "4"))

CONF = json.loads((Path(__file__).parent / "services.json").read_text())
# name -> {"target": host:port, "idle_min": int, "wait_page": bool, "group": [names]}
SERVICES: dict[str, dict] = CONF["services"]
LISTENERS: dict[int, str] = CONF["listeners"]  # local_port -> service name

last_active: dict[str, float] = {}
active_conns: dict[str, int] = {}
wake_locks: dict[str, asyncio.Lock] = {}
keepawake: dict[str, float] = {}
low_cpu_streak: dict[str, int] = {}


def resolve_container(name: str) -> str:
    """Service key -> docker container name (alias-aware)."""
    return SERVICES.get(name, {}).get("container", name)


def is_secondary_alias(name: str) -> bool:
    """True when `name` is an alias whose container is managed under another
    service key (e.g. browser-novnc-ui -> browser-novnc). A key like `comfyui`
    whose container name isn't itself a service key is NOT an alias — it is
    the canonical owner of its container."""
    docker_name = resolve_container(name)
    return docker_name != name and docker_name in SERVICES


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
    docker_name = resolve_container(name)
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
    docker_name = resolve_container(name)
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


HTTP_503 = (
    b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\n"
    b"Retry-After: 15\r\nConnection: close\r\n\r\n"
)


async def handle_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                      name: str) -> None:
    svc = SERVICES.get(name)
    if svc is None:
        writer.close()
        return
    docker_name = resolve_container(name)
    active_conns[docker_name] = active_conns.get(docker_name, 0) + 1
    first = b""
    try:
        st = await container_state(docker_name)
        if st != "running":
            # Peek at the first bytes (up to one segment covers typical HTTP
            # headers): a *browser* navigation (GET + Accept: text/html) to a
            # UI port gets the waiting page + auto-refresh. Everything else —
            # API GETs (Accept: */*), POST bodies, SOCKS5/VNC — blocks until
            # the service is awake. An HTML page in reply to an API call
            # would just fail the caller.
            try:
                first = await asyncio.wait_for(reader.read(8192), timeout=3)
            except TimeoutError:
                first = b""
            browser_nav = (
                first[:4] == b"GET " and b"text/html" in first.lower())
            if svc.get("wait_page") and browser_nav:
                asyncio.ensure_future(wake(name))
                writer.write(WAIT_PAGE)
                await writer.drain()
                return
            if not await wake(name):
                if first[:4] in HTTP_VERBS:
                    writer.write(
                        HTTP_503 + json.dumps(
                            {"error": f"{docker_name} failed to wake"}
                        ).encode())
                    await writer.drain()
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
            if is_secondary_alias(name):
                continue
            docker_name = resolve_container(name)
            try:
                st = await container_state(docker_name)
                if st in ("stopped", "missing"):
                    continue
                if st == "starting":
                    low_cpu_streak[docker_name] = 0
                    continue
                if time.monotonic() < keepawake.get(docker_name, 0):
                    continue
                idle_for = time.monotonic() - last_active.get(docker_name, 0)
                if idle_for < svc["idle_min"] * 60:
                    low_cpu_streak[docker_name] = 0
                    continue
                # Open proxied conns (SSE, idle noVNC tabs, forgotten MCP
                # streams) don't block sleep, but earn extra low-CPU polls so
                # a genuinely busy session keeps the service up via CPU.
                need_streak = CONN_STREAK if active_conns.get(
                    docker_name, 0) > 0 else 2
                if await cpu_pct(docker_name) < CPU_PCT_MAX:
                    low_cpu_streak[docker_name] = low_cpu_streak.get(
                        docker_name, 0) + 1
                    if low_cpu_streak[docker_name] >= need_streak:
                        await sleep_service(name)
                else:
                    low_cpu_streak[docker_name] = 0
                    touch(docker_name)  # busy outside the proxy — stays awake
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
            names = list(SERVICES)
            res = await asyncio.gather(*(_status_of(n) for n in names))
            body, code = dict(zip(names, res)), 200
        elif len(segs) == 2 and segs[0] == "status":
            if segs[1] not in SERVICES:
                code, body = 404, {"error": "unknown service"}
            else:
                body, code = await _status_of(segs[1]), 200
        elif len(segs) == 2 and segs[0] == "wake" and method == "POST":
            if segs[1] not in SERVICES:
                code, body = 404, {"error": "unknown service"}
            else:
                ok = await wake(segs[1])
                code, body = (200, {"woke": segs[1]}) if ok else (500, {"error": segs[1]})
        elif len(segs) == 2 and segs[0] == "sleep" and method == "POST":
            if segs[1] not in SERVICES:
                code, body = 404, {"error": "unknown service"}
            else:
                ok = await sleep_service(segs[1])
                code, body = (200, {"slept": segs[1]}) if ok else (500, {"error": segs[1]})
        elif segs == ["sleep-all"] and method == "POST":
            canonical = [n for n in SERVICES if not is_secondary_alias(n)]
            res = await asyncio.gather(*(sleep_service(n) for n in canonical))
            code, body = 200, {"slept": dict(zip(canonical, res))}
        elif len(segs) == 2 and segs[0] == "keepawake" and method == "POST":
            docker_name = resolve_container(segs[1])
            keepawake[docker_name] = time.monotonic() + (ttl or 7200)
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
    docker_name = resolve_container(name)
    return {
        "container": docker_name,
        "state": await container_state(docker_name),
        "last_active_ago_s": int(time.monotonic() - last_active[docker_name])
        if docker_name in last_active else None,
        "active_conns": active_conns.get(docker_name, 0),
        "keepawake_s": max(0, int(keepawake.get(docker_name, 0) - time.monotonic())),
    }


def _docker_ts_to_monotonic(ts: str, now_wall: float, now_mono: float) -> float:
    """Convert a docker RFC3339 StartedAt into monotonic-clock time."""
    try:
        epoch = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc).timestamp()
        return now_mono - max(0.0, now_wall - epoch)
    except ValueError:
        return now_mono


async def main() -> None:
    now_mono, now_wall = time.monotonic(), time.time()
    for name, svc in SERVICES.items():
        docker_name = svc.get("container", name)
        if docker_name in last_active:
            continue
        # Seed idle clock from the container's real StartedAt so containers
        # started outside the proxy (compose up, session healer) get a full
        # idle window, and long-running-but-untouched ones can sleep promptly.
        rc, out = await docker(
            "inspect", "-f", "{{.State.StartedAt}}", docker_name)
        last_active[docker_name] = (
            _docker_ts_to_monotonic(out, now_wall, now_mono)
            if rc == 0 and out and out != "<nil>" else now_mono)
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
