#!/usr/bin/env python3
"""Inject a prompt into a ComfyUI API-format workflow JSON.

Supports both UI-format (nodes array) and API-format (keyed by node ID) workflows.
The positive prompt is node "2" (CLIPTextEncode) in the marketing pipeline.

Usage:
    inject_prompt.py <input_workflow.json> <prompt> <output_workflow.json>
"""
import json
import sys


def inject_api_format(wf: dict, prompt: str) -> dict:
    """API format: keys are node IDs as strings."""
    if "2" in wf and wf["2"].get("class_type") == "CLIPTextEncode":
        wf["2"]["inputs"]["text"] = prompt
    return wf


def inject_ui_format(wf: dict, prompt: str) -> dict:
    """UI format: nodes is a list with id fields."""
    for node in wf.get("nodes", []):
        if node.get("type") == "CLIPTextEncode" and node.get("id") == 2:
            node["inputs"]["text"] = prompt
            break
    return wf


def main():
    if len(sys.argv) != 4:
        print("Usage: inject_prompt.py <input_workflow.json> <prompt> <output_workflow.json>")
        sys.exit(1)
    input_path, prompt, output_path = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(input_path, "r") as f:
        wf = json.load(f)

    # Detect format: API format has string keys, UI format has "nodes" list
    if "nodes" in wf and isinstance(wf["nodes"], list):
        wf = inject_ui_format(wf, prompt)
    else:
        wf = inject_api_format(wf, prompt)

    with open(output_path, "w") as f:
        json.dump(wf, f, indent=2)
    print(f"Prompt injected into {output_path}")


if __name__ == "__main__":
    main()
