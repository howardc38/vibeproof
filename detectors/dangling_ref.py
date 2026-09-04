#!/usr/bin/env python3
"""One claim per import naming something its module does not define."""
import argparse, json, sys
from pathlib import Path
def main() -> int:
    """The program, as a function.

    A module-level script has no symbol a stack frame can be named after,
    so `review.resolve_symbol` refuses every `--symbol` for it and a
    finding raised with none has nothing for `redgreen` to trace -- which
    leaves a signature as the only exit, the outcome that function exists
    to prevent.

    `sys.exit` inside stays `sys.exit`: it raises, so it travels out
    through `main()` unchanged and the verdict is the one it always was.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from kernel.analysis import dangling_ref  # noqa: E402

    p = argparse.ArgumentParser()
    p.add_argument("--subject", required=True); p.add_argument("--facts"); p.add_argument("--out")
    a = p.parse_args()
    s = json.loads(Path(a.subject).read_text())
    root = Path(s["repo_root"])
    files = [r["path"] for r in s.get("subject_refs", []) if r.get("kind") == "file"]
    # Both extractors. The detector raised only the Python half, so a TypeScript
    # dangling reference had no claim to be answered by the checker that can
    # already see it.
    found = (dangling_ref.scan(root, files or None)
             + dangling_ref.ts_scan(root, files or None))
    for f, n, m, name in found:
        print(f'V4-CLAIM: kind=dangling-ref file={f} symbol={name} line={n} '
              f'variant=import note="from {m} import {name} — {m} does not define it"')
    if a.out:
        Path(a.out).write_text(json.dumps({"ok": True}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
