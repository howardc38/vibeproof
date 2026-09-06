#!/usr/bin/env python3
"""Build or verify a public distribution from one committed private revision.

No network calls or pushes. The manifest owns every exported path; the policy
is an explicit allowlist. Private operational records are never selected.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys

MANIFEST = ".public-release.json"
POLICY = "publishing/public-files.json"
FORBIDDEN = (".git", ".v4/risks", ".v4/deferred", ".v4/ledger.db",
             ".v4/ledger_export.jsonl", ".v4/chain_head.json", "publishing/migration",
             "docs/launch/publish", "docs/launch/OWNER_HANDOFF.zh-TW.md")


class ExportError(RuntimeError):
    pass


def git(root, *args, binary=False):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if result.returncode:
        raise ExportError((result.stderr or result.stdout).decode(errors="replace").strip()
                          or f"git {args[0]} exited {result.returncode}")
    return result.stdout if binary else result.stdout.decode().strip()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(path):
    p = PurePosixPath(path)
    if not path or p.is_absolute() or ".." in p.parts or "\\" in path or str(p) != path:
        raise ExportError(f"unsafe path: {path}")
    return path


def forbidden(path):
    path = path.casefold()
    return any(path == x or path.startswith(x + "/") or
               x.endswith(".jsonl") and path.startswith(x + ".")
               for x in FORBIDDEN) or path.startswith(".v4/ledger.db") or any(
                   x.casefold() == ".git" or x == ".env" or x.startswith(".env.")
                   for x in PurePosixPath(path).parts)


def tree(root, revision):
    out = {}
    for row in git(root, "ls-tree", "-rz", revision, binary=True).split(b"\0"):
        if row:
            metadata, path = row.split(b"\t", 1)
            mode, kind, sha = metadata.decode().split()
            out[safe_path(path.decode())] = {"mode": mode, "kind": kind, "blob": sha}
    return out


def source_bytes(root, revision, path):
    return git(root, "show", f"{revision}:{safe_path(path)}", binary=True)


def plan(root, revision, public_parent):
    revision = git(root, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}")
    policy_raw = source_bytes(root, revision, POLICY)
    policy = json.loads(policy_raw)
    if policy.get("schema") != 1:
        raise ExportError("unsupported publishing policy")
    tracked = tree(root, revision)
    selected = {}
    patterns = policy["include"]
    for path, entry in tracked.items():
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns):
            if forbidden(path):
                raise ExportError(f"policy selects private operational path: {path}")
            if entry["kind"] != "blob" or entry["mode"] not in ("100644", "100755"):
                raise ExportError(f"only regular files may be exported: {path}")
            selected[path] = (path, source_bytes(root, revision, path), entry["mode"])
    for destination, origin in policy.get("overrides", {}).items():
        safe_path(destination); safe_path(origin)
        if forbidden(destination) or forbidden(origin):
            raise ExportError(f"private override path: {origin} -> {destination}")
        entry = tracked.get(origin)
        if not entry or entry["kind"] != "blob" or entry["mode"] not in ("100644", "100755"):
            raise ExportError(f"missing/non-regular override: {origin}")
        selected[destination] = (origin, source_bytes(root, revision, origin), entry["mode"])
    for required in policy["required"]:
        if required not in selected:
            raise ExportError(f"required public file not selected: {required}")
    if MANIFEST in selected:
        raise ExportError("the manifest is generated, not copied")
    manifest = {"schema": 1, "source_revision": revision,
                "public_parent": public_parent, "policy_sha256": digest(policy_raw),
                "exporter_sha256": digest(source_bytes(root, revision, "tools/export_public.py")),
                "files": {path: {"source": origin, "sha256": digest(data), "mode": mode}
                          for path, (origin, data, mode) in sorted(selected.items())}}
    return manifest, selected


def write_export(out, manifest, selected):
    out = Path(out)
    if out.is_symlink() or out.exists() and any(out.iterdir()):
        raise ExportError("export destination must be empty")
    out.mkdir(parents=True, exist_ok=True)
    for path, (_, data, mode) in selected.items():
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o755 if mode == "100755" else 0o644)
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def verify(root, *, source=None):
    root = Path(root).resolve()
    manifest = json.loads((root / MANIFEST).read_text())
    if manifest.get("schema") != 1 or not isinstance(manifest.get("files"), dict):
        raise ExportError("invalid public manifest")
    errors = []
    tracked = set(tree(root, "HEAD")) if (root / ".git").exists() else {
        p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    expected = set(manifest["files"]) | {MANIFEST}
    if tracked != expected:
        errors.append(f"tree differs: extra={sorted(tracked-expected)}, missing={sorted(expected-tracked)}")
    for path, item in manifest["files"].items():
        safe_path(path)
        target = root / path
        if forbidden(path) or target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(root):
            errors.append(f"invalid exported file: {path}"); continue
        if digest(target.read_bytes()) != item["sha256"]:
            errors.append(f"content drift: {path}")
        executable = bool(target.stat().st_mode & 0o111)
        if executable != (item["mode"] == "100755"):
            errors.append(f"mode drift: {path}")
    if source is not None:
        canonical, _ = plan(source, manifest["source_revision"], manifest["public_parent"])
        if canonical != manifest:
            errors.append("public manifest is not the canonical source export")
    if errors:
        raise ExportError("\n".join(errors))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--source", type=Path, default=Path.cwd())
    build.add_argument("--revision", default="HEAD")
    build.add_argument("--public-parent", required=True)
    build.add_argument("--out", type=Path, required=True)
    check = sub.add_parser("verify")
    check.add_argument("--root", type=Path, default=Path.cwd())
    check.add_argument("--source", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "build":
            manifest, selected = plan(args.source, args.revision, args.public_parent)
            write_export(args.out, manifest, selected)
            print(f"exported {len(selected)} files from {manifest['source_revision']}")
        else:
            manifest = verify(args.root, source=args.source)
            print(f"verified {len(manifest['files'])} public files; source {manifest['source_revision']}")
        return 0
    except (ExportError, OSError, ValueError, KeyError) as exc:
        print(f"public export refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
