#!/usr/bin/env python3
"""A test's fake credential has to look fake.  SPEC.md §9.

Lands R-6ababf30 ("Tests touching a secret boundary use fake tokens with a
test prefix, even when the test expects rejection", UNIT_TEST_GENERATION_
GUIDELINE.md L221), moved here from the before-gate.

Not "is this a real credential" -- `secret` asks that, and answers it correctly:
`123:SECRET` is not one. This asks the narrower question that `secret` cannot,
because the answer is the same either way: does a literal shaped like a live
token say, in itself, that it is not one.

Measured: the base handed both arms of the X/Y run `bot_token="123:SECRET"`.
One renamed it while writing an engagement sentence; the other kept it through
eight tasks with `secret` green every time. Two gates carried the rule as words
and neither caught it, because forgetting is not a timing or a perspective
problem.

Exit: 0 clean | 1 a literal that travels as a live one | 4 no test files.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel.analysis import test_token_shape as analysis  # noqa: E402
from kernel import baseline  # noqa: E402
from kernel.analysis import subject_files  # noqa: E402

KIND = "test-token-shape"

def _is_test(rel: str) -> bool:
    p = rel.replace("\\", "/")
    if "/fixtures/" in p or p.startswith("fixtures/"):
        return False          # deliberately-broken sample data is a rule's input
    # `subject_files.is_test` decides, not the filename: this checker is itself
    # `checkers/test_token_shape.py` and matched its own rule by name.
    return subject_files.is_test(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--facts")
    ap.add_argument("--out")
    a = ap.parse_args()

    try:
        s = json.loads(Path(a.subject).read_text())
        root = Path(s["repo_root"])
        refs = [r["path"] for r in s.get("subject_refs", [])
                if r.get("kind") == "file"]
    except Exception as exc:                                    # noqa: BLE001
        print(f"cannot read subject: {exc}", file=sys.stderr)
        return 5

    scoped = bool(refs)
    if not refs:
        # `tracked` and not `git ls-files`: a test file written by this task and
        # not yet committed is exactly where a fresh credential-shaped literal
        # appears, and plain `ls-files` cannot see one.
        refs = subject_files.tracked(s, root)

    from kernel.analysis.subject_files import TS_SUFFIXES
    tests = [f for f in refs
             if f.endswith((".py", ".go") + TS_SUFFIXES) and _is_test(f)]
    if not tests:
        print("no test source in the subject; nothing here writes a fake "
              "credential.")
        return 4

    findings = []
    for rel in tests:
        p = root / rel
        if not p.is_file():
            continue
        if p.suffix == ".go":
            # A credential-shaped literal travels the same way out of a Go test
            # as out of a Python one, and the two regexes that decide are the
            # same two. Only the extractor differs.
            from kernel.analysis import gosource
            shape = gosource.shape(p)
            if shape is not None:
                findings += analysis.go_scan(shape, rel)
            continue
        if p.suffix in TS_SUFFIXES:
            # Third extractor, same two regexes. A credential copied into a
            # TypeScript test travels exactly the way it travels out of a
            # Python one -- by its value, leaving the name behind.
            findings += analysis.ts_scan(
                p.read_text(encoding="utf-8", errors="replace"), rel)
            continue
        findings += analysis.scan(p.read_text(encoding="utf-8", errors="replace"),
                                  rel)

    # Standing debt. Ten of these held the first task on a real adoption, every
    # one in a test file that task had not opened.
    try:
        accepted, _ = baseline.load(root, KIND)
    except baseline.Unreadable as exc:
        print(f"{exc}", file=sys.stderr)
        return 4
    # The literal is in the id and not recoverable from it -- a hash of a
    # credential-shaped string is safe to commit, the string is not.
    fid = lambda f: baseline.finding_id(KIND, f.path, f.symbol,   # noqa: E731
                                        f.literal)
    findings, carried, stale = baseline.partition(findings, accepted, fid,
                                              complete=not scoped)

    if a.out:
        Path(a.out).write_text(json.dumps(
            {"findings": [{**f.as_dict(), "id": fid(f)} for f in findings],
             "carried": [fid(f) for f in carried], "stale": stale}, indent=2))
    if carried:
        print(f"carrying {len(carried)} accepted literal(s) from "
              f"{baseline.where(KIND)}")
    for sid in stale:
        print(f"  baseline entry {sid} matches nothing any more -- delete it")
    if not findings:
        print(f"PASS: {len(tests)} test file(s); every credential-shaped literal "
              f"says it is not one.")
        return 0

    print(f"FAIL: {len(findings)} literal(s) in tests are shaped like a live "
          f"credential and say nothing about being fake.\n")
    for f in findings:
        print(f"  {f.path}:{f.line}  in {f.symbol}")
        # Redacted before it is printed. Checker stdout is stored verbatim in
        # `attempt.stdout` and exported to the committed
        # `.v4/ledger_export.jsonl`, and `runner.redact`'s own table does not
        # cover the telegram shape `\d{2,}:[A-Za-z0-9_-]{4,}` this checker
        # exists for -- so the flagship case `123:SECRET` was written
        # unredacted into a git-tracked file by the checker whose stated
        # purpose is keeping it out of one. The module it imports has provided
        # `redact()` for exactly this and used it only in `as_dict()`.
        print(f"      {analysis.redact(f.literal)}")
        print(f"      id {fid(f)}  -- to accept it, add that to "
              f"{baseline.where(KIND)}")
    print("\nThe value travels and the variable name does not: this string gets "
          "copied into a fixture, a script, a paste, and something scans it. "
          "Put the word in the value -- `123:TEST-NOT-A-REAL-TOKEN`, "
          "`sk-test-…` -- so it says what it is everywhere it goes.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
