#!/usr/bin/env python3
"""Does any import cross a layer the wrong way?  SPEC.md §3.

`.v4/layers.json` names the layers and the edges that are allowed. Every other
cross-layer import is a violation.

The rule this repo already stated and nothing read: `kernel/analysis/` says in
its own contract that it does no I/O, and one module there imported the whole of
`kernel.facts` -- which opens files and runs git -- for two pure functions. One
violation in the repo, found the first time anything asked.

Not ArchUnit. That library says the same things and is a pytest plugin; a
checker here is stdlib-only, takes three flags and returns one of four exit
classes. The rule language is worth copying, the dependency is not.

Exit: 0 every import stays inside the declaration | 1 one does not
| 4 no `.v4/layers.json` | >=5 broke.
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel.analysis import layers as analysis  # noqa: E402
from kernel import baseline  # noqa: E402
from kernel.config import BASELINE_TEMPLATE  # noqa: E402

BASELINE_PATH = BASELINE_TEMPLATE.format(kind="layer-boundary")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--facts"); ap.add_argument("--out")
    a = ap.parse_args()
    try:
        s = json.loads(Path(a.subject).read_text())
        root = Path(s["repo_root"])
        cfg_path = root / ".v4" / "layers.json"
        if not cfg_path.is_file():
            print("no .v4/layers.json: this repo has not declared its layers")
            return 4
        cfg = json.loads(cfg_path.read_text())
    except Exception as exc:                                    # noqa: BLE001
        print(f"cannot read subject or layers: {exc}", file=sys.stderr); return 5

    try:
        found = analysis.scan(root, cfg, subject=s)
    except Exception as exc:                                    # noqa: BLE001
        print(f"checker failed: {exc}", file=sys.stderr); return 5
    if found is None:
        print(".v4/layers.json declares no layers"); return 4

    # Debt that predates the rule is carried, not forgiven silently: the ids are
    # in git and they are printed on a pass as well as on a failure.
    try:
        carried, _ = baseline.load(root, "layer-boundary")
    except baseline.Unreadable as exc:
        # 4, not 5. The checker is fine; what it cannot establish is what this
        # repo has already signed for, and 4 is the code that says so.
        print(f"{exc}", file=sys.stderr)
        return 4

    fresh, held = [], 0
    seen = set()
    for path, line, src, dst, module in found:
        fid = analysis.finding_id(path, src, dst)
        if (path, src, dst) in seen:
            continue                  # one edge, however many modules express it
        seen.add((path, src, dst))
        if fid in carried:
            held += 1
            continue
        fresh.append((path, line, src, dst, module, fid))

    if a.out:
        Path(a.out).write_text(json.dumps(
            {"violations": [{"file": f, "line": l, "from": s_, "to": d,
                             "module": m, "id": i}
                            for f, l, s_, d, m, i in fresh],
             "carried": held}, indent=2))
    if held:
        print(f"carrying {held} violation(s) from {BASELINE_PATH}")
    if fresh:
        print(f"FAIL: {len(fresh)} import(s) cross a layer the wrong way.\n")
        for f, l, s_, d, m, i in fresh:
            print(f"  {f}:{l}  {s_} -> {d}  ({m})   id={i}")
        print(f"\n  Either the import is wrong, or the edge belongs in "
              f"`.v4/layers.json` -- and that is a line in a diff with a name "
              f"on the commit.")
        return 1
    print("every import stays inside the declaration")
    return 0


if __name__ == "__main__":
    sys.exit(main())
