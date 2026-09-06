#!/usr/bin/env python3
"""Is the control plane still the size it said it would be?  SPEC.md §10.

The predecessor grew from 4,308 lines to 37,511 over the period this project
measured, and nothing in it was ever deleted -- while 65 of its gate refusals
came from two bookkeeping gates and none from the proof or security ones. Its
answer was a ceiling in a file, checked on every push, and the reason it gave is
the right one: every fail-closed rule here is paid by every adopter on every
task, and a cost nobody can see is a cost nobody argues about.

This project wrote a target into its own spec and then went 37% past it with
nothing measuring. A number in prose is not a ceiling.

The baseline is a committed file rather than a constant, for the same reason
every other delta checker here uses one: raising it should be a diff with a name
on the commit, and the diff is the argument.

Exit: 0 within the ceiling | 1 over it | 4 no baseline | >=5 broke.
"""

import argparse
import ast
import json
import sys
from pathlib import Path

#: What does not count. An allowlist of directories was the first version, and a
#: bypass fixture walked around it by putting the code in a directory nobody had
#: thought to name -- which is what every allowlist does eventually. Everything
#: this repo wrote counts unless it is on this list.
#:
#: Tests are excluded because a suite growing is not a control plane growing --
#: it is the opposite, and charging for it makes the cheapest way under the
#: ceiling be deleting tests.
NOT_COUNTED = ("tests", "docs", "__pycache__", ".venv", "node_modules",
               ".git", "site-packages", "build", "dist")


def measure(root: Path, subject=None):
    """Statements per top-level area, not lines.

    Lines were the first measure and a bypass fixture put two hundred statements
    on one of them. What is being paid for is the amount of control plane, and a
    semicolon does not make it smaller.

    Falls back to non-blank lines for anything that does not parse, so a syntax
    error in one file cannot make the whole repo look empty.
    """
    out = {}
    # `subject_files.tracked`, not `rglob` plus a hand-written list.
    # `NOT_COUNTED` named nine directories and `hashing.tree_state` records that
    # exact shape being removed -- "a hand-written list is the shape this
    # project keeps finding and removing, and it would have said nothing about
    # vendor/, target/ or .tox/". It also ignored `derive_exclude` entirely, so
    # in an adopter -- where `v4 install` copies the fixture sets to
    # `.v4/fixtures/**` -- every deliberately-broken red fixture counted against
    # that repo's own control-plane ceiling. And `rglob` reaches untracked bytes
    # on disk, which `git ls-files` does not.
    from kernel.analysis.subject_files import tracked
    for name in tracked(subject or {}, root, suffixes=(".py",)):
        rel = Path(name)
        f = root / rel
        # Against the repo-relative path, not the absolute one. The walk used
        # to be `root.rglob`, whose parts carry every ancestor directory, so a
        # repo checked out under any directory named `tests`, `docs`, `build`,
        # `dist`, `node_modules` or `.git` matched on its own parent and
        # measured zero statements -- total 0 against its ceiling, passing
        # forever. Reproduced: the same tree under `.../myrepo/proj` gave
        # `{"kernel": 3}` and under `.../build/proj` gave `{}`.
        #
        # `NOT_COUNTED` survives for `tests` and `docs`, which git tracks and
        # which are not control plane. What it no longer has to name is the
        # untracked half -- `.venv`, `node_modules`, `__pycache__` -- because
        # `git ls-files` never returns those.
        if set(rel.parts) & set(NOT_COUNTED):
            continue
        area = rel.parts[0] if len(rel.parts) > 1 else "(root)"
        text = f.read_text(encoding="utf-8", errors="replace")
        try:
            n = sum(1 for node in ast.walk(ast.parse(text))
                    if isinstance(node, ast.stmt))
        except SyntaxError:
            n = sum(1 for line in text.splitlines() if line.strip())
        out[area] = out.get(area, 0) + n
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--facts")
    ap.add_argument("--out")
    a = ap.parse_args()

    try:
        # The whole subject, not one key out of it. `measure` takes `subject`
        # so that `subject_files.tracked` can honour this repo's
        # `derive_exclude` -- which is the reason its own comment gives for
        # switching from `rglob` to `tracked`: in an adopter, `v4 install`
        # copies the fixture sets to `.v4/fixtures/**`, and every deliberately
        # broken red fixture was counting against that repo's own ceiling.
        # Reading `repo_root` alone meant `tracked(subject or {})` received
        # `{}` on every run and the exclusion never applied to anything.
        subject = json.loads(Path(a.subject).read_text())
        root = Path(subject["repo_root"])
    except Exception as exc:                                    # noqa: BLE001
        print(f"cannot read subject: {exc}", file=sys.stderr)
        return 5

    bpath = root / ".v4" / "control_plane_budget.json"
    if not bpath.is_file():
        print(f"no {bpath.name}; nothing declares a ceiling")
        return 4

    try:
        budget = json.loads(bpath.read_text())
        now = measure(root, subject)
    except Exception as exc:                                    # noqa: BLE001
        print(f"checker failed: {exc}", file=sys.stderr)
        return 5

    total = sum(now.values())
    ceiling = budget.get("ceiling")
    if a.out:
        Path(a.out).write_text(json.dumps({"measured": now, "total": total,
                                           "ceiling": ceiling}, indent=2))
    for area in sorted(now):
        print(f"  {area:<12} {now[area]:>6}")
    print(f"  {'total':<12} {total:>6}   ceiling {ceiling}")

    if ceiling is None:
        print(f"\n{bpath.name} has no `ceiling`")
        return 5
    if total > ceiling:
        # `statement(s)`, which is what `measure` counts. These two lines said
        # `line(s)`, and lines are the unit this checker was changed *away*
        # from -- a bypass fixture put two hundred statements on one of them. A
        # worker over the ceiling and told to remove lines removes comments,
        # which moves this number by nothing: the unit confusion the change to
        # statements was made to end, reintroduced by the two sentences a
        # worker actually reads.
        print(f"\nFAIL: the control plane is {total - ceiling} statement(s) over its "
              f"own ceiling.\n\n"
              f"Raising it is allowed and is a one-line diff. What is not "
              f"allowed is nobody noticing: every rule in here is paid by every "
              f"adopter on every task, and a cost nobody can see is a cost "
              f"nobody argues about.\n"
              f"  reason on record: {budget.get('why', '(none)')}")
        return 1
    print(f"\nwithin the ceiling, {ceiling - total} statement(s) of room")
    return 0


if __name__ == "__main__":
    sys.exit(main())
