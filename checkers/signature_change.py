#!/usr/bin/env python3
"""Every call site threads the newly required parameter.  SPEC.md §3.

Exit: 0 nothing was tightened, or every caller followed | 1 a caller did not
| 4 no diff base | >=5 broke.
"""
import argparse, json, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel.analysis import signature_change  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--facts"); ap.add_argument("--out")
    a = ap.parse_args()
    try:
        s = json.loads(Path(a.subject).read_text())
        root = Path(s["repo_root"])
        base = s.get("diff_base") or ""
    except Exception as exc:                                    # noqa: BLE001
        print(f"cannot read subject: {exc}", file=sys.stderr); return 5
    if not base:
        print("no diff base, so nothing can be said to have been tightened")
        return 4

    d = subprocess.run(["git", "diff", "--name-only", base], cwd=root,
                       capture_output=True, text=True)
    if d.returncode != 0:
        print("no diff base, so nothing can be said to have been tightened")
        return 4
    from kernel.analysis.subject_files import TS_SUFFIXES
    changed = [f for f in d.stdout.splitlines() if f.endswith(".py")]
    changed_ts = [f for f in d.stdout.splitlines() if f.endswith(TS_SUFFIXES)]

    def before_of(rel):
        r = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=root,
                           capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None

    try:
        found = signature_change.scan(root, before_of, changed, subject=s)
        found += signature_change.ts_scan(root, before_of, changed_ts, subject=s)
    except Exception as exc:                                    # noqa: BLE001
        print(f"checker failed: {exc}", file=sys.stderr); return 5

    if a.out:
        Path(a.out).write_text(json.dumps({"stale_calls": [
            {"file": f, "line": n, "symbol": sym, "given": g, "needs": w}
            for f, n, sym, g, w in found]}, indent=2))
    if found:
        print(f"FAIL: {len(found)} call site(s) left at the old arity.\n")
        for f, n, sym, g, w in found:
            print(f"  {f}:{n}  {sym}() called with {g}, now needs {w}")
        print("\nA required parameter added is not finished until every caller "
              "threads it. Each one that does not is a TypeError the first time "
              "that path runs, which may be in production.")
        return 1
    print("no function gained a required parameter, or every caller followed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
