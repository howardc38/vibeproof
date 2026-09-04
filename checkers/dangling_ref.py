#!/usr/bin/env python3
"""Does every same-repo import name something that exists?  SPEC.md §3.

Exit: 0 all resolve | 1 one does not | >=5 broke.

Lands R-59ea1448 ("Removing a public symbol, route path or string contract
must leave no dangling reference" -- baseline/step-1.md L54-59).
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel.analysis import dangling_ref  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--facts"); ap.add_argument("--out")
    a = ap.parse_args()
    try:
        s = json.loads(Path(a.subject).read_text())
        root = Path(s["repo_root"])
        files = [r["path"] for r in s.get("subject_refs", []) if r.get("kind") == "file"]
    except Exception as exc:                                    # noqa: BLE001
        print(f"cannot read subject: {exc}", file=sys.stderr); return 5
    try:
        found = dangling_ref.scan(root, files or None, subject=s)
        go_found, unread = dangling_ref.go_scan(root, files or None, subject=s)
        found += go_found
        found += dangling_ref.ts_scan(root, files or None, subject=s)
    except Exception as exc:                                    # noqa: BLE001
        print(f"checker failed: {exc}", file=sys.stderr); return 5
    if a.out:
        Path(a.out).write_text(json.dumps({"dangling": [
            {"file": f, "line": n, "module": m, "name": s_} for f, n, m, s_ in found]}, indent=2))
    if found:
        print(f"FAIL: {len(found)} reference(s) name something the module does "
              f"not define.\n")
        for f, n, m, name in found:
            print(f"  {f}:{n}  {m}.{name}" if f.endswith(".go")
                  else f"  {f}:{n}  from {m} import {name}")
        print("\nDeleting a symbol is not finished until nothing names it.")
        if any(f.endswith(".go") for f, _, _, _ in found):
            print("  Go: this file carries a build constraint no ordinary "
                  "build satisfies, so `go build` and `go vet` both pass and "
                  "nothing says the symbol is gone until somebody runs it.")
        if any(not f.endswith(".go") for f, _, _, _ in found):
            print("  Python: this import runs the first time that code path is "
                  "reached, which may be in production and may be the first "
                  "time anybody finds out.")
        if unread:
            print(f"\n  and {len(unread)} Go path(s) could not be read at all, "
                  f"so nothing above is a statement about them: "
                  f"{', '.join(unread[:5])}"
                  + (f" and {len(unread) - 5} more" if len(unread) > 5 else ""))
        return 1
    if unread:
        # Exit 4, not 0. `gosource.shape` returns `None` for a helper that
        # would not build, a build that timed out and a `go` that is not
        # installed, and this checker answered all three with "every same-repo
        # reference resolves" -- a sentence about files it never opened. SPEC
        # §3: 4 is "the checker cannot answer", it is not a PASS and it does
        # not close a claim, which is the difference that was being lost.
        print(f"UNSUPPORTED: {len(unread)} Go path(s) could not be read, so "
              f"whether their references resolve is unanswered here.")
        for u in unread:
            print(f"  {u}")
        print("\n`go` absent, a helper that would not build, or a build that "
              "timed out under load -- `kernel/analysis/gosource.py` returns "
              "the same None for all three. Nothing above is a verdict about "
              "these paths.")
        return 4
    print("every same-repo reference resolves")
    return 0


if __name__ == "__main__":
    sys.exit(main())
