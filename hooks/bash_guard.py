#!/usr/bin/env python3
"""Refuse a shell command that writes a protected path.  SPEC.md §5.

`hooks/write_block.py` watches Write and Edit. One `sed -i .v4/config.json` goes
nowhere near it, and that file names the sole test oracle for every `test`
claim. The `scope` checker catches it at ship, so this is early warning rather
than a boundary -- but a boundary is not what is missing here. What was missing
is that a worker could reach the judge through a tool nobody was watching.

Same contract as the write hook: PreToolUse reads
`hookSpecificOutput.permissionDecision`, and exit 0 always. A guard that
crashes must not become a guard that blocks every command.

`main` now refuses on two grounds rather than one, and the second is the
larger. `writes_to_protected` answers `None` when a protected path is named and
it cannot say the command only reads it -- an interpreter, `sudo`, anything on
neither roll call -- and `python3 -c "open('.v4/config.json','w')"` was measured
walking through this hook while `sed -i`, `tee` and `>` on the same file were
all denied. An argv cannot say what a program handed to it will open, so no
list of writers was ever going to reach it. The refusal text has to carry that
difference, because "rewrite it plainly" is not advice a `python3 -c` can act
on: the way out is to put the path on a command that only reads.
"""

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _framework                                           # noqa: E402
except ImportError:                                             # noqa: E402
    # The one import a hook cannot guard with the thing it imports. A hook
    # shipped without its siblings has to fail the way a hook that cannot
    # reach the kernel fails -- allowing, and saying so on stderr -- rather
    # than dying before `main`, which prints nothing at all and is read as
    # approval by the agent that launched it.
    _WHY = f"hooks/_framework.py is not beside {Path(__file__).name}"
    # `repo_root` too: without `_framework` there is no `git rev-parse` answer
    # to fall back on, and the directory this file was copied into is still a
    # better guess than the caller's cwd -- which is the value this whole
    # repair is removing. The hook stands down either way; it should stand
    # down naming the right tree.
    # `record_seen` and `is_open` are in here because this hook calls them on
    # exactly the path this fallback exists for. Omitting them turned an import
    # failure -- the case the fallback is *for* -- into an `AttributeError`
    # inside the handler that was reporting it, and the reason never reached
    # anybody. A stand-in that records nothing is honest; one that does not
    # exist is a second failure on top of the first.
    _framework = SimpleNamespace(on_path=lambda r: _WHY, ledger=lambda r: None,
                                 config=lambda r: None, why=lambda r: _WHY,
                                 home=lambda r: None, db_path=lambda r: None,
                                 record_seen=lambda *a, **k: _WHY,
                                 is_open=lambda r, t: True,
                                 repo_root=lambda: Path(__file__).resolve().parent.parent)



#: `NAME=value` in front of a command. POSIX allows zero or more of them.
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _program_name(cmd: str) -> str:
    """What this command runs, for the row that says the guard saw it.

    `cmd.split()[0]` is not the program when the shell was handed assignments
    first: `PGPASSWORD=hunter2 psql …` gives `PGPASSWORD=hunter2`, and that
    string then goes into an append-only table that is exported to a committed
    file. Not hypothetical -- an adopter's committed export already carries nine
    `hook_seen` rows whose "program name" is an assignment (`V4_HOME=…`,
    `PYTHONPATH=…`, `SP=…`). Those are paths; the next one is a password.

    `secret` cannot catch it afterwards: it reads changed files, and a value
    this hook wrote into the ledger is not one. This is a route around this
    repo's own secret gate, and the repair is to not take the wrong word in the
    first place.

    A command that is nothing but assignments has no program, and says so
    rather than reporting the last assignment as one.
    """
    words = cmd.split()
    i = 0
    while i < len(words) and _ASSIGNMENT.match(words[i]):
        i += 1
    if i >= len(words):
        return "(no command)" if not words else "(assignments only)"
    return words[i][:80]


def _mark(root: Path, cmd: str, *, allowed: bool, reason: str = "",
          basis: str = "", session: str = ""):
    """Record that this guard ran, against every open task.

    This hook wrote no mark on any path -- not on allow, not on the deny it
    exists for -- so the one guard covering the shell route to a protected path
    left nothing in the ledger: `v4 ship` counts only `write_block`'s marks when
    it decides DEGRADED, `trend.gate` reports refusals from `write_block` only,
    and a deny that actually stopped `sed -i .v4/config.json` was unrecoverable
    afterwards. `write_block._record_seen` states the principle -- "whether a
    hook is installed is configuration; whether it fired is a fact" -- and the
    sibling implemented it.

    A shell command names no task, and `V4_TASK` may be pointing anywhere, so
    the mark goes against each open task rather than a guess. The command is
    truncated: this lands in an append-only table that is exported to a
    committed file, and a shell line is the most likely place in this payload
    for a credential to be sitting.
    """
    ledger = _framework.ledger(root)
    # A repo that has never adopted v4 has nothing to be accountable to, and
    # writing the mark anyway is what created a ledger where there was none:
    # `record_seen` opens the writable door, correctly, because it *is* a
    # write -- so the repair for "a read through the writing door" fixes the
    # read and leaves the write, and the measured symptom stands. Measured
    # again after fixing only the read: still a 65,536-byte `.git/v4/ledger.db`
    # in a fresh `git init` tree. `.v4/` is the adoption, and its absence is
    # not a failure this guard reports -- it is a repo this guard is not for.
    if not (Path(root) / ".v4").is_dir():
        return

    ids = []
    if ledger is not None:
        try:
            # `connect_readonly`, not `connect`. One read -- `open_task_ids` --
            # went through the door that runs `executescript(SCHEMA)`, the
            # append-only triggers, `commit()` and `_migrate()`. Measured: with
            # `V4_REPO` pointed at a fresh `git init` tree holding no `.v4/`,
            # this guard printed "allowed without checking", exited 0, and left
            # a 65,536-byte `.git/v4/ledger.db` behind in a repo that has never
            # adopted v4. `connect_readonly`'s own docstring names this shape as
            # the defect it exists for.
            conn = ledger.connect_readonly(root)
            ids = ledger.open_task_ids(conn)
            conn.close()
        except Exception:                                       # noqa: BLE001
            ids = []
    # `cmd.split()[0]` on an empty command raises `IndexError` before
    # `record_seen` is reached, which is precisely the case the unreadable-payload
    # paths hand it: they know there is no command, that is what they are
    # reporting. So the repair that made those paths loud left them still
    # unrecorded, and the caller's `except` swallowed the reason. A command
    # nobody could read has a name for the row -- it is what happened.
    head = _program_name(cmd)
    for tid in ids or [None]:
        failed = _framework.record_seen(root, tid, head,
                                        allowed=allowed, reason=reason,
                                        basis=basis, session=session,
                                        scattered=True, hook="bash_guard")
        if failed:
            print(f"v4 bash_guard: no mark written -- {failed}", file=sys.stderr)
            return


def _allowed_without_checking(why: str, basis: str, session: str = ""):
    """Say the allow out loud, record it if the ledger is reachable, answer.

    The answer comes first and unconditionally. A guard that cannot write its
    own mark still has to reply -- writing the mark before printing left the
    hook exiting 1 with an empty stdout on exactly the inputs it could not
    read, which is worse than the silence it was repairing: the caller then has
    no verdict at all rather than an allow it can see.
    """
    print(f"v4 bash_guard: allowed without checking -- {why}", file=sys.stderr)
    print("{}")
    sys.stdout.flush()
    try:
        _mark(_framework.repo_root(), "", allowed=True, basis=basis,
              session=session)
    except Exception as exc:                                    # noqa: BLE001
        print(f"v4 bash_guard: and the mark did not land either "
              f"({type(exc).__name__}: {exc})", file=sys.stderr)


def main():
    # The rule this file states forty lines down, applied to its own first
    # branch: allowing on failure is right, allowing *silently* is not. These
    # two returns printed `{}` and exited 0 with no stderr and no `hook_seen`
    # row -- measured by piping in an empty body, `{not json`, and
    # `{"tool_input": {}}`: three ways for the guard to be handed nothing it
    # can read, and three allows indistinguishable from a clean command.
    #
    # A payload for another tool stays quiet, because that is not a failure to
    # read: it is an event this hook is not about, and one line per tool call
    # would drown the record it is trying to keep.
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:                                    # noqa: BLE001
        _allowed_without_checking(
            f"the payload did not read ({type(exc).__name__}: {exc})",
            "unreadable payload")
        return 0
    if payload.get("tool_name") != "Bash":
        print("{}")
        return 0
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    session = str(payload.get("session_id") or "")
    if not cmd.strip():
        _allowed_without_checking("a Bash event carrying no command",
                                  "no command in payload", session)
        return 0

    # Allowing on failure is right -- a guard that crashes must not block every
    # command. Allowing *silently* is not: it makes a dead guard and a clean
    # command look the same, which is how this one sat broken in every adopting
    # repo. stderr from a hook reaches the agent's transcript, so the allow is
    # still an allow and the reason is on the record.
    root = _framework.repo_root()
    why = _framework.on_path(root)
    if why is None:
        try:
            from kernel.analysis.shell_command import writes_to_protected
            from kernel.config import RepoConfig
            cfg = RepoConfig(root)
            hits = writes_to_protected(cmd, cfg.protected)
        except Exception as exc:                                # noqa: BLE001
            why = f"{type(exc).__name__}: {exc}"
    if why is not None:
        print(f"v4 bash_guard: allowed without checking -- {why}", file=sys.stderr)
        _mark(root, cmd, allowed=True, basis="unreadable", session=session)
        print("{}")
        return 0

    if hits == []:
        _mark(root, cmd, allowed=True, basis="no protected path", session=session)
        print("{}")
        return 0

    if hits is None:
        reason = (
            "This command names a protected path and this guard cannot say it "
            "only reads it. It is refused because it cannot be read, not "
            "because it is wrong.\n\n"
            "Two ways to arrive here. The command runs something this guard "
            "does not get to see -- an interpreter (`python3 -c`, `node -e`), "
            "or `sudo`/`env`/`xargs` in front of something else; an argv says "
            "nothing about what a program it is handed will open. Or it uses "
            "syntax this guard does not parse: command substitution, `eval`, a "
            "heredoc, an unbalanced quote.\n\n"
            "Put the protected path on a command that only reads -- `cat`, "
            "`head`, `grep`, `git log`, and `cat x | sed ...` rather than `sed "
            "... x` -- or make the change through Write/Edit where the scope "
            "hook can see it.")
    else:
        listed = "\n".join(f"  {p}   {why}" for p, why in hits)
        reason = (
            f"This command writes a protected path:\n\n{listed}\n\n"
            f"Those files are what judge the work: `.v4/` holds the test "
            f"command, `checkers/` and `detectors/` are the programs that "
            f"decide. Changing them through a shell is the one route the write "
            f"hook cannot see.\n\n"
            f"  v4 --repo . scope widen --task $V4_TASK --add <path> "
            f"--why '<why this belongs in this task>'")

    # `unparseable` was the whole of the `None` case and is now the smaller
    # half of it: most refusals on this branch are a command on neither roll
    # call, which parses fine. A basis is read back by equality, so it has to
    # say the thing that is now true of every row carrying it.
    _mark(root, cmd, allowed=False, reason="protected path",
          basis="cannot tell" if hits is None else "protected",
          session=session)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
