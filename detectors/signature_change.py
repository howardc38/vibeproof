#!/usr/bin/env python3
"""Raise a claim for each call site left at a signature's old arity.

Conditional on there being a diff base and on something actually having been
tightened. `kernel.analysis.signature_change.scan` is the same function
`checkers/signature_change.py` calls, and `before_of` is built the same way --
`git show <base>:<rel>` -- because a detector and its checker disagreeing about
what the previous version said is the failure this pairing exists to prevent.

The checker exits 4 with "no diff base, so nothing can be said to have been
tightened", and UNSUPPORTED is not terminal. Raising this unconditionally would
hang an unanswerable claim on every task started outside a diff.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel.analysis import signature_change  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--subject", required=True)
    p.add_argument("--facts")
    p.add_argument("--out")
    a = p.parse_args()
    try:
        s = json.loads(Path(a.subject).read_text())
        root = Path(s["repo_root"])
        base = s.get("diff_base") or ""
    except Exception as exc:                                    # noqa: BLE001
        print(f"cannot read subject: {exc}", file=sys.stderr)
        return 3
    if not base:
        return 0

    d = subprocess.run(["git", "diff", "--name-only", base], cwd=root,
                       capture_output=True, text=True)
    if d.returncode != 0:
        return 0
    from kernel.analysis.subject_files import TS_SUFFIXES
    changed = [f for f in d.stdout.splitlines() if f.endswith(".py")]
    changed_ts = [f for f in d.stdout.splitlines() if f.endswith(TS_SUFFIXES)]

    def before_of(rel):
        r = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=root,
                           capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None

    try:
        found = (signature_change.scan(root, before_of, changed, subject=s) or [])
        found += (signature_change.ts_scan(root, before_of, changed_ts,
                                           subject=s) or [])
    except Exception as exc:                                    # noqa: BLE001
        print(f"detector failed: {exc}", file=sys.stderr)
        return 3
    for caller, line, symbol, given, needs in found:
        print(f'V4-CLAIM: kind=signature-change file={caller} symbol={symbol} '
              f'line={line} variant=old_arity '
              f'note="calls {symbol} with {given}, it now requires {needs}"')
    if a.out:
        Path(a.out).write_text(json.dumps({"n": len(found)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
