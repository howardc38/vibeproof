#!/usr/bin/env python3
"""Refuse to end a turn on a task that was never shipped.

Every gate in this system hangs off `v4 ship`, and until now nothing required
anybody to reach it. A worker could run `v4 check`, read three FAILs, and stop
with "the main parts are done". The ledger stays clean, the chain verifies,
`v4 audit` passes -- because nothing was ever forged. Nobody tried to ship.

That makes the whole apparatus optional, which is a different failure from any
it was built to catch: not a false answer, an absent one.

The predecessor had this and it was the only mechanism answering "is the work
finished at the moment the agent decides to stop". Its wording is worth keeping:
complete it, or record a FAIL before stopping. Recording the failure is a valid
ending. Walking away without either is not.

Reads a Claude Code Stop payload on stdin, answers on stdout:

    {"decision": "block", "reason": "..."}   claims still open
    {}                                        nothing owed

Exit 0 either way. A hook that crashes must not become a hook that blocks
every turn, because that is a hook somebody switches off.

It blocks once. The payload carries `stop_hook_active` when this hook already
blocked the stop it is being asked about again, and the first version threw the
payload away -- so of the three endings it offered, two were unreachable: a
worker that recorded the FAIL and said so plainly got the identical message and
stopped again, forever. Same input, same output, no way out but the one ending
the message did not list. Blocking twice is not twice the friction; it is a
loop, and this is friction rather than a boundary (SPEC.md §13).
"""

import json
import os
import sqlite3
import subprocess
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
    _framework = SimpleNamespace(on_path=lambda r: _WHY, ledger=lambda r: None,
                                 config=lambda r: None, why=lambda r: _WHY,
                                 home=lambda r: None, db_path=lambda r: None, repo_root=lambda: Path(__file__).resolve().parent.parent)

TERMINAL = {"ANSWERED", "RISK_ACCEPTED", "RETRACTED"}


def _child_env(repo_root: Path):
    """The environment for `python -m kernel.cli`, with the framework on it.

    Where the framework lives is `_framework.home` -- imported at the top of
    this file, and it was not asked. This built the `PYTHONPATH` from the home
    marker alone and fell back to the repo root, so an adopter with no marker
    (`v4 install` writes one machine's absolute path, so anybody else's clone
    has none) handed the child a path with no `kernel` on it and the gate stood
    down every turn.

    `None` leaves `PYTHONPATH` alone rather than naming a directory nobody
    chose. `_states` then reports what the child said, which is the difference
    between a gate that stood down for a reason and one nobody can account for.
    """
    env = dict(os.environ)
    home = _framework.home(repo_root)
    if home:
        env["PYTHONPATH"] = home + (os.pathsep + env["PYTHONPATH"]
                                    if env.get("PYTHONPATH") else "")
    return env
#: Two or more tasks open and nothing saying which one this turn was about.
#:
#: The same object, under the same name, as `hooks/write_block.py` -- one rule
#: with two implementations is what the finding this replaces was about, and
#: the two must give the same answer: refuse rather than pick.
AMBIGUOUS = object()


def _open_task(repo_root: Path, session: str = ""):
    """The one task that has not ended, `AMBIGUOUS`, or None.

    This called `ledger.open_task_id`, whose own docstring ends "callers that
    must not guess ask `open_task_ids` and refuse on more than one" -- which is
    exactly what `hooks/write_block.open_task` does. This gate did the opposite
    of what the function it called told it to do: it took the newest and said
    nothing, so with more than one task open it guarded whichever task had been
    opened last, and `_worked_here` was then asked whether the session had
    worked on a task nobody had claimed it was working on.

    Measured 2026-08-27: one session with three tasks open was handed a task it
    had never touched and asked whether to ship it.

    The session narrows before the refusal, because refusing is friction and it
    should land on the person it is about. `sessions_on` says which of the open
    tasks carry a mark from *this* session; exactly one means the question is
    answered and nothing was guessed. Zero or several means it is not, and then
    this returns `AMBIGUOUS` and the caller says so -- once -- rather than
    picking. That narrowing is only worth anything because `sessions_on` no
    longer counts the marks a hook writes against every open task when it too
    could not tell; before that every open task looked like this session's.

    What counts as ended is `ledger.ENDED_TASKS_SQL`, not a copy of the query
    here.
    """
    db, ledger = _framework.db_path(repo_root), _framework.ledger(repo_root)
    if db is None or ledger is None:
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            ids = ledger.open_task_ids(conn)
            if len(ids) <= 1:
                return ids[0] if ids else None
            mine = [t for t in ids
                    if session and ledger.sessions_on(conn, t, session)[1]]
            return mine[0] if len(mine) == 1 else AMBIGUOUS
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _open_ids(repo_root: Path):
    """Every task this ledger still has open -- for the refusal to name them."""
    db, ledger = _framework.db_path(repo_root), _framework.ledger(repo_root)
    if db is None or ledger is None:
        return []
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return ledger.open_task_ids(conn)
        finally:
            conn.close()
    except sqlite3.Error:
        return []


def _ambiguous_text(repo_root: Path) -> str:
    """What `hooks/write_block.py` says for the same state, said for a stop.

    Not shared code: the two hooks answer different events with different
    payload shapes and neither imports the other. What is shared is the rule --
    with more than one task open, a guard that picks is a guard that is right
    until the moment it matters.
    """
    ids = _open_ids(repo_root)
    return (
        f"{len(ids)} tasks are open and nothing says which one this turn was "
        f"about:\n  " + "\n  ".join(ids) + "\n\n"
        f"Either finish the one that is done -- `v4 ship --task <id>`, or "
        f"`v4 abandon --task <id> --why '…'` if it will not ship -- or say "
        f"which one you were in:\n"
        f"  export V4_TASK=<id>\n\n"
        f"This used to take the newest and say nothing, so it asked about "
        f"whichever task happened to be opened last. If none of these is "
        f"yours, say so plainly in your reply and stop again -- this hook does "
        f"not ask twice.")


def _is_open(repo_root: Path, task_id: str):
    """Is `task_id` still open in this ledger?  `True` when it cannot be read.

    A hook that cannot tell must not start ignoring what it was told.
    """
    db = _framework.db_path(repo_root)
    ledger = _framework.ledger(repo_root)
    if db is None or ledger is None:
        return True
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return task_id in ledger.open_task_ids(conn)
        finally:
            conn.close()
    except sqlite3.Error:
        return True


def _worked_here(repo_root: Path, task_id: str, session: str):
    """Has *this* session done anything on this task?  `True` when it cannot tell.

    The gate resolves a task from the repo -- `V4_TASK`, or the one open task in
    the ledger -- and a repo is not a session. Two agents in one checkout, or an
    agent watching a repo it is not working in, both stop; both were handed
    whatever task happened to be open. Measured 2026-08-26: a session under a
    standing read-only instruction was told to `ship` a task, then to `derive`
    another, then to answer or sign thirteen claims on a third, more than twenty
    times, having touched none of them.

    `hook_seen` rows carry a `session` since the same day. A task with none of
    them -- opened before that, or worked without hooks -- returns `True`: this
    is friction being aimed, not a boundary being added, and aiming it by
    silently switching it off for older tasks would be the worse mistake.
    """
    if not session:
        return True
    db = _framework.db_path(repo_root)
    ledger = _framework.ledger(repo_root)
    if db is None or ledger is None:
        return True
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            tagged, mine = ledger.sessions_on(conn, task_id, session)
        finally:
            conn.close()
    except sqlite3.Error:
        return True
    if not tagged:
        return True                      # nothing to tell us apart
    return bool(mine)


def _repo_and_task(session: str = ""):
    root = _framework.repo_root()
    # The same rule `hooks/write_block.py` holds, and it was written there and
    # not here -- one rule, two implementations, one of them fixed. Measured on
    # the reference adopter: a shell still exported `V4_TASK=t-e2e-green` a day
    # after that task was abandoned, so this gate refused to let the session
    # stop until somebody answered fifteen claims belonging to a task that
    # already had an ending. It offered three ways out and every one of them
    # would have given a finished task a second ending, which the ledger cannot
    # hold. A name pointing at something over is not a disambiguation.
    named = os.environ.get("V4_TASK")
    if named and not _is_open(root, named):
        named = None
    return root, (named or _open_task(root, session))


def _states(repo_root: Path, task_id: str):
    """Ask the kernel, rather than reimplementing state derivation here.

    A second implementation of "is this claim answered" is a second answer, and
    the two would drift the first time either changed.

    Through `--json`, which `cmd_status` grew for exactly this consumer: its
    docstring says the flag exists because "which claim, in which state, and
    what is blocking it had to be recovered by parsing lines written for a
    human, and those lines change whenever the wording improves". This was the
    one caller doing that parsing, on `parts[0].isupper()`, and it was not
    moved over when the flag landed.

    The failure that made it worth moving is not a stand-down: a parse yielding
    zero rows makes `open_claims` empty at the call site, which takes the
    *other* block branch and tells the worker every claim is answered and it
    should ship. Wrong message, not no message.
    """
    # `timeout=120` raises `TimeoutExpired`, and `main` calls this unguarded, so
    # a slow `v4 status` crashed the hook -- which prints nothing, and nothing is
    # what the platform reads as allow. The module docstring states the contract
    # this broke: "a hook that crashes must not become a hook that blocks every
    # turn". It also must not become a hook that silently stands down without
    # saying so, and `None` is the value this function already has for "cannot
    # tell", handled at the one call site.
    try:
        r = subprocess.run(
            [sys.executable, "-m", "kernel.cli", "--repo", str(repo_root),
             "status", "--task", task_id, "--json"],
            cwd=repo_root, env=_child_env(repo_root), capture_output=True,
            text=True, timeout=120)
    except subprocess.TimeoutExpired:
        print(f"v4: `v4 status --task {task_id}` did not answer in 120s, so the "
              f"stop gate stood down for this turn", file=sys.stderr)
        return None
    except Exception as exc:                                    # noqa: BLE001
        print(f"v4: the stop gate could not read this task's state ({exc}), so "
              f"it stood down for this turn", file=sys.stderr)
        return None
    if r.returncode not in (0, 1):
        # Silence here was the third stand-down with no message, while the two
        # above both print. A `status` that died on a `ConfigError` (exit 5)
        # let the turn end looking exactly like a task with nothing owed.
        print(f"v4: `v4 status --task {task_id}` exited {r.returncode}, so the "
              f"stop gate stood down for this turn"
              + (f" -- {r.stderr.strip()[:200]}" if r.stderr.strip() else ""),
              file=sys.stderr)
        return None
    try:
        claims = json.loads(r.stdout)["claims"]
    except Exception as exc:                                    # noqa: BLE001
        print(f"v4: `v4 status --json` gave something this gate cannot read "
              f"({exc}), so it stood down for this turn", file=sys.stderr)
        return None
    return [(c["state"], c["id"], c["kind"]) for c in claims]


def _ended(repo_root: Path, task_id: str) -> bool:
    """Has this task been given an ending -- shipped **or** abandoned?

    This asked only for `shipped`, and the name said so, so a task somebody had
    deliberately closed still held the session: the gate exists to stop a turn
    ending on a task *nobody tried to end*, and `abandon` is one of the two ways
    to end one. `ledger.ENDED_KINDS` is the list, and asking it here rather than
    naming the kinds keeps this from being a fourth copy that drifts.

    Reachable whenever something names that task -- `V4_TASK` pointing at it, or
    a caller passing it in -- because `_open_task` would never return it. On the
    reference adopter both halves fired at once: a stale `V4_TASK` named an
    abandoned task, and this said that task was not finished.
    """
    db = _framework.db_path(repo_root)
    if db is None:
        return True
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        ledger = _framework.ledger(repo_root)
        kinds = getattr(ledger, "ENDED_KINDS", ("shipped", "abandoned"))
        marks = ", ".join("?" for _ in kinds)
        row = conn.execute(
            f"SELECT 1 FROM event WHERE task_id = ? AND kind IN ({marks}) LIMIT 1",
            (task_id, *kinds)).fetchone()
        conn.close()
        return row is not None
    except sqlite3.Error as exc:
        # Said, like the two stand-down paths above. `True` here means "treat
        # the task as ended", which is how this gate lets a turn finish -- so a
        # ledger it could not read and a task somebody shipped produced the
        # same silence. The answer stays the same (a hook that cannot do its
        # job must not block every turn); what changes is that it says so.
        print(f"v4: the stop gate could not tell whether {task_id} has ended "
              f"({type(exc).__name__}: {exc}), so it stood down for this turn",
              file=sys.stderr)
        return True


def refuse_the_stop(reason: str) -> int:
    """Refuse to let this turn end, in the one shape a Stop hook is read by.

    The decision this file makes, with a name on it. Three call sites in `main`
    built this JSON inline, and the consequence was not duplication: it was that
    the one entry surface in this repo that *refuses* had no symbol anything
    could name. `hooks/**` is in `entrypoint_globs` and both sibling hooks are
    covered by `auth_decision` rows (`.protected`, `writes_to_protected`), while
    this file appeared in that table nowhere -- so "who may end a turn" was
    decided here and written down in the one place the repo keeps its record of
    who may do what.

    `.v4/facts.vibeproof.json` names this symbol now. Keeping the shape in one
    function is what makes that row mean something: a fourth refusal added later
    reaches the declared decision by calling it, rather than by printing the
    same dict somewhere new.

    Exit 0, like every other ending here. The refusal is the JSON, never the
    exit code -- `hooks/write_block.py` carries the same sentence, and the
    predecessor's spec pins it: rebuilding a hook as `exit 1 = block` is unsafe.
    """
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def main():
    try:
        payload = json.load(sys.stdin) or {}
    except Exception:                                           # noqa: BLE001
        # An unreadable payload is not an empty one. `stop_hook_active` is the
        # field that stops this hook asking twice, and treating "cannot read it"
        # as "it was not set" is how a gate turns into the loop its own docstring
        # warns about.
        #
        # This read `payload = {}` and carried on. It was safe only because
        # `V4_TASK` was usually unset, so the next check exited first -- the
        # moment the task could be found without that variable, the crash path
        # started blocking. A hook that blocks when it cannot tell is a hook
        # somebody switches off.
        print("{}")
        return 0

    # Already said once. Saying it again to an agent that has read it and
    # decided is not friction, it is a loop -- and the ending it was told to
    # take ("record the FAIL and say so") is the one that produces this exact
    # payload a second time.
    if payload.get("stop_hook_active"):
        print("{}")
        return 0

    session = str(payload.get("session_id") or "")
    repo_root, task_id = _repo_and_task(session)
    if task_id is AMBIGUOUS:
        # Nothing here knows which task this turn was about, and the answer this
        # gate gives is the one `write_block` gives to the same state: say so.
        # Guessing is silent and costs the guard -- it guarded whichever task
        # was opened last, and `_worked_here` could not correct it, because the
        # marks it reads had been written against every open task by a hook
        # that could not tell either.
        print(json.dumps({"decision": "block",
                          "reason": _ambiguous_text(repo_root)}))
        return 0
    if not task_id:
        print("{}")
        return 0

    if _ended(repo_root, task_id):
        print("{}")
        return 0

    # Whose task this is. `V4_TASK` is this session saying so itself, and is
    # taken at its word; anything else came from the repo, and the repo does not
    # know who is asking.
    if not os.environ.get("V4_TASK") and \
            not _worked_here(repo_root, task_id, session):
        print("{}")
        return 0

    states = _states(repo_root, task_id)
    if states is None:
        print("{}")            # cannot tell; never block on a broken read
        return 0

    if not states:
        # Zero claims is not "every claim is answered". It is one of two things
        # and both are worth saying: `v4 derive` has not run on this task, or it
        # ran and its scope matched nothing a detector could raise about. The
        # old branch below is true of an empty list in the way that every
        # statement about an empty set is true, and it sent the worker to
        # `v4 ship` on work nothing had ever asked a question about.
        return refuse_the_stop(
            f"{task_id} holds no claims at all.\n\n"
            f"  v4 --repo . derive --task {task_id}\n\n"
            f"Either derive has not run here, or it ran and this task's "
            f"scope matched nothing any detector raises about -- and those "
            f"are different problems with the same silence. A task with no "
            f"claims passes every check in this system, because none of "
            f"them exist.\n\n"
            f"If the scope really is right and really raises nothing, say "
            f"so plainly in your reply and stop again: this hook does not "
            f"ask twice.")

    open_claims = [(s, cid, kind) for s, cid, kind in states if s not in TERMINAL]
    if not open_claims:
        return refuse_the_stop(
            f"Every claim on {task_id} is answered and it has not shipped.\n\n"
            f"  v4 --repo . ship --task {task_id}\n\n"
            f"Shipping is what records that the work is finished. Stopping "
            f"here leaves a task that passed everything and delivered "
            f"nothing.\n\n"
            f"If you are the worker, shipping is not yours to call "
            f"(SPEC.md §12.5) -- hand back and say the claims are answered. "
            f"This hook will not ask twice.")

    lines = "\n".join(f"  {s:<14} {cid}  {kind}" for s, cid, kind in open_claims[:8])
    return refuse_the_stop(
        f"{len(open_claims)} claim(s) on {task_id} are still open:\n\n{lines}\n\n"
        f"Three ways to end this properly, and all three are fine:\n"
        f"  answer them   -- fix the code, then `v4 --repo . check --task {task_id}`\n"
        f"  sign for one  -- `v4 --repo . risk accept --claim <id> --kind <kind> "
        f"--why '...'`\n"
        f"  say it failed -- leave the FAIL recorded and say so plainly in your reply\n\n"
        f"What is not fine is stopping with neither. A task nobody tried to ship "
        f"passes every check in this system, because none of them ran.\n\n"
        f"This hook will not ask twice: stop again and it lets you through. "
        f"The FAIL stays in the ledger either way, which is the record -- "
        f"your reply is not.")


if __name__ == "__main__":
    sys.exit(main())
