import sys
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
    sys.exit(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
