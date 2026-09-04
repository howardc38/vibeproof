#!/usr/bin/env python3
"""``external-write`` detector.  SPEC.md §2.

Answers "what in this change needs verifying?", not "is it correct" -- that is
``checkers/external_write.py``, and both call the same
``kernel.analysis.external_write`` so they can never disagree.

    detectors/external_write.py --subject /tmp/subject.json [--facts f.json] --out /tmp/out.json

**All three flags are declared**, including the two this rule barely uses.  An
undeclared flag makes ``argparse`` exit 2, 2 is not in the exit table, and the
kernel then reads the whole scan as ERROR -- sixteen fixtures at exit 2 is the
recorded cost of getting this wrong (SPEC.md §2).

Emits one line per claim on stdout::

    V4-CLAIM: kind=external-write file=core/workers/dispatch.py symbol=_publish variant=readback line=209

Exit codes -- ``0`` is the *only* success value:

    0   scanned (zero claims is still 0)
    3   the detector itself broke; the kernel must not ship on this

A file that will not parse is reported on stderr and skipped: an unparseable
file is a fact about the repo, not a broken detector, and exiting non-zero for
it would block every ship on a syntax error somewhere in scope.

**The convergence property this detector has to hold** (SPEC.md §4, condition
②): answering one of its claims must not raise another of the same kind.  A
``readback`` claim is answered by adding an *outbound read*, and

* ``kernel/facts.py`` refuses a table where a write pattern matches a read
  pattern, so the read-back can never be classified as a write; and
* the analysis suppresses every unobserved write in a scope that gained a read,
  so bookkeeping the fix introduces does not become the next claim.

``tests/test_external_write.py::SelfTrigger`` runs a written-out fix and
re-derives, which is the executable form of that paragraph.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kernel.analysis import external_write as analysis  # noqa: E402

EXIT_SCANNED = 0
EXIT_BROKEN = 3

PYTHON_SUFFIXES = {".py", ".pyi"}
GO_SUFFIXES = {".go"}
#: Every file this rule can read. `.py` alone was the list, so a Go repo's
#: subject came back "not a Python file" for every path and the rule answered
#: about nothing at all.
#: Three languages, and the same pairing the checker keeps: this set says what
#: will be read, the dispatch below says by which reader.
from kernel.analysis.subject_files import TS_SUFFIXES as _TS  # noqa: E402
TS_SUFFIXES = frozenset(_TS)
ANALYSABLE_SUFFIXES = PYTHON_SUFFIXES | GO_SUFFIXES | TS_SUFFIXES


def file_refs(subject: dict) -> list[str]:
    """Repo-relative paths of ``subject_refs`` entries with ``kind == "file"``.

    Every other kind is ignored by contract.  De-duplicated and sorted so the
    scan order -- and therefore the output order -- cannot depend on how the
    kernel happened to build the list.
    """
    refs = subject.get("subject_refs") or []
    paths = {
        str(ref.get("path"))
        for ref in refs
        if isinstance(ref, dict) and ref.get("kind") == "file" and ref.get("path")
    }
    return sorted(paths)


def load_table(facts_path):
    """The outbound vocabulary: the repo's facts file, or the built-in default.

    Never an empty table.  ``kernel.facts`` raises on a malformed file rather
    than degrading, because a detector scanning with no patterns reports a clean
    repo -- fail-open, silently, in the one step that decides what gets checked.
    """
    if not facts_path:
        return analysis.default_table()
    return analysis.table_from(json.loads(Path(facts_path).read_text(encoding="utf-8")))


def scan(repo_root: Path, rel_paths, table, warn) -> list[analysis.Claim]:
    findings: list[analysis.Finding] = []
    for rel in rel_paths:
        if Path(rel).suffix not in ANALYSABLE_SUFFIXES:
            continue
        path = repo_root / rel
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            warn(f"skipped {rel}: unreadable ({exc.__class__.__name__})")
            continue
        try:
            read = (analysis.go_analyse_source if path.suffix in GO_SUFFIXES
                    else analysis.ts_analyse_source if path.suffix in TS_SUFFIXES
                    else analysis.analyse_source)
            findings.extend(read(source, path=rel, table=table))
        except SyntaxError as exc:
            warn(f"skipped {rel}: will not parse (line {exc.lineno})")
    return analysis.to_claims(findings)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="external-write claim detector")
    parser.add_argument("--subject", required=True, help="path to the subject JSON")
    parser.add_argument("--facts", help="path to the repo's facts file")
    parser.add_argument("--out", help="path to write the structured claim list to")
    args = parser.parse_args(argv)

    subject = json.loads(Path(args.subject).read_text(encoding="utf-8"))
    repo_root = Path(subject["repo_root"])
    table = load_table(args.facts)

    def warn(message: str) -> None:
        print(f"external-write detector: {message}", file=sys.stderr)

    claims = scan(repo_root, file_refs(subject), table, warn)
    for claim in claims:
        print(
            f"V4-CLAIM: kind=external-write file={claim.path} symbol={claim.symbol} "
            f"variant={claim.variant} line={claim.line}"
        )
    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {
                    "claim_kind": "external-write",
                    "table": getattr(table, "source", ""),
                    "claims": [
                        {
                            "file": c.path,
                            "symbol": c.symbol,
                            "variant": c.variant,
                            "line": c.line,
                            "shapes": sorted({f.shape for f in c.findings}),
                            "sites": [f.line for f in c.findings],
                        }
                        for c in claims
                    ],
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    return EXIT_SCANNED


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # the detector itself broke -- never a silent 0
        print(f"external-write detector broke: {exc!r}", file=sys.stderr)
        sys.exit(EXIT_BROKEN)
