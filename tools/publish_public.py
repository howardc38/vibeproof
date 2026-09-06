#!/usr/bin/env python3
"""Prepare a public release commit; pushing and opening a PR are explicit.

Run in the private source repository. The public target must be a separate,
clean clone. An unexpected public commit or edit is a refusal, not permission
to overwrite it. The tool never imports private history into that clone.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from export_public import ExportError, MANIFEST, git, plan, tree, verify, write_export

PUBLIC_REPO = "howardc38/vibeproof"
PUBLIC_URLS = {f"git@github.com:{PUBLIC_REPO}.git", f"https://github.com/{PUBLIC_REPO}.git",
               f"https://github.com/{PUBLIC_REPO}"}


def baseline(target, source, expected_head):
    target = Path(target).resolve()
    source = Path(source).resolve()
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ExportError("source and target must be separate non-nested repositories")
    source_common = Path(git(source, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    target_common = Path(git(target, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    if source_common == target_common:
        raise ExportError("source and target must not share a Git object/ledger directory")
    if git(target, "remote", "get-url", "origin") not in PUBLIC_URLS:
        raise ExportError("target origin is not the authorized public repository")
    if git(target, "status", "--porcelain", "--untracked-files=all"):
        raise ExportError("public target has local changes")
    current = git(target, "rev-parse", "HEAD")
    if current != expected_head:
        raise ExportError(f"public HEAD moved: expected {expected_head}, got {current}")
    if (target / MANIFEST).exists():
        # A separately edited file AND manifest must not pass the preflight.
        previous = verify(target, source=source)
        parent = git(target, "rev-parse", "HEAD^")
        if previous["public_parent"] != parent:
            raise ExportError("public HEAD is not the recorded release commit; import its changes first")
    else:
        config = json.loads((source / "publishing/bootstrap.json").read_text())
        if current != config["public_head"]:
            raise ExportError("unrecognized initial public snapshot")
        actual = {path: item["blob"] for path, item in tree(target, "HEAD").items()}
        if actual != config["files"]:
            raise ExportError("initial public tree changed")
    return current


def publish(source, target, revision, expected_head, *, apply=False, push=False, pr=False):
    source, target = Path(source).resolve(), Path(target).resolve()
    if push and not apply or pr and not push:
        raise ExportError("--push needs --apply; --pr needs --push")
    if git(source, "status", "--porcelain", "--untracked-files=all"):
        raise ExportError("commit the canonical source before publishing")
    revision = git(source, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}")
    head = baseline(target, source, expected_head)
    if (target / MANIFEST).exists():
        previous = json.loads((target / MANIFEST).read_text())["source_revision"]
        ancestry = subprocess.run(["git", "-C", str(source), "merge-base", "--is-ancestor",
                                   previous, revision], capture_output=True)
        if ancestry.returncode:
            raise ExportError("source is older than or diverges from the published source; reconcile first")
    manifest, selected = plan(source, revision, head)
    old = tree(target, "HEAD")
    added = sorted(set(selected) - set(old))
    removed = sorted(set(old) - set(selected) - {MANIFEST})
    changed = sorted(path for path in set(selected) & set(old)
                     if git(target, "show", "HEAD:" + path, binary=True) != selected[path][1]
                     or old[path]["mode"] != selected[path][2])
    report = {"source_revision": revision, "public_parent": head,
              "added": added, "changed": changed, "removed": removed, "applied": False}
    if not apply:
        return report
    if MANIFEST in old and not added and not removed and not changed:
        report["unchanged"] = True
        return report
    if push:
        pending = subprocess.run(["gh", "pr", "list", "--repo", PUBLIC_REPO,
            "--state", "open", "--limit", "100", "--json", "headRefName,url"],
            capture_output=True, text=True, check=True)
        candidates = [p["url"] for p in json.loads(pending.stdout)
                      if p["headRefName"].startswith("publish/")]
        if candidates:
            raise ExportError("an existing release PR must be resolved first: " + ", ".join(candidates))
    branch = "publish/" + revision[:12]
    if git(target, "branch", "--list", branch):
        raise ExportError(f"candidate branch already exists: {branch}; inspect it before retrying")
    with tempfile.TemporaryDirectory(prefix="vibeproof-export-") as td:
        export = Path(td) / "tree"
        write_export(export, manifest, selected)
        # All validation and export work completed before any target mutation.
        git(target, "switch", "-c", branch)
        for path in removed:
            deleted = target / path
            deleted.unlink()
            parent = deleted.parent
            while parent != target:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        for path in selected:
            destination = target / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(export / path, destination)
        shutil.copy2(export / MANIFEST, target / MANIFEST)
    paths = sorted(set(selected) | set(removed) | {MANIFEST})
    with tempfile.NamedTemporaryFile(mode="wb") as pathspec:
        pathspec.write(b"\0".join(path.encode() for path in paths) + b"\0"); pathspec.flush()
        git(target, "--literal-pathspecs", "add", "--pathspec-from-file=" + pathspec.name, "--pathspec-file-nul")
    git(target, "diff", "--cached", "--check")
    git(target, "commit", "-m", f"Publish framework source {revision[:12]}")
    verify(target, source=source)
    report.update(applied=True, branch=branch, public_commit=git(target, "rev-parse", "HEAD"))
    if push:
        # Read before write; pushing only a new branch cannot replace public main.
        remote = git(target, "ls-remote", "origin", "refs/heads/main").split()[0]
        if remote != head:
            raise ExportError("public main moved before push; candidate retained for reconciliation")
        git(target, "push", "-u", "origin", branch)
        report["pushed"] = True
    if pr:
        if not push:
            raise ExportError("--pr requires --push")
        body = (f"Publish the reviewed canonical source `{revision}`.\n\n"
                f"Public base: `{head}`.\n\n"
                f"Added {len(added)}, changed {len(changed)}, removed {len(removed)} files. "
                "The manifest records the exact source revision and hashes. Private operational "
                "records and private Git history are excluded.\n\n"
                "Merge with rebase after the public checks pass so the release commit retains "
                "its recorded parent. Do not squash or create an extra merge commit.\n")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md") as f:
            f.write(body); f.flush()
            result = subprocess.run(["gh", "pr", "create", "--repo", PUBLIC_REPO,
                "--base", "main", "--head", branch, "--title", f"Publish source {revision[:12]}",
                "--body-file", f.name], capture_output=True, text=True)
            if result.returncode:
                raise ExportError("branch pushed, PR creation failed; inspect remote before retrying: " + result.stderr)
            report["pr_url"] = result.stdout.strip()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--expected-public-head", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--pr", action="store_true")
    args = parser.parse_args()
    if args.push and not args.apply or args.pr and not args.push:
        parser.error("--push needs --apply; --pr needs --push")
    try:
        print(json.dumps(publish(args.source, args.target, args.revision, args.expected_public_head,
                                 apply=args.apply, push=args.push, pr=args.pr), indent=2))
        return 0
    except (ExportError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"public publish refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
