#!/usr/bin/env python3
"""Raised when this task touched a test file.

Conditional, not unconditional: a task that changed no test wrote no fake
credential, and a claim raised on it would be a question about somebody else's
code.
"""
import argparse, json, sys
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
    p = argparse.ArgumentParser(); p.add_argument("--subject"); p.add_argument("--facts"); p.add_argument("--out")
    a = p.parse_args()
    try:
        s = json.loads(open(a.subject).read())
        refs = [r["path"] for r in s.get("subject_refs", []) if r.get("kind") == "file"]
    except Exception:
        refs = []
    def a_test(f: str) -> bool:
        """A test file, in either language this reads.

        `_test.go` is the compiler's rule and not a convention: a Go file whose
        name does not end that way is not compiled into the test binary, so the
        name is the whole question. Python has no such rule, which is why its
        half is a list of the three places a test is written.
        """
        if "/fixtures/" in f:
            return False
        if f.endswith("_test.go"):
            return True
        # `subject_files.is_test` owns which files are tests, and it has known
        # the TypeScript conventions since the three test kinds learned them:
        # `.test.` and `.spec.` and `.tests.` by name, `__tests/` and
        # `__tests__/` and `spec/` by position. Repeating the Python line here
        # and not the TS one is how a seventh answer to that question starts.
        from kernel.analysis.subject_files import TS_SUFFIXES, is_test
        if f.endswith(TS_SUFFIXES):
            return is_test(f)
        return f.endswith(".py") and ("test" in f.split("/")[-1]
                                      or "/tests/" in f or f.startswith("tests/"))

    if any(a_test(f) for f in refs):
        print("V4-CLAIM: kind=test-token-shape symbol=<module> variant=fake_says_fake")
    sys.exit(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
