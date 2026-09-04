#!/usr/bin/env python3
"""Check that the design document's claims about the code are still true.

The document and the code describe the same mechanisms, so prose that restates
code drifts away from it. Within a day of consolidating the design into one
file, four of its statements were already wrong -- staleness keyed on HEAD when
the code keys on worktree content, a hash-chain formula missing three fields, a
red-green rule missing its third condition, and a checker contract declaring one
flag where the kernel passes three. That last one is not theoretical: a checker
written from it exited 2 on all sixteen of its own fixtures.

So the document stops restating and starts pointing:

    <!-- pinned: kernel/hashing.py::worktree_digest -->
    <!-- pinned: kernel/ledger.py::CHAIN_SCHEME=v4-chain-3 -->

A pin says "this section is about that symbol, and it exists". With a value, it
also says what the value is. Rename the symbol or change the constant and this
fails, which is the only kind of documentation promise worth making.

What it cannot check is whether the prose around a pin describes the symbol
correctly -- only that the thing being described is there. That is a smaller
promise than "the docs are accurate", and it is the one that can be kept.

Exit: 0 every pin resolves | 1 some do not | 4 nothing to check | >=5 broke.
"""

import argparse
import ast
import json
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel.analysis import spec_pins  # noqa: E402

#: The pin grammar, and the one place that says what a pin is. `spec_coverage`
#: counts them too, and the two disagreed by thirteen -- see
#: `kernel/analysis/spec_pins.py` for what each was missing.
PIN = spec_pins.MARKER


def defined_names(tree):
    """Top-level and one-level-nested defs, classes and assignments."""
    names = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names[node.name] = None
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    try:
                        names[t.id] = ast.literal_eval(node.value)
                    except (ValueError, SyntaxError):
                        names[t.id] = None
                # `A, B, C = 1, 2, 3` defines three names, and this used to see
                # none of them -- so a pin naming one reported that the code no
                # longer had it. Found by pinning a constant this checker itself
                # could not resolve.
                elif isinstance(t, (ast.Tuple, ast.List)):
                    values = (node.value.elts
                              if isinstance(node.value, (ast.Tuple, ast.List))
                              and len(node.value.elts) == len(t.elts) else None)
                    for i, el in enumerate(t.elts):
                        if not isinstance(el, ast.Name):
                            continue
                        v = None
                        if values is not None:
                            try:
                                v = ast.literal_eval(values[i])
                            except (ValueError, SyntaxError):
                                v = None
                        names[el.id] = v
    return names


def check(repo_root: Path, doc_paths):
    problems, checked = [], 0
    for doc in doc_paths:
        if not doc.is_file():
            problems.append(f"{doc}: not found")
            continue
        for path, symbol, want in spec_pins.pins(doc.read_text(encoding="utf-8")):
            checked += 1
            # A pin naming only a file. `README.md` promises that renaming
            # something makes a document fail a check, and for these it did
            # not: the old pattern required a `::symbol`, so thirteen pins --
            # every hook, `tests/run_without_silent_skips.py`, three detectors
            # -- were the one shape that could not fail.
            if symbol is None:
                if not path:
                    problems.append(
                        f"{doc.name}: a `<!-- pinned: -->` with nothing in it. "
                        f"It reads as a pin and resolves to no file and no "
                        f"symbol.")
                elif not (repo_root / path).is_file():
                    problems.append(f"{doc.name} -> {path}: file does not exist")
                continue
            target = repo_root / path
            where = f"{doc.name} -> {path}::{symbol}"
            if not target.is_file():
                problems.append(f"{where}: file does not exist")
                continue
            try:
                names = defined_names(ast.parse(target.read_text(encoding="utf-8")))
            except SyntaxError as exc:
                problems.append(f"{where}: {path} does not parse ({exc})")
                continue
            if symbol not in names:
                problems.append(
                    f"{where}: not defined. The document describes something the "
                    f"code no longer has."
                )
                continue
            if want is not None and want != "":
                got = names[symbol]
                if str(got) != want:
                    problems.append(f"{where}: document says {want!r}, code says {got!r}")
    return checked, problems


def _every_pinned_doc(root: Path):
    """Every tracked markdown file that carries a pin.  SPEC.md §3.

    The repo-scoped fallback, and it exists because naming the documents was an
    allowlist. CI passed `docs/SPEC.md` and nothing else, so a pin written into
    any other document was decorative -- and one was: `RATIONALE.md` pinned
    `CHAIN_SCHEME=v4-chain-1` long after the code moved to `v4-chain-3`, naming
    the version whose hash material a checker can collide. The promise this
    checker exists to keep is "rename a symbol and the document fails a check";
    that promise was being kept for one file out of four.

    Tracked files only: an untracked scratch document is not a claim about the
    code, and .git is not markdown anybody wrote.

    `derive_exclude` from the repo's own config applies here for the reason it
    applies to derivation: a red fixture is deliberately broken, so a pin that
    fails inside one is the fixture working. The list is the repo's declaration
    rather than this checker's, which is what keeps it from being a second
    allowlist replacing the one just removed.
    """
    out = subprocess.run(["git", "ls-files", "*.md", "**/*.md"], cwd=root,
                         capture_output=True, text=True)
    if out.returncode != 0:
        return []
    try:
        excl = json.loads((root / ".v4/config.json").read_text()).get(
            "derive_exclude", [])
    except (OSError, json.JSONDecodeError):
        excl = []
    docs = []
    for rel in sorted(set(out.stdout.split("\n"))):
        if not rel.strip():
            continue
        if any(fnmatch(rel, g) or fnmatch(rel, g.rstrip("/") + "/*")
               for g in excl):
            continue
        path = root / rel
        try:
            if spec_pins.MARKER.search(spec_pins.prose(
                    path.read_text(encoding="utf-8", errors="replace"))):
                docs.append(path)
        except OSError:
            continue
    return docs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--facts")
    ap.add_argument("--out")
    args = ap.parse_args()

    try:
        subject = json.loads(Path(args.subject).read_text())
        root = Path(subject["repo_root"])
        docs = [root / r["path"] for r in subject["subject_refs"]
                if r.get("kind") == "file" and r["path"].endswith(".md")]
        if not docs:
            docs = _every_pinned_doc(root)
    except Exception as exc:                                    # noqa: BLE001
        print(f"could not read subject: {exc}", file=sys.stderr)
        return 5

    if not docs:
        print("no markdown carrying pins anywhere in this repo")
        return 4

    try:
        checked, problems = check(root, docs)
    except Exception as exc:                                    # noqa: BLE001
        print(f"checker failed: {exc}", file=sys.stderr)
        return 5

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"pins_checked": checked, "problems": problems}, indent=2))

    if not checked:
        print("no pins found in the documents given")
        return 4
    if problems:
        print(f"FAIL: {len(problems)} of {checked} pins do not hold.\n")
        for p in problems:
            print(f"  {p}")
        return 1
    print(f"all {checked} pins hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
