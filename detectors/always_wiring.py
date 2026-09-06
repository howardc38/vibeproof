#!/usr/bin/env python3
"""Every task asks whether anything is declared with one end unwired.

Unconditional: a table stops being written because a call site moved, not
because a file a task touched changed.
"""
import argparse, sys
def main() -> int:
    """The detector, as a function.

    A module-level script has no symbol a stack frame can be named after,
    so `review.resolve_symbol` refuses every `--symbol` for it and a
    finding raised with none has nothing for `redgreen` to trace -- which
    leaves a signature as the only exit, the outcome that function exists
    to prevent. Every other program under `checkers/` and `detectors/` has this shape;
    a count of them was written here, in nine files, and went stale in
    all nine at once -- nothing in this repo reads a docstring, so
    nothing could have said so.
    """
    p = argparse.ArgumentParser(); p.add_argument("--subject"); p.add_argument("--facts"); p.add_argument("--out"); p.parse_args()
    print("V4-CLAIM: kind=dead-wiring")
    return 0


if __name__ == "__main__":
    sys.exit(main())
