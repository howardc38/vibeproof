"""Rust source, read without a Rust parser.

`pysource` leans on `ast` because the interpreter running this ships one.
`gosource` leans on Go's own parser because a Go repo has a Go toolchain. There
is no third case: every Rust parser is a crate, this framework takes no
third-party dependency, and `rustc` is not something an adopting repo can be
assumed to have. So this is a scanner and a small set of expressions over what
it produces, and the compromise carries fixtures rather than a promise -- the
`bypass/` cases put a test inside a comment and inside a raw string, and if one
ever gets through, the answer is a narrower expression before it is a parser.

Nothing here imports anything else in this package. That is the property the
module exists to keep: `subject_files` asks it whether a file holds tests, and
`subject_files` is imported by checkers that have never read a line of Go.
"""

from __future__ import annotations

import re

# ── Rust ─────────────────────────────────────────────────────────────────────
#
# TypeScript is masked by three regexes applied in turn. Rust is masked by a
# scanner, and the reason is three counts off the five corpus repos rather than
# a preference:
#
#   8,138  lifetimes (`&'a str`, `<'a,`)  -- a char-literal pattern reads `'a`
#          as an opening quote and swallows source up to the next apostrophe,
#          which in a doc comment is usually several lines away
#   1,910  raw strings (`r"…"`, `r#"…"#`) -- the terminator is chosen by the
#          writer, so no fixed pattern ends it
#       3  nested block comments (`/* /* */ */`) -- Rust nests them and the
#          first `*/` is not the end
#
# The scanner preserves length: every masked byte becomes a space and newlines
# survive, so an offset or a line number taken from the mask still points at
# the same place in the source. `_TS_STRING` substitutes `""` and does not,
# which is why its callers cannot slice the original by mask offsets.

_RS_ID = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def rs_masked(src: str) -> str:
    """Rust source with comments and literal bodies blanked, length preserved.

    Delimiters are kept -- a caller looking for `"` to find a string still
    finds one, and what is gone is anything that could have been mistaken for
    code. What stays is code, so a declaration inside a comment or a string is
    not a declaration any more.

    The one judgement in here is the apostrophe. `'a` that is not followed by a
    closing `'` is a lifetime and only the quote is consumed; `'x'` and `'\n'`
    are char literals and the body goes. A lifetime named with a single letter
    and a char literal are two characters apart in the source and the corpus
    has thousands of both.
    """
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            depth, j = 1, i + 2
            while j < n and depth:
                if src.startswith("/*", j):
                    depth += 1
                    j += 2
                elif src.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            out.append("".join(ch if ch == "\n" else " " for ch in src[i:j]))
            i = j
        elif (c in "rb" and i + 1 < n
              and (src[i + 1] in '#"' or src[i:i + 2] == "br")):
            j = i + 1
            if src[j] == "r":                       # `br"…"` and `br#"…"#`
                j += 1
            hashes = 0
            while j < n and src[j] == "#":
                hashes += 1
                j += 1
            if j >= n or src[j] != '"':             # an identifier, not a raw
                out.append(c)
                i += 1
                continue
            close = '"' + "#" * hashes
            k = src.find(close, j + 1)
            k = n if k < 0 else k + len(close)
            out.append(src[i:j + 1])
            body = src[j + 1:k]
            out.append("".join(ch if ch == "\n" else " " for ch in body))
            i = k
        elif c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            out.append('"')
            out.append("".join(ch if ch == "\n" else " " for ch in src[i + 1:j]))
            if j < n:
                out.append('"')
            i = min(j + 1, n)
        elif c == "'":
            # A char literal ends within four bytes (`'a'`, `'\n'`, `'\\u{1F}'`
            # is longer and starts with a backslash). Anything else is a
            # lifetime, and consuming past the quote would eat live code.
            if i + 1 < n and src[i + 1] == "\\":
                j = src.find("'", i + 2)
                j = i + 1 if j < 0 else j
                out.append("'" + " " * (j - i - 1))
                i = j
            elif i + 2 < n and src[i + 2] == "'":
                out.append("' ")
                i += 2
            else:
                out.append("'")                     # lifetime: quote only
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


#: A definition. Attributes above it are found by walking rather than by a
#: pattern -- see `rs_functions`.
_RS_FN = re.compile(
    r"^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?(?:default[ \t]+)?(?:const[ \t]+)?"
    r"(?:async[ \t]+)?(?:unsafe[ \t]+)?"
    r"fn[ \t]+(\w+)", re.M)


def rs_balanced(masked: str, at: int, open_ch: str, close_ch: str):
    """Index just past the delimiter matching the one at `at`, or None.

    Runs on the mask, where a brace inside a string or a comment is a space, so
    counting is the whole algorithm.
    """
    depth = 0
    for i in range(at, len(masked)):
        if masked[i] == open_ch:
            depth += 1
        elif masked[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def _rs_attrs_above(lines, idx: int) -> str:
    """The attributes attached to the definition on line `idx`.

    Walked upward with bracket depth rather than matched with a pattern, for a
    counted reason: 30 of the corpus's 2,914 test attributes have a multi-line
    attribute between them and their function --

        #[test]
        #[should_panic(
            expected = "a span cannot start after its end"
        )]
        fn spans_are_ordered() { … }

    -- and a pattern requiring each attribute to close on its own line stops at
    the `)]`, never sees the `#[test]` above it, and reads those 30 as having
    no attribute at all. A blank line is stepped over rather than stopped at,
    because a comment between the attribute and its definition is blank in the
    mask and 12 more corpus tests are written that way. The walk still ends at
    the first line that is not part of an attribute, which is what bounds it.
    """
    out, depth, i = [], 0, idx - 1
    while i >= 0:
        line = lines[i].strip()
        if not line:
            i -= 1
            continue
        if depth == 0 and not line.endswith("]"):
            break
        out.append(line)
        depth += line.count("]") - line.count("[")
        if depth < 0:
            break
        i -= 1
    return "\n".join(reversed(out))


def rs_functions(masked: str):
    """Every function in masked Rust source.

    Yields `(name, attrs, brace, body_end)`: the name, the attribute text above
    it, the index of the `{` that opens its body and the index just past the
    matching `}`. `brace` is None for a definition with no body -- a trait
    method signature ending in `;`.

    One home for a question two kinds ask. `test-weakened` counts what carries
    a test attribute; `test-expectation` reads what is inside the body of one.
    """
    lines = masked.splitlines()
    for m in _RS_FN.finditer(masked):
        attrs = _rs_attrs_above(lines, masked.count("\n", 0, m.start()))
        brace = masked.find("{", m.end())
        semi = masked.find(";", m.end())
        if brace < 0 or (0 <= semi < brace):
            yield m.group(1), attrs, None, None
            continue
        yield m.group(1), attrs, brace, rs_balanced(masked, brace, "{", "}")

