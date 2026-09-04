#!/usr/bin/env python3
"""Every task's tests must pass.  Three lines, because SPEC.md §2 says a new
rule is a new file -- hard-coding this list in the kernel is how 4,308 lines
became 37,511."""
import argparse, sys
def main() -> int:
    """The detector, as a function.

    A module-level script has no symbol a stack frame can be named after,
    so `review.resolve_symbol` refuses every `--symbol` for it and a
    finding raised with none has nothing for `redgreen` to trace -- which
    leaves a signature as the only exit, the outcome that function exists
    to prevent. Twenty-six siblings already have this shape.
    """
    p = argparse.ArgumentParser(); p.add_argument("--subject"); p.add_argument("--facts"); p.add_argument("--out"); p.parse_args()
    print("V4-CLAIM: kind=test")
    return 0


if __name__ == "__main__":
    sys.exit(main())
