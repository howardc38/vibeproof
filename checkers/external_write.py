#!/usr/bin/env python3
"""``external-write`` checker.  SPEC.md §3.

Answers one claim: *after this outbound write, can anything here tell that it
landed (``readback``), and does running it again apply the effect twice
(``replay``)?*  The exit code is the answer -- nothing this program prints is
load-bearing for the verdict.

    checkers/external_write.py --subject /tmp/subject.json [--facts f.json] --out /tmp/out.json

**All three flags are declared.**  ``kernel/runner.py`` passes ``--subject`` and
``--out`` on every invocation and ``--facts`` whenever the repo has a facts
file; an undeclared flag makes ``argparse`` exit 2, which is not in the exit
table, so the kernel reads it as ERROR and the claim is never answered.

Exit codes:

    0    nothing in scope.  Includes "these files perform no outbound write" --
         a genuine, verified clean.
    1    findings.  Every one is named on stdout as file:line.
    4    cannot verify: something in scope is not analysable Python (wrong
         language, missing file, syntax error), or the facts table is unusable.
         Not a pass and not a fail.
    >=5  the checker itself broke.

**Why an unanalysable file is 4 and not 0.**  "I found no problems in the files
I could read" is not "there are no problems".  A file the checker cannot read
makes the answer unknown, and unknown is not a pass.  A definite finding still
outranks a 4: an unreadable sibling cannot turn a found defect back into an
unknown.

**Why a broken facts table is 4 and not 0.**  Scanning with an empty vocabulary
finds no outbound writes anywhere and reports a clean repo.  That is fail-open
in the one place the whole claim kind rests on, so ``kernel.facts`` raises and
this maps the raise to 4.

The subject may narrow the question.  A non-empty ``symbol`` restricts the
verdict to that enclosing function/class and a non-empty ``variant`` to that
variant, so the checker answers exactly the claim the detector raised.  Empty
means "no filter" -- which is what ``kernel/register.py`` sends when it runs the
fixtures, so a red fixture fails on any finding it contains.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kernel import facts as facts_mod  # noqa: E402
from kernel.analysis import external_write as analysis  # noqa: E402
from kernel import baseline  # noqa: E402


#: This checker's own name, and the file `kernel.baseline` keeps its standing
#: debt in. Both spellings have to be the one the registry uses.
KIND = "external-write"


def finding_id(f) -> str:
    """The id `kernel.baseline` files this finding under.  No line number.

    `shape` and `trigger` because `Finding` already says `line` is display
    only and `Claim` collapses to (path, symbol, variant) -- which is the claim,
    and coarser than one finding inside it.

    `kernel.baseline.finding_id` rather than a fourth sha256: SPEC's Baseline
    section says 全部 delta checker 用同一個形狀, 唔准各自發明, and it had
    already been reinvented three times when that was written.
    """
    return baseline.finding_id(KIND, f.path, f.symbol, f.variant, f.shape, f.trigger)

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CANNOT_VERIFY = 4
EXIT_BROKEN = 5

PYTHON_SUFFIXES = {".py", ".pyi"}
GO_SUFFIXES = {".go"}
#: Every file this rule can read. `.py` alone was the list, so a Go repo's
#: subject came back "not a Python file" for every path and the rule answered
#: about nothing at all.
#: Three languages now. This set is what `inspect` agrees to read, and the
#: dispatch further down picks which reader -- a suffix added here without a
#: branch there would hand a TypeScript file to the Python parser and call the
#: SyntaxError unverifiable.
from kernel.analysis.subject_files import TS_SUFFIXES as _TS  # noqa: E402
TS_SUFFIXES = frozenset(_TS)
ANALYSABLE_SUFFIXES = PYTHON_SUFFIXES | GO_SUFFIXES | TS_SUFFIXES


def subject_files_deleted(repo_root, base: str = "") -> set:
    """Paths this change removed, as git reports them.

    Replaces `_why_absent`, which returned a sentence and left the caller to
    treat both kinds of missing the same way. The caller needs the fork, not
    the wording: a deletion is listed and stands aside, a path git never knew
    stops the verdict.

    `base` is the commit this task is judged against, and passing it is not
    optional. `deleted_since` falls back to HEAD, which answers "deleted since
    the last commit" -- true while the work is uncommitted and false the moment
    it is committed. Measured: with the base omitted, this checker reported 26
    subject files as unreadable one commit after they were deleted on purpose,
    which is the exact failure the split was written to end.
    """
    from kernel.analysis import subject_files
    return set(subject_files.deleted_since(repo_root, base))


def file_refs(subject: dict) -> list[str]:
    """Repo-relative paths of ``subject_refs`` entries with ``kind == "file"``."""
    refs = subject.get("subject_refs") or []
    paths = {
        str(ref.get("path"))
        for ref in refs
        if isinstance(ref, dict) and ref.get("kind") == "file" and ref.get("path")
    }
    return sorted(paths)


def load_table(facts_path):
    """``(table, problem)``.  A problem means 4, never a scan with no patterns."""
    try:
        if not facts_path:
            return analysis.default_table(), None
        obj = json.loads(Path(facts_path).read_text(encoding="utf-8"))
        return analysis.table_from(obj), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"facts file unreadable: {exc.__class__.__name__}"
    except json.JSONDecodeError as exc:
        return None, f"facts file is not valid JSON: {exc}"
    except facts_mod.FactsError as exc:
        return None, str(exc)


def inspect(repo_root: Path, rel_paths, table, base: str = ""):
    """(findings, unverifiable, deleted) -- both lists are [(path, why)].

    ``deleted`` is separate from ``unverifiable`` because the two settle
    differently: a file this change removed issues no write and does not hold
    the verdict, while a path git never knew is a subject written wrong and
    still does.
    """
    findings: list[analysis.Finding] = []
    unverifiable: list[tuple[str, str]] = []
    deleted: list[tuple[str, str]] = []
    for rel in rel_paths:
        if Path(rel).suffix not in ANALYSABLE_SUFFIXES:
            unverifiable.append((rel, "not a file this rule can read"))
            continue
        path = repo_root / rel
        if not path.is_file():
            # A subject file this change deleted is not a file that could not
            # be read. `subject_files.deleted_since` asks git which it is: a
            # path the diff reports as `D` was removed on purpose, while a path
            # git never knew is a subject written wrong.
            #
            # They settle differently, which is why they no longer share a
            # bucket. A file that is not in the tree issues no outbound write,
            # so it is listed and then stands aside. A path git never knew is a
            # subject written wrong, and that still cannot be verified.
            #
            # Measured on a sibling checker before this: a claim whose subject
            # held 22 deleted files and 4 live ones returned 4 on the strength
            # of the 22, while `derive._retract_orphans` refused to retract it
            # because the 4 were still there -- neither answerable nor
            # retractable. This checker had the same helper and the same fork.
            if rel in subject_files_deleted(repo_root, base):
                deleted.append((rel, "deleted by this change -- a file that is "
                                     "not in the tree issues no write"))
            else:
                unverifiable.append((rel, "file is missing"))
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            unverifiable.append((rel, f"unreadable: {exc.__class__.__name__}"))
            continue
        try:
            read = (analysis.go_analyse_source if path.suffix in GO_SUFFIXES
                    else analysis.ts_analyse_source if path.suffix in TS_SUFFIXES
                    else analysis.analyse_source)
            findings.extend(read(source, path=rel, table=table))
        except SyntaxError as exc:
            unverifiable.append((rel, f"will not parse at line {exc.lineno}"))
    return sorted(findings), unverifiable, deleted


def select(findings, symbol: str, variant: str):
    """Narrow to the claim's own symbol/variant.  Empty string means no filter."""
    out = findings
    if symbol:
        out = [f for f in out if f.symbol == symbol]
    if variant:
        out = [f for f in out if f.variant == variant]
    return out


def _deleted_note(out, deleted) -> None:
    """List what the change removed, and say why it is not part of the verdict."""
    if not deleted:
        return
    out("")
    out(f"Deleted by this change: {len(deleted)} file(s). A file that is not in "
        f"the tree issues no write, so these do not hold the verdict.")
    for rel, why in deleted:
        out(f"  {rel}: {why}")


def report(findings, unverifiable, scanned, symbol, variant, table_source, out=print,
           deleted=(), fid=None) -> int:
    scope = []
    if symbol:
        scope.append(f"symbol={symbol}")
    if variant:
        scope.append(f"variant={variant}")
    scope_note = f" (narrowed to {', '.join(scope)})" if scope else ""

    if findings:
        out(f"FAIL: {len(findings)} external-write finding(s){scope_note}.")
        out("")
        for f in findings:
            out(f"  {f.path}:{f.line}  in {f.symbol}  [{f.variant}/{f.shape}]")
            out(f"      {f.detail}")
            if fid is not None:
                out(f"      id {fid(f)}  -- to accept it, add that to "
                    f"{baseline.where(KIND)}")
            out("")
        out(
            "A `readback` finding means nothing in this file establishes that the "
            "write landed; a `replay` finding means running the same operation twice "
            "applies the effect twice."
        )
        if unverifiable:
            out("")
            out("Also unverifiable (did not change the verdict):")
            for rel, why in unverifiable:
                out(f"  {rel}: {why}")
        _deleted_note(out, deleted)
        return EXIT_FAIL

    if unverifiable:
        out(f"CANNOT VERIFY: {len(unverifiable)} subject file(s) are not analysable Python.")
        for rel, why in unverifiable:
            out(f"  {rel}: {why}")
        out("")
        out(
            "No external-write finding in the files that could be read, but that is "
            "not a clean bill of health for the ones that could not."
        )
        _deleted_note(out, deleted)
        return EXIT_CANNOT_VERIFY

    if not scanned:
        # A subject that is nothing but deletions is an answer, not a shrug.
        # There is no file left to issue a write, and saying CANNOT VERIFY here
        # is what left such a claim stuck: never answerable, and not retractable
        # either while any sibling path survived.
        if deleted:
            out(f"PASS: {len(deleted)} file(s) removed by this change and "
                f"nothing left to analyse; a deletion issues no outbound write.")
            for rel, why in deleted:
                out(f"  {rel}: {why}")
            return EXIT_PASS
        out("CANNOT VERIFY: the subject named no files to analyse.")
        return EXIT_CANNOT_VERIFY

    out(f"PASS: {len(scanned)} source file(s) analysed{scope_note}; no finding.")
    for rel in scanned:
        out(f"  {rel}")
    out("")
    out(
        f"Outbound vocabulary: {table_source}. Every outbound write in these files "
        "either keeps what it returned or sits in a scope that reads the outside "
        "world back, and none is replayed without a dedupe identity that survives "
        "the replay. Files with no outbound write pass for the same reason: there "
        "is nothing here to leave unconfirmed."
    )
    _deleted_note(out, deleted)
    return EXIT_PASS


def machine_payload(code, findings, scanned, unverifiable, symbol, variant,
                    table_source, deleted=()):
    """What ``--out`` gets.  The exit code stays the verdict; this is detail."""
    return {
        "claim_kind": "external-write",
        "exit_code": code,
        "verdict": {0: "PASS", 1: "FAIL", 4: "CANNOT_VERIFY"}.get(code, "ERROR"),
        "scope": {"symbol": symbol, "variant": variant},
        "table": table_source,
        "scanned": list(scanned),
        "unverifiable": [{"path": p, "why": w} for p, w in unverifiable],
        "deleted": [{"path": p, "why": w} for p, w in deleted],
        "findings": [
            {
                "path": f.path,
                "line": f.line,
                "symbol": f.symbol,
                "variant": f.variant,
                "shape": f.shape,
                "trigger": f.trigger,
                "trigger_line": f.trigger_line,
                "detail": f.detail,
            }
            for f in findings
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="external-write claim checker")
    parser.add_argument("--subject", required=True, help="path to the subject JSON")
    parser.add_argument("--facts", help="path to the repo's facts file")
    parser.add_argument("--out", help="path to write the structured report to")
    parser.add_argument("--emit-baseline", action="store_true",
                        help="print a baseline covering the whole repo and stop; "
                             "writes nothing")
    args = parser.parse_args(argv)

    subject = json.loads(Path(args.subject).read_text(encoding="utf-8"))
    repo_root = Path(subject["repo_root"])
    symbol = str(subject.get("symbol") or "")
    variant = str(subject.get("variant") or "")

    table, problem = load_table(args.facts)
    if table is None:
        print(f"CANNOT VERIFY: {problem}")
        print("")
        print(
            "Scanning with no outbound vocabulary would report every repo clean, so "
            "an unusable table is an unknown answer rather than a pass."
        )
        if args.out:
            Path(args.out).write_text(
                json.dumps(
                    {
                        "claim_kind": "external-write",
                        "exit_code": EXIT_CANNOT_VERIFY,
                        "verdict": "CANNOT_VERIFY",
                        "problem": problem,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        return EXIT_CANNOT_VERIFY

    if args.emit_baseline:
        # The whole repo, not the subject: a baseline built from one task's
        # files would forgive those and leave every other pre-existing finding
        # to arrive on whoever touches that file next -- which is the shape
        # this exists to end. `subject_files.tracked` is the one answer to
        # "which files are this repo's"; asking it again here by hand is how
        # `derive_exclude` came to be ignored in five separate places.
        from kernel.analysis import subject_files
        rels = subject_files.tracked(subject, repo_root, ANALYSABLE_SUFFIXES)
        found, _unverifiable, _deleted = inspect(repo_root, rels, table, "")
        print(baseline.block(
            {"id": finding_id(f), "path": f.path, "symbol": f.symbol,
             "variant": f.variant, "shape": f.shape, "note": ""}
            for f in found))
        return EXIT_PASS

    rels = file_refs(subject)
    # The task's own base, not HEAD. See `subject_files_deleted`.
    base = str(subject.get("diff_base") or "")
    findings, unverifiable, deleted = inspect(repo_root, rels, table, base)
    analysable = [r for r in rels if Path(r).suffix in ANALYSABLE_SUFFIXES]
    scanned = [r for r in analysable if r not in {u[0] for u in unverifiable}]
    selected = select(findings, symbol, variant)
    try:
        # `complete` is false whenever the subject named files: a scoped run has
        # not looked everywhere the baseline could match, so the rest of the
        # repo's entries are not stale, they are out of view.
        selected, _carried, notes = baseline.forgive(
            repo_root, KIND, selected, finding_id, complete=not rels)
    except baseline.Unreadable as exc:
        print(f"{exc}", file=sys.stderr)
        return EXIT_CANNOT_VERIFY
    for note in notes:
        print(note)
    source = getattr(table, "source", "<builtin>")

    code = report(selected, unverifiable, scanned, symbol, variant, source,
                  deleted=deleted, fid=finding_id)
    if args.out:
        Path(args.out).write_text(
            json.dumps(
                machine_payload(code, selected, scanned, unverifiable, symbol,
                                variant, source, deleted),
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # the checker itself broke -- never mistaken for a verdict
        print(f"external-write checker broke: {exc!r}", file=sys.stderr)
        sys.exit(EXIT_BROKEN)
