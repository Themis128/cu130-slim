---
name: dmr-ops
description: >
  Diagnose the Docker Model Runner serving layer — probes every engine
  (llama.cpp + vllm) × loaded model with a real /chat/completions call,
  mapping which model names actually serve vs hang. The failure class:
  /models 200 but chat disconnects (engine accepts, can't serve that model).
  Tool: scripts/dmr_doctor.py.
---

# DMR Ops — serving-layer doctor

`/models` returning 200 doesn't mean a model serves — `dmr_doctor.py`
probes each loaded model with a real chat call and reports
`{model: "answer (Xs)" | "FAIL TimeoutError"}`.

## Verified serving-map (2026-10-28)

- `docker.io/ai/smollm2:latest` → **serves** ("Sure, here is a" 4.5s)
- `hf.co/unsloth/qwen3-4b-instruct-2507-gguf:Q4_K_M` → Timeout
- `ai/smollm3` → Timeout
- `ai/smollm2-vllm` → HTTPError

## Note on call_dmr_chat

`call_dmr_chat(model_override="ai/smollm2")` still hangs — the service's
payload shape (tools/schema params) or name normalization differs from
the raw probe. Serving model names need `docker.io/<name>:latest` form.
