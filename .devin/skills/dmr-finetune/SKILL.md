---
name: dmr-finetune
description: QLoRA fine-tune pipeline for the DMR content models — builds the media-prompt dataset, trains a LoRA on Qwen3-4B-Instruct-2507 (the DMR_MID_MODEL base), exports a merged GGUF, packages it into DMR via `docker model package`, and wires it to `expand_visual_prompt` through `DMR_MEDIA_MODEL`. Use when improving the realism/accuracy of AI-generated media prompts or captions.
---

# DMR Fine-Tune (media prompt LoRA)

Fine-tunes the DMR mid model so `expand_visual_prompt()` produces
physically-accurate, photorealistic generation prompts (real camera/lighting
language, plausible scenes) instead of abstract glow-clichés that make
Wan2.2/FLUX output muddy.

## Pipeline

| Step | Script | What it does |
|------|--------|--------------|
| 1. Data | `scripts/build_dataset.py` | Writes `data/media_prompts{,_video}.jsonl` from REAL production inputs — the prompts stored in `media_assets.meta_data.quality.prompt` (dumped to `data/real_prompts.json`). Photographic prompts get paraphrase targets; abstract/illustration prompts map to concrete real scenes. Matches the exact production system prompt + `/no_think` suffix |
| 2. Train | `Dockerfile.trainer` + `scripts/train_lora.py` | Unsloth QLoRA r16 on `unsloth/Qwen3-4B-Instruct-2507-bnb-4bit`, 3 epochs, merged GGUF q4_k_m export to `/work/out/gguf` |
| 3. Package | `docker model package --gguf out/gguf/*.gguf cloudless/qwen3-4b-media:latest` | Registers merged model in the DMR content store |
| 4. Configure | watchdog `apply_configs` or `POST /engines/_configure` | ctx 4096, keep-alive 5m, `--n-gpu-layers 0` (CPU like all DMR models — packaging resets config to GPU+262K ctx and OOMs) |
| 5. Wire | set `DMR_MEDIA_MODEL=cloudless/qwen3-4b-media:latest` in `.env` | `expand_visual_prompt` uses it via `model_override` |
| 6. Eval | `scripts/eval_prompts.py` | A/B base vs tuned. Default topics are OOD smoke tests only; use `--topics-file data/real_prompts.json` for in-distribution eval on the real input corpus |

## GPU discipline (8GB card, shared with ComfyUI + DMR CPU-pinned models)

- Training needs ~6-7GB VRAM — **only run when the ComfyUI queue is empty**
  (`GET /queue` on :8199) and unload DMR GPU remnants first
  (`docker model unload --all`).
- The trainer image reuses `ghcr.io/themis128/cu130-slim:comfyui-latest`
  (torch 2.6+cu118 present) — build once, rerun cheaply.
- keepawake ComfyUI is NOT needed (training doesn't use ComfyUI), but do
  check the sleeper won't stop the *trainer* — it's an unmanaged container.

## Run it

```bash
cd .devin/skills/dmr-finetune
python3 scripts/build_dataset.py
docker build -f Dockerfile.trainer -t dmr-trainer .
docker run --rm --gpus all \
  -v "$PWD:/work" -v "$PWD/scripts:/work/scripts" \
  dmr-trainer
docker model package --gguf out/gguf/*.gguf cloudless/qwen3-4b-media:latest
docker model configure --context-size 4096 cloudless/qwen3-4b-media:latest
```

Then set `DMR_MEDIA_MODEL` in `cu130-slim/.env`, restart social-api +
workers, and run `scripts/eval_prompts.py` to A/B.

## Why merged-GGUF (not runtime --lora)

DMR's llama.cpp backend supports runtime flags (`docker model configure
-- --lora <path>`) but the adapter must live inside the runner's models
volume and flag config is wiped on every runner restart. Merging the LoRA
into a fresh q4_k_m GGUF and `docker model package`-ing it is durable,
rollback-friendly (just repoint `DMR_MEDIA_MODEL`), and needs no runtime
plumbing.

## Pitfalls hit (and fixes now in `train_lora.py`)

- **unsloth#3552 think-token injection**: `unsloth/Qwen3-4B-Instruct-2507*`
  ships a thinking-style chat template — `apply_chat_template` inserts an
  empty `<think></think>` block into assistant turns at *training* time but
  not at *inference* time. Symptom: degenerate `<tool_call>`/`assistant`
  loops on every prompt at temp 0.2-0.7. Fix: force the instruct template
  after `from_pretrained`:
  `tokenizer = get_chat_template(tokenizer, chat_template="qwen3-instruct")`
  (from `unsloth.chat_templates`). Verify a sample render has no `<think>`.
- **`train_on_responses_only`**: loss over the full sequence wastes capacity
  on the fixed system prompt; masking to the assistant turn speeds
  convergence (QLoRA paper +~1%). `instruction_part="<|im_start|>user\n"`,
  `response_part="<|im_start|>assistant\n"`.
- **unsloth Qwen3 merge is CORRUPT (unsloth#3428/#3508)**: on
  `unsloth-2025.10.9`, `save_pretrained_merged`/`save_pretrained_gguf`
  writes a broken merged model — Qwen3 ties `lm_head` and
  `embed_tokens` and the save path corrupts them. Symptom: the
  fine-tuned model emits word-salad / CJK tokens / repetition loops on
  every input, even at temp 0, while the same adapter loaded via
  `PeftModel` works fine. Fix (now in `train_lora.py`): save the
  adapter, `merge_and_unload()` in memory, clone `embed_tokens` into
  `lm_head` and set `tie_word_embeddings=False`, then
  `save_pretrained`. If the model still lands bnb-quantized,
  `model.dequantize()` → `.to(bf16)` → save, before GGUF conversion.
- **unsloth auto-GGUF is fragile**: `save_pretrained_gguf` fails on
  missing `cmake`/`libcurl4-openssl-dev`, stale llama.cpp checkouts
  (`original_gguf_*.py` litter), and obsolete Makefile builds.
  Convert manually instead: `convert_hf_to_gguf.py out/merged16
  --outtype bf16` + `llama-quantize ... Q4_K_M`. Delete
  `llama.cpp/original_gguf_*.py` between runs.
- **fresh `docker model package` loses runtime config**: defaults to
  `-ngl 999` + full 262K ctx -> 36GB KV alloc -> HTTP 500. Always
  re-apply canonical config after packaging (ctx 4096, keep-alive,
  `--n-gpu-layers 0` via `/engines/_configure` runtime-flags) — the
  dmr-watchdog does this automatically for models in its list.
- **xformers cu124 wheel vs torch cu118**: expected warning; unsloth
  falls back to PyTorch attention. Do not chase the mismatch —
  rebuilding xformers is not worth it on this card.
- **every `docker model package` orphans the previous entry**: each
  repackage creates a new model ID and leaves the old one as `<none>` in
  `docker model ls` (~2.5GiB each) AND resets runtime config to
  `-ngl 999` + 262K ctx (36GB KV → OOM → HTTP 500 on next load). After
  any repackage: `docker model rm <stale ids>` for `<none>` rows, then
  re-run `dmr-configure.py`.
- **`build_dataset.py` self-validates**: refuses to write rows with
  duplicate-target rate ≥10%, missing `/no_think` suffixes, protocol
  fragments (`<|im_`, `<tool_call`), secret-like strings, or
  out-of-bounds target lengths — run it before every training run.
- **dataset lessons (hard-won)**: (a) `expand_visual_prompt` receives
  terse VISUAL prompts, not post captions — train on the prompts stored
  in `media_assets`, not the `posts` table; (b) keep ONE output
  scaffold — mixing sentence templates makes sampling splice
  mid-probability tokens; (c) never repeat identical target strings
  dozens of times — the model memorizes n-grams detached from context;
  (d) targets must stay photographable — strip "marketing image",
  illustration, hex-color, title/subtitle fragments from real prompts.
