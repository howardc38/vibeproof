"""Checker registration.  SPEC.md §12.

A checker only enters the registry after the kernel has watched it fail on
something known-broken and pass on something known-clean.  rev 1 put "each one
has to catch something real once" in a stage completion criterion, which covers
the hand-written batch and nothing `checker-author` produces later.  Here it is
a precondition of being usable at all.

Determinism is checked too: a checker that answers differently on identical
input cannot support the staleness rule, because the same bytes have to keep
giving the same answer.

Honest limit, stated once so nobody has to rediscover it: an author who knows
the fixtures can write a checker that satisfies exactly these and detects
nothing else.  This raises the cost of a hollow checker.  It does not make one
impossible, and the anchor for that is a human reading the checker's diff.
"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import config as config_mod

from . import detector_protocol, hashing, runner

RED, GREEN, BYPASS = "red", "green", "bypass"

#: How many evasion attempts a checker has to survive.  Three rather than five,
#: because a bypass case costs more to write than a red one -- it has to be a
#: real attempt at the specific rule, not a variation on a theme.
MIN_BYPASS = 3


class RulerMoved(RuntimeError):
    """The acceptance criteria changed while a measurement round was open."""


def _acceptance_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def open_round(conn, acceptance_path, label):
    """Start a measurement round and pin the criteria to it.

    Freezing is scoped to a round rather than held forever, because a ruler
    nobody may ever correct does not stay frozen -- it stops being written
    down, which is worse. Between rounds the criteria may change, and every
    measurement taken under the old ones is re-run rather than grandfathered.

    Inside a round there is no judgement to make and so nothing to rationalise:
    the criteria are pinned, and assert_ruler_unmoved refuses rather than asks.
    """
    from .ledger import insert
    path = Path(acceptance_path)
    if not path.is_file():
        raise RulerMoved(
            f"{path} does not exist, so there is nothing to freeze. `v4 init` "
            f"writes the shape; the criteria in it are yours to answer, the same "
            f"way `test_command` is.")
    try:
        criteria = json.loads(path.read_text()).get("criteria")
    except json.JSONDecodeError as exc:
        raise RulerMoved(f"{path} does not parse: {exc}") from exc
    if criteria == "TODO" or not criteria:
        raise RulerMoved(
            f"{path} still says the criteria are TODO. Freezing a ruler nobody "
            f"has written is the failure the freeze exists to prevent, performed "
            f"on schedule. Write what a deliverable has to satisfy, then open the "
            f"round.")
    sha = _acceptance_sha(path)
    insert(conn, "event", task_id=None, claim_id=None, kind="round_opened",
           actor="kernel",
           payload={"label": label, "acceptance_sha": sha,
                    "acceptance": json.loads(path.read_text())},
           created_at=datetime.now(timezone.utc).isoformat())
    return sha


def current_round(conn):
    row = conn.execute(
        "SELECT * FROM event WHERE kind IN ('round_opened','round_closed') "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None or row["kind"] == "round_closed":
        return None
    return json.loads(row["payload"])


def assert_ruler_unmoved(conn, acceptance_path):
    """Refuse to measure if the criteria moved since the round opened.

    This is the one rule that cannot be left to discipline. Every mid-round
    amendment arrives with a good local justification -- that is exactly how
    V3 grew from 4,308 lines to 37,511.
    """
    rnd = current_round(conn)
    if rnd is None:
        return None
    now = _acceptance_sha(Path(acceptance_path))
    if now != rnd["acceptance_sha"]:
        raise RulerMoved(
            f"acceptance criteria changed mid-round '{rnd['label']}' "
            f"({rnd['acceptance_sha']} -> {now}). Close the round and re-measure "
            f"everything under the new criteria, or restore the old file. "
            f"Amending now would be grading work against a ruler adjusted after "
            f"the work was seen."
        )
    return rnd


def close_round(conn, label, note=""):
    from .ledger import insert
    insert(conn, "event", task_id=None, claim_id=None, kind="round_closed",
           actor="kernel", payload={"label": label, "note": note},
           created_at=datetime.now(timezone.utc).isoformat())


def _case_payload(files) -> tuple:
    """One case's content, as something two cases can be compared by.

    Names are excluded on purpose: renaming is exactly the move this is written
    to see through. Nothing else is. The first version skipped `.v4/` on the
    grounds that a case's config is its harness rather than its payload, and
    `facts_coverage/bypass/near_miss_declaration` disproved it: its `app/x.py`
    is byte-identical to `red/outbound_http_added` and its
    `.v4/facts.fixture.json` declares `requests.get` where the code calls
    `requests.post`. The near miss *is* the evasion, and it lives entirely in
    the table. For a checker the table drives, the table is the payload.

    So: same case only if every file matches. Two cases that differ in one byte
    of one file are two cases, and a copy is a copy.
    """
    # Content, sorted by content. Keying on the filename put the one thing the
    # sentence above says is excluded back into the comparison: `cp
    # red/adds_a_checker.py bypass/renamed.py` compared unequal and passed, and
    # renaming is the move this is written to see through.
    out = []
    for f in files:
        try:
            out.append(Path(f).read_bytes())
        except OSError:
            out.append(b"")
    return tuple(sorted(out))


def _fixture_cases(fixtures_dir: Path, colour: str):
    """Each case is (name, [files...]).  A case is one file, or a directory of them."""
    root = fixtures_dir / colour
    if not root.is_dir():
        return []
    cases = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            files = sorted(p for p in entry.rglob("*") if p.is_file())
            if files:
                cases.append((entry.name, files))
        elif entry.is_file():
            cases.append((entry.name, [entry]))
    return cases


def _subject_for(case_files, repo_root: Path, kind: str, case_root: Path = None):
    """A fixture is normally a file the checker reads.

    Some checkers judge a whole repo instead -- `test` runs a declared command,
    `scope` diffs against a base. Those cannot be exercised by pointing at a
    file, so a fixture case that is itself a directory containing `.v4/` is
    treated as its own repo root. Without this, exactly the checkers with the
    widest reach are the ones that could never be gated, which is how a stub
    gets in.
    """
    root = repo_root
    if case_root is not None and (case_root / config_mod.CONFIG).is_file():
        root = case_root
        refs = [{"kind": "file", "path": str(p.relative_to(root))}
                for p in case_files if root in p.parents or p.parent == root]
    else:
        refs = [{"kind": "file", "path": str(p.relative_to(repo_root))} for p in case_files]
    # A case may carry its own params. Without this, any checker whose answer
    # depends on params -- `scope` is the obvious one -- can only ever be
    # exercised on one input, and the fixtures stop meaning anything.
    params, symbol, task_id = {}, "", None
    if case_root is not None:
        pf = case_root / ".v4" / "fixture.json"
        if pf.is_file():
            spec = json.loads(pf.read_text())
            params = spec.get("params", {})
            symbol = spec.get("symbol", "")
            # A case may name its own subject. The default -- every file in the
            # directory, sorted -- puts .v4/config.json first, which is a
            # perfectly good subject for nothing.
            if spec.get("subject_refs"):
                refs = spec["subject_refs"]
            # And its own task id. A checker that reads the ledger by task --
            # `request-coverage` reads `task.request` -- cannot be exercised at
            # all against a synthetic id no row carries, so every case came back
            # UNSUPPORTED and the gate could not be satisfied by any behaviour.
            # The id stays synthetic-looking below when a case does not ask.
            task_id = spec.get("task_id") or None
    # Ids that look like real ones. A checker that could tell it was being
    # registered would only have to behave during registration: two lines of
    # `if subject["task_id"] != "fixture": return 0` pass every fixture and go
    # permanently green in production. The gate is the only mechanical cost on
    # a checker an LLM wrote, so it must not announce itself.
    seed = hashlib.sha256(f"{kind}\x1f{root}".encode()).hexdigest()
    return refs, {
        "claim_id": seed[:16],
        "claim_kind": kind,
        "task_id": task_id or f"t-{seed[16:24]}",
        "repo_root": str(root),
        "diff_base": "HEAD",
        "subject_refs": refs,
        "symbol": symbol,
        "file": refs[0]["path"] if refs and refs[0].get("kind") == "file" else "",
        "variant": "",
        "params": params,
    }


def verify_checker(*, repo_root, checker_path, fixtures_dir, kind,
                   facts=None, timeout_sec=120, min_cases=5):
    """Run every fixture.  Returns (ok, report dict) -- no side effects."""
    repo_root, checker_path = Path(repo_root), Path(checker_path)
    fixtures_dir = Path(fixtures_dir)

    # A fixture set may carry its own vocabulary. Handing it the adopter's
    # table instead makes the fixtures test that repo's word list rather than
    # the checker's rule, and a synthetic `client.post` stops matching for a
    # reason that has nothing to do with what is being verified.
    facts = fixture_facts(fixtures_dir, facts)
    report = {
        "checker": str(checker_path),
        "checker_sha": hashing.file_sha(checker_path),
        "kind": kind,
        "cases": [],
        "failures": [],
    }

    if not checker_path.is_file():
        report["failures"].append(f"checker not found: {checker_path}")
        return False, report

    # `bypass/` is the third class, and it answers a different question from
    # red. Red asks whether the checker tells the two states apart. Bypass asks
    # whether an author who knows the rule can slip past it -- the same defect,
    # rewritten to look like it evades: an aliased import, a wrapper function, a
    # renamed symbol, a changed suffix. The spec used to say the anchor here was
    # a person reading the checker's diff, and a person reading a diff is not a
    # mechanism.
    # What the red cases are, so a bypass can be asked whether it is one.
    red_payloads = {_case_payload(files)
                    for _name, files in _fixture_cases(fixtures_dir, RED)}
    for colour, want in ((RED, runner.FAIL), (GREEN, runner.PASS),
                         (BYPASS, runner.FAIL)):
        cases = _fixture_cases(fixtures_dir, colour)
        if colour == BYPASS:
            # Counted and then asked. The gate required three cases under
            # `bypass/` and ran each expecting exit 1 -- and a red payload also
            # exits 1, so three copies of red cases, renamed, satisfied it
            # exactly as well as three real evasions. Reproduced: deleting
            # `sweep_current`'s four real bypasses, copying three reds in and
            # renaming them gave `VERDICT: registrable`, exit 0.
            #
            # `.claude/agents/checker-author.md` already states the rule the
            # gate did not implement -- 一個 red case 嘅副本…呢個係儀式 -- and the
            # agent that writes the checker also writes the fixtures that grade
            # it, which is the one place a ritual is cheapest to perform.
            copies = [name for name, files in cases
                      if _case_payload(files) in red_payloads]
            if copies:
                report["failures"].append(
                    f"bypass/{', bypass/'.join(copies)}: byte-identical to a "
                    f"red case. A copy of a red payload passes because red "
                    f"payloads fail -- it proves the checker still catches "
                    f"what it already caught, which is what red/ is for. A "
                    f"bypass is the same defect rewritten to look like it "
                    f"evades: an aliased import, a wrapper, a renamed symbol, "
                    f"a changed suffix.")
        need = MIN_BYPASS if colour == BYPASS else min_cases
        if len(cases) < need:
            report["failures"].append(
                f"{colour}: {len(cases)} fixtures, need at least {need}"
                + (". A checker with no bypass cases has been shown to tell two "
                   "states apart and nothing more. Whether an author who knows "
                   "the rule can walk around it is a separate question, and it "
                   "has not been asked." if colour == BYPASS else ""))
        for name, files in cases:
            # `_Case`, not a fourth copy of this. Its own docstring says it
            # exists because the setup "was written three times" and that
            # "three copies of a setup is three chances to add a capability to
            # two of them" -- and it removed one, leaving this and
            # `verify_detector`. The two had already diverged in the way it
            # predicted: only this one seeded the ledger, so a detector case
            # carrying a `ledger` block was silently ignored.
            case = _Case(fixtures_dir / colour / name, repo_root, files)
            case_root, files = case.root, case.files
            refs, payload = _subject_for(files, repo_root, kind,
                                         case_root if case_root.is_dir() else None)
            payload.setdefault("params", {}).setdefault("scope_globs", ["**"])
            # A case may carry its own vocabulary, exactly as it may for a
            # detector. `case_facts` was written for `verify_detector` and this
            # function did not learn it -- the same asymmetry its own docstring
            # records, in the other direction. A checker whose verdict turns on
            # a declared value (`webhook_replay` reading `route_receivers`,
            # `bundle_secret` reading `ui_globs`) then has one of its two
            # branches ungateable: with one table per set, "declares it" and
            # "does not" cannot both be a case.
            case_table = case_facts(fixtures_dir, colour, name, facts)
            res = runner.run_checker(
                repo_root=repo_root, checker_path=checker_path, registered_sha=None,
                subject_payload=payload, subject_refs=refs, facts=case_table,
                timeout_sec=timeout_sec,
            )
            entry = {
                "colour": colour, "case": name, "want": want,
                "got": res.exit_code, "ok": res.exit_code == want,
                "stdout_head": res.stdout[:200],
            }
            if not entry["ok"]:
                # By what the case wants, not by whether it is RED. The ternary
                # split RED from "the rest", and BYPASS wants exit 1 too -- so a
                # bypass case that walked straight through, which is the finding
                # the gate exists to produce, printed "should have passed this
                # (want 1)": the sentence and the number contradicting each
                # other, in the line a reader acts on.
                verb = ("should have failed on this" if want == runner.FAIL
                        else "should have passed this")
                report["failures"].append(
                    f"{colour}/{name}: exit {res.exit_code}, {verb} (want {want})"
                )
                if res.stderr.strip():
                    report["failures"].append(f"    stderr: {res.stderr.strip()[:300]}")

            # Same bytes must give the same answer, or staleness means nothing.
            again = runner.run_checker(
                repo_root=repo_root, checker_path=checker_path, registered_sha=None,
                subject_payload=payload, subject_refs=refs, facts=case_table,
                timeout_sec=timeout_sec,
            )
            if (again.exit_code, again.stdout) != (res.exit_code, res.stdout):
                entry["ok"] = False
                report["failures"].append(f"{colour}/{name}: not deterministic across two runs")

            report["cases"].append(entry)
            case.close()

    report["passed"] = sum(1 for c in report["cases"] if c["ok"])
    report["total"] = len(report["cases"])
    return (not report["failures"]), report


def _seed_ledger(live: Path, spec) -> None:
    """Rows a case needs the ledger to already hold.  SPEC.md §12.

    The ledger lives under `.git/`, and a fixture cannot ship one: git will not
    track another repo's `.git`, and the case is git-inited here in a copy
    anyway. So a checker that reads the ledger had no way to be exercised --
    `request-coverage` reads `task.request`, and every case came back
    UNSUPPORTED with no behaviour able to change it.

    Deliberately plain rows rather than a database: what a case declares stays
    readable in the diff, which is where every other fixture keeps its meaning.
    """
    if not spec:
        return
    from .ledger import connect, insert
    conn = connect(live)
    try:
        for row in spec.get("tasks") or []:
            insert(conn, "task", id=row["id"], request=row.get("request", ""),
                   scope_globs=row.get("scope_globs", ["**"]),
                   base_commit=row.get("base_commit", "x"),
                   created_at=row.get("created_at", "2026"))
        for ev in spec.get("events") or []:
            insert(conn, "event", task_id=ev.get("task_id"),
                   claim_id=ev.get("claim_id"), kind=ev["kind"],
                   actor=ev.get("actor", "worker"), payload=ev.get("payload", {}),
                   created_at=ev.get("created_at", "2026"))
    finally:
        conn.close()


def _newest_task(root: Path) -> str:
    """The last task this repo opened, or `probe` when it has never opened one."""
    try:
        from .ledger import connect
        conn = connect(root)
        row = conn.execute("SELECT id FROM task ORDER BY rowid DESC LIMIT 1").fetchone()
        conn.close()
        return (row["id"] if row else "") or "probe"
    except Exception as exc:                                     # noqa: BLE001
        # Said, not just returned. `probe` is a real answer -- a repo that has
        # never opened a task gets the same label -- so a reader had no way to
        # tell "no task yet" from "the ledger would not open", and the second
        # one means this registration is being probed against a subject built
        # from a guess.
        print(f"v4: the last task could not be read ({type(exc).__name__}: "
              f"{exc}); registering against a probe subject instead",
              file=sys.stderr)
        return "probe"


def probe_repo_subject(*, repo_root, checker_path, kind, timeout_sec=60):
    """Does this repo have anything for that checker to look at, right now?

    Fixtures prove a checker works. They are self-contained directories, so
    they prove nothing about whether *this* repo has a subject -- and that gap
    was reachable: `v4 register --id spec-coverage` succeeds in a repo with no
    SPEC.md, `v4 doctor` then reports every checker green, and the first task
    after that blocks on an exit 4 with nothing anywhere having said why.

    Only meaningful for a repo-scoped kind, where the checker works out its own
    subject from the tree. A subject-scoped kind is raised about particular
    files by a detector reading a particular diff, so there is no repo-wide
    question to ask here.

    Returns (exit_code, first line of output), or (None, reason) if the probe
    could not be run -- inconclusive is not a finding.
    """
    root = Path(repo_root)
    payload = runner.Subject(
        # The newest real task when there is one. A checker that reads the
        # ledger by task -- `request-coverage` reads `task.request` -- answers
        # UNSUPPORTED for a synthetic id no matter what the repo holds, so the
        # probe would warn on every install of a repo that has been worked in,
        # forever. A warning that always fires is one people stop reading.
        claim_id="probe", claim_kind=kind, task_id=_newest_task(root),
        repo_root=str(root), diff_base="HEAD", subject_refs=(),
        # `derive_exclude` was the key this payload did not have and
        # `lifecycle`'s did -- the divergence between two hand-built copies of
        # one contract. It matters most here: this probe runs at `v4 register`,
        # right after `v4 install` has written every checker's bypass fixtures
        # into the repo, so without it the probe asks repo-scoped checkers about
        # deliberately broken code and reports what they find as this repo's.
        params=runner.subject_params(
            derive_exclude=hashing.declared_exclude(root)),
    ).as_dict()
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(payload, fh)
        subject = fh.name
    # `runner._run_contained`, not `subprocess.run`. Its docstring names both
    # failures this call had, and it was written to replace exactly this shape:
    #
    #   `capture_output` waits on the pipe, not the process. A checker that
    #   exits while something it spawned still holds stdout blocks the read.
    #
    #   `timeout=` kills the direct child only. Its descendants survive.
    #
    # Measured here: `v4 install` on the reference adopter reached
    # `checkers/test.py`, which shells out to the repo's real `pytest -q`, and
    # sat there past twenty minutes -- the 60s timeout fired, the checker was
    # killed, and the orphaned pytest kept the pipe open. The install never
    # finished and left the adopter half-copied, two checkers and two fixture
    # directories short.
    #
    # This is the reason `_run_contained` exists, in the one call site that did
    # not use it. One rule, two implementations, and the unfixed one was in the
    # path every adopter runs first.
    try:
        with tempfile.TemporaryDirectory() as td:
            code, out, err, _survivors = runner._run_contained(
                [sys.executable, str(checker_path), "--subject", subject],
                root, timeout_sec, td)
    except OSError as exc:
        return None, f"the probe could not run: {exc}"
    finally:
        Path(subject).unlink(missing_ok=True)
    if code == runner.TIMEOUT:
        return None, f"the probe did not finish in {timeout_sec}s"

    text = (out or err or "").strip().splitlines()
    if runner.reported_nothing(code, out, err):
        # The probe asks "can this checker read anything here". A crash answers
        # 1, which reads as "yes, and it found something" -- the same overload
        # `runner` corrects for a real run, and the probe spawns its own process.
        last = (err or "").strip().splitlines()
        return None, f"the probe died: {last[-1][:120] if last else 'no output'}"
    return code, text[0][:120] if text else ""


def register(conn, *, checkers_json: Path, checker_id, checker_path, kinds,
             fixtures_dir, timeout_sec, repo_root, facts=None, reads=None):
    """Verify, then write to the registry.  Refuses on any fixture failure.

    Every kind is exercised, not just the first. A checker registered for two
    kinds and gated on one is gated on half of what it claims to do.
    """
    ok, report = True, {"cases": [], "failures": [], "passed": 0, "total": 0,
                        "checker_sha": hashing.file_sha(Path(checker_path)),
                        "program_sha": hashing.program_sha(repo_root, checker_path,
                                                           framework_root=runner.V4_HOME)}
    for kind in kinds:
        k_ok, k_report = verify_checker(
            repo_root=repo_root, checker_path=checker_path,
            fixtures_dir=fixtures_dir, kind=kind, facts=facts,
            # The value being registered, not `verify_checker`'s own default of
            # 120. This repo registers `test` at 1800, `surface-proof` at 1800
            # and `runtime-proof` at 600, and every one of them was graded at
            # 120 -- a third number that neither of the two SPEC.md §8 spends a
            # paragraph on reconciling.
            timeout_sec=timeout_sec,
        )
        ok = ok and k_ok
        report["cases"] += k_report["cases"]
        report["failures"] += [f"[{kind}] {f}" for f in k_report["failures"]]
        report["passed"] += k_report.get("passed", 0)
        report["total"] += k_report.get("total", 0)
    if hashing.program_sha(repo_root, checker_path, framework_root=runner.V4_HOME) != report["program_sha"]:
        ok = False
        report["failures"].append("checker program changed during fixture verification; re-run registration")
    from .ledger import insert
    # The full report only when something moved. The fixtures still run every
    # install -- that is what caught 23 kinds registering as 2 -- but writing the
    # whole case-by-case report each time records a repetition, not a fact.
    # Measured on one adopter: 23 checkers x 33 installs = 752 events, 3.48 MB,
    # 45% of an export that had grown past the secret scanner's read limit. What
    # an auditor needs is when the answer changed and what it was.
    prior = conn.execute(
        "SELECT payload FROM event WHERE kind = 'register' AND "
        "json_extract(payload, '$.checker') = ? ORDER BY id DESC LIMIT 1",
        (checker_id,)).fetchone()
    same = False
    if prior is not None:
        try:
            was = json.loads(prior["payload"])
            same = (was.get("accepted") == ok
                    and was.get("checker_sha") == report["checker_sha"]
                    and was.get("program_sha") == report["program_sha"])
        except (ValueError, TypeError, AttributeError):
            same = False
    payload = ({"checker": checker_id, "accepted": ok,
                "checker_sha": report["checker_sha"], "program_sha": report["program_sha"], "unchanged": True,
                "cases": report["total"]}
               if same else
               {"checker": checker_id, "accepted": ok,
                "checker_sha": report["checker_sha"], "program_sha": report["program_sha"], "report": report})
    insert(conn, "event", task_id=None, claim_id=None, kind="register",
           actor="kernel", payload=payload,
           created_at=datetime.now(timezone.utc).isoformat())
    if not ok and not report["total"]:
        # "Your fixtures failed" and "I found no fixtures" are not the same
        # answer, and they went down the same path. A mistyped `--fixtures`
        # therefore unregistered a checker that was working -- measured here,
        # by typing `tests/fixtures/dependency` for a directory named
        # `dependency_audit`, which removed the entry and left `dependency`
        # with `path: null`. The gate needs 5 red, 5 green and 3 bypass cases
        # to accept, so zero cases is not a fixture run at all; it is a command
        # that named nothing. Refuse, and leave the registry alone.
        report["found_no_fixtures"] = True
        return False, report

    if not ok:
        # A refusal has to take the previous entry with it. `register` only ever
        # wrote, never removed, so a checker that passed once stayed registered
        # through every later refusal -- its fixtures deleted, an analysis module
        # it imports rewritten, a bug introduced anywhere but in its own bytes.
        # `doctor` compares the checker file's own hash and sees nothing wrong.
        #
        # Measured: 21 of 23 kinds were refused on a real adoption and every one
        # kept running, because the entry from an earlier install was still
        # there. Removing it hands the question to `config.py`, which already
        # refuses a kind whose checker is not registered and says so.
        checkers_json = Path(checkers_json)
        if checkers_json.is_file():
            reg = json.loads(checkers_json.read_text())
            if reg.pop(checker_id, None) is not None:
                checkers_json.write_text(
                    json.dumps(reg, indent=2, sort_keys=True) + "\n")
                report["unregistered"] = checker_id
        return False, report

    checkers_json = Path(checkers_json)
    reg = json.loads(checkers_json.read_text()) if checkers_json.is_file() else {}
    # `reads` survives a re-register that does not pass it. Dropping it would
    # silently return the checker to "run over any tree", which is the state
    # this field exists to leave.
    prior = reg.get(checker_id) or {}
    reg[checker_id] = {
        "path": str(Path(checker_path).relative_to(Path(repo_root))),
        "sha256": report["checker_sha"],
        "program_sha": report["program_sha"],
        "timeout_sec": timeout_sec,
        "kinds": kinds,
        "fixtures": str(Path(fixtures_dir).relative_to(Path(repo_root))),
        "reads": list(reads) if reads else (prior.get("reads") or []),
    }
    checkers_json.parent.mkdir(parents=True, exist_ok=True)
    checkers_json.write_text(json.dumps(reg, indent=2, sort_keys=True) + "\n")

    # Registered, and now the second question: is there anything here for it?
    # Reported, never refused -- registering a checker before writing the file
    # it reads is a reasonable order to work in. What is not reasonable is
    # finding out one task later, from an exit code.
    try:
        kinds_cfg = json.loads(
            (Path(repo_root) / ".v4" / "claim_kinds.json").read_text())
    except (OSError, json.JSONDecodeError):
        kinds_cfg = {}
    report["no_subject"] = []
    for kind in kinds:
        spec = kinds_cfg.get(kind, {})
        if spec.get("staleness") != "repo":
            continue
        # Only an unconditional detector turns "nothing here to read" into a
        # block. A conditional one finds no subject, raises no claim, and the
        # checker is never run -- warning about it would be describing a
        # consequence that does not follow.
        if not (spec.get("detector") or "").startswith(detector_protocol.UNCONDITIONAL):
            continue
        code, line = probe_repo_subject(repo_root=repo_root, checker_path=checker_path,
                                        kind=kind, timeout_sec=timeout_sec)
        if code == runner.UNSUPPORTED:
            report["no_subject"].append({"kind": kind, "said": line})
    return True, report


#: Re-exported: the naming convention is part of the detector contract.
UNCONDITIONAL = detector_protocol.UNCONDITIONAL


def _shares_logic(repo_root, detector_path, checker_rel) -> bool:
    """Can these two disagree about the same file?

    They cannot when one answer comes from one place: `external_write`'s
    detector and checker both call `kernel.analysis.external_write` and say so
    in their docstrings, and `test_weakened`'s checker imports `_count` from its
    own detector. In that shape a green case is green for both by construction,
    and one fixture set is not a shortcut -- it is the guarantee.
    """
    from .analysis import pysource
    try:
        det = pysource.imported_modules(Path(detector_path).read_text(encoding="utf-8"))
        chk = pysource.imported_modules(
            (Path(repo_root) / checker_rel).read_text(encoding="utf-8"))
    except OSError:
        return False
    # The module that *answers the question*, not any module they both happen
    # to import. This was "share one `kernel.analysis.*` and you cannot
    # disagree", and the plumbing breaks it: `subject_files`, `pysource` and
    # `facts_grammar` are imported by half the repo, so the first time
    # `bundle_secret`'s two halves were made to read `ui_globs` from one place
    # -- which is a *vocabulary* fix, not a judgement one -- this function
    # started saying they could not disagree about a leak. They still can.
    #
    # The convention this repo already follows is what decides: the analysis
    # that holds a rule is named after the rule. `external_write`'s detector and
    # checker both import `kernel.analysis.external_write`.
    named = {Path(detector_path).stem, Path(checker_rel).stem}
    shared = {m.rsplit(".", 1)[-1]
              for m in det if m.startswith("kernel.analysis.")} & \
             {m.rsplit(".", 1)[-1]
              for m in chk if m.startswith("kernel.analysis.")}
    # `test_expectation`'s two halves share `test_expectation_diff`, which is
    # the judgement under a suffix -- an exact stem match refused a pair that
    # really cannot disagree, and refusing registration leaves `derive` skipping
    # the detector entirely. A prefix is what "named after the rule" means;
    # `facts_grammar` is still not named after `bundle_secret`.
    if any(name and (m == name or m.startswith(name + "_"))
           for m in shared for name in named):
        return True
    # Or the checker reads the detector directly, which is the same guarantee
    # arriving without a third module.
    stem = Path(detector_path).stem
    return any(m == f"detectors.{stem}" or m.startswith(f"detectors.{stem}.")
               for m in chk)


def _borrowed_from_a_checker(repo_root, fixtures_dir, detector_path):
    """Refuse a checker's fixture set when the two can disagree.  SPEC.md §2.

    The spec states the rule and had no mechanism behind it, so registering a
    detector against its checker's fixtures passed silently -- which happened
    twice within ten minutes of the gate existing. `bundle-secret` is the case
    it is about: the detector asks "does this change touch client source" and
    the checker asks "is a server secret reachable from the bundle", so the
    checker's green cases -- front-end files with no leak -- are all files the
    detector must fire on. Four of its five green cases passed anyway, because
    their paths resolved outside `ui_globs`; only the one written as a mini-repo
    failed.

    The spec's blanket wording was too strong, though, and the code says so in
    two places: where a detector and its checker share their analysis they
    cannot disagree, and there the shared set is the point rather than a
    shortcut. So the refusal is narrowed to the pairs that can.
    """
    reg = Path(repo_root) / ".v4" / "checkers.json"
    if not reg.is_file():
        return ""
    try:
        entries = json.loads(reg.read_text())
    except (OSError, json.JSONDecodeError):
        return ""
    want = Path(fixtures_dir).resolve()
    for cid, entry in sorted(entries.items()):
        if (Path(repo_root) / entry.get("fixtures", "")).resolve() != want:
            continue
        if _shares_logic(repo_root, detector_path, entry.get("path", "")):
            return ""
        return (f"{want.name} is checker {cid!r}'s fixture set, and the two do "
                f"not share an analysis module -- so they can disagree, and a "
                f"checker's green case can be a detector's red one. Give this "
                f"detector its own set, by convention {want.name}_detector.")
    return ""


def register_detector(conn, *, detectors_json: Path, detector_path, fixtures_dir,
                      repo_root, facts=None, timeout_sec=120, min_cases=3):
    """Verify, then write to the detector registry.  Refuses on any failure.

    `verify_detector` existed and printed a verdict that went nowhere. So the
    spec's own bold requirement -- a conditional detector must pass the gate --
    had nothing behind it: `derive` ran every file in `detectors/` regardless,
    `registry-consistency` compared names and not hashes, and `detector_sha`
    was computed at derivation time from the file that was about to run, so
    there was nothing to compare it against.

    Passing a gate has to leave a trace, or nothing can require it. That is the
    same argument this project makes about a hook that is installed and never
    fires.
    """
    borrowed = _borrowed_from_a_checker(repo_root, fixtures_dir, detector_path)
    if borrowed:
        return False, {"detector": str(detector_path), "cases": [], "failures":
                       [borrowed], "passed": 0, "total": 0}

    ok, report = verify_detector(
        repo_root=repo_root, detector_path=detector_path,
        fixtures_dir=fixtures_dir, facts=facts, timeout_sec=timeout_sec,
        min_cases=min_cases)

    from .ledger import insert
    insert(conn, "event", task_id=None, claim_id=None, kind="register_detector",
           actor="kernel",
           payload={"detector": Path(detector_path).name, "accepted": ok,
                    "report": report},
           created_at=datetime.now(timezone.utc).isoformat())
    if not ok:
        return False, report

    detectors_json = Path(detectors_json)
    reg = json.loads(detectors_json.read_text()) if detectors_json.is_file() else {}
    reg[Path(detector_path).name] = {
        "path": str(Path(detector_path).relative_to(Path(repo_root))),
        "sha256": report["detector_sha"],
        "fixtures": str(Path(fixtures_dir).relative_to(Path(repo_root))),
        "cases": report.get("total", 0),
    }
    detectors_json.parent.mkdir(parents=True, exist_ok=True)
    detectors_json.write_text(json.dumps(reg, indent=2, sort_keys=True) + "\n")
    return True, report


def verify_detector(*, repo_root, detector_path, fixtures_dir, facts=None,
                    timeout_sec=120, min_cases=3):
    """A detector needs a gate for the same reason a checker does, and more.

    Checkers are gated and detectors are not, which is backwards: a detector
    decides what gets checked at all. One that emits nothing looks exactly like
    one that scanned and found a clean repo -- narrow a suffix set from `.py` to
    `.pyx` and every task passes while the ship report still prints the
    detector as having run.

    Same fixture shape, different question. A red case must produce at least one
    `V4-CLAIM:` line; a green case must produce none. Exit is 0 either way --
    for a detector, anything else means it broke.
    """

    repo_root, detector_path = Path(repo_root), Path(detector_path)
    fixtures_dir = Path(fixtures_dir)
    report = {"detector": str(detector_path),
              "detector_sha": hashing.file_sha(detector_path),
              "cases": [], "failures": []}

    # The same question `register_detector` asks, asked here too.
    #
    # It was asked in one of them, so `v4 verify-detector` printed `VERDICT:
    # usable` over a fixture set `v4 register-detector` then refused -- and
    # verify-then-register is the order the documentation gives and the order a
    # person reaches for. Two commands, one question, two answers, and the one
    # that says yes is the one with no consequences.
    borrowed = _borrowed_from_a_checker(repo_root, fixtures_dir, detector_path)
    if borrowed:
        report["failures"].append(borrowed)

    if not detector_path.is_file():
        report["failures"].append(f"detector not found: {detector_path}")
        return False, report

    facts = fixture_facts(fixtures_dir, facts)

    # `BYPASS` belongs in this loop and was not in it. SPEC.md §8.7 rests the
    # whole TypeScript compromise -- regex over stripped source instead of a
    # parser -- on the sentence "嗰個妥協由 bypass fixture 睇住", and on the
    # detector side nothing was watching: these fixtures had never been
    # executed since the day they were written. The checker side ran them all
    # along (`verify` prints "including bypasses"), so the promise was half
    # kept and read as whole.
    #
    # A bypass is the same defect rewritten to look like it evades, so it wants
    # a claim exactly as a red case does. Measured before wiring it: 35 cases,
    # 34 caught, and the one gap was a shipped-table gap rather than a detector
    # one -- `client.Post` was missing from the default outbound vocabulary, so
    # the rule only closed for repos that had already written their own facts.
    for colour, want_claims in ((RED, True), (GREEN, False), (BYPASS, True)):
        cases = _fixture_cases(fixtures_dir, colour)
        # No minimum on `bypass/` here, and that is deliberate rather than an
        # oversight. The finding this wiring answers is "these fixtures never
        # ran", not "every detector owes three of them" -- six detectors use
        # their own fixture set and have none, and turning a targeted repair
        # into a blanket obligation would refuse all six the moment it landed.
        #
        # The stated gap: `verify_checker` does hold a `MIN_BYPASS` floor and
        # also refuses a bypass that is byte-identical to a red case. The
        # detector side has neither. Adding them is a separate change with its
        # own fixtures to write, and it is written down here rather than left
        # to be rediscovered.
        if colour != BYPASS and len(cases) < min_cases:
            report["failures"].append(
                f"{colour}: {len(cases)} fixtures, need at least {min_cases}")
        for name, files in cases:
            # Same mini-repo handling as verify_checker. Without it the paths
            # handed to the detector are fixture-relative to the real repo, so
            # a rule matching `src/**` sees
            # `tests/fixtures/<set>/red/<case>/src/app.ts` and never fires --
            # every red case fails for a reason that has nothing to do with the
            # detector.
            # `_Case`, the same one `verify_checker` and `self_trigger` use.
            # This was the second inline copy, and it was the one that got
            # capabilities last: mini-repo support first, then
            # `parent_content`, each time after the same defect had been paid
            # for on the checker side. It still had neither `_seed_ledger` nor
            # a repo for a case whose `fixture.json` names no `parent_content`,
            # so one spec meant two things.
            case = _Case(fixtures_dir / colour / name, repo_root, files)
            root, files = case.root, case.files
            spec = case.spec

            rel = []
            for f in files:
                try:
                    rel.append(str(f.relative_to(root)))
                except ValueError:
                    rel.append(str(f.relative_to(repo_root)))
            # `HEAD`, the same base `verify_checker` passes. `parent_content`
            # commits the previous state and leaves the new one in the tree, so
            # `git diff HEAD` is exactly the change under test.
            rc, stdout, stderr, _out, _ms = detector_protocol.run_detector(
                root, detector_path, rel,
                case_facts(fixtures_dir, colour, name, facts),
                timeout=timeout_sec, diff_base="HEAD")
            case.close()
            claims = detector_protocol.parse_claim_lines(stdout) if rc == 0 else []
            entry = {"colour": colour, "case": name, "exit": rc,
                     "claims": len(claims),
                     "ok": rc == 0 and bool(claims) == want_claims}
            if rc != 0:
                report["failures"].append(
                    f"{colour}/{name}: exit {rc} -- a detector only ever exits 0. "
                    f"{stderr.strip()[:200]}")
            elif not entry["ok"]:
                report["failures"].append(
                    f"{colour}/{name}: {len(claims)} claim(s), expected "
                    f"{'at least one' if want_claims else 'none'}")
            report["cases"].append(entry)

    report["passed"] = sum(1 for c in report["cases"] if c["ok"])
    report["total"] = len(report["cases"])
    return (not report["failures"]), report


def fixture_facts(fixtures_dir: Path, fallback=None):
    """The fact table a fixture set judges against.

    A set that ships `facts.json` uses it; otherwise the repo's own table is
    handed over, which is the trap the spec already warns about -- the fixtures
    then test one repo's vocabulary rather than the rule. Measured here: five
    green cases went red because `require_permission` is not a symbol the
    reference repo happens to use.

    One function because this is the fourth capability that existed in
    `verify_checker`, was copied into `verify_detector`, and was missing from
    `self_trigger`.
    """
    own = Path(fixtures_dir) / "facts.json"
    if own.is_file():
        # A table that does not parse is not a table this set does not have.
        # Swallowing it handed back the fallback, so the case ran against a
        # vocabulary its author did not write and the gate recorded the verdict
        # as legitimate -- silent in both directions: a red case can go green
        # and a green case red for the same reason. This is the function whose
        # whole purpose is that a set answers for itself.
        return json.loads(own.read_text())
    return fallback


def case_facts(fixtures_dir, colour, name, fallback=None):
    """The table one case judges against, when it needs its own.

    A set gets one table, which is right while the table only *narrows* what a
    detector looks at -- `bundle_secret` and `route_auth` differ case to case in
    their source, not in their vocabulary. It stops being right for a detector
    whose decision to raise at all depends on a row being present: with one
    table per set, "declares the fact" and "does not" cannot both be a case, so
    exactly one of the two branches can ever be gated.

    Found writing the first such detector. `surface_proof` raises when the repo
    declares `ui_globs`, and every green case in its set inherited that
    declaration from the set's table and raised too.

    A case answers for itself by carrying `.v4/facts.json`, the same way it
    already carries `.v4/config.json` and `.v4/fixture.json`.
    """
    own = Path(fixtures_dir) / colour / name / ".v4" / "facts.json"
    if own.is_file():
        # See `fixture_facts`: unreadable is not absent, and the whole point of
        # this function is that a case answers for itself.
        return json.loads(own.read_text())
    return fallback


class _Case:
    """One fixture case, set up the same way for all three gates.

    This exists because it was written three times. `verify_checker` learned
    mini-repo cases; `verify_detector` did not, and every red case failed for a
    reason unrelated to the detector. `verify_checker` then learned
    `parent_content`; `verify_detector` did not, same result. And `self_trigger`
    had neither, so a detector whose whole question is "did this change weaken
    something" could not have a self-trigger case at all -- `before/` raised
    nothing, because there was no before.

    Three copies of a setup is three chances to add a capability to two of them.
    """

    def __init__(self, case_dir: Path, repo_root: Path, files):
        self.repo_root = Path(repo_root)
        self._tmp = None
        self.root = case_dir if (case_dir / ".v4").is_dir() else self.repo_root
        self.files = list(files)

        spec_path = self.root / ".v4" / "fixture.json"
        self.spec = json.loads(spec_path.read_text()) if spec_path.is_file() else {}
        if not spec_path.is_file():
            return

        # A history, for any case that ships a `fixture.json`. This used to
        # build only when `parent_content` was present, which is what
        # `verify_detector` did too -- while `verify_checker`, the third copy,
        # built for every such case *and* seeded the ledger. So this class,
        # written to end the three copies, was the weakest of them: the same
        # `.v4/fixture.json` meant one thing under `verify_checker` and another
        # under `verify_detector`, and a detector case carrying a `ledger` block
        # was silently ignored.
        self._tmp = tempfile.TemporaryDirectory()
        live = Path(self._tmp.name) / "case"
        shutil.copytree(self.root, live)
        for cmd in (["git", "init", "-q"],
                    ["git", "config", "user.email", "t@t"],
                    ["git", "config", "user.name", "t"]):
            subprocess.run(cmd, cwd=live, capture_output=True)
        parent = self.spec.get("parent_content") or {}
        # `None` for a path the case does not ship: the change deleted it.
        #
        # This read every `parent_content` path off disk, which made a delete
        # impossible to write down -- the builder died on the missing file
        # before any checker saw the case. Measured when this was found: 182
        # cases carry `parent_content` and not one of them expressed a delete,
        # while four checkers had been repaired for deleted-file handling in
        # one week. Four repairs, no fixture, because the fixture could not be
        # written.
        restore = {r: ((live / r).read_text() if (live / r).is_file() else None)
                   for r in parent}
        for r, body in parent.items():
            (live / r).parent.mkdir(parents=True, exist_ok=True)
            (live / r).write_text(body)
        subprocess.run(["git", "add", *self.spec.get("committed", [])],
                       cwd=live, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "fixture base"],
                       cwd=live, capture_output=True)
        for r, body in restore.items():
            if body is None:
                (live / r).unlink()
            else:
                (live / r).write_text(body)
        # Opt-in history for a checker that verifies a committed transition,
        # rather than a working-tree change relative to the fixture base.
        if self.spec.get("commit_current") is True:
            subprocess.run(["git", "add", "-A"], cwd=live, capture_output=True, check=True)
            subprocess.run(["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0",
                            "commit", "-qm", "fixture current"], cwd=live,
                           capture_output=True, check=True)
        _seed_ledger(live, self.spec.get("ledger"))
        self.files = [live / f.relative_to(self.root) for f in self.files]
        self.root = live

    def rel(self):
        out = []
        for f in self.files:
            try:
                out.append(str(f.relative_to(self.root)))
            except ValueError:
                out.append(str(f.relative_to(self.repo_root)))
        return out

    def close(self):
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def self_trigger(*, repo_root, detector_path, fixtures_dir, facts=None,
                 timeout_sec=120):
    """Does answering a claim this detector raises create another one?

    SPEC.md §4 makes this a condition of ship converging and, until now, said
    it in prose with nothing behind it. It is not hypothetical: the first
    external-write rule matched any outbound symbol including GET, so the
    read-back added to answer a readback claim raised a fresh readback claim
    about itself. Infinite, and only visible by trying it.

    A case is a directory holding `before/` and `after/`: the code that raises
    the claim, and the same code with the claim answered. Firing on `before`
    and not on `after` is the property. Anything else means the fix is not a
    fix, and the detector will keep the task from ever shipping.
    """

    root = Path(fixtures_dir) / "self_trigger"
    if not root.is_dir():
        return None, []                     # no cases declared
    facts = fixture_facts(fixtures_dir, facts)

    problems = []
    checked = 0
    for case in sorted(p for p in root.iterdir() if p.is_dir()):
        for phase, want_claims in (("before", True), ("after", False)):
            d = case / phase
            if not d.is_dir():
                problems.append(f"{case.name}: no {phase}/ -- a self-trigger case "
                                f"needs both halves to mean anything")
                continue
            files = sorted(p for p in d.rglob("*") if p.is_file())
            with _Case(d, Path(repo_root), files) as c:
                rc, stdout, _, _out, _ms = detector_protocol.run_detector(
                    c.root, Path(detector_path), c.rel(), facts,
                    timeout=timeout_sec, diff_base="HEAD")
            claims = detector_protocol.parse_claim_lines(stdout) if rc == 0 else []
            checked += 1
            if rc != 0:
                problems.append(f"{case.name}/{phase}: detector exited {rc}")
            elif bool(claims) != want_claims:
                if phase == "before":
                    problems.append(
                        f"{case.name}/before: raises nothing, so this case proves "
                        f"nothing about what answering it does")
                else:
                    problems.append(
                        f"{case.name}/after: answering the claim raised "
                        f"{len(claims)} more. Ship can never converge on code like "
                        f"this -- the fix is what triggers the next round.")
    return checked, problems
