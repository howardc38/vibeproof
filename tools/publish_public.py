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


def verify_imported_public(target, source, imported_revision, public_head):
    """An imported canonical snapshot accounts for every current public byte."""
    previous = json.loads((target / MANIFEST).read_text())
    if previous.get("schema") != 1:
        raise ExportError("invalid prior public manifest")
    previous_revision = git(source, "rev-parse", "--verify", "--end-of-options",
                            previous["source_revision"] + "^{commit}")
    ancestry = subprocess.run(["git", "-C", str(source), "merge-base", "--is-ancestor",
                               previous_revision, imported_revision], capture_output=True)
    if ancestry.returncode:
        raise ExportError("imported revision is older than or diverges from the published source")
    _, selected = plan(source, imported_revision, public_head)
    actual = tree(target, "HEAD")
    actual.pop(MANIFEST, None)
    if set(actual) != set(selected):
        raise ExportError("public tree differs from imported canonical export: "
                          f"extra={sorted(set(actual) - set(selected))}, "
                          f"missing={sorted(set(selected) - set(actual))}")
    changed = [path for path, (_, data, mode) in selected.items()
               if actual[path]["mode"] != mode
               or git(target, "show", "HEAD:" + path, binary=True) != data]
    if changed:
        raise ExportError("public content/mode differs from imported canonical export: "
                          + ", ".join(sorted(changed)))
    # Git status can hide local replacements under skip-worktree/assume-unchanged.
    # The writer uses the checkout, so committed-tree equality alone is insufficient.
    for path, (_, data, mode) in selected.items():
        checkout = target / path
        if (checkout.is_symlink() or not checkout.is_file()
                or not checkout.resolve().is_relative_to(target)):
            raise ExportError(f"invalid public checkout file: {path}")
        if (checkout.read_bytes() != data
                or bool(checkout.stat().st_mode & 0o111) != (mode == "100755")):
            raise ExportError(f"public checkout content/mode drift: {path}")


def baseline(target, source, expected_head, *, imported_public_revision=None):
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
    manifest_path = target / MANIFEST
    manifest_entry = tree(target, "HEAD").get(MANIFEST)
    if manifest_entry is not None or manifest_path.exists() or manifest_path.is_symlink():
        if (manifest_entry is None or manifest_entry["kind"] != "blob"
                or manifest_entry["mode"] not in ("100644", "100755")
                or manifest_path.is_symlink() or not manifest_path.is_file()):
            raise ExportError("public manifest must be a tracked regular file")
        if imported_public_revision is not None:
            verify_imported_public(target, source, imported_public_revision, current)
        else:
            # A separately edited file AND manifest must not pass the preflight.
            previous = verify(target, source=source)
            parent = git(target, "rev-parse", "HEAD^")
            if previous["public_parent"] != parent:
                raise ExportError("public HEAD is not the recorded release commit; import its changes first")
    else:
        if imported_public_revision is not None:
            raise ExportError("import reconciliation requires a prior public manifest")
        config = json.loads((source / "publishing/bootstrap.json").read_text())
        if current != config["public_head"]:
            raise ExportError("unrecognized initial public snapshot")
        actual = {path: item["blob"] for path, item in tree(target, "HEAD").items()}
        if actual != config["files"]:
            raise ExportError("initial public tree changed")
    return current


def publish(source, target, revision, expected_head, *, apply=False, push=False, pr=False,
            imported_public_revision=None):
    source, target = Path(source).resolve(), Path(target).resolve()
    if push and not apply or pr and not push:
        raise ExportError("--push needs --apply; --pr needs --push")
    if git(source, "status", "--porcelain", "--untracked-files=all"):
        raise ExportError("commit the canonical source before publishing")
    revision = git(source, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}")
    if imported_public_revision is not None:
        imported_public_revision = git(source, "rev-parse", "--verify", "--end-of-options",
                                       imported_public_revision + "^{commit}")
        ancestry = subprocess.run(["git", "-C", str(source), "merge-base", "--is-ancestor",
                                   imported_public_revision, revision], capture_output=True)
        if ancestry.returncode:
            raise ExportError("imported revision must be an ancestor of the requested release")
    head = baseline(target, source, expected_head,
                    imported_public_revision=imported_public_revision)
    if (target / MANIFEST).exists():
        previous = json.loads((target / MANIFEST).read_text())["source_revision"]
        ancestry = subprocess.run(["git", "-C", str(source), "merge-base", "--is-ancestor",
                                   previous, revision], capture_output=True)
        if ancestry.returncode:
            raise ExportError("source is older than or diverges from the published source; reconcile first")
    manifest, selected = plan(source, revision, head)
    # New release paths can traverse ignored symlinks absent from the imported
    # tree. Validate every destination component before creating a branch.
    for path in set(selected) | {MANIFEST}:
        destination = target / path
        for component in (destination, *destination.parents):
            if component == target:
                break
            if component.is_symlink():
                raise ExportError(f"unsafe public destination: {path}")
    old = tree(target, "HEAD")
    added = sorted(set(selected) - set(old))
    removed = sorted(set(old) - set(selected) - {MANIFEST})
    changed = sorted(path for path in set(selected) & set(old)
                     if git(target, "show", "HEAD:" + path, binary=True) != selected[path][1]
                     or old[path]["mode"] != selected[path][2])
    report = {"source_revision": revision, "public_parent": head,
              "added": added, "changed": changed, "removed": removed, "applied": False}
    if imported_public_revision is not None:
        report["imported_public_revision"] = imported_public_revision
    if not apply:
        return report
    if (imported_public_revision is None and MANIFEST in old
            and not added and not removed and not changed):
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
    parser.add_argument("--imported-public-revision",
                        help="canonical commit whose export exactly matches the current public tree; "
                             "prove the public contribution was imported before repairing provenance")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--pr", action="store_true")
    args = parser.parse_args()
    if args.push and not args.apply or args.pr and not args.push:
        parser.error("--push needs --apply; --pr needs --push")
    try:
        print(json.dumps(publish(args.source, args.target, args.revision, args.expected_public_head,
                                 apply=args.apply, push=args.push, pr=args.pr,
                                 imported_public_revision=args.imported_public_revision), indent=2))
        return 0
    except (ExportError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"public publish refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
