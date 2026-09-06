#!/usr/bin/env python3
"""Refuse a write outside the task's scope, at the moment it is attempted.

SPEC.md §5 has called this the only intervention during coding since the first
draft, and until now no such file existed -- the `scope` checker was the whole
enforcement, and it runs at ship. The difference matters: at ship you learn that
twenty minutes of work went somewhere it should not have, and the cheapest way
out is to widen after the fact, which is the habit `v4 scope widen` exists to
make unnecessary rather than to launder.

What it is not: a boundary. A hook is platform configuration, an agent with file
access can edit it, and any platform without hooks has none of this. So the
`scope` checker stays as the answer of record and this is the early warning. A
task running without the hook is marked degraded rather than assumed clean --
"做唔到就記低做唔到" is the same rule the facts table follows.

Reads a Claude Code PreToolUse payload on stdin and answers on stdout:

    {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": "..."}}   outside scope
    {}                                                            allowed

**The shape is load-bearing.** PreToolUse reads
`hookSpecificOutput.permissionDecision`; the top-level `decision` field other
events use is not supported here. This file printed that field for its entire
existence, so it ran, decided to refuse, printed a refusal nothing reads, and
exited 0 -- which means allow. No error, no log, and no fixture would have
caught it: the registration gate tests checkers, and this is a hook.

Exit 0 either way: a hook that crashes must not become a hook that blocks
everything, and a hook that blocks everything is a hook that gets switched off.
A non-zero exit without a deny payload also fails open, so exiting 1 is not a
substitute for the payload.
"""

import fnmatch
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
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
    # `record_seen` and `is_open` for the same reason as in `bash_guard`: this
    # hook reaches both on the path this fallback is for, and a stand-in that
    # is missing them fails a second time while reporting the first.
    _framework = SimpleNamespace(on_path=lambda r: _WHY, ledger=lambda r: None,
                                 config=lambda r: None, why=lambda r: _WHY,
                                 home=lambda r: None, db_path=lambda r: None,
                                 record_seen=lambda *a, **k: _WHY,
                                 is_open=lambda r, t: True,
                                 repo_root=lambda: Path(__file__).resolve().parent.parent)

WRITE_TOOLS = {"Write", "Edit", "NotebookEdit", "MultiEdit"}

# Where the ledger is now comes from `_framework.db_path`. The body used to be
# a private `_ledger` here, and `stop_gate` held four more copies of the same
# eight lines -- so the file whose comments say "one rule, two implementations,
# one of them fixed" was itself the fifth site of another one. `_framework`
# prefers `kernel.ledger.ledger_path`, which owns where a ledger lives, and
# keeps the inline derivation for the case a hook exists for: no kernel in
# reach.


def current_scope(repo_root: Path, task_id: str):
    """`kernel.scope.current_scope`, or this file's own read of the same rows.

    The copy below carried a comment saying "kernel/scope.py holds the same
    read and the same reason; this file cannot import it" -- and since
    `hooks/_framework.py` exists that sentence is no longer true. The rule it
    keeps is one line long (a refused widen does not count) and it is exactly
    the kind of one-liner that gets fixed on one side, which is why it is asked
    of the owner whenever the owner is reachable.

    The fallback stays because a hook must work when the kernel does not: it
    fires from the coding agent's settings, in a process this framework did not
    start.
    """
    if _framework.on_path(repo_root) is None:
        conn = _framework.ledger(repo_root)
        if conn is not None:
            try:
                from kernel import ledger as _l, scope as _s
                db = _l.connect(repo_root)
                try:
                    return _s.current_scope(db, task_id)
                finally:
                    db.close()
            except Exception:                                   # noqa: BLE001
                pass
    db = _framework.db_path(repo_root)
    if db is None:
        return None
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT scope_globs FROM task WHERE id = ?",
                           (task_id,)).fetchone()
        if row is None:
            return None
        globs = json.loads(row["scope_globs"])
        for e in conn.execute(
            "SELECT kind, payload FROM event WHERE task_id = ? AND kind IN "
            "('scope_widen', 'scope_narrow') ORDER BY id", (task_id,),
        ):
            p = json.loads(e["payload"])
            if e["kind"] == "scope_widen":
                # A refused widen is recorded and must not count.
                # `kernel/scope.py` holds the same read and the same reason;
                # this file cannot import it, so the one thing to keep true
                # between them is this predicate.
                if (p.get("engagement") or {}).get("accepted", True):
                    globs += p["added"]
            else:
                # Both kinds, in one ordered pass, because the last statement
                # about a path is the one that counts. A fallback that knew
                # only about widening would keep allowing writes to a glob the
                # task had taken back -- and this hook and the kernel
                # disagreeing about the scope is the shape both of them exist
                # to prevent.
                dropped = set(p.get("dropped") or ())
                globs = [g for g in globs if g not in dropped]
        return globs
    except Exception as exc:                                    # noqa: BLE001
        # Every other read boundary in this file is `except Exception`, for the
        # reason written at each of them: a hook that cannot do its job must not
        # become a hook that blocks. This one had no handler at all, so one
        # malformed `scope_globs` or one malformed widen payload took the whole
        # process down -- and a hook that dies prints nothing, which the agent
        # reads as approval. The guard failed open and left no mark saying so.
        #
        # `basis: unreadable` is the mark in the ledger; this is the one on the
        # screen. A person watching a write go through deserves the same
        # sentence the row gets.
        print(f"v4: this task's scope could not be read "
              f"({type(exc).__name__}: {exc}), so the write hook is not "
              f"checking scope for this write", file=sys.stderr)
        return None
    finally:
        conn.close()


def in_scope(rel: str, globs, repo_root=None) -> bool:
    """`subject_files.matches`, or the hook's own two spellings if the
    framework is out of reach.

    A hook cannot depend on the kernel being importable -- that is what
    `_framework` is for -- but it must not answer a *different* question
    when it is. The fallback is the pair this file used to carry alone,
    and it is narrower: `**/*.py` reaches `x.py` through the owner and
    not through the fallback.

    `repo_root` rather than `Path(".")`. `on_path` resolves the home marker
    under whatever it is handed, so a hardcoded `.` made *which of the two
    matchers runs* depend on the shell's working directory -- which is the cwd
    dependency `_framework.repo_root` says it exists to remove. Both call sites
    already hold the resolved root. `None` keeps the old reading for a caller
    that has none, and there is no longer one in this file.
    """
    if _framework.on_path(Path(repo_root) if repo_root else Path(".")) is None:
        from kernel.analysis.subject_files import matches
        return matches(rel, globs)
    return any(fnmatch.fnmatch(rel, g) or
               fnmatch.fnmatch(rel, g.rstrip("/") + "/*") for g in globs)


def protected(repo_root: Path):
    """What this repo says judges the work, or `None` if it cannot be read.

    Not task-scoped, and that is the whole point. `.v4/config.json` names the
    sole test oracle whether or not anybody has a task open, and this hook read
    only `current_scope`/`in_scope` and stood down entirely when none was --
    which `ledger.ENDED_TASKS_SQL` makes the normal state for a review or
    monitor session, since it unions `repo-review` into the ended set.

    Measured before this: `sed -i .github/monitor/PROMPT.md` was denied by
    `bash_guard`, while a `Write` payload for the same path, and for
    `.v4/config.json`, both returned `{}` from here. `bash_guard`'s own refusal
    text asserts the opposite -- "changing them through a shell is the one route
    the write hook cannot see" -- and `.claude/settings.template.json` calls the
    two hooks 同一個問題嘅兩半. `checkers/scope.py:109` names the escape out loud:
    "write it with no task open, and commit that separately".

    `None` rather than `()` when the kernel is out of reach: a guard that cannot
    read the list has not decided that the list is empty, and the caller says so
    on stderr rather than allowing quietly.

    `docs/SPEC.md`'s hook table said the opposite of this function for as long
    as it existed -- "唔睇 protected, protected 嗰半歸 `bash_guard.py`" -- so the
    contract denied the behaviour of the guard it was describing, and a reader
    checking whether this deny was intended found the document saying it was
    not. The row now carries the asymmetry this function is half of: with a task
    open it stays a scope question, and with none it is this list.
    """
    mod = _framework.config(repo_root)
    if mod is None:
        return None
    try:
        return mod.RepoConfig(repo_root).protected
    except Exception:                                           # noqa: BLE001
        return None


def is_protected(rel: str, globs, repo_root=None) -> bool:
    """Does this path match one of the globs that judge the work.

    One question, one implementation. This was three lines byte-identical to
    `in_scope` under a different name, with a one-line docstring in place of
    the four-line one recording that the fallback is narrower -- so the known
    divergence was documented at one copy and invisible at the other. Which
    matcher a repo gets is a fact about the kernel being importable, and it
    cannot be allowed to differ between "is this in scope" and "is this
    protected" when both are asked of the same path in the same call.
    """
    return in_scope(rel, globs, repo_root)


def _protected_text(rel: str, globs) -> str:
    return (
        f"{rel} is one of the paths that judge this work.\n\n"
        f"  protected: {', '.join(globs)}\n\n"
        f"`.v4/` holds the test command and the claim registry; `checkers/` and "
        f"`detectors/` are the programs that decide; `.github/` holds the "
        f"workflow and the monitor contract. A task that edits them is a task "
        f"grading its own paper, and it costs a signature rather than a flag.\n\n"
        f"  v4 --repo . scope widen --task $V4_TASK --add {rel} "
        f"--why '<why this belongs in this task>'\n"
        f"  v4 --repo . risk accept --claim <id> --kind scope_widen_protected "
        f"--why '…'")


def unengaged(repo_root: Path, task_id: str):
    """Claims this task must have a sentence for before it writes anything.

    The rule says "Refused before the work, not after it" and the gate that
    enforced it sat in `v4 check`, which runs after the work. Measured over six
    tasks, writes before the first sentence: 14, 19, 9, 29, 15, 22 -- and two of
    those tasks wrote every file they were ever going to write before writing a
    sentence, so engagement could not have changed a decision in them. Six
    claims failed their first check with a sentence already on record arguing
    the code was right.

    Two shapes of claim, and only one of them can be gated here. A claim about
    code that already exists is raised at the first derive: measured, 37 of 43.
    A claim about code this task is creating cannot exist until the code does --
    the detector reads the tree -- so it is answered afterwards by necessity.

    Once a write is allowed with something to check, the gate is spent and never
    asks again, so the fix loop after a later derive is not interrupted.

    This used to read "before the first write, the only claims that can exist are
    the first kind", and spend the gate on any allowed write at all. That premise
    is false: `derive` is a separate command, so a write can land while the task
    still has no claims. Measured on one adopter, 2 of 19 tasks -- both of them
    doc-editing tasks, where a worker has no detector to wait for and starts
    typing. Both spent the gate on a write that had nothing to check, and neither
    was asked for a sentence again; six engagement-carrying claims appeared 74
    seconds later in one of them. Nothing enforces the ordering, so the gate
    cannot assume it.

    Returns `(owed, basis)`. `owed` is `None` when this cannot be established --
    a hook that cannot tell must not block -- and `basis` says why: `unreadable`,
    `spent`, `no-claims`, `cleared`, or `owed`. The basis is recorded, because
    `allowed` alone could not tell "nothing was owed" from "nothing was visible
    yet", and those are the two states this whole check turns on.
    """
    db = _framework.db_path(repo_root)
    if db is None:
        return None, "unreadable"
    try:
        kinds = json.loads((repo_root / ".v4" / "claim_kinds.json")
                           .read_text(encoding="utf-8"))
        kinds = kinds.get("kinds", kinds)
    except Exception:                                           # noqa: BLE001
        return None, "unreadable"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # Only a write that was allowed *with something to check* spends the
        # gate -- and only rows this version can read get judged by this
        # version's rule. A row written before `basis` existed, or before
        # `allowed` existed, records a write that already went through under
        # the rule of its day; re-reading it as unspent would stop a task
        # mid-flight and ask for a sentence about code already written, which
        # is the exact timing this gate exists to remove. An absent field is
        # not a false value, it is "cannot tell" -- and a hook that cannot tell
        # must not block.
        spent = conn.execute(
            "SELECT 1 FROM event WHERE task_id = ? AND kind = 'hook_seen' AND ("
            "  json_extract(payload, '$.basis') = 'cleared'"
            "  OR (json_extract(payload, '$.basis') IS NULL"
            "      AND (json_extract(payload, '$.allowed') = 1"
            "           OR json_extract(payload, '$.allowed') IS NULL)))"
            " LIMIT 1", (task_id,)).fetchone()
        if spent:
            return [], "spent"
        out, asked = [], 0
        for row in conn.execute(
            "SELECT id, kind, file, symbol FROM claim WHERE task_id = ? "
            "ORDER BY kind", (task_id,),
        ):
            if not (kinds.get(row["kind"]) or {}).get("engagement"):
                continue
            asked += 1
            # `engagement.accepted_for` in one query: the newest sentence for
            # this claim, and only if it was accepted. A refused one is on the
            # record and does not count, which is the same answer the kernel
            # gives at `v4 check`.
            e = conn.execute(
                "SELECT payload FROM event WHERE claim_id = ? AND "
                "kind = 'engagement' ORDER BY id DESC LIMIT 1",
                (row["id"],)).fetchone()
            if e and (json.loads(e["payload"]) or {}).get("verdict") == "accepted":
                continue
            out.append({"id": row["id"], "kind": row["kind"],
                        "where": row["file"] or row["symbol"] or ""})
        if out:
            return out, "owed"
        # `asked` is the whole point of the split: zero means the detectors have
        # not run yet and this write proves nothing about engagement, so it does
        # not get to close the gate on the claims that arrive after it.
        return [], ("cleared" if asked else "no-claims")
    except Exception as exc:                                    # noqa: BLE001
        # `sqlite3.Error` was the whole boundary, and the loop above calls
        # `json.loads` on a payload: `issubclass(json.JSONDecodeError,
        # sqlite3.Error)` is False, so one malformed engagement record raised
        # through `main` and killed the hook. Same failure as `current_scope`
        # and the same answer -- cannot tell, do not block, and say so.
        print(f"v4: the engagement rows for this task could not be read "
              f"({type(exc).__name__}: {exc}), so the write hook is not "
              f"holding this write for a sentence", file=sys.stderr)
        return None, "unreadable"
    finally:
        conn.close()


#: Two tasks open and no `V4_TASK`: the guard has no way to know which one this
#: write belongs to, and the old answer was to pick the newer and say nothing.
AMBIGUOUS = object()

#: The ledger raised, or the kernel is out of reach: the guard has no way to
#: know whether a task is open at all. `None` is a fact about the ledger -- it
#: was read and holds no open task -- and returning it for a read that never
#: happened is the same conflation `current_scope` already has a `basis` for.
UNREADABLE = object()


def _is_open(repo_root: Path, task_id: str):
    """Is `task_id` a task this ledger still has open?

    `None` when the ledger cannot be read, and the caller treats that as open --
    a hook that cannot tell must not start ignoring what it was told.
    """
    # `_framework.is_open`, not a copy. This function and its twin in
    # `stop_gate` said the same sentence and did two different things with it:
    # the connect was outside the `try` here, so a database that will not open
    # raised out of the hook rather than answering `True` -- the one case the
    # docstring above is about.
    return _framework.is_open(repo_root, task_id)


def open_task(repo_root: Path):
    """The newest task that has not ended, `AMBIGUOUS`, `UNREADABLE`, or None.

    `V4_TASK` wins when it names a task that is still open -- explicit beats
    inferred. It used to be the only source, which made the whole hook depend on
    a person exporting an environment variable before every task: unset it and
    the hook allows every write, silently, with no sign anywhere that it stopped
    guarding. That is the wrong layer. "You will forget" is what a program is
    for.

    What it must not do is win when it names a task that has *ended*. Measured
    on the reference adopter: a shell exported `V4_TASK=t-e2e-green`, that task
    was abandoned, a new one was opened, and every write for the whole of the
    new task was checked against the abandoned task's scope and denied. The
    worker could not write a line and the hook's own message named a task the
    ledger says is over. An environment variable outliving its task is the
    ordinary case -- shells outlive tasks -- so this is not a rare corner.

    A name that points at something finished is not a disambiguation, it is a
    leftover. `V4_TASK` exists to say *which of the open ones*, and an ended task
    is not one of them. So it is dropped and the ledger answers, and the reason
    is recorded rather than swallowed.

    What counts as ended lives in `ledger.ENDED_TASKS_SQL` and not here. The
    hook still allows when this cannot be answered -- a hook that cannot tell
    must not block -- but it answers `UNREADABLE` rather than `None`, and says
    why on stderr.

    `None` is a fact: the ledger was read and no task in it is open, so there is
    no scope to check and silence is right. A ledger that raised and a kernel
    that will not import are not that fact, and returning `None` for them made
    the guard standing down look identical to the guard having nothing to do --
    with no `hook_seen` row either, so `v4 ship`'s DEGRADED line reported the
    same thing for both. `current_scope`, one screen up, has printed the
    exception and marked `basis=unreadable` for the same class of failure since
    it was written; this is that treatment, at the step before it.

    No `hook_seen` here, and it is not an omission: writing one needs a task id,
    which is the thing that could not be established, and it needs the kernel
    and the ledger, which are the two things that just failed. stderr from a
    hook reaches the agent's transcript, so the stand-down is on the record even
    when the table cannot be.

    A missing ledger file is left as `None` on purpose: `.git/v4/ledger.db` is
    untracked and a fresh clone has none, so "there is no ledger here" is a
    state this repo documents rather than a read that failed, and it is what
    `current_scope` and `unengaged` already return `None`/quiet for.
    """
    db = _framework.db_path(repo_root)
    ledger = _framework.ledger(repo_root)
    # The two conditions answer two different questions and were tested as one.
    # `db is None or ledger is None -> return None` came first, so the branch
    # below re-asked `ledger is None` after it had already been ruled out: the
    # import failure could never reach `UNREADABLE`, `main` took the `if not
    # task_id` path, checked protected paths only, and allowed the write with no
    # `hook_seen` row. That is the exact state the docstring above is built
    # around distinguishing, and a reader trusting it would debug the wrong
    # branch.
    if db is None:
        return None
    if ledger is None:
        print(f"v4: the kernel could not be reached "
              f"({_framework.why(repo_root) or 'no reason given'}), so the "
              f"write hook cannot tell which task is open and is not checking "
              f"scope for this write", file=sys.stderr)
        return UNREADABLE
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        # `ledger.open_task_id` is this query. It was extracted from three
        # copies, with a docstring saying "each wrote the same SQL … the next
        # one would too" -- and then this hook wrote it a fourth time in the
        # same batch of commits. Extracting the string and leaving the function
        # uncalled is what let that happen.
        ids = ledger.open_task_ids(conn)
        if len(ids) > 1:
            return AMBIGUOUS
        return ids[0] if ids else None
    except sqlite3.Error as exc:
        print(f"v4: the open tasks in {db} could not be read "
              f"({type(exc).__name__}: {exc}), so the write hook cannot tell "
              f"which task this write belongs to and is not checking scope "
              f"for it", file=sys.stderr)
        return UNREADABLE
    finally:
        conn.close()


def _record_seen(repo_root: Path, task_id, rel: str,
                 allowed: bool = True, reason: str = "", basis: str = "",
                 note: str = "", session: str = "", scattered: bool = False):
    """`_framework.record_seen`, and it says on stderr when it could not.

    A record that cannot be written is not a weaker record; it is the same
    output as a hook that was never installed, which is what `ship` reads.

    Except in a repo that never adopted v4: `record_seen` opens the writable
    door because it is a write, so marking there creates a ledger in a tree
    that has no `.v4/` and never asked for one. The same fact as in
    `bash_guard._mark`, and the same test measured it in both.
    """
    if not (Path(repo_root) / ".v4").is_dir():
        return
    why = _framework.record_seen(repo_root, task_id, rel, allowed=allowed,
                                 reason=reason, basis=basis, note=note,
                                 session=session, scattered=scattered,
                                 hook="write_block")
    if why:
        print(f"v4 write_block: no mark written -- {why}", file=sys.stderr)


def _ambiguous_text(repo_root: Path) -> str:
    db = _framework.db_path(repo_root)
    ledger = _framework.ledger(repo_root)
    ids = []
    if db is not None and ledger is not None:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            ids = ledger.open_task_ids(conn)
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return (
        f"{len(ids)} tasks are open and nothing says which one this write is "
        f"for:\n  " + "\n  ".join(ids) + "\n\n"
        f"Either finish the one that is done -- `v4 ship --task <id>`, or "
        f"`v4 abandon --task <id> --why '…'` if it will not ship -- or say "
        f"which one you are in:\n"
        f"  export V4_TASK=<id>\n\n"
        f"This used to pick the newest and say nothing, which is right until "
        f"the write was for the other one. Then it checked the write against a "
        f"scope that was not its own and recorded it under a task that did not "
        f"make it.")


def _outside_scope_text(rel, globs, task_id) -> str:
    return (
        f"{rel} is outside this task's scope {globs}.\n\n"
        f"If it belongs here, say so and carry on:\n"
        f"  v4 --repo . scope widen --task {task_id} --add {rel} "
        f"--why '<why this file is part of this task>'\n\n"
        f"That is one event. No re-plan, no re-split, nothing already "
        f"answered is re-run. Widening is meant to be cheap; going "
        f"around it is not."
    )


def _engagement_text(owed, task_id) -> str:
    """What to say when the sentence a claim asks for has not been written.

    Every one of these is about code that is already here, so it can be answered
    without writing anything -- reading is not gated, only writing is. Named in
    full rather than counted, because a number is not a thing anybody can act on.
    """
    lines = "\n".join(f"  {c['id'][:16]}  {c['kind']:<16} {c['where']}"
                      for c in owed)
    return (
        f"{len(owed)} claim(s) on this task ask for a sentence and have none "
        f"yet.\n\n{lines}\n\n"
        f"  v4 --repo . engage --claim <id>            # the rule it is about\n"
        f"  v4 --repo . engage --claim <id> --text '…' # what it means here\n\n"
        f"These are about code that already exists, so nothing has to be "
        f"written to answer them -- reading is not gated. Once they have "
        f"sentences this stops asking, and claims raised later, about code you "
        f"are about to write, are not gated here at all."
    )


def refuse_the_write(reason: str) -> int:
    """Refuse this write, in the one shape a PreToolUse hook is read by.

    The decision this file makes, with a name on it. Three call sites in `main`
    built the same JSON inline -- the ambiguous-task refusal, the protected-path
    refusal, and the one that carries this hook's actual job, "outside the task
    scope" or "an engagement sentence is still owed". `stop_gate.refuse_the_stop`
    was extracted for exactly this reason and its docstring says both sibling
    hooks are covered by `auth_decision` rows; that was true of the protected
    branch and of nothing else here, so the repair was made at the site it was
    reported at and not at the other place the same fact lives.

    Keeping the shape in one function is what lets the facts table name a symbol
    for "who may write here": a fourth refusal added later reaches the declared
    decision by calling it, rather than by printing the same dict somewhere new.
    """
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": reason}}))
    return 0


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:                                    # noqa: BLE001
        # Allowing is right -- a payload this hook cannot read is not evidence
        # of a bad write. Allowing *silently* is not: it makes a dead guard and
        # a clean write look the same, which is the state `bash_guard` was
        # repaired out of and which this file still carried. The same
        # `json.load` with the same silence sat in all three hooks.
        print(f"v4 write_block: allowed without checking -- the payload did "
              f"not read ({type(exc).__name__}: {exc})", file=sys.stderr)
        print("{}")
        return 0

    if payload.get("tool_name") not in WRITE_TOOLS:
        print("{}")
        return 0

    repo_root = _framework.repo_root()

    # `NotebookEdit` is in WRITE_TOOLS and in the matcher this hook is
    # registered under, and it does not send `file_path` -- its parameter is
    # `notebook_path`. Reading one key meant every notebook write returned here
    # before the scope check, the engagement gate and the mark, so it was
    # allowed and left no `hook_seen` either. One key per tool is what the
    # matcher promised to cover; this reads the keys the matcher names.
    session = str(payload.get("session_id") or "")
    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not path:
        print("{}")
        return 0
    try:
        rel = str(Path(path).resolve().relative_to(repo_root))
    except ValueError:
        print("{}")            # outside the repo entirely; not this hook's business
        return 0

    # Dropped, not obeyed, and not silently: the mark below carries the value
    # so that "the hook used the ledger because your shell was pointing at a
    # finished task" is answerable afterwards. `_framework` owns the rule now,
    # because it was written here and in `stop_gate` and the two disagreed
    # about what happens to the value that was dropped.
    named, stale = _framework.task_id(repo_root)
    task_id = named or open_task(repo_root)
    if task_id is AMBIGUOUS:
        # Two tasks open and nothing saying which this write is for. Picking the
        # newer is what it used to do, and that answer is wrong exactly when it
        # matters: a write meant for the older task gets checked against a scope
        # that is not its own, passes, and leaves a `hook_seen` naming the wrong
        # task. Refusing is loud and costs a sentence; guessing is silent and
        # costs the guard. Measured on one adopter: two tasks open twice in a
        # day, both times because work finished and nobody ran `ship`.
        # Marked on every open task, then refused. This path wrote no
        # `hook_seen` at all, so the one case where this hook actually blocks
        # something left no trace, and `ship` went on reporting "the write hook
        # never fired for this task" -- the same output as a hook that was never
        # installed. `_record_seen` says it itself: "the hook ran" and "the hook
        # let it through" are two facts, and the second cannot be recovered
        # afterwards. Which task the write was for is the unknown here, so it is
        # recorded against each of the candidates rather than guessed at.
        try:
            ledger = _framework.ledger(repo_root)
            # `connect_readonly`: one read, through the door that matches it.
            # Same repair as `bash_guard._mark`, and the same fact -- `connect`
            # creates the file, runs the schema and the triggers, commits and
            # migrates, all to answer `open_task_ids`.
            conn = ledger.connect_readonly(repo_root) if ledger else None
            ids = ledger.open_task_ids(conn) if conn else []
            if conn:
                conn.close()
        except Exception:                                       # noqa: BLE001
            ids = []
        for tid in ids:
            _record_seen(repo_root, tid, rel, allowed=False,
                         reason="ambiguous task", basis="two tasks open",
                         session=session, scattered=True)
        return refuse_the_write(_ambiguous_text(repo_root))
    if task_id is UNREADABLE:
        # `open_task` has already said on stderr what it could not read. There
        # is no task id to check a scope against and none to record a mark
        # under, so this write goes through -- a hook that cannot tell must not
        # block -- and it falls through to the protected-path check below,
        # which needs no ledger. What it must not do is arrive there wearing
        # `None`, which is the ledger's answer that nothing is open.
        task_id = None
    if not task_id:
        # No open task means nothing declared a scope, so for an ordinary path
        # there is nothing to enforce and silence is right; the ship report says
        # the task ran without the hook.
        #
        # A protected path is the exception, and it is the escape
        # `checkers/scope.py:109` names out loud -- "write it with no task open,
        # and commit that separately". Nothing declared this write, nothing will
        # judge it, and `ledger.ENDED_TASKS_SQL` unions `repo-review` into the
        # ended set, so a review or monitor session is *always* in this state:
        # measured, `sed -i .github/monitor/PROMPT.md` was denied by
        # `bash_guard` while a `Write` for the same path returned `{}` from
        # here, and `.claude/settings.template.json` calls the two hooks
        # 同一個問題嘅兩半.
        #
        # Under a task it stays a scope question, which is the asymmetry
        # `bash_guard`'s own refusal depends on: its stated way through is "make
        # the change through Write/Edit where the scope hook can see it", and
        # denying here as well would leave the framework's own `checkers/`
        # unwritable by any route.
        guarded = protected(repo_root)
        if guarded is None:
            print(f"v4 write_block: protected paths not checked -- "
                  f"{_framework.why(repo_root) or 'the repo config was unreadable'}",
                  file=sys.stderr)
        elif is_protected(rel, guarded, repo_root):
            return refuse_the_write(_protected_text(rel, guarded))
        print("{}")
        return 0

    globs = current_scope(repo_root, task_id)
    if globs is None:
        # Allowing is right -- nothing readable said this write is out of bounds.
        # Saying nothing is not: a task whose scope could not be read looks
        # exactly like a task the hook watched and approved, and `ship` reports
        # on the marks it finds. The mark is the difference between a guard that
        # stood down for a reason and a guard nobody can account for.
        _record_seen(repo_root, task_id, rel, allowed=True, basis="unreadable",
                     session=session)
        print("{}")
        return 0

    # Decided before the mark is written, so the mark can say which it was.
    refusal, basis = None, ""
    if not in_scope(rel, globs, repo_root):
        # The engagement gate is not consulted, so it is not spent either: a
        # write that never happened cannot be the one that cleared the claims.
        refusal = ("outside scope", _outside_scope_text(rel, globs, task_id))
    else:
        owed, basis = unengaged(repo_root, task_id)
        if owed:
            refusal = ("engagement owed", _engagement_text(owed, task_id))

    _record_seen(repo_root, task_id, rel, allowed=refusal is None,
                 reason=refusal[0] if refusal else "", basis=basis,
                 note=f"V4_TASK={stale} has ended; used the ledger" if stale else "",
                 session=session)

    if refusal is None:
        print("{}")
        return 0

    # PreToolUse takes `hookSpecificOutput.permissionDecision`, and the
    # top-level `decision` field this used to print is not supported for this
    # event at all. So the hook ran, decided to block, printed a refusal
    # nothing reads, and exited 0 -- which is "allow". It failed open for its
    # entire existence, and no fixture would have caught it, because the
    # registration gate tests checkers and this is a hook.
    #
    # The predecessor knew: its spec says "rebuilding them with `exit 1 = block`
    # is incorrect and unsafe" and pins this exact shape. That sentence was in a
    # document nobody carried across.
    return refuse_the_write(refusal[1])


if __name__ == "__main__":
    sys.exit(main())
