"""A task, from opening it to shipping it.  SPEC.md §5, §6.4.

There is no step here, and no cycle. A task opens, claims are derived from the
repo, checkers answer them, and shipping asks one question: is every claim
answered against the bytes that are there now.

The one loop that does exist is bounded and says why. Re-deriving before ship
can surface a claim that did not exist when the task opened -- a worker's own
code can introduce an outbound write. Answering that claim changes the diff,
which means deriving again. Two properties make that converge (identity is
stable under line shifts; a detector must not emit claims that answering it
creates), and a hard limit of three rounds catches the case where one of them
is broken. Hitting the limit is a bug report about a detector, not a risk for
somebody to sign.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from . import config as config_mod
from . import derive as derive_mod
from . import facts as facts_mod
from . import review as review_mod
from .analysis import subject_files
from . import hashing, runner, state
from . import ledger as ledger_mod
from .ledger import insert


def _now():
    return datetime.now(timezone.utc).isoformat()


def _lenses_reviewed(conn, task_id):
    """`{lens: findings}` for the lenses that reported on this task.

    Last row wins per lens: a reviewer that ran again after a repair is saying
    something newer, not something additional, and summing them would report a
    second pass of the same diff as twice the findings.
    """
    out = {}
    for r in conn.execute(
            "SELECT payload FROM event WHERE task_id = ? AND "
            "kind = 'lens_reviewed' ORDER BY id", (task_id,)):
        try:
            p = json.loads(r["payload"])
        except (ValueError, TypeError):
            continue
        if p.get("lens") is not None:
            out[p["lens"]] = p.get("findings")
    return out


def carry_forward(conn, after: str) -> str:
    """What the previous task settled, in one block a worker can read.

    V4 chose one request per task, and the argument for it holds: a different
    proof surface is not a reason to split, and the predecessor's four split
    boundaries produced a contract renegotiation per finding. But that argument
    disproves one wrong way to split; it does not disprove that some work is
    larger than one context.

    And the real usage is a long chain -- the reference repo ran F167 to F238.
    Without this, a worker at its context limit has three moves: push on until
    it breaks, open a fresh task that knows nothing and may undo the last one,
    or paste a summary it invented with no shape and no check.

    So: the previous task's request, its scope, and which of its claims were
    actually answered. Facts the ledger already holds, not a narrative -- a
    narrative is the prose contract this project removed.
    """
    row = conn.execute("SELECT request, scope_globs FROM task WHERE id = ?",
                       (after,)).fetchone()
    if row is None:
        raise RuntimeError(f"no such task to continue from: {after}")
    answered, open_ = [], []
    for c in conn.execute("SELECT id, kind FROM claim WHERE task_id = ?", (after,)):
        a = conn.execute("SELECT exit_code FROM attempt WHERE claim_id = ? "
                         "ORDER BY id DESC LIMIT 1", (c["id"],)).fetchone()
        (answered if a and a["exit_code"] == 0 else open_).append(c["kind"])
    from .request_cover import carried_line
    lines = [carried_line(after),
             f"  its request: {row['request']}",
             f"  its scope  : {', '.join(json.loads(row['scope_globs']))}"]
    if answered:
        lines.append(f"  answered   : {', '.join(sorted(set(answered)))}")
    if open_:
        lines.append(f"  still open : {', '.join(sorted(set(open_)))}")
    return "\n".join(lines)


WORKTREE_KIND = "task_worktree"
CONTINUES_KIND = "task_continues"


def open_task(conn, cfg, *, task_id, request, scope_globs, forbid_globs=None,
              after=None, base=None):
    """`base` is what every delta gate diffs against.  HEAD unless given.

    HEAD was the only answer, and it is the wrong one for work that is already
    committed: open a task after committing and the diff is empty, so `scope`
    reads nothing and passes, `lint` and `test-shape` find no delta, and the
    task ships having been judged against nothing. The way out was
    `git reset --soft` and abandoning the task -- measured on an adopter, that
    is what happened.

    Naming an older base cannot weaken anything, which is why the flag is safe
    to offer: every gate here is a delta gate, so a base further back means a
    larger diff and more to answer for, never less. There is no base that hides
    work, because there is no commit newer than HEAD.
    """
    if after:
        request = f"{request}\n\n{carry_forward(conn, after)}"
    base = base or runner.head_commit(cfg.root)
    insert(conn, "task", id=task_id, request=request, scope_globs=scope_globs,
           base_commit=base, created_at=_now())

    # The negative of scope, and deliberately the same machinery. `scope_globs`
    # says where work may go; nothing said where it may not, so a request's
    # "do not touch the retry logic" had no expression anywhere -- `task.request`
    # is written once and read by nothing.
    #
    # An event rather than a column: no schema change, the same shape
    # `scope_widen` already uses, and the forbidden set is then part of the
    # append-only record rather than a mutable field.
    #
    # What this does not reach is stated in SPEC.md §10: constraints whose
    # violation is a relationship between two things ("the report must not
    # become authoritative") rather than a path. Those are not narrower globs;
    # they are a different kind of statement.
    if forbid_globs:
        insert(conn, "event", task_id=task_id, claim_id=None, kind="task_forbid",
               actor="human", payload={"globs": list(forbid_globs)},
               created_at=_now())

    # The same shape again, for the same reason. `after` was read once to build
    # a block of text and then dropped: the edge existed only as a line inside
    # `task.request` that a regex could match, so "what continued t-dead-tests"
    # had no answer a query could give. `carry_forward` says it writes "facts
    # the ledger already holds" -- and this was the one fact it did not hold.
    if after:
        insert(conn, "event", task_id=task_id, claim_id=None, kind=CONTINUES_KIND,
               actor="human", payload={"after": after}, created_at=_now())

    # Which working tree this task was opened in.
    #
    # One ledger serves every worktree -- `ledger_path` reads
    # `git rev-parse --git-common-dir` and says so -- so several tasks can be
    # open at once in several trees, which is how parallel work is done here.
    # Measured on the reference adopter: eight worktrees, five open tasks, one
    # task per tree, and nothing recorded the pairing. `doctor` could say "5
    # unshipped" and could not say where any of them was; the pairing lived in
    # a naming convention (`t-w2-workers` beside `wt-k1-workers`) that nothing
    # reads and nothing enforces.
    #
    # It matters more than tidiness: with two tasks open and `V4_TASK` unset,
    # the write hook takes the newest and judges a write against a scope that is
    # not its own. A reader who can see which tree each task belongs to can see
    # that coming; one who cannot, cannot.
    #
    # An event rather than a column, for the third time on this function and the
    # same reason: no schema change, and a fact that was true when the task was
    # opened belongs in the append-only record.
    insert(conn, "event", task_id=task_id, claim_id=None, kind=WORKTREE_KIND,
           actor="human", payload={"root": str(Path(cfg.root).resolve())},
           created_at=_now())
    return base


def worktree_of(conn, task_id):
    """The working tree this task was opened in, or None for one opened before
    that was recorded."""
    row = conn.execute(
        "SELECT payload FROM event WHERE task_id = ? AND kind = ? "
        "ORDER BY id LIMIT 1", (task_id, WORKTREE_KIND)).fetchone()
    return json.loads(row["payload"])["root"] if row else None


def continues(conn, task_id):
    """The task this one continues, or None."""
    row = conn.execute(
        "SELECT payload FROM event WHERE task_id = ? AND kind = ? "
        "ORDER BY id LIMIT 1", (task_id, CONTINUES_KIND)).fetchone()
    return json.loads(row["payload"])["after"] if row else None


def continued_by(conn, task_id):
    """Every task opened as a continuation of this one.  The query that had no
    answer while the edge lived inside a text field."""
    return [r["task_id"] for r in conn.execute(
        "SELECT task_id, payload FROM event WHERE kind = ? ORDER BY id",
        (CONTINUES_KIND,)) if json.loads(r["payload"])["after"] == task_id]


def _task(conn, task_id):
    row = conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"no such task: {task_id}")
    return row


def _kinds(cfg):
    return cfg.kinds


def _checker_sha_of(cfg):
    return lambda cid: cfg.checker_sha_on_disk(cid)


def derive(conn, cfg, task_id, phase="open"):
    t = _task(conn, task_id)
    # `scope.current_scope`, not the task row. Widening is recorded as an event
    # rather than by mutating the row -- the ledger is append-only -- and both
    # this and the subject below re-derived the scope from the row alone, so a
    # widened path stayed outside scope for every checker and every detector.
    #
    # Measured: `v4 scope widen --add CLAUDE.md` printed the new three-path
    # scope and `v4 scope show` agreed, while `scope` went on reporting
    # CLAUDE.md as outside a two-path scope. The one command whose whole purpose
    # is to say "this file belongs here" did not reach the gate that asks.
    from . import scope as scope_mod
    scope = scope_mod.current_scope(conn, task_id)
    files = _files_in_scope(cfg.root, scope,
                            exclude=cfg.config.get("derive_exclude", []))
    return derive_mod.derive(conn, cfg, task_id=task_id, scope_globs=scope,
                             subject_files=files, phase=phase)


def _files_in_scope(root: Path, scope_globs, exclude=()):
    """Files a detector should look at.

    `derive_exclude` exists for one reason worth naming: red fixtures are
    deliberately broken code. A detector firing on them is not a finding, it is
    the fixture doing its job, and letting those become claims would bury the
    real ones under noise the repo put there on purpose.

    The candidate list is `hashing.tree_state`, which is the same list the
    staleness digest is taken over. It used to be `root.rglob("*")`, and that
    made two answers to one question: on the reference adopter the walk returned
    94,147 paths in 4.5s where the digest covered a few thousand, and the
    difference was `.venv/` and `node_modules/`. So detectors read a vendored
    library that the key those detectors' claims hang off did not cover.

    A directory git does not manage falls back to the walk. That is not a
    repo under judgment -- it is a test fixture or a scratch dir -- and
    returning nothing there would silently derive zero claims.
    """
    from . import hashing
    from .analysis.subject_files import matches

    known = hashing.tree_state(root)
    if known:
        candidates = known.keys()
    else:
        candidates = (str(p.relative_to(root)) for p in root.rglob("*")
                      if p.is_file() and "/.git/" not in f"/{p}")
    return sorted(rel for rel in candidates
                  if matches(rel, scope_globs) and not matches(rel, exclude))


#: A checker this slow is one you do not want to run twice for nothing.
#:
#: Not a guess. Measured on the reference adopter: `test` has a median of 567
#: seconds and every other kind is at or under 1.5 -- one kind is 380x the next,
#: so anywhere in that gap is the same threshold.
EXPENSIVE_MS = 30_000


def kind_cost(conn, kind) -> int:
    """What this kind has actually cost in this repo, as a median.  0 if new.

    From the ledger rather than from `timeout_sec`, which is a ceiling somebody
    wrote down and not a measurement. A kind nobody has run reads as cheap: the
    first run is how it stops being unknown.
    """
    rows = conn.execute(
        "SELECT a.duration_ms FROM attempt a JOIN claim c ON c.id = a.claim_id "
        "WHERE c.kind = ? ORDER BY a.duration_ms", (kind,)).fetchall()
    return rows[len(rows) // 2][0] if rows else 0


def check(conn, cfg, task_id, only=None, run_expensive=True):
    """Run the checker for every claim that is not already answered."""
    t = _task(conn, task_id)
    results = []
    # The repo, not the task's scope. Scope says where work may go; it does not
    # say what a checker may read. `registry-consistency` reads `.v4/`,
    # `design-pins` reads `**/*.md`, and neither is ever inside a task's scope --
    # testing against scope made both permanently UNSUPPORTED, which trades one
    # dishonest answer for another. Computed once, not once per claim.
    # Lazily, because only a claim with no file refs needs it -- a repo-scoped
    # one -- and a task whose claims all name files walked the whole tree for
    # nothing. Computed at most once per `check`.
    _repo_files: list = []

    def in_repo():
        if not _repo_files:
            _repo_files.extend(_files_in_scope(
                cfg.root, ["**"],
                exclude=cfg.config.get("derive_exclude", [])))
        return _repo_files
    # Cheapest first, by what each kind has actually cost here.
    #
    # A repo-scoped claim is keyed on the content of the tree, so any edit
    # anywhere expires it. Run the 567-second one before the 0.1-second ones and
    # every failure among them means an edit, and the 567 seconds are spent
    # before the answer they produced can be true. Measured across one day on
    # the reference adopter: `test` ran 18 times for 143 minutes, and 25 of those
    # minutes were spent while a claim that can only be answered by editing a
    # file was still failing -- known to be void before they started.
    #
    # Ordering alone is most of it: put the cheap ones first and their failures
    # arrive in a second, before anything expensive has begun.
    # One query for every kind, not one per claim per use. `kind_cost` reads the
    # whole attempt table for a kind, `claim.kind` has no index, and the ledger
    # is append-only -- and it was called three times per claim here (ordering,
    # the skip decision, the failure decision). Scoped to this call so its
    # lifetime is visible: a `lru_cache` would go stale the moment an attempt
    # lands, with nothing saying when it is cleared.
    claims = state.task_claims(conn, task_id)
    cost = {k: kind_cost(conn, k) for k in {r["kind"] for r in claims}}
    claims = sorted(claims, key=lambda r: cost[r["kind"]])
    failed_cheap = []
    skipped = []
    for row in claims:
        if only and row["id"] not in only:
            continue
        # `--claim` is somebody asking for this one by name. Never skipped.
        if (not only and failed_cheap and not run_expensive
                and cost[row["kind"]] >= EXPENSIVE_MS):
            skipped.append((row, failed_cheap))
            results.append((row, "SKIPPED_EXPENSIVE", None))
            continue
        st = state.claim_state(conn, cfg.root, row, kinds_cfg=_kinds(cfg),
                               config_sha=cfg.sha, checker_sha_of=_checker_sha_of(cfg),
                               facts_sha_of=cfg.facts_sha_for,
                               reads_of=cfg.reads_for)
        if st in state.TERMINAL and not only:
            results.append((row, st, None))
            continue

        from . import engagement
        if engagement.required_for(cfg, row) and not engagement.accepted_for(conn, row["id"]):
            # Refused before the work, not after it. A checkpoint that costs
            # nothing to hit is one people hit; one that costs twenty minutes
            # is one they route around.
            results.append((row, "NEEDS_ENGAGEMENT", None))
            continue

        try:
            cid, path, entry = cfg.checker_for(row["kind"])
        except config_mod.ConfigError:
            # The kind was registered when this claim was raised and is not now
            # -- `v4` can remove one, and the rows it already produced stay by
            # design. `cfg.kind` says "a detector that emits an unregistered
            # kind is refused", which is the right refusal at the wrong moment:
            # nothing is emitting here, and letting it escape the loop takes
            # every other claim on the task down with it.
            #
            # Not retracted, and not passed. `_retract_orphans` will not retract
            # a claim that failed, so that removing a rule cannot remove a
            # finding; answering one here would be the same move through a
            # different door. Exit 4 is what this is -- nobody can verify it --
            # and it is not terminal, so it keeps holding and `doctor` reports
            # it under `unanswerable kinds`.
            res = runner.CheckResult(
                runner.UNSUPPORTED, "",
                f"claim kind {row['kind']!r} was registered when this claim "
                f"was raised and is not now, so nothing can judge it. It is "
                f"not answered and not retracted: `v4 doctor` lists it under "
                f"`unanswerable kinds`, and it clears when the kind comes "
                f"back or somebody signs it.",
                [], 0, {}, "")
            runner.record(conn, row["id"], res, repo_root=cfg.root,
                          config_sha=cfg.sha, worktree=str(cfg.root),
                          facts_sha="")
            results.append((row, None, res))
            continue
        refs = json.loads(row["subject_refs"])

        # Nothing here this checker reads. Recorded as UNSUPPORTED without
        # running it, because a program that never executed cannot report a
        # clean repo -- and eight of twenty-seven did exactly that on a Go
        # tree, including one that answered PASS about an unpinned dependency.
        named = [r["path"] for r in refs if r.get("kind") == "file"] or in_repo()
        if not subject_files.readable(named, entry.get("reads")):
            res = runner.CheckResult(
                runner.UNSUPPORTED, "",
                f"nothing in this repo matches what {cid!r} reads "
                f"({', '.join(entry.get('reads') or [])}), so it was not run. "
                f"A checker with nothing to read has no verdict; it does not "
                f"have a clean one.",
                [], 0, {}, cfg.checker_sha_on_disk(cid))
            runner.record(conn, row["id"], res, repo_root=cfg.root,
                          config_sha=cfg.sha, worktree=str(cfg.root),
                          facts_sha=cfg.facts_sha_for(row["checker"]))
            results.append((row, None, res))
            continue
        # `runner.Subject`, not a dict literal. This was one of three hand-built
        # copies of the one contract that crosses into a checker's process, and
        # the only one that sent `derive_exclude` -- which is why the probe in
        # `register` did not.
        #
        # `derive_exclude` travels with the subject rather than staying in
        # `_files_in_scope`, because a repo-scoped claim names no refs and every
        # checker that serves one re-derives the file set from git -- at which
        # point the kernel's own rule about deliberately-broken fixture code is
        # not in the picture.
        #
        # Measured on a first adoption: `v4 install` puts the checkers' bypass
        # fixtures into the repo, and `secret` reported 13 committed
        # credentials, all of them the fixture doing its job.
        payload = runner.Subject(
            repo_root=str(cfg.root), claim_id=row["id"], claim_kind=row["kind"],
            task_id=task_id, diff_base=t["base_commit"], subject_refs=refs,
            symbol=row["symbol"] or "", variant=row["variant"] or "",
            params=runner.subject_params(
                scope_globs=_scope_now(conn, task_id),
                forbid_globs=forbidden(conn, task_id),
                derive_exclude=cfg.config.get("derive_exclude", []),
                **_extra_params(conn, row)),
        ).as_dict()
        # Taken before the checker runs, not after. Taking it after absorbs
        # anything that lands during the run -- and a test suite runs for
        # twenty seconds, which is plenty of room for another worktree to edit
        # a file. The answer would then certify bytes it never saw, which is
        # the hole worktree_digest was introduced to close.
        kind_cfg = cfg.kinds.get(row["kind"], {})
        repo_scoped = kind_cfg.get("staleness") == state.REPO_SCOPED
        # The state, not only the digest it hashes to. If the tree moves the
        # digest says so and cannot say what -- and what is almost always the
        # answer: the checker itself wrote something into the repo it is
        # judging. Kept only for a repo-scoped claim, and only until the run
        # ends.
        before_state = hashing.tree_state(cfg.root) if repo_scoped else {}
        # Over what this checker declared it reads, because that is the key the
        # answer is stored under and compared against. Taking the two stamps
        # over the whole tree while `state` compares a narrower one would make
        # every repo-scoped answer arrive stale the moment it was written.
        reads = cfg.reads_for(row["checker"])
        stamp = (hashing.worktree_digest(cfg.root, reads=reads) if repo_scoped
                 else runner.head_commit(cfg.root))

        res = runner.run_checker(
            repo_root=cfg.root, checker_path=path, registered_sha=entry.get("sha256"),
            subject_payload=payload, subject_refs=refs,
            # A named function, not two nested lambdas built at the call site.
            # `runner` is the layer that execs a checker and reads an exit code,
            # and this argument is a database read threaded down into
            # `hashing.subject_digest`; `hashing` states why it is injected
            # ("so this module stays free of database imports") and that
            # argument says nothing about why it should be anonymous.
            latest_attempt_id=_latest_attempt_id_of(conn),
            facts=cfg.facts or None, timeout_sec=entry.get("timeout_sec", config_mod.DEFAULT_CHECKER_TIMEOUT),
        )
        after = (hashing.worktree_digest(cfg.root, reads=reads) if repo_scoped
                 else runner.head_commit(cfg.root))
        if after != stamp:
            # Something moved mid-run. Neither stamp describes what was tested,
            # so the attempt is recorded as an error rather than guessed at.
            res = runner.CheckResult(
                runner.SUBJECT_MOVED, res.stdout,
                f"{res.stderr}\nthe working tree changed while the checker ran; "
                f"this answer describes neither state"
                + (("\n  " + "\n  ".join(
                    hashing.moved(before_state, hashing.tree_state(cfg.root))))
                   if repo_scoped else "")
                + ("\n\nIf the checker wrote these itself, that is the whole of "
                   "the problem: a staleness key the act of answering moves is "
                   "not a staleness key. Write outside the repo, or name the "
                   "path in `derive_exclude` / .gitignore." if repo_scoped else ""),
                res.argv, res.duration_ms, res.subject_digest, res.checker_sha)

        runner.record(conn, row["id"], res, repo_root=cfg.root, config_sha=cfg.sha,
                      worktree=str(cfg.root), staleness_stamp=stamp,
                      facts_sha=cfg.facts_sha_for(row["checker"]))
        results.append((row, None, res))
        # Only a cheap failure holds back an expensive claim. An expensive one
        # that fails has already been paid for.
        if res.exit_code not in (0, 4) and cost[row["kind"]] < EXPENSIVE_MS:
            failed_cheap.append(row["kind"])

    # Refresh the anchor after the run, so the next audit knows how long the
    # chain is supposed to be. It has to be committed to be worth anything.
    #
    # And it moves on every check while `.v4/ledger_export.jsonl` is written
    # only by `ship`, so the two committed files are snapshots of different
    # moments *by design*: an export is what the ledger looked like when the
    # task ended, and the anchor is how long the chain is now. CI compared them
    # to each other and failed all six runs at "export has 72 attempts, anchor
    # records 81" -- the ordinary gap between a ship and the checks after it,
    # reported as tampering. The export carries its own anchor, and that is
    # what `verify_exported` reads; nothing here needs to be held back to keep
    # the two in step.
    from .ledger import write_chain_head
    write_chain_head(conn, cfg.root)
    return results


def _latest_attempt_id_of(conn):
    """The reader `runner` is handed for "what was the last answer here".

    `ledger.attempt_reader` is that reader, and this is the name it goes under
    at the call site below. It used to build the closure here instead, over
    `ledger.latest_attempt_id` -- and this module binds `ledger_mod`, never
    `ledger`, so every call raised `NameError`. Nothing caught it because
    nothing reached it: `hashing.subject_digest` calls this only for a subject
    ref whose kind is not `file`, and no claim in this ledger has ever had one.

    So the repair is not the missing `_mod`. It is that a one-line reader had
    two constructions -- this one and `state._digest_now`'s lambda -- of which
    only the second ever ran.
    """
    return ledger_mod.attempt_reader(conn)


def _scope_now(conn, task_id):
    """The task's scope including everything `v4 scope widen` has added."""
    from . import scope as scope_mod
    return scope_mod.current_scope(conn, task_id)


def _extra_params(conn, row):
    """Whatever a claim needs that its kind cannot know in advance.

    A review finding is closed by a test chosen after the claim exists, and the
    ledger takes no updates, so it arrives as an event and is merged here.
    """
    if row["kind"] != "review-finding":
        return {}
    from . import review
    return review.closing_params(conn, row["id"])


def report(conn, cfg, task_id):
    """`(rows, blocked, reported)`.  The third is what defers, with its reason.

    Handed `cfg.thresholds` because escalation is configured rather than
    compiled: a `report` kind that piles up, ages, or keeps re-failing blocks
    anyway, and the three numbers that decide it live in `.v4/config.json`.
    """
    rows, blocked, reported = state.task_report(
        conn, cfg.root, task_id, kinds_cfg=_kinds(cfg), config_sha=cfg.sha,
        checker_sha_of=_checker_sha_of(cfg), facts_sha_of=cfg.facts_sha_for,
        reads_of=cfg.reads_for, thresholds=cfg.thresholds)
    return rows, blocked, reported


def _nothing_seen(coverage):
    """Detectors that completed, raised nothing, and had nothing to read.

    `(ran=True, claims=0)` is 2,842 of 3,502 rows on the reference adopter and
    it has four causes: a clean tree, a word list that did not match, a facts
    table naming none of these symbols, or a subject holding no file this
    detector's kind can read. Only the fourth is decidable from here, and it is
    the one that grows with adoption -- a TypeScript and Go fixture with no
    `.py` at all raised 7 claims from 11 detectors and shipped green.

    The fourth used to be answered by counting `.py` in the subject, which was
    a proxy for "can anything here be parsed" and stopped being one the day a
    second language arrived. `derive` now asks each detector's own `reads`, so
    this reads a fact instead of a proxy.

    A subject with no files at all is not this: nothing to read because nothing
    changed is a clean answer, and reporting it would put a warning on every
    documentation-only task.
    """
    out = []
    for name, p in sorted(coverage.items()):
        if not p.get("ran") or p.get("claims"):
            continue
        c = p.get("considered")
        # Rows written before this field existed carry no opinion, and reading
        # their absence as "saw nothing" would put every historical task into
        # this list on the first ship after the upgrade.
        if not c or not c.get("files"):
            continue
        # `None` is no verdict -- an unregistered detector, or a kind that
        # declared no `reads`. Only an explicit False is "there was nothing
        # here it could read".
        if p.get("could_read") is False:
            out.append((name, c["files"]))
    return out


def ship(conn, cfg, task_id, max_rounds=None):
    """SPEC.md §4.  One predicate, plus a bounded re-derive."""
    from .ledger import audit_chain
    max_rounds = max_rounds or cfg.thresholds["ship_rederive_max"]

    # The budget belongs to the task, not to one invocation. Reading it fresh
    # each call meant `v4 ship` three times handed out three fresh budgets --
    # and every round appends claims to an append-only ledger, so retrying was
    # a way to accumulate permanently unanswerable claims with nothing that
    # cleared them.
    # Rounds that produced claims. A derive that created nothing is the
    # convergence this budget exists to wait for, not a round of it.
    #
    # It counted every round, and the loop records one per `v4 ship` before it
    # can know whether anything came back -- so three ships that each converged
    # on the first derive spent the whole budget, and the task became
    # permanently unshippable. Measured on the reference adopter: `t-resign`
    # had three `ship_round` events, `created=0` on all three, and every one
    # of those ships had failed for a reason that had nothing to do with
    # re-deriving (the signature chain was broken elsewhere in the repo).
    #
    # The message the budget prints says what it is for -- "a detector is
    # emitting claims that answering it creates" -- and that is divergence. A
    # converged round is the opposite and must not be charged for.
    #
    # `created` has been in the payload since this was written, so the older
    # rounds answer the new question too and a task charged wrongly is not
    # left charged.
    spent = 0
    for r in conn.execute(
            "SELECT payload FROM event WHERE task_id = ? AND kind = 'ship_round'",
            (task_id,)):
        try:
            spent += 1 if json.loads(r["payload"]).get("created") else 0
        except (ValueError, TypeError):
            spent += 1          # unreadable: charge it, the safe direction
    remaining = max_rounds - spent
    if remaining <= 0:
        return False, {"converged": False, "rounds": [], "spent": spent,
                       "why": f"this task has already spent its {max_rounds} "
                              f"re-derive rounds. Re-deriving again would add more "
                              f"claims nobody can answer. A detector is emitting "
                              f"claims that answering it creates -- fix the detector."}

    rounds = []
    for i in range(remaining):
        res = derive(conn, cfg, task_id, phase="ship")
        insert(conn, "event", task_id=task_id, claim_id=None, kind="ship_round",
               actor="kernel", payload={"created": len(res["created"])},
               created_at=_now())
        rounds.append(len(res["created"]))
        if not res["created"]:
            break
    else:
        insert(conn, "event", task_id=task_id, claim_id=None, kind="blocked",
               actor="kernel",
               payload={"reason": "ship_not_converging", "rounds": rounds},
               created_at=_now())
        return False, {"converged": False, "rounds": rounds,
                       "why": "re-deriving before ship kept producing new claims. "
                              "That is a detector emitting claims that answering it "
                              "creates -- a bug in a detector, not a risk to sign."}

    rows, blocked, reported = report(conn, cfg, task_id)
    _walked, chain_problems = audit_chain(conn, cfg.root)
    # A concurrent append is reported and does not hold the task. It means the
    # order of two rows is unproven and nothing else -- every row still hashes
    # over its own content and none is missing -- and the ledger is append-only
    # by design, so it cannot be repaired. Held, it would be one of this
    # framework's own writer bugs turning into a permanent verdict about the
    # repo that ran it. Measured on the reference adopter: 21 of them, all
    # `hook_seen`, all inside ten minutes of two sessions running at once.
    chain_ok = not ledger_mod.fatal(chain_problems)
    coverage = derive_mod.detector_coverage(conn, task_id)

    # Outside the pass/fail branch on purpose. The export records what happened,
    # not that it went well -- a held task is exactly the one an auditor wants to
    # walk. Writing it only on success would mean the chain is auditable only
    # when nobody needs to audit it.
    export = Path(cfg.root) / ".v4" / "ledger_export.jsonl"
    export.parent.mkdir(parents=True, exist_ok=True)
    # `cfg.root`, because the redaction floor in `export_jsonl` reads this
    # repo's own `.v4/secret_patterns.json`. This is the call that writes the
    # committed export, so it is the one where a narrower table costs the most.
    ledger_mod.export_jsonl(conn, export, cfg.root)

    # A facts table whose absences the installer wrote and nobody confirmed.
    # Adoption is allowed to proceed on those -- refusing there is what made a
    # repo with no outbound write unadoptable -- but shipping is not, because
    # every detector over that surface is reporting a clean repo on the strength
    # of a verb-ending sweep. The forcing function moves from the gate that
    # blocked the wrong thing to the one that blocks the right thing.
    unconfirmed = sorted(k for k, v in (cfg.facts or {}).get("absent", {}).items()
                         if isinstance(v, str) and v.startswith(facts_mod.AUTO_PREFIX))

    ok = (not blocked) and chain_ok and not unconfirmed
    if ok:
        # Recorded so the stop gate can tell a finished task from one nobody
        # tried to finish. Without it every ended turn looks the same.
        insert(conn, "event", task_id=task_id, claim_id=None, kind="shipped",
               actor="kernel", payload={"claims": len(rows)}, created_at=_now())
    return ok, {
        "converged": True, "rounds": rounds,
        "claims": [(r["id"], r["kind"], s) for r, s in rows],
        "blocked": [(r["id"], r["kind"], s) for r, s in blocked],
        # Unanswered and not holding this task, each with the reason its kind
        # defers -- or, where a threshold moved, the reason it is holding the
        # task anyway. Printed on every ship for the same reason `deferred` is:
        # **a claim that stopped blocking and stopped being mentioned has been
        # deleted, not deferred.** This framework has already paid for the
        # quieter version of that -- `nothing_seen` exists because
        # `(ran=True, claims=0)` was a line nobody could read, and 2,842 of
        # 3,502 detector runs were saying it.
        "reported": [(r["id"], r["kind"], s, why) for r, s, why in reported],
        "chain_ok": chain_ok, "chain_problems": chain_problems,
        "facts_unconfirmed": unconfirmed,
        # Printed, never gated. A deferral is a decision somebody made with
        # their name on it; the failure it replaces is the finding going quiet,
        # and a count on every ship is what stops that.
        "deferred": review_mod.deferred(conn),
        "detectors": {name: p.get("ran") for name, p in coverage.items()},
        "detectors_not_run": [n for n, p in coverage.items() if not p.get("ran")],
        # Detectors that ran, raised nothing, and had nothing they could read.
        # `detectors_not_run` covers the ones that were refused; this covers the
        # ones that completed against a subject holding no file their own
        # `reads` matches. The two are printed side by side because the failure
        # they share is the same: a report that says a question was asked when
        # it was not.
        #
        # Not gated. Whether eighteen kinds being unanswerable here is
        # acceptable is a decision about this repo, and the framework's part is
        # that nobody makes it by accident.
        "nothing_seen": _nothing_seen(coverage),
        # Detectors that raised claims this round and had every one of them
        # thrown away -- out of scope, or a kind this repo does not register.
        # `claims: 0` and "everything it found was dropped" were the same row
        # until `derive` started counting, and the in-memory `refused` list the
        # caller returns is discarded here, so this was the one report that
        # could not see it.
        "detectors_all_dropped": sorted(
            n for n, p in coverage.items()
            if p.get("ran") and not p.get("claims") and p.get("dropped")),
        # Which reviewer lenses printed their brief here. Not a review: this
        # row is written by `v4 review lens`, so it says the brief was taken
        # and nothing about whether anyone read a diff with it.
        "lenses_run": sorted({json.loads(r["payload"])["lens"] for r in conn.execute(
            "SELECT payload FROM event WHERE task_id = ? AND kind = 'lens_run'",
            (task_id,))}),
        # Which ones came back. Printed, never gated: a lens is judgement, and
        # gating on "did a judgement happen" buys a checkbox. What it buys
        # instead is that "nobody reviewed this" and "three reviewers found
        # nothing" stop looking the same -- which the row above could not do,
        # because printing a brief is free and reviewing is not.
        "lenses_reviewed": _lenses_reviewed(conn, task_id),
        # Where this task's claims came from. `lenses_run` says a reviewer ran;
        # this says whether it produced anything. Two different facts, and the
        # column carrying the second had three declared values, two writers and
        # no reader at all -- which is the shape `dead-wiring` exists to find,
        # in the one directory `dead-wiring` skips.
        "claim_origins": {
            r["origin"]: r["n"] for r in conn.execute(
                "SELECT origin, COUNT(*) n FROM claim WHERE task_id = ? "
                "GROUP BY origin ORDER BY origin", (task_id,))},
        # Measured, not configured: a hook that is installed but never fires is
        # the same as no hook, and only the events know which happened.
        "hook_seen": conn.execute(
            "SELECT COUNT(*) n FROM event WHERE task_id = ? AND kind = 'hook_seen'",
            (task_id,)).fetchone()["n"],
    }


#: Re-exported. It lives in `scope` now -- see there for why -- and this name
#: stays because it is what `lifecycle.forbidden` has always been called from
#: outside, and moving a function is not a reason to break that.
from .scope import forbidden                                   # noqa: E402,F401
