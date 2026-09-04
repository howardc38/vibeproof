"""Was the expectation edited to fit the result?  SPEC.md §3.

Layer 1 carries "Never edit the expectation to match the result" and nothing
fired on it. `test-weakened` counts test functions, so deleting a test is
caught; changing what one expects is not, and it is the cheaper move -- the
suite stays the same size, the diff looks like maintenance, and the claim goes
ANSWERED.

The judgement this makes, and the one it refuses to make:

  it fires when an assertion's expected literal changed **and no non-test
  source changed in the same diff**. If the code moved too, the expectation
  moving with it is ordinary work and this says nothing.

That conjunct is the whole rule. Without it every legitimate behaviour change
raises a claim, which is the noise that gets a checker switched off. With it,
what is left is a diff that says: the code is what it was, and the test now
agrees with it.

Compared per test function and per literal, not per line: reordering arguments
or reformatting a file is not a changed expectation.
"""

import ast
import re

#: Calls whose expected value is a literal argument.  `assertTrue` is absent on
#: purpose -- it carries no expectation to edit, and weakening `assertEqual` to
#: `assertTrue` is a different rule with a different fixture.
#:
#: The comparison assertions were absent with no reason given for any of them,
#: and the sentence above was written as though the set were complete. Loosening
#: `assertGreaterEqual(n, 10)` to `assertGreaterEqual(n, 1)` is exactly "edit
#: the expectation to match the result", and `expectations()` recorded nothing
#: for it, so `changed()` saw an identical list and the rule said nothing.
#: Counted in this repo's own suite at the time: 12 `assertGreaterEqual`,
#: 7 `assertGreater`, 4 `assertLess`, 3 `assertLessEqual`, 1 `assertRegex`.
#: The bare-assert branch already covered `assert n >= 10` because it reads both
#: sides of a `Compare`, so the same edit was visible written one way and
#: invisible written the other.
ASSERT_CALLS = {
    "assertEqual": (0, 1), "assertNotEqual": (0, 1),
    "assertIn": (0, 1), "assertNotIn": (0, 1),
    "assertIs": (0, 1), "assertAlmostEqual": (0, 1),
    "assertNotAlmostEqual": (0, 1),
    "assertListEqual": (0, 1), "assertDictEqual": (0, 1),
    "assertSetEqual": (0, 1), "assertCountEqual": (0, 1),
    "assertTupleEqual": (0, 1), "assertMultiLineEqual": (0, 1),
    "assertGreater": (0, 1), "assertGreaterEqual": (0, 1),
    "assertLess": (0, 1), "assertLessEqual": (0, 1),
    "assertRegex": (0, 1), "assertNotRegex": (0, 1),
}


def _literal(node):
    """A comparable value, or None when the node is not a literal."""
    if isinstance(node, ast.Constant) and not isinstance(node.value, type(...)):
        return repr(node.value)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        parts = [_literal(e) for e in node.elts]
        return None if any(p is None for p in parts) else "[" + ",".join(parts) + "]"
    if isinstance(node, ast.Dict):
        pairs = []
        for k, v in zip(node.keys, node.values):
            lk, lv = (_literal(k) if k is not None else None), _literal(v)
            if lk is None or lv is None:
                return None
            pairs.append(f"{lk}:{lv}")
        return "{" + ",".join(sorted(pairs)) + "}"
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _literal(node.operand)
        return None if inner is None else "-" + inner
    return None


def expectations(fn) -> list:
    """Every literal this test asserts against, sorted.

    Sorted rather than positional: swapping two assertions is not a changed
    expectation, and a checker that says it is spends its credibility on
    formatting.
    """
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert):
            test = node.test
            if isinstance(test, ast.Compare):
                for side in [test.left] + list(test.comparators):
                    lit = _literal(side)
                    if lit is not None:
                        out.append(lit)
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else \
                getattr(node.func, "id", "")
            if name not in ASSERT_CALLS:
                continue
            for idx in ASSERT_CALLS[name]:
                if idx < len(node.args):
                    lit = _literal(node.args[idx])
                    if lit is not None:
                        out.append(lit)
    return sorted(out)


def test_functions(src: str) -> dict:
    """{name: [expected literals]} for every test function, or None if unparsable."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name.startswith("test"):
            out[node.name] = expectations(node)
    return out


def changed(before: str, now: str) -> list:
    """[(test_name, before_literals, now_literals)] -- expectations that moved.

    A test present in only one of the two is not this rule's business: adding
    one is normal and removing one is `test-weakened`.
    """
    a, b = test_functions(before), test_functions(now)
    if a is None or b is None:
        return []
    out = []
    for name in sorted(set(a) & set(b)):
        if a[name] != b[name] and (a[name] or b[name]):
            out.append((name, a[name], b[name]))
    return out


#: Go's failure reporters. A comparison that guards one of these is an
#: assertion; a comparison that guards anything else is a loop bound, a retry,
#: a nil guard. Go has no `assert` statement, so this pairing is the only thing
#: that tells the two apart, and getting it wrong in the loose direction would
#: make every `if i < 10` a moved expectation the first time somebody changed a
#: batch size.
_GO_REPORTERS = ("Error", "Errorf", "Fatal", "Fatalf", "Fail", "FailNow")

#: testify, and only the calls whose expected value sits at a position this can
#: name. `assert.Len(t, xs, 3)` counts the third argument and `assert.Equal`
#: the second; a table is how the Python half says the same thing, and a call
#: that is not in it is left alone rather than guessed at.
#:
#: The receiver must be `assert` or `require`. A method named `Equal` on a
#: domain type is not this, and matching on the last segment alone would make
#: it this -- the collision `_REQUEST_ROOTS` records paying for.
GO_ASSERT_CALLS = {"Equal": (1,), "NotEqual": (1,), "EqualValues": (1,),
                   "JSONEq": (1,), "Len": (2,)}
_GO_ASSERT_ROOTS = ("assert", "require")

#: A table-driven case names its expectation. `want`, `wantErr`, `wantN`,
#: `expected`, `expect` -- one prefix each, because the suffix is the field it
#: is about and there is no list of those.
_GO_WANT_KEYS = ("want", "expect")


def go_expectations(shape, fn: str) -> list:
    """Every literal the Go test named `fn` asserts against, sorted."""
    out = []
    for c in shape.get("checks") or []:
        if c.get("fn") != fn:
            continue
        if not any(str(call).split(".")[-1] in _GO_REPORTERS
                   for call in c.get("calls") or []):
            continue
        out.extend(c.get("lits") or [])
    for lit in shape.get("lits") or []:
        if lit.get("fn") != fn:
            continue
        where = lit.get("in")
        if where == "field":
            if str(lit.get("key") or "").lower().startswith(_GO_WANT_KEYS):
                out.append(lit.get("value"))
        elif where == "arg":
            parts = str(lit.get("call") or "").split(".")
            if len(parts) < 2 or parts[0] not in _GO_ASSERT_ROOTS:
                continue
            if lit.get("idx") in GO_ASSERT_CALLS.get(parts[-1], ()):
                out.append(lit.get("value"))
    return sorted(str(v) for v in out)


def go_test_functions(src: str) -> dict:
    """{name: [expected literals]} for every Go test, or None if unparsable.

    `test_functions` above is the same answer for Python, and the two are kept
    apart rather than dispatched inside one function because the extractors are
    different programs -- `ast` here, a compiled helper there.
    """
    from . import gosource
    from .test_weakened import GO_TEST_PREFIXES
    shape = gosource.shape_source(src)
    if shape is None:
        return None
    out = {}
    for fn in shape.get("funcs") or []:
        name = fn.get("name") or ""
        if name.startswith(GO_TEST_PREFIXES):
            out[name] = go_expectations(shape, name)
    return out


def go_changed(before: str, now: str) -> list:
    """`changed`, for Go.  Same rule about a test present in only one side."""
    a, b = go_test_functions(before), go_test_functions(now)
    if a is None or b is None:
        return []
    out = []
    for name in sorted(set(a) & set(b)):
        if a[name] != b[name] and (a[name] or b[name]):
            out.append((name, a[name], b[name]))
    return out


# ── TypeScript and JavaScript ────────────────────────────────────────────────
#
# A third shape. Python puts the expected value in an argument to a named
# assert; Go puts it in an argument to `assert.Equal`; TypeScript puts it in an
# argument to the *matcher* that follows `expect(...)`. The set below is what
# 406 test files across five DeepSWE repos actually use, counted rather than
# recalled: toStrictEqual 2015, toBe 729, toEqual 394, toContain 230,
# assert.strictEqual 316, assert.deepStrictEqual 149.
#
# Absent on purpose, on the same principle the Python table states: a matcher
# carrying no expectation to edit is not this rule. `toBeDefined` (52) and
# `toBeUndefined` (78) take no argument. `toThrowError` (122) takes a message
# or a class, and weakening a thrown-type assertion is a different rule with a
# different fixture. `toMatchSnapshot` (49) has its expectation in another
# file, and `toMatchInlineSnapshot` (76) is rewritten by the tool on purpose,
# so treating either as a hand-edited expectation would fire on the tool.
#
# Also absent, and worth naming because they are frequent: `toParse` (433),
# `toBuild` (250), `toJSON` (238), `toDelta` (141). Those are matchers the repo
# defines itself, and this cannot know which argument of a matcher it has never
# seen carries the expectation. A custom matcher is left alone rather than
# guessed at -- the same call the Go table makes.
TS_MATCHERS = frozenset({
    "toBe", "toEqual", "toStrictEqual", "toContain", "toContainEqual",
    "toHaveLength", "toBeCloseTo", "toBeGreaterThan", "toBeGreaterThanOrEqual",
    "toBeLessThan", "toBeLessThanOrEqual",
    "toHaveBeenCalledWith", "toHaveBeenLastCalledWith", "toHaveBeenCalledTimes",
    # Same shape as `toEqual`, and 10 of them sat in corpus files this
    # otherwise recorded nothing for.
    "toMatchObject",
})

#: node:assert and chai, by method name. Same rule: the ones carrying a value.
TS_ASSERTS = frozenset({
    "strictEqual", "notStrictEqual", "deepStrictEqual", "notDeepStrictEqual",
    "equal", "notEqual", "deepEqual", "notDeepEqual",
})

_TS_EXPECT = re.compile(r"(?<![\w.$])expect\s*\(")
_TS_MATCHER_AFTER = re.compile(
    r"\s*\.\s*(?:(?:resolves|rejects|not)\s*\.\s*)*([\w$]+)\s*\(")
_TS_ASSERT_CALL = re.compile(r"(?<![\w.$])assert\s*\.\s*([\w$]+)\s*\(")
_TS_TEST_CALL = re.compile(r"(?<![\w.$])(?:it|test)\s*\(")
_TS_EXPORTED_TEST = re.compile(
    r"^[ \t]*export\s+(?:const|let|var|async\s+function|function)\s+(test[\w$]*)",
    re.M)

#: What counts as a literal, applied to the argument's source text. Anything
#: else -- a call, an identifier, an expression -- is not recorded, because a
#: renamed helper is not a moved expectation.
_TS_LITERAL = re.compile(
    r"""^(?:-?\d[\d_]*(?:\.\d+)?(?:e-?\d+)?n?          # 42, 1_000, 1.5, 2e3, 5n
        |0[xXbBoO][0-9a-fA-F_]+                        # 0xff
        |true|false|null|undefined
        |'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|`(?:[^`\\$]|\\.)*`
        )$""", re.X | re.S)

#: `[…]` and `{…}` of literals, which the Python half accepts through
#: `ast.List` and `ast.Dict` and this half was refusing. Measured before the
#: fix: expectations came off 97 of 406 corpus files, because `valibot` --
#: 3,322 of the 4,842 tests -- writes `toStrictEqual({ kind: 'check', … })`
#: and every one of them was being dropped as "not a literal".
#:
#: There is no parser, so literalness is decided on the masked copy, where
#: strings are already blank. Remove the object keys, the numbers, the three
#: keywords and the punctuation; if anything is left it is an identifier or an
#: expression, and this is not a literal after all.
_TS_STRUCT_NOISE = re.compile(
    r"""[A-Za-z_$][\w$]*\s*:                    # a key
        |-?\d[\d_]*(?:\.\d+)?(?:e-?\d+)?n?      # a number
        |0[xXbBoO][0-9a-fA-F_]+
        |\b(?:true|false|null|undefined)\b
        |[\[\]{},:\s]                           # structure
        |\.\.\.                                 # spread of nothing readable
     """, re.X)


def _ts_looks_literal(raw: str, masked: str) -> bool:
    """Is this argument a literal value, scalar or structured?"""
    if _TS_LITERAL.match(raw):
        return True
    if not (raw.startswith("[") or raw.startswith("{")):
        return False
    if "(" in masked or "=>" in masked:
        return False                      # a call or a function is not a value
    return not _TS_STRUCT_NOISE.sub("", masked).strip()


#: `import * as t from 'lib0/testing'`, which is how `yjs` writes every one of
#: its 296 tests. Without it that repo contributed no expectations at all --
#: it never calls `expect` and never imports `assert`. The alias is read off
#: the import rather than assumed to be `t`, for the reason the Go table gives
#: about receivers: a method named `compare` on a domain type is not this.
_TS_LIB0_IMPORT = re.compile(
    r"import\s*\*\s*as\s+([\w$]+)\s*from\s*['\"]lib0/testing['\"]")

#: `t.assert(cond)` is 494 of yjs's calls and carries no expectation to edit --
#: the same reason `assertTrue` is absent from the Python table. `t.compare`
#: is the one that names an expected value, and it sits second.
TS_LIB0_CALLS = {"compare": 1, "compareStrings": 1, "compareArrays": 1,
                 "compareObjects": 1}


def _ts_mask(src: str) -> str:
    """`src` with comments and string bodies blanked, same length throughout.

    Length has to hold, because the brace matching below runs on the mask and
    the text is sliced out of the original. Replacing a string with `""` -- what
    `ts_count` does, where offsets do not matter -- would shift every position
    after the first quote.
    """
    from . import symbols
    out = list(src)
    for rx in (symbols._TS_BLOCK_COMMENT, symbols._TS_LINE_COMMENT,
               symbols._TS_STRING):
        for m in rx.finditer("".join(out)):
            for i in range(m.start(), m.end()):
                if out[i] != "\n":
                    out[i] = " "
    return "".join(out)


def _ts_balanced(mask: str, start: int, open_ch: str, close_ch: str):
    """Index just past the bracket that closes the one at `start`, or None."""
    depth = 0
    for i in range(start, len(mask)):
        if mask[i] == open_ch:
            depth += 1
        elif mask[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _ts_arg_spans(mask: str, open_paren: int) -> list:
    """`[(start, end)]` for each top-level argument of the call at `open_paren`.

    Offsets rather than text, so the caller can slice the same argument out of
    both the source and the mask. Returning two lists of strings instead put
    them out of step: a string argument is all blanks in the mask, `.strip()`
    made it empty, and the empty-tail rule then dropped it -- so index 1 of one
    list was index 0 of the other.
    """
    end = _ts_balanced(mask, open_paren, "(", ")")
    if end is None:
        return []
    inner_a, inner_b = open_paren + 1, end - 1
    spans, depth, start = [], 0, inner_a
    for i in range(inner_a, inner_b):
        c = mask[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            spans.append((start, i))
            start = i + 1
    if start < inner_b or spans:
        spans.append((start, inner_b))
    return spans


def _ts_take(src, mask, open_paren, idx, out):
    """Record argument `idx` of this call when it is a literal."""
    spans = _ts_arg_spans(mask, open_paren)
    if len(spans) <= idx:
        return
    a, b = spans[idx]
    raw, masked = src[a:b].strip(), mask[a:b].strip()
    if _ts_looks_literal(raw, masked):
        out.append(raw)


def ts_expectations(src: str, mask: str, lo: int, hi: int,
                    lib0_alias: str = "") -> list:
    """Expected literals inside `src[lo:hi]`, sorted.

    Sorted for the reason the Python half gives and this half first got wrong:
    swapping two assertions is not a changed expectation, and a checker that
    says it is spends its credibility on formatting. The
    `green/ts_assertions_reordered` fixture is what caught it.
    """
    out = []
    for m in _TS_EXPECT.finditer(mask, lo, hi):
        after = _ts_balanced(mask, m.end() - 1, "(", ")")
        if after is None:
            continue
        tail = _TS_MATCHER_AFTER.match(mask, after)
        if not tail or tail.group(1) not in TS_MATCHERS:
            continue
        _ts_take(src, mask, tail.end() - 1, 0, out)
    for m in _TS_ASSERT_CALL.finditer(mask, lo, hi):
        if m.group(1) not in TS_ASSERTS:
            continue
        # `assert.strictEqual(actual, expected)` -- the second argument is the
        # expectation, the same position the Go table names for `assert.Equal`.
        _ts_take(src, mask, m.end() - 1, 1, out)
    if lib0_alias:
        for m in re.finditer(
                r"(?<![\w.$])" + re.escape(lib0_alias) + r"\s*\.\s*([\w$]+)\s*\(",
                mask[lo:hi]):
            idx = TS_LIB0_CALLS.get(m.group(1))
            if idx is None:
                continue
            _ts_take(src, mask, lo + m.end() - 1, idx, out)
    return sorted(out)


def ts_test_functions(src: str) -> dict:
    """{name: [expected literals]} for every test in a TS/JS file.

    Both declaration forms, because both are in the corpus: the `it(...)` call
    and `yjs`'s exported `testXxx` binding. Bodies are found by matching braces
    on the masked copy -- there is no parser here, and the alternative is
    comparing literals file-wide, which would report a reordering as a moved
    expectation.
    """
    mask = _ts_mask(src)
    # Searched in the source, not the mask: the module path is a string, and
    # the mask exists precisely to blank string bodies -- so looking for
    # `'lib0/testing'` there finds a run of spaces every time. Measured: yjs
    # contributed 0 expectations from 293 tests until this line moved.
    lib0 = _TS_LIB0_IMPORT.search(src)
    alias = lib0.group(1) if lib0 else ""
    out, seen = {}, {}

    def name_for(base):
        seen[base] = seen.get(base, 0) + 1
        return base if seen[base] == 1 else f"{base}#{seen[base]}"

    for m in _TS_TEST_CALL.finditer(mask):
        open_paren = m.end() - 1
        end = _ts_balanced(mask, open_paren, "(", ")")
        if end is None:
            continue
        spans = _ts_arg_spans(mask, open_paren)
        label = src[spans[0][0]:spans[0][1]].strip().strip("`'\"") if spans else ""
        brace = mask.find("{", open_paren, end)
        if brace == -1:
            continue
        body_end = _ts_balanced(mask, brace, "{", "}")
        if body_end is None:
            continue
        out[name_for(label or "<anonymous>")] = ts_expectations(
            src, mask, brace, body_end, alias)

    for m in _TS_EXPORTED_TEST.finditer(mask):
        brace = mask.find("{", m.end())
        if brace == -1:
            continue
        body_end = _ts_balanced(mask, brace, "{", "}")
        if body_end is None:
            continue
        out[name_for(m.group(1))] = ts_expectations(
            src, mask, brace, body_end, alias)
    return out


# ── Rust ─────────────────────────────────────────────────────────────────────
#
# Which argument holds the expectation was measured rather than recalled. Of
# the corpus's 2,367 `assert_eq!` calls where both arguments could be read,
# exactly one side is a literal in 1,219 of them -- and it is the right-hand
# side 1,177 times against the left's 42. So `assert_eq!(actual, expected)`,
# the same position the Go table names for `assert.Equal` and the Python half
# names for `assertEqual`.
#
# `assert!(cond)` carries no expectation to edit, which is why it is absent for
# the same reason `assertTrue` is absent from the Python table. The 409 `insta`
# snapshot assertions are absent too and for a different reason: their expected
# value lives in a `.snap` file beside the test, so editing one does not touch
# the source this reads.
#
# That gap is `known_miss/rs_insta_snapshot_lives_in_another_file`, and a test
# runs it and asserts it returns 0. This comment said it was a fixture while it
# was not one, which is worse than saying nothing -- a reader who believes it
# stops looking. A `request-fidelity` reviewer went and looked.

#: Macros whose expectation sits at this argument index.
RS_ASSERT_ARGS = {"assert_eq": 1, "assert_ne": 1, "debug_assert_eq": 1,
                  "debug_assert_ne": 1}

_RS_ASSERT = re.compile(r"(?<![\w.])(\w+)!\s*\(")

#: A value rather than a computation. Rust's literals, plus the four wrappers
#: that a test writes an expected value inside (`Some`, `Ok`, `Err`, `vec!`)
#: and the `Type::Variant` path form -- `assert_eq!(k, Keyword::Async)` is an
#: expectation by every reading, and it is not a literal in any language's
#: grammar.
_RS_LITERAL = re.compile(
    r"""^(?:
        (?:b|r|br)?\#*"                 # string, raw string, byte string
      | '                               # char
      | -?\d
      | true\b | false\b
      | None\b
      | \(\s*\)\s*$                     # the unit value, as in `Ok(())`
      | &?\[
      | vec!
      | [A-Z]\w*(?:::\w+)+\s*$           # Type::Variant, no call
    )""", re.X)

#: A wrapper whose arguments are themselves the expectation. `Some`, `Ok` and
#: `Err` are the three a test writes an expected value inside; the path form is
#: `Position::new(10, 50)`, which is how the corpus writes an expected value of
#: a type that has no literal syntax.
_RS_WRAPPED = re.compile(r"^(?:Some|Ok|Err|[A-Z]\w*(?:::\w+)+)\s*\(")


def _rs_split_args(inner: str):
    """Top-level comma-separated pieces of `inner`."""
    out, depth, start = [], 0, 0
    for i, c in enumerate(inner):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            out.append(inner[start:i])
            start = i + 1
    out.append(inner[start:])
    return [x for x in out if x.strip()]


def _rs_is_expectation(raw: str) -> bool:
    """Is this argument a value a test pins, rather than a thing it computes.

    A construction counts when everything inside it counts, and stops counting
    the moment a name appears: `Position::new(10, 50)` is an expected value and
    editing the 10 edits the expectation, while `Position::new(line, col)` is
    computed from the test's own variables and moving it is not this rule.

    Rejecting is the safe direction. A value wrongly taken for an expectation
    becomes a FAIL on a test nobody weakened.
    """
    raw = raw.strip()
    if not raw:
        return False
    m = _RS_WRAPPED.match(raw)
    if m and raw.endswith(")"):
        inner = raw[m.end():-1].strip()
        return all(_rs_is_expectation(a) for a in _rs_split_args(inner)) \
            if inner else True
    return bool(_RS_LITERAL.match(raw))


def _rs_arg_spans(masked: str, open_paren: int):
    """`(start, end)` for each top-level argument of the call opening here."""
    from . import rssource
    end = rssource.rs_balanced(masked, open_paren, "(", ")")
    if end is None:
        return []
    spans, depth, start = [], 0, open_paren + 1
    for i in range(open_paren, end):
        c = masked[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                spans.append((start, i))
                break
        elif c == "," and depth == 1:
            spans.append((start, i))
            start = i + 1
    return spans


def rs_expectations(src: str, masked: str, lo: int, hi: int) -> list:
    """Expected values inside `src[lo:hi]`, sorted.

    Sorted for the reason every other half here is: reordering two assertions
    is not a changed expectation, and a rule that says it is spends its
    credibility on formatting.
    """
    out = []
    for m in _RS_ASSERT.finditer(masked, lo, hi):
        idx = RS_ASSERT_ARGS.get(m.group(1))
        if idx is None:
            continue
        spans = _rs_arg_spans(masked, m.end() - 1)
        if len(spans) <= idx:
            continue
        a, b = spans[idx]
        raw = src[a:b].strip()
        if _rs_is_expectation(raw):
            out.append(raw)
    return sorted(out)


def rs_test_functions(src: str) -> dict:
    """`{test name: expected values}` for one Rust file.

    Every function carrying a test attribute, whether it sits in a `tests/`
    file or inside `#[cfg(test)] mod tests` in the implementation file -- which
    is where 377 of the corpus's 455 test-carrying files keep them.
    """
    from . import rssource
    from .test_weakened import _RS_TEST_ATTR
    masked = rssource.rs_masked(src)
    out, seen = {}, {}
    for name, attrs, brace, body_end in rssource.rs_functions(masked):
        if not _RS_TEST_ATTR.search(attrs) or brace is None or body_end is None:
            continue
        seen[name] = seen.get(name, 0) + 1
        key = name if seen[name] == 1 else "%s#%d" % (name, seen[name])
        out[key] = rs_expectations(src, masked, brace, body_end)
    return out


def rs_changed(before: str, now: str) -> list:
    """`changed`, for Rust.  Same rule about a test present in one side only."""
    a, b = rs_test_functions(before), rs_test_functions(now)
    out = []
    for name in sorted(set(a) & set(b)):
        if a[name] != b[name] and (a[name] or b[name]):
            out.append((name, a[name], b[name]))
    return out


def ts_changed(before: str, now: str) -> list:
    """`changed`, for TypeScript.  Same rule about a test present in one side."""
    a, b = ts_test_functions(before), ts_test_functions(now)
    out = []
    for name in sorted(set(a) & set(b)):
        if a[name] != b[name] and (a[name] or b[name]):
            out.append((name, a[name], b[name]))
    return out
