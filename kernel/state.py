"""Claim state, derived.  SPEC.md §4.

There is no status column.  State is recomputed every time anybody asks, from
the claim's attempts, a fresh hash of its subject, the current HEAD, the
checker's current hash, the config's current hash, and whether a human signed.

Adding a status column here would give the ledger two sources of truth for the
same fact, which is the exact failure this whole design is built to avoid.
"""

import json
from pathlib import Path

from . import hashing, runner
from .ledger import latest_attempt

OPEN = "OPEN"
STALE = "STALE"
ANSWERED = "ANSWERED"
UNSUPPORTED = "UNSUPPORTED"
CHECKER_ERROR = "CHECKER_ERROR"      # the checker crashed
UNKNOWN_EXIT = "UNKNOWN_EXIT"        # runner gave a code this table has no row for
CHECKER_TAMPERED = "CHECKER_TAMPERED"  # its registered bytes are not its bytes
SUBJECT_MOVED = "SUBJECT_MOVED"      # the tree changed while it ran
TIMEOUT = "TIMEOUT"                  # it did not finish

#: `runner`'s exit codes, by the name it already gives them. Kept as a table
#: rather than a chain of `if`s so adding a code to `runner` and forgetting it
#: here is one missing row rather than a silent fall-through to CHECKER_ERROR.
_EXIT_STATE = {
    runner.ERROR: CHECKER_ERROR,
    runner.CHECKER_TAMPERED: CHECKER_TAMPERED,
    runner.SUBJECT_MOVED: SUBJECT_MOVED,
    runner.TIMEOUT: TIMEOUT,
}
RISK_ACCEPTED = "RISK_ACCEPTED"
RETRACTED = "RETRACTED"      # the code it was about is gone

# UNSUPPORTED is deliberately absent: a checker that cannot judge something
# has not judged it, and shipping on that is the hollow-scanner failure.
TERMINAL = {ANSWERED, RISK_ACCEPTED, RETRACTED}

#: A sentinel that equals no staleness key, so an unreadable signature covers
#: nothing rather than crashing the command that read it.
_UNREADABLE = object()

SUBJECT_SCOPED = "subject"
REPO_SCOPED = "repo"

#: The one staleness a re-run cannot clear, spelled once.
#:
#: `risk.route` reads this to know when its own advice does not apply, so the
#: sentence and the branch that decides it cannot drift apart.
NEEDS_DERIVE = ("`v4 check` cannot clear this -- only `v4 derive` appends the "
                "`claim_reraised` that moves the sha the row carries.")


def _digest_now(conn, repo_root, claim_row):
    refs = json.loads(claim_row["subject_refs"])

    from .ledger import attempt_reader
    digest = hashing.subject_digest(Path(repo_root), refs, attempt_reader(conn))
    if claim_row["kind"] == "review-finding":
        from . import review
        digest.update(review.closing_binding_digest(review.closing_params(conn, claim_row["id"]), repo_root))
    return digest


def claim_state(conn, repo_root, claim_row, *, kinds_cfg, config_sha, checker_sha_of,
                worktree=None, facts_sha_of=None, reads_of=None):
    """kinds_cfg: {kind: {...'staleness': 'subject'|'repo'}}
    checker_sha_of: (checker_id) -> current sha on disk
    reads_of: (checker_id) -> the paths that checker declared it reads, or None

    `reads_of` is why a repo-scoped claim does not expire on every edit in the
    repo. Injected like the two beside it, so this module still imports no
    config.
    """
    cid = claim_row["id"]

    if conn.execute(
        "SELECT 1 FROM event WHERE claim_id = ? AND kind = 'retracted' LIMIT 1", (cid,)
    ).fetchone():
        # Derivation is the authority on which claims exist. If a full scan no
        # longer raises this one and the files it was about are gone, it is not
        # a risk anybody should have to sign for -- it is a rename.
        return RETRACTED

    risk = conn.execute(
        "SELECT * FROM accepted_risk WHERE claim_id = ? ORDER BY id DESC LIMIT 1", (cid,)
    ).fetchone()
    if risk:
        # A signature covers the bytes it was given, exactly like a PASS does --
        # and it has to use the same key, or signing becomes cheaper than
        # passing. A repo-scoped claim has no subject files, so keying a
        # signature on subject digest alone would make it permanent: sign once
        # and the claim never reopens no matter what changes afterwards.
        #
        # A key that does not parse covers nothing. This repo's own ledger holds
        # one row whose `subject_digest` is the single character `d`, and every
        # command that resolves state -- `status`, `ship`, and the sweep that
        # found it -- died on that task with a JSONDecodeError. The ledger is
        # append-only, so the row can never be corrected: the only way out is
        # for a signature nobody can read to cover nothing, which is also the
        # safe direction.
        try:
            covered = json.loads(risk["subject_digest"])
        except (ValueError, TypeError):
            covered = _UNREADABLE
        if covered == _staleness_key(
            conn, repo_root, claim_row, kinds_cfg=kinds_cfg, config_sha=config_sha,
            checker_sha_of=checker_sha_of, worktree=worktree,
            facts_sha_of=facts_sha_of, reads_of=reads_of,
        ):
            return RISK_ACCEPTED

    att = latest_attempt(conn, cid)
    if att is None:
        return OPEN

    code = att["exit_code"]
    if code == runner.FAIL:
        return OPEN
    if code == runner.UNSUPPORTED:
        # A `scope=repo` signature applies here and nowhere else: it is read
        # only once the checker has actually said UNSUPPORTED for this claim,
        # so the checker still runs every task and the ledger still records
        # what it said. The signature covers the answer, it does not replace
        # the asking.
        if _repo_risk_covers(conn, repo_root, claim_row,
                             config_sha=config_sha, checker_sha_of=checker_sha_of):
            return RISK_ACCEPTED
        return UNSUPPORTED          # deliberately not terminal
    if code != runner.PASS:
        # Four distinct failures used to print as one string. `runner` names
        # them separately and writes a different stderr for each -- ERROR(5) is
        # a checker that crashed, CHECKER_TAMPERED(6) is one whose registered
        # bytes were swapped, SUBJECT_MOVED(7) is a tree that changed while it
        # ran, TIMEOUT(8) is one that hung -- and `v4 status`, `status --json`
        # and the ship report showed `CHECKER_ERROR` for all four. The one
        # command a person reads to find out what is wrong threw the
        # distinction away, and only `--detail` could recover it.
        # `UNKNOWN_EXIT`, not `CHECKER_ERROR`. The comment over `_EXIT_STATE`
        # says the table exists so that adding a code to `runner` and
        # forgetting it here "is one missing row rather than a silent
        # fall-through to CHECKER_ERROR" -- and the only read of it was
        # `.get(code, CHECKER_ERROR)`, which is that fall-through verbatim, in
        # a dict-with-a-default that is the chain of `if`s the sentence
        # contrasts itself with. Nothing falls through today; the property the
        # comment promised did not exist, and a new code -- or any checker
        # exiting 2 or 3 -- would have read as "the checker crashed" with
        # nothing saying the state was unrecognised.
        return _EXIT_STATE.get(code, UNKNOWN_EXIT)

    if stale_reason(conn, repo_root, claim_row, att, kinds_cfg=kinds_cfg,
                    config_sha=config_sha, checker_sha_of=checker_sha_of,
                    worktree=worktree, facts_sha_of=facts_sha_of,
                    reads_of=reads_of):
        return STALE

    return ANSWERED


def stale_reason(conn, repo_root, claim_row, att, *, kinds_cfg, config_sha,
                 checker_sha_of, worktree=None, facts_sha_of=None,
                 reads_of=None) -> str:
    """Which input moved since this answer, or `""` if none did.

    Six comparisons reached one word. `STALE` printed the same for all of them
    and `risk.route` told every one of them the same thing -- "`v4 check` asks
    it again and costs a run of one checker" -- which is true of five and false
    of the sixth. A detector edit is not cleared by re-running the checker: the
    row keeps the sha of the version that raised the claim until a re-derive
    appends `claim_reraised`, so the answer comes back PASS and the state comes
    back STALE, forever. Measured 2026-08-27 on this repo's own ledger: claim
    `c8f3f1d4` passed eight times between 12:34 and 17:31, read STALE after
    every one, and one `v4 derive` cleared it.

    So the reason is what this returns, and `claim_state` asks it the question
    it was already asking. One implementation: a second copy of six comparisons
    is how the printed reason and the verdict start disagreeing.
    """
    now_subject = _digest_now(conn, repo_root, claim_row)
    was_subject = json.loads(att["subject_digest"])
    if now_subject != was_subject:
        moved = sorted(k for k in set(now_subject) | set(was_subject)
                       if now_subject.get(k) != was_subject.get(k))
        return (f"its subject moved: {', '.join(m.split(':', 1)[-1] for m in moved[:3])}"
                + (f" and {len(moved) - 3} more" if len(moved) > 3 else ""))
    if config_sha != att["config_sha"]:
        # It said "the test command in it is an oracle", of a comparison that
        # is the sha of the whole file. That file also carries `thresholds`,
        # `lens_sweep`, `derive_exclude`, `protected_paths`, `surface_command`,
        # `surface_cwd`, `runtime_proof` and `truth_command`, and this repo's
        # own history has three commits -- 47ea047 (a repo rename), eedcc29
        # (three `report_max_*` thresholds) and 04db3e2 (`surface_command`,
        # `surface_cwd`) -- that edited it without touching `test_command`.
        # Each of those expired every answered claim in the repo and blamed a
        # test command that had not moved.
        #
        # The reason says what actually happened. The granularity does not
        # change here: `att["config_sha"]` is a whole-file sha already written
        # into every attempt row ever recorded, so narrowing what it covers
        # would make every one of those rows compare against a different
        # quantity and expire every answered claim in every adopter once. That
        # is a schema decision with a migration, not a wording repair, and the
        # comparison eight lines below shows the shape it would take:
        # `facts_sha_for` returns `""` for a checker that does not read the
        # table, so an unrelated edit costs it nothing.
        return (".v4/config.json changed. This compares the whole file, which "
                "carries the test command (an oracle) and also thresholds, "
                "protected paths, the sweep window and the probe commands -- "
                "so an edit to any of them expires this answer, and which one "
                "moved is not recorded")
    if checker_sha_of(claim_row["checker"]) != att["checker_sha"]:
        return (f"checker {claim_row['checker']} changed -- a different program "
                f"gave that answer")
    # The facts table is an oracle too, for the seven checkers that scan against
    # it. `RepoConfig.facts_sha` existed for exactly this and nothing read it, so
    # editing `.v4/facts.json` expired no answer anywhere -- an `external-write`
    # PASS given under one set of outbound patterns stayed answered under
    # another. `""` for every other kind, so an unrelated edit costs them nothing.
    if facts_sha_of is not None and \
            facts_sha_of(claim_row["checker"]) != (att["facts_sha"] or ""):
        return "the facts table changed, and this checker scans against it"
    if claim_row["detector"] and _detector_sha_now(repo_root, claim_row) != \
            _detector_sha_of_record(conn, claim_row):
        # The detector *file*, not the program behind it: `_detector_sha_now`
        # hashes the entry and not what it imports, so a rule that lives in
        # `kernel/analysis/` can change without moving this. Left as it is
        # rather than widened, because the same edit moves `checker` two lines
        # up -- 20 of 27 checkers are a thin CLI over the same module -- so no
        # claim survives it either way. What was wrong was the comment, which
        # claimed a coverage this line does not have.
        return (f"detectors/{claim_row['detector']} changed since it raised "
                f"this, and the row still carries the sha of the version that "
                f"did. {NEEDS_DERIVE}")

    kind = kinds_cfg.get(claim_row["kind"], {})
    if kind.get("staleness", SUBJECT_SCOPED) == REPO_SCOPED:
        # Content, not HEAD: a worker's edits are uncommitted, so keying on HEAD
        # means a `test` claim answered against a stub stays answered against
        # whatever replaces it.
        now = (worktree if worktree is not None
               else hashing.worktree_digest(
                   repo_root,
                   reads=reads_of(claim_row["checker"]) if reads_of else None))
        if now != att["head_commit"]:
            # cross-task semantic breakage lands here too.
            #
            # And so does a reader standing somewhere else. One ledger serves
            # every worktree, so `v4 status` in the main checkout answers about
            # *that* tree for a claim earned in another -- 13 ANSWERED read as
            # 13 STALE, measured on `t-outran`. `attempt.worktree` has held the
            # path all along; nothing compared it.
            where = att["worktree"] or ""
            if where and where != str(repo_root):
                return (f"it was answered in {where}, and you are reading from "
                        f"{repo_root}. The tree here is not the tree that "
                        f"answered it.")
            return "the tree this checker reads has changed content"

    return ""


def _repo_risk_covers(conn, repo_root, claim_row, *, config_sha, checker_sha_of):
    """Is there a repo-scoped signature that still matches this claim?"""
    want = cover_key(repo_root, claim_row,
                     config_sha=config_sha, checker_sha_of=checker_sha_of)
    for row in conn.execute(
        "SELECT cover_key FROM accepted_risk WHERE scope = 'repo' AND kind IN "
        "('unprovable', 'no_checker') ORDER BY id DESC"
    ):
        try:
            if json.loads(row["cover_key"] or "null") == want:
                return True
        except json.JSONDecodeError:
            continue
    return False


def repo_risks(conn):
    """Every live repo-scoped signature.  For `v4 status` and `v4 doctor`."""
    return conn.execute(
        "SELECT * FROM accepted_risk WHERE scope = 'repo' ORDER BY id DESC"
    ).fetchall()


def cover_key(repo_root, claim_row, *, config_sha, checker_sha_of):
    """What a `scope=repo` signature covers.  SPEC.md §6.1.

    Deliberately not the working tree. The thing being signed for is that this
    repo has no subject at all -- no SPEC.md, no lockfile, no declared layers --
    and editing an unrelated file does not make one appear. Keying it on the
    tree, the way an *answer* is keyed, is what forced the same structural fact
    to be re-signed once per task forever.

    What does lapse it: a different checker, a different detector, a different
    config -- and, enforced separately in `claim_state`, the checker no longer
    saying UNSUPPORTED. That last one is the important half: the day the file
    appears, the checker stops exiting 4 and this signature stops applying,
    without anyone having to remember to revoke it.
    """
    return {
        "kind": claim_row["kind"],
        "checker": checker_sha_of(claim_row["checker"]),
        "detector": _detector_sha_now(repo_root, claim_row),
        "config": config_sha,
    }


def _staleness_key(conn, repo_root, claim_row, *, kinds_cfg, config_sha, checker_sha_of,
                   worktree=None, facts_sha_of=None, reads_of=None):
    """Everything an answer depends on, in one comparable value.

    `facts` is here because `claim_state` expires a PASS on it and this did
    not. SPEC.md §6 says the two use the same key -- 過期 | 同 PASS 一樣,對同一條
    key -- and the comment above says why: "it has to use the same key, or
    signing becomes cheaper than passing." Sixteen of this repo's checkers scan
    against the facts table, so for every one of them editing
    `.v4/facts.<repo>.json` turned a PASS STALE and left a RISK_ACCEPTED
    covering: the signature was the cheaper exit, which is the inversion the
    rule exists to stop.
    """
    kind = kinds_cfg.get(claim_row["kind"], {})
    return {
        "subject": _digest_now(conn, repo_root, claim_row),
        "config": config_sha,
        "checker": checker_sha_of(claim_row["checker"]),
        # The detector decides a claim exists at all. Narrowing one should
        # invalidate what it raised under the old rule, the same way editing a
        # checker invalidates what it passed -- otherwise the column is written
        # and read by nobody, which is the shape this project keeps removing.
        "detector": _detector_sha_now(repo_root, claim_row),
        # Over the paths this checker declared it reads, not over the tree.
        # `worktree=` still wins when a caller computed the stamp itself --
        # `lifecycle.check` takes one before the run and one after, and both
        # have to be the same question.
        "worktree": ((worktree if worktree is not None
                      else hashing.worktree_digest(
                          repo_root,
                          reads=reads_of(claim_row["checker"]) if reads_of else None))
                     if kind.get("staleness", SUBJECT_SCOPED) == REPO_SCOPED else None),
        # `""` for a kind whose checker does not read the table, exactly as
        # `claim_state` does -- an unrelated edit must cost those nothing.
        "facts": (facts_sha_of(claim_row["checker"]) if facts_sha_of else ""),
    }


def _detector_sha_of_record(conn, claim_row) -> str:
    """The newest detector sha known to have raised this claim.

    The column holds whichever version raised it first, and the ledger takes no
    updates -- so a detector edit made every claim it had ever raised stale
    forever: the re-derive raises the same id, the row keeps the old sha, and
    no answer can ever match again. `derive` appends `claim_reraised` when
    newer bytes raise the same claim, and this is where that lands.
    """
    newest = conn.execute(
        "SELECT payload FROM event WHERE claim_id = ? AND kind = 'claim_reraised' "
        "ORDER BY id DESC LIMIT 1", (claim_row["id"],)).fetchone()
    if newest:
        try:
            return json.loads(newest["payload"])["sha"]
        except (ValueError, TypeError, KeyError):
            pass
    return claim_row["detector_sha"] or ""


def _detector_sha_now(repo_root, claim_row):
    name = claim_row["detector"]
    if not name:
        return ""
    return hashing.file_sha(Path(repo_root) / "detectors" / name)


#: What a kind costs to defer, and therefore whether an unanswered one holds a
#: ship. Two values only.
#:
#: `ship` is the default and the default is not a formality. A kind whose
#: registry entry says nothing about `gate` blocks, because the failure this
#: guards against is one-directional: a new kind that quietly did not block
#: would be a gate nobody installed, and nothing would say so. A kind that
#: blocks when it did not need to is visible on the first task.
GATE_SHIP = "ship"
GATE_REPORT = "report"


def gate_for(kinds_cfg, kind: str) -> str:
    """`ship` unless the registry says `report`.  Anything unknown is `ship`."""
    entry = (kinds_cfg or {}).get(kind) or {}
    return GATE_REPORT if entry.get("gate") == GATE_REPORT else GATE_SHIP


#: How long a `report` kind may stay unanswered before it blocks anyway.
#:
#: "可以唔理,唔可以永遠唔理". Three conditions, because deferral rots in three
#: different ways and a threshold on one of them misses the other two:
#:
#:   report_max_open    a pile nobody is working through
#:   report_max_days    one that has been there long enough to be forgotten
#:   report_max_repeat  the same claim failing over and over, which is not a
#:                      backlog at all -- it is a rule asking a question the
#:                      work keeps re-opening, and no amount of waiting fixes it
#:
#: The values live in `.v4/config.json`; `config.DEFAULT_THRESHOLDS` carries the
#: fallback. Constants here would be the shape `LINT-CONFIG-DUAL-TRUTH` exists
#: to catch -- a setting with two owners.
ESCALATE_KEYS = ("report_max_open", "report_max_days", "report_max_repeat")


def _fail_runs(conn, claim_id) -> int:
    """In how many *different states* this claim has failed.

    Not attempts. `report_max_repeat` is for a claim the work keeps re-opening
    -- one that will never settle, where waiting buys nothing -- and counting
    rows measures something else entirely: how many times somebody ran
    `v4 check`.

    Measured on this ledger when that was the count: `test-weakened` had 257
    failing attempts across **50 distinct states**, so 81% of them were the same
    question asked again. Its worst claim failed ten times in one state -- nine
    re-runs of an identical check, three of them inside the same minute. At a
    limit of five, that escalated 52% of `test-weakened` claims: the tier
    handing back with one rule what it granted with another, on the very kind
    it was written for. Counting states instead, no claim of any reporting kind
    has ever crossed five.

    The state is what an answer depends on and therefore what could change the
    verdict: the subject, the program, and the tree it read. Two attempts
    agreeing on all three asked one question twice.

    Exit 4 is excluded. A checker saying it cannot judge is not the work
    re-opening anything -- it is a different problem with a different exit, and
    escalating on it would punish a kind for being unanswerable here.
    """
    row = conn.execute(
        "SELECT COUNT(*) n FROM (SELECT DISTINCT subject_digest, checker_sha, "
        "worktree FROM attempt WHERE claim_id = ? AND exit_code NOT IN (0, 4))",
        (claim_id,)).fetchone()
    return (row["n"] if row else 0) or 0


def _opened_at(conn, claim_id):
    row = conn.execute("SELECT created_at FROM claim WHERE id = ?",
                       (claim_id,)).fetchone()
    return (row["created_at"] if row else "") or ""


def _escalates(conn, row, open_same_kind, thresholds):
    """Why this reported claim blocks anyway, or `""`.

    A sentence, not a boolean: the thing a person needs is which threshold
    moved, and `ship` prints it.
    """
    t = thresholds or {}
    n = t.get("report_max_open")
    if isinstance(n, int) and open_same_kind > n:
        return (f"{open_same_kind} open and the limit is {n} -- a pile this "
                f"size is not a backlog anybody is working through")
    days = t.get("report_max_days")
    opened = _opened_at(conn, row["id"])
    if isinstance(days, int) and opened:
        from datetime import datetime, timedelta, timezone
        try:
            when = datetime.fromisoformat(opened)
        except ValueError:
            when = None
        if when is not None:
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - when > timedelta(days=days):
                return (f"opened {opened[:10]}, past the {days}-day limit -- "
                        f"long enough that nobody is going to remember it")
    repeat = t.get("report_max_repeat")
    if isinstance(repeat, int):
        runs = _fail_runs(conn, row["id"])
        if runs > repeat:
            return (f"failed {runs} times and the limit is {repeat} -- the work "
                    f"keeps re-opening this, which waiting does not fix")
    return ""


def split_open(conn, out, kinds_cfg=None, thresholds=None):
    """`(blocked, reported)` for the claims that are not terminal.

    `blocked` holds every `ship` kind, plus any `report` kind past a threshold.
    `reported` holds the rest, each with the reason its kind defers -- so the
    caller can print them, which is the whole point. **A reported claim that
    nothing prints is worse than no tier at all**: this framework already paid
    for that lesson once, when `nothing_seen` had to be built because
    `(ran=True, claims=0)` was a line nobody could read, and 2,842 of 3,502
    detector runs were saying it.
    """
    unanswered = [(r, s) for r, s in out if s not in TERMINAL]
    per_kind = {}
    for r, _s in unanswered:
        if gate_for(kinds_cfg, r["kind"]) == GATE_REPORT:
            per_kind[r["kind"]] = per_kind.get(r["kind"], 0) + 1

    blocked, reported = [], []
    for r, s in unanswered:
        if gate_for(kinds_cfg, r["kind"]) == GATE_SHIP:
            blocked.append((r, s))
            continue
        why = _escalates(conn, r, per_kind.get(r["kind"], 0), thresholds)
        if why:
            blocked.append((r, s))
            reported.append((r, s, "ESCALATED: " + why))
        else:
            entry = (kinds_cfg or {}).get(r["kind"]) or {}
            reported.append((r, s, entry.get("gate_why", "")))
    return blocked, reported


def task_claims(conn, task_id):
    return conn.execute(
        "SELECT * FROM claim WHERE task_id = ? ORDER BY rowid", (task_id,)
    ).fetchall()


def task_report(conn, repo_root, task_id, **kw):
    """`(every claim, the ones that block, the ones that only report)`.

    Three values, not two, and the third is not optional to print. An
    unanswered `report` kind is still unanswered -- what changed is that it
    does not hold a ship, and a reader who is not told about it has been given
    a quieter system rather than a faster one. `split_open` says the rest.

    The worktree digest is taken once here and handed down. It was taken inside
    `claim_state`, once per repo-scoped claim, and it is the same answer every
    time within one report -- measured at 367ms on the reference adopter, so a
    task with twenty repo-scoped claims spent seven seconds asking git the same
    question twenty times.

    Handed down rather than cached in a module global on purpose.
    `lifecycle.py` takes this digest immediately before a checker runs and again
    immediately after, and those two are *required* to differ when the checker
    moved the tree -- that difference is the whole SUBJECT_MOVED signal. A cache
    that made them equal would delete it silently.
    """
    kinds_cfg = kw.get("kinds_cfg")
    thresholds = kw.pop("thresholds", None)
    rows = task_claims(conn, task_id)
    if "worktree" in kw:
        # A caller that took its own stamp is asking about that stamp.
        out = [(r, claim_state(conn, repo_root, r, **kw)) for r in rows]
        blocked, reported = split_open(conn, out, kinds_cfg, thresholds)
        return out, blocked, reported

    # One digest per distinct `reads` set. Twenty claims across five checkers
    # is five digests, not twenty -- and not one, because the whole point is
    # that a `test` claim and a `lint` claim no longer answer to the same
    # question about the tree. The memo is local to this call for the reason
    # the paragraph above gives: `lifecycle` needs its two stamps to be able
    # to differ.
    reads_of = kw.get("reads_of")
    memo, out = {}, []
    for r in rows:
        globs = tuple(reads_of(r["checker"]) or ()) if reads_of else ()
        if globs not in memo:
            memo[globs] = hashing.worktree_digest(
                repo_root, reads=list(globs) or None)
        out.append((r, claim_state(conn, repo_root, r,
                                   worktree=memo[globs], **kw)))
    blocked, reported = split_open(conn, out, kinds_cfg, thresholds)
    return out, blocked, reported
