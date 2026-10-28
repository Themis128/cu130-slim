#!/usr/bin/env node
/**
 * n8n workflow validator CLI for the Cloudless stack.
 *
 * Usage:
 *   node validate-n8n-workflow.js <file.json> [...more files]
 *   node validate-n8n-workflow.js --dir n8n-workflows/
 *   node validate-n8n-workflow.js --live <workflow-id-or-name>
 *   options: --strictness low|medium|high (default medium)
 *
 * Exit 0 when every workflow passes, 1 on any issue — gate before
 * `import:workflow` / `publish:workflow`.
 */

const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");
const { validateWorkflow } = require("./n8n-workflow-validator");

const args = process.argv.slice(2);
let strictness = "medium";
const files = [];
let live = null;
let dir = null;

for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === "--strictness" || a === "-s") strictness = args[++i];
  else if (a === "--dir" || a === "-d") dir = args[++i];
  else if (a === "--live") live = args[++i];
  else if (a === "--help" || a === "-h") {
    console.log("Usage: validate-n8n-workflow.js [--dir DIR|--live ID|file.json ...] [-s low|medium|high]");
    process.exit(0);
  } else files.push(a);
}

if (dir) {
  for (const f of fs.readdirSync(dir)) {
    if (f.endsWith(".json")) files.push(path.join(dir, f));
  }
}

if (live) {
  const tmp = `/tmp/n8n-validate-${Date.now()}.json`;
  try {
    execSync(
      `docker exec n8n n8n export:workflow --id="${live}" --output="${tmp}"`,
      { stdio: "pipe" }
    );
    files.push(tmp);
  } catch (e) {
    console.error(`live export failed for "${live}": ${e.message.slice(0, 200)}`);
    process.exit(2);
  }
}

if (!files.length) {
  console.error("no workflows given — pass file(s), --dir, or --live");
  process.exit(2);
}

let failed = 0;
for (const f of files) {
  let wf;
  try {
    const parsed = JSON.parse(fs.readFileSync(f, "utf8"));
    wf = Array.isArray(parsed) ? parsed[0] : parsed; // export may wrap in []
  } catch (e) {
    console.log(`FAIL ${f}: unreadable JSON — ${e.message.slice(0, 120)}`);
    failed++;
    continue;
  }
  const r = validateWorkflow(wf, { strictness });
  if (r.passed) {
    console.log(`PASS ${path.basename(f)} (${wf.name || wf.id || "?"})`);
  } else {
    failed++;
    console.log(`FAIL ${path.basename(f)} (${r.workflow?.name || "?"}) — ${r.totalIssues} issue(s)`);
    for (const [cat, res] of Object.entries(r.results)) {
      for (const iss of res.issues) console.log(`  [${cat}] ${iss}`);
      for (const sug of res.suggestions) console.log(`    → ${sug}`);
    }
  }
}
process.exit(failed ? 1 : 0);
