#!/usr/bin/env python3
"""Did this task add a structural violation the baseline does not already
carry?  SPEC.md §3.

Repo-scoped: the files to judge come from `repo_root` + `diff_base`, not from
`subject_refs`.  The code never branches on `subject_refs` being empty, because
that branch is unreachable from the registration gate -- `kernel/register.py`
`_subject_for` fills refs for every file in a directory fixture -- and an
unreachable branch in the one place the checker decides what to look at is
worth less than no branch at all.

Why the question is a delta
---------------------------
SPEC.md §4 gives a claim one answer and no severity.  An absolute lint
therefore fails every task that touches an already-dirty file, and the worker's
only exit is to repay debt the request never mentioned.  Measured on
adopter_a: the tool this replaces reports 200 criticals over 297 production
files, so every task would fail on somebody else's code.

The baseline
------------
`.v4/lint_baseline.json`, committed **when there is one**. It is not required
and this repo does not have it: `load_baseline` reports "nothing is baselined"
on every run, which is the honest state for a repo that has never had a lint
finding. Writing one before the first finding forgives nothing and hides the
next. The two that exist here -- `test-shape` and `test-token-shape` -- were
each written the day their checker went red.

    {"version": 1,
     "findings": [{"id": "9f2c...", "rule": "LINT-PRIVATE-IMPORT",
                   "path": "core/workers/dispatch.py",
                   "detail": "core.brands.resolver._resolve_brands_root",
                   "note": "free text, ignored"}]}

**Only `id` is consulted.**  The other fields exist so the file is reviewable
in a diff: the anchor on this whole mechanism is a person reading the commit
that widened it (SPEC.md §5, §6), and a person cannot review a list of opaque
hashes.  A bare list of id strings is also accepted.

The id is `sha256(rule ‖ path ‖ detail)` and holds no line number -- SPEC.md §1:
a line number in an identity means one added import invents a batch of new
violations and orphans a batch of old ones.  See `finding_id`.

Five rules this file has to hold to, and where each one is:

  baseline missing      no amnesty.  `load_baseline` returns an empty set and
                        every violation in a changed file is new.  A missing
                        file is not "everything is forgiven".
  worker cannot widen   `.v4/**` is in `protected_paths` (SPEC.md §5), so
                        adding an entry needs ACCEPTED_RISK
                        kind=scope_widen_protected and leaves a signed commit.
                        Nothing here writes the file; `--emit-baseline` prints.
  repaid debt           an entry matching nothing is unused, never fatal.
                        Failing somebody for *repairing* a violation is the
                        fastest way to teach them to route around the gate.
  carried debt          printed on PASS, not only on FAIL.  An honest record
                        with no reader is the V3 severity column again.
  rename / move         `git diff -M` supplies the rename map and the entry is
                        looked up under both paths.  A move git does not
                        detect is a known gap, asserted in the tests.

Exit codes (SPEC.md §3)
-----------------------
  0  nothing new; anything found was already in the baseline
  1  this task added at least one violation the baseline does not carry
  4  nothing could be established at all -- no usable diff base
  5  the checker itself broke

A definite finding beats 4.  If some changed file will not parse but another
one holds a real violation, the answer is 1 and the unreadable file is named
alongside it.  "I could not read everything" is not a reason to withhold what
was read.
"""

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel.analysis import structural_lint as lint  # noqa: E402
from kernel import baseline  # noqa: E402
from kernel.config import BASELINE_TEMPLATE  # noqa: E402

BASELINE_PATH = BASELINE_TEMPLATE.format(kind="lint")

# Tests reach into internals on purpose; that is what a test is for.  This is a
# rule about what the lint means, so it lives here rather than in every
# adopter's config.
#
# Location, not filename. `**/test_*.py` and `**/*_test.py` were here too, and
# in a framework whose subject matter is tests they exempted thirteen production
# modules -- `checkers/test_shape.py`, `detectors/test_weakened.py`,
# `kernel/analysis/test_expectation_diff.py` and the rest -- while earning zero
# real tests, because every test in this repo already lives under `tests/`.
# Measured before removing them. A repo that keeps tests beside its source can
# say so: `config.derive_exclude` and the subject's `params.lint_exclude` both
# feed `config_globs` below.
# `.v4/fixtures/**` is here because of where `v4 install` puts them. In this
# repo the fixtures live under `tests/fixtures/**` and `tests/**` covers them;
# in an adopter they are copied to `.v4/fixtures/**` and nothing did. Measured
# on the reference adopter when the name-based globs came out: 106 fixture
# files entered lint scope, every one of them a program written to be wrong on
# purpose. The other 9 that entered were `checkers/test_*.py` and
# `detectors/test_*.py` -- the framework's own programs, which is the point.
TEST_GLOBS = ("tests/**", "test/**", "**/tests/**", "**/conftest.py",
              ".v4/fixtures/**")

PASS, FAIL, UNSUPPORTED, ERROR = 0, 1, 4, 5


#: What this checker still judges. `structural_lint.RULES` holds four; this
#: names the three the eval gives evidence for, so dropping one is a line here
#: rather than a deletion in the analysis module another rule may still want.
RULES_HERE = frozenset({
    lint.PRIVATE_IMPORT,          # 4 FAIL claims across 4 tasks
    lint.PRIVATE_MODULE_ACCESS,   # 2 across 2
    lint.IMPORT_CYCLE,            # 14 across 14 -- the bulk of the value
})


def git(root, *args, check=True):
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def excluded(rel, globs):
    return any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(rel, g.replace("**/", ""))
               for g in globs)


def load_baseline(root):
    """`(ids, entries, problem)`.

    A missing file yields an empty set, so nothing is forgiven.  The tempting
    alternative -- treat "no baseline yet" as "not adopted yet, pass" -- makes
    deleting one file switch the whole check off, and it looks like cleanup in
    a diff.
    """
    path = Path(root) / BASELINE_PATH
    if not path.is_file():
        return set(), [], f"no {BASELINE_PATH}; nothing is baselined"
    # One reader for the file format, in `kernel/baseline.py`. This spelled out
    # its own and silently dropped any entry it did not recognise -- a typo in
    # the baseline read as "that debt was never signed for", which fails the
    # task rather than the file.
    try:
        ids = baseline.load(root, "lint")[0]
    except baseline.Unreadable as exc:
        return set(), [], f"{exc}; nothing is baselined"
    raw = json.loads(path.read_text())
    entries = raw.get("findings", raw.get("accepted", [])) \
        if isinstance(raw, dict) else raw
    kept = [e if isinstance(e, dict) else {"id": e} for e in (entries or [])]
    return ids, kept, None


def changed_paths(root, base):
    """`(changed, renames)` between `base` and the working tree.

    Renames are asked for explicitly.  Without them a moved file reads as a
    file full of brand-new violations, and every baseline entry for it goes
    stale at once -- the same failure SPEC.md §1 keeps line numbers out of a
    claim id to avoid, one level up.
    """
    changed, renames = set(), {}
    fields = [f for f in git(root, "diff", "--name-status", "-M", "-C", "-z",
                             base).split("\0") if f != ""]
    i = 0
    while i < len(fields):
        status = fields[i]
        if status[:1] in ("R", "C") and i + 2 < len(fields):
            renames[fields[i + 2]] = fields[i + 1]
            changed.add(fields[i + 2])
            i += 3
            continue
        path = fields[i + 1] if i + 1 < len(fields) else ""
        if status[:1] != "D" and path:
            changed.add(path)
        i += 2
    for path in git(root, "ls-files", "--others", "--exclude-standard").splitlines():
        if path.strip():
            changed.add(path.strip())
    return changed, renames


def tree_sources(root, globs):
    out = {}
    tracked = [p for p in git(root, "ls-files", "-z", "--", "*.py").split("\0") if p]
    others = [p for p in git(root, "ls-files", "--others", "--exclude-standard",
                             "-z", "--", "*.py").split("\0") if p]
    for rel in sorted(set(tracked) | set(others)):
        if excluded(rel, globs):
            continue
        try:
            out[rel] = (Path(root) / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue                      # deleted in the working tree
    return out


def baseline_block(violations):
    """The rows `lint` puts in a baseline; `kernel.baseline.block` wraps them.

    The wrapper moved to `kernel/baseline.py` when two more kinds needed it --
    that module already owns `load`, and a producer that disagrees with its
    reader forgives nothing. What stays here is the part only this kind knows:
    which fields let somebody reading `.v4/lint_baseline.json` next year tell
    what was forgiven.
    """
    return baseline.block({"id": v.id, "rule": v.rule, "path": v.path,
                           "detail": v.detail, "note": ""} for v in violations)


def config_globs(root, subject):
    config = {}
    path = Path(root) / ".v4" / "config.json"
    if path.is_file():
        config = json.loads(path.read_text())
    return (tuple(config.get("derive_exclude", [])) + TEST_GLOBS
            + ("**/__pycache__/**",)
            + tuple(subject.get("params", {}).get("lint_exclude", [])))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subject", required=True)
    parser.add_argument("--facts")
    parser.add_argument("--out")
    parser.add_argument("--emit-baseline", action="store_true",
                        help="print a baseline covering the whole repo and stop; "
                             "writes nothing")
    args = parser.parse_args()

    try:
        subject = json.loads(Path(args.subject).read_text())
        root = Path(subject["repo_root"])
        base = subject.get("diff_base") or "HEAD"
        globs = config_globs(root, subject)
    except Exception as exc:                      # noqa: BLE001 -- report, do not guess
        print(f"cannot read subject or config: {exc}", file=sys.stderr)
        return ERROR

    def write(payload):
        if args.out:
            Path(args.out).write_text(json.dumps(payload, indent=2, sort_keys=True))

    if args.emit_baseline:
        found, _ = lint.analyse(tree_sources(root, globs))
        print(baseline_block(found))
        return PASS

    baseline_ids, entries, baseline_problem = load_baseline(root)

    try:
        git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    except RuntimeError:
        print(f"UNSUPPORTED: {base!r} does not resolve to a commit here, so the "
              f"set of files this task changed cannot be established. Judging "
              f"the whole repo instead would answer a different question.")
        write({"unsupported": f"no such commit: {base}"})
        return UNSUPPORTED

    try:
        changed, renames = changed_paths(root, base)
        sources = tree_sources(root, globs)
    except RuntimeError as exc:
        print(f"git failed: {exc}", file=sys.stderr)
        return ERROR

    # `kernel_written` for the same reason `checkers/scope.py:70` and
    # `checkers/test.py:98` do it: running `v4 install` while a task is open puts
    # every copied checker and detector into `git diff`, and this reads the diff.
    # A worker who never touched them would be told their task added a violation
    # -- and the answer to it is a baseline entry, which needs a signature on a
    # protected path. A file whose bytes still match what install wrote was not
    # edited by anybody here; one that was edited stops matching and comes back.
    from kernel.hashing import kernel_written
    wanted = sorted(p for p in changed
                    if p.endswith(".py") and not excluded(p, globs)
                    and p in sources and not kernel_written(p, root))
    violations, unparsed = lint.analyse(sources, only=set(wanted))
    # Three rules, not four. `LINT-CONFIG-DUAL-TRUTH` is gone, and the reason is
    # a measurement rather than a preference: across the 113-task eval this kind
    # raised 128 claims and ran 178 times, and that rule produced **zero**
    # findings while the other three produced twenty. It is not a gate that
    # never opened -- it needs no declared fact, it finds config modules by name
    # -- so the zero is the answer, not a symptom.
    #
    # Keeping it would also be the one thing that made this kind worth
    # installing in a Go tree, and that is backwards: `go build` already refuses
    # cross-package unexported access and import cycles (measured, four modules,
    # three refused), so the only rule Go would need is the only rule with no
    # evidence behind it.
    violations = [v for v in violations if v.rule in RULES_HERE]
    # `stale` is the third value the two sibling partitions already returned
    # and this one did not. It is `repaid` computed a second way -- the block
    # below derives the same thing from `entries` -- so taking it here is the
    # one that reads the baseline the split was made against.
    new, carried, stale = lint.partition(violations, baseline_ids,
                                         renames=renames)

    changed_set = set(wanted) | {renames[p] for p in wanted if p in renames}
    repaid = [e for e in entries
              if e.get("id") not in {v.id for v in violations}
              and e.get("path") in changed_set]

    write({
        "base": base,
        "baseline": {"path": BASELINE_PATH, "entries": len(entries),
                     "problem": baseline_problem},
        "python_changed": wanted,
        "renamed": dict(sorted(renames.items())),
        # Reported, never failed on: failing a task because somebody *repaired*
        # a violation is the fastest way to teach people to route around a gate.
        "stale_baseline_entries": stale,
        "unparsed": [{"path": p, "detail": d} for p, d in unparsed],
        "carried": [{"id": v.id, "rule": v.rule, "path": v.path,
                     "detail": v.detail} for v in carried],
        "repaid": repaid,
        "new": [{"id": v.id, "rule": v.rule, "path": v.path, "detail": v.detail,
                 "symbol": v.symbol, "line": v.line} for v in new],
    })

    def report_context():
        """Carried debt is printed whatever the verdict.  SPEC.md §4 makes the
        point about the ship report: an honest record nobody reads is the same
        move as V3's severity column that nothing branched on."""
        if baseline_problem:
            print(f"  baseline: {baseline_problem}")
        else:
            print(f"  baseline: {len(entries)} entry(s) in {BASELINE_PATH}")
        print(f"  carrying: {len(carried)} pre-existing violation(s) in the "
              f"{len(wanted)} python file(s) this task changed")
        for violation in carried:
            print(f"      {violation.rule}  {violation.detail}  "
                  f"({violation.path}:{violation.line})")
        if repaid:
            print(f"  repaid  : {len(repaid)} baseline entry(s) for these files no "
                  f"longer match; drop them from {BASELINE_PATH} when convenient")
            for entry in repaid:
                print(f"      {entry.get('rule', '?')}  {entry.get('detail', entry['id'])}")
        for path, detail in unparsed:
            print(f"  unread  : {path} ({detail})")

    if new:
        # Not "this task adds": with no baseline every violation in a changed
        # file lands here, and most of them are older than the task. That is the
        # intended rule -- a missing baseline is not amnesty -- but the sentence
        # claimed something the checker never established, and two readers spent
        # five commands each disproving it before finding the real answer.
        print(f"FAIL: {len(new)} structural violation(s) in the file(s) this "
              f"task changed that {BASELINE_PATH} does not carry.\n")
        for violation in new:
            print(f"  {violation.render()}")
        print()
        report_context()
        print(f"\nFix the code, or -- if this crossing is deliberate -- land these "
              f"into {BASELINE_PATH}:\n")
        print(baseline_block(new))
        print(f"\n{BASELINE_PATH} is a protected path (SPEC.md §5): landing it "
              f"needs ACCEPTED_RISK kind=scope_widen_protected and leaves a "
              f"commit with a name on it.")
        return FAIL

    if unparsed and not violations:
        print(f"UNSUPPORTED: {len(unparsed)} changed file(s) do not parse and "
              f"nothing else could be established.")
        report_context()
        return UNSUPPORTED

    if not wanted:
        print(f"no python files changed against {base[:12]}")
        report_context()
        return PASS

    print(f"{len(wanted)} python file(s) changed against {base[:12]}; "
          f"no new structural violation")
    report_context()
    return PASS


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:                      # noqa: BLE001
        print(f"structural_lint crashed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(ERROR)
