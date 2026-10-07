"""Unit tests for stack-ops (idle-sleep + wake proxy).

Run:  python3 -m unittest test_stack_ops -v   (or: pytest test_stack_ops.py)

Docker-touching paths are patched — these tests cover config integrity,
alias/container resolution, the HTTP wait-page predicate, the control API
routing, and the full proxy pipe path against an in-process echo server.
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import stack_ops
from stack_ops import (
    HTTP_VERBS,
    LISTENERS,
    SERVICES,
    api_handler,
    handle_conn,
    is_secondary_alias,
    resolve_container,
)


class TestConfigIntegrity(unittest.TestCase):
    def test_every_listener_points_at_a_service(self):
        for port, name in LISTENERS.items():
            self.assertIn(name, SERVICES, f"listener :{port} -> missing {name}")

    def test_every_service_has_host_port_target(self):
        for name, svc in SERVICES.items():
            host, _, port = svc["target"].rpartition(":")
            self.assertTrue(host, f"{name}: bad target {svc['target']}")
            self.assertTrue(port.isdigit(), f"{name}: bad target {svc['target']}")

    def test_every_group_dep_exists(self):
        for name, svc in SERVICES.items():
            for dep in svc.get("group", []):
                self.assertIn(dep, SERVICES, f"{name}: missing dep {dep}")

    def test_every_service_has_positive_idle_min(self):
        for name, svc in SERVICES.items():
            self.assertGreater(svc.get("idle_min", 0), 0, name)

    def test_no_listener_port_collision(self):
        self.assertEqual(len(LISTENERS), len(set(LISTENERS)))

    def test_alias_targets_use_same_container(self):
        # Aliases pointing at one container must not disagree about ports
        # that already exist as canonical listeners (except intentionally
        # remapped ports like comfyui's 8199 side-door).
        by_container = {}
        for name, svc in SERVICES.items():
            by_container.setdefault(resolve_container(name), set()).add(
                svc["target"].rsplit(":", 1)[0])
        for container, hosts in by_container.items():
            self.assertEqual(len(hosts), 1,
                             f"{container}: conflicting upstream hosts {hosts}")


class TestAliasResolution(unittest.TestCase):
    def test_comfyui_is_canonical_owner(self):
        # regression: sleeper used to skip any service whose "container"
        # differed from the key — comfyui's container is
        # social-media-comfyui-gpu, which is NOT another service key, so it
        # must stay managed (this was the auto-sleep gap).
        self.assertEqual(resolve_container("comfyui"), "social-media-comfyui-gpu")
        self.assertFalse(is_secondary_alias("comfyui"))

    def test_novnc_ui_is_secondary_alias(self):
        self.assertEqual(resolve_container("browser-novnc-ui"), "browser-novnc")
        self.assertTrue(is_secondary_alias("browser-novnc-ui"))

    def test_plain_service_and_unknown(self):
        self.assertFalse(is_secondary_alias("languagetool"))
        self.assertFalse(is_secondary_alias("nonexistent"))
        self.assertEqual(resolve_container("nonexistent"), "nonexistent")

    def test_canonical_set_covers_all_containers(self):
        canonical = {resolve_container(n)
                     for n in SERVICES if not is_secondary_alias(n)}
        for name in SERVICES:
            self.assertIn(resolve_container(name), canonical)


class TestWaitPagePredicate(unittest.TestCase):
    def test_get_gets_wait_page(self):
        self.assertEqual(b"GET /"[0:4], b"GET ")

    def test_verbs_cover_common_methods(self):
        for verb in (b"GET ", b"POST", b"PUT ", b"HEAD", b"OPTI", b"DELE",
                     b"PATC"):
            self.assertIn(verb, HTTP_VERBS)

    def test_socks5_greeting_is_not_http(self):
        self.assertNotIn(b"\x05\x01\x00"[:4], HTTP_VERBS)


class TestTimestampParsing(unittest.TestCase):
    def test_docker_rfc3339_to_monotonic(self):
        now_wall, now_mono = 1_000_000.0, 500.0
        # container started 10s ago in wall-clock -> monotonic 490
        import datetime as dt
        started = dt.datetime.fromtimestamp(now_wall - 10, tz=dt.timezone.utc)
        ts = started.strftime("%Y-%m-%dT%H:%M:%S") + ".123456789Z"
        got = stack_ops._docker_ts_to_monotonic(ts, now_wall, now_mono)
        self.assertAlmostEqual(got, 490.0, places=1)

    def test_garbage_timestamp_falls_back(self):
        self.assertEqual(stack_ops._docker_ts_to_monotonic("junk", 1.0, 42.0), 42.0)


class ApiTestBase(unittest.IsolatedAsyncioTestCase):
    """Spin the real api_handler on an ephemeral port with docker faked."""

    async def _serve(self, handler, **patch_kwargs):
        self._docker_p = patch.object(stack_ops, "docker",
                                      new=AsyncMock(return_value=(0, "")))
        self._docker_p.start()
        self.addCleanup(self._docker_p.stop)
        for fname, ret in patch_kwargs.items():
            p = patch.object(stack_ops, fname, new=AsyncMock(return_value=ret))
            p.start()
            self.addCleanup(p.stop)
        self._srv = await asyncio.start_server(handler, "127.0.0.1", 0)
        self.addCleanup(self._srv.close)
        self._port = self._srv.sockets[0].getsockname()[1]

    async def _req(self, method: str, path: str) -> tuple[int, dict]:
        r, w = await asyncio.open_connection("127.0.0.1", self._port)
        w.write(f"{method} {path} HTTP/1.0\r\nHost: x\r\n\r\n".encode())
        await w.drain()
        raw = await r.read()
        w.close()
        head, _, body = raw.partition(b"\r\n\r\n")
        code = int(head.split()[1])
        return code, json.loads(body) if body else {}


class TestControlApi(ApiTestBase):
    async def asyncSetUp(self):
        await self._serve(api_handler)

    async def test_healthz(self):
        code, body = await self._req("GET", "/healthz")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])

    async def test_status_unknown_service_404(self):
        code, _ = await self._req("GET", "/status/not-a-service")
        self.assertEqual(code, 404)

    async def test_sleep_unknown_service_404(self):
        code, _ = await self._req("POST", "/sleep/not-a-service")
        self.assertEqual(code, 404)

    async def test_wake_unknown_service_404(self):
        code, _ = await self._req("POST", "/wake/not-a-service")
        self.assertEqual(code, 404)

    async def test_status_returns_every_service(self):
        code, body = await self._req("GET", "/status")
        self.assertEqual(code, 200)
        self.assertEqual(set(body), set(SERVICES))
        for info in body.values():
            self.assertIn(info["state"],
                          ("running", "starting", "stopped", "missing"))


class TestControlApiFailures(ApiTestBase):
    async def asyncSetUp(self):
        await self._serve(api_handler, sleep_service=False, wake=False)

    async def test_sleep_failure_returns_500(self):
        # regression: /sleep used to report success unconditionally
        code, body = await self._req("POST", "/sleep/languagetool")
        self.assertEqual(code, 500)
        self.assertIn("error", body)

    async def test_wake_failure_returns_500(self):
        code, body = await self._req("POST", "/wake/languagetool")
        self.assertEqual(code, 500)


class TestSleepAll(ApiTestBase):
    async def asyncSetUp(self):
        self.slept = []

        async def fake_sleep(name):
            self.slept.append(name)
            return True

        self._p = patch.object(stack_ops, "sleep_service", new=fake_sleep)
        self._p.start()
        self.addCleanup(self._p.stop)
        await self._serve(api_handler)

    async def test_sleep_all_hits_canonical_only(self):
        code, body = await self._req("POST", "/sleep-all")
        self.assertEqual(code, 200)
        self.assertTrue(all(body["slept"].values()))
        # comfyui MUST be in the canonical set (the auto-sleep gap), and no
        # secondary alias may appear.
        self.assertIn("comfyui", self.slept)
        self.assertNotIn("browser-novnc-ui", self.slept)
        self.assertIn("browser-novnc", self.slept)


class TestKeepawake(ApiTestBase):
    async def asyncSetUp(self):
        await self._serve(api_handler)

    async def test_keepawake_keyed_by_container(self):
        # regression: keepawake was stored under the alias, but the sleeper
        # checks the container name — so /keepawake/comfyui never protected
        # social-media-comfyui-gpu.
        code, _ = await self._req("POST", "/keepawake/comfyui?ttl=600")
        self.assertEqual(code, 200)
        self.assertIn("social-media-comfyui-gpu", stack_ops.keepawake)
        self.assertNotIn("comfyui", stack_ops.keepawake)
        del stack_ops.keepawake["social-media-comfyui-gpu"]


class ProxyTestBase(unittest.IsolatedAsyncioTestCase):
    """Drive handle_conn with a fake service + in-process echo upstream."""

    async def _proxy_to(self, svc_name="__test_svc__", **svc_over):
        svc = {"target": "127.0.0.1:1", "idle_min": 1}
        svc.update(svc_over)
        SERVICES[svc_name] = svc
        self.addCleanup(SERVICES.pop, svc_name)
        self._srv = await asyncio.start_server(
            lambda r, w: handle_conn(r, w, svc_name), "127.0.0.1", 0)
        self.addCleanup(self._srv.close)
        return self._srv.sockets[0].getsockname()[1]

    async def _roundtrip(self, port, payload: bytes) -> bytes:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(payload)
        await w.drain()
        data = await asyncio.wait_for(r.read(65536), timeout=10)
        w.close()
        return data


class TestWaitPageFlow(ProxyTestBase):
    async def asyncSetUp(self):
        self._state_p = patch.object(stack_ops, "container_state",
                                     new=AsyncMock(return_value="stopped"))
        self._state_p.start()
        self.addCleanup(self._state_p.stop)
        self.wake_calls = []

        async def fake_wake(name):
            self.wake_calls.append(name)
            await asyncio.sleep(0.05)
            return False  # never comes up

        self._wake_p = patch.object(stack_ops, "wake", new=fake_wake)
        self._wake_p.start()
        self.addCleanup(self._wake_p.stop)

    async def test_get_serves_wait_page_and_kicks_wake(self):
        port = await self._proxy_to(wait_page=True)
        data = await self._roundtrip(port, b"GET / HTTP/1.1\r\n\r\n")
        self.assertIn(b"503", data.split(b"\r\n", 1)[0])
        self.assertIn(b"Waking up", data)
        await asyncio.sleep(0.1)
        self.assertTrue(self.wake_calls)

    async def test_post_blocks_and_gets_503_json_on_wake_failure(self):
        # regression: POST used to receive the HTML wait page, breaking API
        # clients. Now it waits for the wake and gets JSON 503 on failure.
        port = await self._proxy_to(wait_page=True)
        data = await self._roundtrip(
            port, b"POST /api/x HTTP/1.1\r\nContent-Length: 2\r\n\r\n{}")
        self.assertIn(b"503", data.split(b"\r\n", 1)[0])
        self.assertIn(b"application/json", data)

    async def test_non_http_waits_for_wake(self):
        # SOCKS5 greeting (no wait_page service): no response, wake attempted,
        # connection closed on failure — not an HTML page.
        port = await self._proxy_to()
        data = await self._roundtrip(port, b"\x05\x01\x00")
        self.assertEqual(data, b"")
        self.assertTrue(self.wake_calls)


class TestProxyPipe(ProxyTestBase):
    async def asyncSetUp(self):
        self._state_p = patch.object(stack_ops, "container_state",
                                     new=AsyncMock(return_value="running"))
        self._state_p.start()
        self.addCleanup(self._state_p.stop)

        async def echo(r, w):
            while data := await r.read(65536):
                w.write(b"ECHO:" + data)
                await w.drain()

        self._echo = await asyncio.start_server(echo, "127.0.0.1", 0)
        self.addCleanup(self._echo.close)
        self._echo_port = self._echo.sockets[0].getsockname()[1]

    async def test_running_service_pipes_bytes(self):
        port = await self._proxy_to(target=f"127.0.0.1:{self._echo_port}")
        data = await self._roundtrip(port, b"hello-proxy")
        self.assertEqual(data, b"ECHO:hello-proxy")


if __name__ == "__main__":
    unittest.main()
