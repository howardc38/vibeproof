"""Signing for something that cannot be proved.  SPEC.md §6.

This is the only place a person is required, so it is worth being exact about
what it does and does not guarantee.

The TTY check is friction. `pty.spawn(['v4','risk','accept', ...])` defeats it
in one line, and the identity comes from `git config user.email`, which any
agent with a shell can rewrite. Calling it a boundary -- as an earlier draft
did -- is the same overstatement this project keeps having to retract.

The ways past it are not equal, so every one of them is named wherever any one
is, and `TTY_ROUTES` below is the list they are named from. Naming only the
pty -- which the refusal text did, while `--no-tty-check` appeared solely in
`--help` -- pointed the caller at the one that makes the record lie. Measured
on an adopter: a worker read that refusal, concluded a human at a terminal was
the only route, and abandoned a task whose fix was already written.

A third route landed on 2026-08-27 and this paragraph went on saying "the two
ways" -- which is how the protected-path message a worker reads went on
enumerating two of three. A sentence is not a list. The list is the constant,
and `tests/test_printed_exits_exist.py` holds every printed or raised text that
names two or more of them to naming all of them. Narrow on purpose: a refusal
that points at one route is pointing, not listing, and a rule that made every
sentence recite every flag would be the blanket version of this one.

What it does buy: every signature also writes a file into the repo, so bypassing
it leaves a commit with somebody's name on it. The anchor is a human reading
that diff, not the check.

And a signature expires exactly like a PASS does. It covers the bytes it was
shown. Without that, signing would be permanent where passing is not, and
signing would become the cheaper move -- which is the opposite of the point.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import state
from .ledger import (insert, git_identity,
                     latest_attempt as ledger_latest_attempt)

KINDS = ("unprovable", "no_checker", "baseline_raise", "scope_widen_protected")


class RefusedToSign(RuntimeError):
    pass


#: The two policies SPEC.md §6 offers a repo, and the one that means "not here".
#: `policy` was a required key that nothing read: an adopter writing
#: `no_accepted_risk` got the `allow` behaviour, and the only way to find out was
#: to sign something and watch it work. A configured value with no reader is the
#: shape this project's own `dead-wiring` checker exists to find, sitting in the
#: config template of the document that defines it.
NO_RISK = "no_accepted_risk"


#: `scope=repo` says a structural fact about the repo, not a judgement about one
#: task's work: there is no SPEC.md to resolve against, no lockfile to trace a
#: dependency to, no declared layers to check an import against. Those are the
#: only two risk kinds where that sentence is even meaningful -- a lint
#: violation or a widened scope is about work, and work is per task.
REPO_SCOPABLE = ("unprovable", "no_checker")
TASK, REPO = "task", "repo"


#: What `signed_by` says, in the committed record and in the ledger row.
#:
#: Named here because five texts and two reports spell these, and the day a
#: third value was added the reports still had two buckets.
PERSON, AGENT, MONITOR = "person", "agent", "monitor"

#: A signature row written before `accepted_risk.signed_by` existed. It cannot
#: be filled in later -- the ledger refuses UPDATE -- and it must not be guessed
#: from `was_tty`, because the guess would be a second copy of the rule below
#: and would silently answer for rows the rule never ran on.
UNRECORDED = "unrecorded"

#: Every route past the terminal check, and what each one records.
#:
#: The list rather than a sentence, because the sentence was "the two ways past
#: it" and stayed that way when a third was added. `tests/test_printed_exits_
#: exist.py` reads this and refuses any printed or raised text that names two of
#: these and not the rest, so the next route cannot be added to one place only.
#:
#: `pty` is a technique and the other two are flags; it is on the list anyway,
#: because what the list is for is what the record ends up saying, and
#: allocating a pty makes it say `person` for a signature no person gave.
TTY_ROUTES = (("--no-tty-check", AGENT), ("pty", PERSON), ("--as-monitor", MONITOR))


#: The origins that name a program rather than a hand -- which is the only half
#: of "did you raise this yourself" that `claim.origin` can answer.
#:
#: `.github/monitor/SCOPE.md` refused `risk accept` outright, for a reason that
#: is still right -- "signing away your own finding", one actor producing the
#: problem, the fix and the verdict. What that refusal also blocked is a
#: different relationship: a claim a *detector* raised, judged by a session that
#: did not write the code. There the signer is a third party to both.
#:
#: The guard wants to know *who* raised the claim. `origin` records *how* it was
#: made: `derive | review | widen`. Those are different questions, and only one
#: direction of the answer survives the difference. `origin = derive` does mean
#: no session raised this by hand -- a detector did, and a detector is not a
#: session -- so a monitor signing one really is a third party. `origin =
#: review` means a hand filed it and says nothing about whose hand, so a
#: finding filed by a different reviewer is indistinguishable from the signer's
#: own, and the refusal used to tell that caller "so it is yours", which was
#: false for that input.
#:
#: No column closes the gap. Two sessions in one repo share `git config
#: user.email`, and `v4` is a subprocess that is told nothing about which
#: session started it -- the same fact `--as-monitor` is friction rather than a
#: boundary for. A `claim.raised_by` would therefore record one identical
#: string for both sessions: a column with a writer and no decision able to use
#: it, which is what this repo's own `dead-wiring` checker exists to find.
#:
#: So the guard asks the question the column can answer, and the class it
#: refuses is wider than "yours": every hand-raised claim, including ones that
#: are somebody else's. That is the fail-closed direction, and the refusal says
#: so instead of asserting a fact about the reader.
RAISED_BY_A_PROGRAM = ("derive",)

#: `git log --format=%G?` values that mean a signature was there and verified.
#: `G` good, `U` good but the key is untrusted. Everything else -- `B` bad,
#: `E` key unavailable, `N` none, `X`/`Y`/`R` expired or revoked -- is a commit
#: whose author line is an assertion and nothing more.
VERIFIED_SIGNATURE = ("G", "U")


def attribution(record: dict) -> str:
    """The line a person reads after signing, from the record that was written.

    Here rather than inline in `cmd_risk` so it can be called. It was a
    one-liner printing `record["who"]` after the words "signed by", and `who` is
    a git identity: with `--no-tty-check` the same command writes
    `signed_by: agent` and `stdin_was_a_tty: false` into the file, so the record
    was honest and the terminal was not -- in the one place a person actually
    looks. `record` is the file's own content, so the two cannot drift.
    """
    signer = record.get("signed_by") or "person"
    if signer == "person":
        return f"signed by {record['who']} at {record['at']}"
    return (f"signed as {signer} at {record['at']} -- `{record['who']}` is the "
            f"git identity on this machine, not a person who saw this")


def commit_anchor(root: Path) -> dict:
    """Is this repo's history actually signed, or only asserted?

    Every signature record says its identity comes from `git config`, which is
    editable, and then names the durable half: *this file existing in a commit*.
    That sentence is only true where the commits carry a cryptographic
    statement of who made them. Measured on this repo the day it was written:
    `git log --format=%G?` returned `N` for all fifteen of the most recent
    commits, and `commit.gpgsign` and `user.signingkey` were both unset -- so
    the anchor was a filename and an author line taken from the same editable
    config the note was written to compensate for.

    Asked of git rather than assumed either way, and recorded in the record.
    A repo that does sign gets a record that says so and means it; a repo that
    does not gets one that does not claim otherwise. `kernel/ledger.py` goes to
    real length to make a forged attempt row detectable, and this is the layer
    above it saying, per signature, how much the file it just wrote is worth.

    Returns `{"head": <sha|"">, "signature": <%G? or "?">, "configured": bool,
    "verified": bool}`. `"?"` means git could not be asked, which is not the
    same as unsigned and must not read as it.
    """
    def git(*args):
        try:
            r = subprocess.run(["git", *args], cwd=str(root),
                               capture_output=True, text=True)
        except OSError:
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    head = git("rev-parse", "HEAD")
    mark = git("log", "-1", "--format=%G?")
    signing = git("config", "--get", "commit.gpgsign")
    key = git("config", "--get", "user.signingkey")
    return {
        "head": head or "",
        "signature": mark if mark else "?",
        "configured": (signing or "").lower() in ("true", "1", "yes")
                      and bool(key),
        "verified": mark in VERIFIED_SIGNATURE,
    }


def accept(conn, cfg, *, claim_id, kind, why, require_tty=True, scope=TASK,
           as_monitor=False):
    if cfg.config.get("policy") == NO_RISK:
        raise RefusedToSign(
            f"this repo's .v4/config.json sets policy={NO_RISK!r}, so there is "
            f"no signature route here. A claim that cannot be proved holds the "
            f"task, which is what that policy chose. Changing it is a line in a "
            f"diff with a name on the commit."
        )
    if kind not in KINDS:
        raise RefusedToSign(f"kind must be one of {KINDS}, not {kind!r}")
    if not why or len(why.strip()) < cfg.thresholds["min_chars"]:
        raise RefusedToSign(
            f"a reason under {cfg.thresholds['min_chars']} characters is not a reason. "
            f"This is the same bar engagement sentences answer to, for the same "
            f"purpose: a field that accepts anything gets filled with anything."
        )

    row = conn.execute("SELECT * FROM claim WHERE id = ?", (claim_id,)).fetchone()
    if row is None:
        raise RefusedToSign(f"no such claim: {claim_id}")

    if as_monitor and row["origin"] not in RAISED_BY_A_PROGRAM:
        raise RefusedToSign(
            f"a monitor signs what a program raised, and {claim_id} was raised "
            f"by hand: origin={row['origin']!r}.\n\n"
            f"The rule this keeps is the one `.github/monitor/SCOPE.md` states: "
            f"raising and settling belong to different sessions, because one "
            f"session doing both is one actor deciding it has convinced itself.\n\n"
            f"This is not a statement that the finding is yours. Nothing here "
            f"knows whose it is: `claim.origin` records how a claim was made, "
            f"not who made it, and two sessions in one repo share a git "
            f"identity, so a finding another reviewer filed reads exactly like "
            f"one you filed. Rather than guess, this refuses every hand-raised "
            f"claim -- and origin in {RAISED_BY_A_PROGRAM} is the one case "
            f"where the answer is certain, because a detector is not a "
            f"session.\n\n"
            f"What is left for this claim: whoever is answerable for it signs "
            f"with `--no-tty-check`, which records `signed_by: {AGENT}` and "
            f"says in the file that no person read it; or it is closed on its "
            f"merits -- `v4 review close` for a finding, `v4 check` for "
            f"anything a checker can still answer."
        )

    if scope not in (TASK, REPO):
        raise RefusedToSign(f"scope must be {TASK!r} or {REPO!r}, not {scope!r}")
    if scope == REPO:
        if kind not in REPO_SCOPABLE:
            raise RefusedToSign(
                f"only {REPO_SCOPABLE} can be signed for the repo. {kind!r} is "
                f"about this task's work, and work does not carry to the next "
                f"task. Sign it per task, or fix it."
            )
        if cfg.kinds.get(row["kind"], {}).get("staleness") != state.REPO_SCOPED:
            raise RefusedToSign(
                f"{row['kind']!r} is a subject-scoped kind: it is raised about "
                f"particular files, so 'this repo structurally cannot answer it' "
                f"is not a true sentence about it. Only a repo-scoped kind can "
                f"be signed for the repo."
            )
        # Earned, not asserted. Without this a signature can be written before
        # the checker has ever run, which makes `scope=repo` a way to switch a
        # checker off by hand -- the exact thing this framework is supposed to
        # make impossible.
        att = ledger_latest_attempt(conn, claim_id)
        if att is None or att["exit_code"] != 4:
            saw = "never run" if att is None else f"exit {att['exit_code']}"
            raise RefusedToSign(
                f"a repo-scoped signature says the checker cannot answer this "
                f"anywhere in this repo, and the checker has to have said so "
                f"first. Latest attempt for {claim_id}: {saw}. Run `v4 check` "
                f"and sign what it actually reports."
            )

    is_tty = bool(sys.stdin.isatty())
    # A monitor session is an agent by construction -- the role exists to be a
    # second one -- so asking it for a terminal asks for the thing it is not.
    # It still records what it is; that is the next block's job.
    if require_tty and as_monitor:
        require_tty = False
    if require_tty and not is_tty:
        raise RefusedToSign(
            "stdin is not a terminal, so this is refused.\n\n"
            "This is friction, not a boundary. If an agent is the one answerable "
            "for this waiver, say so on the record: run that same command again "
            "with `--no-tty-check`.\n\n"
            f"That signs it `{AGENT}` and writes a note saying `who` came from "
            f"git config and is not evidence that the person named read this "
            f"claim. There are two other ways past this line and neither is "
            f"that. Allocating a pty records the opposite -- `{PERSON}`, "
            f"indistinguishable from a signature somebody actually gave. "
            f"`--as-monitor` records `{MONITOR}` and is refused on anything "
            f"raised by hand; it belongs to a session that did not do this "
            f"work, and a worker passing it writes a false line into a "
            f"committed file.\n\n"
            "The check is here because the signature means a person took "
            "responsibility. The flag is here so that when nobody did, the file "
            "says so."
        )

    who = git_identity(cfg.root)
    now = datetime.now(timezone.utc).isoformat()
    ck = state.cover_key(cfg.root, row, config_sha=cfg.sha,
                         checker_sha_of=lambda cid: cfg.checker_sha_on_disk(cid)) \
        if scope == REPO else None
    # `who` comes from git config either way, and with the tty check waived it is
    # the identity of whoever configured the repo, not of anybody who read this
    # claim. A record naming a person with nothing saying an agent ran it reads
    # as that person having signed. Measured: a worker waived the check and
    # signed away the claim that was judging its own work, and the file it left
    # named the repo owner and said nothing else -- which is the case the
    # friction exists for. Recording it does not stop that; it stops it being
    # invisible, and the orchestrator that caught it had to read the prose to
    # find out.
    # Three values, because two could not tell the two agents apart. `agent`
    # was reached by `--no-tty-check` and said only "not a person" -- so a
    # worker signing away the claim judging its own work and a second session
    # signing a detector's claim about somebody else's code left the identical
    # record. Measured on this repo, 2026-08-27: three `agent` signatures in
    # one day, all three by the session that wrote the code.
    signed_by = MONITOR if as_monitor else (PERSON if is_tty else AGENT)
    # What the commit this file is about to land in is actually worth. Every
    # branch of the note below leans on it -- "this file existing in a commit is
    # the durable part", "a false line into a committed file, which is the
    # anchor the rest of this mechanism rests on" -- and none of them had ever
    # asked. Asked once, here, so all three say the same true thing.
    anchor = commit_anchor(cfg.root)
    anchored = (
        f"The commit this lands in carries a signature that verifies "
        f"(`git log --format=%G?` = {anchor['signature']}), so the anchor is "
        f"cryptographic rather than a name typed into a config."
        if anchor["verified"] else
        f"`git log -1 --format=%G?` here is {anchor['signature']!r} and "
        f"commit signing is {'configured' if anchor['configured'] else 'not configured'}, "
        f"so the commit this file lands in is not signed either: its author "
        f"line comes from the same editable `git config` as `who`. The durable "
        f"part is a person reading the diff, not the file being in git. Sign "
        f"the commits if this record is meant to say more than that."
        if anchor["signature"] != "?" else
        "git could not be asked whether the commits here are signed, so how "
        "much the anchor under this record is worth is unknown -- not proven, "
        "and not disproven.")
    record = {
        "claim": claim_id, "kind": kind, "scope": scope, "who": who,
        "signed_by": signed_by, "why": why,
        "at": now, "stdin_was_a_tty": is_tty,
        "claim_question": row["question"], "claim_kind": row["kind"],
        # The measurement beside the sentence: prose can be read past, a field
        # can be queried across every record in `.v4/risks/`.
        "commit_anchor": anchor,
        "note": (
            # What this value does and does not prove, said in the file rather
            # than left for a reader to infer from `signed_by` alone.
            "Signed --as-monitor: a session that did not raise this claim, on a "
            "claim a detector raised. What that is worth is that the signer is "
            "not the actor whose work is being waived. What it is not is proof "
            "of who ran it -- nothing tells `v4` which session started it, so "
            "this flag is friction rather than a boundary (SPEC.md §13), the "
            "same standing as --no-tty-check. A worker passing it is writing a "
            "false line into a committed file. " + anchored
            if as_monitor else
            "Identity comes from git config, which is editable. " + anchored
            if is_tty else
            "Signed with --no-tty-check, so `who` is the repo's git "
            "identity and not evidence that the person named read this. "
            "Whoever ran it is answerable for the claim they waived. "
            + anchored),
    }
    if scope == REPO:
        record["covers"] = ck
        record["lapses_when"] = (
            f"the checker for {row['kind']} stops exiting 4 -- which is what "
            f"happens the moment this repo grows the thing it was looking for -- "
            f"or the checker, its detector, or .v4/config.json changes.")
    # A repo-scoped record is named for what it is about, not for the claim that
    # happened to surface it: the claim id carries a task id, so naming the file
    # after it would file a permanent fact under whichever task first tripped
    # over it, and the next reader would have no way to find it.
    rec_path = (cfg.root / ".v4" / "risks" / "repo" / f"{row['kind']}.json"
                if scope == REPO else
                cfg.root / ".v4" / "risks" / f"{claim_id}.json")
    rec_path.parent.mkdir(parents=True, exist_ok=True)
    rec_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")

    # The key is taken after the record is written, not before. A repo-scoped
    # claim is keyed on working-tree content, and this file is part of that
    # tree -- computing the key first means the act of signing invalidates the
    # signature, which is the same shape as the bytecode that made every check
    # invalidate the last one.
    # Every injection `claim_state` makes when it reads this key back, and the
    # count is the point: four of them, built here and compared there, and the
    # day `reads_of` was added to one side it was not added to the other.
    #
    # Measured: a repo-scoped claim signed and then read back came out `OPEN`.
    # `worktree` is hashed over the paths the checker declared it reads, so a
    # key built with `reads_of=None` covers the whole tree and the one built at
    # verification covers a subset -- two different values, never equal, and a
    # signature that could not cover anything. 12 of this repo's 19 repo-scoped
    # kinds were unsignable, `secret`, `secret-chain` and `bundle-secret`
    # among them.
    key = state._staleness_key(
        conn, cfg.root, row, kinds_cfg=cfg.kinds, config_sha=cfg.sha,
        checker_sha_of=lambda cid: cfg.checker_sha_on_disk(cid),
        facts_sha_of=cfg.facts_sha_for, reads_of=cfg.reads_for)

    # `signed_by` goes into the row and not only into the file. The file was
    # the only place it lived for a day, and the file is not what any report
    # reads: `trend.signatures` and the `signed :` line `v4 ship` prints both
    # query this table, so a monitor signature and a `--no-tty-check` worker
    # signature were the same two columns -- `was_tty = 0` -- in the one place
    # anybody counts them. Which is the distinction the third value was added
    # to make.
    insert(conn, "accepted_risk", claim_id=claim_id, kind=kind, who=who, why=why,
           was_tty=int(is_tty), signed_by=signed_by,
           git_record=str(rec_path.relative_to(cfg.root)),
           subject_digest=key, created_at=now,
           scope=scope, cover_key=json.dumps(ck, sort_keys=True) if ck else "")
    return record, rec_path


#: What a claim's own state says to do about it.  `v4 risk waiting`.
#:
#: The command listed claims a signature *could* cover and stopped there, which
#: reads as a to-do list of signatures. Most of that list is not: a STALE claim
#: is one that was answered and whose subject moved, and `v4 check` asks it
#: again for nothing. Measured on this repo the day this was written: of the
#: claims the command listed, the two at the top were both STALE `test` claims
#: -- a re-run, not a decision.
#:
#: The distinction is mechanical, so the reader should not have to make it. A
#: signature is the expensive exit; it should be offered only where it is the
#: only one.
RERUN = "re-run"
#: The one staleness `RERUN` is wrong about. A detector edit leaves every claim
#: it had raised answering PASS and reading STALE, and only a re-derive moves
#: the sha the row carries -- so it is a different thing to do, not a footnote
#: on the same one.
REDERIVE = "re-derive, then re-run"
SIGN_REPO = "sign for the repo"
SIGN_ONLY = "signature is the only exit"
CLOSE_REDGREEN = "close with a red-green test"
#: The exit `SIGN_ONLY` was handed out instead of, for the population it was
#: built for. `review.bind_text_change` landed 2026-08-09 and this router was
#: written 2026-08-20, and it never asked whether the cheaper exit existed: it
#: read `resolve_symbol` raising as "no test can enter this symbol, therefore
#: nothing but a signature", when `checkers/review_finding._text_closure`
#: closes exactly that finding and needs no symbol at all.
CLOSE_TEXT = "close with a text closure"
FIX_FIRST = "fix the checker first"
MAKE_IT_PASS = "make the checker pass, then re-run"
TASK_ENDED = "signature is the only settlement"
ALREADY_SIGNED = "already signed -- nothing to do"


def _stale_reason(conn, cfg, row) -> str:
    """`state.stale_reason` with the arguments `claim_state` is given.

    Recomputed here rather than threaded through `route`'s signature: this runs
    on the handful of claims a person is looking at, and the alternative is
    every caller of `route` carrying a value only one branch of it reads.
    Falls back to the old sentence if there is no attempt to compare against,
    which cannot happen for a STALE claim and is not worth a crash if it does.
    """
    att = state.latest_attempt(conn, row["id"])
    if att is None:
        return "its subject moved."
    return state.stale_reason(
        conn, cfg.root, row, att, kinds_cfg=cfg.kinds, config_sha=cfg.sha,
        checker_sha_of=lambda cid: cfg.checker_sha_on_disk(cid),
        facts_sha_of=cfg.facts_sha_for, reads_of=cfg.reads_for) or "its subject moved."


def route(conn, cfg, row, st):
    """(what to do, why) for one claim that is not terminal.

    Reads the same facts `claim_state` read to reach `st`, and turns them into
    the one sentence the person in front of it needs.
    """
    from . import review as review_mod
    if st == state.UNSUPPORTED:
        return SIGN_REPO, (
            "the checker ran and said it cannot answer this. A repo-scoped "
            "signature (`--scope repo`) covers every task's claim of this kind "
            "and lapses by itself the day the checker can answer -- so it is "
            "the one signature that expires without anybody remembering.")
    if st in (state.CHECKER_ERROR, state.CHECKER_TAMPERED,
              state.SUBJECT_MOVED, state.TIMEOUT):
        return FIX_FIRST, (
            f"{st} is not an answer about the code -- it is the checker not "
            f"having answered. Signing it would record a person taking "
            f"responsibility for a verdict nobody has. Fix or re-register the "
            f"program, then ask again.")
    if st == state.STALE:
        # Which of the six inputs moved, not that one did. This branch used to
        # tell all six to run `v4 check`, and for the detector-sha one that is
        # advice that cannot work: the answer comes back PASS and the state
        # comes back STALE, every time. Followed eight times in one evening
        # before the pattern was visible at all.
        why = _stale_reason(conn, cfg, row)
        if state.NEEDS_DERIVE in why:
            return REDERIVE, (
                f"this was answered, and then {why} No signature is needed and "
                f"none would mean anything: the bytes it would cover are not "
                f"the bytes that were judged.")
        return RERUN, (
            f"this was answered, and then {why} `v4 check` asks it again and "
            f"costs a run of one checker. No signature is needed and none would "
            f"mean anything: the bytes it would cover are not the bytes that "
            f"were judged.")
    # OPEN.
    #
    # Whether the task is still open comes first, because every other answer
    # below assumes somebody can still work on it. Found by running this
    # command: it told a reader to close a `scope` finding on `t-001` with a
    # red-green test, and `t-001` was abandoned on 2026-08-13. `doctor`'s
    # `outlived findings` row already names this population; this is the same
    # fact reaching the person who is about to act on it.
    signed = conn.execute(
        # `git_record` as well, because the sentence below used to spell the
        # path out: `.v4/risks/{claim id}.json`. That is where a task-scoped
        # signature goes and not where a repo-scoped one goes -- `accept`
        # writes those to `.v4/risks/repo/{kind}.json`, and says why: a
        # permanent fact must not be filed under whichever task first tripped
        # over it. So the one command whose whole job is to tell a reader what
        # to do next sent half of them to a file that is not there, while the
        # row it had just read carried the real path.
        "SELECT why, created_at, git_record FROM accepted_risk "
        "WHERE claim_id = ? ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
    if signed:
        # A task-scoped signature covers the bytes it saw, so it lapses the
        # moment the tree moves and the claim reads OPEN again. On a task that
        # has ended, nothing will ever re-ask it -- so "sign this" is advice to
        # sign a second time. Found by running this command against a claim
        # signed forty minutes earlier.
        where = signed["git_record"] or f".v4/risks/{row['id']}.json"
        return ALREADY_SIGNED, (
            f"signed {str(signed['created_at'])[:19]}: "
            f"{str(signed['why'])[:140]} … the record is "
            f"{where}. It covers the bytes it saw and those "
            f"have since moved, which is why this reads OPEN again -- but the "
            f"decision was made and is in the chain.")
    if _task_has_ended(conn, row["task_id"]):
        return TASK_ENDED, (
            f"{row['task_id']} has ended, and nothing re-opens a task. A later "
            f"task starts from a new base, so a delta gate finds no delta and "
            f"stops asking -- the finding is not wrong, it is unasked. Repair "
            f"it inside a task that touches the file if you want it answered; "
            f"otherwise the settlement is a signature saying why it cannot be.")
    if row["file"] and row["symbol"]:
        try:
            review_mod.resolve_symbol(cfg.root, row["file"], row["symbol"])
        except review_mod.BadCoordinates as exc:
            # Red-green is out; that much was right. What follows from it was
            # not. A review finding has a second closure -- `v4 review close
            # --gone/--now`, red-green moved from behaviour to source text --
            # which needs no frame and was built for precisely the findings
            # whose repair is a sentence. Telling their author that a signature
            # is the only exit spends the one expensive move on a claim that
            # had a cheap one, which is what the constant above promises not to
            # do.
            #
            # The file has to be here for it: `_text_closure` reads it at the
            # parent commit and at HEAD, so a finding naming a path this repo
            # does not carry really is down to a signature.
            if (row["kind"] == review_mod.KIND
                    and (cfg.root / row["file"]).is_file()):
                return CLOSE_TEXT, (
                    f"{exc} So `redgreen.verify`'s third condition -- that the "
                    f"test actually entered the symbol -- can never hold. That "
                    f"is not the last exit: a finding whose repair is a "
                    f"sentence closes on the text moving, which needs no "
                    f"frame.\n\n"
                    f"  v4 --repo . review close --claim {row['id']} \\\n"
                    f"      --gone '<the sentence that has to go>' \\\n"
                    f"      --now '<what replaces it>' --parent <commit>\n\n"
                    f"It proves a string moved and says only that; a finding "
                    f"about behaviour still owes a test against a symbol a "
                    f"frame can be named after.")
            return SIGN_ONLY, (
                f"{exc} So `redgreen.verify`'s third condition -- that the test "
                f"actually entered the symbol -- can never hold, and a red-green "
                f"close is not available at any price.")
    if not row["symbol"]:
        # A repo-scoped kind -- `scope`, `test`, `lint` -- asks about the whole
        # tree and names no symbol. "Execute this symbol" is not advice there,
        # it is a sentence with nothing behind it.
        return MAKE_IT_PASS, (
            "this one names no symbol: it is asked about the tree, not about a "
            "call site. There is nothing to write a red-green test against -- "
            "do what the checker's own output asks for, then `v4 check` and it "
            "answers itself.")
    return CLOSE_REDGREEN, (
        "a test that is red at the parent commit, green at HEAD, and that "
        "actually executes this symbol. That is the cheap exit and it is the "
        "one that leaves something behind.")


def _task_has_ended(conn, task_id) -> bool:
    if not task_id:
        return False
    from .ledger import ENDED_KINDS
    marks = ", ".join(f"'{k}'" for k in ENDED_KINDS)
    return conn.execute(
        f"SELECT 1 FROM event WHERE task_id = ? AND kind IN ({marks}) LIMIT 1",
        (task_id,)).fetchone() is not None


def signatures(conn, task_id, *, empty="none"):
    """`(by_kind, by_signer, line)` -- what this task signed for, and who signed.

    `v4 status` and `v4 ship` both print this, and `status` prints it twice
    (once for a person, once for `--json`), so the join lived in the entry
    surface three times, spelled out by hand. Three copies of one query in the
    layer whose job is to print made `cli.py` the largest direct-SQL site in
    this repo -- fifteen queries, more than `lifecycle` or `review` -- and left
    the shape of a signature record free to move under three readers at once.

    Here because this module owns the `accepted_risk` table: it is what `accept`
    writes and what `route` and `waiting` read. Where a signature is recorded
    stops being a fact the printer knows.

    It groups by signer as well as by kind because grouping by kind alone is
    the half `--as-monitor` exists to make visible: a monitor's signature and a
    worker's own `--no-tty-check` signature counted as one number, and which of
    the two a task's terminal state rests on is the question that number was
    being asked. `unrecorded` is a row written before `accepted_risk.signed_by`
    existed -- not inferred from `was_tty`, for the reason `trend.signatures`
    gives.

    The rendered line comes back beside the dicts rather than from a second
    function. Two functions was the arrangement that let one of the three
    copies say `nothing` where the others said `none`: the same fact with two
    spellings in one command's output. `empty` is the only part that was ever
    deliberately different, so it stays a parameter and the rest cannot drift.
    """
    by_kind, by_signer = {}, {}
    for r in conn.execute(
            "SELECT ar.kind, ar.signed_by, COUNT(*) n FROM accepted_risk ar "
            "JOIN claim c ON c.id = ar.claim_id WHERE c.task_id = ? "
            "GROUP BY ar.kind, ar.signed_by", (task_id,)).fetchall():
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + r["n"]
        by_signer[r["signed_by"] or UNRECORDED] = (
            by_signer.get(r["signed_by"] or UNRECORDED, 0) + r["n"])
    line = (", ".join(f"{k}={n}" for k, n in sorted(by_kind.items()))
            + "  |  by "
            + ", ".join(f"{w}={n}" for w, n in sorted(by_signer.items()))
            ) if by_kind else empty
    return by_kind, by_signer, line


def closed_by_a_test(conn, kind, limit=1):
    """Claims of this kind that a red-green test really did close.

    The example a person needs to see before deciding a signature is the only
    way: somebody closed one of these with a test, and here is which test.
    """
    rows = conn.execute(
        "SELECT e.claim_id, e.payload, c.file, c.symbol FROM event e "
        "JOIN claim c ON c.id = e.claim_id "
        "WHERE e.kind = 'review_close' AND c.kind = ? "
        "ORDER BY e.id DESC LIMIT ?", (kind, limit)).fetchall()
    out = []
    for r in rows:
        try:
            p = json.loads(r["payload"])
        except (TypeError, ValueError):
            continue
        out.append((r["claim_id"], f"{r['file']}::{r['symbol']}",
                    p.get("closing_test") or "?"))
    return out


def waiting(conn, cfg, task_id=None, ended=False):
    """Claims that a signature would unblock.  `v4 risk waiting`.

    Not `v4 status --waiting`, which this said and which the parser has never
    declared: `status` takes `--json`, `--task` and `--detail`, so a reader
    following the docstring got "unrecognized arguments" and no hint that the
    command lives under a different subcommand.
    """
    from .ledger import ENDED_TASKS_SQL
    # Live tasks only, unless asked. The command scanned every claim this repo
    # has ever held -- 553 of them here, of which 234 were on tasks abandoned
    # in bring-up. A list that long is one nobody opens, which is the failure
    # `doctor` was fixed for twice: a number that is mostly noise stops being
    # read. What ended is `doctor`'s `outlived findings` row, and it says how
    # many.
    if task_id:
        sql, params = "SELECT * FROM claim WHERE task_id = ?", (task_id,)
    elif ended:
        sql, params = "SELECT * FROM claim", ()
    else:
        sql = f"SELECT * FROM claim WHERE task_id NOT IN ({ENDED_TASKS_SQL})"
        params = ()
    rows = conn.execute(sql, params).fetchall()
    out = []
    for row in rows:
        st = state.claim_state(conn, cfg.root, row, kinds_cfg=cfg.kinds,
                               config_sha=cfg.sha,
                               checker_sha_of=lambda cid: cfg.checker_sha_on_disk(cid),
                               facts_sha_of=cfg.facts_sha_for,
                               reads_of=cfg.reads_for)
        if st not in state.TERMINAL:
            out.append((row, st))
    return out
