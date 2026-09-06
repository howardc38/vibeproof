"""Widening a task's scope.  SPEC.md §5.

The design calls this the one mechanism here that treats a cause rather than a
symptom, and it was the one that had not been built. Everything else bounds a
loop; this removes the reason for one.

V3's shape: a contract declared the files a phase would touch, a gate enforced
it, and correcting the declaration meant a new cycle through eight steps. One
phase spent nine rounds and two and a half hours there without writing code.

The declaration stays -- silently sprawling out of scope is worth stopping. What
changes is the price of being wrong about it: one event, no re-plan, no
re-split, no re-running of anything already answered.
"""

import fnmatch
import json
from datetime import datetime, timezone
from pathlib import Path

from . import ledger as ledger_mod
from .ledger import insert, git_identity

from .analysis.subject_files import PROTECTED_DEFAULT  # noqa: F401


def protected_for(config_obj) -> list:
    """The framework's protected set, plus whatever this repo added.

    Union, never replace, and never defaulting to empty -- and written
    once, because it was written twice. `checkers/scope.py` is the
    program whose answer counts and it carried its own copy of this
    expression; its comment records what the earlier divergence cost
    ("a repo that never wrote the key had zero protected paths according
    to the one program whose answer counts"). Two copies of the repair
    is the same shape one step on.
    """
    declared = (config_obj or {}).get("protected_paths") or []
    return sorted(set(PROTECTED_DEFAULT) | set(declared))


class WidenRefused(RuntimeError):
    pass


def forbidden(conn, task_id):
    """Globs this task declared it must not touch.  SPEC.md §5.

    Here rather than in `lifecycle`, because `scope.widen` needs it and the
    lazy `from .lifecycle import forbidden` it used was the single edge that put
    four modules on an import cycle: `config`, `review` and `request_cover` all
    reach `lifecycle` only by routing through this one line. `lifecycle` imports
    `scope` already, so this direction adds no edge at all.

    And it belongs here on the reading too: what a task may not touch is the
    same fact as what it may, and `current_scope` is three lines above.
    """
    out = []
    for r in conn.execute(
        "SELECT payload FROM event WHERE task_id = ? AND kind = 'task_forbid' "
        "ORDER BY id", (task_id,)
    ):
        out += json.loads(r["payload"]).get("globs", [])
    return out


def _matches(path, globs):
    """`subject_files.matches`.  Kept as a name because `widen` reads better
    for it, and because the two spellings this used to carry were the two
    that missed a bare directory: `v4 scope widen --add checkers` reported
    no protected hit while the widened scope granted the write.
    """
    from .analysis.subject_files import matches
    return matches(path, globs)


def current_scope(conn, task_id):
    """The globs this task may write to, after every statement about them.

    One ordered pass over both kinds, because the last statement about a path
    is the one that counts: a glob narrowed away and then widened back is in
    scope. Two passes -- every widen, then every narrow -- reads the same rows
    and gives the opposite answer for that sequence, which is what the first
    version of this did while the comment beside it said otherwise.
    """
    row = conn.execute("SELECT scope_globs FROM task WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise WidenRefused(f"no such task: {task_id}")
    globs = json.loads(row["scope_globs"])
    for e in conn.execute(
        "SELECT kind, payload FROM event WHERE task_id = ? AND kind IN "
        "('scope_widen', 'scope_narrow') ORDER BY id", (task_id,),
    ):
        p = json.loads(e["payload"])
        if e["kind"] == "scope_widen":
            # `widen` writes the event and then raises if the reason was
            # refused, so a refused widen is on the record -- which is right, an
            # attempt is worth keeping. What was wrong is reading it back: this
            # summed every row, so the CLI printed REFUSED and exited 2 while
            # the scope had already grown for this reader, for the write hook
            # and for the `scope` checker. The verdict was already in the
            # payload and nobody looked at it.
            if (p.get("engagement") or {}).get("accepted", True):
                globs = globs + p["added"]
        else:
            # `narrow` is the mirror, and it exists for the reason
            # `request_cover.withdraw` and `review defer --withdraw` do: a
            # declaration made before the work is a thing somebody can get
            # wrong, and the only correction on offer was to abandon the task --
            # which, measured, makes a delta gate stop asking about what it had
            # already found.
            dropped = set(p.get("dropped") or ())
            globs = [g for g in globs if g not in dropped]
    return globs


def _reaches_protected(root, add, protected):
    """Which of `add` would put a protected path in scope.

    This asked whether the *glob string* looked like a protected path --
    `_matches(g, protected)` -- which is a different question and answers
    wrongly in the one direction that costs something. Measured against
    `PROTECTED_DEFAULT`: `_matches(".v4/**")` is True and `_matches("**")`,
    `_matches("**/*.py")` and `_matches(".v4")` are all False, so
    `v4 scope widen --add **` printed `protected hits: []`, never printed the
    signature instruction, and widened onto every protected file in the repo.
    Whether the caller wrote a trailing `/**` decided it.

    Whether two globs overlap is not decidable in general. Which of *this
    repo's* files both of them cover is, and it is the same universe
    `_files_in_scope` and `checkers/scope.py` judge against, so the early
    warning and the answer of record are about one set of paths.

    The old rule is kept as the second half of a union, not replaced: a repo
    whose `.v4/` is empty still gets told that `--add .v4/**` reaches the
    directory that judges it. Strictly wider, so nothing that was reported
    stops being reported.
    """
    from .analysis import subject_files
    guarded = [p for p in subject_files.tracked({}, root)
               if subject_files.matches(p, protected)]
    return [g for g in add
            if subject_files.matches(g, protected)
            or any(subject_files.matches(p, [g]) for p in guarded)]


def _refused(conn, cfg, task_id, add, why, reason):
    """Record that a widen was asked for and turned down, then refuse.

    Three ways a widen is refused and two of them left no row anywhere: both
    raised before any insert ran, while the third was deliberately written
    first -- the `scope_widen` event goes in carrying
    `engagement.accepted = false` and only then raises. `usage()` counts
    refusals off that flag, so a widen turned down for a forbidden path counted
    as zero, and `cli._refused_widens` printed nothing about it -- a helper that
    exists because "a widen that was written, judged and turned down was
    invisible in all three places the widen count is printed".

    An attempt to widen into a path the task declared out of bounds is the
    permission-boundary event this table is for, and it was the one refusal
    that left no evidence anybody had tried.
    """
    insert(conn, "event", task_id=task_id, claim_id=None, kind="scope_widen",
           actor=ledger_mod.who_acted(),
           payload={"added": list(add), "why": why or "", "protected": [],
                    "who": git_identity(cfg.root),
                    "asked_by": ledger_mod.who_acted(),
                    "engagement": {"accepted": False, "reason": reason}},
           created_at=datetime.now(timezone.utc).isoformat())
    raise WidenRefused(reason)


def widen(conn, cfg, *, task_id, add, why):
    """Record the widening.  Returns (globs, protected_hits).

    Protected paths are not refused here -- they are reported, and the caller
    turns that into a signature. Refusing outright would make the cheap exit
    expensive again, which is how the expensive one gets used instead.

    A forbidden path is the other case and is refused. `protected` has a way
    through -- a signature -- so reporting it leaves the worker somewhere to go.
    `forbid` has none: `checkers/scope.py` says "widening cannot reach it,
    reopen the task if that was wrong", and it is the truth. Measured before
    this: widening onto a forbidden path returned `scope now: [...]` and
    `protected hits: []`, said nothing about the forbidden set, and the refusal
    arrived at `v4 check` -- which is the twenty minutes this exit exists to
    save. Nothing legitimate is lost: only writes are scoped (the hook gates
    `Write`/`Edit`, reading is free), and a written forbidden path can never
    ship.
    """
    th = cfg.thresholds
    if not why or len(why.strip()) < th["min_chars"]:
        _refused(conn, cfg, task_id, add, why,
                 f"a reason under {th['min_chars']} characters is not a reason. "
                 f"The point of this command is that widening is visible, and "
                 f"an empty reason makes it invisible again.")
    # `_reaches_protected`, not `_matches`. The sibling's own docstring says the
    # glob-against-glob proxy "answers wrongly in the one direction that costs
    # something", and it was repaired there for protected paths and left here:
    # `--add kernel/**` against a forbidden `kernel/ledger.py` is a widen that
    # reaches a path the task declared out of bounds, and glob-vs-glob does not
    # see it. The two questions are the same question, so they get the same
    # answer.
    off_limits = _reaches_protected(cfg.root, add, forbidden(conn, task_id))
    if off_limits:
        _refused(conn, cfg, task_id, add, why,
                 f"{', '.join(off_limits)} was declared out of bounds when this "
                 f"task opened, and widening cannot reach a forbidden path -- "
                 f"`scope` refuses it at check whether or not it is in scope. If "
                 f"that declaration was wrong, reopen the task; do not work "
                 f"around it here.")
    scope = current_scope(conn, task_id)
    protected = cfg.protected
    hits = _reaches_protected(cfg.root, add, protected)

    # The reason is the engagement sentence for this widen, judged by the same
    # mechanical rules and exempt from duplicate detection: a second widen in
    # one task has the same reason as the first, and refusing it for that makes
    # the cheap exit expensive, which is how it stops being used.
    from . import engagement
    row = {"kind": "scope_widen", "file": ", ".join(add), "symbol": ""}
    # The `scope` kind carries a rule and does not engage: widening already asks
    # for a sentence, and two prompts for the same thing is how a cheap exit
    # becomes an expensive one. So the rule rides on this sentence instead --
    # including the duplicate test, which is what stops "same reason as last
    # time" from being copied forward.
    rules = (cfg.kinds.get("scope") or {}).get("rule") or []
    ok, reason = engagement.judge_text(
        cfg, sentence=why, subject_words=set(add),
        rule=(rules[0]["text"] if rules else None))

    # Who asked. `actor="worker"` was a literal, and every later reader of this
    # event -- `current_scope`, `hooks/write_block.current_scope`,
    # `checkers/scope.py`, `usage`, `v4 ship` -- consumes the widening it grants
    # without being able to say whose it was. `risk.accept` treats the same
    # question as load-bearing for the other permission-granting act, recording
    # `who` from git config and `signed_by = "person" if is_tty else "agent"`,
    # and `review.defer` does the same. Widening into a protected path is the
    # one that costs a signature, and the record of the request carried no name
    # at all.
    import sys as _sys
    at_a_terminal = bool(_sys.stdin.isatty())
    now = datetime.now(timezone.utc).isoformat()
    insert(conn, "event", task_id=task_id, claim_id=None, kind="scope_widen",
           actor=ledger_mod.who_acted(),
           payload={"added": list(add), "why": why, "protected": hits,
                    "who": git_identity(cfg.root),
                    "asked_by": ledger_mod.who_acted(),
                    "engagement": {"accepted": ok, "reason": reason}},
           created_at=now)
    if not ok:
        raise WidenRefused(reason)
    return scope + list(add), hits


class NarrowRefused(WidenRefused):
    """A narrowing that would drop a path this task has already changed."""


def touched_since_base(conn, root, task_id) -> list:
    """Every path this task has changed against the commit it opened at.

    `git diff --name-only <base>` plus what is untracked: the same two
    questions `checkers/scope.py` asks, because a narrowing has to be judged
    against exactly what that checker will judge.
    """
    import subprocess
    row = conn.execute("SELECT base_commit FROM task WHERE id = ?",
                       (task_id,)).fetchone()
    base = (row["base_commit"] if row else "") or ""
    out = []
    for args in ((["diff", "--name-only", base] if base else None),
                 ["ls-files", "--others", "--exclude-standard"]):
        if args is None:
            continue
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
        if r.returncode == 0:
            out += [l for l in r.stdout.splitlines() if l.strip()]
    return sorted(set(out))


def narrow(conn, cfg, *, task_id, drop, why):
    """Take back part of a scope this task declared.  Returns the globs left.

    The mirror of `widen`, with one gate `widen` does not need: a path this
    task has already changed cannot be dropped. That is what stops a narrowing
    from being a way to hide work -- it is decided by the diff, so there is no
    judgement in it and no exception to argue for.

    Why it has to exist: a scope is declared before the work, and `derive`
    raises claims over everything in it, not over what was touched. Declaring
    `src/**` when the work is `src/workers/**` pulls in every other file's
    whole-file claims, and the only correction on offer was to abandon and
    reopen -- which is measured elsewhere to make a delta gate stop asking
    about what it had already found. A framework whose position is that
    corrections must be possible and visible had none for this one.
    """
    th = cfg.thresholds
    if not why or len(why.strip()) < th["min_chars"]:
        raise NarrowRefused(
            f"a reason under {th['min_chars']} characters is not a reason. "
            f"Narrowing is a statement that this task was never going to touch "
            f"those paths, and it is recorded so somebody can disagree.")
    drop = list(drop or ())
    if not drop:
        raise NarrowRefused("name at least one glob to drop.")

    scope = current_scope(conn, task_id)
    unknown = [g for g in drop if g not in scope]
    if unknown:
        raise NarrowRefused(
            f"{', '.join(unknown)} is not in this task's scope, so there is "
            f"nothing to take back. Scope now: {scope}")

    # The gate. A path already changed is work, and work cannot be moved out of
    # sight by a declaration made afterwards.
    #
    # Out of sight, not inside the dropped glob. A scope can name a path twice
    # -- `kernel/**` and `kernel/cli.py` -- and dropping the wide one moves
    # nothing, because the narrow one still puts every claim over that file in
    # front of this task. Asking `matches(p, drop)` was a proxy for the real
    # question and diverged on exactly the correction this function exists for:
    # the docstring's own `src/**` where the work is `src/workers/**`, once the
    # work has started. Measured 2026-08-26: refused twice in one day, costing
    # one signature on an untouched file and one abandoned task.
    touched = touched_since_base(conn, cfg.root, task_id)
    from .analysis.subject_files import matches
    kept = [g for g in scope if g not in drop]
    caught = sorted(p for p in touched
                    if matches(p, drop) and not matches(p, kept))
    if caught:
        raise NarrowRefused(
            f"{len(caught)} path(s) this task has already changed are inside "
            f"what you are dropping, so dropping it would take work out of "
            f"scope after the fact:\n  " + "\n  ".join(caught[:8])
            + (f"\n  … and {len(caught) - 8} more" if len(caught) > 8 else "")
            + "\n\nNarrowing is for a declaration that turned out wider than "
              "the work, not for work that turned out wider than the "
              "declaration.")

    import sys as _sys
    at_a_terminal = bool(_sys.stdin.isatty())
    insert(conn, "event", task_id=task_id, claim_id=None, kind="scope_narrow",
           actor=ledger_mod.who_acted(),
           payload={"dropped": drop, "why": why,
                    "who": git_identity(cfg.root),
                    "asked_by": ledger_mod.who_acted()},
           created_at=datetime.now(timezone.utc).isoformat())
    return current_scope(conn, task_id)


def usage(conn, cfg, task_id):
    """How much this task has widened.  Printed by `v4 status` and by ship.

    There is no gate on widening -- a gate is what V3 had. The whole defence is
    that the number is visible, so it has to be somewhere a person looks.

    `widens` counts the ones that took effect, and `refused` the ones that did
    not. The comment below said summing every row was the defect being
    repaired, and then only `added` was repaired: `widens` stayed `len(rows)`,
    so a task that asked three times and was refused twice printed "widened 3
    time(s)" beside one path -- an overstatement of the one number the design
    calls the entire defence, in the direction that makes the defence look like
    it is doing more than it is. Both are returned, and both are printed;
    `refused` with no reader was the same defect one key over.
    """
    rows = conn.execute(
        "SELECT payload FROM event WHERE task_id = ? AND kind = 'scope_widen'",
        (task_id,)).fetchall()
    # A refused widen is recorded and must not count -- `current_scope` filters
    # exactly these out, with a comment saying why, and this summed every row.
    # So `v4 status` and `v4 ship` reported widens and paths that never took
    # effect, in the number the design calls the entire defence against sprawl.
    added, refused = [], 0
    for r in rows:
        p = json.loads(r["payload"])
        if (p.get("engagement") or {}).get("accepted", True):
            added += p["added"]
        else:
            refused += 1

    # The files this repo tracks, not `rglob("*")`. `lifecycle._files_in_scope`
    # documents that walk being replaced after it returned 94,147 paths in 4.5s
    # where the tracked set was a few thousand, and the difference was `.venv/`
    # and `node_modules/` -- so on any adopter with a virtualenv the percentage
    # was divided by a number two orders of magnitude too large, `over_warn`
    # could never fire, and `v4 status` paid the 4.5s walk every time it printed.
    from .analysis.subject_files import tracked
    files = tracked({}, cfg.root)
    total = len(files)

    # How much of the repo the widened globs actually reach, not how many globs
    # there are. `len(added) / total` divided a count of globs by a count of
    # files, so `--add "**"` -- every file in the repo -- read as 0.04%.
    from .analysis.subject_files import matches
    reached = sum(1 for f in files if matches(f, added)) if added else 0
    pct = (reached / total * 100) if total else 0.0
    return {"widens": len(rows) - refused, "refused": refused, "paths": added,
            "repo_files": total, "reached": reached, "pct": round(pct, 2),
            "over_warn": pct > cfg.thresholds["widen_warn_pct"]}
