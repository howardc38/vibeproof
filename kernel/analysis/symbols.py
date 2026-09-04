"""Does this file define this name.  One question, one extractor per language.

`request_cover.unresolved` and nothing else asks it: a coverage entry says
`path::name`, and the entry accounts for nothing if the name is not there.  The
module's own docstring records what that cost before Python was checked --

    two entries named `core/config/gcs_bucket.py::_require_bucket`, a function
    that does not exist … both were accepted, counted, and took the kind to
    PASS at 100%

That repair reached Python and stopped.  On a Go or TypeScript file
`_names_in` returned `None`, the caller read `None` as "no verdict", and the
entry was accepted unchecked -- the same defect, in the 79 of 113 DeepSWE tasks
that are not Python.

**`None` means no verdict, never "no names".**  A language nobody has written
an extractor for returns `None` and the caller must leave the entry alone.
Returning an empty set instead would turn every reference into a false FAIL,
which is better than a false PASS and still wrong.

The three extractors are deliberately unequal, and the inequality is the point:

    Python   `ast`, because the interpreter running this ships one
    Go       Go's own parser through `gosource`, because a Go repo has a Go
             toolchain -- a comment or a string saying `func Foo()` cannot fool
             a parser, and `docs/EVIDENCE.md` §4 is what being fooled costs
    TS / JS  a regular expression over source with comments and strings
             stripped, because `node` exposes no parser and every JavaScript
             one is an npm package -- and this framework has no third-party
             dependency to spend

The TypeScript compromise carries a trip-wire rather than a promise: its bypass
fixtures put a declaration inside a comment and inside a string.  If a future
one gets through, the answer is a narrower expression, and if that fails, the
decision to take a parser dependency will have a failing fixture behind it
instead of an argument.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PYTHON_SUFFIXES = frozenset({".py", ".pyi"})
GO_SUFFIXES = frozenset({".go"})
TS_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})
RS_SUFFIXES = frozenset({".rs"})

#: `//` to end of line, `/* … */` across lines, and every string body.  Applied
#: before anything is matched, so a declaration written inside either is gone
#: before the declaration pattern ever sees the file.
_TS_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_TS_LINE_COMMENT = re.compile(r"//[^\n]*")
_TS_STRING = re.compile(r"""(?<!\\)(['"`])(?:\\.|(?!\1)[^\\])*\1""", re.S)

#: Only at the start of a line, allowing indentation and the modifiers a
#: declaration really can carry.  A `function` in the middle of a line is an
#: expression, not a binding this file is asked about.
_TS_DECL = re.compile(
    r"""^[ \t]*
        (?:export\s+(?:default\s+)?)?
        (?:declare\s+)?
        (?:abstract\s+)?
        (?:async\s+)?
        (?:function\s*\*?|class|interface|enum|type|const|let|var)
        \s+
        ([A-Za-z_$][\w$]*)
    """, re.M | re.X)


def _qualified_names(tree) -> set:
    """`Class.method`, for every method and nested class a class defines.

    `ast.walk` flattens, so the bare pass below binds `run` and never
    `DmIngestWorker.run` -- and the membership test that reads it is
    `name not in names`. The effect was backwards: `file.py::run` was accepted
    (and, walking a whole module, could mean any of several `run`s), while
    `file.py::DmIngestWorker.run` -- the reference with no ambiguity in it --
    was refused as a symbol that is not there. Measured on an adopter: a worker
    named a method twice, was told the file does not define it, and fell back to
    naming the class, which is one level coarser than what it meant.

    Only what a `class` body binds. A module-level `def` is already here under
    its bare name, and prefixing it would invent a spelling nobody writes.
    """
    out = set()

    def walk(node, prefix):
        for child in getattr(node, "body", []):
            if not isinstance(child, (ast.ClassDef, ast.FunctionDef,
                                      ast.AsyncFunctionDef)):
                continue
            dotted = f"{prefix}{child.name}"
            if prefix:
                out.add(dotted)
            walk(child, f"{dotted}.")

    walk(tree, "")
    return out


def owner_of_line(path, lines) -> dict | None:
    """`{line: symbol}` for the lines given.  `None` if we cannot read this
    language -- and `None` is not an empty map.

    The innermost `def` or `class` containing the line, which is the same thing
    a detector reports as a finding's `symbol`, so the two can be compared. A
    line inside a class but outside any of its methods belongs to the class; a
    line at module level belongs to nothing and is absent from the map.

    Python only, deliberately. Go and TypeScript have extractors here for
    *names*, not for spans, and a report that guessed at spans for them would
    be wrong in the direction that matters: it would call somebody else's code
    yours.
    """
    path = Path(path)
    if path.suffix not in PYTHON_SUFFIXES:
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return None
    spans = []                                # (start, end, name, depth)

    def walk(node, depth):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef,
                                  ast.AsyncFunctionDef)):
                spans.append((child.lineno, child.end_lineno or child.lineno,
                              child.name, depth))
                walk(child, depth + 1)

    walk(tree, 0)
    out = {}
    for line in lines:
        best = None
        for start, end, name, depth in spans:
            if start <= line <= end and (best is None or depth > best[0]):
                best = (depth, name)
        if best:
            out[line] = best[1]
    return out


def _python_names(path: Path) -> set | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return None
    out = _qualified_names(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            out.add(node.id)
        elif isinstance(node, ast.arg):
            out.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            # A re-exported name is in this file as far as anybody reading the
            # reference is concerned, and rejecting it would be this check
            # inventing a rule about where a symbol is allowed to be defined.
            for al in node.names:
                out.add(al.asname or al.name.split(".")[0])
    return out


def _ts_names(path: Path) -> set | None:
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    stripped = _TS_BLOCK_COMMENT.sub(" ", src)
    stripped = _TS_LINE_COMMENT.sub(" ", stripped)
    # Replaced with a quote pair rather than removed, so the line structure the
    # declaration pattern anchors on survives.
    stripped = _TS_STRING.sub('""', stripped)
    return set(_TS_DECL.findall(stripped))


#: Every name a Rust file binds at the top of a module or an `impl` block.
#: `fn`, `struct`, `enum`, `trait`, `type`, `const`, `static`, `mod`, and the
#: macros a `macro_rules!` defines -- a coverage entry naming any of them is
#: naming something a reader can go and find.
#:
#: `const` is in the keyword list and also in the modifier run, because Rust
#: writes it both ways: `const fn parse()` binds `parse` and `const LIMIT: usize`
#: binds `LIMIT`. With it only as a modifier, every associated constant in the
#: corpus read as no binding at all.
_RS_BINDS = re.compile(
    r"^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?(?:default[ \t]+)?"
    r"(?:async[ \t]+)?(?:unsafe[ \t]+)?(?:const[ \t]+)?"
    r"(?:fn|struct|enum|trait|type|static|union|mod|const)[ \t]+([A-Za-z_]\w*)",
    re.M)
_RS_MACRO = re.compile(r"^[ \t]*macro_rules!\s*([A-Za-z_]\w*)", re.M)


def _rs_names(path: Path) -> set | None:
    """What a Rust file defines, read off the mask.

    `RS_SUFFIXES` sat beside the other three suffix sets while `names_in` had
    no branch for it, so every `.rs` file answered `None` -- and `None` is the
    one answer this module's docstring says means "no verdict". Measured on a
    fresh Rust repo: `v4 cover --symbol src/parse.rs::no_such_function` was
    recorded and counted, while the same entry on a `.ts` file was refused.
    That is the defect the docstring above describes happening to Go and
    TypeScript, arriving a third time.

    A regex over the mask, for the reason `rssource` gives at length: there is
    no Rust parser here and every one of them is a crate. So this sees what a
    declaration keyword introduces and nothing else -- a name bound by a macro
    expansion is invisible, which is a miss in the direction of `None`'s own
    rule rather than against it: it under-reports what a file defines, and the
    caller's failure mode for that is refusing an entry that was in fact fine,
    not accepting one that names nothing.
    """
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    from . import rssource
    masked = rssource.rs_masked(src)
    return set(_RS_BINDS.findall(masked)) | set(_RS_MACRO.findall(masked))


def names_in(path) -> set | None:
    """Every name this file binds.  `None` if we cannot read this language.

    `None` is not "no names" -- it is "no verdict", and the caller must treat
    it as such. Adding a language means adding an extractor here, never
    guessing from a suffix this module does not know.
    """
    path = Path(path)
    suffix = path.suffix
    if suffix in PYTHON_SUFFIXES:
        return _python_names(path)
    if suffix in GO_SUFFIXES:
        from . import gosource
        return gosource.names_in(path)
    if suffix in TS_SUFFIXES:
        return _ts_names(path)
    if suffix in RS_SUFFIXES:
        return _rs_names(path)
    return None


#: An ES module import, with the names it binds. Both halves matter and to
#: different callers: `layers` needs only where the specifier points, and
#: `dangling_ref` needs the names to ask whether the target exports them.
#:
#: `export … from` is included because it is an import that re-publishes: a
#: barrel file crossing a layer crosses it whether or not it uses what it took.
_TS_IMPORT = re.compile(
    r"""^[ \t]*(?:import|export)\s*
        (?:
            (?P<names>\{[^}]*\})
          | (?:\*\s*as\s*(?P<star>[\w$]+))
          | (?P<default>[\w$]+)
        )?
        \s*(?:,\s*(?P<also>\{[^}]*\}))?
        \s*(?:from\s*)?
        ['"](?P<spec>[^'"]+)['"]
     """, re.M | re.X)

#: Every top-level binding a module publishes. `ts_defs` in `signature_change`
#: answers a narrower question -- exported *functions*, with their arity -- and
#: a dangling reference is about any name at all: a type, an interface, a
#: constant, a class. Two questions, two readers, one file.
_TS_EXPORT_DECL = re.compile(
    r"""^[ \t]*export\s+(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?
        (?:async\s+)?(?:function\s*\*?|class|interface|enum|type|const|let|var)
        \s+(?P<name>[A-Za-z_$][\w$]*)
     """, re.M | re.X)
#: `export { a }` and `export type { T }` both publish. The optional `type` is
#: not cosmetic: without it a module whose only publication of a name is
#: `export type { Session };` publishes nothing by that name, and every
#: importer of it is reported. Measured on an adopter: one such re-export in
#: a hook module made `dangling_ref` flag the component that imported it.
_TS_EXPORT_LIST = re.compile(r"^[ \t]*export\s*(?:type\s+)?\{(?P<names>[^}]*)\}", re.M)


def ts_imports(source: str):
    """`[(specifier, [bound names], line)]` for every ES import in `source`.

    Comments are stripped, for the reason every reader here strips them: an
    import inside a comment is not an import.

    Strings are *located* rather than stripped, and that is the difference
    between this reader and the others. A real import carries its specifier in
    a string, so blanking the strings before matching would take every
    specifier with it -- which is why this pass was
    `_TS_STRING.sub(lambda m: m.group(0), ...)`, an identity substitution that
    read as a strip and did nothing. So the strings are found instead, and an
    `import` that *begins* inside one is text rather than code: a template
    literal holding an example, a test building a module as a string. Its own
    specifier is inside the match, not around its start, so nothing real is
    lost. Measured before this: an import written inside a backtick template
    was yielded as a real import and reached `dangling_ref.ts_scan` and
    `layers.scan` as a crossing the file does not make.
    """
    masked = _TS_LINE_COMMENT.sub(" ", _TS_BLOCK_COMMENT.sub(" ", source))
    quoted = [m.span() for m in _TS_STRING.finditer(masked)]
    out = []
    for m in _TS_IMPORT.finditer(masked):
        if any(a <= m.start() < b for a, b in quoted):
            continue
        names = []
        for group in ("names", "also"):
            raw = m.group(group)
            if not raw:
                continue
            for piece in raw.strip("{}").split(","):
                piece = piece.strip()
                if not piece:
                    continue
                # `type X` inside a named list is TypeScript's inline type
                # modifier: the piece binds `X`, and there is no name `type`.
                # Measured on an adopter before this line existed: four pages
                # and two tests writing `import { useGate, type Tier } from
                # "../hooks/useGate"` were each reported as importing a symbol
                # called `type Tier`, which no module publishes -- six findings,
                # all false, on code `tsc --noEmit` accepts.
                piece = re.sub(r"^type\s+", "", piece)
                # `x as y` binds `y` and names `x` in the target.
                names.append(re.split(r"\s+as\s+", piece)[0].strip())
        if m.group("default"):
            names.append("default")
        line = masked.count("\n", 0, m.start()) + 1
        out.append((m.group("spec"), names, line))
    return out


def ts_exported_names(source: str) -> set:
    """Every name this module publishes, by any of the forms TypeScript has."""
    masked = _TS_LINE_COMMENT.sub(" ", _TS_BLOCK_COMMENT.sub(" ", source))
    out = {m.group("name") for m in _TS_EXPORT_DECL.finditer(masked)}
    for m in _TS_EXPORT_LIST.finditer(masked):
        for piece in m.group("names").split(","):
            piece = piece.strip()
            if not piece:
                continue
            # `export { a as b }` publishes `b`.
            out.add(re.split(r"\s+as\s+", piece)[-1].strip())
    if re.search(r"^[ \t]*export\s+default\b", masked, re.M):
        out.add("default")
    # `export * from './x'` republishes names this file cannot see, so a module
    # that has one publishes an unknown set. Saying so is the difference
    # between "this name is not here" and "this file does not know".
    if re.search(r"^[ \t]*export\s*\*", masked, re.M):
        return set()
    return out
