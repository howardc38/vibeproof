"""Running one claim.  SPEC.md §6.1, §7.

The kernel execs the checker itself and reads the exit code from the OS.  That
is the whole answer to "the agent said PASS": nobody gets to report the result
of a program they did not run.

What it is not: the checker itself is written by an LLM (`checker-author`), so
the *meaning* of that exit code is still authored.  Registration demands a red
fixture (see register.py) to make a do-nothing checker harder to land, but that
is friction, not a boundary.
"""

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import hashing

PASS = 0
FAIL = 1
UNSUPPORTED = 4
ERROR = 5
CHECKER_TAMPERED = 6
SUBJECT_MOVED = 7
TIMEOUT = 8

#: What each number is called, for the reports a person reads.  The kernel
#: diagnoses 6, 7 and 8 itself and no command printed anything but the digit,
#: so `exit 7` was the whole message about a checker whose subject moved under
#: it -- a state the reader has no other way to name.
EXIT_NAMES = {PASS: "PASS", FAIL: "FAIL", UNSUPPORTED: "UNSUPPORTED",
              ERROR: "ERROR", CHECKER_TAMPERED: "CHECKER_TAMPERED",
              SUBJECT_MOVED: "SUBJECT_MOVED", TIMEOUT: "TIMEOUT"}


def exit_name(code) -> str:
    """`"7 SUBJECT_MOVED"`, or just the number for one nothing here defines."""
    name = EXIT_NAMES.get(code)
    return f"{code} {name}" if name else str(code)


def _now():
    return datetime.now(timezone.utc).isoformat()


def head_commit(repo_root: Path) -> str:
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root,
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else "no-head"


@dataclass(frozen=True)
class Subject:
    """What the kernel hands a checker or a detector.  SPEC.md §6.1, §2.

    The only contract that crosses from this process into theirs, and it was an
    untyped dict literal typed out by hand in three places -- `lifecycle` for a
    claim, `register.probe_repo_subject` for an install-time probe, and
    `detector_protocol.run_detector` for every detector. Nothing compared them,
    so they drifted in the way three copies drift: quietly, and in the field
    that decides what a program is allowed to look at. Measured before this,
    `params` was `{"scope_globs": ..., "forbid_globs": ..., "derive_exclude":
    ...}` from `lifecycle` and `{"scope_globs": ["**"], "forbid_globs": []}`
    from the probe -- so the probe pointed repo-scoped checkers at the bypass
    fixtures `v4 install` had just written, which is the exact run that reported
    thirteen committed credentials on a clean repo.

    `run_detector`'s own docstring records the same shape twice more: `diff_base`
    empty in one caller and not the other, `params` `{}` in one and not the
    other, each found long after it shipped.

    Frozen and keyword-only: a field added here appears in every subject or in
    none, and `as_dict` is the one place the wire names are written.
    """

    repo_root: str
    claim_id: str = ""
    claim_kind: str = ""
    task_id: str = ""
    diff_base: str = ""
    subject_refs: tuple = ()
    symbol: str = ""
    variant: str = ""
    params: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        """The JSON a checker reads off `--subject`.

        Through `asdict`, so the wire names are the field names and are written
        once. A dict literal here would be a fourth list of them, in the
        function whose whole job is that there is only one.
        """
        return asdict(self)


def subject_params(*, derive_exclude, scope_globs=("**",), forbid_globs=(),
                   **extra) -> dict:
    """`params` for a subject, with the repo's exclusions stated rather than left out.

    `derive_exclude` has no default on purpose. It is the key the three
    hand-built payloads disagreed about, and a default here would restore the
    disagreement with a shorter spelling: a caller that forgets it would look
    exactly like a caller that decided against it. Those are different, and both
    are real -- `subject_files.exclusions` records why a fixture run passes `()`
    ("a fixture run is *about* the broken code, and excluding it would make
    every red case pass"), while a run against a live repo that omits it points
    detectors at code the repo has said is not under judgement.

    `extra` is what a single claim needs that its kind cannot know in advance --
    `review-finding` carries the test chosen to close it.
    """
    return {"scope_globs": list(scope_globs), "forbid_globs": list(forbid_globs),
            "derive_exclude": list(derive_exclude), **extra}


class CheckResult:
    def __init__(self, exit_code, stdout, stderr, argv, duration_ms,
                 subject_digest, checker_sha, out_payload=None):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.argv = argv
        self.duration_ms = duration_ms
        self.subject_digest = subject_digest
        self.checker_sha = checker_sha
        self.out_payload = out_payload

    @property
    def answered(self):
        return self.exit_code == PASS


def run_checker(*, repo_root, checker_path, registered_sha, subject_payload,
                subject_refs, latest_attempt_id=None, facts=None, timeout_sec=300,
                emit_baseline=False, registered_program_sha=None):
    """Exec one checker and come back with what the OS said.

    Order matters and each step buys something specific:

      verify sha   a FAIL whose standard fix is "edit the checker" is not a
                   fix.  rev 1 stored this hash in two places and read it in
                   neither.
      hash before  so we can tell later whether the answer still applies
      exec         the kernel's own subprocess, not a reported one
      hash after   catches a concurrent edit landing mid-run
    """
    repo_root = Path(repo_root)
    checker_path = Path(checker_path)

    disk_sha = hashing.file_sha(checker_path)
    # Old registries pin the entry alone. New registrations also bind the same
    # transitive program fingerprint already used to expire earlier answers.
    program = hashing.program_sha(repo_root, checker_path, framework_root=V4_HOME)
    if registered_sha and disk_sha != registered_sha:
        return CheckResult(
            CHECKER_TAMPERED, "",
            f"checker on disk ({disk_sha[:12]}) is not the registered one "
            f"({registered_sha[:12]}); refusing to run",
            [], 0, {}, program,
        )
    if registered_program_sha is not None and program != registered_program_sha:
        return CheckResult(
            CHECKER_TAMPERED, "",
            "checker program differs from the registered dependencies; "
            "re-register and pass its fixtures before running",
            [], 0, {}, program,
        )

    before = hashing.subject_digest(repo_root, subject_refs, latest_attempt_id)

    with tempfile.TemporaryDirectory() as td:
        subj = Path(td) / "subject.json"
        subj.write_text(json.dumps(subject_payload, indent=2, sort_keys=True))
        # sys.executable, not "python3": that name resolves off a PATH the
        # worker controls, so a shim in front of it decides every checker's exit
        # code -- verified here, and it turned a FAIL into a PASS. Hashing a
        # checker's bytes and then handing them to an interpreter somebody else
        # picked verifies the wrong half of the pair.
        argv = [sys.executable, str(checker_path), "--subject", str(subj)]

        if facts is not None:
            fpath = Path(td) / "facts.json"
            fpath.write_text(json.dumps(facts, indent=2, sort_keys=True))
            argv += ["--facts", str(fpath)]

        outp = Path(td) / "out.json"
        argv += ["--out", str(outp)]

        # `v4 derive` tells a worker carrying inherited debt that "a delta kind
        # takes `--emit-baseline`". Three checkers do -- `structural_lint`,
        # `external_write`, `fail_closed` -- and no `v4` subcommand could hand
        # it to them, so the one route this framework documents (`USING.md`:
        # everything goes through `./bin/v4`) did not reach the flag its own
        # advice named. An adopter looked, found the flag in no CLI, and signed
        # a `baseline_raise` risk instead, which leaves the baseline file empty
        # and asks the next task the same question.
        #
        # Passed through rather than interpreted: what `--emit-baseline` prints
        # is the checker's business, and a kernel that formats it would be a
        # second owner of the baseline format.
        if emit_baseline:
            argv += ["--emit-baseline"]

        t0 = time.monotonic()
        code, out, err, survivors = _run_contained(argv, repo_root, timeout_sec, td)
        dur = int((time.monotonic() - t0) * 1000)
        if survivors:
            # A direct child that exited while its descendants keep running
            # means the exit code is not a complete observation of what happened
            # -- something the checker started is still going, and whatever it
            # was going to do has not finished.
            code = ERROR
            err = (f"{err}\nthe checker exited but left {survivors} process(es) "
                   f"running. Its exit code describes a run that had not "
                   f"finished, so it is not an answer.").strip()

        if reported_nothing(code, out, err):
            code = ERROR
            err = (f"{err}\nthis checker died of an uncaught exception. Exit 1 "
                   f"from a crash and exit 1 from a finding are the same number; "
                   f"the traceback above is what actually happened.").strip()

        payload = None
        if outp.is_file():
            try:
                # Through `redact` like stdout and stderr, and for the identical
                # reason -- this lands in the same append-only table and then in
                # `.v4/ledger_export.jsonl`, which is committed. Only the streams
                # were filtered, so the one route a checker uses to write
                # *structured* output was the unfiltered one. `secret_scan.py`
                # redacts its own `--out` by hand and says why; that is one
                # checker being careful, and this is the floor under all of them.
                # With the root. Two of the three redaction calls in this file
                # carried it and this one did not, so a repo's own declared
                # credential families reached a checker's streams and not its
                # structured payload -- into the same append-only row.
                payload = redact_json(json.loads(outp.read_text()),
                                      root=repo_root)
            except json.JSONDecodeError as exc:
                code, err = ERROR, f"{err}\n--out was not valid JSON: {exc}"

    if registered_program_sha is not None and hashing.program_sha(
            repo_root, checker_path, framework_root=V4_HOME) != program:
        return CheckResult(SUBJECT_MOVED, out,
                           f"{err}\nchecker program changed while it ran; this is not an answer",
                           argv, dur, before, program)
    after = hashing.subject_digest(repo_root, subject_refs, latest_attempt_id)
    if after != before:
        moved = sorted(k for k in before if before[k] != after.get(k))
        return CheckResult(
            SUBJECT_MOVED, out,
            f"{err}\nsubject changed while the checker ran: {', '.join(moved)}",
            argv, dur, before, program,
        )

    return CheckResult(code, out, err, argv, dur, before, program, payload)


#: The third `cost_observation.source`. `self_reported` is what an agent says
#: it spent and `transcript_derived` is what a transcript says; this is what the
#: kernel timed itself, which is the only one of the three nobody can shade.
COST_OBSERVED = "observed"


def record(conn, claim_id, result, *, repo_root, config_sha, worktree,
           staleness_stamp=None, facts_sha=""):
    """`staleness_stamp` is whatever key this claim's kind is judged by.

    A repo-scoped claim is judged against working-tree content, a subject-scoped
    one against HEAD. The column has to hold the same value the staleness check
    reads back, and the ledger is append-only, so it is decided here rather than
    corrected afterwards.
    """
    from .ledger import append_attempt, insert
    started = _now()
    # Wall-clock is observed, so the kernel records it. Tokens are not: running
    # a checker costs none, and the spend that matters happens in an agent's
    # reasoning, which only the platform sees. A number an agent reports about
    # itself cannot be used to judge that agent, so it arrives separately and
    # carries where it came from.
    # `task_id` was written as None on every row -- 980 of them on one adopter,
    # the whole column empty -- while this function holds the claim it belongs
    # to and the claim names its task. Not a decision anybody made: the value
    # was one query away and nobody wrote the query, so "what did this task
    # cost" had no answer while the rows to answer it were being written.
    owner = conn.execute("SELECT task_id FROM claim WHERE id = ?",
                         (claim_id,)).fetchone()
    insert(conn, "cost_observation", task_id=owner[0] if owner else None,
           claim_id=claim_id,
           # `observed`, and the schema comment at `ledger.py` declares two
           # values -- `self_reported | transcript_derived`. A third that
           # nothing declares is a bucket `v4 cost` prints and no reader can
           # look up, so the comment is where the set is stated and this is
           # the row that has to be in it.
           source=COST_OBSERVED, tokens=None, wall_ms=result.duration_ms,
           created_at=started)
    if result.out_payload is not None:
        # Kept, because the alternative is what was here before: the kernel
        # parsed this, held it in memory, and dropped it. A checker writing
        # per-site line numbers into --out was writing them into nothing.
        # `owner` is already in scope and was already the fix for the identical
        # omission one insert up. Written as `None` here, every structured
        # checker output in the ledger was unattributable to a task by query --
        # reachable only one claim at a time through `cli.py`.
        insert(conn, "event", task_id=owner[0] if owner else None,
               claim_id=claim_id, kind="checker_out",
               actor="kernel", payload=result.out_payload, created_at=started)
    return append_attempt(
        conn,
        claim_id=claim_id,
        subject_digest=result.subject_digest,
        checker_sha=result.checker_sha,
        config_sha=config_sha,
        head_commit=staleness_stamp or head_commit(Path(repo_root)),
        worktree=str(worktree),
        # `""` for the 22 kinds whose checker never reads the table, so
        # an unrelated facts edit does not expire their answers.
        facts_sha=facts_sha,
        argv=result.argv,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        started_at=started,
        ended_at=_now(),
        duration_ms=result.duration_ms,
    )


#: Shapes that must not reach an append-only table.  Deliberately short: this is
#: a floor under a checker's own discipline, not a secret scanner. The scanner
#: is `checkers/secret_scan.py` and it is far better at this.
#: `redact` and its helpers moved to `kernel/analysis/redaction.py`, which is
#: where a pure-text function three `kernel/` modules need belongs. Re-exported
#: here because this module's own callers name it, and because the docstrings
#: of two checkers point at `runner.redact` by that name.
from .analysis.redaction import redact, redact_json        # noqa: E402,F401


#: Where this framework's `kernel/` package lives, from the package itself.
#:
#: An adopter has `checkers/` but no `kernel/`; the launcher `bin/v4` supplies
#: `PYTHONPATH=$V4_HOME` and every checker subprocess inherited it. Inheritance
#: is not a contract. Measured: `v4 install --repo <adopter>` run from the
#: framework side gave the child no such variable, all 27 checkers died on
#: `from kernel.analysis import ...`, and 21 of 23 kinds were refused by their
#: own fixtures -- on an install that reported success and went on to write
#: CLAUDE.md.
V4_HOME = str(Path(__file__).resolve().parent.parent)


#: What a checker is entitled to, by name.
#:
#: This function said "the environment a checker is entitled to" and then wrote
#: `dict(os.environ)`, which is not an entitlement, it is everything. Every
#: checker and every detector -- 27 plus 30 programs an LLM wrote, on the
#: `derive` and `check` paths -- was handed the whole of a developer shell: the
#: cloud tokens, the database URLs, the API keys. Their stdout and stderr go
#: into an append-only table that `ship` exports to a committed, scanned file,
#: and `analysis/redaction.py` exists precisely because a program that prints
#: its own inputs leaks without meaning to. Redaction is documented there as a
#: floor rather than a scanner; the floor should not have to hold the largest
#: set of credentials on the machine.
#:
#: `PYTHONPATH` is not here because it is not inherited -- it is set below, and
#: has to be: an adopter has `checkers/` and no `kernel/`. The rest are what a
#: program needs to *run* rather than what it needs to know: where the binaries
#: are, where to write a temp file, how to decode bytes.
#:
#: A checker that needs more than this is a checker with an undeclared input.
#: Dropping a variable it needed is loud -- the command is not found, the
#: locale is wrong, the test fails -- which is the direction this trades in:
#: nothing here can turn a leak into a passing run, and everything here fails
#: in the open.
CHILD_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ")

#: And this framework's own variables, which name probe directories, the task,
#: the repo and where the kernel lives. Kept by prefix rather than by list, so
#: a new one does not have to be added in two places to work.
CHILD_ENV_PREFIX = "V4_"


def child_env(base=None):
    """The environment a checker is entitled to, regardless of who spawned us.

    An allowlist, and it is short. See `CHILD_ENV_KEYS` for what is in it and
    why the alternative -- the whole parent environment -- is the shape this
    refuses.
    """
    src = dict(base if base is not None else os.environ)
    env = {k: v for k, v in src.items()
           if k in CHILD_ENV_KEYS or k.startswith(CHILD_ENV_PREFIX)}
    env["PYTHONPATH"] = V4_HOME + (os.pathsep + src["PYTHONPATH"]
                                   if src.get("PYTHONPATH") else "")
    return env


def reported_nothing(code, stdout, stderr) -> bool:
    """Did a checker claiming a finding actually report one?

    Exit 1 is overloaded. A checker that finds a violation exits 1 and says what
    it found; a checker that dies of an uncaught exception -- a bad import, a
    missing dependency, an edit that did not parse -- also exits 1, writes a
    traceback to stderr, and says nothing on stdout. The two are indistinguishable
    by exit code alone, and `ERROR` exists for the second only if the program
    lives long enough to catch its own exception. An import error happens before
    any handler.

    Measured: this is how 21 checkers were refused by fixtures they pass, and
    how their red cases "passed" -- a red case wants exit 1 and a crash gives it
    one. A gate that a broken checker satisfies is the hollow scanner this
    framework exists to refuse, in the gate itself.

    The test is CPython's own behaviour, not a guess about output style: a
    process that dies from an uncaught exception writes `Traceback (most recent
    call last):` to stderr and nothing to stdout. Requiring stdout alone was too
    broad -- `checkers/test.py` reports a failing suite on stderr and is right to
    exit 1 -- so both conditions have to hold.
    """
    if code != FAIL or (stdout or "").strip():
        return False
    err = stderr or ""
    if "Traceback (most recent call last):" in err:
        return True
    # The third cause in the list above is the false one. A `SyntaxError` in the
    # file CPython was asked to run is reported by the *parser*, not by the
    # exception machinery, so stderr is `  File "x.py", line 2` / caret /
    # `SyntaxError: invalid syntax` with no `Traceback` header at all. Verified:
    # a two-line file containing `def f(:` exits 1, writes nothing to stdout,
    # and the header count is zero -- so the one crash a worker actually
    # produces by editing a checker was recorded as FAIL, a real finding,
    # instead of ERROR, the hollow-scanner case this exists for.
    return any(err.lstrip().startswith(n + ":") or f"\n{n}:" in err
               for n in ("SyntaxError", "IndentationError", "TabError"))


def _process_group_size(pgid) -> int:
    """How many processes are still in this group.  `0` when it is gone.

    `ps -o pgid=` rather than `killpg(pgid, 0)`: the signal probe answers "is
    anybody there", which is the question the caller does *not* ask. A count
    that cannot count is a claim the code does not support, in the one artefact
    a reader gets for this condition.

    Falls back to the probe when `ps` is unavailable or unreadable -- knowing
    that at least one survived is still worth more than knowing nothing, and
    `1` is then honest about being a floor rather than a total.
    """
    if pgid is None:
        return 0
    try:
        r = subprocess.run(["ps", "-o", "pgid=", "-A"], capture_output=True,
                           text=True, timeout=10)
        if r.returncode == 0:
            return sum(1 for line in r.stdout.split() if line.strip() == str(pgid))
    except Exception:                                           # noqa: BLE001
        pass
    try:
        os.killpg(pgid, 0)
        return 1
    except (ProcessLookupError, PermissionError):
        return 0


def _run_contained(argv, cwd, timeout_sec, tmpdir):
    """Run a checker in its own process group and account for what it left.

    Two failures, both measured, both invisible under `subprocess.run`:

      A checker that exits 0 while a background process it spawned still holds
      the stdout pipe makes `capture_output` wait for the pipe rather than the
      process. Reproduced: a checker that forks a sleeper and exits 0 raises
      TimeoutExpired, so a real PASS is recorded as CHECKER_ERROR. Output goes
      to files instead of pipes, which removes the coupling entirely.

      A timeout kills the direct child only. Its descendants survive, keep
      writing, keep holding locks, and the next run inherits them. `start_new_session`
      puts the checker in its own process group so the whole group can be
      signalled.

    Returns (exit_code, stdout, stderr, survivor_count). Survivors are killed
    before returning -- reporting them is the point, leaving them is not.
    """
    op, ep = Path(tmpdir) / "stdout", Path(tmpdir) / "stderr"
    timed_out = False
    with op.open("wb") as fo, ep.open("wb") as fe:
        proc = subprocess.Popen(argv, cwd=cwd, stdout=fo, stderr=fe,
                                env=child_env(), start_new_session=True)
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            pgid = None
        try:
            proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            proc.wait()

    # An actual count. `survivors = 1` was a presence test, and the caller
    # renders it as "left {survivors} process(es) running" -- so a checker that
    # leaked twelve children reported one, and a number that is always 0 or 1 is
    # not a number anybody can act on. The docstring says it returns
    # `survivor_count` and that "reporting them is the point".
    survivors = _process_group_size(pgid)
    if survivors:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    # `cwd` is the repo, so the repo's own credential families apply here too.
    out = redact(op.read_text(errors="replace") if op.is_file() else "", cwd)
    err = redact(ep.read_text(errors="replace") if ep.is_file() else "", cwd)
    if timed_out:
        # `survivors`, not a hardcoded `0`. The probe above has already run, so
        # a checker that timed out *and* left descendants running reported
        # exactly the same as a clean timeout -- and the caller escalates to
        # ERROR only when `survivors` is truthy, so the "its exit code
        # describes a run that had not finished" message could never fire for
        # the one case most likely to produce it.
        return (TIMEOUT, out,
                f"checker exceeded {timeout_sec}s\n{err}".strip(), survivors)
    return proc.returncode, out, err, survivors
