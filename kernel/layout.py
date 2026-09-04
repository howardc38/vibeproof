"""Where this framework's own files live, spelled once.

`kernel/config.py` declared these four and nothing imported them: twelve other
modules spell `.v4/config.json` as a literal, which is the shape
`BASELINE_TEMPLATE` was added to that same file to remove.  They live here
rather than there because `kernel/facts.py` needs one of them and importing
`config` for it made a cycle -- `facts` -> `config` at import time, `config` ->
`facts` inside `_load_facts` -- which `structural_lint` reports from both ends
(`LINT-IMPORT-CYCLE`).

A path is not a policy.  Nothing here validates a file, applies a rule or
decides anything, so both directions can import it and neither gains a
dependency on the other's rules.

`repo_name` asks git a question and is the one thing here that does I/O.  It
is here rather than in `hashing` or `config` because it answers the same kind
of question as the constants above -- *what is this repo, and where are its
things* -- and because the three callers are `doctrine`, `facts` and `config`,
which is exactly the set that cannot import each other.  The invariant that
matters is preserved: it adds no dependency on anybody's rules.
"""

import json
import subprocess
from pathlib import Path

#: The directory this framework keeps its own state in, inside the repo it
#: judges.  Everything below is relative to the repo root.
DIR = ".v4"

CONFIG = f"{DIR}/config.json"
CLAIM_KINDS = f"{DIR}/claim_kinds.json"
CHECKERS = f"{DIR}/checkers.json"
DETECTORS = f"{DIR}/detectors.json"



def declared_repo_name(root) -> str | None:
    """The name this repo gives itself, or `None` if it gives none.

    A facts table's `repo` field is a declaration: somebody wrote it, `v4 facts
    validate` reads it, and it does not move when the tree is copied. That is
    what makes it the owner of "what is this repo called" -- every other answer
    below is derived from where the checkout happens to sit.

    Only when exactly one table is there. Two, and which one names *this* repo
    is the question `config.facts_path_for` needs this function to answer, so
    reading either would be circular; falling through to the filesystem is the
    honest answer to a repo that has not settled its own vocabulary.
    """
    try:
        named = sorted(p for p in (Path(root) / DIR).glob("facts*.json")
                       if p.suffix == ".json")
    except OSError:
        return None
    if len(named) != 1:
        return None
    try:
        name = json.loads(named[0].read_text(encoding="utf-8")).get("repo")
    except (OSError, ValueError, AttributeError):
        return None
    return name if isinstance(name, str) and name.strip() else None


def repo_name(root) -> str:
    """What this repo is called -- its own answer first, then the filesystem.

    Twice now a verdict has turned on the name of the directory a checkout
    happens to sit in, and the second time is what says the first fix was in
    the wrong place.

    First: a worktree of this repo at `…/wt-d1` ran `v4 doctrine` -- which
    writes when given no flag -- and rewrote `CLAUDE.md`'s title from the
    repo's name to `wt-d1`, leaving the file modified in git and
    `registry-consistency` failing in every worktree afterwards. An adopter
    running eight worktrees worked around it by naming every one of them after
    the repo. The repair asked `git rev-parse --git-common-dir`, whose parent
    is the main worktree -- one ledger per repo, the same question
    `ledger.ledger_path` asks.

    Second: the published tree, cloned into `verify-public`. Same file, same
    failure -- `CLAUDE.md is not what v4 doctrine generates`, because the title
    renders the directory's name -- and `v4 accept` at 6/7 on a fresh clone.
    The common dir's parent is still a directory name; `git clone <url>
    my-name` and GitHub's Download ZIP, which unpacks to `<repo>-main/`, both
    walk straight through it.

    So the derivation was never the fix. A repo that has written a facts table
    has already said what it is called, and that declaration travels with the
    bytes. Git answers only for a repo that has not; a bare directory answers
    only when git cannot.
    """
    declared = declared_repo_name(root)
    if declared:
        return declared
    root = Path(root)
    try:
        common = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                                cwd=root, capture_output=True, text=True,
                                check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return root.resolve().name
    p = Path(common)
    if not p.is_absolute():
        p = (root / p).resolve()
    return p.parent.name or root.resolve().name
