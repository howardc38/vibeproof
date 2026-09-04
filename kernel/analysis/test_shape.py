"""Two shapes a test can have that make its green mean nothing.  SPEC.md §3.

**Asserting source text.** `assert "with _lock" in inspect.getsource(f)` fails
when you delete the lock, so it satisfies red-green perfectly, and it never
calls the function. `kernel/redgreen.py` already refuses this for a claim that a
test closes a review finding -- by tracing execution -- but that only covers
tests offered as closure. Nothing looks at the suite. Measured on the reference
repo: 28 sites across 15 files read a source or SQL file inside a test.

**Unbounded fan-out.** `await asyncio.gather(*(f(x) for x in items))` where
`items` is a runtime-sized collection has no ceiling: the failure mode is not a
wrong answer, it is a hundred concurrent connections the first time the input is
large. Verified: `app/chat/photo_intake.py:769`.

Both are AST shapes with no configuration, which is why they can be one module.
"""

import ast
import re
import hashlib

from . import pysource, subject_files

KIND = "test-shape"


def findings(rel: str, src: str, tree, variant: str = ""):
    """`[(file, symbol, line, why, variant)]` -- one finding, spelled once.

    Both the checker and the detector need this list and both need the id
    derived from it. It lived in the checker, and the detector emitted claims
    from a different expression -- so the day the detector started applying the
    baseline, "the finding the detector skipped" and "the finding the checker
    forgives" would have been two different strings hashed into two different
    ids, and every baselined finding would have come straight back as a claim.
    One spelling, in the module both already import.

    The enclosing function is part of it. Without it the id is (file, reason),
    so forgiving one read in a file forgives every later one added to it -- 32
    findings in this repo collapsed to 9 ids when that was tried.
    """
    out = []
    is_test = subject_files.is_test(rel, src)
    enclosing = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for ln in range(fn.lineno, (fn.end_lineno or fn.lineno) + 1):
                enclosing.setdefault(ln, fn.name)
    if variant in ("", "source_assertion") and is_test:
        for line, why in source_assertions(tree, src):
            out.append((rel, enclosing.get(line, "<module>"), line,
                        f"{why} — a test that reads source text passes "
                        f"whatever the code does", "source_assertion"))
    if variant in ("", "unbounded_fanout"):
        for line, why in unbounded_fanout(tree, src):
            out.append((rel, enclosing.get(line, "<module>"), line, why,
                        "unbounded_fanout"))
    return out


#: Go's ways of reading a file, by the tail of the callee.  A test that reads
#: source text is the same defect in any language; only the spelling changes.
GO_READERS = ("ReadFile", "ReadAll", "Open", "OpenFile")

#: What bounds a fan-out in Go.  `sync.WaitGroup` is deliberately not here: it
#: waits for goroutines, it does not limit how many start -- and a rule that
#: took it for a bound would pass every unbounded fan-out written carefully.
#:
#: A buffered channel is the idiom with no library behind it, and the only
#: thing separating `make(chan struct{}, 8)` from `make(chan struct{})` is the
#: argument count, which is why the extractor emits it.
GO_BOUNDS = ("Semaphore", "semaphore", "SetLimit", "Acquire", "limiter",
             "Limiter", "Throttle", "chunk", "batch", "Bounded", "bounded",
             "Pool", "pool")


def _go_assigned_into(assigns, fn, names, hops=4):
    """String literals that could have reached a call, through named variables.

    The Go half of `_assigned_into`. It reads the assignments the extractor
    emitted rather than an AST, because the tree is on the other side of a
    subprocess -- but it follows the same four hops and answers the same
    question: not "every string in this function", which made one unrelated
    `.go` literal turn every read into a finding, but "what could have reached
    this call".
    """
    wanted, lits, seen = set(names), [], set()
    for _ in range(hops):
        grew = False
        for i, a in enumerate(assigns):
            if i in seen or a.get("fn") != fn:
                continue
            if not (set(a.get("targets") or []) & wanted):
                continue
            seen.add(i)
            grew = True
            lits += list(a.get("strings") or [])
            wanted |= set(a.get("names") or [])
        if not grew:
            break
    return lits


def go_findings(rel: str, shape: dict, is_test: bool, variant: str = ""):
    """`[(file, symbol, line, why, variant)]` for one Go file.

    The same two shapes, asked of Go:

      **Asserting source text.**  `os.ReadFile("handler.go")` inside a test,
      followed by `strings.Contains` -- red-green perfect, and the function
      under test is never called.  The Python half looks for `inspect.getsource`
      and a `read_text` whose path ends in a source suffix; this looks for Go's
      readers with the same suffix rule, and `SOURCE_SUFFIXES` is shared so the
      two cannot drift.

      **Unbounded fan-out.**  `for _, x := range items { go send(x) }` has no
      ceiling; the failure is a hundred connections the first time the input is
      large, which is exactly what the Python rule is about.  A `go` statement
      outside a loop is a worker and not a fan-out, so the extractor reports
      the loop depth rather than guessing here.

    A bound is code, never a mention: the extractor parses without comments, so
    a comment saying a semaphore was considered cannot satisfy this -- the
    failure the Python rule's own docstring records.
    """
    out = []
    if not isinstance(shape, dict):
        return out
    calls = shape.get("calls") or []
    refs = shape.get("refs") or []

    if variant in ("", "source_assertion") and is_test:
        for c in calls:
            tail = (c.get("name") or "").rsplit(".", 1)[-1]
            if tail not in GO_READERS:
                continue
            lits = list(c.get("strings") or [])
            if not any(str(l).endswith(SOURCE_SUFFIXES) for l in lits):
                # The path may have been assembled a line earlier. Same
                # evasion, same bounded search, as `_assigned_into` on the
                # Python side -- and the same reason it is bounded: a checker
                # that walks until it stops growing is one the code it reads
                # can hang.
                lits += _go_assigned_into(shape.get("assigns") or [],
                                          c.get("fn"), c.get("names") or [])
            if any(str(l).endswith(SOURCE_SUFFIXES) for l in lits):
                out.append((rel, c.get("fn") or "<file>", c.get("line", 0),
                            "reads a source file — a test that reads source "
                            "text passes whatever the code does",
                            "source_assertion"))

    if variant in ("", "unbounded_fanout"):
        bounded = set()
        for c in calls:
            name = c.get("name") or ""
            if any(b in name for b in GO_BOUNDS):
                bounded.add(c.get("fn"))
            # `make(chan T, n)` -- a buffered channel is a bound; an unbuffered
            # one is a rendezvous and bounds nothing.
            if name == "make" and c.get("argc", 0) >= 2:
                bounded.add(c.get("fn"))
        for r in refs:
            if any(b in (r.get("name") or "") for b in GO_BOUNDS):
                bounded.add(r.get("fn"))
        for sp in shape.get("spawns") or []:
            if not sp.get("in_loop") or sp.get("fn") in bounded:
                continue
            out.append((rel, sp.get("fn") or "<file>", sp.get("line", 0),
                        f"a goroutine started for every item in "
                        f"{sp.get('fn')} with nothing bounding it",
                        "unbounded_fanout"))
    return out


def finding_id(t) -> str:
    """The id `kernel.baseline` files this finding under.  No line number.

    Deliberately over `(file, symbol, why)` and not the variant: the variant is
    derivable from the reason and adding it would change every id already in
    every adopter's baseline file.

    Same rule as claim identity: an edit anywhere above renumbers every finding
    below it, and a baseline made of line numbers forgives the wrong thing on
    the next commit.
    """
    return hashlib.sha256(
        "\0".join([KIND, t[0], t[1], t[3]]).encode("utf-8")).hexdigest()[:16]

SOURCE_SUFFIXES = (".py", ".sql", ".js", ".ts", ".go", ".rs")

#: Anything that bounds a fan-out.  Presence anywhere in the enclosing function
#: is enough -- proving the bound actually applies is a reviewer's job, and a
#: checker that tried would flag every careful implementation.
BOUNDS = ("Semaphore", "semaphore", "chunk", "batch", "limit", "islice",
          "gather_limited", "bounded", "TaskGroup", "as_completed", "[:")


def _dotted(node):
    parts, node = pysource.attribute_chain(node)
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def source_assertions(tree, src=None):
    """[(line, why)] -- test code reading source text instead of running it.

    `src` is accepted and unused: every branch works off `tree`. It is kept
    optional rather than removed because three call sites thread it through --
    `findings()` and `kernel/redgreen.py:243`, which does
    `_ts.source_assertions(_ast.parse(_src), _src)` -- and a parameter that
    decides nothing is a thing a reader has to read the body to learn. Now the
    signature says so.

    Three evasions the bypass fixtures found, all of which worked:
      `g = inspect.getsource; g(f)`  -- so the name is checked wherever it is
                                        referenced, not only where it is called
      `p = Path('a') / 'b.py'`       -- so the path literal is looked for in the
        `p.read_text()`                 enclosing function rather than inside
                                        the one call node
      a comment naming `Semaphore`   -- see `unbounded_fanout`

    **Source text, and not reflection.**  `inspect.signature`, `__annotations__`
    and `dir()` are not read here, and a test built only out of them is
    invisible to this rule while having the property the module docstring names:
    it satisfies red-green and never calls the function.  Three in this repo do
    exactly that -- `test_kernel.py::test_the_flag_that_could_not_work_is_gone`,
    `test_the_kernel_reports_what_it_measured.py::test_the_default_timeout_has_one_owner`
    and `test_nothing_seen.py::test_the_report_carries_it`.

    The line is drawn there because the two are not the same act.  Reading a
    function's *text* is never the contract; reading its *signature* sometimes
    is the contract -- an arity, a keyword-only parameter, a default that must
    come from one owner are things a test is entitled to assert, and nothing in
    an AST distinguishes "asserts the signature because that is the promise"
    from "asserts the signature because calling it was harder".  A rule that
    reported both is a gate people learn to ignore, which is the cost
    `fail_closed`'s own scope note weighs the same way.  Three sound tests in
    this repo would be its first three findings.

    Written down as `tests/fixtures/test_shape/known_miss/`
    `a_signature_assertion_never_calls_it` rather than as this paragraph:
    SPEC §12 requires a blind spot to be a fixture with a test asserting it
    returns 0, and `test_the_gaps_that_are_written_down.py` runs every case
    under `known_miss/` off the registry.  The day this rule does grow to see
    reflection, that case fires and says so.
    """
    out = []
    for node in ast.walk(tree):
        # Referenced, not just called: binding the function to a name and
        # calling that is the same read with one more line.
        if isinstance(node, (ast.Name, ast.Attribute)):
            d = node.id if isinstance(node, ast.Name) else _dotted(node)
            if d.endswith("getsource") or d.endswith("getsourcelines"):
                out.append((node.lineno, "inspect.getsource"))
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        if name.endswith("getsource") or name.endswith("getsourcelines"):
            continue          # already reported by the reference branch
        if name.endswith("read_text") or name.endswith("read_bytes"):
            # Extensions matched at the end of a string literal, not as a
            # substring: `data.json` contains `.js`, and the substring form
            # reported a fixture file as a source file.
            lits = [n.value for n in ast.walk(node)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            if not any(l.endswith(SOURCE_SUFFIXES) for l in lits):
                # The path may have been assembled a line earlier. This used to
                # take every string in the enclosing function, which meant one
                # unrelated `.py` literal anywhere in it made every `read_text`
                # in that function a finding. Measured on this repo's own tests:
                # `json.loads((dst / MANIFEST).read_text())` was reported as
                # reading source because the same test also asserts on the
                # string `"checkers/c.py"` -- it reads a JSON manifest.
                #
                # Only what could have reached this call: the names it mentions,
                # and what those names were assigned, followed as far as the
                # assignments go.
                for fn in ast.walk(tree):
                    if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                            and fn.lineno <= node.lineno <= (fn.end_lineno or node.lineno):
                        lits += _assigned_into(fn, node)
            if any(l.endswith(SOURCE_SUFFIXES) for l in lits):
                out.append((node.lineno, "reads a source file"))
    return out


def _assigned_into(fn, call, hops=4):
    """String literals that could have reached `call`, through named variables.

    `p = Path('app') / 'core.py'` then `p.read_text()` has to stay caught -- it
    is one of this rule's bypass fixtures -- and so does one more hop,
    `a = 'core.py'; p = Path('app') / a`. What must not stay caught is a literal
    the call has no path to at all.

    Bounded rather than a fixpoint: four hops is past anything a test does, and
    a checker that walks until it stops growing is a checker that can be made to
    hang by the code it reads.
    """
    names = {n.id for n in ast.walk(call) if isinstance(n, ast.Name)}
    lits, seen = [], set()
    for _ in range(hops):
        grew = False
        for st in ast.walk(fn):
            if not isinstance(st, ast.Assign) or id(st) in seen:
                continue
            targets = {t.id for t in st.targets if isinstance(t, ast.Name)}
            if not targets & names:
                continue
            seen.add(id(st))
            grew = True
            lits += [n.value for n in ast.walk(st.value)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            names |= {n.id for n in ast.walk(st.value) if isinstance(n, ast.Name)}
        if not grew:
            break
    return lits


def unbounded_fanout(tree, src=None):
    """[(line, why)] -- gather/all over a runtime-sized collection with no bound."""
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # A bound has to be code, not a mention. A comment saying "Semaphore
        # was considered here" satisfied a text search, which is the same defect
        # `dead_wiring` found in itself.
        bound = False
        for n in pysource.reachable_nodes(fn):
            if isinstance(n, (ast.Name, ast.Attribute)):
                d = n.id if isinstance(n, ast.Name) else _dotted(n)
                if any(b.strip("[:") and b.strip("[:") in d for b in BOUNDS):
                    bound = True
                    break
            elif isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice):
                bound = True
                break
        if bound:
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            if not _dotted(node.func).endswith("gather"):
                continue
            # A starred generator or comprehension is fan-out over a collection
            # whose size is decided at runtime. `gather(a, b, c)` is three known
            # things and is fine.
            for arg in node.args:
                if isinstance(arg, ast.Starred) and isinstance(
                        arg.value, (ast.GeneratorExp, ast.ListComp, ast.Name)):
                    out.append((node.lineno,
                                f"gather over a runtime-sized collection in "
                                f"{fn.name} with nothing bounding it"))
                    break
    return out


# ── TypeScript and JavaScript ────────────────────────────────────────────────

#: How a TypeScript file reads a file off disk. `SOURCE_SUFFIXES` is shared
#: with the other two halves, so what counts as *source* cannot drift.
TS_READERS = ("readFileSync", "readFile", "readTextFile")

#: What bounds a fan-out here. `Promise.all` over an unbounded `map` is the
#: shape: N requests leave at once and the ceiling is the length of the input.
#: A limiter, a pool, a chunked loop or a queue is a bound; a comment saying
#: "we should limit this" is not, and neither is a string that happens to
#: contain one of these words.
#:
#: Read by `_ts_bounded` below: an *identifier*, in the enclosing function.
#: This used to be `any(b in masked for b in TS_BOUNDS)` over the whole file,
#: over text with the string literals still in it -- so `const label = "batch
#: upload"` anywhere in a file silenced `unbounded_fanout` for all of it. That
#: is the mention-is-not-a-bound failure the Python half's own comment says it
#: was rewritten to stop, still standing on this side.
TS_BOUNDS = ("pLimit", "pMap", "Bottleneck", "Semaphore", "semaphore",
             "concurrency", "limiter", "queue", "chunk", "batch", "pool")

#: An identifier, so a bound is something the code names rather than something
#: the file says. `b in ident` inside it, matching the Python half's `b in d`
#: over a dotted name: `chunked`, `batchSize` and `withLimiter` are bounds.
_TS_IDENT = re.compile(r"[A-Za-z_$][\w$]*")


def _ts_bounded(masked: str, scopes, line: int) -> bool:
    """Does anything a fan-out at `line` can see bound it?

    What it can see is its own function, every function around it, and the
    module level -- and not the body of a function it is not in. A limiter at
    the bottom of a module does not bound a `Promise.all` at the top of it,
    which is what the file-wide search this replaces was saying; a
    `const limit = pLimit(5)` beside the imports does bound one inside a
    function below, which a strictly per-function search would deny.
    """
    hidden = [(a, b) for _n, a, b in scopes if not (a <= line <= b)]
    visible = "\n".join(
        text for i, text in enumerate(masked.splitlines(), start=1)
        if not any(a <= i <= b for a, b in hidden))
    return any(b in ident
               for ident in _TS_IDENT.findall(visible)
               for b in TS_BOUNDS)


#: `arg` starts at the paren, with no `\s*` in front of it. It had one, and on
#: a mask -- where the path is blanks rather than a string -- that `\s*` ate the
#: path and the group began after it, so the one shape this rule is named for
#: read as an argument list with no filename in it.
_TS_READ = re.compile(
    r"(?<![\w.$])(?:%s)\s*\((?P<arg>[^)]{0,200})" % "|".join(TS_READERS))
_TS_TEXT_ASSERT = re.compile(
    r"\.\s*(?:toContain|toMatch|toContainEqual|includes|indexOf|search)\s*\(")
_TS_PROMISE_ALL = re.compile(
    r"(?<![\w.$])Promise\s*\.\s*(?:all|allSettled)\s*\(")
_TS_MAP_CALL = re.compile(r"\.\s*(?:map|flatMap)\s*\(")


def ts_findings(rel: str, source: str, is_test: bool, variant: str = ""):
    """`[(file, symbol, line, why, variant)]` for one TypeScript file.

    The same two shapes, asked of TypeScript.

      **Asserting source text.** `readFileSync('handler.ts')` inside a test and
      then `expect(text).toContain('…')` -- red when the line is deleted, so it
      satisfies red-green, and it never calls the function. Same rule as the
      Python and Go halves, and `SOURCE_SUFFIXES` is the shared list of what
      counts as source so the three cannot drift apart.

      **Unbounded fan-out.** `await Promise.all(items.map(x => fetch(x)))` has
      no ceiling: the failure is not a wrong answer, it is a hundred concurrent
      connections the first time the input is large. A limiter, a pool or a
      chunked loop is a bound; nothing else in the function is.

    Comments *and strings* are blanked before anything is matched -- the mask
    is `test_expectation._ts_mask`, which keeps every offset, so an argument is
    sliced back out of the original source where the rule needs to read it.
    Blanking only the comments left the fan-out bound reading string literals,
    and a reader named inside a string is no more a read than one named inside
    a comment.

    The bound is asked of the enclosing function, not of the file, for the same
    reason the Python half asks it of `fn`: a limiter at the bottom of a module
    bounds nothing at the top of it.
    """
    from .test_expectation import _ts_mask
    from .external_write import _ts_scopes
    masked = _ts_mask(source)
    out = []

    if variant in ("", "source_assertion") and is_test:
        reads_source = []
        for m in _TS_READ.finditer(masked):
            # Out of `source`, not out of the mask: the path is a string
            # literal and the mask is what makes it not one. `_ts_mask` keeps
            # the length, so the same offsets slice the same argument.
            arg = source[m.start("arg"):m.end("arg")]
            if not any(s in arg for s in SOURCE_SUFFIXES):
                continue
            reads_source.append(masked.count("\n", 0, m.start()) + 1)
        if reads_source and _TS_TEXT_ASSERT.search(masked):
            for line in reads_source:
                out.append((rel, "<module>", line,
                            "this test reads a source file and asserts on its "
                            "text; deleting the line makes it red without the "
                            "function under test ever being called",
                            "source_assertion"))

    if variant in ("", "unbounded_fanout"):
        scopes = _ts_scopes(source, masked)
        for m in _TS_PROMISE_ALL.finditer(masked):
            tail = masked[m.end():m.end() + 400]
            if not _TS_MAP_CALL.search(tail):
                continue            # a fixed list of promises has a ceiling
            line = masked.count("\n", 0, m.start()) + 1
            if _ts_bounded(masked, scopes, line):
                continue
            out.append((rel, "<module>", line,
                        "every item in the mapped list starts at once and "
                        "nothing bounds how many run together",
                        "unbounded_fanout"))
    return out
