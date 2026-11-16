import socket
import threading

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


def pipe(a, b):
    try:
        while True:
            d = a.recv(65536)
            if not d:
                break
            b.sendall(d)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass


def handle(client, host, port):
    try:
        upstream = socket.create_connection((host, port), timeout=15)
    except OSError:
        client.close()
        return
    threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
    threading.Thread(target=pipe, args=(upstream, client), daemon=True).start()


def serve(port, host, tport):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(128)
    print(f"listening :{port} -> {host}:{tport}", flush=True)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c, host, tport), daemon=True).start()


for p, (h, tp) in ROUTES.items():
    threading.Thread(target=serve, args=(p, h, tp), daemon=True).start()
threading.Event().wait()
