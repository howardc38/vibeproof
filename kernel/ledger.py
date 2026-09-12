"""Append-only ledger.  SPEC.md §1.

Three rules this module enforces structurally rather than by convention:

  1. Nothing is UPDATEd or DELETEd.  Triggers raise on both.
  2. There is no `status` column anywhere.  Whether a claim is answered is a
     query over attempts plus a fresh hash -- never a field somebody can write.
  3. Every attempt carries prev_hash/row_hash so tampering is detectable after
     the fact.

That third rule is worth being precise about.  It does NOT make the ledger
unwritable: a worker has a shell and this is a plain SQLite file.  What it buys
is that `v4 audit` can tell you a row was forged.  SPEC.md §1 states the
same limit in the same words -- if you find yourself writing "structurally
impossible" about this file, you are repeating the mistake rev 1 made.
"""

import contextlib
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS task (
  id          TEXT PRIMARY KEY,
  request     TEXT NOT NULL,
  scope_globs TEXT NOT NULL,          -- JSON array
  base_commit TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim (
  id           TEXT PRIMARY KEY,      -- sha256(task|kind|file|symbol|variant) §4.1
  task_id      TEXT NOT NULL REFERENCES task(id),
  kind         TEXT NOT NULL,
  question     TEXT NOT NULL,         -- generated from template, never authored
  subject_refs TEXT NOT NULL,         -- JSON [{kind:file,path}|{kind:attempt,claim}]
  checker      TEXT NOT NULL,
  origin       TEXT NOT NULL,         -- derive | review | widen
  file         TEXT,
  symbol       TEXT,
  variant      TEXT,
  line         INTEGER,               -- display only, NOT part of identity
  note         TEXT,                  -- free text, never selects a checker
  detector     TEXT,
  detector_sha TEXT,
  created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attempt (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  claim_id       TEXT NOT NULL REFERENCES claim(id),
  subject_digest TEXT NOT NULL,       -- JSON {ref_key: digest}
  checker_sha    TEXT NOT NULL,
  config_sha     TEXT NOT NULL,
  head_commit    TEXT NOT NULL,
  worktree       TEXT NOT NULL,
  argv           TEXT NOT NULL,       -- JSON array, the exact command
  exit_code      INTEGER NOT NULL,    -- from the OS, never from a claim
  stdout         TEXT NOT NULL,
  stderr         TEXT NOT NULL,
  started_at     TEXT NOT NULL,
  ended_at       TEXT NOT NULL,
  duration_ms    INTEGER NOT NULL,
  claim_digest   TEXT NOT NULL DEFAULT '',
  facts_sha      TEXT NOT NULL DEFAULT '',   -- '' when this kind does not read the table
  scheme         TEXT NOT NULL DEFAULT 'v4-chain-1',
  prev_hash      TEXT NOT NULL,
  row_hash       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id    TEXT,
  claim_id   TEXT,
  kind       TEXT NOT NULL,           -- one of EVENT_KINDS; `insert` refuses any other
  actor      TEXT NOT NULL,
  payload    TEXT NOT NULL,           -- JSON
  created_at TEXT NOT NULL,
  -- Chained, for the same reason attempts are. The walk read only `attempt`,
  -- and event rows are what decide permission: `kind = 'scope_widen'` is the
  -- sole input to `current_scope` in both `kernel/scope.py` and
  -- `hooks/write_block.py`, and `kind = 'hook_seen'` is what `ship` reports the
  -- guard by. `reconcile_signatures` exists because RISK_ACCEPTED is reached by
  -- inserting one row rather than by an attempt, so `audit_chain` never saw it;
  -- the identical argument applies to a widen, which grants write access to a
  -- protected path, is never revoked, and has no committed artefact to
  -- reconcile against the way `.v4/risks/` gives signatures one.
  prev_hash  TEXT NOT NULL DEFAULT '',
  row_hash   TEXT NOT NULL DEFAULT '',
  scheme     TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS accepted_risk (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  claim_id       TEXT NOT NULL REFERENCES claim(id),
  kind           TEXT NOT NULL,       -- unprovable|no_checker|baseline_raise|scope_widen_protected
  who            TEXT NOT NULL,
  why            TEXT NOT NULL,
  was_tty        INTEGER NOT NULL,    -- recorded, because isatty is friction not proof
  -- Which of the three routes signed this.  `was_tty` answers "was there a
  -- terminal", which stopped being the same question the day a third route
  -- existed: `--as-monitor` waives the terminal check, so a monitor signature
  -- and a `--no-tty-check` worker signature are both `was_tty = 0` and the
  -- reports that count signatures could not tell them apart.  The record file
  -- has carried `signed_by` since that route was added; this is the same fact
  -- in the table the reports read.  '' means the row was written before the
  -- column existed, and no backfill is possible: the ledger is append-only and
  -- UPDATE is refused by a trigger.
  signed_by      TEXT NOT NULL DEFAULT '',
  git_record     TEXT NOT NULL,
  subject_digest TEXT NOT NULL,       -- a signature expires when the bytes move
  created_at     TEXT NOT NULL,
  scope          TEXT NOT NULL DEFAULT 'task',  -- task|repo
  cover_key      TEXT NOT NULL DEFAULT ''       -- scope=repo: what this covers
);

CREATE TABLE IF NOT EXISTS cost_observation (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id    TEXT,
  claim_id   TEXT,
  source     TEXT NOT NULL,           -- self_reported | transcript_derived | observed
  tokens     INTEGER,
  wall_ms    INTEGER,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_claim_task    ON claim(task_id);
CREATE INDEX IF NOT EXISTS ix_attempt_claim ON attempt(claim_id, id);
CREATE INDEX IF NOT EXISTS ix_event_task    ON event(task_id, id);
"""

_APPEND_ONLY = [
    "task", "claim", "attempt", "event", "accepted_risk", "cost_observation",
]


#: Every `event.kind` this kernel writes, and what a reader who finds one in the
#: ledger is looking at.
#:
#: The column comment used to be the only enumeration of this vocabulary and it
#: named six -- `scope_widen|engagement|detector_run|blocked|remerge|register`.
#: Measured at the commit this table was written: the code writes 32 and the
#: ledger holds 27 of them, so 26 of the names a reader meets in a query had
#: nowhere to be looked up. `runner.record` repaired the identical omission for
#: `cost_observation.source` and stated the rule it was following: "the comment
#: is where the set is stated".
#:
#: A comment cannot hold that -- this one drifted by 26 names -- so the set is a
#: value and `insert` refuses an event whose kind is not in it. Adding a kind is
#: therefore adding a line here, in the same diff, with the sentence a reader
#: needs. Five of these have never been written in this repo's own ledger
#: (`engagement_before`, `facts_narrowed_detection`, `remerge`,
#: `request_cover_withdrawn`, `task_continues`); they are what the code writes,
#: which is what this vocabulary is about, and a kind nobody has reached yet is
#: not the same fact as a kind nothing can write.
EVENT_KINDS = {
    "maintenance_run": "a versioned maintenance run, its expected review coverage and completion",
    "maintenance_handoff": "correlated role assignment, decision and repair evidence; not a claim verdict",
    "review_observed": "a maintenance review's reference to a concrete ledger finding and its lens/check",
    "abandoned": "a task ended without shipping: why, and the FAILs it never settled",
    "blocked": "ship or remerge ran out of bounded rounds and stopped asking",
    "checker_out": "the structured `--out` a checker wrote for one claim",
    "claim_reraised": "a claim that had been retracted, raised again by a detector",
    "claim_not_run": "a claim the kernel decided not to put to its checker, and what held it back",
    "detector_run": "one detector over one task: whether it ran, what it claimed, what was dropped",
    "engagement": "the sentence a claim's kind asked for, and the verdict on it",
    "engagement_before": "the same sentence written against a kind before any claim of it exists",
    "facts_narrowed_detection": "a detector that saw less because a facts glob matched nothing",
    "finding_deferral_withdrawn": "a deferral cancelled, because it was written about nothing",
    "finding_deferred": "a finding not being fixed now, with the durable target it went to",
    "finding_grouped": "several findings recorded as one fact, with the sentence naming it",
    "finding_note_amended": "the sentence a finding says, corrected -- both readings stay",
    "hook_seen": "one write a hook judged: the path, whether it was allowed, and on what basis",
    "lens_reviewed": "a reviewer finished with a lens and says how many findings it reported",
    "lens_run": "a lens brief was printed for a task, with how many checks it carries",
    "lens_sweep": "the periodic after-gate ran: which lenses, and what it claimed to find",
    "register": "a checker passed its fixtures and entered the registry",
    "register_detector": "the same, for a detector",
    "remerge": "one re-check round after a merge moved the tree under open answers",
    "request_cover": "one quoted piece of a task's request, accounted for",
    "request_cover_withdrawn": "that accounting withdrawn, with why",
    "retracted": "a claim the detector that raised it no longer raises",
    "review_close": "what is offered as closing a finding -- a test, or a text change",
    "round_closed": "a measurement round closed",
    "round_opened": "a measurement round opened, freezing the acceptance criteria",
    "scope_narrow": "a glob dropped from a task's scope",
    "scope_widen": "a path added to a task's scope, with why -- the sole input to `current_scope`",
    "ship_round": "one re-derive round inside `v4 ship`",
    "shipped": "every claim terminal, and the task recorded as delivered",
    "task_continues": "which earlier task this one continues",
    "task_forbid": "paths this task may not touch, declared when it was opened",
    "host_binding": "explicit host session and agent to task/worktree binding",
    "task_worktree": "the working tree a task was opened in",
}


#: Who did it, for the `actor` column beside `kind`.
#:
#: The sibling column has had `EVENT_KINDS` and a gate since early on, and the
#: comment over that gate says this is "where the vocabulary is either kept or
#: lost". `actor` had neither, and it had already drifted: `review.defer`,
#: `review.amend_note` and `review.withdraw_deferral` wrote `human` from
#: `sys.stdin.isatty()`, `scope` wrote `person` from the identical test, and
#: `risk` declared `PERSON, AGENT, MONITOR` as constants because "five texts
#: and two reports spell these". One column, two words for one fact, so a
#: query for `person` got scope widens and missed every deferral.
#:
#: `person`, not `human`, because that is the spelling that was already a named
#: constant with a reason written beside it, and `risk.attribution` reads it.
#:
#: Measured in this repo's ledger the day the gate landed: `hook` 35,279,
#: `kernel` 25,420, `worker` 1,396, `reviewer` 214, `agent` 211, `human` 102,
#: `person` 0. Those 102 rows keep saying `human` -- the ledger takes no
#: updates, and rewriting history to match a vocabulary would be the worse of
#: the two problems. It is not in this table because nothing may write it
#: again; a reader of rows older than the gate should read it as `person`.
ACTORS = {
    "agent": "a program, with no person at the terminal when it acted",
    "hook": "a hook, recording a write it judged",
    "kernel": "the framework itself, not anyone using it",
    "monitor": "a monitor session, which watches and does not do the work",
    "orchestrator": "the session that splits and merges, not one that answers",
    "person": ("somebody was at a terminal: a tty, which is the only evidence "
               "there is, and which a pty fakes -- an agent that needs to act "
               "says so with --no-tty-check instead"),
    "reviewer": "a lens read, raising findings rather than answering claims",
    "splitter": "the step that turns a request into a task's scope",
    "worker": "the session answering a task's claims",
}


#: The three `risk` has always named, re-exported so its `signed_by` and this
#: table's `actor` cannot drift apart -- which is the whole finding.
PERSON, AGENT, MONITOR = "person", "agent", "monitor"


def who_acted() -> str:
    """`PERSON` or `AGENT`, from the one piece of evidence there is.

    Five sites wrote this ternary out -- three in `review`, two in `scope` --
    and the two files disagreed on the word. The test itself is not the fragile
    part; having it in five places, each free to spell its answer differently,
    is. A tty is the only evidence available: nothing else distinguishes a
    person at a keyboard from a program with the same credentials, and
    `risk.accept` says so where it makes the same call for `signed_by`. The
    honest route for an agent that needs to act is `--no-tty-check`, which
    records `agent`; a pty is not, because it makes `isatty` true and the row
    then asserts a person who was never there.

    `lifecycle.open_task` did not even ask. It wrote `human` for all three of
    its rows, so every task an agent opened -- six of them the day this landed
    -- carried a row asserting a person did it. Asking is the repair; a
    vocabulary that says `person` means "a tty was there" and a writer that
    says it unconditionally cannot both be right.
    """
    import sys as _sys
    return PERSON if _sys.stdin.isatty() else AGENT


class UndeclaredActor(ValueError):
    """An actor with no entry in `ACTORS`.

    Refused for the reason `UndeclaredEventKind` is: the row is permanent, and
    a second word for one fact is invisible until the day somebody queries by
    the other one.
    """


class UndeclaredEventKind(ValueError):
    """An event kind with no entry in `EVENT_KINDS`.

    Refused rather than warned about: the row would be permanent, and a name a
    reader cannot look up is what this vocabulary exists to stop being writable.
    """

#: The write gate.  UPDATE and DELETE are refused outright; INSERT is refused
#: unless the connection doing it registered `v4_kernel_can_write`, and the only
#: place that happens is `connect()` in this module.
#:
#: The design said for a long time that out-of-band writes are detectable and
#: not preventable, on the grounds that no file permission, uid or socket
#: boundary can stop a process that can already open the file. That is true of a
#: boundary and it was the wrong conclusion, because the attack it named --
#: `sqlite3 ledger.db "INSERT INTO attempt ..."` -- is refused by this, for
#: twenty lines and no daemon:
#:
#:     kernel, inside writing()      inserts
#:     kernel, outside writing()     ABORT: attempts go through the kernel
#:     external python sqlite3       no such function: v4_kernel_can_write
#:     external sqlite3 CLI          no such function: v4_kernel_can_write
#:     after DROP TRIGGER            inserts
#:
#: So it is not a boundary: the last row is real and one command away. What it
#: does is move the cost from "one shell command" to "drop the trigger first",
#: and a dropped trigger is itself visible -- `connect()` re-creates it, so the
#: forged row sits in a database whose schema says it could not have been
#: written. Detectable-not-preventable was the honest sentence for a boundary.
#: It was not the honest sentence for this, and the predecessor had already
#: built it.
_TRIGGERS = "".join(
    f"""
CREATE TRIGGER IF NOT EXISTS no_update_{t} BEFORE UPDATE ON {t}
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: {t}'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_{t} BEFORE DELETE ON {t}
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: {t}'); END;
CREATE TRIGGER IF NOT EXISTS gate_insert_{t} BEFORE INSERT ON {t}
WHEN v4_kernel_can_write() != 1
BEGIN SELECT RAISE(ABORT, 'inserts into {t} go through the kernel'); END;
"""
    for t in _APPEND_ONLY
)

#: Per-connection, re-entrant.  A process-global flag would be wrong: the
#: permission belongs to the connection performing the insert, and several
#: worktrees share this file.
_WRITE_DEPTH: dict[int, int] = {}


@contextlib.contextmanager
def writing(conn):
    """Open the write gate for one write.  Nested writes keep it open.

    **Not a lock.** It is an in-process depth counter keyed on `id(conn)`, and
    the only thing it controls is `v4_kernel_can_write`, the SQL function the
    append-only triggers call -- so it says "this write came from the kernel",
    not "no one else is writing". Two processes each hold their own counter and
    neither can see the other.

    Saying so here because the name reads like one and a caller believed it:
    the event chain read its tail and inserted inside this block and nothing
    else, which is a read-then-write with no lock between the halves.
    `append_attempt` takes `BEGIN IMMEDIATE` for that, and so does the event
    branch of `insert` now.
    """
    key = id(conn)
    _WRITE_DEPTH[key] = _WRITE_DEPTH.get(key, 0) + 1
    try:
        yield
    finally:
        _WRITE_DEPTH[key] -= 1
        if _WRITE_DEPTH[key] <= 0:
            _WRITE_DEPTH.pop(key, None)

GENESIS = "0" * 64


def ledger_path(repo_root: Path) -> Path:
    """One ledger per repo, shared by every worktree.  SPEC.md §1."""
    common = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout.strip()
    p = Path(common)
    if not p.is_absolute():
        p = (Path(repo_root) / p).resolve()
    return p / "v4" / "ledger.db"


def connect_readonly(repo_root: Path) -> sqlite3.Connection:
    """The ledger, opened so it cannot be written.

    `connect` is the only door this module had, and it creates the file, runs
    the schema, installs the append-only triggers, commits, and migrates
    columns. A checker calling it -- `checkers/request_coverage.py` does, to
    read one `task.request` -- performs DDL on the ledger from the layer that
    must not write it, and holds the per-connection write gate while it does.

    `mode=ro` makes the question and the door match. It also fails loudly rather
    than helpfully if the database is not there, which is right for a reader:
    an empty ledger and a missing one are different answers.

    `hooks/write_block.py` opens the same way and cannot import this module, so
    the two spellings exist on purpose; this one is for everything that can.
    """
    path = ledger_path(Path(repo_root))
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def connect(repo_root: Path) -> sqlite3.Connection:
    path = ledger_path(Path(repo_root))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")     # N workers, one short row each
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    # deterministic=False on purpose: SQLite may cache the result of a
    # deterministic function, and this one's answer changes within a connection.
    conn.create_function("v4_kernel_can_write", 0,
                         lambda: 1 if _WRITE_DEPTH.get(id(conn), 0) > 0 else 0)
    conn.executescript(SCHEMA)
    conn.executescript(_TRIGGERS)
    conn.commit()
    _migrate(conn)
    return conn


def _enc(cols: dict) -> dict:
    return {
        k: json.dumps(v, sort_keys=True) if isinstance(v, (list, dict)) else v
        for k, v in cols.items()
    }


def git_identity(repo_root) -> str:
    """Who is running this, as this repo's git config says.

    Here rather than in `risk`, where it was, because all three callers use it
    for the same thing: filling the `who` of a row they are about to `insert`.
    `risk.accept` needed it first and it stayed there, and the cost was an
    import edge from `scope` into `risk` that put four modules on a cycle --
    `config -> scope -> risk -> review -> config` -- which `lint` reported the
    first time anything touched `review.py`.

    Nothing is imported for this that `insert`'s own module did not already
    need, so no caller gains a dependency by asking here.
    """
    r = subprocess.run(["git", "config", "user.email"], cwd=repo_root,
                       capture_output=True, text=True)
    return r.stdout.strip() or "unknown"


def _root_of(conn):
    """The work tree this connection's ledger belongs to, or `None`.

    `insert` is handed a connection and nothing else, and the redaction below
    needs a root: without one `redact` falls back to the twelve shipped
    patterns and every credential family an adopter declared in
    `.v4/secret_patterns.json` goes in clear. The path is derivable --
    `ledger_path` builds `<git-common-dir>/v4/ledger.db` -- so this walks it
    back rather than making every caller carry it, because a caller that
    forgets is the same silence one level out.

    `None` when the shape is not the one `ledger_path` builds (an in-memory
    database, a test fixture), and `redact` degrades to the shipped patterns
    there rather than raising.
    """
    # Not guarded. `PRAGMA database_list` on the connection this row is about to
    # be written through fails only when the connection itself does, and an
    # `except` here would hand back `None` -- which means "use the shipped
    # patterns", a narrower redaction, decided by a failure nobody was told
    # about. If the connection cannot answer, the insert that follows cannot
    # happen either, and the caller should see that rather than a quietly
    # thinner filter. `fail_closed` names this shape and is right about it.
    rows = list(conn.execute("PRAGMA database_list"))
    for r in rows:
        name = r[2] if not isinstance(r, sqlite3.Row) else r["file"]
        if not name:
            continue
        p = Path(name)
        if p.parent.name == "v4" and p.parent.parent.name == ".git":
            return p.parent.parent.parent
    return None


#: What the exporter leaves where a value was. One spelling, because
#: `verify_exported` has to recognise it in a row it cannot otherwise explain,
#: and a second copy of the string is how that recognition goes quietly wrong.
_REDACTED_MARK = "[redacted]"


#: Free text a row carries out of the repo, by table.
#:
#: Named here because `insert` is where it has to be applied. It was applied at
#: export instead, and the comment there gave the reason -- "a row hashed over
#: redacted text would not verify against the database it came from" -- which
#: has it exactly backwards: redacting *after* the hash is what makes the export
#: fail to verify, because `verify_exported` re-derives over the bytes it can
#: see. Reported independently by two adopters on the same day; reproduced here,
#: `v4 audit --events` exiting 1 with four rows accused of being edited by
#: somebody, all four altered by this framework's own exporter.
#:
#: Redacting at `insert` also means the value never reaches `.git/v4/ledger.db`.
#: That file is untracked and so does not leave by git, but it is copied,
#: bundled and printed like any other file, and "the location is not evidence,
#: only the value is" is this repo's own rule about exactly that.
_REDACTED_ON_WRITE = {
    "event": ("payload",),
    "accepted_risk": ("why",),
    "claim": ("note", "question"),
    "task": ("request",),
}


def _redact_free_text(conn, table: str, cols: dict) -> dict:
    """The redaction, before the row is hashed rather than after."""
    fields = _REDACTED_ON_WRITE.get(table)
    if not fields:
        return cols
    root = _root_of(conn)
    out = dict(cols)
    for f in fields:
        v = out.get(f)
        if isinstance(v, str):
            out[f] = _redact(v, root)
        elif isinstance(v, (dict, list)):
            # `payload` arrives as an object and `_enc` serialises it, so the
            # string form is what a reader sees and what the hash covers.
            out[f] = json.loads(_redact(json.dumps(v, ensure_ascii=False), root))
    return out


def insert(conn, table: str, if_absent: bool = False, **cols) -> int:
    """`if_absent` turns "check, then create" into one statement.

    Two worktrees sharing this ledger both read "not there", both insert, and
    the second one dies on a PRIMARY KEY it could not have known about -- while
    the documentation tells people to run in parallel. Retrying or locking would
    both work and both leave the collision possible; `ON CONFLICT DO NOTHING`
    removes it. The return value is not a row id when the row was already there,
    so `if_absent` callers must not use it.
    """
    if table == "attempt":
        raise RuntimeError("attempts go through append_attempt() so the chain is kept")
    # Before the write gate opens, because the row would be permanent. Every
    # event write in this repo comes through here -- the kernel's own modules and
    # `hooks/_framework.py` alike -- so this is where the vocabulary is either
    # kept or lost.
    if table == "event" and cols.get("kind") not in EVENT_KINDS:
        raise UndeclaredEventKind(
            f"event kind {cols.get('kind')!r} is not in EVENT_KINDS "
            f"(kernel/ledger.py). A kind is what a reader queries this table "
            f"by, so writing one that is documented nowhere leaves a row "
            f"nothing can look up -- and the ledger takes no updates, so it "
            f"stays. Add the name and the one line saying what it means, in "
            f"this diff.")
    if table == "event" and cols.get("actor") not in ACTORS:
        raise UndeclaredActor(
            f"actor {cols.get('actor')!r} is not in ACTORS (kernel/ledger.py). "
            f"`actor` is the other half of what a reader queries this table by, "
            f"and it had no gate until this one: `human` and `person` were two "
            f"words for one fact, so a query for either missed every row that "
            f"used the other. "
            f"{'`human` is that older spelling and is now `person`. ' if cols.get('actor') == 'human' else ''}"
            f"Add the name and the one line saying what it means, in this diff.")
    cols = _redact_free_text(conn, table, cols)
    enc = _enc(cols)
    with writing(conn):
        if table == "event":
            # Reading the tail and inserting have to be one transaction, for
            # the reason `append_attempt` states above its own copy of this
            # line: several worktrees share this file by design, and SQLite's
            # default deferred transaction takes the write lock at the INSERT
            # -- after both readers have seen the same tail. `writing` is an
            # in-process depth counter for the write gate and locks nothing.
            #
            # Reproduced before this line existed: four processes, 40 events
            # each, 3 linkage breaks and 4 rows sharing a prev_hash. The
            # attempt chain was given this in its first version and the event
            # chain, added later, was not -- one file, two chains, one lock.
            #
            # Guarded because `insert` is called from inside transactions that
            # callers opened; `BEGIN` inside one is an error, and there the
            # caller already holds the lock.
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            prev = conn.execute(
                "SELECT row_hash FROM event WHERE row_hash != '' "
                "ORDER BY id DESC LIMIT 1").fetchone()
            enc["scheme"] = EVENT_SCHEME
            enc["prev_hash"] = prev["row_hash"] if prev else GENESIS
            enc["row_hash"] = _event_hash(enc["prev_hash"], enc)
        cur = conn.execute(
            f"INSERT INTO {table} ({', '.join(enc)}) VALUES "
            f"({', '.join('?' * len(enc))})"
            + (" ON CONFLICT DO NOTHING" if if_absent else ""),
            tuple(enc.values()),
        )
        conn.commit()
    return cur.lastrowid


#: Bumped to 4 to bring two recorded facts inside the hash. `worktree` was
#: written NOT NULL on every attempt and covered by nothing, so the record of
#: which worktree produced an answer could be rewritten and `v4 audit` would
#: still say the chain was intact -- provenance whose whole value is that it
#: cannot be changed afterwards. `facts_sha` arrives in the same bump because
#: it is new, and one scheme change is cheaper to reason about than two.
#:
#: Old rows keep verifying: `audit_chain` hashes each row under the scheme it
#: was written with, which is why the tag is stored per row.
CHAIN_SCHEME = "v4-chain-4"

#: Every scheme this ledger knows how to verify.  Old rows keep the scheme they
#: were written under, so changing the formula does not turn the whole history
#: permanently red -- which would leave `v4 ship` blocked forever with no way
#: back.
SCHEMES = ("v4-chain-1", "v4-chain-2", "v4-chain-3", "v4-chain-4")


def claim_digest(conn, claim_id: str) -> str:
    """The parts of a claim that decide how it is judged.

    The claim table has append-only triggers and no row hash, so dropping a
    trigger and repointing `claim.checker` at something friendlier left no
    trace anywhere. Folding these into the attempt means the answer carries
    proof of what it was answering.
    """
    row = conn.execute(
        "SELECT kind, checker, subject_refs, detector, detector_sha FROM claim "
        "WHERE id = ?", (claim_id,)).fetchone()
    if row is None:
        return ""
    material = "\x1f".join([claim_id, row["kind"], row["checker"],
                            row["subject_refs"], row["detector"] or "",
                            row["detector_sha"] or ""])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _migrate(conn):
    """Add columns the schema grew after this database was created.

    `CREATE TABLE IF NOT EXISTS` is a no-op against a table that already
    exists, so adding a column to SCHEMA changed new databases and left old
    ones behind with no error anywhere. Measured here: this repo's own ledger
    held 48 attempts and no `claim_digest` column, so every `v4 check` died on
    insert -- and the claim binding that column exists to provide had never
    once been active in it. The test suite stayed green throughout, because
    every test starts from a fresh database and none of them can reach this.

    Only ever adds. An append-only ledger cannot drop or retype a column
    without rewriting the history it exists to protect.
    """
    have_e = {r[1] for r in conn.execute("PRAGMA table_info(event)")}
    for name in ("prev_hash", "row_hash", "scheme"):
        if name not in have_e:
            conn.execute(f"ALTER TABLE event ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
    conn.commit()

    have = {r[1] for r in conn.execute("PRAGMA table_info(attempt)")}
    for name, decl in (("claim_digest", "TEXT NOT NULL DEFAULT ''"),
                       ("facts_sha", "TEXT NOT NULL DEFAULT ''"),
                       ("scheme", "TEXT NOT NULL DEFAULT 'v4-chain-1'")):
        if name not in have:
            conn.execute(f"ALTER TABLE attempt ADD COLUMN {name} {decl}")
    # A signature was reachable only through a claim id, and a claim id carries
    # its task id -- so "this repo has no SPEC.md for spec-coverage to read"
    # could be accepted for one task and never for the repo. Eight tasks meant
    # eight identical signatures for one structural fact, which is how signing
    # stops meaning anything. `scope='repo'` is that fact said once.
    have = {r[1] for r in conn.execute("PRAGMA table_info(accepted_risk)")}
    for name, decl in (("scope", "TEXT NOT NULL DEFAULT 'task'"),
                       ("cover_key", "TEXT NOT NULL DEFAULT ''"),
                       # Rows written before this one carry ''. That is not a
                       # gap somebody can close later: `_APPEND_ONLY` puts an
                       # UPDATE trigger on this table, so the historical rows
                       # keep saying "not recorded" for as long as the ledger
                       # exists. Everything that reads the column has to have
                       # a bucket for that rather than guess from `was_tty`.
                       ("signed_by", "TEXT NOT NULL DEFAULT ''")):
        if name not in have:
            conn.execute(f"ALTER TABLE accepted_risk ADD COLUMN {name} {decl}")
    conn.commit()


#: The event chain's own scheme tag, separate from the attempt chain's: the two
#: cover different columns and either can change without turning the other red.
EVENT_SCHEME = "v4-event-1"


def _event_hash(prev: str, c: dict) -> str:
    """Covers every column an event carries.

    All of them, because unlike an attempt there is no subset that "a state
    decision reads" -- `payload` is where a widen says which globs it added and
    where a `hook_seen` says whether the write was allowed, and both are the
    whole point of the row.
    """
    parts = [EVENT_SCHEME, prev,
             str(c.get("task_id") or ""), str(c.get("claim_id") or ""),
             str(c.get("kind") or ""), str(c.get("actor") or ""),
             str(c.get("payload") or ""), str(c.get("created_at") or "")]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _row_hash(prev: str, c: dict, scheme: str = None) -> str:
    """Covers every field a state decision reads.

    The scheme tag is here so the formula can change later without turning the
    whole history permanently red -- `audit_chain` can verify old rows under
    the scheme they were written with.
    """
    scheme = scheme or CHAIN_SCHEME
    parts = [
        scheme,
        prev, c["claim_id"], c["subject_digest"], c["checker_sha"], c["config_sha"],
        c["head_commit"], c["argv"], str(c["exit_code"]), c["stdout"], c["stderr"],
        c["started_at"], c["ended_at"],
    ]
    if scheme != "v4-chain-1":
        parts.append(c.get("claim_digest", ""))
    if scheme == "v4-chain-4":
        # Two fields the earlier schemes wrote and did not cover.
        parts.append(c.get("worktree", ""))
        parts.append(c.get("facts_sha", ""))
    if scheme in ("v4-chain-3", "v4-chain-4"):
        # Length-prefixed, because a checker controls both stdout and stderr and
        # could print the \x1f separator itself: ("A\x1fB", "C") and
        # ("A", "B\x1fC") hashed identically. Anyone able to do that could
        # already write the database -- what it bought them was that this class
        # of edit survived `v4 audit`, the one command whose whole job is to
        # notice exactly that.
        material = "".join(f"{len(x)}:{x}" for x in parts)
    else:
        material = "\x1f".join(parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class NoSuchTask(RuntimeError):
    """A requested task is absent, rather than completed or blocked."""


def require_task(conn, task_id):
    row = conn.execute("SELECT * FROM task WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise NoSuchTask(f"no such task: {task_id}")
    return row


def append_attempt(conn, **cols) -> int:
    """The only way an attempt enters the ledger, and the only place the chain grows.

    Reading the last hash and inserting have to be one transaction. Several
    worktrees share this file by design, so two kernels appending at once would
    otherwise read the same prev_hash and fork the chain -- and a forked chain
    fails `v4 audit` forever, with no way to tell it apart from tampering.
    """
    enc = _enc(cols)
    conn.execute("BEGIN IMMEDIATE")
    gate = writing(conn)
    gate.__enter__()
    try:
        enc.setdefault("claim_digest", claim_digest(conn, enc["claim_id"]))
        enc["scheme"] = CHAIN_SCHEME
        prev = conn.execute("SELECT row_hash FROM attempt ORDER BY id DESC LIMIT 1").fetchone()
        enc["prev_hash"] = prev["row_hash"] if prev else GENESIS
        enc["row_hash"] = _row_hash(enc["prev_hash"], enc, CHAIN_SCHEME)
        cur = conn.execute(
            f"INSERT INTO attempt ({', '.join(enc)}) VALUES ({', '.join('?' * len(enc))})",
            tuple(enc.values()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        gate.__exit__(None, None, None)
    return cur.lastrowid


def chain_head_path(repo_root) -> Path:
    """The chain's anchor, and it has to live outside the database.

    audit_chain walks from genesis to the last row, so deleting rows off the
    end leaves a shorter chain that still verifies -- and an empty ledger
    verifies too. Measured: zero attempts, `chain: intact`. A check that cannot
    fail is the shape this project exists to remove, and it was sitting in the
    one command meant to catch tampering.

    So the count and last hash are written to a file in git. Truncating the
    ledger now disagrees with a committed record, and rebuilding it from
    scratch disagrees more loudly.
    """
    return Path(repo_root) / ".v4" / "chain_head.json"


def _chain_anchor_prefix(conn, anchor, *, table, count_key, id_key, hash_key):
    """Count, last id and hash must witness the same immutable chain prefix."""
    if not isinstance(anchor, dict):
        return ["chain_head.json must be an object"]
    count, last_id, head = (anchor.get(k) for k in (count_key, id_key, hash_key))
    if (type(count) is not int or count < 0 or type(last_id) is not int
            or last_id < 0 or not isinstance(head, str)):
        return [f"chain_head.json has invalid {count_key}/{id_key}/{hash_key}"]
    condition = "row_hash != ''" if table == "event" else "1=1"
    prefix = conn.execute(
        f"SELECT COUNT(*) n, COALESCE(MAX(id), 0) last_id FROM {table} "
        f"WHERE {condition} AND id <= ?", (last_id,)).fetchone()
    total = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {condition}").fetchone()[0]
    if total < count:
        return [f"{total} {table} rows on disk, {count} recorded in chain_head.json "
                "-- rows were removed from the end"]
    if prefix["n"] != count or prefix["last_id"] != last_id:
        detail = "same count, different chain; " if total == count else ""
        return [f"chain_head.json {count_key}/{id_key} do not identify a {table} prefix: "
                f"{detail}recorded {count}/{last_id}, actual {prefix['n']}/{prefix['last_id']}; "
                "the anchor was edited or rows were removed/rebuilt"]
    row = conn.execute(f"SELECT row_hash FROM {table} WHERE {condition} AND id = ?",
                       (last_id,)).fetchone()
    actual_head = row["row_hash"] if row else GENESIS
    if actual_head != head:
        detail = ("same count, different chain. The ledger was rebuilt, not appended to"
                  if total == count else
                  "the chain grew without continuing from the recorded anchor")
        return [f"chain_head.json {hash_key} does not match {table} {last_id}: {detail}"]
    return []


def _event_anchor(conn, anchor) -> list:
    """Legacy anchors with no event witness remain readable."""
    if not isinstance(anchor, dict) or "events" not in anchor:
        return []
    return _chain_anchor_prefix(conn, anchor, table="event", count_key="events",
                                id_key="last_event_id", hash_key="event_head_hash")


def write_chain_head(conn, repo_root):
    """Both chains, because both are chained and only one was witnessed.

    `attempt` had an anchor from early on and `event` did not, though `event` is
    where `scope_widen` lives -- the sole input to `current_scope` in both
    `kernel/scope.py` and `hooks/write_block.py` -- along with engagement
    verdicts and every `hook_seen` mark. So the table that records what a guard
    allowed had no witness outside the database, and the forgery the anchor
    exists to catch worked there unchanged: drop the append-only triggers,
    delete rows off the end, and `_walk_events` returns a clean list.

    The `event_*` keys are written beside the old ones rather than replacing
    them: every adopter's committed `chain_head.json` predates this, and
    `audit_chain` treats their absence as "not anchored yet" rather than as a
    finding. A gate that goes red on everybody's first run after an upgrade is
    a gate that gets switched off.
    """
    row = conn.execute(
        "SELECT COUNT(*) n, COALESCE(MAX(id), 0) last_id FROM attempt").fetchone()
    head = conn.execute(
        "SELECT row_hash FROM attempt ORDER BY id DESC LIMIT 1").fetchone()
    ev = conn.execute(
        "SELECT COUNT(*) n, COALESCE(MAX(id), 0) last_id FROM event "
        "WHERE row_hash != ''").fetchone()
    ev_head = conn.execute(
        "SELECT row_hash FROM event WHERE row_hash != '' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    path = chain_head_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "attempts": row["n"], "last_id": row["last_id"],
        "head_hash": head["row_hash"] if head else GENESIS,
        "scheme": CHAIN_SCHEME,
        # Only chained rows are counted. A ledger older than the event chain
        # carries rows with an empty `row_hash`, and `_walk_events` already
        # reports that boundary once rather than per row; counting them here
        # would anchor a number the walk does not produce.
        "events": ev["n"], "last_event_id": ev["last_id"],
        "event_head_hash": ev_head["row_hash"] if ev_head else GENESIS,
    }, indent=2) + "\n")


def _walk_events(conn) -> list:
    """The event chain, and where it begins.

    Rows written before `event` was chained carry an empty `row_hash`. They are
    reported once, as a boundary, rather than per row: a database that predates
    the mechanism is not a tampered one, and saying so ten thousand times would
    make the real finding unreadable. What it must not do is stay silent -- a
    chain that starts partway through covers less than a reader assumes.
    """
    problems, prev, unchained, chained = [], GENESIS, 0, 0
    forks, seen_hashes = [], {GENESIS}
    for row in conn.execute("SELECT * FROM event ORDER BY id"):
        d = dict(row)
        if not (d.get("row_hash") or ""):
            unchained += 1
            if chained:
                # A prefix of unhashed rows is history. One *after* a hashed
                # row is a row inserted into the covered part, which is the
                # thing this walk exists to see -- and treating both the same
                # way would make every existing repo report BROKEN forever,
                # which is the same as not reporting.
                problems.append(
                    f"event {d['id']} ({d['kind']}) carries no hash and sits "
                    f"after {chained} that do. A row was inserted into the "
                    f"chained history.")
            continue
        chained += 1
        edited = d["row_hash"] != _event_hash(d["prev_hash"], d)
        if edited:
            problems.append(
                f"event {d['id']} ({d['kind']}): contents do not match "
                f"row_hash -- the row was edited after it was written.")
        if d["prev_hash"] != prev:
            # Three different faults reached this line and it named all of
            # them, so the one it printed was false about the one that
            # happened. They are separable from the rows themselves:
            #
            #   edited     the row's own hash no longer covers its own content
            #   removed    an id is missing from the sequence
            #   forked     neither -- two rows name the same predecessor
            #
            # A fork is what two kernels appending at once produce, and this
            # framework produced 21 of them in one adopter before
            # `insert` took `BEGIN IMMEDIATE`. Nothing was edited and nothing
            # was removed; what is unproven is the order of two rows written
            # milliseconds apart.
            #
            # Honest limit, stated rather than implied: a row inserted into the
            # middle *with an explicit id* would leave the same three answers.
            # `id` is AUTOINCREMENT, so that id has to be supplied by hand, and
            # the write gate is what stands between an attacker and doing it.
            # This walk cannot tell those two apart and does not pretend to.
            # A concurrent append names a predecessor that *is* in the chain,
            # just not the newest one -- two kernels read the tail at the same
            # moment and one of them lost the race. A row that was removed or
            # reordered names a predecessor that is not there at all, because
            # the hash it points at left with it.
            #
            # Measured on the adopter: 11704 points two rows back and 11705
            # points two back as well -- a three-way race. A rule written for
            # pairs called both of them tampering.
            forked = not edited and d["prev_hash"] in seen_hashes
            if forked:
                forks.append(f"{d['id']} ({d['kind']})")
            else:
                problems.append(
                    f"event {d['id']} ({d['kind']}): prev_hash does not follow "
                    f"the event before it, and this is not a concurrent append "
                    f"-- a row was removed, reordered, or inserted.")
        seen_hashes.add(d["row_hash"])
        prev = d["row_hash"]
    if forks:
        # Reported for as long as the rows exist, and not fatal. The ledger is
        # append-only by design, so a fork cannot be repaired -- and a verdict
        # that is both permanent and produced by this framework's own writer
        # would make one of its bugs brick every repo that ran it.
        problems.append(
            _FORK_PREFIX
            + f"{len(forks)} event(s) share a predecessor with the row before "
              f"them: {', '.join(forks[:6])}"
            + (f" … +{len(forks) - 6}" if len(forks) > 6 else "")
            + ". Every one of them still hashes over its own content and no id "
              "is missing, so nothing was edited and nothing was removed -- two "
              "kernels appended at the same moment. `insert` takes "
              "`BEGIN IMMEDIATE` now; these rows predate it and append-only is "
              "why they stay.")
    return problems


#: How a caller tells the one non-fatal finding from the rest.
#:
#: A prefix rather than a second return value: every caller of `audit_chain`
#: prints the list, and one of them decides whether `ship` holds.
_FORK_PREFIX = "concurrent append: "


def fatal(problems) -> list:
    """The problems that mean somebody changed the record.

    A fork means the order of two rows is unproven and nothing else -- no
    content is unverified. Holding `ship` on it forever, in a ledger that
    cannot be repaired, is a framework bug becoming a permanent verdict about
    the repo that ran it.
    """
    return [p for p in problems if not p.startswith(_FORK_PREFIX)]


def event_chain_starts_at(conn) -> tuple:
    """`(unchained, chained)` -- how much of the event history is covered.

    Reported, never failed on. A database that predates the mechanism is not a
    tampered one, and a check that fires on every existing repo forever is the
    same as no check. `v4 audit` prints it so the reader knows where the cover
    begins rather than assuming it covers everything.
    """
    row = conn.execute(
        "SELECT sum(row_hash = '') AS bare, sum(row_hash != '') AS hashed "
        "FROM event").fetchone()
    return (row["bare"] or 0), (row["hashed"] or 0)


def audit_chain(conn, repo_root=None):
    """Walk the whole chain.  Returns (ok, [problem, ...]).

    A clean walk means nobody edited a row after the fact.  It does not mean
    nobody could have -- see the module docstring.
    """
    problems = []
    prev, count = GENESIS, 0
    anchor = None
    if repo_root is not None:
        path = chain_head_path(repo_root)
        if path.is_file():
            try:
                anchor = json.loads(path.read_text())
            except json.JSONDecodeError as exc:
                problems.append(f"chain_head.json does not parse: {exc}")
        else:
            # Two states, and this reported one. A repo that has never shipped
            # has no anchor because nothing has written one yet; a repo whose
            # anchor was removed has none because something removed it. Both
            # came back `chain: BROKEN`, exit 1, on the first `v4 audit` a new
            # adopter runs -- which teaches them the command cries wolf before
            # they have anything for it to be right about.
            #
            # Deliberately still a problem in both: an unanchored chain is an
            # unanchored chain, and `ship` writes the anchor, so the sentence
            # names what has and has not happened rather than softening.
            never_shipped = conn.execute(
                "SELECT COUNT(*) FROM attempt").fetchone()[0] == 0
            problems.append(
                "no .v4/chain_head.json and no attempt in this ledger -- "
                "nothing has been checked here yet, so there is nothing to "
                "anchor. The first `v4 ship` writes it"
                if never_shipped else
                "no .v4/chain_head.json -- without an anchor outside the "
                "database, a truncated or rebuilt ledger verifies clean")
    for row in conn.execute("SELECT * FROM attempt ORDER BY id"):
        if row["prev_hash"] != prev:
            problems.append(f"attempt {row['id']}: prev_hash does not follow attempt {row['id'] - 1}")
        d = dict(row)
        expect = _row_hash(row["prev_hash"], d, d.get("scheme") or "v4-chain-1")
        if row["row_hash"] != expect:
            problems.append(f"attempt {row['id']}: contents do not match row_hash (edited in place)")
        elif (d.get("scheme") or "v4-chain-1") != "v4-chain-1":
            now = claim_digest(conn, row["claim_id"])
            if now != (d.get("claim_digest") or ""):
                problems.append(
                    f"attempt {row['id']}: the claim it answered has changed since. "
                    f"Its kind, checker, subject or detector is not what was judged.")
        prev = row["row_hash"]
        count += 1


    problems += _walk_events(conn)
    problems += _event_anchor(conn, anchor)
    problems += reconcile_signatures(conn, repo_root)

    if anchor is not None:
        problems += _chain_anchor_prefix(conn, anchor, table="attempt", count_key="attempts",
                                         id_key="last_id", hash_key="head_hash")
    return (not problems), problems


def latest_attempt_id(conn, claim_id):
    """The id of the newest attempt on this claim, or `None`.

    A named function because the same query existed twice: `state._digest_now`
    defined it as a local `latest_id`, and `lifecycle.check` built it again at
    the call site as two nested lambdas passed into `runner.run_checker` --
    which is the layer that execs a checker and reads its exit code, taking an
    untyped callable that turns out to be SQL. `hashing.subject_digest` states
    why it is injected at all ("so this module stays free of database
    imports"); that argument is about `hashing`, and says nothing about the
    injected thing being anonymous and constructed inline.
    """
    row = conn.execute(
        "SELECT id FROM attempt WHERE claim_id = ? ORDER BY id DESC LIMIT 1",
        (claim_id,)).fetchone()
    return row["id"] if row else None


def attempt_reader(conn):
    """`(claim_id) -> int | None`, bound to this connection.

    The shape `hashing.subject_digest` takes so that it can import no database,
    with a name and one home. Naming the *query* was not enough: both callers
    went on building the callable themselves, `state._digest_now` as an inline
    lambda and `lifecycle._latest_attempt_id_of` as a closure -- and the second
    one referred to a module it does not import, so calling it raised
    `NameError`. It had done so for its whole existence without a single
    failure, because `hashing.subject_digest` only reaches it for a ref whose
    `kind` is not `file`, and `derive.subject_refs_for` produces one of those
    only for a kind declaring `depends_on_kind`, which no kind here declares:
    measured on this ledger, 1,082 claims and 492 subject refs, every one of
    them `kind: "file"`.

    Two constructions of one line is what let that happen. `state`'s copy runs
    on every claim state derivation and is fine, so nothing ever compared the
    two, and a green suite of 1,418 tests said nothing about either.
    """
    return lambda claim_id: latest_attempt_id(conn, claim_id)


def latest_attempt(conn, claim_id: str):
    return conn.execute(
        "SELECT * FROM attempt WHERE claim_id = ? ORDER BY id DESC LIMIT 1", (claim_id,)
    ).fetchone()


#: The row `export_jsonl` writes last and `verify_exported` reads first. Not a
#: real table: it is the anchor, carried inside the file so an auditor needs
#: nothing else.
ANCHOR_TABLE = "_chain_head"


#: Free-text columns an export carries out of the repo. Anything a person or an
#: agent types lands in one of these.
_EXPORT_REDACTED = {
    "event": ("payload",),
    "accepted_risk": ("why",),
    "claim": ("note", "question"),
    "task": ("request",),
}


def _redact(text: str, root) -> str:
    """`analysis.redaction.redact`, judged by the repo's own pattern table.

    This used to reach into `runner` with a late import and a comment saying
    the import had to be late because `runner` imports this module back --
    which is an import cycle with an apology beside it, and `structural_lint`
    reported it. The function is pure text and now lives in the layer whose
    contract says so.

    `root` is what says which repo. Without it `redact` falls back to the twelve
    shipped patterns, so every credential family an adopter declares in
    `.v4/secret_patterns.json` was written into the committed export in the
    clear -- measured before the repair: a declared `whsec_` token survives
    `redact(text)` and this function verbatim, and `redact(text, root)` blanks
    it. `runner.py` and `derive.py` already thread the root through their own
    calls; this was the one that did not, and it is the last filter before an
    append-only file that gets committed and scanned.

    Required, not defaulted: a default of `None` here is the same narrow table
    reached by forgetting an argument, which is the state this repair is
    removing.
    """
    try:
        from .analysis.redaction import redact
        return redact(text, root)
    except Exception as exc:                                    # noqa: BLE001
        # Not `return text`. This is the last filter before an append-only file
        # that gets committed and scanned, and returning the input means the
        # one case the filter exists for -- it could not run -- is the case it
        # lets through, into a row nothing can delete from. Nine lines above,
        # this same function argues that a wider table must never be reached by
        # accident; a swallowed exception reaches the widest one there is.
        #
        # The whole value goes, and says why. Losing a note is recoverable and
        # visible; writing an unredacted credential into a committed export is
        # neither.
        return (f"[redacted: this value could not be filtered "
                f"({type(exc).__name__}: {exc})]")


#: Bytes at which `export_jsonl` seals the file it was about to rewrite and
#: starts a new one beside it. GitHub warns at 50 MiB per file and refuses a
#: push at 100 MiB, and an adopter's export was measured at 58.7 MB growing
#: 2.34 MB a day (2026-09-02, 23 days of use) -- seventeen days from a push
#: that would not go. Compressing it would have bought months and cost git its
#: deltas: an append-only text file packs as the lines added since the last
#: version, a gzip of it as a whole new blob on every ship. Sealing bounds the
#: growth and makes every sealed file byte-stable forever.
#:
#: It does **not** keep every file well under the limit, which this line used to
#: say. `_seal_if_full` measures the previous file *before* a rewrite that has
#: no ceiling of its own, so a segment comes out at whatever the unsealed rows
#: add up to. Enumerated in this repo: the one sealed segment,
#: `ledger_export.jsonl.0001`, is 49,233,070 bytes -- 46.95 MiB, 1.97x this
#: number and 94% of the 50 MiB cited above -- and it is committed. Nothing has
#: breached anything; the margin is one export's growth, which is a great deal
#: less than "well under" promises. The comment beside `_seal_if_full` records
#: why sealing after the write was tried and reverted.
SEAL_AT = 25_000_000

#: What this number does and does not promise, because a sweep read the comment
#: above it as a guarantee and the measurement does not support that reading.
#:
#: `_seal_if_full` asks the size *before* a rewrite that has no bound -- the
#: export writes every row since the last seal -- so a file at 24 MB passes the
#: check and comes out at whatever the ledger produces. Measured here: the one
#: sealed segment on disk is 49,233,070 bytes, 1.97x this number. It is still
#: under the 50 MiB GitHub warns at, which is the limit the comment cites, so
#: nothing has breached anything; the margin is one export's growth, and that
#: is smaller than the wording suggests.
#:
#: Not repaired, and the reason is the segment format rather than reluctance.
#: The open file's first row carries the `skip` counts and `starts_after` that
#: let a reader stitch the segments together, and `_seal_if_full` reads them
#: from it. Sealing after a write therefore has to leave a stub carrying that
#: header, and that changes how many segments one export produces -- which
#: `tests/test_an_export_that_outgrows_one_file.py` pins at two for two exports.
#: Tried and reverted here: without the stub the next export sees no open file,
#: reads no `skip`, and re-exports every row into a second segment.


#: The first row of an export that continues from sealed segments. `skip` is
#: how many rows of each table the sealed files already hold, so this file
#: carries the rest; `starts_after` is the attempt chain head those files ended
#: on, so `verify_exported` can walk across the boundary. Absent from an export
#: that has never sealed: no continuation header is needed before the first
#: segment. The final anchor independently records both chains.
SEGMENT_TABLE = "_segment"


def segment_files(path) -> list:
    """The sealed segments beside an export, oldest first.

    `ledger_export.jsonl.0001`, `ledger_export.jsonl.0002`, … next to
    `ledger_export.jsonl`. The sealed name keeps the open file's name as its
    prefix on purpose: `hashing.KERNEL_WRITTEN` and
    `checkers/review_finding.py` both recognise the export by that prefix, so
    a sealed segment is kernel output to the scope checker and a record of
    findings to the text-closure check without either being told.
    """
    from pathlib import Path as _Path
    p = _Path(path)
    return sorted(q for q in p.parent.glob(f"{p.name}.[0-9][0-9][0-9][0-9]")
                  if q.is_file())


def _rows_of(path):
    import json as _json
    from pathlib import Path as _Path
    for line in _Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield _json.loads(line)


def _seal_if_full(path, seal_at):
    """`(skip, starts_after)` for the file about to be written.

    The open file's first row says what earlier segments already hold. When
    the open file has outgrown `seal_at` it is renamed to the next segment
    name and never written again: its own rows are counted into `skip`, and
    the attempt head it ended on becomes `starts_after`. A file older than
    sealing -- no `_segment` row -- seals like any other; its rows are counted
    from the file and its head is read from the anchor it carries, or from its
    last attempt when it predates anchors too.
    """
    import json as _json
    from pathlib import Path as _Path
    p = _Path(path)
    skip, starts_after = {}, GENESIS
    if not p.is_file():
        return skip, starts_after
    with p.open("r", encoding="utf-8") as fh:
        first = fh.readline()
    if first.strip():
        head_row = _json.loads(first)
        if head_row.get("_table") == SEGMENT_TABLE:
            skip = dict(head_row.get("skip") or {})
            starts_after = head_row.get("starts_after") or GENESIS
    if p.stat().st_size < seal_at:
        return skip, starts_after
    counts, anchored, last_attempt = {}, None, None
    for r in _rows_of(p):
        t = r.get("_table")
        if t == SEGMENT_TABLE:
            continue
        if t == ANCHOR_TABLE:
            anchored = r.get("head_hash")
            continue
        counts[t] = counts.get(t, 0) + 1
        if t == "attempt":
            last_attempt = r.get("row_hash") or last_attempt
    seq = len(segment_files(p)) + 1
    p.rename(p.with_name(f"{p.name}.{seq:04d}"))
    for t, n in counts.items():
        skip[t] = skip.get(t, 0) + n
    return skip, (anchored or last_attempt or starts_after)


class ExportProjectionError(ValueError):
    """The original ledger cannot support a claimed redacted export view."""


def projection_path(path):
    return Path(str(path) + ".projection.json")


def _projection_digest(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"))
                          .encode()).hexdigest()


def _export_files(path):
    return segment_files(path) + [Path(path)]


def _write_export_projection(conn, path, root):
    """Commit to a redacted view after checking its source; never rehash history.

    Old sealed files stay byte-identical. The sidecar is a portable commitment
    to their public bytes, not a recovered SHA256 preimage or an authenticated
    signature. Git review anchors this commitment, as it anchors the original
    export. An edited source or unexplained change is ineligible. No secret
    value or secret-specific digest is written to the sidecar.
    """
    files, events = [], []
    for f in _export_files(path):
        raw = f.read_bytes()
        files.append({"name": f.name, "sha256": hashlib.sha256(raw).hexdigest()})
        for line in raw.decode("utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("_table") != "event" or not row.get("row_hash"):
                continue
            if not row.get("_redacted") and (
                    _REDACTED_MARK not in str(row.get("payload")) or
                    _event_hash(row.get("prev_hash") or "", row) == row["row_hash"]):
                continue
            source = conn.execute("SELECT * FROM event WHERE id = ?", (row["id"],)).fetchone()
            where = f"{f.name}: event {row['id']}"
            if source is None:
                raise ExportProjectionError(f"{where}: no original row for redacted export proof")
            source = dict(source)
            if (source.get("row_hash") != row["row_hash"] or
                    _event_hash(source.get("prev_hash") or "", source) != source["row_hash"]):
                raise ExportProjectionError(f"{where}: original hash is invalid; cannot certify a redacted view")
            projected, fields = dict(source), []
            for field in _EXPORT_REDACTED["event"]:
                if isinstance(source.get(field), str):
                    projected[field] = _redact(source[field], root)
                    if projected[field] != source[field]:
                        fields.append(field)
            public = {k: v for k, v in row.items() if k not in {"_table", "_redacted"}}
            if (not fields or public != projected or
                    ("_redacted" in row and row["_redacted"] != sorted(fields))):
                raise ExportProjectionError(f"{where}: bytes are not the current redaction of the original row")
            events.append({"file": f.name, "id": row["id"], "source_hash": row["row_hash"],
                           "projection_hash": _projection_digest(row), "fields": sorted(fields)})
    target = projection_path(path)
    if not events and not target.exists():
        return
    current = [{"name": f.name, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
               for f in _export_files(path)]
    if current != files:
        raise ExportProjectionError("export changed while its redacted view was being checked; retry export")
    proof = {"scheme": "v4-export-projection-1", "files": files, "events": events}
    import os
    import tempfile
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=target.name+".", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(proof, stream, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _load_export_projection(path, files):
    """Return exact row commitments plus explicit errors; no database is read."""
    target = projection_path(path)
    if not target.exists():
        return {}, [], None
    try:
        proof = json.loads(target.read_text())
        expected = [{"name": f.name, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
                    for f in files]
        if (not isinstance(proof, dict) or proof.get("scheme") != "v4-export-projection-1" or
                proof.get("files") != expected or not isinstance(proof.get("events"), list)):
            raise ValueError("schema, segment inventory or file digest differs")
        records = {}
        for row in proof["events"]:
            fields = row.get("fields") if isinstance(row, dict) else None
            if (not isinstance(row, dict) or row.get("file") not in {f.name for f in files} or
                    type(row.get("id")) is not int or row["id"] <= 0 or
                    not isinstance(fields, list) or not fields or
                    any(f not in _EXPORT_REDACTED["event"] for f in fields) or
                    fields != sorted(set(fields))):
                raise ValueError("invalid redacted event coordinates")
            for field in ("source_hash", "projection_hash"):
                digest = row.get(field)
                if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise ValueError("invalid event digest")
            key = (row["file"], row["id"])
            if key in records:
                raise ValueError("duplicate redacted event")
            records[key] = row
        return records, [], proof
    except (OSError, ValueError, TypeError):
        return {}, [f"{target.name}: redacted export proof is invalid or stale; re-export from the original ledger"], None


def export_jsonl(conn, out_path, root, seal_at=SEAL_AT):
    """Write the ledger out as portable JSONL.  The whole ledger, on purpose.

    The chain argument in SPEC.md rests on CI walking it. CI cannot: the ledger
    lives in `.git/v4/`, which a fresh clone does not have. So the anchor named
    in three places has never once existed, and could not have.

    Rows retain their original hashes, predecessors and schemes. Unchanged
    rows can be re-derived from JSONL; legacy redactions use the separately
    source-checked public-byte commitments below. An auditor needs the export
    set and any projection sidecar, not the live database.

    This took a `task_id=` and `v4 export --task` passed one, and it could not
    work twice over. The SQL was `WHERE task_id = ?` against every table, and
    `task` has no such column, `attempt` has no such column, `accepted_risk`
    has no such column -- the call raised OperationalError on the first table
    every time, so the flag had never once run to completion. And repairing the
    SQL would not have saved it: `verify_exported` walks `prev_hash` from
    GENESIS, so a file holding one task's attempts reports "a row was removed"
    on every row after the first. A slice of a hash chain is not a chain. The
    only caller that ever worked -- `lifecycle` on ship -- never passed it.

    `root` is the repo this ledger belongs to, and it is here for one line: the
    redaction below reads `<root>/.v4/secret_patterns.json`, so without it the
    file this writes is filtered by the shipped families only. Both callers
    already hold it -- `lifecycle.ship` as `cfg.root`, `cli.cmd_export` as
    `--repo` -- so nothing had to be found; it had to be passed.

    The whole ledger, and not necessarily one file. Once the open file has
    outgrown `seal_at` (`SEAL_AT` unless a test says otherwise) it is sealed
    under a numbered name beside it and this writes only the rows the sealed
    files do not hold, after a `_segment` row saying so. Every row still goes
    out exactly once, in order, and `verify_exported` reads the set.
    """
    import json as _json
    from pathlib import Path as _Path
    tables = ["task", "claim", "attempt", "event", "accepted_risk", "cost_observation"]
    skip, starts_after = _seal_if_full(out_path, seal_at)
    n, attempts, head = 0, 0, starts_after
    events, event_head, sealed_event_rows = 0, GENESIS, 0
    for sealed in segment_files(out_path):
        for row in _rows_of(sealed):
            if row.get("_table") == "event":
                sealed_event_rows += 1
                if row.get("row_hash"):
                    events += 1
                    event_head = row["row_hash"]
    if type(skip.get("event", 0)) is not int or skip.get("event", 0) != sealed_event_rows:
        raise ExportProjectionError("sealed event count differs from the export offset; cannot carry missing history")
    with _Path(out_path).open("w", encoding="utf-8") as fh:
        if skip:
            fh.write(_json.dumps({"_table": SEGMENT_TABLE,
                                  "seq": len(segment_files(out_path)) + 1,
                                  "skip": skip, "starts_after": starts_after},
                                 sort_keys=True) + "\n")
        for t in tables:
            # `rowid` order and an offset, never a `WHERE`: the ledger takes no
            # deletes, so the rows a sealed segment holds are exactly the first
            # `skip[t]` of the table, and the rest are this file's.
            for row in conn.execute(
                    f"SELECT * FROM {t} ORDER BY rowid LIMIT -1 OFFSET ?",
                    (skip.get(t, 0),)):
                d = dict(row)
                if t == "attempt":
                    attempts += 1
                    head = d.get("row_hash") or head
                if t == "event" and d.get("row_hash"):
                    events += 1
                    event_head = d["row_hash"]
                # Free text, on its way into a committed file. `runner.redact`
                # is applied to `attempt.stdout`, `attempt.stderr` and the
                # checker `--out` payload, with a comment naming this file as
                # the reason -- and `insert` applied it to neither
                # `event.payload` nor `accepted_risk.why`, so scope-widen
                # reasons, deferral reasons, review notes and risk sign-off
                # text left the repo unredacted. `engagement.judge_text` guards
                # its own path and says why; no other writer of free text into
                # this ledger had an equivalent.
                #
                # `insert` applies this now, so for any row written since that
                # change this loop finds nothing to do -- `[redacted]` does not
                # match the patterns, so it is idempotent. It stays as the belt:
                # a row written before the change still holds the original text,
                # and it must not leave the repo just because the writer that
                # produced it predates the repair.
                #
                # And when it does fire, the row says so. Its `row_hash` covers
                # the original bytes, so nothing downstream can re-derive it
                # from what is written here -- and `verify_exported` used to
                # report that as "Something edited the row after it was
                # written", which is a sentence about a person who does not
                # exist. Two adopters chased it on the same day.
                changed = []
                for field in _EXPORT_REDACTED.get(t, ()):
                    if isinstance(d.get(field), str):
                        was = d[field]
                        d[field] = _redact(was, root)
                        if d[field] != was:
                            changed.append(field)
                if changed:
                    d["_redacted"] = sorted(changed)
                fh.write(_json.dumps({"_table": t, **d}, sort_keys=True) + "\n")
                n += 1
        # The anchor travels with the file it anchors. `.v4/chain_head.json` is
        # refreshed by every `v4 check` and this is written only by `ship`, so
        # the two are snapshots of different moments -- and CI compared them as
        # though they were one. Measured: all six runs of the workflow failed at
        # "export has 72 attempts, anchor records 81", which is the ordinary gap
        # between a ship and the checks after it, reported as tampering. The
        # step that has never once passed is the step the whole tamper argument
        # rests on.
        #
        # Self-contained is also what `verify_exported`'s own docstring asks
        # for: "an auditor should need the file and nothing else."
        fh.write(_json.dumps({"_table": ANCHOR_TABLE, "attempts": attempts,
                              "head_hash": head, "events": events,
                              "event_head_hash": event_head}, sort_keys=True) + "\n")
    _write_export_projection(conn, out_path, root)
    return n


def verify_exported(path, *, details=None):
    """Re-walk an exported chain with no database in reach.

    Deliberately re-derived against the JSONL rather than by reusing the live
    walk: the live walk reads the same rows it is judging, so a tampered
    database hands it a consistent set of lies. Legacy redactions additionally
    need the adjacent projection sidecar. Its public-byte commitments do not
    recover the original secret preimage; `details` reports that distinction.
    """
    from pathlib import Path as _Path
    open_file = _Path(path)
    files = segment_files(open_file) + [open_file]
    projection, projection_problems, projection_snapshot = _load_export_projection(open_file, files)
    used_projection = set()
    if details is not None:
        details["redacted_projection_events"] = []
    # `ev_prev` beside `prev`, and outside the loop for the same reason: the
    # event chain crosses a segment boundary exactly as the attempt chain does.
    # It was initialised per file, so the first event of the open file was
    # compared against GENESIS instead of the last event of the sealed segment
    # before it -- measured on this repo, one `prev_hash does not follow` at
    # precisely that seam, reported the day the event walk was added.
    problems, total, prev, ev_prev = list(projection_problems), 0, GENESIS, GENESIS
    event_total = 0
    for seq, f in enumerate(files, 1):
        sealed = f is not open_file
        where = f"{f.name}: " if len(files) > 1 else ""
        if sealed:
            # The sealed names are a sequence, and a hole in it is rows gone:
            # `.0001` and `.0003` with nothing between them cannot chain, and
            # saying which name is missing beats a row-level message about the
            # first attempt of `.0003` not following `.0001`.
            want = f"{open_file.name}.{seq:04d}"
            if f.name != want:
                problems.append(
                    f"{f.name}: expected {want} at this point in the sequence, "
                    f"so a sealed segment is missing or misnamed and the rows it "
                    f"held are gone.")
        header, attempts, anchor, events = None, [], None, []
        for r in _rows_of(f):
            t = r.get("_table")
            if t == "attempt":
                attempts.append(r)
            elif t == "event":
                # Collected, because they were skipped. An auditor with the
                # file and nothing else could re-derive every attempt and not
                # one event -- and the event rows are the ones that say what a
                # guard allowed, which scope a task was given, and whether an
                # engagement sentence was accepted.
                events.append(r)
            elif t == ANCHOR_TABLE:
                anchor = r
            elif t == SEGMENT_TABLE:
                header = r
        if header is not None:
            claimed = header.get("starts_after") or GENESIS
            if claimed != prev:
                problems.append(
                    f"{where}says it continues from {str(claimed)[:12]} and the "
                    f"segment before it ended on {prev[:12]}. Rows between the "
                    f"two are missing, or a segment was rebuilt.")
        for r in attempts:
            # Same defaults the live walk uses. Getting either of these wrong
            # makes every row red, which reads as total compromise and means
            # nothing -- the same uselessness as a check that never fires,
            # pointed the other way.
            scheme = r.get("scheme") or "v4-chain-1"
            if scheme not in SCHEMES:
                problems.append(f"{where}attempt {r['id']}: unknown chain scheme {scheme!r}")
                prev = r.get("row_hash", "")
                continue
            row = {k: v for k, v in r.items() if k != "_table"}
            expect = _row_hash(r.get("prev_hash") or "", row, scheme)
            if expect != r.get("row_hash"):
                problems.append(
                    f"{where}attempt {r['id']} (claim {r.get('claim_id')}, exit "
                    f"{r.get('exit_code')}): the recorded hash does not match its "
                    f"own row. Something edited the row after it was written.")
            if (r.get("prev_hash") or "") != prev:
                problems.append(
                    f"{where}attempt {r['id']}: prev_hash does not follow the row "
                    f"before it. A row was removed, reordered, or inserted.")
            prev = r.get("row_hash", "")

        # The other chain, re-derived the same way and against its own
        # predecessor. Rows written before `event` was chained carry an empty
        # `row_hash`; they are skipped rather than reported, for the reason
        # `_walk_events` gives -- a database that predates the mechanism is not
        # a tampered one -- and skipping them here keeps the two walks
        # answering the same question about the same rows.
        for r in events:
            if not r.get("row_hash"):
                continue
            event_total += 1
            row = {k: v for k, v in r.items() if k != "_table"}
            expect = _event_hash(r.get("prev_hash") or "", row)
            key = (f.name, r["id"])
            commitment = projection.get(key) or {}
            projected = ((r.get("_redacted") or expect != r.get("row_hash")) and
                         commitment.get("source_hash") == r.get("row_hash") and
                         commitment.get("projection_hash") == _projection_digest(r))
            if projected:
                used_projection.add(key)
                if details is not None:
                    details["redacted_projection_events"].append({"file": f.name, "id": r["id"]})
            elif r.get("_redacted"):
                problems.append(
                    f"{where}event {r['id']} ({r.get('kind')}): "
                    f"{', '.join(r['_redacted'])} was redacted when this file "
                    f"was written, and the hash covers the original -- so this "
                    f"row cannot be verified from the export alone. Re-export "
                    f"from the original ledger to create its redacted-view proof.")
            elif expect != r.get("row_hash"):
                if _REDACTED_MARK in json.dumps(row, ensure_ascii=False):
                    problems.append(
                        f"{where}event {r['id']} ({r.get('kind')}): carries "
                        f"`{_REDACTED_MARK}` and hashes to something else, so "
                        f"the exporter blanked a value after the hash was taken "
                        f"and this row cannot be verified from the export alone. "
                        f"Re-export from the original ledger; the sealed bytes stay unchanged.")
                else:
                    problems.append(
                        f"{where}event {r['id']} ({r.get('kind')}): the recorded "
                        f"hash does not match its own row. Something edited the "
                        f"row after it was written.")
            # A projection covers redacted bytes, never a missing predecessor.
            if (r.get("prev_hash") or "") != ev_prev:
                problems.append(
                    f"{where}event {r['id']}: prev_hash does not follow the event "
                    f"before it. A row was removed, reordered, or inserted.")
            ev_prev = r.get("row_hash", "")

        # Walking forward cannot see rows dropped off the end -- the shorter
        # chain is internally perfect. `audit_chain` grew `.v4/chain_head.json`
        # for that and this walk, the one CI runs and the only one that can run
        # there, was never given an equivalent: truncating the file from the
        # end printed "chain intact across N attempt(s)", the same output as a
        # complete chain. Every file, sealed or open, carries its own anchor,
        # so a sealed segment cut short is seen the same way.
        if anchor is None:
            problems.append(
                f"{where}this export carries no chain anchor, so rows removed "
                f"from the end cannot be detected here. Re-export with `v4 ship`.")
        else:
            if len(attempts) != anchor.get("attempts"):
                problems.append(
                    f"{where}{len(attempts)} attempt(s) in this file and "
                    f"{anchor.get('attempts')} in the anchor written beside them. "
                    f"Rows were added or removed after the export.")
            elif prev != (anchor.get("head_hash") or GENESIS):
                problems.append(
                    f"{where}the last row hashes to {prev[:12]} where the anchor "
                    f"records {str(anchor.get('head_hash'))[:12]} -- same count, "
                    f"different chain. The export was rebuilt, not written.")
            if "events" in anchor or "event_head_hash" in anchor:
                if type(anchor.get("events")) is not int or anchor["events"] != event_total:
                    problems.append(f"{where}event count differs from the export-prefix anchor; rows were added or removed")
                if anchor.get("event_head_hash") != ev_prev:
                    problems.append(f"{where}event head differs from the export-prefix anchor")
            elif not sealed:
                problems.append(f"{where}no event anchor: re-export from the original ledger; sealed files need not change")
        # Rows walked, both chains. It counted attempts alone, which was true
        # while attempts were all this walked; an export of a repo that has
        # events and no attempt yet reported "0 rows" over a file it had just
        # re-derived every event in, and a number that understates the check is
        # the same shape as a check that does not run.
        total += len(attempts) + sum(1 for r in events if r.get("row_hash"))
    if set(projection) != used_projection:
        problems.append("redacted export proof names an absent or changed event")
    if projection_snapshot is not None:
        _, changed, current = _load_export_projection(open_file, _export_files(open_file))
        problems.extend(changed)
        if not changed and current != projection_snapshot:
            problems.append("redacted export proof changed while the export was being verified; retry audit")
    if details is not None:
        details["original_hashes_rederived"] = total - len(used_projection)
    return total, problems


def _same(value):
    """The two sides spell this one the same way."""
    return value


def _as_json(value):
    """A JSON value, whether it arrived as one or as the text of one.

    `covers` is a dict in the record and the *text* of one in the row:
    `risk.accept` writes `cover_key=json.dumps(ck, sort_keys=True)`. Compared
    raw, a dict is never equal to a string, so every repo-scoped signature made
    `v4 ship` print `chain: BROKEN` naming two values a reader can see are the
    same -- permanently, since neither side is wrong. `doctor` reads the same
    column and remembers to parse it; this did not.
    """
    if isinstance(value, str):
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value or None


#: What an empty column means, when the record carries the field and the row
#: does not.
#:
#: For a column that has existed for as long as the table has, it means one
#: side was written without a fact the other holds, and a reader should be told.
#: For a column added later it means the row was written before the column
#: existed -- and `_APPEND_ONLY` puts an UPDATE trigger on this table, so no
#: backfill is possible and it will mean that forever. Reporting those is the
#: permanently-BROKEN failure `superseded` -- further down this file -- was
#: added to stop, arriving by a different road.
ROW_WAS_INCOMPLETE = "incomplete"
ROW_PREDATES_THE_COLUMN = "predates"

#: (field in the committed record, column in the row, how to read both sides,
#:  what an empty column means).
#:
#: The reader is applied to *both* sides, which is the point: this table is the
#: one place the two spellings meet. It was a pair plus a chain of
#: `if column == ...` inside the loop, and the chain had one entry when a second
#: field needed one -- `covers` went in with no reader and compared a dict
#: against the text of that dict on every repo-scoped signature there has ever
#: been. A per-field difference belongs beside the field, and "what an empty
#: column means" is the second such difference: `signed_by` was added on
#: 2026-08-27 to a table this repo already held 181 rows in, every one of
#: them empty on this column and none of them fillable.
SIGNATURE_FIELDS = (
    ("claim", "claim_id", _same, ROW_WAS_INCOMPLETE),
    ("kind", "kind", _same, ROW_WAS_INCOMPLETE),
    ("why", "why", _same, ROW_WAS_INCOMPLETE),
    ("scope", "scope", _same, ROW_WAS_INCOMPLETE),
    ("who", "who", _same, ROW_WAS_INCOMPLETE),
    # `was_tty` is an int in SQLite and a bool in the record.
    ("stdin_was_a_tty", "was_tty", bool, ROW_WAS_INCOMPLETE),
    # The one fact this pair exists to hold honest: the file says which of the
    # three routes signed, and until this column existed the ledger could not
    # contradict it. A file that says `monitor` against a row that says `agent`
    # is now a finding rather than a difference nobody could see.
    ("signed_by", "signed_by", _same, ROW_PREDATES_THE_COLUMN),
    ("covers", "cover_key", _as_json, ROW_WAS_INCOMPLETE),
)


def reconcile_signatures(conn, repo_root=None):
    """Every signature has a row and a committed record, and they agree.

    `RISK_ACCEPTED` is terminal, and it is reached by inserting one row -- not
    by an attempt, so `audit_chain` walking the attempt table never sees it. The
    write gate now refuses that insert from outside, and the gate is one
    `DROP TRIGGER` from being off, so the second half has to exist too.

    The design already named the real anchor: signing writes
    `.v4/risks/<claim>.json` and that file lands in a commit with a name on it.
    `accepted_risk.git_record` stores the path, and nothing had ever read it --
    the anchor was wired at one end.

    Both directions, because they fail differently. A row with no file is a
    signature nobody put their name to. A file with no row is a record of a
    decision the ledger never made.
    """
    problems = []
    if repo_root is None:
        return problems
    root = Path(repo_root)
    # Keyed by row id, not by claim. `rows[r["claim_id"]] = dict(r)` kept the
    # last signature for each claim and dropped the rest, and a claim gets
    # signed more than once by design -- the message printed at signing says the
    # cover lapses the moment the checker changes, and re-signing is the answer.
    # Measured on the reference adopter: claim a6016966f1777b96 has four rows,
    # three of them naming `.v4/risks/<claim>.json` and the fourth naming
    # `.v4/risks/repo/secret.json` after it was re-signed repo-scoped. Only the
    # fourth was ever examined, with two consequences: that per-claim record was
    # reported as "a signature the ledger never made" -- it is not, three rows
    # name it -- and the three earlier signatures were reconciled against
    # nothing at all, which is the direction this function exists for.
    rows = {}
    try:
        for r in conn.execute("SELECT * FROM accepted_risk"):
            row = dict(r)
            rows[row.get("id")] = row
    except sqlite3.Error as exc:
        # Same shape one function over, and this one is louder: `audit_chain`
        # folds these problems into the verdict `v4 ship` reads, so an
        # unreadable signature table would have shipped as "every signature
        # has a row and a committed record, and they agree".
        problems.append(f"the signature rows could not be read "
                        f"({type(exc).__name__}: {exc}), so whether every "
                        f"signature has a row and a record is unknown")
        return problems

    risks_dir = root / ".v4" / "risks"
    # Every record under `.v4/risks/`, keyed by its repo-relative path.
    #
    # Two things were wrong with `{p.stem: p for p in risks_dir.glob("*.json")}`.
    # `glob` never descends into `repo/`, where a repo-scoped signature is
    # written -- this same function establishes that four lines down -- so the
    # direction the docstring calls out ("a file with no row is a record of a
    # decision the ledger never made") covered per-claim records only, and the
    # file whose whole purpose is to be read in a diff was invisible to
    # `v4 audit`. And the stem is the wrong key for those: a per-claim record is
    # named after the claim and a repo-scoped one after the *kind*, so matching
    # by stem could never have paired them anyway.
    #
    # The row already says which file it wrote -- `accepted_risk.git_record` --
    # so the path is what both sides are keyed by, and there is one key rather
    # than one per layout.
    on_disk = {str(p.relative_to(root)): p for p in risks_dir.rglob("*.json")} \
        if risks_dir.is_dir() else {}

    # A repo-scoped signature writes `.v4/risks/repo/<kind>.json` -- one file
    # per kind -- while the ledger holds one row per claim. Sign the same kind
    # twice and the second write replaces the file, so the first row points at a
    # record that now names somebody else's claim.
    #
    # And signing twice is what the tool asks for: the message printed at
    # signing says the cover lapses "the moment the checker, its detector, or
    # .v4/config.json changes", and the only answer to that is to sign again.
    # Measured on the reference adopter: two signatures at 09:19, two more for
    # the same kinds at 16:22, and `chain: BROKEN` from then on -- permanently,
    # because the ledger is append-only and the file cannot name two claims.
    #
    # A row whose file has since been rewritten by a newer signature for the
    # same kind is superseded, not orphaned. `state._repo_risk_covers` already
    # treats it that way without knowing it: the old row's `cover_key` names the
    # checker as it was, so it matches nothing and covers nothing. This says the
    # same thing in the audit rather than calling history a broken chain.
    superseded = set()
    by_record = {}
    for rid, row in rows.items():
        rec = row.get("git_record")
        if rec:
            by_record.setdefault(rec, []).append(rid)
    for rec, seen in by_record.items():
        superseded.update(sorted(seen)[:-1])

    for rid, row in sorted(rows.items()):
        if rid in superseded:
            continue
        cid = row.get("claim_id")
        rec = row.get("git_record")
        if not rec:
            problems.append(
                f"claim {cid} is signed for and the row names no record. The "
                f"signature exists only inside the database it exempts.")
            continue
        path = root / rec
        from_commit = ""
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                problems.append(f"{rec} does not read: {exc}")
                continue
        else:
            # Two ways for the file to be absent, and only one of them is a
            # signature nobody put a name to. The ledger is shared by every
            # worktree of a repo -- `ledger_path` reads `--git-common-dir` and
            # says so -- while the record is a tracked file that lives on the
            # branch it was committed to. So a signature made on another branch
            # is a row here with no file here, and reporting that as a forged
            # row sends the reader after a tamper that is not there.
            #
            # It used to stop at saying so, and saying so is a problem, and
            # `fatal` keeps every problem that is not a concurrent append -- so
            # `ship` was held on it. Measured 2026-08-27, on the wave this repo
            # ran on itself: two cuts each signed before either shipped, and
            # neither could leave until one was merged into the other. That is
            # not what `fatal` says it is for ("the problems that mean somebody
            # changed the record"), and this branch had already proved the
            # opposite -- it went to git for the commit and then declined to
            # open it.
            #
            # So it opens it. The three questions asked of a record on disk --
            # does it parse, does it agree with the row, is it under version
            # control -- are all answerable from the object store, and the
            # third is answered by construction: a commit holds it.
            #
            # `--diff-filter=d` excludes deletions, so this is the newest commit
            # where the file *exists* rather than the newest that touched it. A
            # record deleted on one branch and alive on another would otherwise
            # resolve to the deletion and read as unreadable bytes.
            known = subprocess.run(
                ["git", "log", "--all", "-1", "--format=%H", "--diff-filter=d",
                 "--", rec],
                cwd=root, capture_output=True, text=True)
            rev = known.stdout.strip() if known.returncode == 0 else ""
            if not rev:
                problems.append(
                    f"claim {cid} is signed for and {rec} is on no branch in "
                    f"this repo. The row is the whole signature, and a row is "
                    f"what an insert can forge.")
                continue
            blob = subprocess.run(["git", "show", f"{rev}:{rec}"],
                                  cwd=root, capture_output=True, text=True)
            if blob.returncode != 0:
                problems.append(
                    f"claim {cid} is signed for and {rec} is named by commit "
                    f"{rev[:12]} whose bytes will not read "
                    f"({blob.stderr.strip().splitlines()[0][:80]
                       if blob.stderr.strip() else 'no reason given'}). "
                    f"The record cannot be paired with its row.")
                continue
            text, from_commit = blob.stdout, rev
        try:
            rec_data = json.loads(text)
        except json.JSONDecodeError as exc:
            problems.append(f"{rec} does not read: {exc}")
            continue
        for field, column, read, when_empty in SIGNATURE_FIELDS:
            if field not in rec_data:
                continue
            theirs, ours = read(rec_data[field]), read(row.get(column))
            if theirs == ours:
                continue
            # A record that carries a cover against a row that carries none is
            # not two edited copies of one fact -- it is one side that never
            # held it. Saying "edited after the other" sends the reader looking
            # for a tamper that is not there. This repo has one such row.
            if ours in (None, "") and theirs not in (None, ""):
                if when_empty == ROW_PREDATES_THE_COLUMN:
                    # Not a finding and never will be. The row was written
                    # before the column, and the UPDATE trigger on this table
                    # means it stays that way -- so reporting it would put a
                    # permanent problem in the verdict `v4 ship` reads, for
                    # every signature made before the column landed.
                    continue
                problems.append(
                    f"{rec} carries {field!r} and the ledger row for claim "
                    f"{cid} carries none. The record is the fuller of the two; "
                    f"the row was written without it.")
                continue
            problems.append(
                f"{rec} and the ledger disagree about {field!r} for claim "
                f"{cid} ({theirs!r} against {ours!r}). One of them was "
                f"edited after the other.")
        # Only for a record read off disk. `ls-files` asks about the index, and
        # the index is this branch's -- a record read out of a commit on another
        # branch is not in it and never will be, so asking would turn the answer
        # to "is this under version control" from yes into no. It came out of a
        # commit; that is the same fact this line exists to establish.
        if not from_commit:
            tracked = subprocess.run(["git", "ls-files", "--error-unmatch", rec],
                                     cwd=root, capture_output=True, text=True)
            if tracked.returncode != 0:
                problems.append(
                    f"{rec} is not tracked by git, so the signature carries no "
                    f"name. Commit it -- that is the part a database row "
                    f"cannot fake.")

    claimed = {row.get("git_record") for row in rows.values() if row.get("git_record")}
    for rel in sorted(on_disk):
        if rel not in claimed:
            problems.append(
                f"{rel} records a signature the ledger never made. Either the "
                f"row was removed, or the file was written by something other "
                f"than `v4 risk accept`.")

    return problems


#: `reconcile_deferrals` used to sit here and now lives in `kernel/review.py`.
#: It reconciles the review domain -- `finding_deferred`,
#: `finding_deferral_withdrawn`, the `.v4/deferred/<claim>.json` layout and the
#: `why`/`target` contract -- and all four of those are names `review` already
#: owns, spelled again here as literals because this module cannot import that
#: one. The cycle was the symptom: a domain reconciliation was sitting one layer
#: below the module that owns the domain, and renaming `DEFER_DIR` in `review`
#: would have left this reconciling a directory nothing writes, reporting every
#: deferral as missing rather than failing. Its only caller was `doctor`, so
#: nothing held it here. `reconcile_signatures` above stays: a signature is this
#: module's own artefact, not another layer's vocabulary.


#: How a task ends. Two ways, and both are appends: `ship` records that the work
#: is finished, `abandon` that it will not be. Three places asked "is this task
#: over" -- both hooks and `doctor` -- and each wrote the same SQL. The second
#: ending was added on 2026-08-10 and had to be pasted into all three; the next
#: one would too.
ENDED_KINDS = ("shipped", "abandoned")

#: Claims whose last answer was a finding and that nobody settled.
#:
#: A claim ends three ways -- answered, signed, retracted -- and abandoning its
#: task creates a fourth that the taxonomy has no name for and no reader. The
#: delta gates then stop asking on the next task, because the change is in the
#: new base and there is no delta left, so the finding is not wrong: it is
#: unasked. Measured on this repo the day the query was first written: 27.
UNSETTLED_FAILS_SQL = """
  SELECT c.id, c.kind, c.file, c.symbol
  FROM claim c
  JOIN attempt a ON a.id = (SELECT MAX(id) FROM attempt WHERE claim_id = c.id)
  WHERE a.exit_code = 1
    AND c.task_id = ?
    AND NOT EXISTS (SELECT 1 FROM accepted_risk r WHERE r.claim_id = c.id)
    AND NOT EXISTS (SELECT 1 FROM event e WHERE e.claim_id = c.id
                                            AND e.kind = 'retracted')
  ORDER BY c.rowid
"""


def unsettled_fails(conn, task_id, exclude=()) -> list:
    """[(id, kind, file, symbol)] -- what this task found and never settled.

    `exclude` is the repo's `derive_exclude`. A finding raised before that line
    covered a directory is not a finding about this repo's code: measured here,
    14 of the 27 this query first returned were `fail-closed` firing on
    `tests/fixtures/*/red/` -- files that are broken on purpose, and that
    `derive_exclude` has named ever since. Counting them makes the row noise,
    and a row that is mostly noise is one people stop reading.
    """
    from .analysis.subject_files import excluded
    rows = [tuple(r) for r in conn.execute(UNSETTLED_FAILS_SQL, (task_id,))]
    if not exclude:
        return rows
    return [r for r in rows if not (r[2] and excluded(r[2], exclude))]

#: The row a sweep's findings hang from.
#:
#: `claim.task_id` is `NOT NULL REFERENCES task(id)` and a periodic sweep has no
#: task -- `v4 review lens` says so in as many words and was fixed to record
#: without one, and three lines later `review add` still joined a `None` into
#: `hashing.claim_id`. Measured on one adopter: nine lenses ran, the sweep
#: recorded 61 findings, and `claim WHERE kind = 'review-finding'` held zero.
#:
#: A row rather than a nullable column, and not only to avoid rebuilding a table
#: in an append-only ledger. `claim_id` mixes the task in because the ledger is
#: shared across worktrees, so two tasks touching one call site do not collapse
#: into one claim. For a sweep that reasoning inverts: the same finding, found
#: again next sweep, *should* be the same claim rather than a new one every four
#: days. A constant gives it exactly that.
REVIEW_TASK = "repo-review"

#: A subquery for "tasks that have ended", for `id NOT IN (...)`.
#:
#: The review row is never open work: it takes no worker, ships nothing, and a
#: hook that guarded it would guard every repo forever. It is excluded here so
#: `open_task_id`, the write hook and `doctor` all get the same answer from the
#: one place that defines it.
ENDED_TASKS_SQL = (
    "SELECT task_id FROM event WHERE kind IN "
    + "(" + ", ".join(f"'{k}'" for k in ENDED_KINDS) + ") "
    + "AND task_id IS NOT NULL"
    + f" UNION ALL SELECT '{REVIEW_TASK}'"
)


def open_task_ids(conn):
    """Every task that has neither shipped nor been abandoned, newest first.

    More than one is the case the hooks cannot infer their way out of: the
    ledger is shared across worktrees, and a task whose work is finished but
    which nobody ran `ship` on stays open forever. Measured on one adopter,
    twice in a day -- and both times the guard silently moved to the newer task,
    so a write meant for the older one would have been checked against a scope
    that was not its own, with nothing saying so.
    """
    return [r[0] for r in conn.execute(
        f"SELECT id FROM task WHERE id NOT IN ({ENDED_TASKS_SQL}) "
        f"ORDER BY rowid DESC")]


def sessions_on(conn, task_id, session):
    """`(marks that name a session, marks naming this one)` for `task_id`.

    Which session did the work is a question the ledger could not answer at all
    until `hook_seen` started carrying it, and the caller that needs it is a
    hook: `stop_gate` resolves a task from the repo, and a repo is not a
    session, so it asked whoever stopped. The count of *tagged* marks is
    returned beside the match because zero of them means "nothing here can tell
    us apart" -- a task from before the field existed -- and that is a different
    answer from "somebody else's".

    Here rather than in the hook, for the reason its two siblings are here:
    `_open_task` and `_is_open` ask this module and hand-write nothing. The
    first draft of the caller ran its own `conn.execute` inside a `try`, which
    `fail-closed` reads as an outbound call under a swallowing guard -- and it
    was right that the query did not belong there.

    A `scattered` mark does not count, on either side of the pair. Two hooks
    write a row against *every* open task when nothing says which one the work
    was for -- `bash_guard`, because a shell command names no task, and
    `write_block`'s `AMBIGUOUS` branch, because a write with two tasks open
    names no task either -- and both are right to record rather than guess.
    Counting them here turned that honesty into its opposite: one `Bash` call
    tagged this session onto every open task, so `stop_gate._worked_here` then
    said the session had worked on all of them. Measured: three tasks open, one
    session, and the gate stood behind a task it had never touched.

    Excluded from `tagged` as well as from `mine`, because a task carrying only
    scattered marks genuinely has nothing that tells two sessions apart, and
    that is the answer `tagged == 0` already means.
    """
    row = conn.execute(
        "SELECT count(*), sum(json_extract(payload, '$.session') = ?) "
        "FROM event WHERE task_id = ? AND kind = 'hook_seen' "
        "  AND json_extract(payload, '$.session') IS NOT NULL"
        "  AND coalesce(json_extract(payload, '$.scattered'), 0) = 0",
        (session, task_id)).fetchone()
    return row[0] or 0, row[1] or 0


def open_task_id(conn):
    """The newest task that has neither shipped nor been abandoned, or None.

    What the hooks guard when `V4_TASK` is unset, and what `doctor` reports.
    Callers that must not guess ask `open_task_ids` and refuse on more than one.
    """
    ids = open_task_ids(conn)
    return ids[0] if ids else None
