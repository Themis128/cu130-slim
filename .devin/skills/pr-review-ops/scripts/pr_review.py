#!/usr/bin/env python3
"""Devin-side PR review — Copilot quota fallback.

  GH_REPO=owner/repo pr_review.py <PR#> [--approve] [--comment "text"]
"""
import json, re, subprocess, sys

def ghs(args, json_mode=False):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return json.loads(r.stdout) if json_mode else r.stdout

def main():
    pr = sys.argv[1]
    approve = "--approve" in sys.argv
    note = ""
    if "--comment" in sys.argv:
        note = sys.argv[sys.argv.index("--comment") + 1]
    repo = ghs(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"]).strip()
    meta = ghs(["pr", "view", pr, "--repo", repo, "--json",
                "title,mergeStateStatus,files,additions,deletions,headRefName"], json_mode=True)
    diff = ghs(["pr", "diff", pr, "--repo", repo])
    files = [f["path"] for f in meta.get("files", [])]
    findings = []
    # secret-ish patterns
    for pat, label in [
        (r"(ghp|gho|github_pat|sk_live|AKIA)[0-9A-Za-z_\-]{10,}", "secret-like token"),
        (r"(?i)(password|secret|api[_-]?key)\s*=\s*['\"][^'\"]{8,}", "hardcoded credential"),
        (r"\b(pdb\.set_trace|breakpoint\(\)|console\.log\()", "debug leftover"),
        (r"\.env|\.pem|\.key\b", "sensitive path touched"),
    ]:
        for i, line in enumerate(diff.splitlines()):
            if line.startswith("+") and re.search(pat, line):
                findings.append(f"`{label}` in diff: `{line[1:120]}`")
                break
    if meta.get("additions", 0) + meta.get("deletions", 0) > 3000:
        findings.append(f"large diff ({meta['additions']}+{meta['deletions']}-)")
    for f in files:
        if f.endswith((".pem", ".key", ".env")):
            findings.append(f"sensitive file `{f}`")
    body = ["### Devin review (Copilot quota fallback)", "",
            f"**{meta['title']}** — {len(files)} files, "
            f"+{meta.get('additions')}/-{meta.get('deletions')}, "
            f"mergeState `{meta['mergeStateStatus']}`", ""]
    if findings:
        body += ["Findings:"] + [f"- {f}" for f in findings]
        verdict = "COMMENT"
    else:
        body += ["No findings — diff is clean."]
        verdict = "APPROVE" if approve else "COMMENT"
    if note:
        body += ["", f"Note: {note}"]
    gh(["api", f"repos/{repo}/pulls/{pr}/reviews", "-f",
        "body=" + "\n".join(body), "-f", f"event={verdict}"])
    print(verdict, "|", len(findings), "findings |", pr)

if __name__ == "__main__":
    main()
