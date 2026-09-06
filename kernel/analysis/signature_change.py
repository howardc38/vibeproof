"""A required parameter added, and a call site left at the old arity.  SPEC.md §3.

Rejected once with a measurement: matching by name alone produced 248 findings
in the reference repo, every one a collision -- two unrelated functions called
`process`, and the checker had no way to know which one a call site meant.

The answer is not a better heuristic, it is resolution. `from mod import f`
and `import mod; mod.f(...)` both say which `f`, and the same-repo module
resolution built for `dangling-ref` already answers where `mod` lives. A call
site that cannot be resolved to a definition in this repo is somebody else's
function and not this rule's business.

Compared against the parent commit, because "added a required parameter" is a
statement about a change. A function that always had three required parameters
and is called with two is a different defect, and one the language reports.
"""

import ast
from pathlib import Path

from . import resolve


def required_params(fn) -> int:
    """Parameters a call has to supply.  `self`/`cls` excluded.

    A keyword-only parameter with no default is a required parameter: `def
    f(a, *, token)` cannot be called `f(1)`. This counted positional ones only,
    so the module titled "a required parameter added, and a call site left at
    the old arity" was blind to the one form that adds a required parameter
    *without* changing the positional arity -- which is the form a careful
    author reaches for, exactly because it does not disturb the positional
    order. Measured: `def f(a, *, token)` returned 1.
    """
    args = fn.args
    pos = args.posonlyargs + args.args
    if pos and pos[0].arg in ("self", "cls"):
        pos = pos[1:]
    positional = max(0, len(pos) - len(args.defaults))
    keyword_only = sum(1 for d in args.kw_defaults if d is None)
    return positional + keyword_only


def _required_kwonly(fn) -> set:
    """The names of the keyword-only parameters a call has to supply.

    Names, not a count, because the call site is compared by name. `required_params`
    counts them and that was enough while both sides counted; the call site
    added `len(node.args) + len(node.keywords)` -- a *total* -- so a
    keyword-only requirement was satisfied by any other keyword. Measured:
    `def f(a, *, verbose=False)` becoming `def f(a, *, token, verbose=False)`
    with a caller at `f(1, verbose=True)` reported nothing, and that call raises
    `TypeError: f() missing 1 required keyword-only argument: 'token'`.
    """
    return {a.arg for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults)
            if d is None}


def _defs(src: str):
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {}
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = required_params(node)
    return out


def _defs_kwonly(src: str):
    """`{name: {required keyword-only parameter names}}`, beside `_defs`."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {}
    return {node.name: _required_kwonly(node) for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _imported_here(tree, root: Path, target_rel: str):
    """Names in this file that refer to a function defined in `target_rel`.

    Both import forms, because both are how a call site says which function it
    means:  `from mod import f` binds `f`;  `import mod` binds `mod`, and
    `mod.f(...)` names it at the call.
    """
    # `direct` maps the name used here to the name defined there, because
    # `from mod import f as g` calls it `g` and the definition is still `f`.
    direct, modules = {}, set()
    want = Path(target_rel).with_suffix("")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            p = resolve.py_resolve(root, node.module)
            if p is not None and p.with_suffix("") == (root / want):
                for al in node.names:
                    direct[al.asname or al.name] = al.name
        elif isinstance(node, ast.Import):
            for al in node.names:
                p = resolve.py_resolve(root, al.name)
                if p is not None and p.with_suffix("") == (root / want):
                    modules.add(al.asname or al.name.split(".")[0])
    return direct, modules


def scan(root: Path, before_of, changed_files, subject=None):
    """[(caller_file, line, symbol, given, needs)] -- calls left at the old arity.

    `before_of(rel)` returns that file's content at the diff base, or None.
    """
    root = Path(root)
    tightened = {}                       # rel -> {name: (was, now)}
    for rel in changed_files:
        before = before_of(rel)
        if before is None:
            continue
        now_path = root / rel
        if not now_path.is_file():
            continue
        now_src = now_path.read_text(encoding="utf-8", errors="replace")
        was, now = _defs(before), _defs(now_src)
        was_kw, now_kw = _defs_kwonly(before), _defs_kwonly(now_src)
        # Two ways a signature can tighten, and only one of them moves the
        # count: a new required keyword-only parameter leaves the positional
        # arity alone, which is exactly why a careful author reaches for it.
        grew = {n: (was[n], now[n], now_kw.get(n, set()) - was_kw.get(n, set()))
                for n in now
                if n in was and (now[n] > was[n]
                                 or (now_kw.get(n, set())
                                     - was_kw.get(n, set())))}
        if grew:
            tightened[rel] = grew
    if not tightened:
        return []

    # `subject_files.tracked`, not a hand-rolled walk. Its docstring names
    # the cost of the copies: `test_shape` skipped nine directory names,
    # `bundle_secret` two, `secret_chain` walked `.venv` anyway and reported
    # a finding inside `jwt/jwks_client.py`. None of the copies honoured
    # `derive_exclude`, which is where the repo already says what is not the
    # code being judged, and a hand-written skip list says nothing about
    # `vendor/`, `target/` or `.tox/`. `git ls-files` needs no list at all:
    # a virtualenv is untracked.
    from .subject_files import tracked
    out = []
    for caller in sorted(root / f for f in tracked(subject or {}, root,
                                                   suffixes=(".py",))):
        try:
            tree = ast.parse(caller.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        try:
            crel = str(caller.relative_to(root))
        except ValueError:
            continue
        for target_rel, grew in tightened.items():
            direct, modules = _imported_here(tree, root, target_rel)
            if crel == target_rel:
                direct.update({n: n for n in grew})   # calls its own by name
            if not direct and not modules:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                name = None
                if isinstance(f, ast.Name) and f.id in direct:
                    name = direct[f.id]
                elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                        and f.value.id in modules:
                    name = f.attr
                if name not in grew:
                    continue
                was, now, new_kw = grew[name]
                if any(k.arg is None for k in node.keywords) or \
                        any(isinstance(a, ast.Starred) for a in node.args):
                    continue             # *args/**kwargs: arity is not knowable here
                given = len(node.args) + len(node.keywords)
                # By name for the keyword-only half. A total count let
                # `f(1, verbose=True)` satisfy a newly required `token`, and
                # that call raises. `required_params` was repaired to count
                # keyword-only on the definition side and the call site was
                # left counting the old way, so `grew` fired and the stale
                # caller stayed invisible.
                named = {k.arg for k in node.keywords}
                missing = sorted(new_kw - named)
                if missing:
                    out.append((crel, node.lineno, name, given, now))
                elif given < now:
                    out.append((crel, node.lineno, name, given, now))
    return out


# ── TypeScript and JavaScript ────────────────────────────────────────────────
#
# The same rule, and the same refusal to guess. Which file a specifier
# names is `resolve.ts_resolve`'s question, and the measurement behind it
# -- 6,948 specifiers, and what is not resolved -- is written there.
import re as _re


#: `export function f(a, b)` and `export const f = (a, b) =>`. Both forms,
#: because the corpus uses both and a rule that knew one would report a repo
#: written in the other as having no exported functions at all.
#:
#: These stop at the name. The parameter list is found by scanning, not by
#: regex, and that is not a preference: a generic list nests and it wraps.
#: `export function isMatching<const p extends Pattern<unknown>>(` has a `>`
#: inside it, so `<[^>]*>` ends in the wrong place, and valibot writes
#: `export async function expectActionIssueAsync<` with the parameters three
#: lines further down. Measured: 42 exported functions across the corpus were
#: invisible for those two reasons, all of them in the repos with the heaviest
#: type signatures -- exactly the code most likely to tighten an arity.
_TS_FN = _re.compile(
    r"""^[ \t]*export\s+(?:default\s+)?
        (?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)
     """, _re.M | _re.X)
_TS_ARROW = _re.compile(
    r"""^[ \t]*export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)
     """, _re.M | _re.X)


def _ts_skip_generic(mask: str, i: int) -> int:
    """Past a `<…>` type-parameter list at `i`, balancing the nesting."""
    n = len(mask)
    while i < n and mask[i].isspace():
        i += 1
    if i >= n or mask[i] != "<":
        return i
    depth = 0
    while i < n:
        if mask[i] == "<":
            depth += 1
        elif mask[i] == ">":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _ts_open_params(mask: str, i: int):
    """Index of the `(` opening a parameter list at or after `i`, or None."""
    i = _ts_skip_generic(mask, i)
    while i < len(mask) and mask[i].isspace():
        i += 1
    return i if i < len(mask) and mask[i] == "(" else None


def _ts_arrow_params(mask: str, i: int):
    """`(` of `= (a) =>`, `= async (a) =>` or `= function (a) {}`, or None.

    The type annotation between the name and the `=` is skipped by balancing
    rather than by matching up to the first `=`: `const f: Cmp<A = B> = (…)`
    puts an `=` inside the annotation, and stopping there reads the rest as a
    parameter list that is not one.
    """
    n, depth = len(mask), 0
    while i < n:
        c = mask[i]
        if c in "<([{":
            depth += 1
        elif c in ">)]}":
            depth -= 1
        elif c == "=" and depth <= 0 and mask[i:i + 2] != "=>":
            i += 1
            break
        elif c in ";\n" and depth <= 0:
            return None
        i += 1
    while i < n and mask[i].isspace():
        i += 1
    if mask.startswith("async", i):
        i += 5
        while i < n and mask[i].isspace():
            i += 1
    if mask.startswith("function", i):
        i += 8
        while i < n and (mask[i].isspace() or mask[i] == "*"):
            i += 1
        while i < n and (mask[i].isalnum() or mask[i] in "_$"):
            i += 1
    return _ts_open_params(mask, i)

_TS_IMPORT_NAMED = _re.compile(
    r"""import\s*\{([^}]*)\}\s*from\s*['"]([^'"]+)['"]""")
_TS_IMPORT_STAR = _re.compile(
    r"""import\s*\*\s*as\s+([\w$]+)\s*from\s*['"]([^'"]+)['"]""")


def ts_required_params(params: str) -> int:
    """How many arguments a caller must pass.

    Optional (`a?`), defaulted (`a = 1`) and rest (`...xs`) parameters are not
    required, and `this` is not a parameter at all -- TypeScript lets a
    signature declare its receiver, and counting it would report every method
    as needing one more argument than it does.
    """
    from .test_expectation import _ts_mask, _ts_arg_spans
    text = "(" + params + ")"
    mask = _ts_mask(text)
    n = 0
    for a, b in _ts_arg_spans(mask, 0):
        raw, m = text[a:b].strip(), mask[a:b].strip()
        if not raw or raw.startswith("..."):
            continue
        if _re.match(r"^[A-Za-z_$][\w$]*\s*\?", raw):
            continue
        # A default can sit either side of the annotation: `a = 1` and
        # `a: string = 'x'` are both optional to the caller, and looking only
        # before the colon saw the first and missed the second. Depth-scanned
        # because `a: Map<string, () => void> = new Map()` carries a `=>` and a
        # `>` that a split would read as the boundary.
        depth, defaulted = 0, False
        for i, c in enumerate(m):
            if c in "<([{":
                depth += 1
            elif c in ">)]}":
                depth -= 1
            elif c == "=" and depth == 0 and m[i:i + 2] != "=>" \
                    and (i == 0 or m[i - 1] not in "=!<>"):
                defaulted = True
                break
        if defaulted:
            continue
        if raw.split(":")[0].strip() == "this":
            continue
        n += 1
    return n


def ts_defs(src: str) -> dict:
    """{exported function name: required parameter count}."""
    from .test_expectation import _ts_mask, _ts_balanced
    mask = _ts_mask(src)
    out = {}
    for rx, find in ((_TS_FN, _ts_open_params), (_TS_ARROW, _ts_arrow_params)):
        for m in rx.finditer(mask):
            open_paren = find(mask, m.end())
            if open_paren is None:
                continue
            end = _ts_balanced(mask, open_paren, "(", ")")
            if end is None:
                continue
            out.setdefault(m.group(1),
                           ts_required_params(src[open_paren + 1:end - 1]))
    return out


def ts_scan(root: Path, before_of, changed_files, subject=None):
    """`scan`, for TypeScript.  Same tuple, same rule about growing arity."""
    from .subject_files import tracked, TS_SUFFIXES
    from .test_expectation import _ts_mask, _ts_arg_spans, _ts_balanced
    root = Path(root)
    tightened = {}
    for rel in changed_files:
        if not rel.endswith(TS_SUFFIXES):
            continue
        before = before_of(rel)
        if before is None or not (root / rel).is_file():
            continue
        was = ts_defs(before)
        now = ts_defs((root / rel).read_text(encoding="utf-8", errors="replace"))
        grew = {n: (was[n], now[n]) for n in now if n in was and now[n] > was[n]}
        if grew:
            tightened[rel] = grew
    if not tightened:
        return []

    out = []
    for caller in sorted(root / f for f in tracked(subject or {}, root,
                                                   suffixes=TS_SUFFIXES)):
        try:
            src = caller.read_text(encoding="utf-8", errors="replace")
            crel = str(caller.relative_to(root))
        except (OSError, ValueError):
            continue
        mask = _ts_mask(src)
        for target_rel, grew in tightened.items():
            direct, modules = {}, {}
            for m in _TS_IMPORT_NAMED.finditer(src):
                if resolve.ts_resolve(root, crel, m.group(2)) != target_rel:
                    continue
                for piece in m.group(1).split(","):
                    piece = piece.strip()
                    if not piece:
                        continue
                    bits = _re.split(r"\s+as\s+", piece)
                    origin = bits[0].strip()
                    local = bits[-1].strip()
                    direct[local] = origin
            for m in _TS_IMPORT_STAR.finditer(src):
                if resolve.ts_resolve(root, crel, m.group(2)) == target_rel:
                    modules[m.group(1)] = True
            if crel == target_rel:
                direct.update({n: n for n in grew})
            if not direct and not modules:
                continue
            for local, origin in list(direct.items()) + \
                    [(f"{mod}.{n}", n) for mod in modules for n in grew]:
                if origin not in grew:
                    continue
                pattern = _re.compile(
                    r"(?<![\w.$])" + _re.escape(local) + r"\s*(?:<[^>()]*>\s*)?\(")
                for hit in pattern.finditer(mask):
                    open_paren = hit.end() - 1
                    if _ts_balanced(mask, open_paren, "(", ")") is None:
                        continue
                    spans = _ts_arg_spans(mask, open_paren)
                    args = [src[a:b].strip() for a, b in spans]
                    args = [a for a in args if a]
                    if any(a.startswith("...") for a in args):
                        continue       # spread: the arity is not knowable here
                    was, now = grew[origin]
                    if len(args) < now:
                        line = src.count("\n", 0, open_paren) + 1
                        out.append((crel, line, origin, len(args), now))
    return out
