"""Pure analysis for the ``test-weakened`` claim kind.  SPEC.md §2.

The judgement -- what counts as a test file, and how many live tests a source
carries -- lived in `detectors/test_weakened.py`, and `checkers/test_weakened.py`
reached in for it with `from detectors.test_weakened import _count,
_is_test_file`. That is the one place in the repo where a checker imported a
detector, and it imported two private names to do it; every other pair
(`dal_write`, `fail_closed`, `route_auth`, `signature_change`, `test_shape`,
`webhook_replay`, ...) shares through a module here, which is the order
SPEC.md §13.6 states: analysis holds the decision, the checker and the detector
are programs over it.

The rule mattered rather than the tidiness: the two programs each decide what a
test file is, and a disagreement between them means the detector raises a claim
the checker cannot answer, or the reverse.

That move left one piece behind. Which count answers for which suffix stayed in
both programs as a private `_count`, character for character the same, so the
one thing a new language touches was still in two places. `count_for` is that
dispatch, here, where the counts it dispatches over already live.
"""

import ast
import re
from pathlib import Path

TEST_FILE = ("test_", "_test.py")

#: TypeScript and JavaScript, from the module that owns which files are tests.
#: Re-exported rather than restated: `is_test_file` below answers a different
#: question from `subject_files.is_test` -- deliberately, and its docstring says
#: why -- but the two must not disagree about how a TS test file is *named*.
#: Measured on five DeepSWE repos, where two guesses were both wrong: `csstree`
#: uses `lib/__tests/` with no second pair of underscores, and `yjs` names its
#: files `*.tests.js`.
from .subject_files import (  # noqa: E402
    RS_SUFFIXES, TS_SUFFIXES, TS_TEST_DIR, TS_TEST_NAME)

#: Go's test corpus is a compiler rule, not a convention: `go test` builds
#: `*_test.go` and nothing else into the test binary.
GO_TEST_FILE = "_test.go"

#: What `go test` collects.  `Example` is left out on purpose: an example with
#: no `// Output:` comment is compiled and not run, so counting it would make a
#: file look like it holds tests it does not.
GO_TEST_PREFIXES = ("Test", "Benchmark", "Fuzz")

#: Go's equivalent of `@unittest.skip`: the test is still listed, still built,
#: and stops judging the moment it runs.  Same direction as the Python list --
#: a `Skip` nobody can evaluate is treated as off, because a test that stopped
#: running is what this asks about.
GO_SKIPS = ("Skip", "Skipf", "SkipNow")


def is_test_file(path: str, source: str = None) -> bool:
    """Is this file part of the test corpus?  By name and position, not content.

    `subject_files.is_test` is the owner of a *different* question -- "does this
    file hold tests" -- and it reads the source to answer it, which is what
    keeps `checkers/test_shape.py` and `detectors/test_weakened.py` from
    counting as tests.

    That answer cannot be used here, and trying it is how this comment exists:
    the red fixture `all_removed` ships a `test_thing.py` whose entire content
    is `# gone`, because the defect this detector exists to catch is a test file
    that stopped holding tests. Asking "does it hold tests" of the *after* state
    answers no, so the file drops out of the comparison and the deletion becomes
    invisible -- the gate reporting clean about exactly the move it was built
    for.

    The over-wide half the owner's docstring warns about costs nothing here:
    a program that examines tests defines no `test_` functions, so its count is
    zero before and zero after and no finding can come of it.
    """
    name = Path(path).name
    if name.endswith(GO_TEST_FILE):
        return True
    if name.endswith(TS_SUFFIXES):
        # Two questions, not one: TS names some test files and files others by
        # where they sit. `valibot` is all `.test.ts` and `yjs` is all
        # `tests/*.tests.js`, so answering only the first would have read yjs
        # as a repo with no tests at all.
        low = name.lower()
        if any(m in low for m in TS_TEST_NAME):
            return True
        where = "/" + path.replace("\\", "/").lower()
        return any(m in where for m in TS_TEST_DIR)
    if name.endswith(RS_SUFFIXES):
        # True for every `.rs` file, which is the honest answer rather than a
        # loose one. Rust keeps most tests inside the implementation file under
        # `#[cfg(test)]` -- 377 of the corpus's 455 files holding tests are not
        # under a `tests/` directory and are not named like tests -- so a name
        # cannot decide this, and this signature has only the name.
        #
        # Being over-wide costs nothing, for the reason this function's own
        # docstring already gives: a `.rs` file with no tests counts zero
        # before and zero after, and no finding comes of a number that did not
        # move.
        return True
    if not name.endswith(".py"):
        return False
    return name.startswith(TEST_FILE[0]) or name.endswith(TEST_FILE[1])


#: Decorators that leave a test listed and stop it judging.
#:
#: The test was `"skip" in decorators`, and the set of decorators that make that
#: move is bigger than that substring on both sides. Measured: `count()` returns
#: 1 for a live test, 1 with `@unittest.expectedFailure`, and 1 with
#: `@pytest.mark.xfail` -- both leave the definition, leave a real body, and turn
#: a failing assertion into a green run. So a worker whose test just started
#: failing adds one decorator, the count stays identical, and `test-weakened`
#: sees no change: precisely the move it exists to catch.
#:
#: It cut the other way too. `@pytest.mark.skipif(False, ...)` runs, and a
#: `parametrize` whose argument name happens to contain `skip` runs, and both
#: were dropped from the count.
OFF_DECORATORS = ("skip", "skipIf", "skipif", "skipUnless", "skip_if",
                  "expectedFailure", "xfail", "expected_failure")


def _switched_off(node) -> bool:
    """Does a decorator take this test out of the run?

    The decorator's own name, not a substring of the whole line. `skipif(False,
    ...)` is the one that runs anyway, and its truth is not a syntactic fact --
    so it is treated as off, the safe direction for a rule about a count going
    down: a test that stopped running is what this asks about, and a `skipif`
    nobody can evaluate is one that might have.
    """
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        name = ast.unparse(target).split(".")[-1]
        if name in OFF_DECORATORS:
            return True
    return False


def go_count(shape) -> int:
    """Live Go test functions, or -1 when the source did not parse.

    The same three moves the Python half was walked around by, spelled in Go:

      the definition stays and the body goes    `func TestX(t *testing.T) {}`
      the definition stays and stops judging    `t.Skip("flaky")`
      the file stays and the tests go           counted as 0, not skipped

    `shape` is `None` for a file that will not parse *and* for a machine with
    no Go toolchain, and both have to read as -1 here rather than 0: a count of
    zero against a previous count of six is the finding this kind exists to
    raise, and reporting it because nobody could parse the file would be the
    gate firing at the environment instead of at the work.
    """
    if not isinstance(shape, dict):
        return -1
    skipped = set()
    for c in shape.get("calls") or []:
        if (c.get("name") or "").rsplit(".", 1)[-1] in GO_SKIPS:
            skipped.add(c.get("fn"))
    n = 0
    for fn in shape.get("funcs") or []:
        name = fn.get("name") or ""
        if not name.startswith(GO_TEST_PREFIXES):
            continue
        if name in skipped or fn.get("stmts", 0) == 0:
            continue
        n += 1
    return n


def count(src: str) -> int:
    """Live test functions, or -1 when the source does not parse.

    Counting definitions alone was walked around twice by bypass fixtures:
    replacing every body with `pass` keeps the count and removes the test, and
    `@unittest.skip` keeps the definition and removes the run. Both are the same
    move -- the judge is still listed and no longer judges.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return -1
    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test"):
            continue
        if _switched_off(node):
            continue
        body = [b for b in node.body
                if not (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant))]
        if len(body) == 1 and isinstance(body[0], (ast.Pass, ast.Ellipsis)):
            continue
        if len(body) == 1 and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and body[0].value.value is Ellipsis:
            continue
        n += 1
    return n


#: Two declaration forms, both counted because both are in the corpus. The call
#: form is jest/vitest/mocha/jasmine. The exported-binding form is `lib0/testing`
#: -- `yjs` writes `export const testMapConflict = tc => {…}` and calls nothing,
#: so counting only the call form reported 17 files holding 1 test between them.
#: With both, the same 17 files hold 296.
_TS_CALL = re.compile(r"(?<![\w.$])(?:it|test)\s*\(")
_TS_EXPORTED = re.compile(
    r"^[ \t]*export\s+(?:const|let|var|async\s+function|function)\s+test[\w$]*",
    re.M)

#: An `it(...)` whose body is empty -- this language's spelling of a body
#: replaced by `pass`, which `count` already refuses to count for Python. Only
#: the shape a person actually writes when emptying a test: an arrow or a
#: `function` with nothing but space between the braces.
_TS_EMPTY = re.compile(
    r"(?<![\w.$])(?:it|test)\s*\(\s*[^,]*,\s*"
    r"(?:async\s+)?(?:\([^)]*\)|[\w$]+)\s*=>\s*\{\s*\}"
    r"|(?<![\w.$])(?:it|test)\s*\(\s*[^,]*,\s*"
    r"(?:async\s+)?function\s*(?:[\w$]*)\s*\([^)]*\)\s*\{\s*\}")


#: What makes the function below it a test. Counted on the five corpus repos:
#: `#[test]` 2,809, `#[test_case(…)]` 99, `#[rstest]` 6 -- 2,914 in all. The
#: async runtimes' spellings score zero there and are in the list anyway: they
#: are how the rest of the ecosystem writes an async test, and a list that fits
#: five repos exactly would read a repo built on tokio as having no tests.
#:
#: `#[should_panic]` is not here, and not because of a count. It says how a
#: test ends, not that a function is one -- a `#[should_panic]` function with
#: no `#[test]` above it is never run, so counting it would count a thing that
#: does not judge, and counting it beside `#[test]` would count one test twice.
_RS_TEST_ATTR = re.compile(
    r"#\[\s*(?:test|rstest|test_case|tokio::test|async_std::test|"
    r"actix_rt::test|googletest::test|test_log::test)\b")

#: The Rust spelling of `@unittest.skip`: still listed, no longer run.
_RS_IGNORE = re.compile(r"#\[\s*ignore\b")

#: One of these is one test, and a function carries as many as it has cases.
#: Counting the function once instead read the corpus's 99 `#[test_case]`
#: attributes as 12 tests -- `boa/core/engine/src/module/loader/mod.rs` alone
#: has 46 cases on one function, and deleting 45 of them would have left the
#: count unmoved, which is the exact move `test-weakened` exists to catch.
_RS_TEST_CASE = re.compile(r"#\[\s*test_case\b")

def _rs_body_is_empty(masked: str, brace: int, body_end) -> bool:
    """Is this body `{}`.

    The Rust spelling of a body replaced by `pass`. Read off the mask, so a
    body holding nothing but a comment counts as empty -- which it is: the
    definition is still listed and it judges nothing.
    """
    if body_end is None:
        return False
    return not masked[brace + 1:body_end - 1].strip()


def rs_count(src: str) -> int:
    """Live tests in a Rust file.

    No parser, for the reason `symbols.rs_masked` records: there is no Rust
    toolchain to lean on the way the Go path leans on `go/parser`, and every
    Rust parser is a crate. So this reads masked source, and like the
    TypeScript count it is never negative -- no parse to fail, no toolchain to
    be missing, so every answer is a real count.

    Three ways a test stops being one, all three subtracted rather than left to
    be discovered:

        the attribute goes        not matched, so never counted
        `#[ignore]` is added      listed and not run -- excluded
        the body is emptied       `fn x() {}` -- excluded

    Two things this does not see, both counted rather than guessed at:

    `#[cfg(feature = "…")] mod tests`, where a module of tests stops being
    compiled because a feature flag moved. The count stays and the tests are
    gone. Seeing it needs the feature resolution `cargo` does.

    `#[test] fn $name()` inside a `macro_rules!` body -- a template, not a
    test, and the tests it makes appear where the macro is invoked. Six corpus
    files write tests this way. Counting the template as one test would be
    wrong in the other direction, since one template can make twenty; what is
    actually missed is a repo deleting the macro and losing every test it made
    while this count does not move.
    """
    from . import rssource
    masked = rssource.rs_masked(src)
    n = 0
    for _name, attrs, brace, body_end in rssource.rs_functions(masked):
        if not _RS_TEST_ATTR.search(attrs):
            continue
        if _RS_IGNORE.search(attrs):
            continue
        if brace is None or _rs_body_is_empty(masked, brace, body_end):
            continue
        n += max(1, len(_RS_TEST_CASE.findall(attrs)))
    return n


def ts_count(src: str) -> int:
    """Live tests in a TypeScript or JavaScript file.

    There is no parser here the way there is for Python and Go, so this reads
    comment- and string-stripped source. Coarser, and enough for what the
    caller asks: `test-weakened` compares a count before against the same count
    after, so a bias landing on both sides cancels. What does not cancel is a
    test that stays listed and stops judging, so those are excluded.

    The other suppression shape needs no arithmetic. `_TS_CALL` requires the
    bracket immediately after the name, so `it.skip(` never matches it; and its
    lookbehind rejects a word character before `it`, so `xit(` does not either.
    Both are absent from the total by construction rather than subtracted from
    it -- measured on `csstree`, which ships 23 of them.

    Stripping first is not optional. `symbols.py` carries the three regexes
    because an `it(` inside a comment or a doc string is not a test, and
    counting one is how a file looks like it lost coverage it never had.

    What this does not see, stated rather than left to be discovered: a
    `describe.skip(…)` wrapping live `it(…)` calls. Those still count, because
    knowing they are inside the skipped block needs brace matching, which is a
    parser. A repo that suppresses a whole block that way keeps its count and
    loses its tests.
    """
    from . import symbols
    src = symbols._TS_BLOCK_COMMENT.sub(" ", src)
    src = symbols._TS_LINE_COMMENT.sub(" ", src)
    src = symbols._TS_STRING.sub('""', src)
    # Counted, then removed, so the call pattern cannot see them: `export
    # function test(tc)` matches both patterns, and a binding named exactly
    # `test` would otherwise be worth two.
    exported = len(_TS_EXPORTED.findall(src))
    src = _TS_EXPORTED.sub(" ", src)
    n = exported + len(_TS_CALL.findall(src)) - len(_TS_EMPTY.findall(src))
    return max(n, 0)


def count_for(path: str, src: str) -> int:
    """Live tests in this source, whichever language it is.

    Which of the four counts above answers for a path was written out twice --
    identically, in `detectors/test_weakened.py::_count` and
    `checkers/test_weakened.py::_count` -- in the one pair whose two module
    docstrings are both about that. The checker used to import the detector's
    private names; the fix moved the judgement here, and the dispatch over that
    judgement stayed behind in both copies. So adding a language meant editing
    two files, and the detector raising a claim the checker cannot answer is
    exactly what a split between these two produces.

    The dispatch is judgement, not plumbing: it decides that `.go` is answered
    by Go's own parser and `.rs` by masked source, and it is where a suffix the
    two halves disagree about would show up.

    Go's count comes from Go's own parser, and `shape_source` takes source
    rather than a path because both sides of this comparison are `git show`
    output with no file behind it. `-1` means "could not read", and every caller
    skips on it -- reporting a drop to zero because nobody could parse the file
    would be the gate firing at the environment.
    """
    if path.endswith(".go"):
        from . import gosource
        return go_count(gosource.shape_source(src))
    if path.endswith(RS_SUFFIXES):
        # Same shape as the TypeScript branch and for the same reason: no
        # toolchain to be missing and no parse to fail, so `rs_count` has no
        # way to answer `-1` and every answer it gives is a real count.
        return rs_count(src)
    if path.endswith(TS_SUFFIXES):
        # Never negative either: a regex over stripped source has neither of
        # the two failures the other languages have, so every TS answer is a
        # real count and the callers' skip-on-negative branch is simply never
        # taken for one.
        return ts_count(src)
    return count(src)
