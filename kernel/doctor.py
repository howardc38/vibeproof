"""Is this repo actually wired, or does it only look wired?  SPEC.md §13.

Everything here is a thing that was true of this repo at some point today while
nothing said so:

  the hook file existed and no settings file called it, so every ship report
  printed DEGRADED for a hook that had never fired

  the ledger predated a schema change, so every `v4 check` died on insert while
  the whole test suite stayed green

  CI named an audit it could not run, because the ledger lives in `.git/` and a
  clone does not carry it

  a facts file was looked for under one name and shipped under another, so the
  table was always empty and the detectors that depend on it found less without
  saying so

The predecessor had 4,317 lines of adoption tooling and its own document
recorded why: a repo that reports itself adopted, and is not, fails silently and
in production. This is the same question in one command.

Nothing here is a gate. It is a report, and every line of it says what to run.
"""

import contextlib
import json
import os
import subprocess
from pathlib import Path

from . import layout as _layout
from . import config

#: `run()` imported this locally, so extracting its sections left every one
#: of them unable to see it. A name a split cannot carry is a name that was
#: keeping the function together.
config_mod = config
# Imported here rather than inside the branch that validates the table. It was
# bound only in that branch and read unconditionally further down, so on the one
# state this report exists to name -- a facts file shipped under a name the
# loader does not prefer -- `v4 doctor` died with NameError instead of saying so,
# and the whole report went with it.
from . import facts as facts_mod
# The reader `derive` uses to decide whether a facts-reading detector may run.
# Imported rather than reimplemented: a glob called dead by two matchers is a
# table that is wrong in one command and right in the other.
from . import derive as derive_mod

OK, WARN, BAD = "ok", "warn", "bad"


#: Suffixes a literal has to end in before this treats it as a path a checker
#: reads. Narrow on purpose: a check that flags a string because it has a slash
#: in it is the alarm nobody reads.
_PATHISH = {".py", ".json", ".jsonl", ".md", ".yml", ".yaml", ".toml",
            ".txt", ".lock", ".cfg", ".ini"}


def _path_literals(source: str):
    """Repo paths a checker names outright.

    Static and partial by construction -- a path built from a variable is
    invisible here, and that is stated rather than papered over. It is enough
    for the shape that matters: a checker that declares one file type and reads
    another.
    """
    import ast as _ast
    from pathlib import PurePosixPath
    out = set()
    try:
        tree = _ast.parse(source)
    except SyntaxError:
        return out
    for node in _ast.walk(tree):
        if not (isinstance(node, _ast.Constant) and isinstance(node.value, str)):
            continue
        v = node.value.strip()
        if ("/" not in v or "://" in v or " " in v or "{" in v or "*" in v
                or v.startswith("http")):
            continue
        if PurePosixPath(v).suffix in _PATHISH:
            out.add(v[2:] if v.startswith("./") else v)
    return out


def _reads_are_honest(root: Path, checkers: dict):
    """Does each checker's `reads` cover what its source names?

    `reads` is not documentation: `subject_files.readable` uses it to decide
    whether a checker runs at all, so a narrow declaration on a checker that
    reads more is a checker skipped on a task it had something to say about.

    Measured here: three of the registered checkers declare one file type and
    read `.v4/*.json` besides. Nothing catches that today, and the narrowing
    this field was added for -- keying staleness on what a checker actually
    reads -- cannot be built on a field nobody verifies.
    """
    import fnmatch as _fn
    bad, seen = [], set()
    for name, entry in sorted(checkers.items()):
        path, reads = entry.get("path"), entry.get("reads") or ["**"]
        if not path or reads == ["**"] or path in seen:
            continue
        seen.add(path)
        try:
            source = (root / path).read_text(encoding="utf-8")
        except OSError:
            continue
        missed = sorted(
            p for p in _path_literals(source)
            if not any(_fn.fnmatch(p, g) or _fn.fnmatch(p, g.rstrip("/") + "/*")
                       for g in reads))
        if missed:
            bad.append((name, missed))
    return bad


@contextlib.contextmanager
def _reports(out, what):
    """A check that raises says so, rather than vanishing from the report.

    Five blocks in `run` were `try: ... except Exception: pass`. A reader
    scanning fifteen lines has no way to notice a missing one, so the sweep
    freshness read, the path-lists and symbol-refs checks, the open-tasks line,
    the unclosed-review-findings line and the deferral reconciliation each
    disappeared silently when they raised -- in the command whose whole subject
    is silent untruth. This file already fixed exactly that shape once, for
    baselines, with the note "Swallowed, `failing` stayed empty ... this printed
    ok from no data at all"; the fix was applied to one of six.

    `BAD` rather than `WARN`: a check that did not run is not a mild finding
    about the repo, it is the absence of a finding, and the report is read as
    though every line in it ran.
    """
    try:
        yield
    except Exception as exc:                                    # noqa: BLE001
        out.append(_c(BAD, what,
                      f"this check did not run: {type(exc).__name__}: {exc}",
                      "the row is here because a missing row is not something "
                      "a reader can see"))


def _reader(root):
    """One read-only connection for the whole report.

    `run` opened the ledger nine times through the *write* door.
    `ledger.connect` creates the file, executes SCHEMA, installs the append-only
    triggers, commits and migrates columns -- all of that, nine times, from a
    command whose entire job is to read; and the one at the sweep check kept no
    reference, so it was never closed either. `ledger.connect_readonly` exists
    beside it and its docstring says `mode=ro` "makes the question and the door
    match", which is exactly what a reader is.

    Cached on the function so every block shares it, and closed once by `run`.
    """
    if getattr(_reader, "_conn", None) is None or _reader._root != str(root):
        _reader._conn = _ro(root)
        _reader._root = str(root)
    return _reader._conn


def _ro(root):
    from . import ledger as _l
    try:
        return _l.connect_readonly(root)
    except Exception:                                           # noqa: BLE001
        # A repo with no ledger yet. The caller's `_reports` block turns the
        # failure into a row rather than a vanished line.
        return _l.connect(root)


def _close_reader():
    conn = getattr(_reader, "_conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:                                       # noqa: BLE001
            pass
    _reader._conn = None


def _c(status, what, detail, fix=""):
    return {"status": status, "what": what, "detail": detail, "fix": fix}


#: Kinds a command inserts rather than a detector emitting them. `v4 review
#: raise` writes `review-finding` straight into the ledger, so it is raisable
#: with no file in `detectors/`. Written down rather than special-cased inline
#: so that adding a second one is a decision somebody makes.
RAISED_BY_A_COMMAND = {"review-finding"}


def _json_or(path, fallback):
    """A registry, or `fallback` when it cannot be read.

    Each check reads what it needs. `run()` used to read `claim_kinds.json`
    once in section 3 and let sections 3c and 5 use the local it left behind,
    which is why the function could not be split without deciding what a
    "section" even was.
    """
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def _check_config(root, out):
    """Is `.v4/config.json` here, does it parse, and does it name a test command?"""
    cfg_path = root / config_mod.CONFIG
    if not cfg_path.is_file():
        out.append(_c(BAD, "config", f"{cfg_path} is missing",
                      "create it with test_command and policy"))
        return out
    try:
        cfg_raw = json.loads(cfg_path.read_text())
    except json.JSONDecodeError as exc:
        out.append(_c(BAD, "config", f".v4/config.json does not parse: {exc}"))
        return out
    missing = [k for k in ("test_command", "policy") if k not in cfg_raw]
    out.append(_c(BAD if missing else OK, "config",
                  f"missing {missing}" if missing else
                  f"test_command = {cfg_raw['test_command']!r}"))



def _check_facts_load_under_the_name_the_loader_prefers(root, out):
    """Is there a facts table under the name the loader prefers, does it validate, and do its rows still cite anything?
    """
    named = sorted(root.glob(".v4/facts*.json"))
    preferred = root / f".v4/facts.{_layout.repo_name(root)}.json"
    if not named:
        # A draft is a different state from nothing: somebody has a starting
        # point and a list of guesses to prune, rather than a blank page and a
        # schema. Reporting both as "no facts" is how the draft went unread.
        draft = sorted(root.glob(".v4/facts*.json.draft"))
        out.append(_c(WARN, "facts",
                      f"{draft[0].name} is drafted and not yet adopted"
                      if draft else "no .v4/facts*.json",
                      (f"prune it against each row's seen_at, set a real `kind`, "
                       f"confirm or replace each AUTO absence line in your own "
                       f"words -- an unconfirmed one holds ship -- "
                       f"then `mv {draft[0].name} {draft[0].name[:-6]}`"
                       if draft else
                       "run `v4 install`, which drafts one from this repo") +
                      ". Until then detectors fall back to a generic vocabulary "
                      "and find less without saying so"))
    elif preferred not in named:
        out.append(_c(WARN, "facts",
                      f"found {[p.name for p in named]}, and the loader prefers "
                      f"{preferred.name}",
                      f"rename to {preferred.name} or confirm the fallback is "
                      f"the one you meant"))
    else:
        try:
            # Kept: `_filters_matching_nothing` reads the table as `derive`
            # hands it over, which is the parsed JSON and not the `Facts`
            # object `load` returns.
            raw_facts = json.loads(preferred.read_text())
            facts_mod.validate(raw_facts)
        except Exception as exc:                                # noqa: BLE001
            out.append(_c(BAD, "facts", f"{preferred.name} did not validate: {exc}"))
            return
        # Schema, then substance. This row said `ok facts ... validates` and
        # stopped there, and `validate` reads the shape of the table -- keys,
        # kinds, match modes -- never whether a row still cites anything that
        # is here. So a table whose every row pointed at a symbol that had been
        # renamed away read exactly like a sound one, in the command whose
        # stated job is telling wired apart from looks-wired. Two rows over,
        # this same report already asks that question of protected paths and of
        # `file::symbol` references; the one table the detectors take their
        # vocabulary from was the one it did not ask it of.
        #
        # Measured 2026-08-27 in the reference adopter: two refactors on one
        # day left rows citing nothing, both while every gate was green, and
        # what would have caught the first is wired into this framework's own
        # CI (`.github/workflows/v4.yml`) -- which `v4 install` does not copy,
        # because an adopter's CI is theirs. So the obligation travelled and
        # the mechanism did not, and this is where it lands instead.
        #
        # `gone_only`: a row whose line moved is a citation the tree advanced
        # past, not a table that is wrong, and `check_seen_at` has said so
        # since it was written. Reported below the verdict, not as one.
        try:
            facts = facts_mod.load(preferred)
            gone = facts_mod.check_seen_at(facts, root, gone_only=True)
            moved = facts_mod.check_seen_at(facts, root)
        except Exception as exc:                                # noqa: BLE001
            out.append(_c(WARN, "facts",
                          f"{preferred.name} validates; whether its rows still "
                          f"cite anything could not be read: {exc}"))
            return
        drifted = len(moved) - len(gone)
        if gone:
            out.append(_c(
                BAD, "facts",
                f"{len(gone)} row(s) cite something that is no longer there: "
                + "; ".join(g[:90] for g in gone[:3])
                + (f" (+{len(gone) - 3} more)" if len(gone) > 3 else ""),
                "v4 facts verify --gone-only names them all. A row citing "
                "nothing narrows every checker that scans against this table, "
                "and narrowing is silent"))
        else:
            out.append(_c(OK, "facts",
                          f"{preferred.name} validates, and every row still "
                          f"cites something that is here"))
        if drifted:
            out.append(_c(WARN, "facts drift",
                          f"{drifted} row(s) match their file at a different "
                          f"line than the one they name",
                          "not a defect -- a citation is true of a commit. "
                          "`v4 facts restate` rewrites the unambiguous ones "
                          "and refuses the rest"))
        # Rows, then filters. The two lines above ask whether a row still cites
        # something; neither asks whether a *glob* still matches something, and
        # a glob that matches nothing does more than narrow a scan -- `derive`
        # refuses the detector outright, `facts-narrowed`, and the kind is not
        # raised on any lane.
        #
        # Reported from an adopter mid-wave: four `ui_globs` carried over from a
        # sibling repo matched no tracked file after the port, so
        # `external_write`, `runtime_proof` and `surface_proof` refused on every
        # derive and no lane had an `external-write` claim -- including the three
        # writing to object storage, exchanging OAuth tokens and sending to a
        # chat API. `v4 ship` did print `NOT RUN` for them. This report, whose
        # whole job is telling wired from looks-wired, printed `ok facts`.
        #
        # `derive`'s own reader, not a second one: a glob called dead by two
        # different matchers is a table that is wrong in one command and right
        # in the other.
        dead = derive_mod._filters_matching_nothing(root, raw_facts)
        unreadable = [d for d in dead if d.startswith(derive_mod._UNREADABLE_PREFIX)]
        if unreadable:
            out.append(_c(WARN, "facts globs",
                          f"whether this table's globs still match anything "
                          f"could not be read: {unreadable[0][:120]}",
                          "the same read `derive` makes before it decides "
                          "whether a facts-reading detector may run"))
        elif dead:
            out.append(_c(
                BAD, "facts globs",
                f"{len(dead)} glob(s) match no tracked file: "
                + "; ".join(dead[:3])
                + (f" (+{len(dead) - 3} more)" if len(dead) > 3 else ""),
                "`derive` refuses every facts-reading detector while one of "
                "these stands -- the kind is not raised at all, on any task. "
                "Prune them or point them at what is here"))
        else:
            out.append(_c(OK, "facts globs",
                          "every filter glob matches something that is here"))



def _check_every_kind_points_at_a_registered_checker_on_disk(root, out):
    """Does every claim kind resolve to a checker that is registered and on disk?"""
    try:
        kinds = json.loads((root / config_mod.CLAIM_KINDS).read_text())
        reg = json.loads((root / config_mod.CHECKERS).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        out.append(_c(BAD, "registry", f"a registry does not read: {exc}"))
        kinds, reg = {}, {}
    bad = []
    for name, spec in sorted(kinds.items()):
        cid = spec.get("checker")
        if cid not in reg:
            bad.append(f"{name} -> {cid} (not registered)")
        elif not (root / reg[cid]["path"]).is_file():
            bad.append(f"{name} -> {reg[cid]['path']} (file gone)")
    out.append(_c(BAD if bad else OK, "kinds",
                  "; ".join(bad) if bad else f"{len(kinds)} kind(s), all resolved",
                  "v4 register --id <kind> --checker <path> --fixtures <dir> "
                  "--kinds <kind>" if bad else ""))



def _check_something_can_actually_raise_each_kind(root, out):
    """Does every registered kind have a detector that can raise it?"""
    kinds = _json_or(root / config_mod.CLAIM_KINDS, {})
    # The other half of 3, and the half that was missing. Six kinds here had a
    # checker that was written, registered, on disk and named by a claim kind,
    # with `detector: null` -- and `derive` only globs `detectors/*.py`, so no
    # claim of those kinds could ever exist and the checkers had never run.
    # Measured on the reference adopter's whole ledger: `dal-write`,
    # `design-pins`, `signature-change`, `webhook-replay`, `surface-proof` and
    # `runtime-proof` appeared zero times across 36 tasks.
    #
    # Check 3 could not see it because it starts from the artefacts that exist
    # and asks whether they resolve. So does every other report here, and so
    # does `v4 ship`, which prints the detectors that ran. Nothing asked the
    # question from the other end: this kind was promised, what delivers it.
    unraisable = []
    for name, spec in sorted(kinds.items()):
        if name in RAISED_BY_A_COMMAND:
            continue
        det = spec.get("detector")
        if not det:
            unraisable.append(f"{name} (no detector)")
        elif not (root / "detectors" / det).is_file():
            unraisable.append(f"{name} -> {det} (not on disk)")
    out.append(_c(BAD if unraisable else OK, "raisable",
                  "; ".join(unraisable) if unraisable else
                  f"{len(kinds)} kind(s), each with something that raises it",
                  "these checkers can never run: nothing emits their kind, so "
                  "no claim of it exists and no task ever has to answer one. "
                  "Write the detector, or drop the kind." if unraisable else ""))



def _check_the_questions_that_need_a_live_environment_have_a_command(root, out):
    """Has this repo said how to drive the surface and the writes it declares?"""
    cfg_raw = _json_or(root / config_mod.CONFIG, {})
    # Both are conditional on a fact the repo declared about itself, so this
    # reads the same on any stack: `ui_globs` says there is a surface,
    # `outbound_write` says something outside changes. A repo that declared
    # either and neither command raised nothing and shipped -- measured on the
    # reference adopter: 7 `ui_globs`, 51 `outbound_write`, 8 Playwright specs
    # somebody maintains, and `web/ui/e2e/**` in zero of 433 claims.
    #
    # Reported here as well as raised as a claim, because a claim arrives after
    # a task is open and this arrives before one is.
    present = {}
    for fp in sorted((root / ".v4").glob("facts.*.json")):
        if fp.name.endswith(".draft"):
            continue
        try:
            f = json.loads(fp.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        present = f.get("present", f)
        break
    owed = []
    if present.get("ui_globs") and not config_mod.declared(cfg_raw, "surface_command"):
        owed.append("ui_globs is declared and `surface_command` is not -- "
                    "nothing says how to drive the surface you say you have")
    if present.get("outbound_write") and not config_mod.declared(cfg_raw, "runtime_proof"):
        owed.append(f"{len(present['outbound_write'])} outbound_write pattern(s) "
                    f"and no `runtime_proof` -- nothing checks that any of them land")
    out.append(_c(WARN if owed else OK, "live proof",
                  "; ".join(owed) if owed else
                  "every question that needs a live environment has a command",
                  "write them in .v4/config.json, or sign once with "
                  "`v4 risk accept --kind unprovable --scope repo`, which lapses "
                  "the day the command is written" if owed else ""))



def _check_lens_files_that_will_not_load(root, out):
    """Which lens files are on disk and unusable?"""
    # `v4 review lens` used to raise on one, taking the list of all the others
    # with it, while this command said nothing and exited 0. Layer 3 is the one
    # layer with no program behind it, so a lens nobody can read is a lens
    # nobody runs, and nothing anywhere would have said so.
    try:
        from . import review as review_mod
        _, unusable_lenses = review_mod.lens_files(root)
    except Exception:                                           # noqa: BLE001
        unusable_lenses = {}
    if unusable_lenses:
        out.append(_c(BAD, "lenses",
                      "; ".join(f"{k}: {v}" for k, v in sorted(unusable_lenses.items())),
                      "a lens that will not load is one nobody reviews against; "
                      "`v4 review lens` lists what loaded"))



def _check_registered_hashes_match_what_is_on_disk(root, out):
    """Has a checker been edited since the registry recorded its hash?"""
    reg = _json_or(root / config_mod.CHECKERS, {})
    stale = [cid for cid, e in sorted(reg.items())
             if (root / e["path"]).is_file()
             and _sha(root / e["path"]) != e.get("sha256")]
    out.append(_c(BAD if stale else OK, "checker hashes",
                  f"{stale} differ from the registry" if stale else
                  f"{len(reg)} checker(s) match",
                  "re-register them; until then every claim they serve exits 6"
                  if stale else ""))



def _check_rows_in_the_facts_table_nobody_has_read_yet(root, out):
    """Which rows in the facts table has nobody read yet?"""
    # `kernel.facts propose` marks every row it guesses, so an adopted repo can
    # be worked in while its table is still being pruned. Unreported, that state
    # is indistinguishable from a table somebody chose.
    proposed = 0
    auto_absent = []
    for fp in sorted(root.glob(".v4/facts*.json")):
        try:
            table = json.loads(fp.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for key in ("outbound_write", "outbound_read", "auth_decision"):
            proposed += sum(1 for r in table.get(key, [])
                            if isinstance(r, dict) and r.get("kind") == "proposed")
        for key, reason in sorted((table.get("absent") or {}).items()):
            if isinstance(reason, str) and reason.startswith(facts_mod.AUTO_PREFIX):
                auto_absent.append(key)
    if proposed:
        out.append(_c(WARN, "facts rows",
                      f"{proposed} row(s) still marked 'proposed'",
                      "each is a guess from a verb ending. Check its seen_at, "
                      "drop what does not reach outside, and set a real kind"))
    # An absence nobody confirmed is the one failure the empty-table refusal was
    # aimed at, wearing the new key. The declaration lets a repo with none adopt;
    # this is what keeps it from being the place the answer quietly stays "none".
    if auto_absent:
        out.append(_c(WARN, "facts absent",
                      f"{len(auto_absent)} table(s) declared empty by the "
                      f"installer, not by a person: {', '.join(auto_absent)}",
                      "the reason says a verb-ending sweep found nothing, which "
                      "misses a wrapper or a subprocess. Confirm it and rewrite "
                      "the line without AUTO:, or add the rows -- until then "
                      "every detector over that surface reports a clean repo"))
    # A repo that says it ships to a browser, with no `ui_globs` to point at.
    #
    # `propose` finds view files by suffix, which is right for the repos that
    # have them and silent for the ones that do not. Measured on the eval
    # corpus: `KaTeX` and `csstree` both build a browser bundle -- webpack,
    # rollup, esbuild -- out of plain `.ts`, so no `.tsx` exists to find and
    # `ui_globs` came back empty for both.
    #
    # This says so rather than guessing a directory, and the reason is the last
    # guess: proposing a repo's top-level directory produced a finding against
    # a build-time Node script. `browser`, `unpkg` and `jsdelivr` are the
    # repo's own words -- a package sets them to say where a browser loads it
    # from -- so this is reading a declaration, not inferring one. Which
    # *source* directory the bundler reads to build that output is in the
    # bundler's config, which is a second format this does not read; a person
    # with the repo open answers it in one line.
    _check_a_browser_bundle_with_no_ui_globs(root, out)


#: What a package.json says when it expects a browser to load it. Not a guess:
#: a bundler and a CDN both read these fields, and a package that has none of
#: them is not published for a browser to fetch.
_BROWSER_FIELDS = ("browser", "unpkg", "jsdelivr")


def _check_a_browser_bundle_with_no_ui_globs(root, out):
    """One row: this repo ships to a browser and has not said where its client is."""
    pkg = root / "package.json"
    if not pkg.is_file():
        return
    try:
        manifest = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    declared = [f for f in _BROWSER_FIELDS if manifest.get(f)]
    if not declared:
        return
    for fp in sorted(root.glob(".v4/facts*.json")):
        try:
            table = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if table.get("ui_globs"):
            return
    out.append(_c(WARN, "ui_globs",
                  f"package.json declares {', '.join(declared)}, so a browser "
                  f"loads this, and facts declare no `ui_globs`",
                  "`v4 install` finds client code by view-file suffix, and a "
                  "bundle built from plain `.ts` has none to find. Name the "
                  "directory the bundler reads -- until then every rule about "
                  "what reaches a browser is off here, and off reads the same "
                  "as clean"))



def _check_the_detectors_are_registered_and_unchanged(root, out):
    """Is every conditional detector registered, and unchanged since it was?"""
    # An unregistered detector does not raise its claims at all, so the repo
    # looks clean instead of looking broken -- the shape this command exists
    # for.
    try:
        dreg = json.loads((root / ".v4/detectors.json").read_text())
    except (OSError, json.JSONDecodeError):
        dreg = {}
    conditional = sorted(p.name for p in (root / "detectors").glob("*.py")
                         if not p.name.startswith(("_", "always_")))
    dbad = [n for n in conditional
            if dreg.get(n, {}).get("sha256") != _sha(root / "detectors" / n)]
    out.append(_c(BAD if dbad else OK, "detectors",
                  f"{dbad} not registered, or changed since"
                  if dbad else f"{len(conditional)} conditional detector(s) match",
                  "v4 register-detector --detector <path> --fixtures <dir>; "
                  "until then derive skips them and raises none of their claims"
                  if dbad else ""))



def _check_every_detector_raises_a_registered_kind(root, out):
    """Does every detector on disk raise a kind this repo still registers?

    The group above asks whether a *conditional* detector is registered, and
    skips `always_*` by name -- so the one command that asks about detectors
    could not see the class of file that goes stale. A kind trim removes a kind
    from `claim_kinds.json`; the detector that raised it is a file on disk that
    nothing removes, and `install` has no step that would.

    Measured on the reference adopter: five `always_*` detectors left behind by
    a 28-to-16 trim, each printing a claim `derive` refuses, on every task since.
    `derive` does say so -- `! always_sweep.py: unknown-kind` -- but that line
    lives for the length of one command, and the worker there had learned to run
    `derive ... | grep -v unknown-kind` by the third task. What survives is the
    `detector_run` row, and it reads `ran: true, claims: 0, dropped: 1`: the same
    shape as a detector that ran and found nothing.

    So this asks the question that outlives the run, and asks it of every
    detector, `always_*` included.
    """
    kinds = _json_or(root / config_mod.CLAIM_KINDS, {})
    if not kinds:
        # `_check_every_kind_points_at_a_registered_checker_on_disk` says so.
        return
    from .install import _kinds_raised_by
    stale = {}
    for det in sorted((root / "detectors").glob("*.py")):
        if det.name.startswith("_"):
            continue
        unknown = sorted(k for k in _kinds_raised_by(det) if k not in kinds)
        if unknown:
            stale[det.name] = unknown
    named = ", ".join(f"{n} -> {', '.join(ks)}" for n, ks in sorted(stale.items()))
    out.append(_c(BAD if stale else OK, "detector kinds",
                  f"{len(stale)} detector(s) raise a kind this repo does not "
                  f"register: {named}" if stale
                  else f"every detector raises a kind {config_mod.CLAIM_KINDS} carries",
                  "derive refuses the claim and records `dropped`, which reads "
                  "the same as a detector that found nothing. Delete the "
                  "detector, or register the kind" if stale else ""))


def _check_delta_checkers_have_their_baseline(root, out):
    """Does every delta checker that has failed here have its baseline?"""
    kinds = _json_or(root / config_mod.CLAIM_KINDS, {})
    # A missing baseline only matters where there is standing debt for it to
    # carry. Naming all four the moment the `baseline` flag was set was three
    # false alarms out of four on this repo -- `secret-chain` and `dependency`
    # find nothing here -- and a line that cries wolf is one people learn to
    # scroll past, which is what this whole command is against.
    #
    # The ledger already knows: a kind whose latest attempt exited 1 with no
    # baseline file is the case the warning is about. A kind that has never run
    # is not yet a fact about this repo, and saying nothing is the honest answer
    # until it has.
    failing, unreadable = {}, ""
    try:
        from . import ledger as _led
        _conn = _reader(root)
        # With the date of that attempt. Without it the row says a kind is
        # failing here, present tense, on the strength of a run that may be
        # weeks old and about code that has since changed -- and a reader has
        # no way to tell the two apart from the line. Measured: `lint` reported
        # missing after its cause (an import cycle) had been repaired, because
        # the last recorded attempt still said 1.
        # Claims on tasks that have not ended. A task that shipped or was
        # abandoned took its failures with it: the claim will never be attempted
        # again, so its last exit code is a fact about a day, not about this
        # repo. Measured -- `lint` was reported as needing a baseline from an
        # attempt on 2026-08-12, on a task long since ended, after the cause
        # (an import cycle in `kernel/ledger.py`) had been repaired and `lint`
        # exited 0. The row said "this task fails on work it did not cause"
        # about no task at all.
        failing = {}
        for r in _conn.execute(f"""
            SELECT c.kind, a.ended_at FROM claim c
            JOIN attempt a ON a.id = (SELECT MAX(id) FROM attempt WHERE claim_id = c.id)
            WHERE a.exit_code = 1
              AND c.task_id NOT IN ({_led.ENDED_TASKS_SQL})""").fetchall():
            failing[r["kind"]] = (r["ended_at"] or "")[:10]
    except Exception as exc:                                    # noqa: BLE001
        # Swallowed, `failing` stayed empty, and every baseline kind then landed
        # in `unexercised` -- so this printed `ok  baselines  every delta checker
        # that has failed here has its baseline` from no data at all. The two
        # other ledger reads in this file already report the failure instead;
        # this is the same read and gets the same treatment.
        unreadable = f"{exc}"
    missing_base, unexercised = [], []
    for name, spec in sorted(kinds.items()):
        b = config.baseline_path(root, name)
        if not spec.get("baseline") or b.is_file():
            continue
        (missing_base if name in failing else unexercised).append(b.name)
    note = ""
    if missing_base:
        seen = sorted({failing.get(n) for n in kinds
                       if config.baseline_path(root, n).name in missing_base
                       and failing.get(n)})
        note = ("a missing baseline is no amnesty, so this task fails on work it "
                "did not cause. Each finding prints the id to put in the file"
                + (f". Last failed {', '.join(seen)} -- re-run it before writing "
                   f"a baseline, the cause may already be gone" if seen else ""))
    elif unexercised:
        # Named, not counted. Staying quiet about which ones is what makes the
        # first red look like a defect: a task touches a file that has carried a
        # violation for a year, the checker says so correctly, and the worker
        # spends five commands proving the violation predates it. Saying now
        # which kinds are one debt-carrying file away from that costs a line.
        note = (f"{len(unexercised)} declared and never needed here yet: "
                f"{', '.join(sorted(unexercised))}. Nothing to carry until one "
                f"fails -- and the first task to touch a file that already "
                f"carries debt is where it will, on debt it did not cause")
    if unreadable:
        out.append(_c(WARN, "baselines",
                      f"could not read the ledger: {unreadable}",
                      "so which kinds have actually failed here is unknown, and "
                      "a missing baseline cannot be told from one that was never "
                      "needed"))
    else:
        out.append(_c(WARN if missing_base else OK, "baselines",
                      f"missing {missing_base}" if missing_base else
                      "every delta checker that has failed here has its baseline",
                      note))



def _check_reads_covers_the_paths_a_checker_names(root, out):
    """Does each checker's `reads` cover the repo paths its own source names?"""
    kinds = _json_or(root / config_mod.CLAIM_KINDS, {})
    try:
        registry = json.loads((root / config_mod.CHECKERS).read_text())
        dishonest = _reads_are_honest(root, registry.get("kinds", registry))
    except Exception as exc:                                    # noqa: BLE001
        # An unread registry is not a clean one. This swallowed and then printed
        # `every checker's reads covers the paths it names` -- the strongest
        # sentence this row has -- from no data at all, which is the shape
        # `_reports`'s own docstring in this file says was already fixed once.
        # Measured: a traced full-suite run enters this function and enters
        # neither `_reads_are_honest` nor `_path_literals`, which is only
        # possible if the read raised every time.
        out.append(_c(WARN, "reads",
                      f"could not read {config_mod.CHECKERS} "
                      f"({type(exc).__name__}: {exc})",
                      "so whether each checker's `reads` covers the paths its "
                      "own source names is unknown -- which is not the same as "
                      "yes, and `readable()` uses that declaration to decide "
                      "whether a checker runs at all"))
        return
    if dishonest:
        out.append(_c(
            WARN, "reads",
            f"{len(dishonest)} checker(s) name files their `reads` does not cover: "
            + "; ".join(f"{n} -> {', '.join(p[:3])}" for n, p in dishonest[:3]),
            "`readable()` uses this to decide whether a checker runs, so a "
            "narrow declaration skips it on a task it had something to say "
            "about. Static and partial: a path built from a variable is "
            "invisible to this"))
    else:
        out.append(_c(OK, "reads",
                      "every checker's `reads` covers the paths it names"))



def _check_kinds_this_repo_keeps_failing_to_answer(root, out):
    """Which kinds have blocked task after task here without ever being answered?"""
    kinds = _json_or(root / config_mod.CLAIM_KINDS, {})
    # Registration proves a checker works; it does not prove this repo has
    # anything for it to read. A repo that registered `spec-coverage` and has
    # no SPEC.md is fully wired by every check above and blocked on every task,
    # and until this line existed nothing anywhere said so -- the exact shape
    # this command's own docstring is about.
    try:
        from . import ledger, state
        conn = _reader(root)
        # `NOT EXISTS ... accepted_risk` is the yardstick `unsettled_fails`
        # already uses, copied here because the two were measuring different
        # things about the same signature. `v4 ship` lets a claim-scoped
        # signature settle a claim; this line only ever looked at
        # `scope = 'repo'`, so the same signed claim was settled on the way out
        # and still "blocked" in the report -- and the remedy printed below then
        # told the reader to sign a thing they had signed. Any scope settles the
        # claim it names, in both places.
        stuck = conn.execute("""
            SELECT c.kind, c.task_id FROM claim c
            JOIN attempt a ON a.id = (SELECT MAX(id) FROM attempt WHERE claim_id = c.id)
            WHERE a.exit_code = 4
              AND NOT EXISTS (SELECT 1 FROM accepted_risk r
                              WHERE r.claim_id = c.id)""").fetchall()
        # Still repo-scoped, and deliberately: this set is what the `ok` line
        # reports as signed off *for the repo*, which is the claim
        # `cover_key`'s docstring describes -- that this repo structurally has
        # no subject for the kind. A claim-scoped signature settles its claim
        # above; it does not say that about the repo.
        signed = {json.loads(r["cover_key"])["kind"]
                  for r in state.repo_risks(conn) if r["cover_key"]}
        # Tasks that have neither shipped nor been abandoned. It was the newest
        # task by rowid, which is not the same thing: `abandon` exists to give a
        # task a second ending, and a probe abandoned with a written reason went
        # on escalating this line to BAD for as long as nothing newer was opened.
        # Reported either way -- the escalation is what changes.
        live = set(ledger.open_task_ids(conn))
        # A kind the registry no longer carries. Three of them were on this
        # line -- `dependency`, `request-coverage`, `secret-chain` -- and none
        # is in `claim_kinds.json` any more, so nothing can raise one and the
        # remedy printed below names a kind that is not there. This is the third
        # repair to this one line and the first that removes rows rather than
        # labelling them: naming the tasks, then marking the abandoned ones,
        # both made a report about dead history easier to read. It stays dead
        # history. A removed kind's old exit 4 is not an obligation, it is a
        # record of a rule this repo decided not to keep.
        known = set(_json_or(root / config_mod.CLAIM_KINDS, {}))
        by_kind = {}
        for r in stuck:
            if r["kind"] not in signed and r["kind"] in known:
                by_kind.setdefault(r["kind"], []).append(r["task_id"])
        # Named, not counted. Two of these in this repo were `t-007` and `t-200`
        # -- demo tasks from a day nobody is going back to. A count alone reads
        # as an emergency and trains the reader to skip the line; the task ids
        # let them tell a live block from an abandoned one, which is a judgement
        # this command should not be making for them.
        touches_current = any(live.intersection(t) for t in by_kind.values())
        # And say which of the named tasks were abandoned. "blocked in t-007,
        # t-200" reads as work that is stuck; both of those were dropped on a
        # day nobody is going back to, and the row already knows -- the same
        # query that de-escalates BAD to WARN. Making the reader run it is
        # making them ask twice.
        dropped = {r[0] for r in conn.execute(
            "SELECT DISTINCT task_id FROM event WHERE kind = 'abandoned' "
            "AND task_id IS NOT NULL")}

        def _mark(ts):
            return ", ".join(f"{t}{' (abandoned)' if t in dropped else ''}"
                             for t in sorted(set(ts))[:4])

        if by_kind:
            out.append(_c(BAD if touches_current else WARN, "unanswerable kinds",
                          "; ".join(f"{k} blocked in {_mark(ts)}"
                                    for k, ts in sorted(by_kind.items())),
                          "exit 4 is not an answer and does not close a task. Add "
                          "what the checker reads, or -- if this repo genuinely "
                          "has none -- sign it once for the whole repo with "
                          "`v4 risk accept --kind unprovable --scope repo`, which "
                          "lapses by itself the day the checker can answer"))
        elif signed:
            out.append(_c(OK, "unanswerable kinds",
                          f"{len(signed)} signed off for this repo: "
                          f"{', '.join(sorted(signed))}"))
    except Exception as exc:                                    # noqa: BLE001
        out.append(_c(WARN, "unanswerable kinds", f"could not read the ledger: {exc}"))



def _check_a_registered_checker_that_has_never_executed(root, out):
    """Is a registered checker holding claims it has never once run against?"""
    kinds = _json_or(root / config_mod.CLAIM_KINDS, {})
    # `engagement: true` on 17 of the 31 kinds, six engagement events in this
    # ledger's whole life, and three registered checkers with claims raised and
    # zero attempt rows: `dep-provenance` (10 claims), `secret-chain` (9) and
    # `test-shape` (3). `lifecycle.check` returns NEEDS_ENGAGEMENT and never
    # launches them, so a program that was written, fixture-gated and
    # registered has never judged anything -- and nothing said so. Whether the
    # gate is worth that is a decision somebody makes; it cannot be made from a
    # report that does not carry the number.
    with _reports(out, "never executed"):
        from . import ledger as _led5
        conn5 = _reader(root)
        never = conn5.execute(
            "SELECT c.checker, count(*) n FROM claim c "
            "WHERE c.checker != '' AND NOT EXISTS ("
            "  SELECT 1 FROM attempt a WHERE a.claim_id = c.id) "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM attempt a2 JOIN claim c2 ON c2.id = a2.claim_id "
            "  WHERE c2.checker = c.checker) "
            "GROUP BY c.checker ORDER BY n DESC").fetchall()
        # Which of the two this is. "Never run" reads as "may be broken", and
        # for most of these it is not: their kind carries `engagement: true`,
        # so `lifecycle.check` returns NEEDS_ENGAGEMENT and the checker is
        # never launched at all. That is the gate working, and it is a
        # different thing from a checker nothing has ever pointed at. Measured
        # here: 4 of the 6 are engagement-held, and all 6 answer when run by
        # hand. One row saying both is a row a reader cannot act on.
        engaged = {v.get("checker") for v in kinds.values() if v.get("engagement")}
        # A claim on an abandoned task was never going to be checked, and
        # counting it as a checker nobody has ever pointed at is the third
        # thing this row was saying at once. The exclusion is the subquery in
        # `live` below; it was also computed into a `dropped` set here that
        # nothing read, so a query ran on every `v4 doctor` and was thrown away
        # under five lines explaining what it decided. A dead local under a
        # comment that explains it is the version a reader cannot tell from
        # live code.
        live = [r for r in conn5.execute(
            "SELECT DISTINCT c.checker FROM claim c WHERE c.checker != '' "
            "AND c.task_id NOT IN (SELECT DISTINCT task_id FROM event "
            "                      WHERE kind = 'abandoned' AND task_id IS NOT NULL)")]
        on_live = {r[0] for r in live}
        held = [r for r in never if r["checker"] in engaged]
        idle = [r for r in never if r["checker"] not in engaged
                and r["checker"] in on_live]
        only_dropped = [r for r in never if r["checker"] not in engaged
                        and r["checker"] not in on_live]
        named = [f"{r['checker']} ({r['n']} claim(s))" for r in never]
        why = []
        if held:
            why.append(f"{len(held)} held by engagement -- the sentence was "
                       f"never written, so the checker was never launched: "
                       f"{', '.join(r['checker'] for r in held)}")
        if idle:
            why.append(f"{len(idle)} with nothing holding them: "
                       f"{', '.join(r['checker'] for r in idle)}")
        if only_dropped:
            why.append(f"{len(only_dropped)} whose only claims are on abandoned "
                       f"tasks, so nothing was ever going to check them: "
                       f"{', '.join(r['checker'] for r in only_dropped)}")
        out.append(_c(WARN if named else OK, "never executed",
                      (f"{len(named)} registered checker(s) have never run: "
                       f"{', '.join(named[:4])}") if named else
                      "every checker with a claim has run at least once",
                      "; ".join(why) + ". A claim that never reaches its checker "
                      "is not answered leniently -- it is not asked."
                      if named else ""))



def _check_hooks_are_called_and_not_merely_present(root, out):
    """Does a settings file call these hooks, and has any of them ever fired?"""
    settings = [p for p in (root / ".claude" / "settings.json",
                            root / ".claude" / "settings.template.json") if p.is_file()]
    hooks = sorted(p.name for p in (root / "hooks").glob("*.py")
                   if not p.name.startswith("_")) if (root / "hooks").is_dir() else []
    if not settings:
        out.append(_c(WARN, "hooks",
                      f"{len(hooks)} hook file(s) and no .claude/settings*.json",
                      "the files exist and nothing calls them; ship reports "
                      "DEGRADED for every task"))
    else:
        wired = settings[0].read_text()
        unwired = [h for h in hooks if h not in wired]
        live = (root / ".claude" / "settings.json").is_file()
        # The heading says "the file existing is not the hook running" and this
        # then greped `settings.json` for each filename and printed `ok  hooks
        # 3 hook(s) wired` -- an environment question answered from source, in
        # the check whose own title is about not doing that. The ledger says
        # otherwise: measured here, the last `hook_seen` was 2026-08-10, and
        # `repo-review` (opened 08-13) and `t-fcprobe` (08-18) had none across
        # eight days of Edit/Write traffic. The sweep check forty lines below
        # already consults the ledger through `sweep_mod.last`; the pattern was
        # in the same function and was not applied.
        # Per hook, not "any hook". One arbitrary `hook_seen` row said all three
        # were alive, and the row carried no hook name to say otherwise -- so
        # the Stop gate, the only one of the three that can block a turn and the
        # only one that recorded nothing at all, was reported alive on the
        # strength of its filename being a substring of a JSON file. It now
        # names itself on the rows it writes, like its two siblings, and this
        # asks each of them separately.
        fired, unread, silent = None, "", []
        try:
            _c_h = _reader(root)
            fired = _c_h.execute(
                "SELECT created_at FROM event WHERE kind = 'hook_seen' "
                "ORDER BY id DESC LIMIT 1").fetchone()
            seen = {r[0] for r in _c_h.execute(
                "SELECT DISTINCT json_extract(payload, '$.hook') FROM event "
                "WHERE kind = 'hook_seen'") if r[0]}
            # Rows written before the payload carried a name are not evidence
            # about any particular hook, so a repo whose whole history predates
            # this is reported as "none of them named itself" rather than as
            # three dead hooks.
            silent = ([h[:-3] for h in hooks if h[:-3] not in seen]
                      if seen else [])
        except Exception as exc:                                # noqa: BLE001
            unread = f"{type(exc).__name__}: {exc}"
        if unwired:
            status, detail = BAD, f"not wired: {unwired}"
        elif not live:
            status, detail = WARN, (f"{len(hooks)} hook(s) wired  "
                                    f"(template only -- copy to settings.json)")
        elif unread:
            status, detail = BAD, (f"{len(hooks)} hook(s) wired, and whether any "
                                   f"has fired could not be read: {unread}")
        elif fired is None:
            status, detail = WARN, (f"{len(hooks)} hook(s) wired and none has "
                                    f"ever fired")
        elif fired is not None and not seen:
            # The comment above says a history older than the `hook` field is
            # "reported as none of them named itself rather than as three dead
            # hooks" -- and then `seen` empty made `silent` empty, which fell
            # through to the strongest green this row has. Intent and code
            # disagreed, and the code was the half that printed. Measured by an
            # adopter before upgrading: 1,823 `hook_seen` rows, every
            # `payload.hook` null, and `ok  3 hook(s) wired, each has fired`.
            # The one of the three least entitled to that line is `stop_gate`,
            # which had no `record_seen` call at all and could not have written
            # a row if it tried.
            status, detail = WARN, (
                f"{len(hooks)} hook(s) wired, last fired "
                f"{str(fired[0])[:19]}, and none of them named itself -- every "
                f"row here predates the `hook` field, so which of them has "
                f"fired is not answerable from this ledger")
        elif silent:
            # The finding this row exists for: wired, something fired, and one
            # of them has never once said so.
            status, detail = WARN, (
                f"{len(hooks)} hook(s) wired, last fired "
                f"{str(fired[0])[:19]}, and {', '.join(sorted(silent))} "
                f"{'has' if len(silent) == 1 else 'have'} never left a mark")
        else:
            status, detail = OK, (f"{len(hooks)} hook(s) wired, each has fired, "
                                  f"last {str(fired[0])[:19]}")
        out.append(_c(status, "hooks", detail,
                      "wiring is configuration; firing is a fact, and `v4 ship` "
                      "reports DEGRADED off the second one"
                      if status is not OK else ""))



def _check_the_after_gate_is_called_and_not_merely_present(root, out):
    """Does anything call `v4 sweep`, and when did a sweep last run?"""
    cfg_raw = _json_or(root / config_mod.CONFIG, {})
    # `kernel/sweep.py` answers "is it due" and deliberately does not start
    # anything -- SPEC.md §10.1 says `--if-due` is "for cron to give up on".
    # So the trigger lives outside this repo's code, exactly like a hook's, and
    # exactly like a hook it can simply not exist. Measured: no workflow, no
    # command, no config key mentioned `sweep`, and 145 of the 254 migrated
    # rules (57%) live in the lens layer this gate is the only trigger for.
    #
    # This repo already refuses that shape twice over: "一個檢查要算數要三樣:
    # 自動觸發、喺一個唔繼承任何本地嘢嘅環境跑過、而且至少 fire 過一次" and
    # "一個喺呢個 repo 裡跑唔起嘅閘,比冇閘更差". The check for hooks was
    # written and the same question was never asked about this one.
    lenses = len(list((root / ".v4" / "lenses").glob("*.json")))
    if lenses:
        declared = "lens_sweep" in cfg_raw
        wf_text = "".join(p.read_text(encoding="utf-8", errors="replace")
                          for p in (root / ".github" / "workflows").glob("*.yml")) \
            if (root / ".github" / "workflows").is_dir() else ""
        cmd_text = "".join(p.read_text(encoding="utf-8", errors="replace")
                           for p in (root / ".claude").rglob("*.md")) \
            if (root / ".claude").is_dir() else ""
        called = "sweep" in wf_text or "sweep" in cmd_text
        # `ran = None` on a failed read gives the identical output to a repo
        # that genuinely never swept -- "wired, and no sweep has ever been
        # recorded" -- so the two are separated here rather than in the
        # sentence below. This is the row `_reports` exists for; it is spelled
        # out because the answer feeds the next branch rather than replacing it.
        ran, unread = None, ""
        try:
            from . import ledger, sweep as sweep_mod
            ran = sweep_mod.last(_reader(root))
        except Exception as exc:                                # noqa: BLE001
            unread = f"{type(exc).__name__}: {exc}"
        if not called:
            out.append(_c(WARN, "sweep",
                          f"{lenses} lens(es) and nothing calls `v4 sweep`",
                          "the after-gate answers 'is it due' and starts "
                          "nothing by design, so the trigger is a cron, a "
                          "workflow or a command -- and there is none. Every "
                          "rule that landed in a lens has never been read."))
        elif unread:
            out.append(_c(BAD, "sweep",
                          f"wired, and the ledger could not be read: {unread}",
                          "this line cannot say whether a sweep has ever "
                          "happened, which is not the same as saying none has"))
        else:
            out.append(_c(OK if ran else WARN, "sweep",
                          (f"last swept {str(ran)[:19]}" if ran else
                           f"wired, and no sweep has ever been recorded")
                          + ("" if declared else
                             "  (no lens_sweep in config -- running on the default)")))



def _run_lines(yaml_text: str) -> str:
    """The shell a workflow actually executes, with the prose left out.

    A YAML comment starts at a `#` that is not inside quotes, and a `run:`
    block carries shell where `#` is a comment too -- so the same rule serves
    both, and this does not need a YAML parser to answer "what would run".
    Nothing here interprets the document: it strips comments and keeps the rest,
    which is strictly less text than before and never more.
    """
    kept = []
    for line in yaml_text.splitlines():
        out, quote = [], ""
        for ch in line:
            if quote:
                out.append(ch)
                if ch == quote:
                    quote = ""
                continue
            if ch in "\"'":
                quote = ch
                out.append(ch)
                continue
            if ch == "#":
                break
            out.append(ch)
        kept.append("".join(out))
    return "\n".join(kept)


def _check_ci_can_actually_walk_the_chain(root, out):
    """Does a CI job walk the exported chain, and does the export walk clean?"""
    wf = list((root / ".github" / "workflows").glob("*.yml")) \
        if (root / ".github" / "workflows").is_dir() else []
    # What a workflow *runs*, not what it says. This read the whole file,
    # comments included, so a paragraph explaining why there is no chain job --
    # which naturally quotes the command -- flipped this row from `not wired` to
    # `wired` without a job existing. It happened here on 2026-09-05, in this
    # repo, to somebody who knew they had changed nothing.
    #
    # The same shape, pointed the other way, is already in this project's own
    # history: a guard scanning for `DROP DATABASE` matched four files that
    # were explaining it, and the repair there was to stop reading prose rather
    # than to tell people not to write the words.
    text = "\n".join(_run_lines(p.read_text()) for p in wf)
    if not wf:
        out.append(_c(WARN, "CI", "no workflows"))
    elif "audit --events" not in text:
        out.append(_c(BAD, "CI", "no job walks the chain",
                      "the ledger lives in .git/ and a clone has none, so an "
                      "`audit` on the live database cannot run there; "
                      "`v4 export` writes what CI can walk"))
    else:
        # `ok  CI  walks the exported chain` came from the substring above plus
        # the file existing, while every run of that workflow had failed and the
        # chain job had never passed once. What is checkable from here is the
        # thing that made it fail: whether the committed export can be walked at
        # all. That is a fact, and it is the same walk CI runs.
        export = root / ".v4" / "ledger_export.jsonl"
        if not export.is_file():
            out.append(_c(WARN, "CI", "a job walks the exported chain, and "
                                      "nothing has been exported yet",
                          "run `v4 ship`; until then that job has nothing to read"))
        else:
            try:
                from . import ledger as _lc
                n, probs = _lc.verify_exported(export)
                out.append(_c(OK if not probs else BAD, "CI",
                              f"the exported chain walks clean across {n} attempt(s)"
                              if not probs else
                              f"the exported chain does not walk: {probs[0][:110]}",
                              "" if not probs else
                              "this is what the chain job runs, so it is failing "
                              "there too -- and a job that has never passed is "
                              "one nobody can tell from a broken one"))
            except Exception as exc:                            # noqa: BLE001
                out.append(_c(BAD, "CI",
                              f"the exported chain could not be read: {exc}"))



def _check_path_lists_naming_things_that_are_not_here(root, out):
    """Do the path lists and the `file::symbol` refs name tracked files?"""
    # `protected_paths` used to be checked by `derive`, which refused any
    # detector that reads the facts table when one of its globs matched nothing.
    # That was wrong twice over: no detector reads `protected_paths` to narrow a
    # scan, and refusing three detectors -- two of them security detectors --
    # over V3 leftovers in a key they never read is a gate that stops working for
    # a reason unrelated to what it guards.
    #
    # It is still worth saying. A protected path that names nothing is a rule
    # nobody can break and a line nobody will delete, and it accumulates.
    with _reports(out, "path lists"):
        from .derive import UNSCANNED_PATH_KEYS
        from fnmatch import fnmatch
        import subprocess as _sp
        # The same file `run` located above, read again rather than threaded
        # through -- this check is a late addition and reaching for a variable
        # that happens to be in scope is how a helper starts depending on where
        # it was pasted.
        chosen = root / f".v4/facts.{_layout.repo_name(root)}.json"
        if not chosen.is_file():
            others = sorted(root.glob(".v4/facts*.json"))
            chosen = others[0] if others else chosen
        facts = json.loads(chosen.read_text()) if chosen.is_file() else None
        listed = _sp.run(["git", "ls-files"], cwd=root, capture_output=True,
                         text=True)
        if facts and listed.returncode == 0:
            files = [f for f in listed.stdout.split("\n") if f.strip()]
            dead = [f"{k}: {g}" for k in UNSCANNED_PATH_KEYS
                    for g in (facts.get(k) or []) if isinstance(g, str)
                    and not any(fnmatch(f, g) or fnmatch(f, g.rstrip("/*") + "/*")
                                for f in files)]
            out.append(_c(WARN if dead else OK, "path lists",
                          f"{len(dead)} protected path(s) name nothing tracked: "
                          f"{dead[:4]}" if dead else
                          "every protected path names something that is here",
                          "harmless in itself -- nothing can touch a file that "
                          "does not exist -- but they are usually left over from "
                          "a framework that was removed" if dead else ""))

            # The sibling constant, which said "reported rather than acted on,
            # for the same reason as protected_paths" and had no reader at all.
            # A `file::symbol` exemption whose file is gone exempts nothing, and
            # `public_routes` is where a repo writes down which handlers are
            # deliberately unauthenticated -- a stale entry there reads as a
            # decision somebody made about code that is not here.
            from .derive import SYMBOL_REF_KEYS
            tracked = set(files)
            gone = []
            for k in SYMBOL_REF_KEYS:
                for ref in (facts.get(k) or []):
                    if not isinstance(ref, str):
                        continue
                    f_half = ref.split("::", 1)[0]
                    if f_half and f_half not in tracked:
                        gone.append(f"{k}: {ref}")
            out.append(_c(WARN if gone else OK, "symbol refs",
                          f"{len(gone)} reference(s) name a file that is not "
                          f"tracked: {gone[:4]}" if gone else
                          "every file::symbol reference names a file that is here",
                          "an exemption whose file is gone exempts nothing, and "
                          "reads as a decision about code this repo no longer has"
                          if gone else ""))



def _check_the_path_the_hooks_use_to_find_the_framework(root, out):
    """Is the framework path in `.v4/home` a directory on this machine?"""
    # `.v4/home` holds one machine's absolute path and is tracked by git, so on
    # anybody else's clone it names a directory that is not there. Nothing
    # checked it, and the failure is silent by construction: a hook that cannot
    # import `kernel` allows the write. `bin/v4` fails loudly on the same fact;
    # this is the quiet half, and now it is one line to see.
    home = root / ".v4" / "home"
    if home.is_file():
        where = home.read_text(encoding="utf-8", errors="replace").strip()
        there = Path(where).is_dir() if where else False
        env = os.environ.get("V4_HOME")
        out.append(_c(OK if (there or env) else BAD, "framework path",
                      f"{where or '(empty)'}"
                      + ("" if there else " -- not a directory on this machine"),
                      "" if there else
                      (f"V4_HOME={env} is set and wins" if env else
                       "every hook that cannot import `kernel` allows the write. "
                       "Run `v4 install` here, or set V4_HOME. `.v4/home` is a "
                       "fact about one machine and does not belong in git")))



def _check_tasks_that_were_opened_and_never_ended(root, out):
    """Which tasks are still open, and how did the ended ones end?"""
    # Worth reporting from the day the hooks stopped needing `V4_TASK`: they now
    # take the newest unshipped task as the one they guard, so an abandoned task
    # silently becomes the scope every write is checked against. Measured
    # immediately -- a throwaway probe task, opened to see whether three
    # detectors had started running again, became the guard.
    #
    # Not a failure. A task left open is normal mid-work. This is the line that
    # makes "which one am I under" answerable without guessing.
    with _reports(out, "open tasks"):
        from . import ledger as _led2
        conn2 = _reader(root)
        open_ids = [r[0] for r in conn2.execute(
            f"SELECT id FROM task WHERE id NOT IN ({_led2.ENDED_TASKS_SQL}) "
            f"ORDER BY rowid DESC").fetchall()]
        if not open_ids:
            # "Shipped" is one of the two ways a task ends and this said it was
            # the only one. Three tasks here were abandoned -- derived, never
            # checked, closed out -- and the row reported them as shipped,
            # which is the difference between work that was judged and work
            # that was dropped.
            dropped = [r[0] for r in conn2.execute(
                "SELECT DISTINCT task_id FROM event WHERE kind = 'abandoned' "
                "AND task_id IS NOT NULL ORDER BY task_id")]
            out.append(_c(OK, "open tasks",
                          "every task has ended" + (
                              f" -- {len(dropped)} abandoned: "
                              f"{', '.join(dropped[:4])}" if dropped else
                              ", all by shipping")))
        else:
            # And which tree each one is in. One ledger serves every worktree,
            # so "5 unshipped" was a number a reader could not act on: the tasks
            # were in five different directories and the pairing lived in a
            # naming convention nothing reads. Measured on the reference
            # adopter: eight worktrees, five open tasks, one per tree.
            #
            # `?` for a task opened before this was recorded -- an honest gap is
            # better than the newest tree's path printed beside all of them.
            from . import lifecycle as _lc
            here = str(Path(root).resolve())

            def _where(tid):
                w = _lc.worktree_of(conn2, tid)
                return "here" if w == here else (w or "?")

            named = ", ".join(f"{t} ({_where(t)})" for t in open_ids[:4])
            out.append(_c(OK if len(open_ids) == 1 else WARN, "open tasks",
                          f"{len(open_ids)} unshipped, newest first: {named}"
                          + (f" … +{len(open_ids) - 4}" if len(open_ids) > 4 else ""),
                          f"with V4_TASK unset the hooks refuse to guess between "
                          f"them: every write is denied until one is named. "
                          f"`export V4_TASK=<id>` in each tree, or ship or "
                          f"abandon the ones you are not working on"
                          if len(open_ids) > 1 else ""))



def _check_findings_a_review_raised_and_nobody_closed(root, out):
    """Which review findings are neither closed nor deferred?"""
    # The review row is deliberately outside "open tasks": a hook that guarded it
    # would guard every repo forever. That exclusion is also why nothing showed
    # these. Measured the day filing started working: one finding landed, sat
    # unrun, and `doctor` said nothing -- so it was as visible in the ledger as it
    # had been in the markdown file it came from, which was the thing being fixed.
    with _reports(out, "review findings"):
        from . import ledger as _led3
        conn3 = _reader(root)
        # `kind`, not just the task. The review row is where a task-less
        # finding hangs, and anything that runs `v4 derive` against it raises
        # the whole battery there too -- measured here: 32 open claims on it,
        # of which 3 were findings and 29 were `fail-closed`, `test`, `lint`
        # and the rest. This line said "32 raised by a review", which is one
        # output standing for two different facts.
        # And not one somebody deferred. A deferral is a decision with a
        # reason and a target written down, and `v4 review defer` is one of the
        # two endings this row is about -- counting those as "nobody closed it"
        # made this line disagree with the `deferrals` row four lines down and
        # print advice ("close it with a test") against a decision already on
        # the record. There were twelve of them the first time both rows ran.
        rows = conn3.execute(
            "SELECT c.id, c.file, c.symbol FROM claim c WHERE c.task_id = ? "
            "AND c.kind = 'review-finding' "
            "AND NOT EXISTS (SELECT 1 FROM attempt a WHERE a.claim_id = c.id "
            "AND a.exit_code = 0) "
            "AND NOT EXISTS (SELECT 1 FROM event e WHERE e.claim_id = c.id "
            "AND e.kind = 'finding_deferred') "
            "ORDER BY c.rowid", (_led3.REVIEW_TASK,)).fetchall()
        if rows:
            named = ", ".join(f"{r['file']}::{r['symbol']}" for r in rows[:3])
            out.append(_c(
                WARN, "review findings",
                f"{len(rows)} raised by a review and neither closed nor "
                f"deferred: {named}"
                + (f" … +{len(rows) - 3}" if len(rows) > 3 else ""),
                f"a review finding closes one way -- a test that fails at the "
                f"parent, passes at HEAD, and runs the symbol -- or it is "
                f"deferred, which is a decision with a target:\n"
                f"    v4 --repo . status --task {_led3.REVIEW_TASK}\n"
                f"    v4 --repo . review close --claim <id> --test <path> "
                f"--command '<cmd with {{path}}>' --parent <commit>\n"
                f"    v4 --repo . review defer --claim <id> --why '…' "
                f"--target '…'"))



def _check_deferrals_the_event_and_the_file_agree(root, out):
    """Do a deferral's ledger event and its committed file agree?"""
    with _reports(out, "deferrals"):
        # `review`, not `ledger`. This reconciles the review domain -- the two
        # event kinds, the `.v4/deferred/` layout and the `why`/`target`
        # contract -- and all four of those are named in `review`, which was
        # spelling them a second time one layer down in the store adapter.
        from . import review as _rev4
        _conn4 = _reader(root)
        bad_defer = _rev4.reconcile_deferrals(_conn4, root)
        out.append(_c(
            WARN if bad_defer else OK, "deferrals",
            (f"{len(bad_defer)} disagreement(s): {bad_defer[0]}"
             + (f" … +{len(bad_defer) - 1}" if len(bad_defer) > 1 else ""))
            if bad_defer else "every deferral has a row and a committed record",
            "`v4 review defer` writes both halves. Unlike a signature this holds "
            "nothing -- a deferral makes no claim terminal, so a stale one costs "
            "the record of a decision and not the decision's effect"
            if bad_defer else ""))



def _check_the_test_that_closed_a_finding_is_still_here(root, out):
    """Does every finding closed by a test still name a test file that exists?

    The binding between a finding and the test that closed it is a path, and a
    rename breaks it silently. Measured here: four findings closed by
    `tests/test_sweep_repairs.py`, which was renamed in the same commit that
    closed 195 of them. Their next run was exit 5, and the three rows that
    count unfinished work each look for something else -- `review findings`
    excludes anything with a passing attempt, `outlived findings` counts exit 1,
    `deferrals` counts deferrals.

    Not a gate, and not `BAD`: the repair happened, and what is missing is the
    evidence, which is recoverable by re-binding to wherever the test lives now.
    """
    with _reports(out, "closing tests"):
        from . import review as _rev
        conn = _reader(root)
        gone = _rev.closing_tests_that_are_gone(conn, root)
        if not gone:
            out.append(_c(OK, "closing tests",
                          "every finding closed by a test names a file that is here"))
            return
        named = "; ".join(f"{cid}: {why}" for cid, _w, _t, why in gone[:2])
        out.append(_c(
            WARN, "closing tests",
            f"{len(gone)} finding(s) whose closing test is not a file here. "
            f"{named}" + (f" … +{len(gone) - 2}" if len(gone) > 2 else ""),
            _rev.HOW_TO_NAME_A_TEST + " The repair is on the record and its "
            "proof is not reachable. Re-bind and run it again:\n"
            "    v4 review close --claim <id> --test <path> "
            "--command '<cmd with {path}>' --parent <the same commit>\n"
            "    v4 check --task repo-review --claim <id>"))


def _check_what_this_repo_installed_is_still_current(root, out):
    """Is what this repo installed still the framework's current bytes?

    `.v4/installed.json` records the bytes this framework shipped, so it tells
    an edit from an untouched copy. It cannot tell an adopter that the framework
    has moved on, because it holds what *was* shipped -- and that is the way an
    adopter actually goes stale. The only mechanism that would have caught it is
    `.github/monitor/PROMPT.md` §3, a person reading the sync diff, and that
    file is itself one of the ones that goes stale.

    Measured on the reference adopter the day this row was written: 99 current,
    **18 behind**, 2 missing, 4 edited there. Two of the eighteen were the
    monitor brief, whose first of four duties named a `facts-current` claim that
    nothing has raised since it was renamed -- so the audit that exists to catch
    drift was pointed at a trigger that does not fire.

    `warn`, not `BAD`: being behind is a thing to do, not a thing that is
    broken, and `v4 install` is the whole of it.
    """
    with _reports(out, "installed"):
        from . import install as install_mod
        src = install_mod.source_root()
        if root.resolve() == src:
            out.append(_c(OK, "installed",
                          "this is the framework itself -- nothing to install here"))
            return
        rows = install_mod.copy_files(
            src, root, install_mod.installable_kinds(src, root)[0], dry=True)
        by = {}
        for rel, how in rows:
            by.setdefault(how, []).append(rel)
        behind = by.get(install_mod.BEHIND, [])
        missing = by.get(install_mod.MISSING, [])
        if not behind and not missing:
            out.append(_c(OK, "installed",
                          f"{len(by.get(install_mod.CURRENT, []))} file(s) match the "
                          f"framework at {src}"))
            return
        named = ", ".join((behind + missing)[:3])
        out.append(_c(
            WARN, "installed",
            f"{len(behind)} behind, {len(missing)} missing: {named}"
            + (f" … +{len(behind) + len(missing) - 3}"
               if len(behind) + len(missing) > 3 else ""),
            "the programs judging this repo are not the ones the framework "
            "ships. `v4 install --check` names every one; `v4 install` brings "
            "them over and leaves anything this repo edited alone"))


def _check_findings_that_outlived_their_task(root, out):
    """Which findings FAILed on a task that has ended and were never settled?

    A claim ends three ways -- answered, signed, retracted -- and a task being
    abandoned creates a fourth the taxonomy never named. The next task starts
    from a new base, so every delta gate finds no delta and stops asking: the
    finding is not wrong, it is unasked, and nothing counted it. Measured here
    the day this row was written: 27, across kinds including `fail-closed`,
    `scope` and `lint`.

    Not a gate. These are on tasks that have ended and nothing can un-end them;
    what was missing is that a number nobody prints becomes a number nobody
    knows.
    """
    with _reports(out, "outlived findings"):
        from . import ledger as _led5
        conn5 = _reader(root)
        ended = [r["task_id"] for r in conn5.execute(
            "SELECT DISTINCT task_id FROM event WHERE kind IN ('shipped', "
            "'abandoned') AND task_id IS NOT NULL")]
        exclude = []
        try:
            exclude = json.loads((root / config.CONFIG).read_text()).get(
                "derive_exclude") or []
        except (OSError, ValueError):
            pass
        rows = []
        for tid in ended:
            for cid, kind, file, symbol in _led5.unsettled_fails(conn5, tid,
                                                                 exclude):
                rows.append((tid, cid, kind, file, symbol))
        if not rows:
            out.append(_c(OK, "outlived findings",
                          "every finding on an ended task was answered, signed "
                          "or retracted"))
            return
        named = ", ".join(f"{k} in {t}" for t, _c_id, k, _f, _s in rows[:3])
        out.append(_c(
            WARN, "outlived findings",
            f"{len(rows)} finding(s) FAILed on a task that has ended and were "
            f"never settled: {named}"
            + (f" … +{len(rows) - 3}" if len(rows) > 3 else ""),
            "a new task starts from a new base, so a delta gate finds no delta "
            "and stops asking -- the finding is not wrong, it is unasked. "
            "Repair it in a task that touches the file, or sign it:\n"
            "    v4 --repo . status --task <the ended task>\n"
            "    v4 --repo . risk accept --claim <id> --kind unprovable --why '…'"))


def _check_the_ledger_schema_is_the_one_this_kernel_writes(root, out):
    """Does the ledger on disk carry the columns this kernel writes?"""
    try:
        from . import ledger
        conn = _reader(root)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(attempt)")}
        want = {"claim_digest", "scheme"}
        out.append(_c(BAD if want - cols else OK, "ledger",
                      f"attempt is missing {sorted(want - cols)}" if want - cols
                      else "schema is current",
                      "connect() migrates on open; if this persists the "
                      "database is not the one being opened" if want - cols else ""))
    except Exception as exc:                                    # noqa: BLE001
        out.append(_c(WARN, "ledger", f"could not open: {exc}"))



def run(repo_root: Path):
    """Every question this report asks, in order.  The call list is the report.

    These were one 605-line function emitting 36 rows, navigated by hand-written
    section comments; each group states its own inputs by reading them rather
    than inheriting a local from whichever group ran before it.

    The split named each one by truncating its section comment mid-phrase --
    `_check_and_something_can_actually_raise`,
    `_check_the_two_questions_that_need` -- and gave all of them the same
    docstring, so this list, which is the table of contents for the whole
    report, said nothing about any row in it. A name here is the only summary a
    reader gets before running the command: it says what the row asks, and the
    docstring says it as a question.
    """
    from . import config as config_mod
    root = Path(repo_root).resolve()
    out = []

    _check_config(root, out)
    _check_facts_load_under_the_name_the_loader_prefers(root, out)
    _check_every_kind_points_at_a_registered_checker_on_disk(root, out)
    _check_something_can_actually_raise_each_kind(root, out)
    _check_the_questions_that_need_a_live_environment_have_a_command(root, out)
    _check_lens_files_that_will_not_load(root, out)
    _check_registered_hashes_match_what_is_on_disk(root, out)
    _check_rows_in_the_facts_table_nobody_has_read_yet(root, out)
    _check_the_detectors_are_registered_and_unchanged(root, out)
    _check_every_detector_raises_a_registered_kind(root, out)
    _check_delta_checkers_have_their_baseline(root, out)
    _check_reads_covers_the_paths_a_checker_names(root, out)
    _check_kinds_this_repo_keeps_failing_to_answer(root, out)
    _check_a_registered_checker_that_has_never_executed(root, out)
    _check_hooks_are_called_and_not_merely_present(root, out)
    _check_the_after_gate_is_called_and_not_merely_present(root, out)
    _check_ci_can_actually_walk_the_chain(root, out)
    _check_path_lists_naming_things_that_are_not_here(root, out)
    _check_the_path_the_hooks_use_to_find_the_framework(root, out)
    _check_tasks_that_were_opened_and_never_ended(root, out)
    _check_findings_a_review_raised_and_nobody_closed(root, out)
    _check_deferrals_the_event_and_the_file_agree(root, out)
    _check_the_test_that_closed_a_finding_is_still_here(root, out)
    _check_what_this_repo_installed_is_still_current(root, out)
    _check_findings_that_outlived_their_task(root, out)
    _check_the_ledger_schema_is_the_one_this_kernel_writes(root, out)
    _close_reader()
    return out


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()
