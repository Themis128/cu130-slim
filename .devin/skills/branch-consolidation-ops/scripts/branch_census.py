#!/usr/bin/env python3
"""branch_census.py — audit remote branches against PR state BEFORE
attempting any merge/sweep.

Built from the 2026-11-13 session: "merge all branches to production"
looked scary (65 remote branches) but the census showed 0 open PRs,
15 merged-PR branches (already landed, safe to delete) and 49
closed-PR `-rebuilt` artifacts whose feature families had already
landed via canonical "(conflict-free rebuild)" PRs. Merging the
closed branches would have regressed master.

Classification:
  open    — has an OPEN PR; feed to pr-sweep / branch_consolidator
  merged  — PR MERGED; content already on base, safe to delete
  closed  — PR CLOSED unmerged; superseded/rejected — do NOT merge
            blindly. Grouped into feature families below.
  nopr    — no PR ever; needs a decision (stale WIP vs lost work)

Feature-family grouping strips `-rebuilt` suffixes so N rebuild
variants of one feature collapse to one line — the `-rebuilt*`
branches are pr-heal artifacts, not distinct work.

    branch_census.py [--base master] [--repo owner/repo]
                     [--delete-merged] [--json]

--delete-merged removes remote branches whose PR state is MERGED
(recoverable: squash-merged content lives on base; the branch ref is
redundant). Never deletes closed/nopr branches — those need a human
or a per-branch resurrection PR.
"""
import json
import re
import subprocess
import sys


def sh(*args, check=True) -> str:
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)}: {r.stderr[:200]}")
    return r.stdout.strip()


def gh(*args, check=True) -> str:
    return sh("gh", *args, check=check)


def remote_branches() -> list[str]:
    out = sh("git", "ls-remote", "--heads", "origin")
    return sorted(l.split("refs/heads/")[1] for l in out.splitlines())


def pr_states(repo: str | None) -> dict[str, str]:
    args = ["pr", "list", "--state", "all", "--limit", "1000",
            "--json", "headRefName,state"]
    if repo:
        args += ["-R", repo]
    out = gh(*args)
    states: dict[str, str] = {}
    for p in json.loads(out):
        # keep the "most terminal" state if a branch had several PRs
        cur = states.get(p["headRefName"])
        if cur in (None, "CLOSED"):
            states[p["headRefName"]] = p["state"]
    return states


def family(branch: str) -> str:
    return re.sub(r"(-rebuilt)+$", "", branch)


def unique_patches(branch: str, base: str) -> int:
    out = sh("git", "cherry", f"origin/{base}", f"origin/{branch}",
             check=False)
    return sum(1 for l in out.splitlines() if l.startswith("+"))


def main() -> int:
    base = "master"
    repo = None
    delete_merged = "--delete-merged" in sys.argv
    as_json = "--json" in sys.argv
    args = sys.argv[1:]
    for flag, val in (("--base", "base"), ("--repo", "repo")):
        if flag in args:
            i = args.index(flag)
            if val == "base":
                base = args[i + 1]
            else:
                repo = args[i + 1]

    branches = [b for b in remote_branches() if b != base]
    states = pr_states(repo)
    local = set(sh("git", "branch", "--format=%(refname:short)").split())

    census = {"open": [], "merged": [], "closed": {}, "nopr": []}
    for b in branches:
        st = states.get(b)
        if st == "OPEN":
            census["open"].append(b)
        elif st == "MERGED":
            census["merged"].append(b)
        elif st == "CLOSED":
            fam = census["closed"].setdefault(family(b), [])
            fam.append({"branch": b, "local": b in local,
                        "unique_patches": unique_patches(b, base)})
        else:
            census["nopr"].append(b)

    if as_json:
        print(json.dumps(census, indent=2))
    else:
        print(f"base={base} remote-branches={len(branches)}")
        print(f"  OPEN PRs        : {len(census['open'])}")
        for b in census["open"]:
            print(f"    {b}")
        print(f"  MERGED (landed) : {len(census['merged'])}"
              "  -> safe to delete")
        for b in census["merged"]:
            print(f"    {b}")
        print(f"  CLOSED families : {len(census['closed'])}")
        for fam, items in sorted(census["closed"].items()):
            loc = sum(1 for i in items if i["local"])
            up = max(i["unique_patches"] for i in items)
            print(f"    {fam}: {len(items)} variant(s), "
                  f"{loc} local, max unique-patches={up}")
            for i in items:
                print(f"      {'L' if i['local'] else '-'} "
                      f"{i['unique_patches']:>3}  {i['branch']}")
        print(f"  NO PR           : {len(census['nopr'])}")
        for b in census["nopr"]:
            print(f"    {b}")
        print("\nNOTE: 'unique_patches' counts non-merge commits whose "
              "patch-id is absent from base — squash merges inflate it; "
              "verify feature families against master log "
              "'(conflict-free rebuild)' PRs before resurrecting.")

    if delete_merged and census["merged"]:
        sh("git", "push", "origin", "--delete", *census["merged"])
        print(f"\ndeleted {len(census['merged'])} merged remote branches")
    return 0


if __name__ == "__main__":
    sys.exit(main())
