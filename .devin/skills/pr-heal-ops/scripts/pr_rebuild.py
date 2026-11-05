#!/usr/bin/env python3
"""Rebuild a conflicted PR cleanly on top of master (API-only, no git push).

  GH_REPO=owner/repo pr_rebuild.py <PR#> [--close]

Fetches each changed file at the PR head ref, keeps only those that differ
from or are missing on master, commits them onto latest master, opens a new
PR with auto-merge, and (with --close) closes the original as superseded.
"""
import base64, json, os, subprocess, sys, argparse

def gh(path, payload=None, method="POST"):
    args = ["gh", "api", path]
    if payload is not None:
        args += ["-X", method, "--input", "-"]
    r = subprocess.run(args, capture_output=True, text=True,
                       input=json.dumps(payload) if payload is not None else None)
    if r.returncode != 0:
        return None
    return json.loads(r.stdout) if r.stdout.strip() else {}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pr"); ap.add_argument("--close", action="store_true")
    args = ap.parse_args()
    REPO = os.environ.get("GH_REPO") or json.loads(subprocess.run(
        ["gh","repo","view","--json","nameWithOwner","-q",".nameWithOwner"],
        capture_output=True,text=True).stdout or "null")
    pr = gh(f"repos/{REPO}/pulls/{args.pr}") or {}
    head = pr["head"]["ref"]
    files = gh(f"repos/{REPO}/pulls/{args.pr}/files?per_page=300") or []
    master = gh(f"repos/{REPO}/git/refs/heads/master")["object"]["sha"]
    items, skipped = [], []
    for f in files:
        if f.get("status") == "removed":
            skipped.append(f["filename"]); continue
        prv = gh(f"repos/{REPO}/contents/{f['filename']}?ref={head}") or {}
        mst = gh(f"repos/{REPO}/contents/{f['filename']}?ref=master") or {}
        if prv.get("sha") and prv.get("sha") == mst.get("sha"):
            skipped.append(f["filename"]); continue
        data = gh(f"repos/{REPO}/git/blobs/{prv['sha']}") if prv.get("sha") else None
        if not data:
            skipped.append(f["filename"]); continue
        items.append({"path": f["filename"], "mode": "100644", "type": "blob",
                      "sha": gh(f"repos/{REPO}/git/blobs",
                        {"encoding":"base64","content":data["content"]})["sha"]})
    if not items:
        print("nothing to rebuild — all changed files already on master"); return
    tree = gh(f"repos/{REPO}/git/trees",
              {"base_tree": gh(f"repos/{REPO}/git/commits/{master}")["tree"]["sha"],
               "tree": items})
    c = gh(f"repos/{REPO}/git/commits",
           {"message": pr.get("title","rebuild") + " (conflict-free rebuild)",
            "tree": tree["sha"], "parents": [master]})
    nb = head + "-rebuilt"
    gh(f"repos/{REPO}/git/refs", {"ref": f"refs/heads/{nb}", "sha": c["sha"]})
    subprocess.run(["gh","pr","create","--repo",REPO,"--base","master","--head",nb,
        "--title",pr.get("title","")[:200],
        "--body",f"Conflict-free rebuild of #{args.pr}. Closes #{args.pr}.\n\nGenerated with Devin"],
        capture_output=True,text=True)
    subprocess.run(["gh","pr","merge",nb,"--repo",REPO,"--squash","--auto"],
                   capture_output=True,text=True)
    if args.close:
        subprocess.run(["gh","pr","close",args.pr,"--repo",REPO,
          "--comment",f"Superseded by clean rebuild on {nb}"],capture_output=True,text=True)
    print(f"rebuilt {len(items)} files -> {nb} | skipped {len(skipped)}: {skipped[:8]}")

if __name__ == "__main__":
    main()
