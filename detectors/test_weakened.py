#!/usr/bin/env python3
"""Did this change make the judge easier?  SPEC.md §2.

`test_command` is the whole oracle for a `test` claim, and `.v4/**` is
protected so nobody edits the command without a signature. The tests themselves
are not protected, and nothing asked about them. So the cheapest way out of a
red `test` claim was never to edit the checker -- that is caught by its hash --
it was to delete the failing test. The suite goes green, the claim is ANSWERED,
and thirteen checkers have nothing to say, because every one of them is looking
at the code rather than at what is judging the code.

This raises a claim when the diff removes test functions without replacing
them. It does not decide whether that was legitimate: deleting a duplicate,
merging two cases, renaming a file are all normal. Deciding is what the
signature is for -- removing a test is exactly the kind of thing that should
carry a name.

Counted by AST, not by line, because a reformatted file is not a weakened one.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# The two questions this asks -- is that a test file, how many live tests does
# this source carry -- lived here as `_is_test_file` and `_count`, and
# `checkers/test_weakened.py` imported both across the layer boundary. They moved
# to `kernel/analysis/`, where every other checker/detector pair already shares,
# so the two programs cannot drift apart about what a test is.
#
# The move left the dispatch behind: which count answers for which suffix was
# still written out here and again in the checker, the same characters twice, so
# adding a language was a two-file edit in the one pair that exists to stay in
# step. `analysis.count_for` is that dispatch now, in the module that holds the
# counts it chooses between.
from kernel.analysis import test_weakened as analysis  # noqa: E402


def _at(root: Path, rev: str, path: str):
    r = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=root,
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--subject", required=True)
    p.add_argument("--facts")
    p.add_argument("--out")
    a = p.parse_args()

    try:
        s = json.loads(Path(a.subject).read_text())
        root = Path(s["repo_root"])
        base = s.get("diff_base") or "HEAD"
    except Exception as exc:                                    # noqa: BLE001
        print(f"cannot read subject: {exc}", file=sys.stderr)
        return 1

    diff = subprocess.run(["git", "diff", "--name-status", base],
                          cwd=root, capture_output=True, text=True)
    if diff.returncode != 0:
        return 0                      # no base to compare against; nothing to say

    findings = []
    for line in diff.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        if not path.endswith((".py", ".go") + analysis.TS_SUFFIXES
                              + analysis.RS_SUFFIXES) \
                or not analysis.is_test_file(path):
            continue

        if status.startswith("D"):
            before = _at(root, base, path)
            n = analysis.count_for(path, before) if before else 0
            if n > 0:
                findings.append((path, "<module>", "file_deleted", n))
            continue

        if status.startswith("M"):
            before = _at(root, base, path)
            now = (root / path).read_text(encoding="utf-8", errors="replace") \
                if (root / path).is_file() else ""
            if before is None:
                continue
            n_before = analysis.count_for(path, before)
            n_now = analysis.count_for(path, now)
            # -1 means it did not parse. A file that stopped parsing is a
            # different problem and not this one's to report.
            if n_before < 0 or n_now < 0:
                continue
            if n_now < n_before:
                findings.append((path, "<module>", "fewer_tests",
                                 n_before - n_now))

    for path, symbol, variant, n in findings:
        print(f'V4-CLAIM: kind=test-weakened file={path} symbol={symbol} '
              f'variant={variant} note="{n} test function(s) fewer than at '
              f'{"the base" if variant == "fewer_tests" else "deletion"}"')

    if a.out:
        Path(a.out).write_text(json.dumps(
            {"findings": [{"file": f, "variant": v, "n": n}
                          for f, _, v, n in findings]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
