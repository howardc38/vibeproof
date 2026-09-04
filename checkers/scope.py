#!/usr/bin/env python3
"""Every changed path must sit inside the task's declared scope, and outside
protected_paths.

Protected paths matter more than they look: .v4/ holds test_command, and
checkers/ holds the programs that judge everything. Widening scope into either
is widening scope into your own judge, so it needs a signature (ACCEPTED_RISK
kind=scope_widen_protected), not a flag.
"""
import argparse, json, subprocess, sys
from fnmatch import fnmatch
from pathlib import Path

# One owner for the default set. This checker used to carry its own `[]`, which
# meant the program issuing the verdict protected nothing in any repo that had
# not written the key.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel import scope as scope_mod  # noqa: E402
from kernel.hashing import kernel_written  # noqa: E402
from kernel.analysis import subject_files  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--subject", required=True); p.add_argument("--facts"); p.add_argument("--out")
a = p.parse_args()
try:
    s = json.loads(Path(a.subject).read_text())
    root = Path(s["repo_root"])
    cfg = json.loads((root / ".v4/config.json").read_text())
    scope = s.get("params", {}).get("scope_globs") or ["**"]
    # The negative of scope. `scope` is an allowlist and answers "may work go
    # here"; this answers "was this named as out of bounds", which is a
    # different sentence and the only one a request's "do not touch X" can be
    # written as. Checked separately so the message can say which it was.
    forbid = s.get("params", {}).get("forbid_globs") or []
    # Merged with the framework's own, never replaced, and never defaulting to
    # empty. This checker issues the verdict, and its default was `[]` while
    # kernel/config.py and kernel/scope.py both used scope_mod.PROTECTED_DEFAULT -- so a
    # repo that never wrote the key had zero protected paths according to the
    # one program whose answer counts, including `.v4/config.json`, the file
    # naming the sole test oracle. And `get` replaces: an adopter declaring one
    # path of their own silently dropped all four of the framework's.
    protected = scope_mod.protected_for(cfg)
    base = s.get("diff_base") or "HEAD"
except Exception as exc:
    print(f"cannot read subject/config: {exc}", file=sys.stderr); sys.exit(5)

#: `git` failing and `git` reporting nothing were the same value, and this is
#: the program that issues the protected-path verdict. Measured: `git diff
#: --name-only <unresolvable-base>` exits 128 with empty stdout, so a rewritten
#: history, a shallow clone or a missing `base_commit` made `changed` empty,
#: printed "no changes against <base>" and exited 0 -- PASS, from a diff it
#: never read. `checkers/secret_scan.py` and `checkers/fail_closed.py` both
#: spend paragraphs on why that state is exit 4, which is what it means: I
#: cannot verify this. Answering it as "clean" is the one direction that costs
#: something.
class DiffUnreadable(RuntimeError):
    pass


def git(*args):
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        raise DiffUnreadable(
            f"`git {' '.join(args)}` exited {r.returncode}"
            + (f": {r.stderr.strip().splitlines()[0][:160]}" if r.stderr.strip() else ""))
    return r.stdout.splitlines()


try:
    # The kernel's, not a fifth copy. `subject_files.changed_since` asks the
    # same two questions -- the diff against the base, and what git has not
    # been told about -- and raises rather than returning an empty set, which
    # is the refusal this block is written around.
    changed = set(subject_files.changed_since(root, base))
except (DiffUnreadable, subject_files.DiffUnreadable) as exc:
    print(f"CANNOT VERIFY: {exc}\n\n"
          f"Scope is a statement about what changed against `{base}`, and that "
          f"diff could not be taken -- a rewritten history, a shallow clone, or "
          f"a base commit this checkout does not have. Reporting the empty "
          f"result as \"nothing changed\" would answer the claim from a diff "
          f"nobody read.\n\n"
          f"  fetch the base, or reopen the task so `base_commit` names a "
          f"commit this repo has.", file=sys.stderr)
    sys.exit(4)
# `--exclude-standard` honours .gitignore, and that was taken to mean build
# artefacts cannot appear here. It does not: a repo adopting this framework may
# have no .gitignore at all, and `v4 install` puts 89 Python files into it, so
# the first run wrote `detectors/__pycache__/*.pyc` and this checker reported
# them as somebody tampering with a protected path. `derive_exclude` is where
# the repo says what is not the code being judged, and it now travels in the
# subject so this is not a second opinion about it.
changed = set(subject_files.keep(s, changed))
# .v4/risks/ holds signature records. They land inside .v4/, which is
# protected, so counting them makes signing a claim fail the scope claim -- the
# act of accepting a risk would report itself as tampering with the judges.
# Kernel output under .v4/ is not a change a person made, and reporting it as
# tampering with the judges makes recording an answer look like an attack.
#
# The list itself comes from kernel/hashing.py. This file used to carry its own
# copy, and it went out of date the day `ship` started writing
# .v4/ledger_export.jsonl: the digest side excluded it and this side did not.
changed = {c for c in changed if c.strip() and not kernel_written(c, root)}
if not changed:
    print(f"no changes against {base}"); sys.exit(0)

#: The program issuing the scope verdict asks the same question as everything
#: else. Its own spelling was `g.replace("**", "*")`, which no other copy
#: had, and it disagreed with `subject_files` on every `**/...` glob -- so a
#: root-level file in a `**/*.py` task was reported outside scope by the
#: answer of record and inside it by everything that fed the answer.
matches = subject_files.matches

bad = [c for c in sorted(changed) if matches(c, protected)]
forbidden = [c for c in sorted(changed) if matches(c, forbid) and c not in bad]
out = [c for c in sorted(changed) if not matches(c, scope)
       and c not in bad and c not in forbidden]
if a.out:
    Path(a.out).write_text(json.dumps(
        {"changed": sorted(changed), "outside_scope": out, "protected": bad,
         "forbidden": forbidden}, indent=2))
if bad or out or forbidden:
    n = len(bad) + len(out) + len(forbidden)
    # Not "outside the declared scope": a protected or forbidden path can be
    # squarely inside it -- `.v4/**` after a signed widen, or a path the task
    # named as off-limits and then widened onto. An operator who reads the
    # headline goes and widens, and widening is what already happened.
    print(f"FAIL: {n} path(s) this task may not change.\n")
    for c in bad:
        print(f"  {c}  [protected: changing this changes what judges you]")
    for c in forbidden:
        print(f"  {c}  [the task declared this out of bounds]")
    for c in out:
        print(f"  {c}  [not in {scope}]")
    if bad:
        # The way past this was printed by `v4 scope widen` and nowhere else,
        # and a protected path reaches this claim by two routes: widened onto
        # after the task opened, or named in the scope the task opened with.
        # The second route never runs `widen`, so it never saw the sentence.
        # Measured on an adopter: a task scoped to `.v4/config.json` alone ran
        # `check` 57 times against a line that says what is wrong and not what
        # to do. The condition is what owns this text, not the command.
        # "Write it with no task open" used to be offered here as the first way
        # forward. It was an accurate description of a hole rather than a way
        # forward: nothing declared such a write, nothing judged it, and
        # `hooks/write_block.py` now refuses a protected path when no task is
        # open for exactly that reason. Printing an escape a gate has closed is
        # the same defect as printing one the CLI never took.
        print("\nA protected path is not a narrow scope: it judges the work, so "
              "widening alone does not reach it. One way forward:\n"
              "  - keep the task and sign for the reach:\n"
              "      v4 risk accept --claim <the scope claim> "
              "--kind scope_widen_protected --why '<40+ chars>'\n"
              "    That refuses without a terminal, and the three ways past it "
              "are not equal: `--no-tty-check` records `signed_by: agent` and "
              "a note saying `who` came from git config; a pty records "
              "`person` for a signature no person gave; `--as-monitor` records "
              "`monitor` and belongs to a session that did not do this work, "
              "so a worker passing it writes a false line into a committed "
              "file.\n"
              "  Writing it with no task open is not the other way: the write "
              "hook refuses a protected path when nothing has declared the "
              "work, which is the state a review or monitor session is always "
              "in.")
    if forbidden:
        print("\nA forbidden path is not a narrow scope: widening cannot reach "
              "it, because the task said at the start that this was the thing "
              "not to touch. Reopen the task if that was wrong.")

    # Paths that entered this diff by being committed after the task opened.
    # They can never leave it: `base_commit` is written once and the ledger is
    # append-only, so every commit made since is in `git diff <base>` forever.
    # Widening does not help either -- the path is not outside the scope, it is
    # somebody else's finished work sitting between the base and now.
    #
    # Measured: a task opened at 50bd7b82, three commits later its `scope`
    # claim listed seven paths, two of them protected. The operator read the
    # FAIL, fixed nothing (there was nothing to fix), ran `check` again, and
    # each attempt cost eleven minutes because forcing the cheap claim green
    # meant `--all` and `--all` runs the suite. Twice. Both of the facts needed
    # to stop were already known -- "commit does not remove a path from a diff"
    # and "ship needs every claim terminal" -- and nothing here put them
    # together.
    try:
        since_base = set(git("diff", "--name-only", f"{base}..HEAD"))
    except DiffUnreadable:
        since_base = set()
    frozen = sorted(since_base.intersection(bad + forbidden + out))
    if frozen:
        try:
            n_commits = len(git("rev-list", f"{base}..HEAD"))
        except DiffUnreadable:
            n_commits = 0
        print(f"\n{len(frozen)} of these were committed after this task opened "
              f"-- {n_commits} commit(s) ago -- so they are in `git diff "
              f"{base[:12]}` and cannot leave it:")
        for c in frozen:
            print(f"  {c}")
        print("This claim will not go green on this task, and widening will not "
              "reach it: `base_commit` is written once and the ledger is "
              "append-only. The way out is a new task on the current HEAD -- "
              "`v4 abandon --task <id> --why '…'`, then `v4 task --id <new>`.")
    sys.exit(1)
print(f"all {len(changed)} changed path(s) inside scope")
sys.exit(0)
