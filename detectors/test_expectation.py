#!/usr/bin/env python3
"""Raise a claim when a test's expected value moved and the code did not."""
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
    from kernel.analysis import test_expectation_diff as diff  # noqa: E402

    p = argparse.ArgumentParser()
    p.add_argument("--subject", required=True); p.add_argument("--facts"); p.add_argument("--out")
    a = p.parse_args()
    try:
        s = json.loads(Path(a.subject).read_text())
        root, base = Path(s["repo_root"]), s.get("diff_base") or "HEAD"
    except Exception as exc:                                        # noqa: BLE001
        print(f"cannot read subject: {exc}", file=sys.stderr); sys.exit(3)

    found = diff.scan(root, base)
    for path, name, was, now in (found or []):
        print(f'V4-CLAIM: kind=test-expectation file={path} symbol={name} '
              f'variant=edited note="expected {was} and now expects {now}, and no '
              f'non-test source changed"')
    sys.exit(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
