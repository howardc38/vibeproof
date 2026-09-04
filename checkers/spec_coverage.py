#!/usr/bin/env python3
"""Is every mechanism the spec describes actually there?  SPEC.md §0.

Exit: 0 everything resolves | 1 something does not | 4 no SPEC.md here | >=5 broke.

The judgement is `kernel/spec_coverage.py`. It was here, all 1052 lines of it,
which meant the only way to ask this checker anything was to spawn a process
and read an exit code. `kernel/` rather than the `kernel/analysis/` SPEC §12
step 1 names, because this judgement's inputs are the kernel and
`.v4/layers.json` allows `analysis` no imports; the module says the rest.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel.spec_coverage import check  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--facts")
    ap.add_argument("--out")
    a = ap.parse_args()

    try:
        subject = json.loads(Path(a.subject).read_text())
        root = Path(subject["repo_root"])
        specs = [root / r["path"] for r in subject.get("subject_refs", [])
                 if r.get("kind") == "file" and r["path"].endswith("SPEC.md")]
        if not specs and (root / "docs" / "SPEC.md").is_file():
            specs = [root / "docs" / "SPEC.md"]
    except Exception as exc:                                    # noqa: BLE001
        print(f"cannot read subject: {exc}", file=sys.stderr)
        return 5

    if not specs:
        print("no SPEC.md in the subject; nothing to resolve")
        return 4

    try:
        problems = []
        for spec in specs:
            problems += check(root, spec.read_text(encoding="utf-8"))
    except Exception as exc:                                    # noqa: BLE001
        print(f"checker failed: {exc}", file=sys.stderr)
        return 5

    if a.out:
        Path(a.out).write_text(json.dumps({"problems": problems}, indent=2))
    if problems:
        print(f"FAIL: {len(problems)} thing(s) the spec describes and the repo "
              f"does not have.\n")
        for p in problems:
            print(f"  {p}")
        return 1
    print("every command, path, kind and mechanism the spec names resolves")
    return 0


if __name__ == "__main__":
    sys.exit(main())
