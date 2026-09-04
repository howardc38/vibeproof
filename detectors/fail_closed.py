#!/usr/bin/env python3
"""``fail-closed`` detector.  SPEC.md §2.

Answers "what in this change needs verifying?", not "is it correct" -- that is
``checkers/fail_closed.py``, and both call the same
``kernel.analysis.fail_closed`` so they can never disagree.

    detectors/fail_closed.py --subject /tmp/subject.json

Emits one line per claim on stdout::

    V4-CLAIM: kind=fail-closed file=core/workers/dispatch.py symbol=publish_post variant=swallow line=209

Exit codes -- ``0`` is the *only* success value:

    0   scanned (zero claims is still 0)
    3   the detector itself broke; the kernel must not ship on this

Two properties the kernel depends on:

* **Deterministic.** Claims are sorted and de-duplicated, so re-running over
  unchanged files is byte-identical, and re-derivation never opens a duplicate.
* **Keyed by symbol, not line.** A claim identified by line number is re-derived
  as a brand-new unanswered claim the moment anything above it grows a line.
  ``line=`` is carried for humans and is not part of the identity.

A file that will not parse is reported on stderr and skipped: an unparseable
file is a fact about the repo, not a broken detector, and exiting non-zero for
it would block every ship on a syntax error somewhere in scope.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kernel.analysis import fail_closed as analysis
from kernel.analysis.subject_files import TS_SUFFIXES  # noqa: E402

EXIT_SCANNED = 0
EXIT_BROKEN = 3

PYTHON_SUFFIXES = {".py", ".pyi"}
GO_SUFFIX = ".go"


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


def scan(repo_root: Path, rel_paths, warn) -> list[analysis.Claim]:
    # The repo's own vocabulary, unioned with the shipped one. `kernel/` is
    # shared and never copied, so an adopter whose transport this table does
    # not name had no way to say so -- `.v4/fail_closed.json` is
    # where they say it, and `vocabulary_for` refuses a file it cannot read
    # rather than quietly judging by the narrower table.
    vocab = analysis.vocabulary_for(repo_root)
    findings: list[analysis.Finding] = []
    for rel in rel_paths:
        if Path(rel).suffix == GO_SUFFIX:
            # Go has no exceptions, so there is no try/except to point at. The
            # question is the same -- can control leave here with the failure
            # unanswered -- asked of `if err != nil {}` and `_ = err`.
            # `shape` is None where the toolchain is absent or the file will
            # not parse, and that is `unverifiable`, never clean.
            #
            # The comment above said so and the code did the opposite: a bare
            # `pass` and a `continue`, no `warn()`, while the other three skips
            # in this same function all warn. So a machine with no Go
            # toolchain, or one unparseable `.go` file, raised zero fail-closed
            # claims for every Go file in the subject and said nothing at all
            # -- and zero claims plus silence is exactly what a repo whose Go
            # code answers every error looks like. `checkers/fail_closed.py`
            # calls the same state `unverifiable` and prints it; the detector
            # is the side that decides whether a claim exists to be checked,
            # so its silence is the more expensive one.
            from kernel.analysis import gosource
            shape = gosource.shape(repo_root / rel)
            if shape is None:
                warn(f"skipped {rel}: no Go toolchain, or it will not parse")
                continue
            findings.extend(analysis.go_findings(rel, shape))
            continue
        if Path(rel).suffix in TS_SUFFIXES:
            # The same rule and the same vocabulary, through the TypeScript
            # extractor. Without this the checker could see a swallowed
            # `fetch` and no claim was ever raised for it to answer.
            path = repo_root / rel
            try:
                findings.extend(analysis.ts_findings(
                    rel, path.read_text(encoding="utf-8"), vocab=vocab))
            except (OSError, UnicodeDecodeError) as exc:
                warn(f"skipped {rel}: unreadable ({exc.__class__.__name__})")
            continue
        if Path(rel).suffix not in PYTHON_SUFFIXES:
            continue
        path = repo_root / rel
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            warn(f"skipped {rel}: unreadable ({exc.__class__.__name__})")
            continue
        try:
            findings.extend(analysis.analyse_source(source, path=rel,
                                                    vocab=vocab))
        except SyntaxError as exc:
            warn(f"skipped {rel}: will not parse (line {exc.lineno})")
    return analysis.to_claims(findings)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="fail-closed claim detector")
    parser.add_argument("--subject", required=True, help="path to the subject JSON")
    # Supplied by kernel/runner.py on every invocation; a detector shares the
    # checker's shape, so it has to accept the checker's argv.
    parser.add_argument("--out", help="path to write the structured claim list to")
    parser.add_argument("--facts", help="accepted for kernel compatibility; unused")
    args = parser.parse_args(argv)

    subject = json.loads(Path(args.subject).read_text(encoding="utf-8"))
    repo_root = Path(subject["repo_root"])

    def warn(message: str) -> None:
        print(f"fail-closed detector: {message}", file=sys.stderr)

    claims = scan(repo_root, file_refs(subject), warn)
    for claim in claims:
        print(
            f"V4-CLAIM: kind=fail-closed file={claim.path} symbol={claim.symbol} "
            f"variant={claim.variant} line={claim.line}"
        )
    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {
                    "claim_kind": "fail-closed",
                    "claims": [
                        {
                            "file": c.path,
                            "symbol": c.symbol,
                            "variant": c.variant,
                            "line": c.line,
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
        print(f"fail-closed detector broke: {exc!r}", file=sys.stderr)
        sys.exit(EXIT_BROKEN)
