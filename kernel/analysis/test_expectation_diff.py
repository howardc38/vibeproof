"""The diff half of `test-expectation`, shared by its detector and checker.

They must not disagree about what the diff contains, so there is one walk. The
`bundle-secret` fixture set showed what the alternative costs: a detector and a
checker asking different questions were given one set of cases, and half of them
were wrong by definition.
"""

import subprocess
from pathlib import Path

from . import test_expectation as expect


# Public: `checkers/test_expectation.py` needs the same answer this module uses,
# and it was reaching in as `diff._is_test`. A checker and its analysis
# disagreeing about what a test file is would raise a claim nothing can answer,
# so there is one function and it has a name anybody may say.
def is_test(path: str, source: str = None, root=None) -> bool:
    """`subject_files.is_test`.  One of the six that answered separately.

    The extension check this carried is the owner's first line; what it did not
    have is the source inspection that tells a test from a program that reads
    tests.
    """
    from .subject_files import is_test as _owner
    return _owner(path, source, root)


def _at(root: Path, rev: str, path: str):
    r = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=root,
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def scan(root: Path, base: str, only_file: str = ""):
    """[(path, test_name, before, now)] -- or None when there is no base.

    The conjunct lives here: if any non-test source changed, the answer is empty.
    An expectation that moved alongside the code it judges is ordinary work, and
    a rule that cannot tell those apart is a rule that fires on every real
    change and then gets removed.
    """
    root = Path(root)
    diff = subprocess.run(["git", "diff", "--name-status", base], cwd=root,
                          capture_output=True, text=True)
    if diff.returncode != 0:
        return None

    changed_paths, tests = [], []
    for line in diff.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        # The conjunct below asks whether any *non-test* source moved, so this
        # list has to hold every language the repo writes source in. Leaving TS
        # out did not make the rule quieter there -- it made it blind in both
        # directions at once: a TS test whose expectation moved was never
        # examined, and a TS source file that moved beside a Python test never
        # cancelled the claim.
        from .subject_files import RS_SUFFIXES, TS_SUFFIXES
        # The second place a language is named. The dispatch below grew a
        # `.rs` branch and this did not, so every Rust fixture reached the
        # checker, was filtered out here, and came back exit 0 -- a green that
        # meant "never looked".
        if not path.endswith((".py", ".go") + TS_SUFFIXES + RS_SUFFIXES):
            continue
        changed_paths.append(path)
        if status.startswith("M") and is_test(path, root=root):
            tests.append(path)

    if any(not is_test(p, root=root) for p in changed_paths):
        return []                     # the code moved too; not this rule

    out = []
    for path in tests:
        if only_file and path != only_file:
            continue
        before = _at(root, base, path)
        now = (root / path).read_text(encoding="utf-8", errors="replace") \
            if (root / path).is_file() else ""
        if before is None:
            continue
        from .subject_files import TS_SUFFIXES as _ts
        if path.endswith(".rs"):
            moved = expect.rs_changed(before, now)
        elif path.endswith(".go"):
            moved = expect.go_changed(before, now)
        elif path.endswith(_ts):
            moved = expect.ts_changed(before, now)
        else:
            moved = expect.changed(before, now)
        for name, was, now_lits in moved:
            out.append((path, name, was, now_lits))
    return out
