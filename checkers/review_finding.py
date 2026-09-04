#!/usr/bin/env python3
"""Close a reviewer's finding with a test that earns it.  SPEC.md 4.4.

A novel finding has no checker -- checker-author only writes one once a kind
has appeared three times -- so the first two occurrences need another way to
reach a terminal state. The alternatives are an ACCEPTED_RISK for every finding,
which floods the one human touchpoint, or an advisory tier, which is the
severity field the previous system kept and never branched on.

So the finding is closed by a test, and this checker decides whether the test
means anything. Three conditions, and the third is the one that matters:

    assert "with _registry_cache_lock" in inspect.getsource(make_brand_registry)

That test is real, it is in the target repo, and it satisfies red-green
perfectly: delete the lock and it fails. It also never calls the function. Two
conditions alone let a worker close any finding with one line of getsource.
"""

import argparse
import json
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel import redgreen  # noqa: E402
# One owner. This was a second bare `MIN_MARKER = 24` with no comment, so
# the write path (`review.raise_finding`, which refuses a short marker) and
# the verdict path (below, which refuses the same thing) could move apart
# and nothing would notice -- the shape `LINT-CONFIG-DUAL-TRUTH` exists to
# catch, in the repo that ships that rule.
from kernel.review import MIN_MARKER  # noqa: E402


def _at(root: Path, commit: str, rel: str) -> str:
    import subprocess
    r = subprocess.run(["git", "show", f"{commit}:{rel}"], cwd=root,
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _tracked(root: Path, subject=None) -> list:
    """Committed and uncommitted alike -- a file this task just wrote is the
    one most likely to carry the finding, and `git ls-files` alone omits it."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from kernel.analysis import subject_files
    return subject_files.tracked(subject, root)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


#: Files that quote findings rather than contain them.
#:
#: `.v4/ledger_export.jsonl` carries every finding's note verbatim and is
#: committed, and `.v4/deferred/*.json` carries the reason a finding was put
#: off. A note that quotes the wrong sentence -- which is what a finding about
#: a wrong sentence does -- then appears in a tracked file forever, and the
#: sweep below reads that as "repaired in one place only" for every `gone`
#: marker anybody ever offers. The rule this serves is that a fact is repaired
#: where it appears; a record *of* the fact is not one of those places.
_RECORDS_OF_FINDINGS = (".v4/ledger_export.jsonl", ".v4/deferred/")


def _is_a_record_of_findings(rel: str) -> bool:
    rel = str(rel).replace("\\", "/")
    return any(rel == r or rel.startswith(r) for r in _RECORDS_OF_FINDINGS)


def _text_closure(root: Path, target, parent, gone, now, subject=None) -> int:
    """Red-green over source text.  Proves a string moved, and says only that."""
    if not target or not parent:
        print("FAIL: a text closure needs the file the finding names and the "
              "parent commit to compare against.")
        return 1
    for label, marker in (("text_gone", gone), ("text_now", now)):
        if marker and len(marker.strip()) < MIN_MARKER:
            print(f"FAIL: {label} is {len(marker.strip())} characters and the "
                  f"floor is {MIN_MARKER}. A marker short enough to match by "
                  f"accident proves nothing about the repair.")
            return 1
    before = _at(root, parent, target)
    if not before:
        print(f"FAIL: {target} could not be read at {parent[:12]}, so there is "
              f"nothing to compare the repair against.")
        return 1
    # The file the finding names is where the fact was seen, not where it lives.
    # The first version compared that one file and called it closed -- while
    # this checker serves the kind whose own standing rule is "修一個 finding =
    # 修嗰個事實喺所有出現嘅位，唔係嗰一行". Deleting a wrong sentence from
    # client.py while the same sentence sits in media.py is one occurrence
    # repaired and a finding closed on the strength of it.
    still = [rel for rel in _tracked(root, subject)
             if gone and not _is_a_record_of_findings(rel)
             and gone in _read(root / rel)] if gone else []
    problems = []
    if gone:
        if gone not in before:
            problems.append(f"text_gone was not in {target} at {parent[:12]} -- "
                            f"a repair cannot remove what was never there")
        elif still:
            where = ", ".join(still[:6]) + ("…" if len(still) > 6 else "")
            problems.append(
                f"text_gone is still in {where}. A finding is repaired where the "
                f"fact appears, not where it was noticed -- {target} was the "
                f"sighting.")
    if now:
        after = _read(root / target)
        if now in before:
            problems.append(f"text_now was already in {target} at {parent[:12]}, "
                            f"so this commit did not put it there")
        elif now not in after:
            problems.append(f"text_now is not in {target}")
    if problems:
        print("FAIL: the text this finding was closed with did not move.\n")
        for p in problems:
            print(f"  {p}")
        return 1
    print(f"{target}: the quoted text moved between {parent[:12]} and now. This "
          f"proves a string changed and nothing more -- a finding about "
          f"behaviour still owes a test.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--facts")
    ap.add_argument("--out")
    a = ap.parse_args()

    try:
        s = json.loads(Path(a.subject).read_text())
        root = Path(s["repo_root"])
        params = s.get("params") or {}
        test_path = params.get("closing_test")
        target = s.get("file") or next(
            (r["path"] for r in s.get("subject_refs", []) if r.get("kind") == "file"), None)
        symbol = s.get("symbol") or ""
        parent = params.get("parent_commit") or s.get("diff_base")
        command = params.get("test_one_file_command")
    except Exception as exc:                                    # noqa: BLE001
        print(f"cannot read subject: {exc}", file=sys.stderr)
        return 5

    # A finding whose repair is a sentence has no behaviour to be red about, and
    # the only exit left for it was a signature -- thirteen of them in one
    # measured run. Red-green over the text instead: the marker was in the file
    # at the parent and is not now, or the other way round.
    gone = (s.get("params") or {}).get("text_gone") or ""
    now = (s.get("params") or {}).get("text_now") or ""
    if gone or now:
        return _text_closure(root, target, parent, gone, now, s)

    missing = [n for n, v in (("closing_test", test_path), ("target file", target),
                              ("parent_commit", parent),
                              ("test_one_file_command", command)) if not v]
    if missing:
        # Not UNSUPPORTED: the claim is answerable, it just has not been given
        # what it needs. Saying "cannot verify" here would let it ship.
        # Named `params.*` keys, which are what `review.bind_closing_test`
        # writes and not something anyone can type, and covered two of the four
        # things it lists. The command below is the reachable route and the rest
        # of this framework already prints it in two other places.
        print(f"FAIL: a review finding closes with a test, and this claim is missing "
              f"{', '.join(missing)}.\n\n"
              f"  v4 --repo . review close --claim <this claim> \\\n"
              f"      --test <the new test file> \\\n"
              f"      --command '<a command with {{path}} in it>' \\\n"
              f"      --parent <the commit before the repair>\n\n"
              f"All four are one command. `--parent` is what the red half is run "
              f"against, and `--test` is the file that has to fail there and pass "
              f"here; without either, nothing has been shown.")
        return 1

    # Asked of `redgreen`, which is the only thing that knows what it can
    # instrument. This was `endswith(".py")` with a comment saying the proof
    # "traces execution through a Python `sitecustomize`" -- true when it was
    # written, and false from the day `_go_executed` and `_node_executed`
    # landed. The gate outlived its reason by refusing, before `verify` was
    # ever called, cases the mechanism behind it answers: measured on a real
    # Vitest suite, `tier_of` in a `.ts` module came back executed 37 times.
    #
    # Still exit 4 for a suffix no tracer covers, and the sentence no longer
    # claims a limitation this module does not have.
    if not redgreen.traceable(test_path):
        print(f"the closing test is {test_path}, and no tracer here can observe "
              f"whether it ran the code it is about -- this module instruments "
              f"{', '.join(sorted(redgreen.TRACEABLE))} and nothing else. "
              f"Saying so is not a finding about this repair.\n\n"
              f"A repo whose tests are in another language needs a tracer for "
              f"its own runner; until there is one this claim has to be signed "
              f"for.")
        return 4

    # `shlex.split`, not `.split()`. The naive split breaks any quoted
    # argument apart: `--command "pytest -q -k \"not slow\" {path}"` yields
    # tokens carrying literal quote characters, and the run then fails for a
    # reason that has nothing to do with the repair being offered. The list
    # form on the same line was already handled correctly.
    argv = [c.format(path=test_path) for c in command] if isinstance(command, list) \
        else shlex.split(command.format(path=test_path))

    try:
        res = redgreen.verify(root, command=argv, test_path=test_path,
                              target_file=target, target_symbol=symbol,
                              parent_commit=parent)
    except Exception as exc:                                    # noqa: BLE001
        print(f"red-green check failed to run: {exc}", file=sys.stderr)
        return 5

    if a.out:
        Path(a.out).write_text(json.dumps(res.as_dict(), indent=2))

    if res.ok:
        print(f"{test_path} fails at {parent[:12]}, passes at HEAD, and executed "
              f"{symbol or target} {res.calls} time(s)")
        return 0

    # Three states, not two. `verify` computes them and this printed two: any
    # `res.ok` false became "has not earned the right to close this finding",
    # including the case where the tracer never attached at all. `redgreen`
    # argues that one at length -- "Unknown is not proof. It is just not an
    # allegation" -- and then the only caller of the result turned it into one.
    #
    # It costs nothing while every closing test is Python, because a Python
    # command that runs leaves a trace file. It is the whole difference for the
    # suffixes now admitted above: a Vitest run under the default pool observes
    # nothing, and the honest answer to that is the one this framework already
    # reserves a code for, not a sentence telling a worker their repair is
    # unproven when nothing looked at it.
    # Only when the trace is the sole thing missing. A test that does not pass
    # at HEAD, or does not fail at the parent, is a finding about the repair and
    # stays one whatever the tracer saw -- reading `symbol_executed is None`
    # alone would turn "your closing test is broken" into "unsupported", which
    # is the opposite mistake and the more dangerous one.
    if res.symbol_executed is None and res.red_failed and res.green_passed:
        print(f"UNSUPPORTED: {test_path} fails at {parent[:12]} and passes at "
              f"HEAD, and nothing observed it running {symbol or target}. The "
              f"tracer never attached, so this says nothing about the repair "
              f"either way.\n")
        for n in res.notes:
            print(f"  {n}")
        print(f"\n  A Node runner has to keep the source id for V8 to attribute "
              f"anything -- measured, `vitest run --pool=threads` does and the "
              f"default pool does not. Either give `--command` a runner this "
              f"module can observe, or sign for this claim.")
        return 4

    print(f"FAIL: {test_path} has not earned the right to close this finding.\n")
    for n in res.notes:
        print(f"  {n}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
