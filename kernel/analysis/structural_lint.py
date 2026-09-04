"""Structural violations, expressed as a delta against a base commit.

SPEC.md §9 lists `lint` as not built.  This is the analysis half: pure
functions over source text, no I/O, no argv, no exit codes (§10 step 1).

Why this is a delta and not an absolute count
---------------------------------------------
SPEC.md §4 gives a claim one answer and no severity.  A whole-repo lint
therefore fails every task that touches an already-dirty file, and the worker's
only exit is to repay debt that has nothing to do with the request.  Measured:
the tool this replaces reports 200 criticals on adopter_a, so on that repo
*every* task would fail on someone else's code.

So a violation is only reported when both hold:

  1. it lives in a file this task changed, and
  2. its key did not exist in that file at `diff_base`.

The base is read out of git, and that is what makes this half unwidenable: a
worker's cheapest fix for a failing gate is to edit the thing that judges it,
and `git show <base>:<path>` cannot be edited without rewriting a commit the
ledger already recorded.

This paragraph used to end "there is no baseline file, which is the point",
and argued that a committed one would sit "in a path nobody protected".  Both
halves are now wrong and the second was the reason for the first.
`checkers/structural_lint.py` does read `.v4/lint_baseline.json`, and `.v4/**`
*is* in `protected_paths` -- so adding an entry costs an
`ACCEPTED_RISK kind=scope_widen_protected` and a signed commit, which is the
review this argument asked for.  The two layers compose: git answers "is this
new in this file", the baseline answers "and has somebody already signed for
it".  Nothing here reads the baseline; that is the checker's half, said here
only so this file stops contradicting it.

Violation identity
------------------
    key = (rule, path, detail)

No line number, for SPEC.md §1's reason: a key holding a line number turns
every insertion above it into a fresh violation plus an orphan.  No enclosing
symbol either -- renaming or moving a function would do the same thing one
level up.  `symbol` and `line` are carried for display only.

The cost of dropping `symbol` is a real miss, and it is written down as an
executable fixture rather than a promise: `known_miss/second_site_same_file`.
A *second* function in the same file reaching for the *same* private is not a
new key, so it is not reported.

Rules
-----
Four, all decidable from the AST alone.  Rules that need a type -- "is this
parameter the executor module or an object that quacks like it" -- are absent
on purpose; see `docs/` note in the checker.

  LINT-PRIVATE-IMPORT         importing a `_name` from outside your package
  LINT-PRIVATE-MODULE-ACCESS  `mod._name` where `mod` is an imported module
                              from outside your package
  LINT-IMPORT-CYCLE           an import edge that lies on a module cycle
  LINT-CONFIG-DUAL-TRUTH      one setting with two owners -- a constant here and
                              a declaration in config, both read
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass

PRIVATE_IMPORT = "LINT-PRIVATE-IMPORT"
PRIVATE_MODULE_ACCESS = "LINT-PRIVATE-MODULE-ACCESS"
IMPORT_CYCLE = "LINT-IMPORT-CYCLE"
CONFIG_DUAL_TRUTH = "LINT-CONFIG-DUAL-TRUTH"

RULES = (PRIVATE_IMPORT, PRIVATE_MODULE_ACCESS, IMPORT_CYCLE, CONFIG_DUAL_TRUTH)

#: Where a repo keeps the values it has decided are settings.
CONFIG_PATH = ("config", "settings", "constants", "conf")

#: Numbers that carry no decision. `0`, `1`, `-1` are arithmetic; `100` and
#: `1000` are units; `0.5`, `0.25`, `2` are halves and doubles. A repo agreeing
#: with itself about any of these is not dual truth.
TRIVIAL = frozenset({0, 1, -1, 2, 10, 100, 1000,
                     0.0, 1.0, -1.0, 0.5, 0.25, 2.0, 10.0, 100.0, 1000.0})


@dataclass(frozen=True, order=True)
class Violation:
    rule: str
    path: str
    detail: str
    symbol: str = "<module>"
    line: int = 0

    @property
    def key(self) -> tuple:
        """Identity.  `symbol` and `line` are deliberately outside it."""
        return (self.rule, self.path, self.detail)

    @property
    def id(self) -> str:
        """The baseline key.  `finding_id` documents what is in it and why."""
        return finding_id(self.rule, self.path, self.detail)

    def id_at(self, path: str) -> str:
        """This finding's id had the file been at `path` -- rename lookup."""
        return finding_id(self.rule, path, self.detail)

    def render(self) -> str:
        where = f"{self.path}:{self.line}"
        if self.symbol and self.symbol != "<module>":
            where += f" in {self.symbol}()"
        return f"{self.rule}  {self.detail}\n      at {where}"


def finding_id(rule: str, path: str, detail: str) -> str:
    """`sha256(rule ‖ path ‖ detail)`, first 16 hex.

    Three things are *not* in it, each for a reason that has already cost
    somebody a day:

      line     SPEC.md §1.  A line number in an identity means adding an import
               at the top of a file invents a new violation for everything
               below it and orphans the entry that used to cover it.  A
               baseline made of line numbers rots on the first edit.

      symbol   the same failure one level up.  Renaming or moving the
               enclosing function would break the baseline entry, and renaming
               a function is not adding a structural violation.  The price is
               recorded as an executable gap, not a sentence: see
               `KnownMiss.test_second_site_for_the_same_private_in_the_same_file`.

      content  a hash of the surrounding code would break on every unrelated
               edit to the same file, which is the rot this is avoiding.

    `path` *is* in it, so the entry means "this file may reach for that
    private", not "anybody may".  That makes the file the weak point on a
    rename, which the checker answers with git's rename map.
    """
    raw = "\0".join((rule, path, detail)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------------------
# module naming
# ---------------------------------------------------------------------------


def module_of(rel_path: str) -> str | None:
    """`a/b/c.py` -> `a.b.c`; `a/b/__init__.py` -> `a.b`.  None if not Python."""
    if not rel_path.endswith(".py"):
        return None
    stem = rel_path[:-3].replace("\\", "/")
    parts = [p for p in stem.split("/") if p]
    if not parts:
        return None
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return None
    return ".".join(parts)


def build_index(rel_paths) -> dict[str, str]:
    """`{dotted module name: relative path}` for every Python file given."""
    index: dict[str, str] = {}
    for rel in sorted(rel_paths):
        dotted = module_of(rel)
        if dotted is not None:
            index.setdefault(dotted, rel)
    return index


def package_of(dotted: str, *, is_init: bool) -> str:
    """The package a module's private accesses are judged relative to.

    A package's `__init__` is judged as the package itself, not as its parent:
    assembling a public surface out of your own submodules is the boundary
    being implemented, not crossed.
    """
    if is_init:
        return dotted
    return dotted.rsplit(".", 1)[0] if "." in dotted else ""


def owner_package(target: str, index) -> str:
    """The directory that defines `target`.

    A package's `__init__` is owned by the package's own directory, so
    `pkg._x` and `pkg.common._x` -- two spellings of the same private when
    `__init__` re-exports it -- resolve to the same owner.  Judging those
    differently was the first version of this rule, and it is not a rule, it is
    a coin toss on how the author spelled the import.
    """
    path = index.get(target)
    is_init = bool(path) and path.endswith("__init__.py")
    return package_of(target, is_init=is_init)


def allowed(target: str, package: str, index, *, accessor_is_init: bool) -> bool:
    """May a file in `package` read a private defined by `target`?

    Two ways, and no others:

      same directory   the unit that owns a private is the directory, not the
                       file.  Splitting one module into four siblings must not
                       manufacture violations -- that shape produced 188 of the
                       200 findings the previous tool reported on adopter_a.
      surface assembly a package's own `__init__` pulling privates out of its
                       descendants is the boundary being implemented, not
                       crossed.

    Reaching *up* into a parent package is not on the list.  It reads like the
    same family, but `pkg/sub/x.py` importing `pkg.common._helper` is the
    identical dependency as any other cross-directory one, and exempting it
    while flagging `pkg/other/y.py` would make the rule depend on where the
    author happened to nest the file.
    """
    owner = owner_package(target, index)
    if owner == package:
        return True
    if accessor_is_init and package and owner.startswith(package + "."):
        return True
    return False


def _is_private(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


def _resolve_relative(package: str, level: int, module: str | None) -> str:
    """Turn `from ...x import y` into an absolute dotted module name."""
    parts = package.split(".") if package else []
    drop = level - 1
    base = parts[: len(parts) - drop] if drop <= len(parts) else []
    dotted = ".".join(base)
    if module:
        dotted = f"{dotted}.{module}" if dotted else module
    return dotted


def _enclosing(tree: ast.AST) -> dict[int, str]:
    """`{id(node): enclosing def/class name}` for display only."""
    owner: dict[int, str] = {}

    def walk(node, name):
        for child in ast.iter_child_nodes(node):
            child_name = name
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                child_name = child.name
            owner[id(child)] = child_name
            walk(child, child_name)

    walk(tree, "<module>")
    return owner


# ---------------------------------------------------------------------------
# per-file analysis
# ---------------------------------------------------------------------------


def module_bindings(tree: ast.AST, dotted: str, package: str, index) -> dict[str, str]:
    """Local names that are bound to a module in this repo.

    Only these can produce a LINT-PRIVATE-MODULE-ACCESS finding.  A bare
    parameter annotated `Any` and then used as `executor._as_list(...)` is not
    in here, and that is the intended limit: whether that object is the
    executor module or something that merely quacks like it is not decidable
    from this file, and guessing is how the previous tool reached 47 findings
    on receivers it had never seen bound.
    """
    bound: dict[str, str] = {}
    roots = {m.split(".")[0] for m in index}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    if alias.name in index:
                        bound[alias.asname] = alias.name
                elif alias.name.split(".")[0] in roots:
                    # `import a.b.c` binds `a`; the access we can see is
                    # `a.b.c._x`, reassembled by the chain walk below.
                    bound.setdefault(alias.name.split(".")[0], alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            source = (
                _resolve_relative(package, node.level, node.module)
                if node.level
                else (node.module or "")
            )
            for alias in node.names:
                if alias.name == "*":
                    continue
                full = f"{source}.{alias.name}" if source else alias.name
                if full in index:
                    bound[alias.asname or alias.name] = full
    bound.pop(dotted, None)
    return bound


def scan_source(rel_path: str, source: str, index) -> list[Violation]:
    """Rules 1 and 2 for one file.  Raises `SyntaxError` if it will not parse."""
    dotted = module_of(rel_path)
    if dotted is None:
        return []
    tree = ast.parse(source)
    is_init = rel_path.endswith("__init__.py")
    package = package_of(dotted, is_init=is_init)
    owner = _enclosing(tree)
    bound = module_bindings(tree, dotted, package, index)
    found: list[Violation] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            found.extend(
                _import_violations(node, rel_path, package, owner, index,
                                   accessor_is_init=is_init))
        elif isinstance(node, ast.Attribute) and _is_private(node.attr):
            target = _chain_module(node.value, bound, index)
            if target is None or allowed(target, package, index,
                                         accessor_is_init=is_init):
                continue
            found.append(
                Violation(
                    rule=PRIVATE_MODULE_ACCESS,
                    path=rel_path,
                    detail=f"{target}.{node.attr}",
                    symbol=owner.get(id(node), "<module>"),
                    line=node.lineno,
                )
            )
    return sorted(set(found))


def _chain_module(node: ast.AST, bound, index) -> str | None:
    """Resolve the receiver of `X._priv` to a repo module, or None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    parts.reverse()
    head = bound.get(parts[0])
    if head is None:
        return None
    dotted = ".".join([head] + parts[1:])
    return dotted if dotted in index else (head if not parts[1:] else None)


def _import_violations(node, rel_path, package, owner, index, *, accessor_is_init):
    out = []
    if isinstance(node, ast.ImportFrom):
        source = (
            _resolve_relative(package, node.level, node.module)
            if node.level
            else (node.module or "")
        )
        if not source or not _known_prefix(source, index):
            return out
        for alias in node.names:
            if alias.name == "*" or not _is_private(alias.name):
                continue
            full = f"{source}.{alias.name}"
            defining = full if full in index else source
            if allowed(defining, package, index, accessor_is_init=accessor_is_init):
                continue
            out.append(
                Violation(
                    rule=PRIVATE_IMPORT,
                    path=rel_path,
                    detail=full,
                    symbol=owner.get(id(node), "<module>"),
                    line=node.lineno,
                )
            )
    else:
        for alias in node.names:
            if alias.name not in index:
                continue
            leaf = alias.name.rsplit(".", 1)[-1]
            if not _is_private(leaf) or allowed(alias.name, package, index,
                                                accessor_is_init=accessor_is_init):
                continue
            out.append(
                Violation(
                    rule=PRIVATE_IMPORT,
                    path=rel_path,
                    detail=alias.name,
                    symbol=owner.get(id(node), "<module>"),
                    line=node.lineno,
                )
            )
    return out


def _known_prefix(source: str, index) -> bool:
    return any(m == source or m.startswith(source + ".") for m in index)


# ---------------------------------------------------------------------------
# rule 3: import cycles
# ---------------------------------------------------------------------------


def import_edges(rel_path: str, source: str, index) -> dict[str, int]:
    """`{imported module: first line}` for repo-internal imports of one file."""
    dotted = module_of(rel_path)
    if dotted is None:
        return {}
    tree = ast.parse(source)
    package = package_of(dotted, is_init=rel_path.endswith("__init__.py"))
    edges: dict[str, int] = {}

    def add(name, line):
        target = _nearest_module(name, index)
        if target and target != dotted:
            edges.setdefault(target, line)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.name, node.lineno)
        elif isinstance(node, ast.ImportFrom):
            src = (
                _resolve_relative(package, node.level, node.module)
                if node.level
                else (node.module or "")
            )
            if not src:
                continue
            for alias in node.names:
                add(f"{src}.{alias.name}" if alias.name != "*" else src, node.lineno)
            add(src, node.lineno)
    return edges


def _nearest_module(name: str, index) -> str | None:
    while name:
        if name in index:
            return name
        if "." not in name:
            return None
        name = name.rsplit(".", 1)[0]
    return None


def _related(a: str, b: str) -> bool:
    """Parent/child in the package tree -- never counted as a cycle.

    `pkg/__init__.py` importing `pkg.sub` while `pkg.sub` imports `pkg` is how
    Python packages are assembled, not a dependency loop.
    """
    return a == b or a.startswith(b + ".") or b.startswith(a + ".")


def cycle_violations(graph: dict[str, dict[str, int]], index) -> list[Violation]:
    """One violation per edge that lies on a cycle.

    Keyed by the *source* module's file, so the finding lands on the file whose
    import created it rather than on every module the loop happens to pass
    through.
    """
    pruned = {
        src: {dst: line for dst, line in dsts.items() if not _related(src, dst)}
        for src, dsts in graph.items()
    }
    found: list[Violation] = []
    for component in _strong_components(pruned):
        if len(component) < 2:
            continue
        members = set(component)
        for src in sorted(members):
            for dst in sorted(pruned.get(src, {})):
                if dst not in members:
                    continue
                found.append(
                    Violation(
                        rule=IMPORT_CYCLE,
                        path=index[src],
                        detail=f"-> {dst}",
                        symbol="<module>",
                        line=pruned[src][dst],
                    )
                )
    return sorted(set(found))


def _strong_components(graph):
    """Tarjan, iterative.  Recursion depth is a repo-size limit otherwise."""
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    counter = 0
    out = []

    for root in sorted(graph):
        if root in index_of:
            continue
        work = [(root, iter(sorted(graph.get(root, {}))))]
        index_of[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index_of:
                    index_of[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(graph.get(child, {})))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index_of[child])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index_of[node]:
                component = []
                while True:
                    popped = stack.pop()
                    on_stack.discard(popped)
                    component.append(popped)
                    if popped == node:
                        break
                out.append(sorted(component))
    return out


# ---------------------------------------------------------------------------
# whole-tree analysis and the delta
# ---------------------------------------------------------------------------


def _is_config(rel_path: str) -> bool:
    """Is this file where a repo states its values?

    A test is never one, whatever it is called. `CONFIG_PATH` matches on the
    substring `conf`, so `tests/conftest.py` was read as a source of config
    bindings -- the mirror of the omission in `_is_test` beneath it, and the
    same sentence twice: a fixture stating a value is what a fixture is for.

    That sentence was general and the guard was not. `tests/conftest.py` was
    excluded because it sits under a directory named `tests`, and a repo-root
    `conftest.py` -- pytest's own standard position for one -- was not:
    measured, `subject_files.is_test("conftest.py")` answered False, so the
    `conf` substring matched and the file's fixture values became the bindings
    every module in the repo was judged as restating. Fixed in the owner rather
    than here, because the same file was also outside `_is_test` for `layers`,
    `test_token_shape` and the skip at the bottom of this module.
    """
    if _is_test(rel_path):
        return False
    parts = rel_path.replace("\\", "/").lower().split("/")
    stem = parts[-1].rsplit(".", 1)[0]
    return any(h in stem for h in CONFIG_PATH) or \
        any(h in p for p in parts[:-1] for h in CONFIG_PATH)


def _is_test(rel_path: str, source: str = None) -> bool:
    """`subject_files.is_test`.  This one held a smaller set than the others.

    It accepted `tests/`, `/tests/` and a basename starting `test_`, and left
    out `*_test.py` -- which is in pytest's default `python_files` beside
    `test_*.py` and is the only convention a Go-style layout uses. In such a
    repo every threshold a fixture states was judged as logic and could be
    reported as `LINT-CONFIG-DUAL-TRUTH`.
    """
    from .subject_files import is_test
    return is_test(rel_path, source)


def _restated(tree: ast.AST, bound: dict):
    """`(value, line, name)` where a config-bound value is written next to its name.

    Not "the file mentions the name somewhere". That version reported
    `rel_path[:-3]` in a module whose *comment* happened to mention
    `ship_rederive_max` -- the name check was file-wide, so one mention anywhere
    made every occurrence of `3` a finding. A false positive produced by the
    very comment explaining the rule.

    So the two have to be adjacent, in one of the four shapes a duplicated
    default actually takes: a `.get(name, value)` fallback, a keyword argument,
    a dict entry, or a parameter default.
    """
    out = []

    def take(name, node):
        if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
            return
        if not isinstance(node.value, (int, float)) or node.value not in bound:
            return
        # Case-insensitive: a config constant is `RETRY_BACKOFF_SECONDS` and
        # the parameter that duplicates it is `retry_backoff_seconds`. That is
        # one name in Python, and comparing exactly missed every instance of
        # the shape this rule is actually about.
        if bound[node.value][0].lower() != str(name).lower():
            return
        out.append((node.value, node.lineno, name))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "get" \
                    and len(node.args) == 2 \
                    and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                take(node.args[0].value, node.args[1])
            for kw in node.keywords:
                if kw.arg:
                    take(kw.arg, kw.value)
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    take(k.value, v)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            # `posonlyargs + args`, because `args.defaults` covers both and
            # this read `args.args` alone. In `def f(a, /, b=1)` that list is
            # `["b"]` and the slice took the last one of it -- right by luck.
            # In `def f(a=1, /, *, b)` it is empty, so the default `1` was
            # paired with nothing; and in `def f(a, b, /, c=2)` with two
            # defaults it would pair `c`'s default with `c` and the other with
            # a name from the wrong list. The language puts the defaults on the
            # tail of the combined list, so that is the list to slice.
            positional = args.posonlyargs + args.args
            names = [a.arg for a in positional][-len(args.defaults):] \
                if args.defaults else []
            for a, d in zip(names, args.defaults):
                take(a, d)
            for a, d in zip(args.kwonlyargs, args.kw_defaults):
                if d is not None:
                    take(a.arg, d)
    return out


def dual_truth(sources: dict[str, str]) -> list[Violation]:
    """A number decided in a config file and decided again in logic.

    Lands R-CONFIG-PLACEMENT's **critical** half, from the predecessor's
    `KIT_PYTHON_DEVX_LINT/snowball-lint.py:171`. Two rules shipped under that
    one name and they are not the same quality:

      warning   any non-trivial float in a logic file, "should come from
                config". Measured on the reference repo: 49 hits. A criterion
                that wide spends a checker's credibility on false positives,
                which is the failure this project keeps deleting rules for.
                **Not implemented, deliberately.**

      critical  the same value written in a config file *and* in logic.
                Measured: 13 hits. Not a heuristic -- two places hold one
                decision, and the day they disagree nobody can say which was
                meant. This is that half.

    Two doctrine rules point straight at it and had no mechanism until now:
    「不要寫死一個系統推導得到的值」 and 「如果改一個值需要改變行為或改一條
    測試,它是程式碼,不是設定」. SPEC §9 said this class has no detector; a
    stdlib AST implementation sitting in the predecessor's repo disproves the
    critical half of that.
    """
    # `{value: (name, file, line)}` -- the *name* matters. Matching on value
    # alone was the first implementation and it reported four findings on this
    # repo, all four false: a similarity threshold of 0.8 in `dep_provenance`
    # against `dup_threshold: 0.8` in the config, a retry count of 3 against
    # `ship_rederive_max: 3`, and two more of the same shape. Small integers and
    # common ratios collide constantly. Same number is not same decision.
    bound: dict = {}
    for rel in sorted(sources):
        if not _is_config(rel):
            continue
        try:
            tree = ast.parse(sources[rel])
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            v = node.value
            if not isinstance(v, ast.Constant) or isinstance(v.value, bool):
                continue
            if not isinstance(v.value, (int, float)) or v.value in TRIVIAL:
                continue
            for t in node.targets:
                if isinstance(t, ast.Name):
                    bound.setdefault(v.value, (t.id, rel, node.lineno))
                elif isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) \
                        and isinstance(t.slice.value, str):
                    bound.setdefault(v.value, (t.slice.value, rel, node.lineno))
        # `{"min_chars": 40}` in a config file binds a name too.
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and isinstance(k.value, str) \
                            and isinstance(v, ast.Constant) \
                            and not isinstance(v.value, bool) \
                            and isinstance(v.value, (int, float)) \
                            and v.value not in TRIVIAL:
                        bound.setdefault(v.value, (k.value, rel, v.lineno))

    out = []
    for rel in sorted(sources):
        # A config file restating its own value is the config. A test stating a
        # value is what a fixture is for.
        if _is_config(rel) or _is_test(rel):
            continue
        try:
            tree = ast.parse(sources[rel])
        except SyntaxError:
            continue
        where = _enclosing(tree)
        for value, line, name in _restated(tree, bound):
            cfg_name, cfg_rel, cfg_line = bound[value]
            out.append(Violation(
                CONFIG_DUAL_TRUTH, rel,
                f"{value!r} is bound as {cfg_name!r} in {cfg_rel}:{cfg_line}, "
                f"and written again beside {name!r} here. Two places hold one "
                f"decision; the day they disagree nothing says which was meant.",
                symbol=where.get(line, "<module>"), line=line))
    return sorted(out)


def analyse(sources: dict[str, str], *, only=None, cycles=True):
    """`(violations, unparsed)` for a tree.

    `sources` is `{relative path: text}` for every Python file -- the whole
    tree, even when `only` narrows the report, because a module cannot be told
    from an arbitrary object without the index, and a cycle is not a property
    of one file.

    A file that will not parse is *returned*, not raised.  The checker has to
    be able to report the findings it did establish and mention the file it
    could not read in the same breath; raising would make one unreadable file
    erase every real finding beside it.
    """
    index = build_index(sources)
    found: list[Violation] = []
    unparsed: list[tuple[str, str]] = []
    graph: dict[str, dict[str, int]] = {}

    for rel in sorted(sources):
        dotted = module_of(rel)
        if dotted is None:
            continue
        wanted = only is None or rel in only
        try:
            if wanted:
                found.extend(scan_source(rel, sources[rel], index))
            if cycles:
                graph[dotted] = import_edges(rel, sources[rel], index)
        except SyntaxError as exc:
            if wanted:
                unparsed.append((rel, f"line {exc.lineno}: {exc.msg}"))
            graph[dotted] = {}

    if cycles:
        for violation in cycle_violations(graph, index):
            if only is None or violation.path in only:
                found.append(violation)

    # Repo-wide like a cycle, and for the same reason: one file cannot tell you
    # a value is written twice.
    for violation in dual_truth(sources):
        if only is None or violation.path in only:
            found.append(violation)

    # One violation per key.  Two call sites for the same private in the same
    # file share an id, so leaving both in would put a duplicate entry in the
    # baseline and count one debt twice in every report.  Sorted first, so the
    # survivor is deterministic rather than whichever the walk reached first.
    unique: dict[tuple, Violation] = {}
    for violation in sorted(set(found)):
        unique.setdefault(violation.key, violation)
    return sorted(unique.values()), sorted(set(unparsed))


def partition(violations, baseline_ids, *, renames=None):
    """Split findings into `(new, carried, stale)` against a baseline id set.

    Three values, because the other two answers to this question already give
    three: `kernel/baseline.partition(findings, accepted, key)` and
    `kernel/analysis/dependency_audit.split_by_baseline(findings, accepted)`.
    One name with two arities is how a reader who learned it from
    `checkers/test_shape.py` unpacks two here and loses `stale` silently --
    which `kernel/baseline.py` opens by saying this shape "was reinvented three
    times anyway". The format reader was consolidated and the split was not.

    `renames` maps a current path to the path the same file had at the base
    commit.  A renamed file is looked up under both, so moving a file is not a
    way to manufacture violations -- and, in the other direction, `git mv` is
    not a way to *lose* an entry either, because the entry still matches.

    A baseline id that matches nothing is simply unused.  It never fails
    anything: failing a task because somebody *repaired* a violation is the
    fastest way to teach people to route around the gate.
    """
    renames = renames or {}
    new, carried, seen = [], [], set()
    for violation in violations:
        ids = {violation.id}
        if violation.path in renames:
            ids.add(violation.id_at(renames[violation.path]))
        seen |= ids
        (carried if ids & baseline_ids else new).append(violation)
    return sorted(new), sorted(carried), sorted(set(baseline_ids) - seen)
