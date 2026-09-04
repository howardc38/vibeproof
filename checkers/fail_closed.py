#!/usr/bin/env python3
"""``fail-closed`` checker.  SPEC.md §3.

Answers one claim: *does anything in these files swallow the failure of an
outbound effect or an identity/permission decision?*  The exit code is the
answer -- nothing this program prints is load-bearing for the verdict.

    checkers/fail_closed.py --subject /tmp/subject.json

Exit codes:

    0    no fail-open handler in scope.  Includes "these files have no try at
         all" -- a genuine, verified clean.
    1    fail-open handlers found.  Every one is named on stdout as file:line.
    4    cannot verify: something in scope is not analysable Python (wrong
         language, missing file, syntax error).  Not a pass and not a fail.
    >=5  the checker itself broke.

**Why an unanalysable file is 4 and not 0.**  "I found no problems in the files
I could read" is not "there are no problems".  Reporting 0 there is the exact
shape of the defect this checker exists to catch, one level up.  A file the
checker cannot read makes the answer unknown, and unknown is not a pass.

**Why a definite finding still wins over 4.**  If a readable file already
contains a fail-open handler, the verdict is settled: an unreadable sibling
cannot turn a found defect back into an unknown.

The subject may narrow the question.  A non-empty ``symbol`` restricts the
verdict to that enclosing function/class, and a non-empty ``variant`` to that
variant -- this is how the checker answers exactly the claim the detector
raised, without a re-scan of the whole file counting against it.  Empty means
"no filter": every fail-open handler in the subject files counts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kernel.analysis import fail_closed as analysis  # noqa: E402
from kernel.analysis.subject_files import TS_SUFFIXES  # noqa: E402
from kernel import baseline  # noqa: E402


#: This checker's own name, and the file `kernel.baseline` keeps its standing
#: debt in. Both spellings have to be the one the registry uses.
KIND = "fail-closed"


def finding_id(f) -> str:
    """The id `kernel.baseline` files this finding under.  No line number.

    `trigger` is what put the site in scope -- two guards in one function
    with different triggers are two findings. No line and no `detail`: the
    detail carries `(line NNN)`, so an edit above would forgive the wrong one.

    `kernel.baseline.finding_id` rather than a fourth sha256: SPEC's Baseline
    section says 全部 delta checker 用同一個形狀, 唔准各自發明, and it had
    already been reinvented three times when that was written.
    """
    return baseline.finding_id(KIND, f.path, f.symbol, f.variant, f.trigger)

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CANNOT_VERIFY = 4
EXIT_BROKEN = 5

PYTHON_SUFFIXES = {".py", ".pyi"}
GO_SUFFIX = ".go"


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


def inspect(repo_root: Path, rel_paths, base: str = ""):
    """(findings, unverifiable, deleted) -- both lists are [(path, why)].

    ``deleted`` is separate from ``unverifiable`` because the two settle
    differently: a file this change removed holds no handler and does not hold
    the verdict, while a path git never knew is a subject written wrong and
    still does.
    """
    # The repo's own vocabulary, unioned with the shipped one. `kernel/` is
    # shared and never copied, so an adopter whose transport this table does
    # not name had no way to say so -- `.v4/fail_closed.json` is
    # where they say it, and `vocabulary_for` refuses a file it cannot read
    # rather than quietly judging by the narrower table.
    vocab = analysis.vocabulary_for(repo_root)
    findings: list[analysis.Finding] = []
    unverifiable: list[tuple[str, str]] = []
    deleted: list[tuple[str, str]] = []
    for rel in rel_paths:
        if Path(rel).suffix == GO_SUFFIX:
            # Go has no exceptions, so there is no try/except to point at. The
            # question is the same -- can control leave here with the failure
            # unanswered -- asked of `if err != nil {}` and `_ = err`.
            # `shape` is None where the toolchain is absent or the file will
            # not parse, and that is `unverifiable`, never clean.
            from kernel.analysis import gosource
            shape = gosource.shape(repo_root / rel)
            if shape is None:
                unverifiable.append((rel, "no Go toolchain, or it will not parse"))
                continue
            findings.extend(analysis.go_findings(rel, shape))
            continue
        if Path(rel).suffix in TS_SUFFIXES:
            # Third extractor, same rule and the same vocabulary. TypeScript
            # has exceptions, so this is the Python shape rather than the Go
            # one: a `try` whose body reaches outside, and a `catch` that lets
            # control continue as if it had worked.
            path = repo_root / rel
            if not path.is_file():
                if rel in subject_files_deleted(repo_root, base):
                    deleted.append((rel, "deleted by this change -- a file that "
                                         "is not in the tree holds no handler"))
                else:
                    unverifiable.append((rel, "file is missing"))
                continue
            try:
                findings.extend(analysis.ts_findings(
                    rel, path.read_text(encoding="utf-8", errors="replace"),
                    vocab=vocab))
            except OSError as exc:
                unverifiable.append((rel, f"unreadable: {exc.__class__.__name__}"))
            continue
        if Path(rel).suffix not in PYTHON_SUFFIXES:
            unverifiable.append((rel, "not a Python file"))
            continue
        path = repo_root / rel
        if not path.is_file():
            # A subject file this change deleted is not a file that could not
            # be read. `subject_files.deleted_since` asks git which it is: a
            # path the diff reports as `D` was removed on purpose, while a path
            # git never knew is a subject written wrong.
            #
            # They settle differently, which is why they no longer share a
            # bucket. A file that is not in the tree holds no handler that can
            # fail open -- deleting one is the opposite of introducing one -- so
            # it is listed and then stands aside. A path git never knew is a
            # subject written wrong, and that still cannot be verified.
            #
            # Measured on a sibling checker before this: a claim whose subject
            # held 22 deleted files and 4 live ones returned 4 on the strength
            # of the 22, while `derive._retract_orphans` refused to retract it
            # because the 4 were still there -- neither answerable nor
            # retractable. This checker had the same helper and the same fork.
            if rel in subject_files_deleted(repo_root, base):
                deleted.append((rel, "deleted by this change -- a file that is "
                                     "not in the tree holds no handler"))
            else:
                unverifiable.append((rel, "file is missing"))
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            unverifiable.append((rel, f"unreadable: {exc.__class__.__name__}"))
            continue
        try:
            findings.extend(analysis.analyse_source(source, path=rel,
                                                    vocab=vocab))
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
        f"the tree holds no handler, so these do not hold the verdict.")
    for rel, why in deleted:
        out(f"  {rel}: {why}")


def report(findings, unverifiable, scanned, symbol: str, variant: str, out=print,
           deleted=(), fid=None) -> int:
    scope = []
    if symbol:
        scope.append(f"symbol={symbol}")
    if variant:
        scope.append(f"variant={variant}")
    scope_note = f" (narrowed to {', '.join(scope)})" if scope else ""

    if findings:
        out(f"FAIL: {len(findings)} fail-open handler(s){scope_note}.")
        out("")
        for f in findings:
            out(f"  {f.path}:{f.line}  in {f.symbol}  [{f.variant}]")
            out(f"      {f.detail}")
            if fid is not None:
                out(f"      id {fid(f)}  -- to accept it, add that to "
                    f"{baseline.where(KIND)}")
            out("")
        # "try/except" is Python's word for it, and half these findings can
        # now come from a language that has neither. The sentence a reader
        # acts on has to be about the construct in front of them.
        out(
            "Each one guards an outbound effect or an identity/permission decision, "
            "and has an escape path that leaves the guard without the failure "
            "propagating -- so no caller can tell the operation did not happen. "
            "In Python that guard is a `try/except`; in Go it is the "
            "`if err != nil` and the one name Go has for discarding a value."
        )
        if unverifiable:
            out("")
            out("Also unverifiable (did not change the verdict):")
            for rel, why in unverifiable:
                out(f"  {rel}: {why}")
        _deleted_note(out, deleted)
        return EXIT_FAIL

    if unverifiable:
        out(f"CANNOT VERIFY: {len(unverifiable)} subject file(s) are not "
            f"analysable source.")
        for rel, why in unverifiable:
            out(f"  {rel}: {why}")
        out("")
        out(
            "No fail-open handler was found in the files that could be read, but "
            "that is not a clean bill of health for the ones that could not."
        )
        _deleted_note(out, deleted)
        return EXIT_CANNOT_VERIFY

    if not scanned:
        # A subject that is nothing but deletions is an answer, not a shrug.
        # There is no file left to hold a handler, and saying CANNOT VERIFY
        # here is what left such a claim stuck: never answerable, and not
        # retractable either while any sibling path survived.
        if deleted:
            out(f"PASS: {len(deleted)} file(s) removed by this change and "
                f"nothing left to analyse; a deletion introduces no fail-open "
                f"handler.")
            for rel, why in deleted:
                out(f"  {rel}: {why}")
            return EXIT_PASS
        out("CANNOT VERIFY: the subject named no files to analyse.")
        return EXIT_CANNOT_VERIFY

    out(f"PASS: {len(scanned)} file(s) analysed{scope_note}; no fail-open handler.")
    for rel in scanned:
        out(f"  {rel}")
    out("")
    out(
        "Every guard around an outbound effect or an identity/permission "
        "decision -- a `try/except` in Python, an `if err != nil` in Go -- leaves "
        "only by propagating the failure, ending the process, or handing the "
        "caller an explicit error. Files with no such guarded operation "
        "pass for the same reason: there is nothing here to fail open."
    )
    _deleted_note(out, deleted)
    return EXIT_PASS


def machine_payload(code: int, findings, scanned, unverifiable, symbol, variant,
                    deleted=()) -> dict:
    """What ``--out`` gets.  The exit code stays the verdict; this is detail."""
    return {
        "claim_kind": "fail-closed",
        "exit_code": code,
        "verdict": {0: "PASS", 1: "FAIL", 4: "CANNOT_VERIFY"}.get(code, "ERROR"),
        "scope": {"symbol": symbol, "variant": variant},
        "scanned": list(scanned),
        "unverifiable": [{"path": p, "why": w} for p, w in unverifiable],
        "deleted": [{"path": p, "why": w} for p, w in deleted],
        "findings": [
            {
                "path": f.path,
                "line": f.line,
                "symbol": f.symbol,
                "variant": f.variant,
                "trigger": f.trigger,
                "trigger_line": f.trigger_line,
                "handler": f.handler,
                "detail": f.detail,
            }
            for f in findings
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="fail-closed claim checker")
    parser.add_argument("--subject", required=True, help="path to the subject JSON")
    # Both are supplied by kernel/runner.py on every invocation.  `--out` takes a
    # machine-readable copy of the report; `--facts` is accepted and unused, because
    # this rule reads source and nothing else.
    parser.add_argument("--out", help="path to write the structured report to")
    parser.add_argument("--facts", help="accepted for kernel compatibility; unused")
    parser.add_argument("--emit-baseline", action="store_true",
                        help="print a baseline covering the whole repo and stop; "
                             "writes nothing")
    args = parser.parse_args(argv)

    subject = json.loads(Path(args.subject).read_text(encoding="utf-8"))
    repo_root = Path(subject["repo_root"])
    symbol = str(subject.get("symbol") or "")
    variant = str(subject.get("variant") or "")

    if args.emit_baseline:
        # The whole repo, not the subject: a baseline built from one task's
        # files forgives those and leaves every other pre-existing finding to
        # arrive on whoever touches that file next, which is the shape this
        # exists to end. `subject_files.tracked` is the one answer to "which
        # files are this repo's".
        from kernel.analysis import subject_files
        rels = subject_files.tracked(
            subject, repo_root,
            tuple(PYTHON_SUFFIXES) + (GO_SUFFIX,) + tuple(TS_SUFFIXES))
        found, _unverifiable, _deleted = inspect(repo_root, rels, "")
        print(baseline.block(
            {"id": finding_id(f), "path": f.path, "symbol": f.symbol,
             "variant": f.variant, "handler": f.handler, "note": ""}
            for f in found))
        return EXIT_PASS

    rels = file_refs(subject)
    # The task's own base, not HEAD. See `subject_files_deleted`.
    base = str(subject.get("diff_base") or "")
    findings, unverifiable, deleted = inspect(repo_root, rels, base)
    # The second place a language is named, and it has to agree with the first.
    # `inspect` grew a TypeScript branch and this did not, so a clean TS file
    # was analysed, produced nothing, and then counted as nothing analysed --
    # `scanned` came back empty and the verdict was CANNOT VERIFY on a file the
    # checker had just read end to end.
    analysable = [r for r in rels
                  if Path(r).suffix in PYTHON_SUFFIXES
                  or Path(r).suffix == GO_SUFFIX
                  or Path(r).suffix in TS_SUFFIXES]
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

    code = report(selected, unverifiable, scanned, symbol, variant,
                  deleted=deleted, fid=finding_id)
    if args.out:
        Path(args.out).write_text(
            json.dumps(
                machine_payload(code, selected, scanned, unverifiable, symbol,
                                variant, deleted),
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
        print(f"fail-closed checker broke: {exc!r}", file=sys.stderr)
        sys.exit(EXIT_BROKEN)
