#!/usr/bin/env python3
"""Are the tests still at least as strong as they were?  SPEC.md §3.

The claim was raised because the diff removed test functions. This asks the
same question again at answer time, so the two ways out are the honest ones:
put the coverage back, or sign for the removal.

There is deliberately no third way. A checker that accepted "the author says it
was a duplicate" would be a checker that accepts a sentence, and the whole
argument for this layer is that sentences are not what it judges. Deleting a
test is one of the few things in this system where the right answer really is a
name on a commit.

Exit: 0 nothing was weakened | 1 fewer tests than at the base
| 4 no base to compare against | >=5 broke.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Was `from detectors.test_weakened import _count, _is_test_file` -- the one
# checker in this repo that imported a detector, and it took two private
# names to do it. The judgement moved to `kernel/analysis/`, which is where
# every other checker/detector pair shares it.
#
# `_count` came back as a copy rather than an import: the same dispatch over
# suffixes, character for character, in this file and in the detector. That is
# the drift the move was for -- a suffix added on one side and not the other is
# a detector raising a claim this checker cannot answer -- so the dispatch is
# `analysis.count_for` now, asked of the module that owns the counts.
#
# Asking it again here is still the point. This re-runs the count at answer
# time against the tree as it is; what it must not do is answer it by a second
# program.
from kernel.analysis import test_weakened as analysis  # noqa: E402


def check(root: Path, base: str, only_file: str = ""):
    diff = subprocess.run(["git", "diff", "--name-status", base],
                          cwd=root, capture_output=True, text=True)
    if diff.returncode != 0:
        return None
    problems = []
    for line in diff.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        if not path.endswith((".py", ".go") + analysis.TS_SUFFIXES
                              + analysis.RS_SUFFIXES) \
                or not analysis.is_test_file(path):
            continue
        if only_file and path != only_file:
            continue
        shown = subprocess.run(["git", "show", f"{base}:{path}"], cwd=root,
                               capture_output=True, text=True)
        before = shown.stdout if shown.returncode == 0 else None
        if before is None:
            continue
        n_before = analysis.count_for(path, before)
        if status.startswith("D"):
            if n_before > 0:
                problems.append(
                    f"{path} is gone, and it held {n_before} test function(s). "
                    f"The suite is greener than it was and nothing else in this "
                    f"system would notice.")
            continue
        now = (root / path).read_text(encoding="utf-8", errors="replace") \
            if (root / path).is_file() else ""
        n_now = analysis.count_for(path, now)
        if n_before < 0 or n_now < 0:
            continue
        if n_now < n_before:
            problems.append(
                f"{path} has {n_now} test function(s) and had {n_before}. "
                f"Put them back, or sign: `v4 risk accept --claim <id> "
                f"--kind unprovable --why '<why this coverage is not needed>'`.")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--facts")
    ap.add_argument("--out")
    a = ap.parse_args()

    try:
        s = json.loads(Path(a.subject).read_text())
        root = Path(s["repo_root"])
        base = s.get("diff_base") or "HEAD"
        only = ""
        for r in s.get("subject_refs", []):
            if r.get("kind") == "file":
                only = r["path"]
                break
    except Exception as exc:                                    # noqa: BLE001
        print(f"cannot read subject: {exc}", file=sys.stderr)
        return 5

    try:
        problems = check(root, base, only)
    except Exception as exc:                                    # noqa: BLE001
        print(f"checker failed: {exc}", file=sys.stderr)
        return 5

    if problems is None:
        print("no diff base to compare against")
        return 4

    if a.out:
        Path(a.out).write_text(json.dumps({"problems": problems}, indent=2))
    if problems:
        print(f"FAIL: the tests are weaker than at {base}.\n")
        for p in problems:
            print(f"  {p}")
        return 1
    print(f"no test function was removed since {base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
