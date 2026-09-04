#!/usr/bin/env python3
"""Did the expectation move to fit the result?  SPEC.md §3.

The claim was raised because a test's expected literal changed in a diff that
touched no other source. This asks the same question at answer time, so the two
ways out are the honest ones: put the expectation back, or sign for it.

There is no third way, for the same reason `test-weakened` has none. A checker
that accepted "the old value was wrong" would be a checker that accepts a
sentence, and whether an expectation was wrong is exactly what a name on a
commit is for.

Exit: 0 nothing moved | 1 an expectation moved on its own
| 4 no base to compare against | >=5 broke.
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel.analysis import test_expectation_diff as diff  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--facts"); ap.add_argument("--out")
    a = ap.parse_args()
    try:
        s = json.loads(Path(a.subject).read_text())
        root, base = Path(s["repo_root"]), s.get("diff_base") or "HEAD"
        # Only a test file narrows this. A mini-repo fixture's refs are every
        # file in the case, sorted, so the first one is `.v4/config.json` --
        # narrowing to that asks the question about a file this rule can never
        # be about, and every red case passes. This kind is `staleness: repo`;
        # with no test file named, it judges the whole diff.
        only = next((r["path"] for r in s.get("subject_refs", [])
                     if r.get("kind") == "file"
                     and diff.is_test(r["path"], root=root)), "")
    except Exception as exc:                                    # noqa: BLE001
        print(f"cannot read subject: {exc}", file=sys.stderr); return 5

    try:
        found = diff.scan(root, base, only)
    except Exception as exc:                                    # noqa: BLE001
        print(f"checker failed: {exc}", file=sys.stderr); return 5

    if found is None:
        print("no diff base to compare against"); return 4
    if a.out:
        Path(a.out).write_text(json.dumps(
            {"moved": [{"file": f, "test": t, "was": w, "now": n}
                       for f, t, w, n in found]}, indent=2))
    if found:
        print(f"FAIL: {len(found)} expectation(s) moved and no other source did.\n")
        for f, t, w, n in found:
            print(f"  {f}::{t} expected {w}, now expects {n}.")
        print("\n  Put the expectation back and change the code, or sign: "
              "`v4 risk accept --claim <id> --kind unprovable --why '…'`")
        return 1
    print(f"no expectation moved on its own since {base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
