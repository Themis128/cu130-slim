"""stack-ops-bridge interim relay — fsbridge flap workaround (BACKLOG 2026-11-16).

The real ``stack-ops`` container has bind mounts, so every fsbridge flap
poisons them and the whole ``stack-ops:*`` route surface (COMFYUI_URL,
browser sidecars, LanguageTool, n8n-sandbox, ...) goes dark. This script
runs INSIDE the mount-free ``stack-ops-bridge`` container (alias
``stack-ops`` on cu130-backend) and TCP-relays each listener port to the
real service container.

Route table mirrors ``stack-ops/services.json`` (listeners -> services ->
target host:port) as of 2026-11-16. Regenerate if services.json changes:
this bridge is interim — remove it after the ``wsl --shutdown`` heal
(``docker rm -f stack-ops-bridge`` + ``docker compose up -d stack-ops``).

Install/launch (compose-watchdog section 14 does this automatically):
    docker cp stack-ops/bridge-relay.py stack-ops-bridge:/tmp/relay.py
    docker exec -d stack-ops-bridge python /tmp/relay.py
"""

import socket
import threading

# listener_port -> (target_container, target_port)
ROUTES = {
    9223: ("browser-novnc", 9223),
    9224: ("tiktok-browser-sidecar", 9224),
    9225: ("linkedin-browser-sidecar", 9225),
    9226: ("facebook-browser-sidecar", 9226),
    9230: ("messenger-sidecar", 9230),
    8000: ("instagram-private-api", 8000),
    1080: ("warp-proxy", 1080),
    8010: ("languagetool", 8010),
    8081: ("n8n-sandbox", 8081),
    3000: ("metabase", 3000),
    8888: ("social-jupyter", 8888),
    5555: ("flower", 5555),
    9000: ("portainer", 9000),
    8080: ("env-manager-frontend", 8080),
    9227: ("linkedin-mcp-server", 9227),
    9228: ("airbyte-mcp-server", 9228),
    6080: ("browser-novnc", 6080),
    6081: ("browser-novnc", 6081),
    5900: ("browser-novnc", 5900),
    8199: ("social-media-comfyui-gpu", 8000),
}


def pipe(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass


def handle(client: socket.socket, host: str, port: int) -> None:
    try:
        upstream = socket.create_connection((host, port), timeout=15)
    except OSError:
        client.close()
        return
    threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
    threading.Thread(target=pipe, args=(upstream, client), daemon=True).start()


def serve(port: int, host: str, target_port: int) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(128)
    print(f"listening :{port} -> {host}:{target_port}", flush=True)
    while True:
        client, _ = srv.accept()
        threading.Thread(target=handle, args=(client, host, target_port), daemon=True).start()


if __name__ == "__main__":
    for p, (h, tp) in ROUTES.items():
        threading.Thread(target=serve, args=(p, h, tp), daemon=True).start()
    threading.Event().wait()
