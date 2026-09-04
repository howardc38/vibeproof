#!/usr/bin/env python3
"""Claims for tests that read source text, and for fan-out with no ceiling.

**Repo-wide, minus the baseline.**  NF-3.

This scanned the task's subject files, so what got looked at was decided by
where the work happened to land. Measured on the reference adopter: a dead test
written 2026-05-21 sat for three months and was found only because a task
happened to edit the same file. Coverage was an accident of scope.

Scanning the whole repo is the fix, and it cannot be done by itself: the same
adopter has 61 findings repo-wide against 24 raised so far, so the next task
would open with 37 claims for debt it did not create. That is the failure
`NF-7` was decided against -- one task paying everyone's bill.

So the baseline decides. It already existed for this kind and the checker
already applied it; the detector now applies it too, and a finding already
forgiven never becomes a claim. What is left is exactly what is new.

`baseline` is applied per (file, variant), because that is the granularity of a
claim -- one claim covers a file's findings of one variant, and the checker
re-scans that file and fails on whichever of them is not forgiven. A file whose
findings are all baselined has nothing to ask about.

Both the finding tuple and its id come from `kernel.analysis.test_shape`. They
used to be spelled in the checker and the claim line was built from a different
expression here; the day the detector started reading the baseline, that would
have been two ids for one finding and every forgiven finding would have come
straight back as a claim.

Exit codes -- ``0`` is the only success value:

    0   scanned (zero claims is still 0)
    3   the detector itself broke, including a baseline file that will not read
"""

import argparse
import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel import baseline  # noqa: E402
from kernel.analysis import subject_files, test_shape  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--subject", required=True)
    p.add_argument("--facts")
    p.add_argument("--out")
    a = p.parse_args()
    try:
        s = json.loads(Path(a.subject).read_text())
        root = Path(s["repo_root"])
    except Exception as exc:                                    # noqa: BLE001
        print(f"cannot read subject: {exc}", file=sys.stderr)
        return 3
    try:
        accepted, _ = baseline.load(root, test_shape.KIND)
    except baseline.Unreadable as exc:
        # Not "nothing is forgiven". A broken baseline read as empty would
        # raise a claim for every standing finding in the repo at once, which
        # is the exact outcome this file exists to avoid.
        print(f"{exc}", file=sys.stderr)
        return 3

    n, carried = 0, 0
    # `subject_files.tracked`, the same list the checker sweeps. A plain
    # `git ls-files` misses `derive_exclude`, and the first thing that costs is
    # this repo's own red fixtures: deliberately broken test files, every one of
    # them a `test-shape` finding, every one of them the fixture doing its job.
    for q in sorted({root / f for f in subject_files.tracked(
            s, root, [".py", ".go"] + list(subject_files.TS_SUFFIXES))}):
        if not q.is_file():
            continue
        try:
            rel = str(q.relative_to(root))
        except ValueError:
            rel = str(q)
        if q.suffix == ".go":
            # The same two rules through Go's own parser. `shape` is `None`
            # where the toolchain is absent or the file will not parse, and a
            # detector that read that as "no findings" would raise nothing and
            # look exactly like a clean repo.
            from kernel.analysis import gosource
            shape = gosource.shape(q)
            if shape is None:
                continue
            found = test_shape.go_findings(rel, shape, subject_files.is_test(rel))
        elif q.suffix in subject_files.TS_SUFFIXES:
            found = test_shape.ts_findings(
                rel, q.read_text(encoding="utf-8", errors="replace"),
                subject_files.is_test(rel))
        else:
            try:
                src = q.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(src)
            except (OSError, SyntaxError):
                continue
            found = test_shape.findings(rel, src, tree)
        fresh = set()
        for f in found:
            if test_shape.finding_id(f) in accepted:
                carried += 1
            else:
                fresh.add(f[4])      # the variant, from the module, not sniffed
        for variant in sorted(fresh):
            note = ("asserts on source text" if variant == "source_assertion"
                    else "fan-out with no ceiling")
            print(f'V4-CLAIM: kind=test-shape file={rel} symbol=<module> '
                  f'variant={variant} note="{note}"')
            n += 1
    if a.out:
        Path(a.out).write_text(json.dumps({"n": n, "carried": carried}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
