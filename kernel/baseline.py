"""Standing debt a repo has already signed for.  SPEC.md §6, "Baseline".

SPEC states the shape and forbids reinventing it:

    .v4/<kind>_baseline.json     一個 finding id 嘅 list,入 git
    全部 delta checker 用同一個形狀。唔准各自發明。

It was reinvented three times anyway -- `dependency_audit.load_baseline`,
`structural_lint.load_baseline`, and eight inline lines in `layer_boundary` --
and they disagree twice over. On a baseline file that does not parse:

    dependency_audit  raises, and the kind exits 4: "I cannot answer this"
    layer_boundary    exits 5: "I broke"
    structural_lint   returns an empty set and answers anyway, having decided
                      that a file it could not read forgives nothing

The first is right. A file listing what is forgiven, which cannot be read,
leaves the checker not knowing what is forgiven; 4 says exactly that, and it is
the code the signature machinery is built around. 5 says the checker is broken,
which it is not. Answering is the hollow scan this framework exists to refuse.

And on the file's shape, where they disagree without any of them being wrong:

    dependency_audit  {"accepted": ["<id>", ...]}
    layer_boundary    {"accepted": ["<id>", ...]}
    structural_lint   {"findings": [...]}, entries as ids or as {"id": ...}

All three are read here, because a format already written into adopters' repos
is not something a refactor gets to break. New writers should use `accepted`.

Absent is different from unreadable, and is fail-closed the other way: no file
means nothing is forgiven, never everything.

Why this exists at all
----------------------
A checker that sweeps the whole repo and judges absolutely will, on the first
task in any repo that has ever shipped, fail for debt that task did not create.
SPEC names this failure by its history: 正正係 `secret-scan.js` 累積到 78 個噪音
嘅方式. Measured on a real adoption -- `secret-chain` held the first task on two
handlers in `core/integrations/`, and `test-token-shape` on ten literals in
tests, none of them in a file the task had opened.

Two shapes answer it, and both are legitimate:

  * the diff shape -- `structural_lint`: a finding counts if it is in a file
    this task changed AND its key was not there at `diff_base`. Nothing to
    commit, nothing a worker can widen.
  * the ledger shape -- this module: the debt is written down, in git, with a
    signature required to grow it.

The diff shape is stronger where it applies. It does not apply to a checker
whose subject is the repo rather than the change, which is why both exist.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .config import BASELINE_TEMPLATE


class Unreadable(RuntimeError):
    """The baseline exists and cannot be read, so what is forgiven is unknown."""


def path(repo_root, kind) -> Path:
    return Path(repo_root) / BASELINE_TEMPLATE.format(kind=kind)


def where(kind) -> str:
    """The baseline's repo-relative path, for the sentence that tells a worker
    how to accept a finding.

    Every caller used to print `path(root, kind).name`, and the directory is the
    part that carries the meaning: `.v4/**` is on `scope.PROTECTED_DEFAULT`, so
    adding a line there is `v4 scope widen` plus a `scope_widen_protected`
    signature, not an edit. A bare filename reads as the second one. Measured:
    one signature spent a paragraph working out, from the filename alone, that
    the checker's own suggested remedy was a gated action -- which is a thing
    the message could have said.
    """
    return BASELINE_TEMPLATE.format(kind=kind)


def block(rows) -> str:
    """The JSON an adopter has to land to carry these findings.  Printed, never
    written.

    Landing it touches `.v4/**`, which SPEC.md §5 protects, so it costs a
    signature and leaves a commit with a name on it -- and that commit is the
    anchor, not the difficulty of typing the file.

    Here rather than in a checker, because `load` below already owns this format
    and a producer that disagrees with its reader forgives nothing. It lived in
    `structural_lint` alone, which is why `external-write` and `fail-closed` --
    both of which call `forgive`, and have since they were written -- had no way
    to produce the file they consume. Measured on an adopter: 26 findings in one
    file, 23 of them on symbols the task never touched, and no route to carry any
    of them except answering each by hand.

    `rows` are per-kind: each kind knows which of its fields identify a finding
    to a person reading the file later. Only the shape around them is fixed, and
    `id` is the only field `load` reads.
    """
    return json.dumps({"version": 1, "findings": list(rows)},
                      indent=2, sort_keys=True)


def load(repo_root, kind):
    """`(accepted ids, the file is there)`.  Raises `Unreadable` if it is broken."""
    p = path(repo_root, kind)
    if not p.is_file():
        return set(), False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Unreadable(f"{p.name} could not be read: {exc}") from exc
    # A bare list is accepted: it is what somebody writes by hand the first time,
    # and refusing it would teach them the file is hostile. `findings` is read
    # because `lint` baselines already in adopters' repos use that key.
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        entries = data.get("accepted", data.get("findings"))
    else:
        entries = None
    if not isinstance(entries, list):
        raise Unreadable(
            f"{p.name} must be a list of finding ids, or an object with an "
            f"'accepted' list. What is forgiven cannot be read out of it.")
    out = set()
    for e in entries:
        if isinstance(e, str):
            out.add(e)
        elif isinstance(e, dict) and isinstance(e.get("id"), str):
            # An entry with room for a note beside the id. `lint` writes these.
            out.add(e["id"])
        else:
            raise Unreadable(
                f"{p.name} has an entry that is neither a finding id nor an "
                f"object with one: {str(e)[:60]!r}")
    return out, True


def finding_id(*parts) -> str:
    """A stable name for one finding.

    No line numbers, for the reason claim identity has none: an edit anywhere
    above renumbers every finding below it, and a baseline made of line numbers
    forgives the wrong thing on the next commit.
    """
    return hashlib.sha256(
        "\0".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def forgive(repo_root, kind, findings, key, *, complete=True):
    """`(still_failing, carried, notes)` -- `partition` with the file read.

    Raises `Unreadable`, so the caller answers 4 rather than deciding for itself
    what an unreadable list of exemptions forgives.

    `notes` are returned rather than printed because stdout belongs to the
    checker. They are not optional: SPEC's Baseline rules say 背住嘅債 PASS
    嗰陣都要印, and debt carried in silence is the record nobody reads.
    """
    accepted, _ = load(repo_root, kind)
    still, carried, stale = partition(findings, accepted, key, complete=complete)
    notes = []
    if carried:
        notes.append(f"carrying {len(carried)} accepted finding(s) from "
                     f"{where(kind)}")
    for sid in stale:
        notes.append(f"  baseline entry {sid} matches nothing any more -- "
                     f"delete it")
    return still, carried, notes


def partition(findings, accepted, key, *, complete=True):
    """`(new, carried, stale)` -- what to fail on, what is signed, what is gone.

    `stale` is the entries that match nothing any more. They are reported rather
    than dropped: a baseline that keeps forgiving a finding nobody can find is a
    list that only grows, and the id is enough to delete the line.

    `complete` is whether this run looked everywhere the baseline could match.
    A checker whose subject names two files scans two files, and every accepted
    id belonging to the other four hundred matches nothing *in that scan* -- so
    a scoped run told the operator to delete the whole list, by id, with the
    words "matches nothing any more". Measured on this repo: the first execution
    of `test-shape` in the ledger's life reported all 22 of its accepted entries
    as stale while scanning one file, and following that advice would have
    failed the next whole-repo run on all 22.
    """
    new, carried, seen = [], [], set()
    for f in findings:
        fid = key(f)
        seen.add(fid)
        (carried if fid in accepted else new).append(f)
    return new, carried, (sorted(accepted - seen) if complete else [])
