"""A credential-shaped literal in a test that does not say it is fake.

Measured on the X/Y run. The base handed both arms
``bot_token="123:SECRET"`` in tests/test_client.py. One arm renamed it to
``123:TEST-NOT-A-REAL-TOKEN`` while writing an engagement sentence about the
`secret` rule; the other kept it through all eight tasks, and the `secret`
checker returned 0 every time -- correctly, because nothing was committed that
is a real credential.

Two gates already carried the rule as words. The before-gate has it as a
`secret` engagement rule; the after-gate has a check about committed secrets.
Neither caught the arm that did not do it, because this is not a timing problem
and not a perspective problem -- it is a memory problem, and a memory problem
belongs to a program.

What it costs to be wrong, in the order it happens: the literal gets copied out
of the test into a fixture, then into a script, then into a paste; a scanner
somewhere raises it; somebody spends an afternoon establishing that a string
that looks exactly like a live token never was one. The convention exists so
that afternoon never happens, and a convention nobody can forget is one a
program holds.
"""

from __future__ import annotations

import ast
import re

#: Shapes a scanner -- or a person -- reads as a live credential.  Deliberately
#: the shapes, not the vendors: `123:SECRET` matches none of the vendor
#: patterns `secret` scans for and is exactly the string this exists for.
CREDENTIAL_SHAPE = re.compile(
    r"""(?x)
    ^(?:
        \d{2,}:(?=[A-Za-z0-9_\-]*[A-Za-z_\-])
              [A-Za-z0-9_\-]{4,}           # telegram bot token -- the digits are
                                           # the bot id, and a test writes a short
                                           # one. `123:SECRET` is the literal this
                                           # checker exists for, and a `{6,}` floor
                                           # walked straight past it.
                                           #
                                           # The lookahead requires at least one
                                           # non-digit after the colon, because
                                           # nothing did and the shape a test
                                           # writes far more often than a bot
                                           # token is a port mapping: `5432:5432`
                                           # was reported, and so were 8080:8080
                                           # and 6379:6379, none of them reached
                                           # by SAYS_FAKE. A checker whose
                                           # finding is a docker-compose port has
                                           # spent the credibility this module
                                           # exists to protect, and the fix a
                                           # worker reaches for is to write TEST
                                           # into a port number.
      | sk-[A-Za-z0-9_\-]{8,}              # openai-ish
      | xox[abposr]-[A-Za-z0-9\-]{8,}      # slack
      | ghp_[A-Za-z0-9]{8,}                # github
      | AKIA[A-Z0-9]{8,}                   # aws access key id
      | AIza[A-Za-z0-9_\-]{10,}            # google
      | eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}   # jwt
    )$
    """)

#: What makes a literal say, in itself, that it is not real.  It has to be in
#: the value: a name like `FAKE_TOKEN = "123:SECRET"` leaves the string looking
#: live everywhere it travels, and travelling is the whole failure.
SAYS_FAKE = re.compile(r"test|fake|dummy|example|sample|placeholder|notreal|"
                       r"not[-_]a[-_]real", re.I)


class Finding:
    __slots__ = ("path", "line", "symbol", "literal")

    def __init__(self, path, line, symbol, literal):
        self.path, self.line, self.symbol, self.literal = path, line, symbol, literal

    def as_dict(self):
        return {"path": self.path, "line": self.line, "symbol": self.symbol,
                "literal": redact(self.literal)}


def redact(s: str) -> str:
    """Enough to recognise, never enough to use.

    A checker that prints the credential it found has moved it into the log the
    rule exists to keep it out of.
    """
    head = s[:6]
    return f"{head}[redacted, {len(s)} chars]"


def _enclosing(tree):
    out = {}
    for node in ast.walk(tree):
        name = getattr(node, "name", None)
        if name and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.ClassDef)):
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    out.setdefault(child.lineno, name)
    return out


def _folded(node):
    """The string this node produces, when every part of it is a literal.

    `"123:" + "SECRET" + "VALUE9"` is the same credential as the one written
    whole, and it reaches the same paste. A scanner that only reads whole
    literals is defeated by a plus sign, which is why splitting one is a
    fixture in this checker's own bypass set.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _folded(node.left), _folded(node.right)
        return None if left is None or right is None else left + right
    return None


def scan(source: str, path: str = "") -> list:
    """Credential-shaped literals in this file that do not say they are fake.

    Reads the value, never the name it is bound to. A string is copied by its
    value; the name stays behind.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    where = _enclosing(tree)
    out = []
    for node in ast.walk(tree):
        value = _folded(node)
        if value is None:
            continue
        value = value.strip()
        if not CREDENTIAL_SHAPE.match(value):
            continue
        if SAYS_FAKE.search(value):
            continue
        out.append(Finding(path, node.lineno, where.get(node.lineno, "<module>"),
                           value))
    return out


def go_scan(shape, path: str = "") -> list:
    """`scan`, for Go.  Same two regexes, a different extractor.

    The rule is about the value and the value only, so nothing here is
    language-specific except how a string literal is found. `CREDENTIAL_SHAPE`
    and `SAYS_FAKE` are the rule; keeping one copy of them is the point of the
    split, and the folding of `"123:" + "SECRET"` happens in the emitter for
    the reason `_folded` happens here.
    """
    out = []
    if not isinstance(shape, dict):
        return out
    for lit in shape.get("strs") or []:
        value = str(lit.get("value") or "").strip()
        if not CREDENTIAL_SHAPE.match(value):
            continue
        if SAYS_FAKE.search(value):
            continue
        out.append(Finding(path, lit.get("line", 0),
                           lit.get("fn") or "<module>", value))
    return out


# ── TypeScript and JavaScript ────────────────────────────────────────────────
#
# The rule is about the value and nothing else, so this adds an extractor and
# not a second copy of `CREDENTIAL_SHAPE` and `SAYS_FAKE`. Same split as the Go
# half, for the same reason: one rule, two ways of finding a string literal.

#: Concatenation across a `+`, the TypeScript spelling of what `_folded` does
#: for Python and the emitter does for Go. `"123:" + "SECRET"` is one
#: credential written as two literals, and a rule that reads them apart reads
#: neither of them.
_TS_JOIN = re.compile(r"""(['"])((?:\\.|(?!\1).)*)\1\s*\+\s*(['"])((?:\\.|(?!\3).)*)\3""")


def _ts_comments_blanked(source: str) -> str:
    """`source` with comments replaced by spaces, same length throughout.

    Length has to hold because line numbers are counted off the result, and
    strings are left alone because they are what this reads.
    """
    from . import symbols
    return symbols.ts_mask(source, strings=False)


def ts_scan(source: str, path: str = "") -> list:
    """`scan`, for TypeScript and JavaScript.

    Comments are blanked first: a credential-shaped string inside a comment is
    not a literal this file ships, and counting one is how a test gets a
    finding about a line it never runs.

    The enclosing name is the label of the `it(…)` or `test(…)` the literal
    sits in, falling back to `<module>` -- the same two answers the Go half
    gives, because a literal at file scope belongs to no test in either.
    """
    from . import symbols
    from .test_expectation import _ts_mask, _ts_balanced, _ts_arg_spans
    text = _ts_comments_blanked(source)
    mask = _ts_mask(source)

    # Where each test's body starts and ends, so a literal can be attributed.
    spans = []
    for m in re.finditer(r"(?<![\w.$])(?:it|test)\s*\(", mask):
        open_paren = m.end() - 1
        args = _ts_arg_spans(mask, open_paren)
        if not args:
            continue
        label = source[args[0][0]:args[0][1]].strip().strip("`'\"")
        brace = mask.find("{", open_paren)
        end = _ts_balanced(mask, brace, "{", "}") if brace != -1 else None
        if end is not None:
            spans.append((brace, end, label or "<anonymous>"))

    def enclosing(pos):
        best = "<module>"
        width = None
        for a, b, name in spans:
            if a <= pos < b and (width is None or b - a < width):
                best, width = name, b - a
        return best

    out = []
    seen = set()
    for m in _TS_JOIN.finditer(text):
        joined = (m.group(2) + m.group(4)).strip()
        if CREDENTIAL_SHAPE.match(joined) and not SAYS_FAKE.search(joined):
            line = text.count("\n", 0, m.start()) + 1
            out.append(Finding(path, line, enclosing(m.start()), joined))
            seen.update(range(m.start(), m.end()))
    for m in symbols._TS_STRING.finditer(text):
        if m.start() in seen:
            continue                      # already reported as one joined value
        value = m.group(0)[1:-1].strip()
        if not CREDENTIAL_SHAPE.match(value):
            continue
        if SAYS_FAKE.search(value):
            continue
        line = text.count("\n", 0, m.start()) + 1
        out.append(Finding(path, line, enclosing(m.start()), value))
    return sorted(out, key=lambda f: (f.line, f.literal))
