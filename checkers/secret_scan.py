#!/usr/bin/env python3
"""``secret`` checker.  SPEC.md §3.

Answers one claim: *does any file in scope commit a live credential?*  The exit
code is the answer -- nothing this program prints is load-bearing for the
verdict.

    checkers/secret_scan.py --subject S.json --out O.json [--facts F.json]
    checkers/secret_scan.py --subject S.json --explain      # for a human

All three kernel flags are declared, per SPEC.md §3 -- ``argparse`` exits 2
on a flag it was not told about, and 2 is not a verdict.

Exit codes:

    0    no committed credential in scope.  Includes "every match in scope was
         a fixture" -- a verified clean, with every suppression printed under
         ``--explain``.
    1    committed credentials found.  Every one is named on stdout as
         file:line.
    4    cannot verify: something in scope could not be read (missing, binary,
         over the size limit, not valid UTF-8), or scope named nothing to read.
    >=5  the checker itself broke.

**Why an unreadable file is 4 and not 0.**  Same reason as ``fail_closed.py``:
"I found no credential in the files I could read" is not "there is no
credential".  A file the checker cannot read makes the answer unknown, and
unknown is not a pass.

**Why a definite finding still wins over 4.**  If a readable file already
contains a live credential the verdict is settled; an unreadable sibling cannot
turn a found leak back into an unknown.

**Why vendored files are neither 0 nor 4.**  ``.venv/``, ``node_modules/`` and
friends hold code this repo did not write and cannot edit.  A credential in a
pip-installed package is not a leak by this repo, and failing a task over one
teaches people to switch the gate off.  Those paths are listed as out of scope,
they do not move the exit code, and they do not count as unverifiable either.
If the *entire* scope is vendored there is nothing to answer, which is 4.

**Predecessor.**  V3's ``SECURITY_GATE/tooling/scanners/secret-scan.js`` matched
the same table and reported 78 P0 on adopter_a, not one of which was a
credential that repo committed.  This checker keeps the matching and adds the
classifier V3 never had; see ``kernel/analysis/secret_patterns.py`` for which of
the 78 motivated which rule.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kernel.analysis import secret_patterns as analysis  # noqa: E402
from kernel.analysis import subject_files  # noqa: E402

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CANNOT_VERIFY = 4
EXIT_BROKEN = 5

#: Largest file this checker will read into memory.  V3 stopped at 512 KiB,
#: which put 140 of adopter_a' own tracked files (mostly ``events.jsonl`` audit
#: bundles) outside its scan without saying so.  A file over this limit is not
#: silently skipped -- it is unverifiable, which is 4.
MAX_FILE_BYTES = 5 * 1024 * 1024

#: How much of an oversize file to sniff before deciding text vs binary.
BINARY_PROBE_BYTES = 8192


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
    """Repo-relative paths this claim covers.

    Normally the kernel names them in ``subject_refs``. A repo-scoped claim
    names none, because the question is about the task rather than a site --
    the same shape as ``test`` and ``scope``, which read the repo directly. In
    that case the subject is what the task changed: everything different from
    ``diff_base``, plus anything untracked.

    Falling back to "nothing to scan" instead would be worse than useless. It
    returns UNSUPPORTED, which correctly does not count as answered, so the
    claim blocks forever and the checker never runs on the files it exists for.

    `None` where git could not take the diff at all -- distinct from `[]`,
    which is "the task changed nothing" and is a pass.
    """
    refs = subject.get("subject_refs") or []
    paths = {
        str(ref.get("path"))
        for ref in refs
        if isinstance(ref, dict) and ref.get("kind") == "file" and ref.get("path")
    }
    if paths:
        return sorted(paths)

    root, base = subject.get("repo_root"), subject.get("diff_base")
    if not root or not base:
        return []

    # git walks upward, so running `git diff` inside a directory that is not
    # itself a repository quietly answers about whatever repository encloses
    # it. A checker pointed at a fixture would then report on the framework.
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root,
                         capture_output=True, text=True)
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != Path(root).resolve():
        return []

    # The kernel's, not a third copy of the same two git calls. It raises where
    # this returned an empty set, and an empty set here means "no file changed,
    # so nothing could carry a credential" -- a clean verdict taken from a diff
    # nobody read. `scope` refuses exactly that and says so; this did it
    # silently.
    try:
        changed = set(subject_files.changed_since(root, base))
    except subject_files.DiffUnreadable:
        # `[]` here means "nothing changed", and the caller turns that into a
        # PASS whenever the subject looked well-formed. A diff that could not
        # be taken is well-formed and unanswered, so returning the empty list
        # reported every file clean from a diff nobody read -- the failure
        # `scope` refuses in as many words, happening here in silence.
        return None
    return subject_files.keep(subject, changed)


def _oversize_why(path: Path, size: int) -> str:
    """Say whether an oversize file is a big text file or a blob.

    Both are unverifiable -- a text scanner has nothing true to say about an
    ONNX model -- but the operator needs to know which, because only one of the
    two is worth raising the limit for.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(BINARY_PROBE_BYTES)
    except OSError as exc:
        return f"unreadable: {exc.__class__.__name__}"
    shape = "binary" if b"\0" in head else "text"
    return f"{size} bytes of {shape} is over the {MAX_FILE_BYTES} byte scan limit"


#: Lines held at once when a file is too big to read whole. Chosen for memory,
#: not for meaning: the answer does not depend on it.
CHUNK_LINES = 20_000

#: `waived_lines` says a pragma covers its own line "plus the one line after
#: it", so a chunk boundary may not fall between those two.
_OVERLAP = 2


def _analyse_streamed(path: Path, rel: str, table=None):
    """The same answer as reading the file whole, without holding it whole.

    The size limit is about memory, and it was answering a different question:
    a file over it came back `unverifiable`, so the checker exited 4 and said
    the repo could not be judged. Measured on one adopter: the file that tripped
    it was `.v4/ledger_export.jsonl` -- written by `v4 ship`, append-only, so it
    only grows -- and it carries engagement prose verbatim, which is where a
    worker pasting a real credential would land. The one file most likely to
    hold one was the one file nothing could read.

    Raising the limit only moves the day it happens again. Streaming removes the
    question: a text file is scanned whatever its size, and the limit goes back
    to meaning what it says -- how much is held at once.
    """
    findings, suppressed, seen = [], [], set()
    buf, base = [], 0

    def flush():
        report = analysis.analyse_source("\n".join(buf), path=rel, table=table)
        for bucket, out in ((report.findings, findings),
                            (report.suppressed, suppressed)):
            for verdict in bucket:
                cand = verdict.candidate
                line = base + cand.line
                key = (line, cand.col, cand.pattern_id)
                if key in seen:          # the overlap sees these twice
                    continue
                seen.add(key)
                out.append(dataclasses.replace(
                    verdict, candidate=dataclasses.replace(cand, line=line)))

    with path.open(encoding="utf-8", errors="strict") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            if "\0" in line:
                raise ValueError("binary content")
            buf.append(line)
            if len(buf) >= CHUNK_LINES:
                flush()
                base += len(buf) - _OVERLAP
                buf = buf[-_OVERLAP:]
    if buf:
        flush()
    return sorted(findings), sorted(suppressed)


def inspect(repo_root: Path, rel_paths, table=None, base: str = ""):
    """(findings, suppressed, scanned, out_of_scope, unverifiable, deleted).

    ``out_of_scope``, ``unverifiable`` and ``deleted`` are ``[(path, why)]``.

    ``deleted`` is separate from ``unverifiable`` because the two settle
    differently: a removed file carries no committed credential and does
    not hold the verdict, while a path git never knew is a subject written
    wrong and still does.
    """
    findings: list[analysis.Verdict] = []
    suppressed: list[analysis.Verdict] = []
    scanned: list[str] = []
    out_of_scope: list[tuple[str, str]] = []
    unverifiable: list[tuple[str, str]] = []
    deleted: list[tuple[str, str]] = []

    for rel in rel_paths:
        vendored = analysis.is_vendored(rel)
        if vendored:
            out_of_scope.append((rel, f"under {vendored}/ -- not written by this repo"))
            continue
        path = repo_root / rel
        if not path.is_file():
            # A subject file this change deleted is not a file that could not be
            # read. `subject_files.deleted_since` asks git which it is: a path
            # the diff reports as `D` was removed on purpose and there is
            # nothing left to analyse, while a path git never knew is a subject
            # written wrong.
            #
            # They also settle differently, which is why they no longer share a
            # bucket. A deletion cannot carry a committed credential -- removing
            # a file is the opposite of committing one -- so it is listed and
            # then stands aside. A path git never knew is a subject written
            # wrong, and that still cannot be verified.
            #
            # Measured before this: a claim whose subject held 22 deleted files
            # and 4 live ones returned 4 on the strength of the 22, while
            # `derive._retract_orphans` refused to retract it because the 4 were
            # still there. Neither answerable nor retractable, and the text it
            # printed promised a retraction that could not happen.
            if rel in subject_files_deleted(repo_root, base):
                deleted.append((rel, "deleted by this change -- a removed file "
                                     "carries no committed credential"))
            else:
                unverifiable.append((rel, "file is missing"))
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            unverifiable.append((rel, f"unreadable: {exc.__class__.__name__}"))
            continue
        if size > MAX_FILE_BYTES:
            why = _oversize_why(path, size)
            if "of text" not in why:
                # A blob. A text scanner has nothing true to say about an ONNX
                # model, and that is a different answer from "too big".
                unverifiable.append((rel, why))
                continue
            try:
                got, skipped = _analyse_streamed(path, rel, table=table)
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                unverifiable.append(
                    (rel, f"{size} bytes and unreadable as text: "
                          f"{exc.__class__.__name__}"))
                continue
            findings.extend(got)
            suppressed.extend(skipped)
            scanned.append(rel)
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            unverifiable.append((rel, "not UTF-8 text; a pattern scanner cannot read it"))
            continue
        except OSError as exc:
            unverifiable.append((rel, f"unreadable: {exc.__class__.__name__}"))
            continue
        if "\0" in source:
            unverifiable.append((rel, "binary content"))
            continue
        report = analysis.analyse_source(source, path=rel, table=table)
        findings.extend(report.findings)
        suppressed.extend(report.suppressed)
        scanned.append(rel)

    return (sorted(findings), sorted(suppressed), scanned, out_of_scope,
            unverifiable, deleted)


#: A repo-wide run can put tens of thousands of vendored files out of scope.
#: Listing all of them buries the verdict, so the tail lists this many and
#: counts the rest.  The count is always exact.
LIST_CAP = 20


def _listing(out, pairs) -> None:
    """Deterministic ``path: why`` lines, capped so the verdict stays readable."""
    for rel, why in pairs[:LIST_CAP]:
        out(f"  {rel}: {why}")
    if len(pairs) > LIST_CAP:
        out(f"  ... and {len(pairs) - LIST_CAP} more")


def _tail(out, out_of_scope, unverifiable, suppressed, explain: bool,
          deleted=()) -> None:
    if suppressed:
        out("")
        out(f"Suppressed {len(suppressed)} match(es) as fixture or placeholder values.")
        if explain:
            for verdict in suppressed:
                c, j = verdict.candidate, verdict.judgement
                out(f"  {c.path}:{c.line}  [{j.rule}]  {c.label}")
                out(f"      {c.excerpt}")
                out(f"      {j.why}")
        else:
            out("  Re-run with --explain to see each one and the rule that silenced it.")
    if out_of_scope:
        out("")
        out(f"Out of scope: {len(out_of_scope)} vendored file(s).")
        _listing(out, out_of_scope)
    if deleted:
        out("")
        out(f"Deleted by this change: {len(deleted)} file(s). A removed file\n"
            f"carries no committed credential, so these do not hold the verdict.")
        _listing(out, deleted)
    if unverifiable:
        out("")
        out(f"Unverifiable: {len(unverifiable)} file(s).")
        _listing(out, unverifiable)


def report(findings, suppressed, scanned, out_of_scope, unverifiable,
           explain=False, out=print, subject_resolvable=False,
           deleted=()) -> int:
    if findings:
        out(f"FAIL: {len(findings)} committed credential(s).")
        out("")
        for verdict in findings:
            c, j = verdict.candidate, verdict.judgement
            out(f"  {c.path}:{c.line}  {c.label}")
            out(f"      {c.redacted}")
            out(f"      {j.why}")
            out("")
        out(
            "Each one is a literal credential in a tracked file. Rotate the "
            "credential first -- deleting the line does not un-leak it -- then "
            "move the value into the secret store."
        )
        _tail(out, out_of_scope, unverifiable, suppressed, explain, deleted)
        return EXIT_FAIL

    if unverifiable:
        out(f"CANNOT VERIFY: {len(unverifiable)} subject file(s) could not be read.")
        _listing(out, unverifiable)
        out("")
        out(
            "No credential was found in the files that could be read, but that "
            "is not a clean bill of health for the ones that could not."
        )
        _tail(out, out_of_scope, [], suppressed, explain, deleted)
        return EXIT_CANNOT_VERIFY

    if not scanned:
        if out_of_scope:
            out("CANNOT VERIFY: every file in the subject is vendored; nothing this repo authored was in scope.")
            _listing(out, out_of_scope)
        else:
            # Two different situations reach here and only one is an answer.
            #
            # A well-formed subject over a task that changed nothing has nothing
            # that could carry a credential -- that is a pass. A subject with no
            # repo_root or no diff_base is malformed, and reading that as "clean"
            # is how a scanner reports every repo safe.
            if deleted:
                out(f"{len(deleted)} file(s) removed by this change and nothing\n"
                    f"left to scan; a deletion carries no credential")
                _listing(out, deleted)
                return EXIT_PASS
            if subject_resolvable:
                out("no files changed; nothing could carry a credential")
                return EXIT_PASS
            out("CANNOT VERIFY: the subject named no files and gave no way to "
                "work out which files a task changed.")
        return EXIT_CANNOT_VERIFY

    out(f"PASS: {len(scanned)} file(s) scanned; no committed credential.")
    for rel in scanned[:LIST_CAP]:
        out(f"  {rel}")
    if len(scanned) > LIST_CAP:
        out(f"  ... and {len(scanned) - LIST_CAP} more")
    _tail(out, out_of_scope, [], suppressed, explain, deleted)
    return EXIT_PASS


VERDICT = {EXIT_PASS: "pass", EXIT_FAIL: "fail", EXIT_CANNOT_VERIFY: "cannot_verify"}


def out_payload(code, findings, suppressed, scanned, out_of_scope, unverifiable) -> dict:
    """What ``--out`` gets: the same answer, without the kernel parsing prose.

    Findings are redacted here for the same reason they are redacted on stdout:
    this file is read into the ledger, and the ledger outlives the leak.
    ``out_of_scope`` is a count plus a sample, because a repo-wide subject puts
    tens of thousands of vendored files in it and none of them is news.
    """
    return {
        "checker": "secret",
        "verdict": VERDICT.get(code, "error"),
        "exit_code": code,
        "counts": {
            "findings": len(findings),
            "suppressed": len(suppressed),
            "scanned": len(scanned),
            "out_of_scope": len(out_of_scope),
            "unverifiable": len(unverifiable),
        },
        "findings": [
            {
                "path": v.candidate.path,
                "line": v.candidate.line,
                "pattern_id": v.candidate.pattern_id,
                "label": v.candidate.label,
                "redacted": v.candidate.redacted,
            }
            for v in findings
        ],
        "suppressed": [
            {
                "path": v.candidate.path,
                "line": v.candidate.line,
                "pattern_id": v.candidate.pattern_id,
                "rule": v.judgement.rule,
            }
            for v in suppressed
        ],
        "unverifiable": [{"path": p, "why": w} for p, w in unverifiable],
        "out_of_scope_sample": [{"path": p, "why": w} for p, w in out_of_scope[:LIST_CAP]],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="committed-secret claim checker")
    parser.add_argument("--subject", required=True, help="path to the subject JSON")
    # SPEC.md §3: the kernel passes --out every time and --facts when facts
    # exist. argparse exits 2 on a flag it has not been told about, and 2 is not
    # a verdict -- a checker that omits these is refused at registration.
    parser.add_argument("--out", help="path to write the structured result")
    parser.add_argument(
        "--facts",
        help="path to the kernel's read-only facts JSON. Accepted and validated; "
             "this checker consumes none, because its classifier reads the matched "
             "value and nothing the kernel knows about the repo",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="print every suppressed match and the rule that silenced it",
    )
    args = parser.parse_args(argv)

    subject = json.loads(Path(args.subject).read_text(encoding="utf-8"))
    repo_root = Path(subject["repo_root"])
    if args.facts:
        json.loads(Path(args.facts).read_text(encoding="utf-8"))

    # A repo's own credential families, unioned with the shipped table. The
    # reference repo has four this scanner does not match, and before the table
    # was data the only way to add one was to edit the kernel -- which
    # `protected_paths` forbids without a signature, so the cheapest correct
    # move was blocked and the cheapest available one was to ignore the gap.
    try:
        table = analysis.table_for(repo_root)
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"cannot read {repo_root / '.v4' / 'secret_patterns.json'}: {exc}",
              file=sys.stderr)
        return 5              # a broken table is an error, never an empty one

    # `None` is "the diff could not be taken", `[]` is "the task changed
    # nothing". Only the second is a pass.
    refs = file_refs(subject)
    findings, suppressed, scanned, out_of_scope, unverifiable, deleted = inspect(
        repo_root, refs or [], table=table,
        base=str(subject.get("diff_base") or ""),
    )
    code = report(findings, suppressed, scanned, out_of_scope, unverifiable,
                  explain=args.explain, deleted=deleted,
                  subject_resolvable=(refs is not None
                                      and bool(subject.get('repo_root')
                                               and subject.get('diff_base'))))
    if args.out:
        Path(args.out).write_text(
            json.dumps(
                out_payload(code, findings, suppressed, scanned, out_of_scope, unverifiable),
                indent=2, sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # the checker itself broke -- never mistaken for a verdict
        print(f"secret checker broke: {exc!r}", file=sys.stderr)
        sys.exit(EXIT_BROKEN)
