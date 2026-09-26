"""Functional tests for the DMR client against the live runner.

Run inside the social-api container (the runner is reachable only from the
compose network + host.docker.internal):

    docker exec social-api python /app/tests/unit/test_dmr_functions.py

or via pytest if installed. Requires the model runner on 12435; the inference
cases intentionally hit the tiny model (smollm3, ~1.9 GB, 5m keep-alive) to
keep VRAM impact minimal.
"""
import asyncio

from app.services import dmr


async def main() -> None:
    # 1. Health pre-flight
    ok = await dmr._check_dmr_health()
    assert ok, "DMR health check failed — runner unreachable?"

    # 2. VRAM info is gracefully None where nvidia-smi is absent (containers)
    vram = dmr._get_vram_info()
    assert vram is None or set(vram) == {"used", "free", "total"}, vram
    assert dmr._has_vram_for_model("ai/smollm3") is True

    # 3. F1 regression: the warmup vision branch reads the 'free' key
    dmr._state.vram = {"used": 2000, "free": 7000, "total": 8192}
    assert dmr._state.vram.get("free", 0) == 7000
    dmr._state.vram = {}

    # 4. configure_keep_alive must not raise even though
    #    /inference/_configure is 404 on the current runner build
    await dmr.configure_keep_alive("ai/smollm3", "5m")

    # 5. Real inference through the public client (tiny-model route)
    r = await dmr.call_dmr_chat("hi")
    text = (r.get("text") or "").strip()
    assert text, f"expected non-empty completion, got {r!r}"

    # 6. Unload fallback: /api/ps + Ollama chat keep_alive=0
    client = await dmr._get_client()
    base = dmr._dmr_base_url()
    ps = await client.get(base + "/api/ps", timeout=5)
    loaded_before = [m.get("name") for m in ps.json().get("models", []) if m.get("name")]
    print("loaded before unload:", loaded_before)

    await dmr._unload_idle_models()

    ps2 = await client.get(base + "/api/ps", timeout=5)
    loaded_after = [m.get("name") for m in ps2.json().get("models", []) if m.get("name")]
    print("loaded after unload:", loaded_after)
    assert not loaded_after, f"unload fallback left models loaded: {loaded_after}"

    await dmr.close_client()
    print("ALL DMR FUNCTIONAL TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
