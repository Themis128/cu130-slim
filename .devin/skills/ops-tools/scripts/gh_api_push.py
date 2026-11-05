#!/usr/bin/env python3
"""Push repo files -> new branch -> PR via GitHub API (git push not needed).

  GH_REPO=owner/repo gh_api_push.py <staging-dir> <branch> "<msg>" ["<title>"]
"""
import base64, json, os, subprocess, sys

def gh(path, payload=None):
    args = ["gh", "api", path]
    if payload is not None:
        args += ["-X", "POST", "--input", "-"]
    r = subprocess.run(args, capture_output=True, text=True,
                       input=json.dumps(payload) if payload is not None else None)
    if r.returncode != 0:
        print("FAIL", path, r.stderr[:400]); sys.exit(1)
    return json.loads(r.stdout) if r.stdout.strip() else {}

def main():
    staging, branch, message = sys.argv[1], sys.argv[2], sys.argv[3]
    title = sys.argv[4] if len(sys.argv) > 4 else message
    r = subprocess.run(["gh","repo","view","--json","nameWithOwner","-q",".nameWithOwner"],
                       capture_output=True, text=True)
    REPO = os.environ.get("GH_REPO") or r.stdout.strip()
    master = gh(f"repos/{REPO}/git/refs/heads/master")["object"]["sha"]
    base = gh(f"repos/{REPO}/git/commits/{master}")["tree"]["sha"]
    items = []
    for root, _, fs in os.walk(staging):
        for f in fs:
            p = os.path.relpath(os.path.join(root, f), staging).replace("\\", "/")
            data = open(os.path.join(root, f), "rb").read()
            sha = gh(f"repos/{REPO}/git/blobs",
                     {"encoding": "base64", "content": base64.b64encode(data).decode()})["sha"]
            items.append({"path": p, "mode": "100644", "type": "blob", "sha": sha})
    tree = gh(f"repos/{REPO}/git/trees", {"base_tree": base, "tree": items})
    commit = gh(f"repos/{REPO}/git/commits",
                {"message": message, "tree": tree["sha"], "parents": [master]})
    gh(f"repos/{REPO}/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
    out = subprocess.run(["gh", "pr", "create", "--repo", REPO, "--base", "master",
                          "--head", branch, "--title", title, "--body", message],
                         capture_output=True, text=True)
    print(out.stdout or out.stderr)
    subprocess.run(["gh", "pr", "merge", branch, "--repo", REPO, "--squash", "--auto"],
                   capture_output=True, text=True)
    print("auto-merge armed:", branch)

if __name__ == "__main__":
    main()
