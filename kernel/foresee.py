"""Who else names the things you are about to change.  PL-6.

Every `scope widen` measured on one adopter had the same cause: something inside
the proposed scope is named by hand in a file outside it. A test that pins a
digest byte-for-byte. Seven test modules each listing the active flow set. Two
`worker_dispatch` files spelling out a handler name. The worker discovers this
after the checker says the diff went out of bounds, which is four hours later
than the moment it could have been said.

So this is asked before the cut, not after: given a proposed scope, which files
outside it mention the names defined inside it.

Deliberately not `re-split`. `re-split` is "the cut was wrong, how do we get
out"; SPEC marks it unresolved and gives it a deadline this project has already
passed. This is "do not cut wrong", it costs one command, and the evidence says
most widens were foreseeable. If widens continue after this exists, that is when
`re-split` has a case -- and it will have a number behind it rather than a
worry.

Zero LLM and zero ledger: an AST for the names, a substring scan for the
mentions. It can be run on a proposed scope that no task has opened yet, which
is the only time it is useful.
"""

from __future__ import annotations

import ast
import fnmatch
from pathlib import Path


def _matches(rel: str, globs) -> bool:
    return any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(rel, g.rstrip("/") + "/*")
               for g in globs)


def names_defined(path: Path) -> set:
    """Top-level names a Python file binds, plus class attributes.

    Only names something else could plausibly spell out. Locals cannot be
    referenced from another file, and `__dunder__` is the language's.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("__"):
                out.add(node.name)
        elif isinstance(node, ast.Assign):
            out |= {t.id for t in node.targets
                    if isinstance(t, ast.Name) and not t.id.startswith("__")}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if not node.target.id.startswith("__"):
                out.add(node.target.id)
    return out


#: A name short enough to appear by accident tells you nothing. Measured on the
#: adopter that motivated this: at three characters the report was 90% `id`,
#: `db` and `ok`; at five it was the handler and flow names that actually caused
#: the widens.
MIN_NAME = 5


def foresee(root, scope_globs, *, exclude=(), min_name=MIN_NAME) -> dict:
    """`{name: [(path, line), ...]}` for names defined in scope and used out of it.

    Files, not modules: a widen is granted per path, so the answer has to be
    per path too. Text search rather than import graph on purpose -- the cases
    that caused every measured widen were a string in a list and a name in a
    docstring, neither of which is an import.
    """
    root = Path(root)
    inside, outside = [], []
    for p in root.rglob("*.py"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root))
        if "/.git/" in f"/{rel}" or _matches(rel, exclude):
            continue
        (inside if _matches(rel, scope_globs) else outside).append((rel, p))

    defined = {}
    for rel, p in inside:
        for name in names_defined(p):
            if len(name) >= min_name:
                defined.setdefault(name, rel)
    if not defined:
        return {}

    # A name the rest of the repo also defines is not a coupling to this cut.
    # `repo_root` and `as_dict` are declared in twenty files; renaming yours
    # breaks none of them, and reporting them buries the one that matters.
    # Measured: on `kernel/analysis/**` this drops 207 names to the handful
    # whose definition really is only here.
    elsewhere = set()
    for _, p in outside:
        elsewhere |= names_defined(p)
    defined = {k: v for k, v in defined.items() if k not in elsewhere}
    if not defined:
        return {}

    hits = {}
    for rel, p in outside:
        for name, line in spelled_out(p, defined):
            hits.setdefault(name, []).append((rel, line))
    return {k: v for k, v in sorted(hits.items())}


def spelled_out(path: Path, names) -> list:
    """`[(name, line)]` where this file writes one of `names` **by hand**.

    An import is not the thing being looked for. `from x import Handler` is a
    dependency the language checks and a rename updates; it breaks loudly and
    it never caused a widen. What caused every measured one was a name written
    as text -- a flow id in a list, a handler name in a registry table, a digest
    pinned byte-for-byte in a test -- which no rename touches and no import
    graph sees.

    So: string constants only, plus attribute access on something this file
    never imported. Both are hand-maintained references to a name somebody else
    owns.
    """
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except (OSError, SyntaxError):
        return []
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported |= {a.asname or a.name for a in node.names}
        elif isinstance(node, ast.Import):
            imported |= {(a.asname or a.name).split(".")[0] for a in node.names}

    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for name in names:
                # A whole word inside the string, not a coincidental substring.
                if name in node.value.split() or node.value.strip() == name:
                    out.append((name, node.lineno))
        elif isinstance(node, ast.Attribute) and node.attr in names:
            if node.attr not in imported:
                out.append((node.attr, node.lineno))
    seen, uniq = set(), []
    for name, line in out:
        if name not in seen:
            seen.add(name)
            uniq.append((name, line))
    return uniq


def render(root, scope_globs, hits) -> str:
    if not hits:
        return ("nothing outside this scope names anything defined inside it.\n"
                "That is the answer this asks for; it is not a promise that the "
                "cut is right, only that no file spells out a name you are about "
                "to change.")
    lines = [f"{len(hits)} name(s) defined inside this scope are spelled out "
             f"in {len({p for v in hits.values() for p, _ in v})} file(s) outside it.",
             "",
             "Each one is a `scope widen` you can expect, or a cut line you can "
             "move now instead:",
             ""]
    for name, where in hits.items():
        lines.append(f"  {name}")
        for path, ln in where[:6]:
            lines.append(f"      {path}:{ln}")
        if len(where) > 6:
            lines.append(f"      … {len(where) - 6} more")
    return "\n".join(lines)
