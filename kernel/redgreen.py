"""Closing a review finding.  SPEC.md §8.5.

A reviewer's finding usually has no checker -- `checker-author` only writes one
once a kind has appeared three times -- so the first two occurrences need some
other way to reach a terminal state. The alternatives are an ACCEPTED_RISK for
every finding, or an advisory tier, which is the severity field V3 kept and
never used.

So a review claim is closed by a test, and the kernel decides whether the test
means anything:

  1. it must FAIL at the parent commit  -- otherwise it pins nothing
  2. it must PASS at HEAD               -- otherwise the fix is not there
  3. it must EXECUTE the claim's symbol -- otherwise see below

Step 3 exists because of a test found in the field:

    assert "with _registry_cache_lock" in inspect.getsource(make_brand_registry)

Delete the lock and that test fails, so it satisfies red-green perfectly. It
also never calls the function. Without step 3 a worker closes any review claim
with one line of `inspect.getsource`, and the mechanism is decorative.

Tracing is how we tell the difference. It is installed through `sitecustomize`
rather than by importing a test runner, so this works with pytest, unittest, or
whatever else a repo's `test_command` happens to be.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_SITECUSTOMIZE = '''
import atexit, json, os, sys, threading

TARGET_FILE = os.environ["V4_TRACE_FILE"]
TARGET_SYMBOL = os.environ.get("V4_TRACE_SYMBOL") or ""
OUT = os.environ["V4_TRACE_OUT"]
hits = {"executed": False, "calls": 0}

try:
    _target_stat = os.stat(TARGET_FILE)
except OSError:
    _target_stat = None


def _is_target(path):
    if _target_stat is not None:
        try:
            st = os.stat(path)
            return (st.st_ino, st.st_dev) == (_target_stat.st_ino, _target_stat.st_dev)
        except OSError:
            pass
    return os.path.basename(path) == os.path.basename(TARGET_FILE)


def tracer(frame, event, arg):
    if event == "call":
        code = frame.f_code
        if _is_target(code.co_filename) and (not TARGET_SYMBOL or code.co_name == TARGET_SYMBOL):
            hits["executed"] = True
            hits["calls"] += 1
    return None


def _dump():
    try:
        with open(OUT, "w") as fh:
            json.dump(hits, fh)
    except OSError:
        pass


sys.settrace(tracer)
threading.settrace(tracer)
atexit.register(_dump)
'''


_SITECUSTOMIZE_ALL = '''
import atexit, json, os, sys, threading

# One file per process, named by pid. `PYTHONPATH` reaches every Python child a
# test command starts, so every one of them installs this and dumps at exit --
# and with a single shared file the last writer won. The last writer is not the
# test run: `multiprocessing.resource_tracker` is spawned by anything that uses
# a semaphore or a shared memory block, and it outlives its parent.
#
# Measured against a real 5,455-test suite: the trace came back with one file in
# it and none of the repo's own, so `test` reported "the suite passed and
# executed none of the changed file(s)" about a file its tests import and call.
OUT = os.path.join(os.environ["V4_TRACE_OUT"], "%d.json" % os.getpid())
ROOT = os.environ.get("V4_TRACE_ROOT", "")
files = set()


def tracer(frame, event, arg):
    if event == "call":
        f = frame.f_code.co_filename
        if ROOT and f.startswith(ROOT):
            files.add(f[len(ROOT):].lstrip("/"))
    return None


def _dump():
    # Written even when empty. With one file per process an empty one costs the
    # union nothing, and the alternative loses a real answer: a suite that ran
    # none of the repo's files is a finding, and a command that is not Python at
    # all leaves no file -- which is "I cannot tell". Skipping the empty case
    # collapsed those two into one.
    try:
        with open(OUT, "w") as fh:
            json.dump(sorted(files), fh)
    except OSError:
        pass


sys.settrace(tracer)
threading.settrace(tracer)
atexit.register(_dump)
'''


def executed_files(repo_root, command, timeout=None):
    """Which files under the repo the test command actually ran.

    `timeout=None` means `config.DEFAULT_TEST_TIMEOUT`. It was a literal `1800`
    here and the same literal again at the one production call site, which is
    two homes for one number.

    `review-finding` already refuses a test that never executes the symbol it
    claims to close. Nothing asked the same of the suite: a `test` claim passes
    when `test_command` exits 0, whether or not a single changed line ran. A
    suite that is green without touching the change proves the suite works, and
    says nothing about the change.

    Traced through `sitecustomize` rather than a runner plugin, so it works with
    pytest, unittest, or whatever a repo's command happens to be.
    """
    if timeout is None:
        from .config import DEFAULT_TEST_TIMEOUT
        timeout = DEFAULT_TEST_TIMEOUT
    repo_root = str(Path(repo_root).resolve())
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "sitecustomize.py").write_text(_SITECUSTOMIZE_ALL)
        out = Path(td) / "traced"
        out.mkdir()
        env = dict(os.environ)
        env["PYTHONPATH"] = td + (os.pathsep + env["PYTHONPATH"]
                                  if env.get("PYTHONPATH") else "")
        env["V4_TRACE_OUT"] = str(out)
        env["V4_TRACE_ROOT"] = repo_root
        proc = subprocess.run(command, cwd=repo_root, env=env, shell=True,
                              capture_output=True, text=True, timeout=timeout)
        # The trace file is written by an `atexit` hook inside the test process.
        # A suite that forks workers, or one killed before `atexit` runs, leaves
        # it empty or half-written -- and `json.loads` on that raised straight
        # out of the kernel. `None` is already this function's word for "I could
        # not tell what ran", and every caller handles it; a crash is not.
        # Union, not last-writer-wins. Each process wrote its own file; a
        # half-written one is skipped rather than raised on, because `atexit` in
        # a process killed mid-dump leaves exactly that, and `None` -- which
        # every caller handles -- is only right when nothing at all was readable.
        ran, any_read = set(), False
        for f in sorted(out.glob("*.json")):
            try:
                ran |= set(json.loads(f.read_text()))
                any_read = True
            except (OSError, ValueError):
                continue
        return proc.returncode, (ran if any_read else None), proc.stdout + proc.stderr


#: Exit codes that mean the test never ran, rather than that it failed.
#: pytest uses 2 for a usage error, 3 for an internal error and 5 for "no tests
#: collected"; 126 and 127 are the shell's "not executable" and "not found",
#: and `_run_traced` produces them itself.
#:
#: It has to, and that is the repair this constant is part of. The command
#: arrives as a list -- `review_finding` `shlex.split`s it, which is right --
#: so `subprocess.run` never sees a shell, and a missing binary raises
#: `FileNotFoundError` instead of exiting 127. This set was written for a 127
#: that could not arrive, so the branch reading it could not fire, and the
#: exception went to `review_finding`'s `except Exception` and exit 5.
#:
#: Measured on the reference adopter, 2026-08-27: a worktree at the parent
#: commit has no `.venv` -- it is gitignored, which is the whole reason the
#: guard below exists -- so `attempt #5333` exited 5 and printed
#: `[Errno 2] No such file or directory: '.venv/bin/pytest'`. The sentence the
#: guard would have printed, telling the worker to point `--command` at a
#: runner that works from a bare checkout, never reached anybody. What reached
#: them instead was a broken checker, and they routed around it by pointing
#: `--command` at an absolute path outside the worktree -- which runs the
#: parent commit's code against HEAD's environment.
COULD_NOT_RUN = frozenset({2, 3, 5, 126, 127})


class RedGreenResult:
    def __init__(self):
        self.red_failed = None
        self.green_passed = None
        self.symbol_executed = None
        self.calls = 0
        self.notes = []

    @property
    def ok(self):
        return bool(self.red_failed and self.green_passed and self.symbol_executed)

    def as_dict(self):
        return {
            "red_failed_at_parent": self.red_failed,
            "green_passed_at_head": self.green_passed,
            "symbol_executed": self.symbol_executed,
            "calls": self.calls,
            "ok": self.ok,
            "notes": self.notes,
        }


#: `go test` reads its flags from `GOFLAGS`, and Node writes V8's own coverage
#: to `NODE_V8_COVERAGE`. Both are set here rather than appended to `command`,
#: because the command is the repo's -- a framework that rewrites it is a
#: framework that decides what your test run is.
#:
#: `-mod=mod` is not added and neither is anything else: one flag, the one this
#: needs. A repo that already sets `GOFLAGS` keeps what it set.
_GO_COVER = "-coverprofile="
_NODE_COVER = "NODE_V8_COVERAGE"

#: Suffixes some tracer in this module can answer for.
#:
#: The one owner of that fact. It was spelled `suffix != ".py"` in two other
#: places -- `checkers/review_finding.py` refusing a closing test and
#: `kernel/review.py::resolve_symbol` refusing a `--symbol` -- and both said, in
#: their own words, that a tracer through `sitecustomize` is all there is. That
#: was true until `_go_executed` and `_node_executed` were added below, and
#: neither caller was told. A copy of a fact is a copy that gets left behind.
#:
#: Membership is a claim about this module, not about a repo: it says a tracer
#: exists that could answer, never that it will. `_run_traced` still answers
#: `None` when nothing was observed, and the three-state result is what carries
#: that. Measured on a real Vitest suite: `--pool=threads` records the source
#: file and V8 reports `tier_of` entered 37 times, while the default pool
#: records 306 scripts, none of them the repo's own. So a suffix listed here
#: whose run observed nothing is `None` -- not proven, and not an accusation.
TRACEABLE: frozenset[str] = frozenset({
    ".py",                                  # sitecustomize + sys.settrace
    ".go",                                  # go test -coverprofile
    ".js", ".mjs", ".cjs",                  # V8, through NODE_V8_COVERAGE
    ".ts", ".tsx", ".jsx", ".mts", ".cts",  # the same -- esbuild keeps both the
                                            # source id and the function name
})


def traceable(path) -> bool:
    """Is there a tracer here that could answer about this file?"""
    return Path(path).suffix.lower() in TRACEABLE


def _go_executed(cwd: Path, profile: Path, target_file: str, target_symbol: str):
    """`(executed, calls)` from a Go coverage profile, or `(None, 0)`.

    `go tool cover -func` reports one line per function with a percentage, and
    a function the tests never entered reports `0.0%`. That is the same fact
    `sys.settrace` gives for Python -- did control ever reach this symbol --
    arrived at through the toolchain rather than an interpreter hook.

    `cwd` is the module, not the profile's directory. The profile names its
    packages by import path (`ex/target.go`), so resolving them needs the
    `go.mod` -- run from the temporary directory the profile happens to sit in,
    `go tool cover` finds no module and prints nothing, and this returned
    `None` for a run that had in fact been measured. Found by the profile
    existing, `go test` reporting `coverage: 100.0%`, and this still answering
    "nothing observed".

    Percentages, not call counts: the profile counts statement coverage and
    does not say how many times. `calls` is 1 or 0 rather than a real count,
    and `verify` only asks whether it is non-zero.
    """
    if not profile.is_file():
        return None, 0
    r = subprocess.run(["go", "tool", "cover", f"-func={profile}"],
                       cwd=str(cwd), capture_output=True, text=True)
    if r.returncode != 0:
        return None, 0
    stem = Path(target_file).name
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[-1].endswith("%"):
            continue
        where, name, pct = parts[0], parts[-2], parts[-1]
        if not where.split(":")[0].endswith(stem):
            continue
        if target_symbol and name != target_symbol:
            continue
        if pct != "0.0%":
            return True, 1
        # Named and at zero: seen, and not entered. That is a real `False`,
        # which is the answer this whole mechanism exists to be able to give.
        return False, 0
    return None, 0


def _node_executed(covdir: Path, target_file: str, target_symbol: str):
    """`(executed, calls)` from V8's own coverage output, or `(None, 0)`.

    V8 reports per-function ranges with a hit count, so unlike the Go profile
    this one carries a real number. A function present with `count: 0` is the
    same `False` the Python tracer gives; a file V8 never loaded is `None`.
    """
    if not covdir.is_dir():
        return None, 0
    stem = Path(target_file).name
    for path in sorted(covdir.glob("*.json")):
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for script in blob.get("result") or []:
            if not str(script.get("url", "")).endswith(stem):
                continue
            for fn in script.get("functions") or []:
                if target_symbol and fn.get("functionName") != target_symbol:
                    continue
                ranges = fn.get("ranges") or []
                count = ranges[0].get("count", 0) if ranges else 0
                if count:
                    return True, count
                if target_symbol:
                    return False, 0
    return None, 0


def _run_traced(cwd: Path, command, target_file: str, target_symbol: str, timeout=600):
    """Run a test command with the tracer attached.  (exit_code, executed, calls, output).

    Three answers, and the third is the reason this is not a boolean: `True`
    entered, `False` seen and not entered, `None` nothing observed. `None` is
    what a command this cannot instrument leaves behind, and calling it `False`
    would accuse a worker of the one bypass this exists to refuse.

    Python answers through `sitecustomize`, Go through its own coverage
    profile, Node through V8's. All three are set up before the command runs
    and read after, so a repo whose suite is a mix answers from whichever left
    something behind -- and the Python path is tried first because it is the
    only one that reports a real call count.
    """
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "sitecustomize.py").write_text(_SITECUSTOMIZE)
        out = Path(td) / "hits.json"
        go_profile = Path(td) / "go-cover.out"
        node_dir = Path(td) / "node-cover"
        node_dir.mkdir()

        env = dict(os.environ)
        env["PYTHONPATH"] = td + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["V4_TRACE_FILE"] = str(Path(cwd) / target_file)
        env["V4_TRACE_SYMBOL"] = target_symbol or ""
        env["V4_TRACE_OUT"] = str(out)
        # Appended, never replaced: a repo that sets `GOFLAGS` for its own
        # reasons keeps them, and a repo that already asks for a coverage
        # profile keeps pointing at its own file -- this one then finds nothing
        # and answers `None`, which is honest, rather than fighting for the flag.
        if _GO_COVER not in env.get("GOFLAGS", ""):
            env["GOFLAGS"] = (env.get("GOFLAGS", "") + " "
                              + _GO_COVER + str(go_profile)).strip()
        env.setdefault(_NODE_COVER, str(node_dir))

        try:
            proc = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                                  text=True, timeout=timeout)
        except (FileNotFoundError, PermissionError) as exc:
            # The shell's answer to the same two questions, given here because
            # there is no shell: the command is a list, so `subprocess` raises
            # where `sh` would have exited. `COULD_NOT_RUN` was written around
            # those two codes and could never see them, so the branch that
            # reads it never fired and the exception left through
            # `review_finding`'s `except Exception` as exit 5 -- a checker
            # reporting itself broken because a repo keeps its virtualenv out
            # of git, which every repo does.
            return (126 if isinstance(exc, PermissionError) else 127), None, 0, str(exc)
        # A missing trace file is not proof of non-execution. The tracer is
        # installed through `sitecustomize` on PYTHONPATH and dumps at
        # `atexit`, so a command that is not Python, a runner killed before
        # exit, or a suite that execs into a subprocess all leave no file --
        # and `executed: False` then makes `verify` accuse the worker of the
        # one bypass this mechanism exists to refuse, when the truth is that
        # the tracer never attached. `executed_files` in this module fixed the
        # same distinction ("None is only right when nothing at all was
        # readable"); the per-symbol path did not get it.
        if out.is_file():
            hits = json.loads(out.read_text())
            return proc.returncode, hits.get("executed", False), \
                hits.get("calls", 0), proc.stdout + proc.stderr

        # No Python trace file. Before answering `None`, ask the two toolchains
        # that leave their own evidence -- `go test` writes the profile the
        # `GOFLAGS` above asked for, and Node writes V8's coverage into the
        # directory above. Neither needs the command rewritten and neither is a
        # third-party dependency, which is why they are here and a coverage
        # library is not.
        for executed, calls in (_go_executed(cwd, go_profile, target_file, target_symbol),
                                _node_executed(node_dir, target_file, target_symbol)):
            if executed is not None:
                return proc.returncode, executed, calls, proc.stdout + proc.stderr

        return proc.returncode, None, 0, proc.stdout + proc.stderr


def verify(repo_root, *, command, test_path, target_file, target_symbol, parent_commit,
           timeout=600):
    """Check whether a test has earned the right to close a review claim.

    `command` runs exactly one test file. The parent commit goes into a throwaway
    worktree; the live tree is never touched, because a worker is standing in it.
    """
    repo_root = Path(repo_root).resolve()
    res = RedGreenResult()

    rc, executed, calls, out_h = _run_traced(repo_root, command, target_file,
                                             target_symbol, timeout)
    res.green_passed = rc == 0
    res.symbol_executed = executed
    res.calls = calls
    if rc in COULD_NOT_RUN:
        # Not the same sentence as a failing test, because it is not the same
        # fact. Here the live tree is the one the worker is standing in, so
        # "no `.venv/bin/pytest`" means the command names a runner this repo
        # does not have -- their invocation, not their code. Saying "does not
        # pass at HEAD" about a test that never started sends them to read the
        # test.
        res.notes.append(
            f"could not run at HEAD (exit {rc}). The command names a runner "
            f"this tree does not have, so nothing was tested -- this says "
            f"nothing about the code either way.\n    {out_h.strip()[:300]}")
    elif not res.green_passed:
        res.notes.append(f"does not pass at HEAD (exit {rc})")
    # Executing the symbol is necessary and not sufficient. A bypass fixture
    # called it once inside a try/except and then asserted on its source text --
    # tracer satisfied, red-green satisfied, and the assertion still tests
    # nothing. The source-text shape is already recognised elsewhere; this is
    # the same question asked of a test offered as closure.
    try:
        import ast as _ast
        from kernel.analysis import test_shape as _ts
        _src = (Path(repo_root) / test_path).read_text(encoding="utf-8", errors="replace")
        if _ts.source_assertions(_ast.parse(_src), _src):
            res.symbol_executed = False
            res.notes.append(
                "asserts on the source text of the code it is closing. Calling "
                "the symbol once and then reading its source satisfies both "
                "halves of red-green and still tests nothing -- the assertion "
                "has to be about what the code did.")
    except Exception as exc:                                    # noqa: BLE001
        # This is the check an author trying to walk around red-green has an
        # interest in making raise, and a swallow left `symbol_executed` at its
        # tracer value with nothing appended -- so `as_dict()` reported
        # `symbol_executed: true` and the verdict was indistinguishable from a
        # test that was examined and cleared. Not fatal: an unparseable test
        # file is not proof of a bypass. Unsaid is the part that was wrong.
        res.notes.append(
            f"the source-assertion check did not run ({type(exc).__name__}: "
            f"{exc}), so this verdict does not include it -- calling the symbol "
            f"once and then reading its source would satisfy both halves of "
            f"red-green and is not ruled out here.")

    # `None` is not `False`, and this is the reader `_run_traced` made the
    # distinction for. It returns `None` when no trace file appeared -- a
    # command that is not Python, a runner killed before `atexit`, a suite that
    # execs into a subprocess -- and its comment says reading that as `False`
    # "makes `verify` accuse the worker of the one bypass this mechanism exists
    # to refuse, when the truth is that the tracer never attached". Then `if
    # not executed` read `None` as `False` and appended exactly that
    # accusation, so the repair had no reader and cost a sentence somebody had
    # to argue their way out of.
    #
    # Both are still not `ok`: `symbol_executed` stays `None`, `as_dict`
    # reports it as `null`, and `RedGreenResult.ok` is false for either. Unknown
    # is not proof. It is just not an allegation.
    if executed is None:
        res.notes.append(
            f"nothing observed whether {target_symbol or target_file} ran: the "
            f"tracer attaches through a `sitecustomize` on PYTHONPATH and left "
            f"no trace file, which is what a non-Python command, a runner "
            f"killed before exit, or a suite that execs into a subprocess all "
            f"look like. This is unknown, not a bypass -- point --command at a "
            f"Python runner in this interpreter to make it answerable")
    elif not executed:
        res.notes.append(
            f"never executed {target_symbol or target_file}. A test that reads source "
            f"text instead of calling the code satisfies red-green while proving "
            f"nothing -- that is the shape this check exists to refuse"
        )

    with tempfile.TemporaryDirectory() as td:
        wt = Path(td) / "parent"
        add = subprocess.run(["git", "worktree", "add", "--detach", str(wt), parent_commit],
                             cwd=repo_root, capture_output=True, text=True)
        if add.returncode != 0:
            res.notes.append(f"could not check out {parent_commit}: {add.stderr.strip()}")
            return res
        try:
            # The test is new, so it does not exist at the parent; carry it over.
            dst = wt / test_path
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text((repo_root / test_path).read_text())

            rc_p, _, _, out_p = _run_traced(wt, command, target_file, target_symbol,
                                            timeout)
            if rc_p in COULD_NOT_RUN:
                # A worktree carries no .venv, no node_modules, nothing
                # untracked -- so "command not found" and "no tests collected"
                # arrive here routinely and mean nothing about the code. Reading
                # them as red hands out a free half of the proof.
                res.red_failed = False
                res.notes.append(
                    f"could not run at {parent_commit[:12]} (exit {rc_p}). A "
                    f"worktree has no untracked files, so the test environment is "
                    f"not the one you ran in. This is inconclusive, not red -- "
                    f"install what the test needs from tracked files, or point "
                    f"--command at a runner that works from a bare checkout.\n"
                    f"    {out_p.strip()[:300]}")
            else:
                res.red_failed = rc_p != 0
                if not res.red_failed:
                    res.notes.append(
                        f"already passes at {parent_commit[:12]}, so it pins "
                        f"nothing -- it would have passed before the fix existed")
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(wt)],
                           cwd=repo_root, capture_output=True)
    return res
