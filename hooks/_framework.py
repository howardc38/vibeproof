"""Where `kernel` lives and which repo this is, for a hook the agent launches.

Two bootstrap questions, and neither of them can be answered by the thing being
bootstrapped: `on_path` finds the framework, `repo_root` finds the tree the
hook is guarding. Both were written once per hook before they were written
here, and both times the copies disagreed.

A hook is started by the agent rather than through `bin/v4`, so it inherits no
`PYTHONPATH` and an adopting repo has no `kernel/` of its own. Finding the
framework is therefore a bootstrap, and a bootstrap cannot live in the thing it
bootstraps -- which is the reason each hook carried its own copy, and the
reason the copies could disagree without anything noticing.

They did. `bash_guard` checked `kernel/` beside `hooks/` first and worked;
`write_block` and `stop_gate` read `V4_HOME` and then `.v4/home` and stopped
there. In this repo `V4_HOME` is set nowhere and `.v4/home` is written only
into an adopter, so both returned `None` on every invocation: measured, no
`hook_seen` row since 2026-08-10, `v4 ship` printing DEGRADED whether the hook
fired or not, and a `Write` to `.v4/config.json` allowed with an empty answer.

The leading underscore keeps this out of `doctor`'s hook census, which lists
`hooks/*.py` and skips names starting with one -- this is not a hook.

Three sources, most explicit first:

* `V4_HOME` -- an override that loses is not an override.
* `kernel/` beside `hooks/` -- the framework's own repo and any repo that
  vendored it. Needs no file to have been written, so it answers before
  `v4 install` has ever run.
* `.v4/home` -- what `install.write_launcher` records. One machine's absolute
  path, so on anybody else's clone it names a directory that is not there.

`on_path` returns **the reason it could not**, not a bool. A hook that cannot
reach the kernel has to stand down, and standing down silently is the failure
this module exists to make reportable: it is the same output as a hook that was
never installed.

`db_path` is here for the same reason one level along. Where the ledger lives is
`kernel.ledger.ledger_path`'s sentence, and a hook that cannot reach the kernel
still has to find it -- so the bootstrap owns the fallback, once, instead of
each hook keeping its own. Before this, `stop_gate` held four copies of it and
`write_block` a fifth.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def home(repo_root: Path):
    """Where the framework lives, by the three sources above.  `None` if nowhere.

    Separate from `on_path` because a hook needs this answer for two acts and
    only one of them is an import: `stop_gate` starts `python -m kernel.cli` as
    a child and has to hand it a `PYTHONPATH`. It resolved that itself, from the
    home marker alone, falling back to the repo root -- so `V4_HOME` lost,
    `kernel/` beside `hooks/` lost, and in this repo (no home marker, `V4_HOME`
    unset) the child was handed the repo root, which is the right directory here
    by accident and the wrong one in an adopter that followed the remedy
    `_framework` and `doctor` both print. Measured: with the marker absent the
    child's `PYTHONPATH` carried no `kernel`, `v4 status` died with
    ModuleNotFoundError, and the stop gate stood down every turn -- the one
    outcome its own docstring calls indistinguishable from a hook nobody
    installed.

    One resolution, so an override that loses is no longer expressible: the
    reason `on_path` returns is about the directory this function found.
    """
    beside = Path(__file__).resolve().parent.parent
    found = os.environ.get("V4_HOME")
    if not found and (beside / "kernel").is_dir():
        found = str(beside)
    if not found:
        marker = Path(repo_root) / ".v4" / "home"
        if marker.is_file():
            found = marker.read_text().strip()
    return found or None




def repo_root() -> Path:
    """Which repo a hook is guarding.  Always a git toplevel, never a cwd.

    The three hooks each opened with `Path(os.environ.get("V4_REPO", ".")).
    resolve()`, and `.claude/settings.template.json` shipped `V4_REPO` as the
    relative value `"."`. A hook's cwd is the *shell's* cwd -- that file says so
    itself, three lines above its `env` block, which is why the hook command is
    `python3 "$(git rev-parse --show-toplevel)/hooks/write_block.py"` rather
    than a relative path. The script path was resolved that way and the repo it
    guards was not, so the boundary moved with whatever directory the agent
    happened to be standing in.

    Measured against a scratch repo, one `Write` payload for a path that repo
    protects, two runs differing only in cwd: from the repo root the write is
    denied; from a subdirectory of the same repo `Path(path).relative_to(root)`
    raises `ValueError`, `write_block` returns `{}` -- allow -- and writes no
    `hook_seen` row at all. One `cd` turns the protected-path refusal off and
    leaves a ledger indistinguishable from a hook nobody installed.

    So the answer comes from the same place the hook path does: `git rev-parse
    --show-toplevel`. A directory is only ever a *starting point* for that
    question, never the answer to it -- which is the whole repair, because
    every directory inside a repo gives the same toplevel and only one of them
    is the repo.

    Where it starts:

    * `V4_REPO` when it is set -- resolved like any path, and then asked for its
      toplevel, so `"."` from a subdirectory names the repo that subdirectory is
      in rather than the subdirectory.
    * this file's own directory otherwise. `install.copy_files` copies
      `hooks/*.py` into the adopting repo, so a hook always sits inside the tree
      it guards, and that holds from any cwd and inside any worktree. The
      template no longer sets `V4_REPO` at all, which makes this the ordinary
      path rather than the fallback.

    When git cannot answer -- no git, or a start that is not in a repo -- the
    start is returned and the caller behaves as it always has for a root it
    cannot use: it allows, and says why. A hook that cannot tell must not block.
    """
    named = os.environ.get("V4_REPO") or ""
    start = (Path(named).resolve() if named
             else Path(__file__).resolve().parent.parent)
    if not start.is_dir():
        return start
    try:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start,
                           capture_output=True, text=True)
    except OSError:
        return start
    if r.returncode != 0 or not r.stdout.strip():
        return start
    return Path(r.stdout.strip()).resolve()


def on_path(repo_root: Path) -> str | None:
    """Put the framework on `sys.path`.  `None` on success, else why not."""
    found = home(repo_root)
    if not found:
        beside = Path(__file__).resolve().parent.parent
        return (f"no V4_HOME, no kernel/ beside {beside}, and no .v4/home "
                f"under {repo_root}; run `v4 install` here, or set V4_HOME to "
                f"where the framework lives")
    if not (Path(found) / "kernel").is_dir():
        return f"no kernel/ under {found}"
    if found not in sys.path:
        sys.path.insert(0, found)
    return None


def db_path(repo_root: Path):
    """The ledger file for this repo, or `None` when there is not one there.

    One ledger per repo, shared by every worktree -- `kernel.ledger.ledger_path`
    owns that sentence, and this asks it whenever the kernel is reachable. The
    inline fallback stays for the reason `write_block.current_scope` already
    gives about its own: a hook fires from the coding agent's settings, in a
    process this framework did not start, and it has to work when the kernel
    does not.

    What it replaces is five copies of eight lines. `stop_gate` wrote the `git
    rev-parse --git-common-dir` block out four times -- `_open_task`,
    `_is_open`, `_worked_here`, `_ended` -- and `write_block._ledger` was
    already this function with a name, in the two files whose comments say a
    rule must not have two implementations. Moving where a ledger lives was a
    five-site edit, and a site that got missed is not a visible break: it is a
    gate that stops finding the ledger and stands down.

    `None` covers both "git could not answer" and "no ledger there yet". Every
    caller treats those the same way, by standing down, and neither is a state
    a hook may block on.
    """
    root = Path(repo_root)
    mod = ledger(root)
    if mod is not None:
        try:
            db = mod.ledger_path(root)
        except Exception:                                       # noqa: BLE001
            # `ledger_path` runs git with `check=True`. A repo git cannot
            # answer about is the fallback's case too, so it falls through
            # rather than being reported here as a different failure.
            db = None
        if db is not None:
            return db if Path(db).is_file() else None
    common = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                            cwd=root, capture_output=True, text=True)
    if common.returncode != 0:
        return None
    p = Path(common.stdout.strip())
    if not p.is_absolute():
        p = (root / p).resolve()
    db = p / "v4" / "ledger.db"
    return db if db.is_file() else None


def ledger(repo_root: Path):
    """`kernel.ledger`, or `None` -- pair it with `why` to say what happened."""
    return _module(repo_root, "ledger")


def config(repo_root: Path):
    """`kernel.config`, or `None`."""
    return _module(repo_root, "config")


def ended_kinds(repo_root: Path):
    """The kinds that end a task, from the module that owns them.

    `stop_gate` asked `getattr(ledger, "ENDED_KINDS", ("shipped", "abandoned"))`
    -- and the default is a second copy, in the function whose own docstring
    says asking the owner "keeps this from being a fourth copy that drifts".
    The day a third ending is added, `ledger.ENDED_TASKS_SQL` and `risk` follow
    and a hook holding its own tuple does not.

    `None` when the kernel is out of reach, so the caller decides what an
    unanswerable question means rather than being handed a guess. Every other
    bootstrap here answers that way.
    """
    mod = ledger(repo_root)
    return None if mod is None else tuple(mod.ENDED_KINDS)


def task_id(repo_root: Path):
    """`(task, dropped)` -- which task this session is about, and what was
    discarded to get there.

    `V4_TASK` was read straight out of the environment in three places, and the
    rule is not trivial: a variable naming a task that has ended is dropped, the
    dropped value is worth recording, and the answer falls back to the ledger.
    Two of the three implemented that and disagreed about the last part; the
    third asked only whether the variable was set.

    Both files record the same incident above their own copy: a shell still
    exported `V4_TASK=t-e2e-green` a day after that task was abandoned, and
    every write in the new task was judged against the old task's scope and
    denied. This module exists so a hook does not pick its own answer to a
    bootstrap question -- it owns `home`, `repo_root`, `on_path`, `db_path`,
    `ledger`, `config`, `is_open`, `why` and `record_seen`, and this was the
    one it had not been given.

    `dropped` is `""` when nothing was: a caller that reports it unconditionally
    would say "your shell pointed at a finished task" on every run.
    """
    named = os.environ.get("V4_TASK") or ""
    if named and not is_open(repo_root, named):
        return "", named
    return named, ""


def is_open(repo_root: Path, task_id: str) -> bool:
    """Is `task_id` a task this ledger still has open?  `True` when unreadable.

    One owner for a rule both hooks state and spelled differently. Each carried
    its own `_is_open` with the same docstring -- "a hook that cannot tell must
    not start ignoring what it was told" -- and the two disagreed about the one
    case that sentence is about: `write_block` opened the connection *outside*
    its `try`, so a database that will not open at all raised `sqlite3.Error`
    out of the hook instead of answering `True`, while `stop_gate` wrapped the
    connect and answered.

    An unreadable ledger is the case, not the exception: `sqlite3.connect` with
    `mode=ro` raises for a file that is missing, locked, or not a database, and
    a hook is handed whatever the repo has.
    """
    db = db_path(repo_root)
    led = ledger(repo_root)
    if db is None or led is None:
        return True
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return task_id in led.open_task_ids(conn)
        finally:
            conn.close()
    except sqlite3.Error:
        return True


#: Why a `_module` call came back `None` after the path had already resolved.
#: Keyed by `(repo root, module name)`: `why` is asked by a later call than the
#: one that failed, and keying on the root alone would let `config` importing
#: cleanly erase the record of `ledger` not importing at all.
_IMPORT_FAILED: dict[tuple[str, str], str] = {}


def why(repo_root: Path) -> str:
    """The reason the kernel is out of reach, or `""`.

    Two reasons, and this used to know only the first. `on_path` answers "there
    is no kernel/ to put on the path", and returns `None` -- success -- when
    there is one; an import that then blows up inside a checkout that *is*
    there returns `None` from `_module` with `on_path` still saying everything
    is fine. So `why` said `""`, and every caller writes `why(...) or '<some
    other sentence>'`: `write_block` printed "the repo config was unreadable"
    and `record_seen` printed "the kernel could not be reached", both about a
    `SyntaxError` in `kernel/ledger.py` that reached nobody.

    `_module` records what it caught and this reads it, so the sentence a hook
    prints is the failure that happened rather than the caller's guess at it.
    """
    reason = on_path(repo_root)
    if reason:
        return reason
    root = str(Path(repo_root))
    return "; ".join(v for (r, _), v in sorted(_IMPORT_FAILED.items())
                     if r == root)


def _module(repo_root: Path, name: str):
    if on_path(repo_root) is not None:
        return None
    key = (str(Path(repo_root)), name)
    try:
        mod = __import__(f"kernel.{name}", fromlist=[name])
    except Exception as exc:                                    # noqa: BLE001
        # An import that fails after the path resolved is a broken checkout,
        # not a missing one. Both leave the hook unable to judge, so `None` is
        # still the answer and the caller still stands down -- but the reason
        # is kept rather than dropped. Dropping it was justified on `why` being
        # what a caller prints, and `why` runs `on_path`, which succeeds in
        # exactly this case.
        _IMPORT_FAILED[key] = (f"kernel/ is on the path and kernel.{name} will "
                               f"not import ({type(exc).__name__}: {exc}) -- a "
                               f"broken checkout, not a missing one")
        return None
    _IMPORT_FAILED.pop(key, None)
    return mod


def record_seen(repo_root: Path, task_id, rel: str, *, allowed: bool = True,
                reason: str = "", basis: str = "", note: str = "",
                session: str = "", scattered: bool = False,
                hook: str = "") -> str:
    """Leave a mark that a hook ran, and what it decided.  `""` on success.

    Whether a hook is installed is configuration; whether it fired is a fact,
    and only the second one means anything -- `v4 ship` reports DEGRADED off
    these rows. `write_block` implemented it and `bash_guard` did not, so the
    one guard covering the shell route to protected paths left nothing behind:
    a deny that actually stopped `sed -i .v4/config.json` was unrecoverable
    afterwards, and `trend.gate` reported refusals from one hook only.

    `allowed` is here because "the hook ran" and "the hook let it through" were
    the same record, and the second cannot be recovered later either --
    `current_scope` grows with every widen, so replaying a refusal against the
    final scope shows it as allowed. `basis` is the same argument one level
    down: everything owed had a sentence, and nothing was visible to owe yet,
    both answered `allowed=True`.

    Returns the reason it could not write rather than swallowing it. The body
    used to be `except Exception: pass` with nothing printed, which produces
    the identical output to a hook nobody installed -- the failure this record
    exists to make visible, in the writer of the record.

    `scattered` says this row was written against *every* open task rather than
    against a task anything identified. Two callers do that and both are right
    to: a shell command names no task (`bash_guard._mark`), and a write with two
    tasks open names no task either (`write_block`'s `AMBIGUOUS` branch), so
    marking one of them would be the guess those branches exist to refuse.

    What it is not is evidence that this session worked on any one of them --
    and `ledger.sessions_on` was reading it as exactly that, which is how
    `stop_gate._worked_here` came to stand behind a task the session had never
    touched. Recorded rather than inferred: nothing in the payload could tell a
    mark aimed at one task from a mark sprayed across all of them.

    Through the kernel, because the ledger refuses inserts from a connection
    that did not register its write gate, and a hook is exactly what that gate
    keeps out. Marking that a hook ran is not a reason to be allowed to write
    attempts.
    """
    mod = ledger(repo_root)
    if mod is None:
        return why(repo_root) or "the kernel could not be reached"
    try:
        conn = mod.connect(repo_root)
    except Exception as exc:                                    # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    # Which hook. Every row said `actor="hook"` and nothing said *which* one,
    # so "has any hook fired" was answerable and "has the Stop gate fired" was
    # not -- and the Stop gate is the one of the three that can block a turn.
    # `doctor` reported all three alive off their filenames appearing in a JSON
    # file, which is the environment question answered from source that its own
    # heading says it exists to stop.
    payload = {"path": rel, "allowed": bool(allowed), "hook": hook or ""}
    if reason:
        payload["reason"] = reason
    if basis:
        payload["basis"] = basis
    # Separate from `basis`, which is an enum read back by equality. Prose used
    # to be concatenated onto it -- `"cleared (V4_TASK=t-x has ended; used the
    # ledger)"` -- and `unengaged`'s `json_extract(payload, "$.basis") =
    # "cleared"` then matched nothing, so the engagement gate was never marked
    # spent in exactly the case `open_task` documents as ordinary: a shell
    # outliving its task. A value that is compared by equality has to stay the
    # value.
    if note:
        payload["note"] = note
    # Who was working. Every row in this ledger says what happened and none of
    # them said in which session, so `stop_gate` -- which asks the repo for its
    # open task and finds exactly one -- had no way to tell a worker from a
    # bystander. Measured 2026-08-26: a session under a standing read-only
    # instruction was told to `ship`, then to `derive`, then to answer or sign
    # thirteen claims, across three tasks it had never touched, more than
    # twenty times.
    #
    # The harness supplies it (measured against a real `PreToolUse` and a real
    # `Stop` payload, not read off documentation), and a hook is the only place
    # in this system that receives it: `v4` is a subprocess the agent starts,
    # and nobody tells it which session it belongs to.
    if session:
        payload["session"] = session
    # Written only when it is true, so an old row -- every one of them, since
    # this field is younger than the table -- stays "cannot tell" rather than
    # becoming a false claim that the mark was aimed.
    if scattered:
        payload["scattered"] = True
    try:
        mod.insert(conn, "event", task_id=task_id, claim_id=None,
                   kind="hook_seen", actor="hook", payload=payload,
                   created_at=datetime.now(timezone.utc).isoformat())
        return ""
    except Exception as exc:                                    # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()
