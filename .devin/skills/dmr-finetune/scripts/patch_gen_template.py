#!/usr/bin/env python3
"""Patch the merged model's chat_template.jinja so the generation prompt
emits the empty-think prefix the QLoRA was trained against (unsloth#3383:
unsloth's Qwen3-4B-Instruct-2507 tokenizer injects a think block into
assistant turns during apply_chat_template, but the generation prompt is
bare). With the prefix in the prompt, the model emits the trained content
directly."""
import sys

im_s = chr(60) + "|im_start|>"
th_o = chr(60) + "think>"
th_c = chr(60) + "/think>"
p = sys.argv[1] if len(sys.argv) > 1 else "/work/out/gguf/chat_template.jinja"
t = open(p).read()
old = ("{%- if add_generation_prompt %}\n"
       "    {{- '" + im_s + "assistant\\n' }}\n"
       "{%- endif %}")
new = ("{%- if add_generation_prompt %}\n"
       "    {{- '" + im_s + "assistant\\n" + th_o + "\\n\\n" + th_c + "\\n\\n' }}\n"
       "{%- endif %}")
assert old in t, "generation-prompt pattern not found"
open(p, "w").write(t.replace(old, new))
print("patched:", p)
print(open(p).read()[-320:])
