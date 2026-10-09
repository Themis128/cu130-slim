#!/usr/bin/env python3
"""QLoRA fine-tune of Qwen3-4B-Instruct-2507 for media-prompt expansion.

Trains on data/media_prompts*.jsonl (chat rows matching the production
expand_visual_prompt call format) and exports a merged GGUF ready for
`docker model package`.

Run inside the trainer container (see SKILL.md — the comfyui image works:
torch 2.6+cu118 already installed).

VRAM: ~6-7GB for 4B 4-bit + LoRA r16 + seq 1024 — fits the RTX 3070 8GB
when ComfyUI/DMR GPU jobs are idle. CHECK the ComfyUI queue first.
"""
import json
import os
import sys
from pathlib import Path

BASE_MODEL = "unsloth/Qwen3-4B-Instruct-2507-bnb-4bit"
OUT_DIR = Path("/work/out")
DATA_DIR = Path("/work/data")
EPOCHS = int(os.environ.get("EPOCHS", "3"))
RESPONSE_ONLY = os.environ.get("RESPONSE_ONLY", "1") == "1"
CE_PATCH = os.environ.get("CE_PATCH", "1") == "1"


def main() -> None:
    from unsloth import FastLanguageModel  # must precede trl/transformers imports
    from unsloth.chat_templates import get_chat_template, train_on_responses_only
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer

    # fused-CE chunk sizing reads free VRAM; when the allocator has already
    # reserved the card it returns 0 and crashes with ZeroDivisionError.
    # Floor target_gb so chunk math never divides by zero.
    if CE_PATCH:
        import unsloth_zoo.fused_losses.cross_entropy_loss as _cel
        _orig_chunk_multiplier = _cel._get_chunk_multiplier

        def _safe_chunk_multiplier(vocab_size, target_gb=None):
            return _orig_chunk_multiplier(vocab_size, target_gb if target_gb else 0.5)

        _cel._get_chunk_multiplier = _safe_chunk_multiplier
        _cel.get_chunk_size.__globals__["_get_chunk_multiplier"] = _safe_chunk_multiplier

    rows = []
    for f in sorted(DATA_DIR.glob("media_prompts*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line)["messages"])
    if not rows:
        raise SystemExit("no training rows — run build_dataset.py first")
    print(f"[train] {len(rows)} rows")

    model, tokenizer = FastLanguageModel.from_pretrained(
        BASE_MODEL, max_seq_length=1024, load_in_4bit=True, dtype=None,
    )
    # unsloth#3552: the Instruct-2507 tokenizer ships a thinking-style
    # template that injects an empty <think></think> block into assistant
    # turns — mismatched with inference. Force the qwen3-instruct template.
    tokenizer = get_chat_template(tokenizer, chat_template="qwen3-instruct")
    model = FastLanguageModel.get_peft_model(
        model, r=16,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        lora_alpha=32, lora_dropout=0.05, bias="none",
        use_gradient_checkpointing="unsloth", random_state=42,
    )

    # Render chat rows with the model's own template → single "text" field.
    ds = Dataset.from_list([
        {"text": tokenizer.apply_chat_template(
            m, tokenize=False, add_generation_prompt=False)}
        for m in rows
    ])

    trainer = SFTTrainer(
        model=model, processing_class=tokenizer, train_dataset=ds,
        args=SFTConfig(
            dataset_text_field="text",
            max_length=1024,
            eos_token="<|im_end|>",
            per_device_train_batch_size=2,
            gradient_accumulation_steps=4,
            warmup_steps=10,
            num_train_epochs=EPOCHS,
            learning_rate=2e-4,
            fp16=False, bf16=True,
            optim="adamw_8bit",
            weight_decay=0.01,
            lr_scheduler_type="linear",
            logging_steps=10,
            save_strategy="no",
            report_to="none",
            seed=42,
            output_dir=str(OUT_DIR / "ckpt"),
        ),
    )
    # Loss only on the assistant turn (QLoRA paper: +1% accuracy; also
    # keeps the model from wasting capacity modelling the fixed prompt).
    if RESPONSE_ONLY:
        trainer = train_on_responses_only(
            trainer,
            instruction_part="<|im_start|>user\n",
            response_part="<|im_start|>assistant\n",
        )
    trainer.train()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # unsloth#3428/#3508: save_pretrained_merged/save_pretrained_gguf
    # corrupt Qwen3 merges — lm_head and embed_tokens share tied weights
    # and the save path breaks them (adapter works, merged model = garbage).
    # Merge in memory (merge_and_unload works), untie lm_head, save.
    import torch
    adapter_dir = OUT_DIR / "adapter"
    model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))
    merged = model.merge_and_unload()
    if getattr(merged.config, "tie_word_embeddings", False):
        merged.lm_head.weight = torch.nn.Parameter(
            merged.model.embed_tokens.weight.detach().clone())
        merged.config.tie_word_embeddings = False
    merged.config.use_cache = True
    merged.save_pretrained(str(OUT_DIR / "gguf"), safe_serialization=True)
    tokenizer.save_pretrained(str(OUT_DIR / "gguf"))
    print("[train] merged 16-bit model written to", OUT_DIR / "gguf")


if __name__ == "__main__":
    sys.exit(main())
