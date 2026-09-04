#!/usr/bin/env python3
"""Tests that prove nothing, and fan-out with no ceiling.  SPEC.md §3.

Exit: 0 neither shape present | 1 one is | >=5 broke.

Lands R-2354b225 ("Tests must not assert on source text or SQL file text" --
UNIT_TEST_GENERATION_GUIDELINE.md L116) and R-1a1abd8c ("Fan-out must be
bounded: no gather/all over a runtime-sized collection" --
PERFORMANCE_OPTIMIZATION_GUIDELINE.md L27).
"""
import argparse, ast, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel import baseline  # noqa: E402
from kernel.analysis import subject_files, test_shape  # noqa: E402

KIND = "test-shape"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--facts"); ap.add_argument("--out")
    a = ap.parse_args()
    try:
        s = json.loads(Path(a.subject).read_text())
        root = Path(s["repo_root"])
        variant = s.get("variant") or ""
        files = [r["path"] for r in s.get("subject_refs", []) if r.get("kind") == "file"]
    except Exception as exc:                                    # noqa: BLE001
        print(f"cannot read subject: {exc}", file=sys.stderr); return 5

    # A whole-repo sweep has to skip what the repo did not write. This used to
    # be nine directory names listed here, with a comment saying `.venv` alone
    # took the reference repo past two minutes -- one rule, restated locally by
    # an author who had just been bitten by it. `derive_exclude` states it once.
    paths = ([root / f for f in files] if files else
             [root / f for f in subject_files.tracked(
                 s, root, [".py", ".go"] + list(subject_files.TS_SUFFIXES))])
    problems = []
    for p in sorted(set(paths)):
        if not p.is_file() or p.suffix not in (
                (".py", ".go") + subject_files.TS_SUFFIXES):
            continue
        rel = str(p.relative_to(root)) if root in p.parents or p.parent == root else str(p)
        if p.suffix == ".go":
            # Both rules, asked of Go. `shape` returns None where the toolchain
            # is absent or the file will not parse, and None is not an empty
            # file: `docs/EVIDENCE.md` §4 is eight checkers reporting PASS on a
            # Go repo having parsed no Go, and skipping here is the honest
            # absence that mechanism exists for.
            from kernel.analysis import gosource
            shape = gosource.shape(p)
            if shape is None:
                continue
            problems.extend(test_shape.go_findings(
                rel, shape, subject_files.is_test(rel), variant))
            continue
        if p.suffix in subject_files.TS_SUFFIXES:
            # Third extractor, same two rules. No `None` branch here: there is
            # no toolchain to be missing and no parse to fail -- the TypeScript
            # half reads comment-stripped source, so every answer is a real one.
            problems.extend(test_shape.ts_findings(
                rel, p.read_text(encoding="utf-8", errors="replace"),
                subject_files.is_test(rel), variant))
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        # One answer, in `kernel.analysis.test_shape`. What counts as a test
        # lives in `subject_files` -- the name alone called this very file a
        # test and reported its own `read_text` as "a test that reads source
        # text", 40 findings on this repo, every one a checker doing its job.
        # And the finding tuple itself lives there too, because the detector
        # now derives baseline ids from it and two spellings would be two ids.
        problems.extend(test_shape.findings(rel, src, tree, variant))

    # Standing debt, the same shape as the other four. 32 of these are in this
    # repo's own tests and none of them is a fresh mistake; without a baseline
    # the next task here fails on all 32.
    try:
        accepted, _ = baseline.load(root, KIND)
    except baseline.Unreadable as exc:
        print(f"{exc}", file=sys.stderr)
        return 4
    fid = test_shape.finding_id
    problems, carried, stale = baseline.partition(problems, accepted, fid,
                                              complete=not files)
    if carried:
        print(f"carrying {len(carried)} accepted finding(s) from "
              f"{baseline.where(KIND)}")
    for sid in stale:
        print(f"  baseline entry {sid} matches nothing any more -- delete it")

    if a.out:
        Path(a.out).write_text(json.dumps(
            {"problems": [{"file": f_, "symbol": sy, "line": ln, "why": w,
                           "variant": v, "id": fid((f_, sy, ln, w))}
                          for f_, sy, ln, w, v in problems],
             "carried": [fid(t) for t in carried],
             "stale": stale}, indent=2))
    if problems:
        print(f"FAIL: {len(problems)}.\n")
        for rel_, sym, line, why, _ in problems:
            print(f"  {rel_}:{line}  in {sym}  {why}")
            print(f"      id {fid((rel_, sym, line, why))}  -- to accept it, "
                  f"add that to {baseline.where(KIND)}")
        return 1
    print("no source-text assertion and no unbounded fan-out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
