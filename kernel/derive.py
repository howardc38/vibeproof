"""Turning a repo into a set of claims.  SPEC.md §4.

This is the step the whole design rests on. V3 derived obligations from prose,
so editing the prose to fix one failure changed the obligation set and produced
the next failure -- nine rounds of that on one phase, two and a half hours, no
code. V4 derives from what is actually in the repo, and the properties that
keep it from becoming the same loop are all here:

  identity excludes line numbers   adding an import above a function does not
                                   mint a new claim and orphan the old one
  the kernel writes subject_refs   a detector naming its own subject would be
                                   choosing which bytes its answer is checked
                                   against
  the question comes from a template   nobody writes the sentence a claim is
                                   judged against
  re-derivation is idempotent      the same repo yields the same claim ids, so
                                   rescanning is free and never duplicates
"""

import ast
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import detector_protocol, hashing, ledger as ledger_mod
from .analysis import subject_files as subject_files_mod
from .ledger import insert

#: The detector contract lives in its own module because `register` needs it
#: too, and reaching for it here made the two modules import each other.
#: Re-exported under the names callers already use.
CLAIM_LINE = detector_protocol.CLAIM_LINE
FIELD = detector_protocol.FIELD
ALLOWED_FIELDS = detector_protocol.ALLOWED_FIELDS
DeriveError = detector_protocol.DeriveError
parse_claim_lines = detector_protocol.parse_claim_lines
run_detector = detector_protocol.run_detector
UNCONDITIONAL = detector_protocol.UNCONDITIONAL



#: The half of a facts table that *narrows* what a detector looks at. Removing
#: these is the control the narrowing guard compares against -- the vocabulary
#: stays, because a repo listing its own boundary symbols is being specific, not
#: being disarmed.  FACTS.md draws the same line.
#: Keys whose globs decide *what a detector looks at*. A dead one there means the
#: detector scanned less than the repo asked for and said nothing about it, which
#: is why a dead one refuses the detector outright.
#:
#: `protected_paths` was in this list and is not one of them. Nothing reads it to
#: narrow a scan -- it is the list `scope` refuses to let a worker touch, and a
#: dead entry in it hides nothing from anybody. Measured on a real adopter: five
#: V3 leftovers there (`kernel/**`, `auto-dev/**`, `.autodev/**`, and two more)
#: refused `external_write`, `route_auth` and `bundle_secret` on every task. Two
#: of those are security detectors, and they were silently not running because of
#: stale entries in a key they never read.
#:
#: A dead `protected_paths` entry is still worth knowing about. It belongs in
#: `v4 doctor`, as tidying, not here as grounds to distrust a detector.
#:
#: And `public_routes` was in it, for the second time the same mistake was made
#: one key over. Its values are `file::symbol` -- FACTS.md calls them "this
#: handler is deliberately unauthenticated" -- so `fnmatch(path, "a/b.py::f")`
#: is false for every file that exists, and every entry read as dead. Measured
#: on the reference adopter: two live exemptions, both symbols present in the
#: file they name, refusing seven detectors on every task -- `bundle_secret`,
#: `dal_write`, `external_write`, `route_auth`, `runtime_proof`,
#: `surface_proof` and `webhook_replay`.
#:
#: It does not narrow anything either way. An exemption that names a symbol
#: nobody defines exempts nothing, so a stale one hides no code from any scan.
FILTER_KEYS = ("entrypoint_globs", "ui_globs", "dal_globs")

#: Path lists that no detector reads. Reported, not acted on.
UNSCANNED_PATH_KEYS = ("protected_paths",)

#: `file::symbol` references, not globs. Checked for the file half, reported
#: rather than acted on, for the same reason as `protected_paths`.
SYMBOL_REF_KEYS = ("public_routes",)

#: Reading the table, as opposed to declaring the flag that carries it.
#: Prefix on the one entry that is not a dead glob. The caller refuses the
#: detector either way -- it cannot know the table is honest -- but the reason
#: it prints has to be the true one.
_UNREADABLE_PREFIX = "the tracked file list could not be read: "


def _filters_matching_nothing(root: Path, facts) -> list:
    """Globs in a facts table that match no tracked file.

    A repo declaring `[]` is saying the surface does not exist here, which
    FACTS.md keeps distinct from an omission; that is not this.  This is a glob
    that names something -- and names nothing.
    """
    tracked = subprocess.run(["git", "ls-files"], cwd=root,
                             capture_output=True, text=True)
    if tracked.returncode != 0:
        # `[]` here reads as "no dead globs" and lets every facts-reading
        # detector through, which is the same answer as a clean table -- in the
        # guard whose whole job is that "a filter that matches nothing turns a
        # detector off and leaves the ship report saying it ran". A git failure
        # is not a clean table, and saying so costs one line.
        return [_UNREADABLE_PREFIX
                + (tracked.stderr.strip().splitlines() or ["git ls-files failed"])[0][:160]]
    files = [f for f in tracked.stdout.split("\n") if f.strip()]
    # The same matcher the rest of the system uses to decide whether a path is
    # covered by a glob. Its own spelling here was a second one, and a facts
    # glob is called dead on the strength of this answer: a `**/*.ts` entry
    # that `matches` says covers a tracked file, and this did not, turns a
    # declared surface into a reported omission.
    dead = []
    for key in FILTER_KEYS:
        for glob in facts.get(key) or []:
            if not isinstance(glob, str):
                continue
            if not any(subject_files_mod.matches(f, [glob]) for f in files):
                dead.append(f"{key}: {glob}")
    return dead


#: How the parsed value of `--facts` is read.  Every detector declares the flag
#: -- the contract requires it -- so declaring says nothing; consuming does.
#:
#: Two earlier criteria were wrong in opposite directions. Searching the raw
#: text for `facts.get(` counted a docstring and missed `f = facts`, which is
#: the method four of this repo's own bypass fixtures were written against.
#: Seeding an AST walk with likely aliases (`table`, `f`) made every `f.read()`
#: in the tree a hit -- a guess about what a variable is probably called, which
#: is the same mistake, wider.
#:
#: The decidable question is whether `args.facts` is read anywhere outside the
#: `add_argument` that declares it. Measured across this repo's nine detectors:
#: three read it, six declare it and never touch it.
_FACTS_ATTR = "facts"


#: Receivers whose `.facts` is the object's own field rather than the parsed
#: `--facts` value.
_OWN_RECEIVERS = frozenset({"self", "cls"})


def _touches_facts(src: str) -> bool:
    """Does this detector consume `--facts`, or only declare it?

    The verdict decides whether the empty-glob guard applies to this detector --
    that is, whether a broken facts table can quietly switch it off. `pysource`
    exists to answer questions like this with an AST, and this module already
    imports from it.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False

    # Every `--facts` in an `add_argument(...)` call. Those are declarations.
    declared = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add_argument":
            for sub in ast.walk(node):
                declared.add(id(sub))

    for node in ast.walk(tree):
        if id(node) in declared:
            continue
        # `a.facts` / `args.facts` -- the parsed value, whatever the namespace
        # was named. A detector that reads it is one whose behaviour the table
        # changes.
        #
        # Not `self.facts`: that is an object's own field, and the receiver
        # names which. `kernel/config.py` counted as facts-reading on the
        # strength of `self.facts` -- the attribute this rule is about is the
        # one argparse put on a namespace, and a method reading its own is a
        # different sentence with the same spelling.
        if isinstance(node, ast.Attribute) and node.attr == _FACTS_ATTR \
                and isinstance(node.value, ast.Name) \
                and node.value.id not in _OWN_RECEIVERS:
            return True

    # And the other half of the same act, one import away: a function that takes
    # the table as a parameter and reads it. `_reads_facts` follows a detector's
    # same-repo imports, and the module it lands in never sees `args` -- it sees
    # `def scan(root, facts)`.
    #
    # A parameter name is a declaration in the AST, not a guess about what a
    # variable is probably called, which is what the alias-seeded version got
    # wrong.
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = {a.arg for a in list(fn.args.args) + list(fn.args.kwonlyargs)}
        if _FACTS_ATTR not in params:
            continue
        for node in ast.walk(fn):
            reads = (isinstance(node, ast.Subscript) and node.value
                     or isinstance(node, ast.Attribute) and node.value
                     or None)
            if isinstance(reads, ast.Name) and reads.id == _FACTS_ATTR:
                return True
            if isinstance(node, ast.Compare) and any(
                    isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
                if any(isinstance(c, ast.Name) and c.id == _FACTS_ATTR
                       for c in node.comparators):
                    return True
    return False


def reads_facts(det_path, root=None) -> bool:
    """Does this program's answer depend on the table?

    Public, and named for the question rather than for the caller: `config.
    facts_sha_for` asks it about *checkers* and used to reach into this module
    for `_touches_facts` -- a private name, and the textual half rather than
    this one. Measured: the two disagree about nine of this repo's registered
    checkers, every one of them reaching the table through
    `kernel/analysis/`, and `facts_sha_for` took the answer that says no. Those
    nine recorded `facts_sha = ""`, so editing the table expired none of them.

    Follows imports, because the textual version did not and that left the
    narrowing guard watching one detector out of three. `bundle_secret.py` reads
    the table in its own file; `external_write` and `route_auth` reach it
    through `kernel/analysis/`, and a scan of the detector's own text sees
    neither. The guard exists so a table cannot turn a detector off quietly,
    and it was not watching two of the three detectors a table can turn off.

    One level of local imports is enough today and the visited set makes deeper
    nesting free. Imports are resolved to files in this repo; a stdlib name has
    no source here and is skipped.
    """
    root = Path(root) if root else Path(det_path).resolve().parent.parent
    seen, queue = set(), [Path(det_path)]
    while queue:
        path = queue.pop()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            src = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if _touches_facts(src):
            return True
        from .analysis import pysource
        for mod in pysource.imported_modules(src):
            rel = Path(*mod.split("."))
            for cand in (root / rel.with_suffix(".py"),
                         root / rel / "__init__.py"):
                if cand.is_file():
                    queue.append(cand)
    return False


def subject_refs_for(kind_cfg, *, file, task_claims):
    """The kernel decides what a claim is checked against.  Never the detector.

    A `surface` claim depends on a `runtime` observation, and no file hash can
    express that: re-running runtime changes no file. So a kind may declare
    `depends_on_kind`, and the reference is to that claim's latest attempt.
    """
    refs = []
    if file:
        refs.append({"kind": "file", "path": file})
    dep = kind_cfg.get("depends_on_kind")
    if dep:
        for cid, ckind in task_claims:
            if ckind == dep:
                refs.append({"kind": "attempt", "claim": cid})
                break
    return refs


def _redact(text: str, root=None) -> str:
    """`runner.redact`.

    A detector's stderr went into an event payload raw, and
    `detector_protocol.run_detector` returns `proc.stderr` as it came --
    unlike the checker path, where `runner._run_contained` redacts both
    streams. Those events are exported into `.v4/ledger_export.jsonl`, which is
    committed and scanned. Same class of output, two paths, only one redacted.
    """
    from .analysis.redaction import redact
    return redact(text or "", root)


def _redact_json(value, root=None):
    """`redaction.redact_json`, for the structured half of the same output.

    A detector's `--out` payload lands in the same append-only table as its
    stderr, so it takes the same filter. Separate from `_redact` above only
    because one takes text and the other takes parsed JSON -- both call the
    module that owns the patterns, neither carries its own.
    """
    from .analysis.redaction import redact_json
    return redact_json(value, root=root)


#: A claim the newest detector raised again.  The row cannot be updated, so the
#: fact that these bytes also raise it is an event.
RERAISED_KIND = "claim_reraised"


def derive(conn, cfg, *, task_id, scope_globs, subject_files, phase, detectors_dir=None):
    """Run every detector, turn its output into claims.  Idempotent by construction.

    `phase` is open | widen | ship and is only recorded -- the same full scan
    runs at all three. rev 1 scanned incrementally after a widen, which bought
    what idempotent ids already give away for free, on a second code path.
    """
    # The review row is a container, not a unit of work. It has no diff base,
    # its scope is `**` because a finding can be anywhere, and nobody writes
    # code under it -- so every claim a detector raises there asks "is this
    # change safe" about no change.
    #
    # Measured here: somebody ran `v4 derive` against it once and left 32 open
    # claims, 3 findings and 29 of `fail-closed`, `test`, `lint` and the rest.
    # Those 29 also spent the task's three re-derive rounds, so `v4 ship
    # --task repo-review` has refused ever since and cannot be recovered --
    # the ledger is append-only, which is the point.
    #
    # Empty rather than an error: `ship` re-derives, and a task that derives
    # nothing converges on the first round, which is the right answer for a
    # container. Findings close one at a time through `v4 review close`.
    if task_id == ledger_mod.REVIEW_TASK:
        # Detectors do not run here, and orphans still do. A finding about a
        # file that has since been deleted is exactly the container's business:
        # `v4 review close` needs the file to compare against, so a finding
        # whose subject is gone has no closure available and no way out but a
        # signature saying the code is gone -- which this function's own
        # comment calls a rename that nobody should have to sign for. Returning
        # `retracted: []` here meant the repair one screen up could not reach
        # the 25 claims it was written for.
        with ledger_mod.writing(conn):
            orphans = _retract_orphans(
                conn, cfg.root, task_id, set(), set(),
                datetime.now(timezone.utc).isoformat())
        return {"created": [], "refused": [], "detectors_ran": {},
                "seen": set(), "retracted": orphans}

    root = cfg.root
    detectors_dir = Path(detectors_dir or root / "detectors")
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute("SELECT base_commit FROM task WHERE id = ?",
                       (task_id,)).fetchone()
    base = (row["base_commit"] if row else "") or ""

    rows = conn.execute(
        "SELECT id, kind, detector_sha FROM claim WHERE task_id = ?",
        (task_id,)).fetchall()
    existing = {r["id"]: r["kind"] for r in rows}
    #: The newest detector sha known to have raised each claim: the column, plus
    #: any `claim_reraised` since. `state._staleness_key` reads the same pair.
    existing_sha = {r["id"]: (r["detector_sha"] or "") for r in rows}
    for r in conn.execute(
            "SELECT claim_id, payload FROM event WHERE task_id = ? AND kind = ? "
            "ORDER BY id", (task_id, RERAISED_KIND)):
        try:
            existing_sha[r["claim_id"]] = json.loads(r["payload"])["sha"]
        except (ValueError, TypeError, KeyError):
            continue
    created, refused, ran = [], [], []
    seen = set()          # every id the detectors emitted this round
    healthy = set()       # detectors that actually completed

    registry = cfg.detectors

    def _did_not_run(name, why, detail=""):
        """Refusal on the record, then in the returned list.

        Three refusal paths -- unregistered, sha changed, unparseable output --
        used to append to `refused` and `continue` without writing an event, and
        `detector_coverage` is built only from `detector_run` rows. So a detector
        that never ran was absent from the coverage dict entirely, `ship`'s
        `detectors_not_run` came back empty, and `v4 ship` printed
        `NOT RUN: []  <- recorded, not assumed clean` for a task where the thing
        that decides what gets checked had been refused. `lifecycle.ship` reads
        `res["created"]` and discards `res["refused"]`, so this is the only
        surface that could carry it.
        """
        insert(conn, "event", task_id=task_id, claim_id=None, kind="detector_run",
               actor="kernel",
               payload={"detector": name, "sha": hashing.file_sha(detectors_dir / name)
                        if (detectors_dir / name).is_file() else "",
                        "phase": phase, "ran": False, "refused": why,
                        "stderr": _redact(str(detail), root)[:2000]},
               created_at=now)
        refused.append((name, why, detail))

    # What the subject actually held, recorded once and attached to every
    # `detector_run` row.
    #
    # `(ran=True, claims=0)` had one meaning and four causes: the tree is clean,
    # the detector's word list did not match, the facts table names none of
    # these symbols, or nothing here is in a language `kernel/analysis/` can
    # parse. Measured on the reference adopter: 2,842 of 3,502 detector runs are
    # that row, and no field on it separates them. Measured on a TypeScript and
    # Go fixture with no `.py` at all: 7 claims from 11 detectors, and `v4 ship`
    # printed SHIP.
    #
    # This does not close the first two -- a word list that missed is the
    # checker's own business and is not visible from here. It closes the fourth,
    # which is the one that scales with adoption and the one SPEC.md §2 already
    # names: "change the suffix set from `.py` to `.pyx` and every task passes,
    # while the ship report keeps printing that it ran."
    #
    # This counted `.py` and read zero of them as "nothing here can be parsed".
    # That was true when `kernel/analysis/` parsed one language. It stopped
    # being true on 2026-08-22, and the failure it produced is the exact shape
    # this field exists to prevent: on a Go repo every detector that raised
    # nothing this round was listed as "not asked", including `fail_closed.py`
    # -- whose claim was printed as blocked eight lines below it, in the same
    # report.
    #
    # The old comment said the kernel cannot know which detectors read what and
    # that asking each one would be 28 files edited. Neither is true: every
    # checker declares `reads` in `.v4/checkers.json`, `claim_kinds.json` maps
    # a detector to its kind, and `subject_files.readable` is already the
    # function that answers it. So the question is asked per detector, from the
    # registry, and the answer is a fact rather than a proxy for one.
    considered = {"files": len(subject_files)}

    def _could_read(detector_name):
        """Did this detector's subject hold a file its kind can read.

        `None` means no verdict -- an unregistered detector, or a kind that
        declared no `reads` -- and the caller must not read `None` as "no".
        """
        globs = []
        for kind_name, kind_row in (cfg.kinds or {}).items():
            if (kind_row or {}).get("detector") != detector_name:
                continue
            # By checker id, which is what `.v4/checkers.json` is keyed on.
            # It matches the kind name for every kind here, and reading the
            # kind name directly would be a coincidence this file relies on.
            declared = cfg.reads_for((kind_row or {}).get("checker") or kind_name)
            if declared is None:
                return None            # declared nothing: might read anything
            globs += list(declared)
        if not globs:
            return None
        return subject_files_mod.readable(subject_files, globs)

    empty_globs = None
    for det in sorted(detectors_dir.glob("*.py")):
        if det.name.startswith("_"):
            continue
        det_sha = hashing.file_sha(det)

        # The gate, enforced. `v4 verify-detector` existed and its verdict went
        # nowhere, so the spec's requirement that a conditional detector pass it
        # had nothing behind it -- every file in `detectors/` ran regardless.
        # Same shape as a checker's exit 6: the bytes that run are the bytes
        # that were gated, or they do not run.
        #
        # `always_*` is exempt and says why in SPEC.md §2: one fixed line, no
        # dependence on the tree, so "should not fire" cannot be written as a
        # fixture. What gates that kind is its checker.
        if not det.name.startswith(detector_protocol.UNCONDITIONAL):
            entry = registry.get(det.name)
            if entry is None:
                _did_not_run(det.name, "unregistered",
                             f"{det.name} has never passed `v4 register-detector`. "
                             f"A detector decides what gets checked at all, so an "
                             f"ungated one is a silent decision about coverage.")
                continue
            if entry.get("sha256") != det_sha:
                _did_not_run(det.name, "detector-changed",
                             f"{det.name} on disk is not the registered one. "
                             f"Re-run `v4 register-detector` so the bytes that "
                             f"choose the claims are the bytes that were gated.")
                continue

        # The task's base, not HEAD. Three detectors read this and all three
        # fall back to `HEAD` when it is absent, which is "what is uncommitted
        # right now" -- so everything a worker had already committed inside the
        # task was invisible to them. `test-weakened` raised 0 claims across 36
        # tasks on the reference adopter.
        rc, stdout, stderr, det_out, det_ms = run_detector(
            root, det, subject_files, cfg.facts or None, diff_base=base,
            # What a checker's subject has carried all along. A detector that
            # sweeps past its subject files needs it for the same reason.
            params={"derive_exclude": cfg.config.get("derive_exclude", [])})

        # A filter that matches nothing turns a detector off and leaves the
        # ship report saying it ran.  `detectors/bundle_secret.py` does
        # `facts.get("ui_globs") or [defaults]`: point `ui_globs` at a directory
        # that does not exist and it raises nothing, forever.
        #
        # **Checked on the filters themselves, not by running the detector
        # twice.**  The two-run version compared "with your table" against
        # "with no table", and lost: it read every legitimate narrowing as an
        # attack.  Measured, the claims it called hidden were the detector
        # scanning `tests/` -- `entrypoint_globs` doing exactly its job -- and
        # `external_write` was refused on every round of two whole runs of the
        # three-arm experiment for that reason.  It also punished any repo whose
        # own vocabulary is shorter than the 26-pattern built-in default, which
        # is every repo that tailored one.
        #
        # A glob that matches no tracked file is the failure, and it is a
        # question about the table, not about a detector's output.
        if rc == 0 and cfg.facts and reads_facts(det):
            if empty_globs is None:
                # Once, not once per detector. It runs `git ls-files` and then
                # fnmatches every glob in `FILTER_KEYS` against every tracked
                # file -- three keys, and this comment said five for as long as
                # `public_routes` and `protected_paths` were reported rather
                # than acted on,
                # and it does not depend on `det` -- so on this repo that was 19
                # detectors x 1,929 files, and `derive` runs up to four times
                # before ship.
                empty_globs = _filters_matching_nothing(root, cfg.facts)
            empty = empty_globs
            if empty:
                insert(conn, "event", task_id=task_id, claim_id=None,
                       kind="facts_narrowed_detection", actor="kernel",
                       payload={"detector": det.name, "empty_globs": empty,
                                "n_lost": len(empty)},
                       created_at=now)
                # Through `_did_not_run`, so it lands in `detector_run` as
                # well. This branch wrote the narrowed event and `continue`d,
                # and `detector_coverage` selects only `kind = 'detector_run'`
                # -- so a detector switched off because a facts glob matches no
                # tracked file appeared in neither `rep["detectors"]` nor
                # `rep["detectors_not_run"]`, and `v4 ship` printed no line
                # about it at all. That is the hole `_did_not_run` documents
                # having been written to close, left open one branch over, and
                # SPEC.md §4 says this path must be listed as did-not-run.
                _did_not_run(det.name, "facts-narrowed",
                             f"{len(empty)} glob(s) in the facts table match no "
                             f"tracked file, so anything behind them is invisible "
                             f"and nothing says so: {empty[:3]}")
                continue

        # Only 0 means "scanned". rev 1 left 1 and 2 undefined, so a detector
        # that crashed with exit 1 read as "scanned, found nothing" -- failing
        # open, silently, in the one place that decides what gets checked.
        if rc != 0:
            insert(conn, "event", task_id=task_id, claim_id=None, kind="detector_run",
                   actor="kernel",
                   payload={"detector": det.name, "sha": det_sha, "phase": phase,
                            "ran": False, "exit": rc, "stderr": _redact(stderr, root)[:2000]},
                   created_at=now)
            refused.append((det.name, rc, stderr.strip()[:200]))
            continue

        try:
            fields_list = parse_claim_lines(stdout)
        except DeriveError as exc:
            _did_not_run(det.name, "bad-output", str(exc))
            continue

        emitted, dropped = 0, 0
        for f in fields_list:
            kind = f["kind"]
            try:
                kind_cfg = cfg.kind(kind)
            except Exception as exc:                            # noqa: BLE001
                refused.append((det.name, "unknown-kind", str(exc)))
                dropped += 1
                continue

            file = f.get("file", "")
            # `subject_files.matches`, not a private matcher. This was the
            # seventh copy of that glob rule and it disagreed with the one the
            # kernel already uses to pick the files a detector is pointed at:
            # measured, `matches("cli.py", ["**/*.py"])` is True and the copy
            # here answered False. So with a scope of `**/*.py` --
            # `lifecycle._files_in_scope` handing over every root-level module
            # -- every claim a detector raised about one of them was dropped
            # here as out of scope, by the half of the pair that never saw the
            # `**/x` rule git already means.
            if file and not subject_files_mod.matches(file, scope_globs):
                refused.append((det.name, "out-of-scope", f"{kind} {file}"))
                dropped += 1
                continue

            cid = hashing.claim_id(task_id, kind, file, f.get("symbol", ""),
                                   f.get("variant", ""))
            seen.add(cid)
            if cid in existing:
                # Idempotent by id -- and the row keeps the sha of whichever
                # detector version first raised it. `state._staleness_key`
                # compares that against the registry, so editing a detector
                # left every claim it had ever raised permanently STALE: the
                # re-derive raises the same id, the row cannot be updated, and
                # no amount of answering ever matches again. Measured on this
                # task: 14 `fail-closed` claims, answered, PASS, and stale
                # forever after the detector learned to read a repo's own
                # vocabulary.
                #
                # An append rather than an update, because that is the only
                # thing this ledger takes. It records the fact that matters:
                # these bytes raised this claim too.
                if (existing_sha.get(cid) or "") != det_sha:
                    insert(conn, "event", task_id=task_id, claim_id=cid,
                           kind=RERAISED_KIND, actor="kernel",
                           payload={"detector": det.name, "sha": det_sha,
                                    "was": existing_sha.get(cid) or ""},
                           created_at=now)
                    existing_sha[cid] = det_sha
                continue                       # idempotent: same repo, same ids

            refs = subject_refs_for(kind_cfg, file=file,
                                    task_claims=list(existing.items()))
            insert(conn, "claim", id=cid, task_id=task_id, kind=kind,
                   question=cfg.question(kind, file=file, symbol=f.get("symbol", ""),
                                         variant=f.get("variant", ""),
                                         line=f.get("line")),
                   subject_refs=refs, checker=kind_cfg["checker"],
                   origin="derive",
                   file=file or None, symbol=f.get("symbol") or None,
                   variant=f.get("variant") or None,
                   line=int(f["line"]) if f.get("line", "").isdigit() else None,
                   note=f.get("note") or None,
                   detector=det.name, detector_sha=det_sha, created_at=now)
            existing[cid] = kind
            created.append((cid, kind, file, f.get("symbol", "")))
            emitted += 1

        insert(conn, "event", task_id=task_id, claim_id=None, kind="detector_run",
               actor="kernel",
               payload={"detector": det.name, "sha": det_sha, "phase": phase,
                        "ran": True, "exit": 0, "claims": emitted,
                        # What this detector raised and this round threw away.
                        # Without it, a detector whose every claim was dropped
                        # for scope records `claims: 0` -- the same row as one
                        # that scanned and found nothing -- and `ship` reads
                        # this table, not the in-memory `refused` list the
                        # caller discards.
                        "dropped": dropped,
                        "considered": considered,
                        # What the detector wrote to `--out`. `run_detector`
                        # parses it and says in its own comment "Returned
                        # rather than stored, so the caller that owns the
                        # ledger decides" -- and this caller bound it as
                        # `det_out` and decided nothing: one occurrence in the
                        # whole repo, the binding itself. Fourteen detectors
                        # write structured findings into a temp file that is
                        # deleted with the directory. `runner.record` keeps the
                        # checker equivalent as `checker_out` and its comment
                        # names the alternative: "the kernel parsed this, held
                        # it in memory, and dropped it".
                        #
                        # Redacted like every other payload that reaches this
                        # table, and only when there is one -- an absent key is
                        # cheaper to read than a null on every row.
                        **({"out": _redact_json(det_out, root)}
                           if det_out is not None else {}),
                        # What it cost. The checker half has carried this since
                        # `runner.record` was written -- `duration_ms` on the
                        # attempt and a `cost_observation` row -- and this row
                        # had nothing, so the timeout every detector runs under
                        # was a number with nothing to compare it to.
                        "duration_ms": det_ms,
                        "could_read": _could_read(det.name)},
               # This detector's own instant, not the one taken before the
               # loop. 20,062 `detector_run` rows in this ledger carry 951
               # distinct timestamps -- one per `derive`, with every detector of
               # a round stamped identically -- so the table could not say which
               # ran first, or where in a round the time went.
               created_at=datetime.now(timezone.utc).isoformat())
        ran.append(det.name)
        healthy.add(det.name)

    retracted = _retract_orphans(conn, root, task_id, seen, healthy, now)
    return {"created": created, "refused": refused, "detectors_ran": ran,
            "seen": seen, "retracted": retracted}


def _retract_orphans(conn, root, task_id, seen, healthy, now):
    """Claims the detectors no longer raise, and whose files are gone.

    Without this, `git mv` on an in-scope file is fatal: the old claim's subject
    stops existing, its checker reports it cannot verify, and UNSUPPORTED is
    deliberately not terminal -- so the task is held forever with no command
    that clears it. Renaming a file is not a risk anybody should have to sign
    for.

    Two guards, and the second is the important one:

      the claim's detector must have completed this round. A detector that
      crashed emits nothing, and treating silence as "no longer applies" would
      let a broken detector retract every claim it owns and ship a clean task.

      every file the claim is about must be gone. A claim the detector merely
      stopped raising -- because someone narrowed a rule -- stays, because the
      code it is about is still there to be judged.
    """
    rows = conn.execute(
        "SELECT * FROM claim WHERE task_id = ?", (task_id,)).fetchall()
    out = []
    for row in rows:
        if row["id"] in seen:
            continue
        # Already retracted stays retracted, and does not get said twice. The
        # row is terminal after the first pass -- `state.claim_state` returns
        # RETRACTED on that one event -- so re-inserting it changes no verdict
        # and grows an append-only table by one row per `derive`. It went
        # unnoticed while nothing reached this branch for a review finding;
        # wiring the container into it made every `v4 derive --task
        # repo-review` write twenty-five more.
        if conn.execute("SELECT 1 FROM event WHERE claim_id = ? AND "
                        "kind = 'retracted' LIMIT 1", (row["id"],)).fetchone():
            continue
        # A claim with no detector passes the detector guard, because the guard
        # is about a detector that crashed and there is none to crash. That
        # reading was missing, so `None not in healthy` was true for every
        # `review-finding` and this function -- whose own comment says
        # "Renaming a file is not a risk anybody should have to sign for" --
        # could not reach the claims where a file had been renamed away.
        # Measured: 21 open findings about `dep_provenance.py`,
        # `sweep_current.py`, `dal_write.py` and six other modules removed at
        # `a9ae5fb`, each one closable only by a signature saying the code is
        # gone.
        #
        # Only the `gone` route for those. `settled` is about a detector that
        # was narrowed until it stopped raising something, which is a sentence
        # about a detector; a claim without one cannot be in that state, and
        # letting it through there would retract a live finding whose file is
        # still on disk.
        detector_less = not row["detector"]
        if not detector_less and row["detector"] not in healthy:
            continue
        refs = json.loads(row["subject_refs"])
        files = [r["path"] for r in refs if r.get("kind") == "file"]
        gone = bool(files) and not any((Path(root) / f).is_file() for f in files)
        # The second way a claim stops applying, and the one that had no exit.
        # A detector that is narrowed -- a false-positive rule repaired -- stops
        # raising claims it used to raise, and those claims keep the sha of the
        # version that raised them, which `state` compares against the registry.
        # So they read STALE forever: the re-derive does not raise them, the row
        # cannot be updated, and answering them again changes nothing. Measured
        # here: 14 `fail-closed` claims, every one already PASS, held a task
        # open after the rule that raised them learned it had been wrong.
        #
        # Only where the checker has already said PASS. A claim that is open, or
        # that failed, keeps holding the task -- otherwise editing a detector
        # until it stops raising something would be a way to make a finding go
        # away, which is the move this whole gate exists to refuse.
        answered = conn.execute(
            "SELECT exit_code FROM attempt WHERE claim_id = ? "
            "ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
        settled = bool(answered) and answered["exit_code"] == 0 and not detector_less
        if not (gone or settled):
            continue
        insert(conn, "event", task_id=task_id, claim_id=row["id"], kind="retracted",
               actor="kernel",
               payload={"reason": ("subject files no longer exist and the detector "
                                   "that raised it completed without raising it")
                                  if gone else
                                  ("the detector that raised it completed without "
                                   "raising it, and the checker had already "
                                   "answered PASS"),
                        "files": files, "detector": row["detector"]},
               created_at=now)
        out.append((row["id"], row["kind"], files,
                    "the code it was about is gone" if gone else
                    "the rule that raised it no longer does, and it had already "
                    "passed"))
    return out


def detector_coverage(conn, task_id):
    """Which detectors ran for this task, and which did not.

    `v4 ship` prints this. A record nobody reads is the same hollow gesture as
    a severity field nobody branches on -- SPEC.md §4 says so about V3, and
    it applies here.
    """
    rows = conn.execute(
        "SELECT payload FROM event WHERE task_id = ? AND kind = 'detector_run' ORDER BY id",
        (task_id,)).fetchall()
    seen = {}
    for r in rows:
        p = json.loads(r["payload"])
        seen[p["detector"]] = p
    return seen
