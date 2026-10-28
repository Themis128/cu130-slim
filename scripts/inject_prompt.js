#!/usr/bin/env node
/**
 * Inject a prompt into a ComfyUI API-format workflow JSON.
 *
 * Supports both UI-format (nodes array) and API-format (keyed by node ID) workflows.
 * The positive prompt is node "2" (CLIPTextEncode) in the marketing pipeline.
 *
 * Usage:
 *   node inject_prompt.js <input_workflow.json> <prompt> <output_workflow.json>
 */
const fs = require('fs');
const path = require('path');

function main() {
  const args = process.argv.slice(2);
  if (args.length !== 3) {
    console.error('Usage: node inject_prompt.js <input_workflow.json> <prompt> <output_workflow.json>');
    process.exit(1);
  }

  const [inputPath, prompt, outputPath] = args;

  let wf;
  try {
    wf = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
  } catch (err) {
    console.error(`Error reading ${inputPath}: ${err.message}`);
    process.exit(1);
  }

  // Detect format: API format has string keys, UI format has "nodes" list
  if (wf.nodes && Array.isArray(wf.nodes)) {
    // UI format
    for (const node of wf.nodes) {
      if (node.type === 'CLIPTextEncode' && node.id === 2) {
        node.inputs.text = prompt;
        break;
      }
    }
  } else {
    // API format — node "2" is the positive CLIPTextEncode
    if (wf['2'] && wf['2'].class_type === 'CLIPTextEncode') {
      wf['2'].inputs.text = prompt;
    }
  }

  try {
    fs.writeFileSync(outputPath, JSON.stringify(wf, null, 2));
    console.log(`Prompt injected into ${outputPath}`);
  } catch (err) {
    console.error(`Error writing ${outputPath}: ${err.message}`);
    process.exit(1);
  }
}

main();
