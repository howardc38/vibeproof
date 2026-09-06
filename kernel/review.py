"""Turning a reviewer's finding into a claim, and closing it.  SPEC.md 4.4.

A reviewer is the only source of claims that no detector raises, so it needs
its own way in. Everything it may decide is an enum or a path the kernel
validates; the sentence it wants to say lives in `note`, which selects no
checker and enters no identity.

Closing is separate from raising, and both are events rather than columns,
because the ledger is append-only and the test that closes a finding is chosen
after the finding exists.
"""

import ast
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import hashing
from . import redgreen
from .config import DEFAULT_THRESHOLDS
from . import ledger as ledger_mod
from .ledger import insert

KIND = "review-finding"

#: What `origin` says about a claim a reviewer filed by hand.
#:
#: Named because two modules turn on it and only one wrote it down: `sweep.
#: raised_since` counts `kind = 'review-finding' AND origin = 'review'` as the
#: floor `reconcile` holds a sweep's self-reported number against, and deleting
#: that clause left every test in this repo green -- so the count would have
#: silently started including detector-raised findings, which is the direction
#: that hides "claimed many, raised few".
ORIGIN = "review"


def _now():
    return datetime.now(timezone.utc).isoformat()


class BadCoordinates(ValueError):
    """The finding names something red-green can never confirm."""


def resolve_symbol(root, file: str, symbol: str) -> str:
    """The name `redgreen` will actually match, or a refusal saying why.

    `redgreen` compares `code.co_name`, which is the bare name a frame carries:
    `_request`, never `ChatBotClient._request`. A finding filed dotted can
    therefore never be executed, so its claim can never close, so the only exit
    left is a signature -- and that is what happened. Measured on one eight-task
    run: 71 signatures, of which about 52 said some version of "my own malformed
    coordinates", one of them adding that it was "the second time this repo has
    paid for the same mistake".

    Two shapes were paid for and both are handled here rather than four hours
    later:

      `Class.method`   normalised to `method`, because that is the same symbol
                       said a longer way and refusing it would be pedantry

      a module-level constant   refused. `_WAIT_REASONS` is a frozenset; no
                       frame is ever named after it, so no test can execute it
                       and no red-green can ever pass. Better to say so at the
                       moment the finding is raised.
    """
    symbol = (symbol or "").strip()
    if not symbol:
        return ""
    path = Path(root) / file
    if not path.is_file():
        raise BadCoordinates(
            f"{file} is not in this repo. A finding whose file is not here "
            f"cannot be closed by any test that runs against it.")
    if not redgreen.traceable(path):
        # `.github/monitor/PROMPT.md` states this refusal as unconditional --
        # "--symbol must name something a stack frame can be named after ...
        # refused at this command rather than four hours later" -- and it only
        # applied to Python: any string at all was accepted for a document, a
        # JSON table or a non-Python source, and the claim that came back could
        # never be closed by red-green. Three are already in this ledger, two of
        # them naming functions that live in `.py` files somewhere else
        # entirely: `.v4/facts.vibeproof.json::writing` and `::widen`.
        #
        # The test is `redgreen.traceable` and not `.py`, because the question
        # is whether a frame in this file can be observed and that module is
        # what knows. Spelled here as a suffix it went stale the day a second
        # tracer landed: a Go or TypeScript finding was refused a symbol on the
        # grounds that nothing in it can be entered, while `_go_executed` and
        # `_node_executed` were entering exactly those files.
        #
        # Refusing the symbol rather than the finding: a finding *about* a JSON
        # table or a document is legitimate, and it closes by text closure --
        # `v4 review close --gone/--now` -- which needs no symbol at all.
        raise BadCoordinates(
            f"{file} is a file no tracer here can enter, so `--symbol {symbol}` "
            f"cannot be resolved against it. Raise it "
            f"with no --symbol: a finding about a document or a table closes "
            f"by text closure (`v4 review close --claim <id> --gone '<the "
            f"sentence that has to go>' --now '<what replaces it>'`), which "
            f"needs no frame.")
    # The two normalisations below read Python with `ast`, so they have nothing
    # to say about a `.go` or `.ts` file. Taken as written rather than guessed
    # at: `_go_executed` and `_node_executed` both compare a bare name, and a
    # name that turns out not to match is refused by `verify` with the trace in
    # hand, which is a better place to learn it than a parser that cannot read
    # the language.
    if path.suffix.lower() != ".py":
        return symbol
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return symbol
    defs, assigns = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs.add(n.name)
        elif isinstance(n, ast.Assign):
            assigns |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            assigns.add(n.target.id)

    bare = symbol.rsplit(".", 1)[-1]
    if bare in defs:
        return bare
    if bare in assigns:
        raise BadCoordinates(
            f"{symbol} is a value in {file}, not something that runs. A finding "
            f"closes by executing its symbol, and no stack frame is ever named "
            f"after an assignment -- so this claim could only ever be signed "
            f"away. Name the function whose behaviour the finding is about.")
    raise BadCoordinates(
        f"{file} defines no {bare!r}. A finding whose symbol is not in the file "
        f"cannot be closed by any test, so it would reach the end of the task as "
        f"a signature. Check the name against the file the reviewer pointed at.")


def ensure_review_task(conn):
    """The row a task-less finding hangs from, created the first time one lands.

    Not created at install: a repo that never sweeps should not carry a task it
    never used, and `doctor` reads the task list.

    Created `if_absent` rather than checked first. `task.id` is a PRIMARY KEY
    and this ledger is shared across worktrees, so "read, see nothing, insert"
    is a collision two parallel `v4 review add` calls will find -- and parallel
    is what the framework's own notes tell people to do. One statement, and
    there is no window to lose.
    """
    insert(conn, "task", if_absent=True, id=ledger_mod.REVIEW_TASK,
           request=("Findings raised by review rather than by a change: a "
                    "periodic lens sweep, or a reviewer reading a diff. Neither "
                    "has a task, and a finding with nowhere to go is a finding "
                    "nobody has to close."),
           scope_globs=["**"], base_commit="", created_at=_now())
    return ledger_mod.REVIEW_TASK


AMENDED_KIND = "finding_note_amended"


def current_note(conn, claim_id: str, stored: str = None) -> str:
    """The finding's text as it stands, amendments applied.

    `amend_note` records a correction as a `finding_note_amended` event and
    cannot do otherwise: `claim` carries append-only triggers, so `UPDATE claim
    SET note` is refused outright. What was missing is the other half. Nothing
    read the event -- grepping the tree found `AMENDED_KIND`, the insert, and
    one test -- while every reader of a finding's text read `claim.note`, the
    original row. So `v4 review add` printed "note amended on <id>", exited 0,
    and served the superseded text to the orchestrator that `status --json`
    exists for. The write happened and the read did not follow, which is the
    shape `checkers/dead_wiring.py` exists to find, in the kernel it skips.

    Fixing the read left the write reading the other one, which is the same
    defect facing the other way and cost more than the first: `raise_finding`
    compared an incoming note against `claim.note` -- the original row -- so a
    reviewer re-filing the *original* text to undo a mistaken amendment matched
    the row, got `already open`, wrote nothing, and left the wrong text
    serving. Measured on `2b7fc6d18f434df4`, 2026-08-27: a reviewer did exactly
    that and reported the repair as done. This is now the one place either path
    asks what a finding says.
    """
    row = conn.execute(
        "SELECT payload FROM event WHERE claim_id = ? AND kind = ? "
        "ORDER BY id DESC LIMIT 1", (claim_id, AMENDED_KIND)).fetchone()
    if row is None:
        return stored or ""
    try:
        return json.loads(row["payload"]).get("now") or (stored or "")
    except (ValueError, TypeError):
        return stored or ""


#: How a second finding at one coordinate is told from the first.
#:
#: A claim id is `(task, kind, file, symbol, variant)` and nothing else, and
#: `resolve_symbol` forces `symbol = ""` for every non-Python file -- so one
#: lens held at most one finding per document, and the second one a reviewer
#: filed did not become a claim. It silently rewrote the first.
#:
#: `variant` is where the rest of this kernel already puts "which finding at
#: this file and symbol": `analysis/external_write.py` separates its readback
#: and replay shapes that way, "because the fix for each is different". So the
#: lens keeps the first slot and the next distinct finding at the same
#: coordinates takes `<lens>#2`. It is hashed from the row it is stored in, so
#: the id is still recomputable from the claim, and it reads as what it is.
SLOT = "#"


def _slot(lens: str, n: int) -> str:
    return (lens or "") if n == 1 else f"{lens or ''}{SLOT}{n}"


def raise_finding(conn, cfg, *, task_id, file, symbol, note, lens=""):
    """Create a review claim.  The question comes from the template, as always.

    Returns `(id, created, siblings)` -- `siblings` being the findings already
    standing at these coordinates, which is the sentence a reviewer needs and
    never got.

    This used to resolve a note it did not recognise by overwriting the one
    that was there, and reporting that as `note amended on <id>`, exit 0. Two
    different intentions arrive at this function looking identical -- "I am
    correcting what I wrote" and "I have found a second thing here" -- and it
    cannot tell them apart, so it guessed, and it guessed the destructive one.
    Measured on this repo, 2026-08-27: one lens sweep amended ten notes across
    nine claims, six of them claims whose last attempt was exit 0 -- answered
    findings, opened 8-18 and closed 8-26, now serving somebody else's text.

    So it no longer guesses. A note it has not seen at these coordinates is a
    finding, and it gets a claim of its own in the next slot; correcting a note
    is `amend_note`, which is asked for by name. Idempotence is kept where it
    was always the point -- the same sweep finding the same thing four days
    later re-files the same sentence and gets the same claim back -- and it is
    kept against `current_note`, not `claim.note`, so a corrected finding
    re-found is still one finding.
    """
    # A finding needs somewhere to be. `resolve_symbol` refuses a `file` that
    # is not in the repo, but it returns early on an empty `symbol` and never
    # looks at the file at all -- so `file=None, symbol=None` walked past every
    # check on this path and wrote a claim with no coordinates. It happened on
    # an adopter, and `claim` is append-only: that finding is in their ledger
    # for good, closable by no test (nothing to run against) and by no text
    # closure (`--gone` is checked against a file).
    #
    # Here rather than in `resolve_symbol`, because the rule is about the
    # finding and not about the symbol: a document finding legitimately has no
    # symbol, and none of them may have no file.
    if not (file or "").strip():
        raise BadCoordinates(
            "a finding needs --file. A claim with no coordinates cannot be "
            "closed by a test (there is nothing to run it against) or by "
            "--gone/--now (there is no file to look in), and `claim` is "
            "append-only, so it stands open forever.")
    kind_cfg = cfg.kind(KIND)
    # The lens slug reaches `variant` and, through it, the claim id -- and
    # nothing on this path asked whether that lens exists. `lens_files()` was
    # consulted in `kernel/cli.py`'s `add` branch only, so the rule belonged to
    # argv rather than to the write: any other caller could file a finding
    # under a lens nobody has, and its slug becomes part of the id forever,
    # because `claim` is append-only. A finding under `securty-permission` is
    # invisible to every query for `security-permission` and identical to one
    # under a lens that was renamed.
    #
    # Refused here, where the row is written, for the reason `resolve_symbol`
    # two lines down gives for the same choice: a refusal costs one retry at
    # this command and a signature at the end of a task.
    # Only against a corpus that exists. A repo with no `.v4/lenses/` has
    # nothing to misspell against, and refusing every slug there would stop an
    # adopter filing findings at all -- caught by
    # `test_what_a_reviewer_is_handed_and_what_comes_back`, whose fixture repo
    # ships no lenses and uses `lens="l"` to exercise the sibling slots. The
    # rule is about a typo inside a corpus, so it needs one.
    usable, _unusable = lens_files(cfg.root)
    if lens and usable and lens not in usable:
        raise BadCoordinates(
            f"no lens named {lens!r}. The slug becomes part of the claim id "
            f"and `claim` is append-only, so a finding filed under a lens "
            f"nobody has cannot be found by anybody looking for that lens. "
            f"This repo has: {', '.join(sorted(usable))}.")
    symbol = resolve_symbol(cfg.root, file, symbol)
    if task_id is None:
        task_id = ensure_review_task(conn)
    note = note or ""
    siblings = []
    n = 1
    while True:
        variant = _slot(lens, n)
        cid = hashing.claim_id(task_id, KIND, file, symbol, variant)
        row = conn.execute("SELECT note FROM claim WHERE id = ?",
                           (cid,)).fetchone()
        if row is None:
            break
        if current_note(conn, cid, row["note"]) == note:
            return cid, False, siblings
        siblings.append(cid)
        n += 1

    insert(conn, "claim", id=cid, task_id=task_id, kind=KIND,
           question=cfg.question(KIND, file=file, symbol=symbol, variant=variant),
           subject_refs=[{"kind": "file", "path": file}],
           checker=kind_cfg["checker"], origin=ORIGIN,
           file=file, symbol=symbol, variant=variant or None, line=None,
           note=note, detector=None, detector_sha=None, created_at=_now())
    return cid, True, siblings


def amend_note(conn, *, claim_id, note, actor=None):
    """Correct what a finding says.  `v4 review amend`.  Returns `(was, now)`.

    An event, because `claim` is append-only and `UPDATE claim SET note` is
    refused outright -- both readings stay on the record and `current_note`
    applies the last one. The same reasoning as `withdraw_deferral`: the
    append-only rule exists so that corrections are visible, not so that
    mistakes are permanent.

    Asked for by name rather than inferred from a re-file. The inference is
    what destroyed six answered findings on 2026-08-27, and it was not
    fixable in place: two intentions reach `raise_finding` looking the same,
    and the only actor who knows which one it is is the one typing.
    """
    note = (note or "").strip()
    if not note:
        raise BadCoordinates(
            "an amendment is the sentence that replaces the wrong one. There "
            "is nothing here to replace it with.")
    row = conn.execute("SELECT note FROM claim WHERE id = ?",
                       (claim_id,)).fetchone()
    if row is None:
        raise BadCoordinates(
            f"no claim {claim_id} in this ledger, so there is no note to "
            f"amend. A finding that does not exist is raised, not corrected.")
    was = current_note(conn, claim_id, row["note"])
    if was == note:
        return was, False
    # The same signal `defer` and `risk.accept` read, for the same reason: the
    # amendments already in this ledger all say `worker`, which was the only
    # value any caller could produce, so "who decided this finding says
    # something else now" was not a question anybody could ask of the record.
    if actor is None:
        actor = ledger_mod.who_acted()
    insert(conn, "event", task_id=None, claim_id=claim_id, kind=AMENDED_KIND,
           actor=actor, payload={"was": was, "now": note, "actor": actor},
           created_at=_now())
    return was, True


def why_not_a_test_file(root, test_path: str) -> str:
    """"" if this names a file here, else a short clause saying what it is.

    Two things are called "the test" and only one of them is a path.
    `tests/x.py::test_name` is how pytest addresses a single test and how a
    person naturally writes one down; `redgreen` runs a file. Measured on the
    reference adopter: six findings were closed against values carrying `::`,
    every one of the `.py` files was on disk, and `doctor` reported all six as
    "closed by a test that is no longer here" -- six true-sounding sentences,
    none of them true.

    Said here, at the moment the value is written, rather than four hours later
    as an `ERROR` from the checker. `resolve_symbol` states the same rule for
    the other coordinate and gives the reason: refusing at this command costs
    one retry, refusing at the end of the task leaves only a signature.
    """
    if not test_path:
        return "no path at all"
    if "::" in test_path:
        head = test_path.split("::", 1)[0]
        return (f"names a test, not a file"
                + ("; that file is here" if (Path(root) / head).is_file()
                   else f"; and `{head}` is not here either"))
    if not (Path(root) / test_path).is_file():
        return "not a file in this repo"
    return ""


#: Said once, where the value is refused and where the old ones are reported.
HOW_TO_NAME_A_TEST = (
    "`redgreen` runs a file and traces which symbols it entered, so `--test` "
    "takes the path and `--command` is where a single test is selected "
    "(`python3 -m pytest {path}::the_test`)."
)


def bind_closing_test(conn, *, claim_id, test_path, command, parent_commit=None,
                      mutation=None, root=None):
    """Record which test is offered as closing this finding, and how it goes red.

    An event rather than a column: the ledger takes no updates, and this is
    chosen after the claim exists. `lifecycle.check` reads the latest one.

    `root` is optional only because the ledger is append-only and this function
    already has callers whose events cannot be rewritten; given one, the value
    is checked before it is recorded.

    `mutation` is `(file, gone, now)` and is the other way to be red -- see
    `redgreen.verify`, which owns the reasoning. It is here rather than a second
    `bind_*` function because what is being recorded is the same thing: this
    test, this command, and what makes it fail.
    """
    if root is not None:
        why = why_not_a_test_file(root, test_path)
        if why:
            raise BadCoordinates(why)
    if bool(parent_commit) == bool(mutation):
        raise BadCoordinates(
            "a closing test needs --parent (the tree before the repair) or a "
            "--mutation (the thing to break), and not both. They are two ways "
            "to make the same half red, and offering both leaves no answer to "
            "which one counts.")
    payload = {"closing_test": test_path, "test_one_file_command": command,
               "parent_commit": parent_commit or ""}
    if mutation:
        rel, gone, now = mutation
        if root is not None:
            why = why_not_a_mutation(root, rel, gone, test_path)
            if why:
                raise BadCoordinates(why)
        payload.update({"mutation_file": rel, "mutation_gone": gone,
                        "mutation_now": now or ""})
    insert(conn, "event", task_id=None, claim_id=claim_id, kind="review_close",
           actor="worker", payload=payload, created_at=_now())


def why_not_a_mutation(root, rel, gone, test_path) -> str:
    """Why this mutation cannot serve as a red half, or `""`.

    Three refusals, and each is a way the proof would be hollow:

      the test itself   Breaking the test to make the test fail says nothing
                        about the code. This is the one an author reaching for
                        the easy answer would write, so it is refused by name.

      not tracked       A mutation to a file git does not carry cannot be
                        reproduced by anybody reading the record, and the red
                        half runs in a worktree, which has only tracked files.

      too short         The same floor the text closure uses, for the same
                        reason: a marker short enough to match by accident
                        proves nothing about what was broken.
    """
    rel = str(rel or "").replace("\\", "/").strip()
    if not rel:
        return "a mutation names the file to break."
    if rel == str(test_path or "").replace("\\", "/").strip():
        return (f"the mutation breaks {rel}, which is the closing test itself. "
                f"A test made to fail by breaking that test says nothing about "
                f"the code it is offered as covering.")
    if len((gone or "").strip()) < MIN_MARKER:
        return (f"the mutation text is {len((gone or '').strip())} characters "
                f"and the floor is {MIN_MARKER}. A marker short enough to match "
                f"by accident proves nothing about what was broken.")
    out = subprocess.run(["git", "ls-files", "--error-unmatch", rel],
                         cwd=str(root), capture_output=True, text=True)
    if out.returncode != 0:
        return (f"{rel} is not tracked by git. The red half runs in a worktree, "
                f"which carries tracked files only -- and a mutation nobody "
                f"else can check out is not a record of anything.")
    return ""


#: One fact, wrong in several places.  `v4 review group`.
GROUPED_KIND = "finding_grouped"


def group(conn, cfg, *, name, claim_ids, why):
    """Record that these findings are one fact.  Returns the claims it covered.

    Measured on the sweep this exists for: 224 findings, 209 real, closed as
    **26 groups** -- 7.4 findings each, the largest 13. A repair done one
    finding at a time is the same fact repaired in twelve places, and this
    repo's doctrine is the opposite: repairing a finding means repairing the
    fact everywhere it appears.

    This records a judgement; it does not make one and does not check one. It
    cannot: measured on those 26 groups, they span 3.2 files each, only 9 live
    in a single file, and 19 of 63 files carry findings belonging to more than
    one group -- so no split by path, directory or lens reproduces them.

    What *is* checked is already checked elsewhere and is worth knowing before
    grouping anything: one test closing a group is run once per claim by
    `redgreen.verify`, and each run requires that claim's own symbol to have
    been entered. A finding swept into a group whose test never reaches it
    fails there. That is not "same root cause", but it is the floor.

    An event, like `task_forbid` and `task_worktree`: no schema change, and a
    grouping is a thing decided at a moment rather than a property of a claim.
    """
    name = (name or "").strip()
    why = (why or "").strip()
    floor = cfg.thresholds["min_chars"]
    if len(name) < 3:
        raise BadCoordinates("a group needs a name that says what the one fact "
                             "is -- it is what the next sweep reads.")
    if len(why) < floor:
        raise BadCoordinates(
            f"a reason under {floor} characters is not a reason. This is the "
            f"same bar `engage` and `risk accept` answer to: the value of a "
            f"grouping is entirely in whether the next person agrees with it.")
    ids = [c for c in dict.fromkeys(claim_ids) if c]
    if len(ids) < 2:
        raise BadCoordinates(
            "a group of one is a finding. Name two or more, or close it on its "
            "own.")
    rows = {r["id"] for r in conn.execute(
        "SELECT id FROM claim WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)}
    missing = [c for c in ids if c not in rows]
    if missing:
        raise BadCoordinates(f"no such claim: {', '.join(missing)}")
    insert(conn, "event", task_id=None, claim_id=None, kind=GROUPED_KIND,
           actor="reviewer", payload={"name": name, "claims": ids, "why": why},
           created_at=_now())
    return ids


def groups(conn):
    """[(name, [claim_id], why, when)] -- every grouping, newest last.

    A claim can appear in more than one: regrouping is how a judgement is
    revised, and an append-only ledger keeps both. Readers that want one answer
    take the last.
    """
    out = []
    for r in conn.execute(
        "SELECT payload, created_at FROM event WHERE kind = ? ORDER BY id",
        (GROUPED_KIND,)
    ):
        try:
            p = json.loads(r["payload"] or "{}")
        except (TypeError, ValueError):
            continue
        out.append((p.get("name", ""), p.get("claims") or [], p.get("why", ""),
                    r["created_at"]))
    return out


def group_of(conn):
    """{claim_id: group name} -- the latest grouping each claim is in."""
    latest = {}
    for name, claims, _why, _when in groups(conn):
        for c in claims:
            latest[c] = name
    return latest


def closing_tests_that_are_gone(conn, root):
    """[(claim_id, file::symbol, test path, why)] -- findings whose proof is not
    a file here, and which of the two reasons it is.

    A finding closes on a test that is red at the parent and green at HEAD, and
    the binding is by path. Rename the file and the binding still names the old
    one: the next run of that claim is `ERROR`, not `FAIL`, and every row that
    counts unfinished work looks for something else.

    Measured here. `tests/test_sweep_repairs.py` was renamed to
    `tests/test_repairs_that_arrived_together.py` in the same commit that closed
    195 findings -- same classes, same test names, new path -- and four findings
    went on pointing at the old one. `doctor`'s `review findings` row excludes
    anything with a passing attempt, and these had one; its `outlived findings`
    row counts exit 1, and these were exit 5. Four closed findings whose proof
    no longer existed, and three rows that all said nothing.

    The latest binding per claim, because re-binding is how this is repaired and
    an earlier dangling one is then history rather than a problem.

    Two reasons, not one. A rename and a `path::test_name` are both "not a file
    here" and only one of them is a missing test: six of these on the reference
    adopter were the second kind, and the row told the reader their proof was
    gone while every one of the files sat on disk. `why_not_a_test_file` is
    where that distinction is made, and `bind_closing_test` refuses the second
    kind outright now -- these six exist because an append-only ledger keeps
    what it was given.
    """
    from pathlib import Path as _P
    latest = {}
    for r in conn.execute(
        "SELECT e.claim_id, e.payload, c.file, c.symbol FROM event e "
        "JOIN claim c ON c.id = e.claim_id "
        "WHERE e.kind = 'review_close' ORDER BY e.id"
    ):
        try:
            test = json.loads(r["payload"] or "{}").get("closing_test")
        except (TypeError, ValueError):
            continue
        if test:
            latest[r["claim_id"]] = (f"{r['file']}::{r['symbol']}", test)
    return [(cid, where, test, why_not_a_test_file(root, test))
            for cid, (where, test) in sorted(latest.items())
            if why_not_a_test_file(root, test)]


#: A marker short enough to appear by accident proves nothing.  Long enough that
#: quoting it is quoting the change.
MIN_MARKER = 24


def bind_text_change(conn, *, claim_id, gone=None, now=None, parent_commit):
    """Close a finding whose repair has no behaviour to test.  SPEC.md 4.4.

    Measured on one eight-task run: of 71 signatures, about thirteen said some
    version of "the repair for this finding is a docstring and nothing else, so
    there is no test that can be red at the parent". They were right, and the
    only exit the design left them was the human touchpoint -- which the same
    design says must stay rare.

    So: red-green, moved from behaviour to text. The worker quotes the sentence
    that was there and is now gone (`gone`), or the one that was not there and
    now is (`now`), and the checker reads both commits. It cannot be satisfied
    without making the change, it needs no test that executes nothing, and a
    human reading the diff sees exactly what was claimed -- which is where this
    project puts its anchor anyway.

    It is deliberately not a general escape. It proves a string moved, and says
    so; a finding about behaviour still owes a test.
    """
    if not (gone or now):
        raise ValueError("a text closure needs `gone`, `now`, or both")
    for label, marker in (("gone", gone), ("now", now)):
        if marker and len(marker.strip()) < MIN_MARKER:
            raise ValueError(
                f"`{label}` is {len(marker.strip())} characters and the floor is "
                f"{MIN_MARKER}. A marker short enough to match by accident "
                f"proves nothing about the repair.")
    insert(conn, "event", task_id=None, claim_id=claim_id, kind="review_close",
           actor="worker",
           payload={"text_gone": gone or "", "text_now": now or "",
                    "parent_commit": parent_commit},
           created_at=_now())


def closing_params(conn, claim_id):
    row = conn.execute(
        "SELECT payload FROM event WHERE claim_id = ? AND kind = 'review_close' "
        "ORDER BY id DESC LIMIT 1", (claim_id,)).fetchone()
    return json.loads(row["payload"]) if row else {}


def lenses(root):
    """The reviewer lenses, one file each.  SPEC.md §10.

    Kept as data rather than prose because `lens_brief` has to index a lens --
    `LENS_KEYS` names the four fields it reads, `unusable` refuses a file
    missing any of them, and the brief ends in one `v4 review add` command
    carrying this lens's slug. None of that can be done to a 369-line document,
    which also gets read differently every time. Converting it did not change a
    single check -- the checks are the predecessor's, verbatim -- it changed
    only where they live and what shape the answer takes.

    It said the reason was that "a reviewer's output has to become a
    `V4-CLAIM:` line", and that form reaches nothing: `parse_claim_lines` runs
    on a detector's stdout and on a fixture run, nowhere else, so a reviewer
    printing one writes into its own transcript. `lens_brief` says so a hundred
    lines below, `checkers/dead_wiring.py::_claim_lines_have_a_reader` fails a
    prompt for teaching it, and `.claude/agents/reviewer.md` was deleted over
    it. The one docstring on the function named for this layer was still
    handing out the dead form as the layer's reason for existing.

    A reviewer emits claims, never verdicts. The severity column the
    predecessor carried is gone: all three levels blocked, so the field decided
    nothing for the entire life of the system.
    """
    return lens_files(root)[0]


#: What `lens_brief` indexes, and therefore what a lens has to carry. Derived
#: from use rather than declared: `name` and `source` head the brief, `checks`
#: is what a reviewer reads, `anti_patterns` is what stops them reporting the
#: wrong thing. A check may be a string or an object -- `check_text` handles
#: both, and both shapes appear inside single files. This used to say "seven of
#: the eleven are strings", which was true of a corpus of nine, went on being
#: said about a corpus of eleven, and was never true of a whole file: no lens
#: file is all-string today. `spec_coverage._reality` counts `check`,
#: `dict-shaped check` and `string-shaped check`, so the numbers belong in the
#: documents it settles rather than in a comment nothing can check.
LENS_KEYS = ("name", "source", "checks", "anti_patterns")


def unusable(lens) -> str:
    """Why this cannot be handed to a reviewer, or `""`."""
    if not isinstance(lens, dict):
        return f"not an object ({type(lens).__name__})"
    missing = [k for k in LENS_KEYS if k not in lens]
    if missing:
        return f"missing {', '.join(missing)}"
    if not isinstance(lens["checks"], list) or not lens["checks"]:
        return "`checks` is empty -- a lens that asks nothing reads as one that found nothing"
    # The same argument, one key over. This looked at `checks` and never at the
    # entries of `anti_patterns`, while `lens_brief` prints every one of them
    # verbatim -- so a blank string reached the reviewer as an empty bullet
    # under "Shapes worth flagging on sight", and 7 of the 11 shipped lenses
    # carried at least one. The half meant to stop a reviewer reporting the
    # wrong thing was the half nothing checked.
    aps = lens.get("anti_patterns")
    if aps is not None:
        if not isinstance(aps, list):
            return f"`anti_patterns` is {type(aps).__name__}, not a list"
        blank = [i for i, a in enumerate(aps)
                 if not str(a if isinstance(a, str) else a).strip()]
        if blank:
            return (f"`anti_patterns` entr{'y' if len(blank) == 1 else 'ies'} "
                    f"{blank} {'is' if len(blank) == 1 else 'are'} blank -- "
                    f"`lens_brief` prints them verbatim, so a reviewer gets a "
                    f"bullet with nothing after the dash")
    return ""


def lens_files(root):
    """`({slug: lens}, {slug: why it was skipped})`.

    One place that answers "is this a lens". It was a dict comprehension over
    `json.loads`, and `lens_brief` then indexed `name`, `source`, `checks` and
    `anti_patterns` straight out of whatever came back -- so a lens file with a
    missing key, an empty `checks`, or text that is not JSON took down
    `v4 review lens` with a traceback. Including the *list*: one bad file and
    the other eleven became unreachable, while `v4 doctor` said nothing and
    exited 0.

    Skipped and named, not crashed on. That is what `facts.validate`,
    `baseline.load` and `config._load` all do with an input file they cannot
    use, and a reviewer layer with no program behind it is the last place that
    should differ.
    """
    d = Path(root) / ".v4" / "lenses"
    if not d.is_dir():
        return {}, {}
    good, bad = {}, {}
    for p in sorted(d.glob("*.json")):
        try:
            lens = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            bad[p.stem] = f"does not read: {exc}"
            continue
        why = unusable(lens)
        if why:
            bad[p.stem] = why
        else:
            good[p.stem] = lens
    return good, bad


def check_text(check) -> str:
    """One check, as a reviewer should read it.

    A lens file carries two shapes, and they are mixed inside single files: a
    rule that came down from the checker layer carries the reason it could not
    stay there, and a rule that was always a reviewer's judgement is a
    sentence. Rendered with an f-string, the first kind reached the reviewer as
    a Python dict literal -- and the literal included `why_not_a_checker`,
    which says "no instance exists in the target repo today". Handing a
    reviewer the argument for not looking is worse than handing them nothing.

    No count here. This said "seven of the nine hold plain strings" and "130 of
    the 280 checks" about a corpus that was neither, and went on saying it
    while the corpus grew -- a number in a docstring about a directory is the
    one number nothing settles. `spec_coverage._reality` counts `check`,
    `dict-shaped check` and `string-shaped check`, so the documents that state
    them are held to them.
    """
    if isinstance(check, dict):
        return str(check.get("check") or check.get("text") or check)
    return str(check)


def lens_brief(lens, slug=None, task=None):
    """What a reviewer agent is handed."""
    lines = [f"# {lens['name']}", f"source: {lens['source']}", ""]
    # `why` sat in the file and reached nobody. `LENS_KEYS` names the four
    # fields a lens has to carry and this is not one of them, so it was never
    # missing and never printed -- the dead-wiring shape in a field rather than
    # a registry, which is why `dead-wiring` never saw it.
    #
    # Five of the twelve lenses carry one, and four of those five were written
    # before this line existed: `devx`, `electrification`, `prevention`, and
    # `general-rule-one-false-instance` -- which has no checklist at all, on
    # purpose, and put its entire method in the field nothing printed. Every
    # reviewer that ever ran that lens got a name, a source, and one check.
    #
    # Optional, not required: seven lenses have none and want none. A lens whose
    # checks say what to look for does not owe an essay.
    if lens.get("why"):
        lines.append(str(lens["why"]).strip())
        lines.append("")
    # Two modes, because there are two callers. `v4 sweep` runs the lenses
    # periodically with no task -- `ledger.REVIEW_TASK` exists for exactly that
    # -- so there is no diff and "the code as it stands" is the only thing that
    # can be meant. A lens run against a named task has one, and some questions
    # have no meaning without it: `request-fidelity` asks whether what was asked
    # for arrived, and there is nothing to ask where there is no request.
    #
    # Printing the sweep's instruction to a task-scoped reviewer put two
    # contradicting orders on one screen -- "not a diff review" above six checks
    # about the diff -- which is the collision this line was written to end,
    # arriving from the other side.
    if task:
        lines.append(f"Read the diff for task `{task}` against its base. "
                     f"For anything that fails,")
        lines.append("open one finding and nothing else:")
    else:
        lines.append("Read the code as it stands -- this is the after-gate, not a")
        lines.append("diff review. For anything that fails,")
        lines.append("open one finding and nothing else:")
    lines.append("")
    # No `--task`: a periodic sweep has none by design (`ledger.REVIEW_TASK`
    # exists for exactly that, and `lens_files` says so twelve lines up), so
    # `$V4_TASK` is unset and the shell drops the argument -- measured, the
    # printed command dies with "argument --task: expected one argument". It is
    # optional anyway; `raise_finding` falls back to `ensure_review_task`.
    #
    # `--lens` is not optional even though the parser lets it be: `raise_finding`
    # derives the claim id from it, so two lenses filing on one file+symbol
    # without it land in one lens's slots and read as that lens having found
    # both -- which is what `sweep` is counting. It no longer costs a finding
    # (the second one opens `#2` rather than rewriting the first), but the
    # attribution is still wrong, and the brief that hands out this command is
    # the place that has to carry it.
    # The slug, not `name`. `name` is the display string -- "Developer
    # experience" -- and the claim id is derived from what `--lens` is given, so
    # printing the display name hands the reviewer a value with a space in it
    # that the shell splits and `raise_finding` would key on differently from
    # every other finding in that lens.
    # `./bin/v4`, not `v4`. Nothing puts `bin/` on a PATH, so the one action
    # this brief hands out exited 127 for every reviewer that ran it as
    # printed -- `command -v v4` returns nothing in this repo. `USING.md` says
    # every command goes through `./bin/v4`; the brief that dispatches thirteen
    # agents was the place that did not.
    lines.append(f"    ./bin/v4 --repo . review add --lens {slug or lens.get('name', '<lens>')} "
                 f"--file <path> --symbol <enclosing symbol> "
                 f"--note \"<what is wrong, one sentence>\"")
    lines.append("")
    # The command that makes a review distinguishable from a no-show, and the
    # brief never named it. `record_lens_reviewed` writes the only
    # `lens_reviewed` row there is, and `v4 ship` and `v4 sweep` read that row
    # to tell "ran and found nothing" from "never ran" -- so a reviewer who
    # followed this brief exactly recorded neither.
    #
    # `--findings 0` is spelled out because zero is the answer the row exists
    # for: a lens that ran and found nothing is a fact, and it is the fact this
    # framework has no other way to hold.
    lines.append("When you are done, whatever you found, say so -- including nothing:")
    lines.append(f"    ./bin/v4 --repo . review done "
                 f"--lens {slug or lens.get('name', '<lens>')} --findings <n>")
    lines.append("")
    # Not a `V4-CLAIM:` line. Those are parsed from a detector's stdout and from
    # a fixture run, and nowhere else, so a reviewer printing one writes into its
    # own transcript. The agent prompt had the same defect and was fixed; this is
    # the function that generates the brief, and it kept the dead form.
    lines.append("You do not decide severity and you do not decide whether it ships.")
    # What actually happens to a finding, not what would be tidier. This said
    # "every finding you raise has to be answered before the task ships", and
    # it is not so: a finding hangs off `ledger.REVIEW_TASK`, `state.
    # task_report` selects `WHERE task_id = ?`, and `ENDED_TASKS_SQL` unions
    # `repo-review` into the ended set -- so it holds the one task nobody ships
    # and no other. Measured when this was found: 86 review-finding claims, 81
    # never attempted, no ship ever held by one. Telling a reviewer otherwise
    # buys nothing and costs the next reviewer's trust in the rest of this
    # brief.
    lines.append("What you file is a durable, named, chain-covered record "
                 "somebody has to answer or sign for --")
    lines.append("closed by a red-green test, or by a signature that `v4 ship` "
                 "counts out loud every time.")
    lines.append("It is not, today, a gate: a finding hangs off the standing "
                 "review task, which nobody ships.")
    lines.append("So you do not need to be right. You need to be specific -- a "
                 "note somebody can act on")
    lines.append("gets settled by an exit code; one that says something looks "
                 "off gets settled by a signature.")
    lines.append("")
    lines.append("## Checks")
    for c in lens["checks"]:
        lines.append(f"  - {check_text(c)}")
    if lens["anti_patterns"]:
        lines.append("")
        lines.append("## Shapes worth flagging on sight")
        for a in lens["anti_patterns"]:
            lines.append(f"  - {a}")
    return "\n".join(lines)


#: A lens printed its brief.  Written every time, task or no task.
LENS_RUN_KIND = "lens_run"

#: A reviewer said it had finished with one, and how many findings it reported.
LENS_REVIEWED_KIND = "lens_reviewed"


def record_lens_run(conn, *, slug, lens, task_id=None):
    """A reviewer was handed this brief.  `sweep.ran_since` reads it.

    Here rather than in `kernel/cli.py`, which is where both of this domain's
    newest events were being inserted directly. Every other review-domain write
    -- `raise_finding`, `amend_note`, `bind_closing_test`, `bind_text_change`,
    `group`, `defer`, `withdraw_deferral` -- is a function in this module, and
    an entry surface that reaches past the layer that owns the writes is
    invisible to every rule that layer enforces. It also had no caller other
    than argv: nothing else could record a lens run, including a test.

    The lens object rather than a name to look up: a brief cannot have been
    printed for a lens that did not resolve, so the argument is the proof.
    `record_lens_reviewed` has no such proof and validates its own.
    """
    # An empty `--task` is no task, and it was stored as one. The reviewer role
    # is told to pass `--task $V4_TASK`, `V4_TASK` is set by nothing in this
    # repo, so the shell expands it to `''` and this row landed with
    # `task_id = ''` -- while `sweep._lens_events` selects `task_id IS NULL`.
    # A lens that ran and reported recorded as a lens that never ran, which is
    # the one distinction this row exists to make. One reviewer in the last
    # sweep noticed and deviated from its own prompt to avoid it; the other
    # twelve did not have to, because they were told to omit the flag.
    insert(conn, "event", task_id=task_id or None, claim_id=None,
           kind=LENS_RUN_KIND, actor="reviewer",
           payload={"lens": slug, "checks": len(lens["checks"])},
           created_at=_now())


def record_lens_reviewed(conn, root, *, slug, findings, task_id=None):
    """A reviewer finished with this lens, and says what it found.

    `findings` is refused when absent rather than defaulted, and `0` is the
    whole point: it is the only way "ran and found nothing" exists as a fact,
    and `v4 ship` prints it. Refused here, where the row is written, because a
    refusal that lives in the parser branch is a rule that only argv is held
    to.
    """
    known, skipped = lens_files(root)
    if slug in skipped:
        raise BadCoordinates(
            f"lens {slug!r} is there and not usable: {skipped[slug]}")
    if slug not in known:
        raise BadCoordinates(f"no lens named {slug!r}")
    if findings is None:
        raise BadCoordinates(
            "--findings is required, and 0 is an answer -- a review that "
            "reports nothing is what this exists to distinguish from one that "
            "never ran")
    # The same normalisation as its sibling, and this is the row that matters
    # more: `sweep` reads `lens_reviewed` to answer "did anybody review", and an
    # empty `--task` filed it where that query cannot see it.
    insert(conn, "event", task_id=task_id or None, claim_id=None,
           kind=LENS_REVIEWED_KIND, actor="reviewer",
           payload={"lens": slug, "findings": findings}, created_at=_now())


# ── Deferring a finding ───────────────────────────────────────────────────
#
# `review-finding` carries this rule: 「而家唔修嘅嘢,要有一個 durable 嘅目標
# 指住。」 Nothing in this repo was that target. `accepted_risk` is per-claim
# and lapses against its own key, which is right for "this cannot be proved
# today" and wrong for "this is real and somebody will fix it later".
#
# So a rule stood for as long as it existed, requiring something the repo could
# not do -- and this repo's own doctrine names that: 「一條長期被違反而從未被
# 執行的規則,要麼執行它,要麼改掉它。兩樣都不做不是一個選項。」
#
# What this is not: the predecessor's risk register. That was built, and
# `import-risk-findings.js` is one of six scripts never invoked once across 64
# phases. A register nobody writes to is the same silence in a longer file. The
# difference here is that the deferral is refused unless it names where the
# work went, and `ship` prints the count every time.

DEFER_KIND = "finding_deferred"
DEFER_DIR = ".v4/deferred"

#: The floor a deferral's reason has to clear.
#:
#: This was bound at import to `DEFAULT_THRESHOLDS["min_chars"]` and `defer()`
#: took `conn` and `root` but never `cfg`, so a repo that raised its floor in
#: `.v4/config.json` got the code default here -- while `scope.widen`,
#: `risk.accept`, `cli.cmd_abandon`, `engagement.judge_text` and
#: `sweep.reconcile` all read the merged value. The comment above it said the
#: defect it was fixing was "a repo that raises its floor raised it everywhere
#: except here", and that sentence stayed literally true of the line under it.
def defer_min(cfg=None) -> int:
    from .config import DEFAULT_THRESHOLDS
    if cfg is None:
        return DEFAULT_THRESHOLDS["min_chars"]
    return cfg.thresholds["min_chars"]



class CannotDefer(ValueError):
    """A deferral that names no durable target."""


def defer(conn, root, *, claim_id, why, target, actor=None, cfg=None):
    """Record that a finding is real, not being fixed now, and where it went.

    `target` is the durable thing the rule asks for -- an issue, a task id, a
    file, a dated review. It is not judged, and it is not optional: a deferral
    with nowhere to point is the silence this exists to replace.
    """
    why = (why or "").strip()
    target = (target or "").strip()
    floor = defer_min(cfg)
    if len(why) < floor:
        raise CannotDefer(
            f"{len(why)} characters, and the floor is {floor}. Deferring a "
            f"finding is a decision somebody has to be able to argue with.")
    if not target:
        raise CannotDefer(
            "a deferral names where the work went -- an issue, a task id, a "
            "file, a dated review. Without one this is the finding going quiet, "
            "which is what the rule it answers exists to stop.")

    # `actor` defaulted to "human" and no caller could say otherwise -- the CLI
    # passes none and the parser has no flag -- so an agent deferring its own
    # finding left a row asserting a person made that call. This project spends
    # real effort on that distinction everywhere else: `risk.accept` writes
    # `signed_by = "person" if is_tty else "agent"` with a note saying why, and
    # `engagement` validates `BEFORE_ACTORS` because "recorded as `worker` for
    # everyone, the difference is not askable". Same signal as `risk`: a tty
    # means somebody was there.
    if actor is None:
        actor = ledger_mod.who_acted()

    # In git, like a signature record, so it survives a clone and shows up in a
    # diff. The ledger lives in .git/ and a clone has none.
    #
    # Written *before* the row, and that order is the whole point. These two
    # writes have no compensating action between them: the ledger is
    # append-only and takes no deletes, so a row is permanent the moment it
    # lands, while this file can be written again with the same content. The
    # other order put the permanent half first, so an `OSError` here left a row
    # asserting a deferral whose record does not exist -- which is exactly the
    # state `ledger.reconcile_deferrals` was written to report, and no way to
    # answer it. `risk.accept`, which the note above says this is modelled on,
    # already writes its record at :376 and inserts at :406.
    p = Path(root) / DEFER_DIR / f"{claim_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {"claim": claim_id, "why": why, "target": target,
         "at": datetime.now(timezone.utc).isoformat()},
        indent=2, ensure_ascii=False) + "\n")

    from .ledger import insert
    insert(conn, "event", task_id=None, claim_id=claim_id, kind=DEFER_KIND,
           actor=actor, payload={"why": why, "target": target, "actor": actor},
           created_at=datetime.now(timezone.utc).isoformat())
    return p


#: A deferral that was written about nothing, cancelled by an append.
WITHDRAWN_KIND = "finding_deferral_withdrawn"


def withdraw_deferral(conn, root, *, claim_id, why, actor=None, cfg=None):
    """Cancel a deferral, and say why.  The correction is itself an append.

    The ledger takes no deletes, so a deferral written by mistake -- a typo'd
    claim id, or a test that wrote into the live ledger before it learned to
    use a throwaway one -- warns forever with no way to answer it. This repo
    holds exactly one: `c1`, 2026-08-09, written while `defer` was being built,
    naming a claim that has never existed. `v4 doctor` reported it on every run
    and the only thing a reader could do was recognise it again.

    `request_cover.withdraw` is the precedent and the reasoning is the same:
    "the append-only rule exists so that corrections are visible, not so that
    mistakes are permanent". Both rows stay and both are readable; only the
    accounting moves.

    Refused for a deferral that is doing its job. This cancels a record, not a
    decision -- a finding somebody genuinely put off is un-deferred by closing
    it, not by deleting the note that says they put it off.
    """
    why = (why or "").strip()
    floor = defer_min(cfg)
    if len(why) < floor:
        raise CannotDefer(
            f"{len(why)} characters, and the floor is {floor}. Cancelling a "
            f"record of a decision is a decision.")
    # `_standing_deferrals`, not `deferred`: a closed finding drops out of
    # the latter, and asking it here would refuse with "there is nothing to
    # cancel" about a record that is plainly committed. The refusal this case
    # deserves is the one below it, which says the claim is real.
    if not any(cid == claim_id for cid, _t in _standing_deferrals(conn)):
        raise CannotDefer(
            f"no deferral names claim {claim_id}. There is nothing to cancel.")
    if conn.execute("SELECT 1 FROM claim WHERE id = ?", (claim_id,)).fetchone():
        raise CannotDefer(
            f"claim {claim_id} exists, so its deferral is a decision about a "
            f"real finding. Close the finding instead -- a record is cancelled "
            f"when it was about nothing, not when somebody changed their mind.")
    if actor is None:
        actor = ledger_mod.who_acted()

    # Same order as `defer`, for the same reason. The file is git-tracked and
    # this removal is retryable -- the guard above reads the ledger, not the
    # disk, so a second run finds the deferral still recorded and `is_file()`
    # makes the unlink a no-op. The row is not retryable: it is permanent the
    # moment it lands, and putting it first meant a failed `unlink` left a row
    # saying the deferral was withdrawn beside the record saying it stands.
    p = Path(root) / DEFER_DIR / f"{claim_id}.json"
    if p.is_file():
        p.unlink()

    from .ledger import insert
    insert(conn, "event", task_id=None, claim_id=claim_id, kind=WITHDRAWN_KIND,
           actor=actor, payload={"why": why, "actor": actor},
           created_at=datetime.now(timezone.utc).isoformat())
    return p


def withdrawn_deferrals(conn) -> set:
    """Claim ids whose deferral has been cancelled.

    Named, and then not called: `deferred` seven lines below carried the same
    `WITHDRAWN_KIND` query inline, so the tree held two implementations of one
    sentence and only one of them ran. Nothing was wrong with either -- which
    is the whole risk. A second copy nobody calls does not drift on the day it
    is written; it drifts on the day somebody changes the one that runs.
    """
    return {r["claim_id"] for r in conn.execute(
        "SELECT claim_id FROM event WHERE kind = ?", (WITHDRAWN_KIND,))}


def settled_findings(conn) -> set:
    """Claim ids whose finding has been closed, signed for, or retracted.

    Not `claim_state`: that answers "is this question open now", and staleness
    re-opens it whenever the tree moves. The question here is a different one
    and has one answer forever -- was this finding ever closed. A repair that
    landed and then had its file edited is still a repair that landed, and
    nobody is putting it off any more.

    The same three endings `UNSETTLED_FAILS_SQL` recognises, read the same way,
    because a finding that stops `v4 ship` complaining in one row should stop it
    complaining in the other.
    """
    return {r[0] for r in conn.execute("""
        SELECT DISTINCT c.id FROM claim c
        WHERE c.kind = ?
          AND (EXISTS (SELECT 1 FROM attempt a
                       WHERE a.claim_id = c.id AND a.exit_code = 0)
            OR EXISTS (SELECT 1 FROM accepted_risk r WHERE r.claim_id = c.id)
            OR EXISTS (SELECT 1 FROM event e WHERE e.claim_id = c.id
                                               AND e.kind = 'retracted'))
    """, (KIND,))}


def _standing_deferrals(conn):
    """[(claim_id, target)] -- every deferral record that has not been cancelled.

    The record, not the accounting. `withdraw_deferral` asks this whether there
    is a record to cancel; `deferred` below asks it what is still outstanding
    and then takes the closed ones out. Splitting the two is what lets a closed
    finding stop being counted without `withdraw_deferral` losing its ability to
    say which refusal applies.
    """
    rows = conn.execute(
        "SELECT claim_id, payload FROM event WHERE kind = ? ORDER BY id",
        (DEFER_KIND,)).fetchall()
    gone = withdrawn_deferrals(conn)
    out = []
    for r in rows:
        if r["claim_id"] in gone:
            continue
        try:
            out.append((r["claim_id"], json.loads(r["payload"])["target"]))
        except (ValueError, TypeError, KeyError):
            continue
    return out


def deferred(conn):
    """[(claim_id, target)] for every finding somebody put off and has not done.

    A cancelled one is not one: `withdraw_deferral` appends the correction and
    `_standing_deferrals` is where that takes effect, so `v4 ship`'s count and
    `doctor`'s reconciliation both read the same set.

    **A closed one is not one either**, and that half was missing.
    `withdraw_deferral`'s own docstring says a finding somebody genuinely put
    off "is un-deferred by closing it" -- and no code implemented the sentence,
    so `v4 ship` printed a repaired finding as outstanding on every ship after
    the repair, and its committed record went on naming work that was finished.
    The count is the whole mechanism here (this is printed, never gated), and a
    count that only ever grows is one people stop reading.
    """
    settled = settled_findings(conn)
    return [(cid, target) for cid, target in _standing_deferrals(conn)
            if cid not in settled]


def reconcile_deferrals(conn, root: Path):
    """The same two directions for `.v4/deferred/`, and a weaker verdict.

    `review.defer` writes a `finding_deferred` event and a git-tracked
    `.v4/deferred/<claim>.json`, and says in its own comment that the file is
    there "like a signature record, so it survives a clone and shows up in a
    diff". The thing it is like was reconciled and this was not, so a
    hand-written or deleted deferral was invisible to `v4 audit`, to staleness
    and to `scope` -- `.v4/deferred/` is in KERNEL_WRITTEN, which is what makes
    the third one true.

    A deferral is weaker than a signature, and the difference decides where the
    answer goes. A forged signature changes a verdict -- `RISK_ACCEPTED` is
    terminal -- so `audit_chain` reports it and `ship` is held. A deferral makes
    nothing terminal: losing the file loses the record of a decision, not the
    decision's effect. So this is reported by `v4 doctor` and holds nothing.
    Putting it in the chain would say a repo with a stale deferral file cannot
    ship, which is not true and is the kind of overreach that gets a gate
    routed around.
    """
    problems = []
    try:
        # A deferral somebody cancelled is not one. `review.withdraw_deferral`
        # appends the correction rather than deleting the row, which is the
        # same instrument `request_cover.withdraw` uses and for the same
        # reason: the append-only rule exists so corrections are visible, not
        # so mistakes are permanent.
        # `withdrawn_deferrals`, whose own docstring is about this exact
        # hazard: it was written, named, and then not called, and a second copy
        # of one sentence "drifts on the day somebody changes the one that
        # runs". This was that second copy -- the third reader of the deferral
        # rows, and the one that would have kept the old answer.
        gone = withdrawn_deferrals(conn)
        rows = {}
        when = {}
        for r in conn.execute(
                "SELECT claim_id, payload, created_at FROM event "
                "WHERE kind = ? ORDER BY id", (DEFER_KIND,)):
            if r["claim_id"] in gone:
                continue
            try:
                rows[r["claim_id"]] = json.loads(r["payload"] or "{}")
            except (ValueError, TypeError):
                rows[r["claim_id"]] = {}
            when[r["claim_id"]] = (r["created_at"] or "")[:10]
    except sqlite3.Error as exc:
        # Not `return problems`, which at this point is empty and is exactly
        # what a clean reconciliation returns. `v4 doctor` prints "every
        # deferral has a row and a committed record" from it, so a database
        # this could not read said the thing it exists to check.
        problems.append(f"the deferral rows could not be read "
                        f"({type(exc).__name__}: {exc}), so whether every "
                        f"deferral has both halves is unknown -- which is not "
                        f"the same as yes")
        return problems

    d = Path(root) / DEFER_DIR
    on_disk = {p.stem: p for p in d.glob("*.json")} if d.is_dir() else {}

    for cid, payload in sorted(rows.items()):
        path = on_disk.get(cid)
        if path is None:
            # An event naming a claim that does not exist is a different fault
            # from a missing file, and this repo has one: `c1`, 2026-08-09,
            # written while `defer` was being built, four days before the file
            # half existed. Saying "the file is missing" about it would send
            # somebody looking for a file that was never meant to be written.
            known = conn.execute("SELECT 1 FROM claim WHERE id = ?",
                                 (cid,)).fetchone()
            if not known:
                # With the date, because the ledger is append-only and this
                # one cannot be repaired: a reader has to be able to tell a
                # row written while `defer` was being built from one written
                # today, and the row is the only place that says which.
                problems.append(
                    f"a deferral names claim {cid}, and no such claim is in the "
                    f"ledger. The event is about nothing. Written "
                    f"{when.get(cid) or 'at an unrecorded time'}; if that is "
                    f"old, it is history an append-only ledger cannot drop.")
            else:
                problems.append(
                    f"claim {cid} was deferred and .v4/deferred/{cid}.json is "
                    f"not on disk. A clone carries the file and not the ledger, "
                    f"so the decision reaches nobody.")
            continue
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{DEFER_DIR}/{path.name} does not read: {exc}")
            continue
        for field in ("why", "target"):
            if field in rec and rec[field] != payload.get(field):
                problems.append(
                    f"{DEFER_DIR}/{path.name} and the ledger disagree about "
                    f"{field!r}. One of them was edited after the other.")
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", f"{DEFER_DIR}/{path.name}"],
            cwd=root, capture_output=True, text=True)
        if tracked.returncode != 0:
            problems.append(
                f"{DEFER_DIR}/{path.name} is not tracked by git, so the "
                f"deferral does not survive a clone. Commit it.")

    for cid, path in sorted(on_disk.items()):
        if cid not in rows:
            problems.append(
                f"{DEFER_DIR}/{path.name} records a deferral the ledger never "
                f"made. Either the event was removed, or the file was written "
                f"by something other than `v4 review defer`.")
    return problems
