#!/usr/bin/env python3
import json
import sys

def main():
    if len(sys.argv) != 4:
        print("Usage: inject_prompt.py <input_workflow.json> <prompt> <output_workflow.json>")
        sys.exit(1)
    input_path, prompt, output_path = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(input_path, 'r') as f:
        wf = json.load(f)
    # Find CLIPTextEncode node (id 2) and set its text
    for node in wf.get('nodes', []):
        if node.get('type') == 'CLIPTextEncode' and node.get('id') == 2:
            node['inputs']['text'] = prompt
            break
    with open(output_path, 'w') as f:
        json.dump(wf, f, indent=2)

if __name__ == '__main__':
    main()