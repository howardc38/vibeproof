#!/usr/bin/env python3
"""Every task gets asked whether it added a structural violation.  SPEC.md §2.

Unconditional, not triggered on "a `.py` file changed", for the reason SPEC.md
§2 gives: a detector is the single point that decides what gets checked, and a
wrong condition there fails open silently.  The condition that looks obvious
here is wrong in a way that matters -- an import cycle is a property of the
module *graph*, so deleting a file, or renaming one, can close a loop between
two modules the task never opened.  The checker settles that in a few hundred
milliseconds and answers 0 when nothing Python moved, which is cheaper than
being clever about when to ask.
"""
import argparse
import sys

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
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject")
    parser.add_argument("--facts")
    parser.add_argument("--out")
    parser.parse_args()

    print("V4-CLAIM: kind=lint")
    return 0


if __name__ == "__main__":
    sys.exit(main())
