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

Tracing is how we tell the difference. Python uses `sitecustomize`, Go and Node
use their runtime coverage, and the optional Playwright fixture records Chromium
coverage of exact served source bytes. Each reports only what it observed.
"""

import ast
import json
import os
import subprocess
import sys
import tempfile
import uuid
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
_relative_cache = {}
_prefix = ROOT.rstrip(os.sep) + os.sep if ROOT else ""


def _relative(filename):
    if filename not in _relative_cache:
        relative = None
        if _prefix and os.path.isabs(filename):
            path = os.path.normpath(filename)
            # Resolve once per code filename, not per call. An external alias
            # may enter this checkout; an internal symlink may leave it.
            real = os.path.realpath(path)
            if real.startswith(_prefix):
                relative = (path if path.startswith(_prefix) else real)[len(_prefix):]
        _relative_cache[filename] = relative
    return _relative_cache[filename]


def tracer(frame, event, arg):
    if event == "call":
        relative = _relative(frame.f_code.co_filename)
        if relative:
            files.add(relative)
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

    `timeout=None` means no wall of this function's own -- the caller's kernel
    owns it. It was a literal `1800` here and the same literal again at the one
    production call site; removing the call site's copy only moved the second
    wall into this default, where `.v4/checkers.json` registering the `test`
    checker at anything above 1800 would have let this one win invisibly again.
    The one caller inside `checkers/` runs under `runner`, which enforces the
    registry's `timeout_sec`, so nothing here is unbounded. Callers outside a
    kernel-enforced wall -- the tests below -- pass their own.

    `review-finding` already refuses a test that never executes the symbol it
    claims to close. Nothing asked the same of the suite: a `test` claim passes
    when `test_command` exits 0, whether or not a single changed line ran. A
    suite that is green without touching the change proves the suite works, and
    says nothing about the change.

    Traced through `sitecustomize` rather than a runner plugin, so it works with
    pytest, unittest, or whatever a repo's command happens to be.
    """
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
        self.browser_trace = None
        self.source_assertion_check = None

    @property
    def ok(self):
        return bool(self.red_failed and self.green_passed and self.symbol_executed and
                    (self.source_assertion_check is None or self.source_assertion_check["status"] == "passed"))

    def as_dict(self):
        result = {
            "red_failed_at_parent": self.red_failed,
            "green_passed_at_head": self.green_passed,
            "symbol_executed": self.symbol_executed,
            "calls": self.calls,
            "ok": self.ok,
            "notes": self.notes,
        }
        if self.source_assertion_check is not None:
            result["source_assertion_check"] = self.source_assertion_check
        if self.browser_trace:
            result["browser_trace"] = self.browser_trace
        return result


#: `go test` reads its flags from `GOFLAGS`, and Node writes V8's own coverage
#: to `NODE_V8_COVERAGE`. Both are set here rather than appended to `command`,
#: because the command is the repo's -- a framework that rewrites it is a
#: framework that decides what your test run is.
#:
#: `-mod=mod` is not added and neither is anything else: one flag, the one this
#: needs. A repo that already sets `GOFLAGS` keeps what it set.
_GO_COVER = "-coverprofile="
_NODE_COVER = "NODE_V8_COVERAGE"

# Node's coverage contains offsets, but loaders may transform the file behind
# its URL. For declaration proof, observe the actual source bytes with the
# in-process inspector; do not infer positions from a filename. No source text
# leaves the process. Each PID/thread records its actual instruction hits.
_NODE_SOURCE_OBSERVER = r'''
const fs = require('node:fs'), crypto = require('node:crypto');
const {fileURLToPath} = require('node:url');
const {threadId} = require('node:worker_threads');
const observations = [];
const points = new Map();
let error = null, session;
try {
  const target = fs.realpathSync(process.env.V4_TRACE_FILE);
  const coordinate = JSON.parse(process.env.V4_DECLARATION_COORDINATE);
  session = new (require('node:inspector').Session)();
  session.connect();
  session.on('Debugger.paused', ({params}) => {
    for (const id of params.hitBreakpoints || []) {
      const point = points.get(id);
      if (!point) continue;
      const {row,after} = point;
      if (!after) { row.entries++; continue; }
      const frame=params.callFrames[0];
      const contains = location => location && location.scriptId === row.script_id;
      const before = (a,b) => a.lineNumber < b.lineNumber ||
        (a.lineNumber === b.lineNumber && a.columnNumber <= b.columnNumber);
      const original={lineNumber:coordinate.statement_line,columnNumber:coordinate.statement_column};
      // Read the scope that actually owns this declaration, excluding a later
      // block or nested function that happens to shadow the same spelling.
      const scope=frame.scopeChain.find(scope => contains(scope.startLocation) && contains(scope.endLocation) &&
        before(scope.startLocation,original) && before(original,scope.endLocation));
      if (!scope || !row.entries) continue;
      session.post('Runtime.getProperties',{objectId:scope.object.objectId,ownProperties:true},(err,result)=>{
        if (err) { error=String(err.message); return; }
        const value=result.result.find(item=>item.name === coordinate.symbol)?.value;
        if (value && ['object','undefined','string','number','boolean','symbol','bigint'].includes(value.type)) {
          row.hits++; row.value_types.push(value.type);
        }
      });
    }
    session.post('Debugger.resume');
  });
  session.on('Debugger.scriptParsed', ({params}) => {
    try {
      if (!params.url.startsWith('file:') || fs.realpathSync(fileURLToPath(params.url)) !== target) return;
      session.post('Debugger.getScriptSource', {scriptId:params.scriptId}, (err, result) => {
        if (err) { error = String(err.message); return; }
        const row = {script_id:params.scriptId,url:params.url,hits:0,entries:0,value_types:[],breakpoint_set:false,after_breakpoint_set:false,
          sha256:crypto.createHash('sha256').update(result.scriptSource,'utf8').digest('hex')};
        observations.push(row);
        if (row.sha256 !== coordinate.source_sha256) return;
        const start={scriptId:params.scriptId,lineNumber:coordinate.statement_line,columnNumber:coordinate.statement_column};
        const end={scriptId:params.scriptId,lineNumber:coordinate.end_line,columnNumber:coordinate.end_column};
        session.post('Debugger.getPossibleBreakpoints', {start,end}, (err, result) => {
          if (err) { error = String(err.message); return; }
          const location = result.locations[0];
          if (!location) return;
          session.post('Debugger.setBreakpoint', {location}, (err, result) => {
            if (err) { error = String(err.message); return; }
            row.breakpoint_set=true; row.location=result.actualLocation;
            points.set(result.breakpointId,{row,after:false});
          });
        });
        session.post('Debugger.getPossibleBreakpoints',{start:end,restrictToFunction:true},(err,result)=>{
          if (err) { error=String(err.message); return; }
          if (!result.locations.length) return;
          session.post('Debugger.setBreakpoint',{location:result.locations[0]},(err,result)=>{
            if (err) { error=String(err.message); return; }
            row.after_breakpoint_set=true;
            points.set(result.breakpointId,{row,after:true});
          });
        });
      });
    } catch (err) { error = String(err.message); }
  });
  session.post('Debugger.enable');
} catch (err) { error = String(err.message); }
process.once('exit', () => {
  try {
    fs.writeFileSync(process.env.V4_NODE_SOURCE_DIR+'/'+process.pid+'-'+threadId+'.json',
      JSON.stringify({observations,error}));
  } catch (_) { /* Missing evidence remains unproved. */ }
  if (session) session.disconnect();
});
'''


def _node_declaration_executed(directory, coordinate):
    """Actual inspector instruction hits tied to exact source bytes."""
    seen, calls = False, 0
    for path in directory.glob('*.json'):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get('error') or not isinstance(data.get('observations'), list):
            continue
        for row in data.get('observations', []):
            if (not isinstance(row, dict) or row.get('sha256') != coordinate['source_sha256'] or
                    row.get('breakpoint_set') is not True or
                    row.get('after_breakpoint_set') is not True or
                    type(row.get('hits')) is not int or row['hits'] < 0):
                continue
            location = row.get('location') or {}
            if (not isinstance(location, dict) or type(location.get('lineNumber')) is not int or
                    type(location.get('columnNumber')) is not int):
                continue
            point = (location.get('lineNumber', -1), location.get('columnNumber', -1))
            if not ((coordinate['statement_line'],coordinate['statement_column']) <= point <
                    (coordinate['end_line'],coordinate['end_column'])):
                continue
            seen = True
            calls += row['hits']
    return (calls > 0, calls) if seen else (None, 0)


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


def _same_file(where: str, want: str) -> bool:
    """Is this coverage row about the file the claim names?

    `endswith` alone, which both tracers used. That was repaired once already,
    from the bare basename -- `internal/cache/store.go` answered a claim at
    `internal/db/store.go` -- to the repo-relative path, and the residue is the
    same shape one directory out: `vendor/internal/db/store.go` ends with
    `internal/db/store.go` and would answer for it.

    A path boundary is what makes the difference: either the two are equal, or
    the coverage path has a `/` immediately before the part that matched. Go
    reports package-relative paths and V8 reports `file://` URLs, so neither
    can be compared for equality alone -- but a prefix that stops mid-segment
    is never the same file.

    This decides whether a review finding may close, which is the one condition
    that stops a closure passing on a test that never ran the code.
    """
    where = str(where).replace("\\", "/")
    want = str(want).replace("\\", "/")
    return where == want or where.endswith("/" + want)


def _identity(target_file: str) -> str:
    """What a coverage line has to end with to be about this file.

    The repo-relative path, not its basename. Both readers below asked
    `endswith(Path(target_file).name)`, so a covered `internal/cache/store.go`
    answered for a claim filed at `internal/db/store.go` -- the tracer reports
    True, `verify` reports the symbol executed, and the finding closes on a
    file nobody ran. One basename collision anywhere in the tree is enough.

    It stayed cheap while Go was the only non-Python tracer. Admitting `.ts`
    and `.tsx` is what makes it likely: a TypeScript tree carries `index.ts`,
    `types.ts` and `utils.ts` in every directory, so the collision is the
    normal case rather than the unlucky one.

    Still a suffix, because neither producer emits a repo-relative path: Go
    prefixes the module path (`example.com/m/internal/db/store.go`) and V8
    emits `file:///abs/...`. Exactly one leading `./` comes off, so the two
    spellings of the same claim agree -- and not with `lstrip("./")`, which
    eats the dot of `.v4/x` as well.
    """
    path = str(target_file).replace("\\", "/")
    return path[2:] if path.startswith("./") else path


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
    want = _identity(target_file)
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[-1].endswith("%"):
            continue
        where, name, pct = parts[0], parts[-2], parts[-1]
        if not _same_file(where.split(":")[0], want):
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
    want = _identity(target_file)
    seen_uncalled = False
    for path in sorted(covdir.glob("*.json")):
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for script in blob.get("result") or []:
            if not _same_file(str(script.get("url", "")), want):
                continue
            for fn in script.get("functions") or []:
                if target_symbol and fn.get("functionName") != target_symbol:
                    continue
                ranges = fn.get("ranges") or []
                count = ranges[0].get("count", 0) if ranges else 0
                if count:
                    return True, count
                if target_symbol:
                    # An import-only parent can record zero while its CLI child
                    # enters the same function. A zero is conclusive only after
                    # every matching process profile has been read.
                    seen_uncalled = True
    return (False, 0) if seen_uncalled else (None, 0)


def _run_traced(cwd: Path, command, target_file: str, target_symbol: str, timeout=600,
                trace_details=None, declaration=False):
    """Run a test command with the tracer attached.  (exit_code, executed, calls, output).

    Three answers, and the third is the reason this is not a boolean: `True`
    entered, `False` seen and not entered, `None` nothing observed. `None` is
    what a command this cannot instrument leaves behind, and calling it `False`
    would accuse a worker of the one bypass this exists to refuse.

    Python answers through `sitecustomize`, Go through its own coverage
    profile, Node through V8's, and integrated Chromium tests through browser
    V8 coverage. Select evidence for the target's runtime: a Python server's
    negative trace cannot answer whether a browser executed its JS asset.
    """
    with tempfile.TemporaryDirectory() as td:
        from . import browser_trace
        from . import review_coordinates
        coordinate = review_coordinates.resolve_declaration(cwd, target_file, target_symbol) if declaration else None
        suffix = Path(target_file).suffix.lower()
        (Path(td) / "sitecustomize.py").write_text(_SITECUSTOMIZE)
        out = Path(td) / "hits.json"
        go_profile = Path(td) / "go-cover.out"
        node_dir = Path(td) / "node-cover"
        node_dir.mkdir()
        browser_dir = Path(td) / "browser-cover"
        browser_dir.mkdir()
        node_sources = Path(td) / "node-sources"
        node_sources.mkdir()
        browser_run = uuid.uuid4().hex
        expected_browser_sha = browser_trace.target_sha(cwd, target_file)

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
        if declaration:
            observer = Path(td) / "observe-source.cjs"
            observer.write_text(_NODE_SOURCE_OBSERVER)
            env["NODE_OPTIONS"] = (env.get("NODE_OPTIONS", "") + " --require=" + json.dumps(str(observer))).strip()
            env["V4_NODE_SOURCE_DIR"] = str(node_sources)
            env["V4_DECLARATION_COORDINATE"] = json.dumps(coordinate)
        if suffix in TRACEABLE - {".py", ".go"}:
            env.update(V4_BROWSER_TRACE_DIR=str(browser_dir), V4_BROWSER_TRACE_RUN=browser_run,
                       V4_BROWSER_TRACE_ROOT=str(Path(cwd).resolve()))
        else:
            for key in ("V4_BROWSER_TRACE_DIR", "V4_BROWSER_TRACE_RUN", "V4_BROWSER_TRACE_ROOT"):
                env.pop(key, None)

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
        # A Python web-server child must not override evidence about a JS target
        # with its own negative Python trace. Select the target's runtime.
        if out.is_file() and suffix in {".py", ".pyi"}:
            hits = json.loads(out.read_text())
            return proc.returncode, hits.get("executed", False), \
                hits.get("calls", 0), proc.stdout + proc.stderr

        # Runtime evidence is read without rewriting the repo's test command.
        if suffix == ".go":
            executed, calls = _go_executed(cwd, go_profile, target_file, target_symbol)
            return proc.returncode, executed, calls, proc.stdout + proc.stderr
        browser_executed, browser_calls, browser_detail = browser_trace.observed(
            browser_dir, cwd, browser_run, target_file, target_symbol, expected_browser_sha, coordinate=coordinate)
        candidates = [(browser_executed, browser_calls, browser_detail),
                      (*(_node_declaration_executed(node_sources, coordinate) if declaration else
                         _node_executed(node_dir, target_file, target_symbol)), {})]
        # Positive evidence wins over an unrelated runtime's zero. Otherwise a
        # known zero remains distinct from an observer that never attached.
        candidates.sort(key=lambda value: value[0] is not True)
        for executed, calls, detail in candidates:
            if executed is not None:
                if trace_details is not None:
                    trace_details.update(detail)
                return proc.returncode, executed, calls, proc.stdout + proc.stderr
        if trace_details is not None:
            trace_details.update(browser_detail)
        return proc.returncode, None, 0, proc.stdout + proc.stderr


def _apply_mutation(worktree: Path, mutation) -> str:
    """Break one thing in a throwaway tree.  `""` when it worked, else why not.

    Exactly one occurrence, and the refusal says so: a marker that matches
    twice breaks two things, and a test going red then says nothing about
    which. A marker that matches none is a worker quoting text that is not
    there, which would otherwise produce a green worktree and read as "your
    test does not cover this".
    """
    rel, gone, now = mutation
    path = worktree / rel
    try:
        src = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"the mutation names {rel}, which could not be read there: {exc}"
    n = src.count(gone)
    if n != 1:
        return (f"the mutation text appears {n} time(s) in {rel} and has to "
                f"appear exactly once -- a marker matching twice breaks two "
                f"things, and a test going red then says nothing about which")
    path.write_text(src.replace(gone, now, 1), encoding="utf-8")
    return ""


def what_runs(tree, command):
    """The part of a test module this command selects, or all of it.

    The refusal below is asked of a whole file, and one source assertion
    anywhere in a large one disqualifies every test in it. Measured four times
    in one day: `tests/test_kernel.py` is 8,710 lines and 114 classes, and a
    closure offered from a class that reads no source at all was refused for
    assertions in unrelated cases; the same happened to
    `tests/test_what_guards_the_guards.py`; and two more findings have their
    subject inside `test_kernel.py`, so they could not have been closed at all.

    The rule is right and its reasoning holds -- calling a symbol once and then
    reading its source satisfies red-green and proves nothing. What was too
    coarse is the unit it was asked of, and a closure runs what its `--command`
    names, not the module.

    String containment against the names this file defines, which is all this
    needs and all it can safely know: a command is a runner's syntax
    (`unittest`'s dots, `pytest`'s `::`, something else tomorrow) and parsing it
    would be this module learning three grammars. A command naming none of
    them -- a whole-module run -- gets the whole module, which is today's
    behaviour and the honest answer for a run that really does execute
    everything.

    Narrows only. A file that passes this today passes it after.
    """
    words = " ".join(command) if isinstance(command, (list, tuple)) else str(command)
    named = [n for n in tree.body
             if isinstance(n, (ast.ClassDef, ast.FunctionDef,
                               ast.AsyncFunctionDef)) and n.name in words]
    if not named:
        return tree
    picked = ast.Module(body=named, type_ignores=[])
    return picked


def verify(repo_root, *, command, test_path, target_file, target_symbol,
           parent_commit=None, mutation=None, timeout=600, declaration=False):
    """Check whether a test has earned the right to close a review claim.

    `command` runs exactly one test file. The red half goes into a throwaway
    worktree; the live tree is never touched, because a worker is standing in it.

    Two ways to be red, and the second exists because the first cannot reach a
    whole class of finding. `parent_commit` puts the test against the tree
    before the repair -- which is the proof when there *was* a repair. A finding
    of the form "nothing in the suite enters this function" has no repair: the
    code was always right, nobody was looking at it, and the test therefore
    passes at the parent and is refused for pinning nothing. Measured on five
    such findings in one cut, all five refused, with the text closure refusing
    them too (it reads the finding's own file, and the repair is a new file
    under `tests/`). That left a signature as the only exit, which is the
    outcome `review.resolve_symbol`'s own docstring exists to prevent.

    So `mutation` is `(file, gone, now)`: break the code the test covers, and
    the test has to notice. That is red-green one level up, and it is what the
    author of such a test does by hand anyway to know the test works.

    Exactly one of the two, because they answer the same question and a caller
    that passed both would be asking which answer counts.
    """
    if bool(parent_commit) == bool(mutation):
        raise ValueError("verify needs `parent_commit` or `mutation`, not both "
                         "and not neither -- they are two ways to make the same "
                         "half red")
    repo_root = Path(repo_root).resolve()
    res = RedGreenResult()

    trace_details = {}
    rc, executed, calls, out_h = _run_traced(repo_root, command, target_file,
                                             target_symbol, timeout, trace_details, declaration)
    res.browser_trace = trace_details or None
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
        from kernel import baseline as _bl
        from kernel.analysis import test_shape as _ts
        _root = Path(repo_root)
        _path = _root / test_path
        _src = _path.read_text(encoding="utf-8", errors="replace")
        # Two readers, one rule, and until now only one of them read the repo's
        # own answer. `checkers/test_shape.py` subtracts the ids in
        # `.v4/test-shape_baseline.json` -- findings an adopter accepted with a
        # written reason -- and this did not. So an accepted assertion left
        # `v4 check` green and still refused every closure offered out of its
        # file. Measured: `d52045d6` was repaired inside a class that has to
        # `ast.parse` `kernel/cli.py` to do its job, entry `2b3f027807b54dd7`
        # accepted exactly that, `test-shape` printed "carrying 8" -- and the
        # closure was refused anyway and had to be signed `unprovable`.
        #
        # Through `findings` and `finding_id` because they are the one spelling
        # both readers already import, and `forgive` because it is `load` plus
        # `partition` with the note the carried debt owes. A second expression
        # here would be a second id, and every id in every adopter's baseline
        # file is the first one. `complete=False` for the reason `partition`
        # documents: this reads one file, so accepted entries belonging to the
        # rest of the tree match nothing *in this scan* and are not stale.
        # `is_test=True` and not `subject_files`' answer: this file is the
        # `--test` of a closure, so it is a test by construction whatever it is
        # named, and outside a declared test root that function answers on the
        # name. Leaving the decision to it would have let a closing test called
        # `t_mod.py` past the rule entirely -- a refusal that used to be
        # unconditional here, so this would have been a hole opened by the fix.
        suffix = _path.suffix.lower()
        from kernel.analysis.subject_files import TS_SUFFIXES
        if suffix in TS_SUFFIXES or suffix in {".mts", ".cts"}:
            _found = _ts.ts_findings(test_path, _src, True, "source_assertion")
            reader = "typescript-text-flow"
        elif suffix == ".go":
            from kernel.analysis import gosource
            shape = gosource.shape_source(_src)
            if shape is None:
                raise ValueError("Go source-shape parser unavailable or could not parse the closing test")
            _found = _ts.go_findings(test_path, shape, True, "source_assertion")
            reader = "go-ast"
        elif suffix == ".py":
            _found = _ts.findings(test_path, _src, what_runs(_ast.parse(_src), command),
                                  "source_assertion", is_test=True)
            reader = "python-ast"
        else:
            raise ValueError("no source-assertion reader for the closing test language")
        res.source_assertion_check = {"status":"passed", "reader":reader}

        try:
            _found, _, _carried_notes = _bl.forgive(
                repo_root, _ts.KIND, _found, _ts.finding_id, complete=False)
        except _bl.Unreadable as exc:
            # Not the outer `except`, which reports that this check did not run
            # and leaves the closure standing. `.v4/**` is protected, so writing
            # this file costs a widen and a signature -- but "expensive to
            # write" is not "cannot be written", and reaching a file nobody can
            # read must not be cheaper than answering the assertion. The checker
            # answers 4 here for the same reason.
            res.symbol_executed = False
            res.source_assertion_check["status"] = "failed"
            res.notes.append(
                f"{exc} -- so what it forgives could not be read, and this "
                f"closure is refused rather than given the benefit of an "
                f"exemption list nobody can read.")
        else:
            # Said out loud: a verdict reached while forgiving something is not
            # the same verdict as one reached with nothing to forgive, and the
            # reader of this closure is entitled to know which one they have.
            res.notes.extend(_carried_notes)
            if _found:
                res.source_assertion_check["status"] = "failed"
                res.symbol_executed = False
                res.notes.append(
                    "asserts on the source text of the code it is closing. "
                    "Calling the symbol once and then reading its source "
                    "satisfies both halves of red-green and still tests "
                    "nothing -- the assertion has to be about what the code "
                    "did.")
    except Exception as exc:                                    # noqa: BLE001
        # This is the check an author trying to walk around red-green has an
        # interest in making raise, and a swallow left `symbol_executed` at its
        # tracer value with nothing appended -- so `as_dict()` reported
        # `symbol_executed: true` and the verdict was indistinguishable from a
        # test that was examined and cleared. Not fatal: an unparseable test
        # file is not proof of a bypass. Unsaid is the part that was wrong.
        res.source_assertion_check = {"status":"unavailable", "reason":str(exc)}
        res.notes.append(
            f"the source-assertion check did not run ({type(exc).__name__}: "
            f"{exc}); this closure remains unproved. Calling the symbol "
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
            f"target's tracer produced no usable execution evidence. This is "
            f"unknown, not a bypass. Use a supported runtime and a command that "
            f"actually exercises this target; browser JS needs the integrated "
            f"Chromium fixture and exact served source bytes")
    elif not executed:
        res.notes.append(
            f"never executed {target_symbol or target_file}. A test that reads source "
            f"text instead of calling the code satisfies red-green while proving "
            f"nothing -- that is the shape this check exists to refuse"
        )

    with tempfile.TemporaryDirectory() as td:
        wt = Path(td) / "parent"
        # HEAD for a mutation: the point is to compare against *this* tree with
        # one thing broken, so any other commit would be measuring two changes.
        ref = parent_commit or "HEAD"
        add = subprocess.run(["git", "worktree", "add", "--detach", str(wt), ref],
                             cwd=repo_root, capture_output=True, text=True)
        if add.returncode != 0:
            res.notes.append(f"could not check out {ref}: {add.stderr.strip()}")
            return res
        try:
            # The test is new, so it does not exist at the parent; carry it over.
            dst = wt / test_path
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text((repo_root / test_path).read_text())

            if mutation:
                broke = _apply_mutation(wt, mutation)
                if broke:
                    res.notes.append(broke)
                    return res

            red_trace = {}
            try:
                rc_p, red_executed, _, out_p = _run_traced(wt, command, target_file, target_symbol,
                                                timeout, red_trace, declaration)
            except ValueError as exc:
                res.red_failed = False
                res.notes.append(f"red control has no usable declaration/trace coordinate: {exc}")
                return res
            if res.browser_trace:
                res.browser_trace["red"] = red_trace
            if rc_p in COULD_NOT_RUN:
                # A worktree carries no .venv, no node_modules, nothing
                # untracked -- so "command not found" and "no tests collected"
                # arrive here routinely and mean nothing about the code. Reading
                # them as red hands out a free half of the proof.
                res.red_failed = False
                res.notes.append(
                    f"could not run at {ref[:12]} (exit {rc_p}). A "
                    f"worktree has no untracked files, so the test environment is "
                    f"not the one you ran in. This is inconclusive, not red -- "
                    f"install what the test needs from tracked files, or point "
                    f"--command at a runner that works from a bare checkout.\n"
                    f"    {out_p.strip()[:300]}")
            else:
                res.red_failed = rc_p != 0
                if declaration and red_executed is not True:
                    res.red_failed = False
                    res.notes.append("declaration red control did not observe the initialized non-function value; startup/type failure is not behavioral red")
                if res.browser_trace and (not red_trace.get("valid") or not red_trace.get("matched")):
                    res.red_failed = False
                    res.notes.append("browser red control did not load the exact mutated target; "
                                     "startup/mapping failure is not behavioral red")
                if not res.red_failed and mutation:
                    res.notes.append(
                        f"still passes with {mutation[0]} broken, so it does not "
                        f"cover what it says it covers. The mutation replaced "
                        f"{mutation[1].strip()[:60]!r} and the test did not "
                        f"notice.")
                elif not res.red_failed:
                    res.notes.append(
                        f"already passes at {ref[:12]}, so it pins "
                        f"nothing -- it would have passed before the fix existed")
        finally:
            gone = subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt)],
                cwd=repo_root, capture_output=True, text=True)
            if gone.returncode != 0:
                # Every other step in this function reports its own failure by
                # name -- the `worktree add` above quotes git's stderr, both
                # COULD_NOT_RUN branches quote the exit and 300 characters of
                # output -- and this one could not. The temp directory goes with
                # the enclosing `TemporaryDirectory`; the administrative record
                # under `.git/worktrees` does not, and `git worktree prune`
                # appears nowhere in this tree, so a leak has no second
                # mechanism to catch it and left no line saying it happened.
                res.notes.append(
                    f"the parent-commit worktree could not be removed (git "
                    f"exited {gone.returncode}), so a record of it is left "
                    f"under .git/worktrees and `git worktree list` will show "
                    f"it: {(gone.stderr or '').strip()[:200]}")
    return res
