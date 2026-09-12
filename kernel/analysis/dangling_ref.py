"""Imports naming something that is not there.  SPEC.md §3.

Verified in the reference repo before this was written:
`runtime/f166_step5_card_generation.py:126` does
`from core.domain.models import ReferenceRunRow`, and `models.py` mentions
that name once -- in a comment. The class does not exist.

Nothing here catches it. The module is imported lazily inside a function, so it
never runs at import time; the test suite never reaches that path; and a
symbol-level rule is exactly the kind `dead-wiring` was built for and does not
do -- it asks about ledger tables, config thresholds and claim-kind field names,
never about an arbitrary name.

Resolution is AST-only and same-repo-only. A name that resolves outside this
repo is somebody else's package and not this rule's business.
"""

import ast
import re
from pathlib import Path

from . import pysource, resolve

#: A Go build constraint that no ordinary build satisfies.
#:
#: Measured before this was written, in a three-file module: deleting
#: `client.Removed` and calling it from `main.go` gives
#: `./main.go:7:13: undefined: client.Removed` and the build stops; importing a
#: package that is not there gives `no required module provides package …`. The
#: Go compiler is this rule's checker for every file it compiles, and a second
#: implementation of it in Python would be a worse compiler.
#:
#: What the compiler never reads is a file it was told to skip. The same call,
#: in `tools/gen.go` under `//go:build ignore`, leaves `go build ./...` and
#: `go vet ./...` both silent and exit 0. That is the whole Go half of this
#: rule, and it is exactly the shape the Python half was written for: a
#: reference on a path nothing executes, found when somebody finally runs it.
#:
#: Only `ignore`, and deliberately. Whether `//go:build linux` is in the build
#: depends on GOOS, GOARCH and `-tags`, and a checker that guessed would report
#: a file that compiles fine on the machine it was written for. `ignore` needs
#: no guess: it is the conventional never-built tag, and a repo that runs
#: `go build -tags ignore` has left this rule's stated ground.
_GO_IGNORED = re.compile(r"^\s*(?://go:build\s+ignore\s*$|//\s*\+build\s+ignore\s*$)")


def _binds(node, names: set) -> None:
    """Add every module-level name this statement binds.

    One function, because the module body and the `if` branches below it both
    ask it and had two answers.
    """
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        names.add(node.name)
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            names.update(n.id for n in ast.walk(t)
                         if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        names.add(node.target.id)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for al in node.names:
            names.add(al.asname or al.name.split(".")[0])


def _defined(path: Path):
    """Top-level names a module binds: classes, functions, assignments, imports."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return None
    names = set()
    for node in tree.body:
        _binds(node, names)
        if isinstance(node, ast.If):
            # `if TYPE_CHECKING:` and platform branches bind names too -- and
            # the copy that used to live here answered a smaller question than
            # the one above it: classes, functions and plain assignments, but
            # not imports and not annotated assignments. An import is the
            # commonest thing inside `if TYPE_CHECKING:`; it is what the block
            # is *for*. So a module importing `Bar` there, and a document or
            # another module naming `mod.Bar`, got "the code no longer has it".
            for sub in ast.walk(node):
                _binds(sub, names)
    for node in tree.body:
        # `__all__` is a module saying what it exports. A name listed there is
        # bound one way or another, and second-guessing that is this checker
        # inventing a rule the language does not have.
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                names |= {e.value for e in node.value.elts
                          if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        # PEP 562: a module with a top-level `__getattr__` binds names at access
        # time, so nothing static can say which. `core/flow_engine/__init__.py`
        # does exactly this and produced 188 findings on its own.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == "__getattr__":
            return None
    return names


def scan(root: Path, files=None, subject=None):
    """[(file, line, module, name)] -- imports of a name the module does not bind."""
    root = Path(root)
    if files:
        paths = [root / f for f in files if str(f).endswith(".py")]
    else:
        # `subject_files.tracked`, not a hand-rolled walk. Its docstring names
        # the cost of the copies: `test_shape` skipped nine directory names,
        # `bundle_secret` two, `secret_chain` walked `.venv` anyway and reported
        # a finding inside `jwt/jwks_client.py`. None of the copies honoured
        # `derive_exclude`, which is where the repo already says what is not the
        # code being judged, and a hand-written skip list says nothing about
        # `vendor/`, `target/` or `.tox/`. `git ls-files` needs no list at all:
        # a virtualenv is untracked.
        from .subject_files import tracked
        paths = [root / f for f in tracked(subject or {}, root, suffixes=(".py",))]

    out = []
    for p in sorted(set(paths)):
        if not p.is_file():
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = pysource.import_from_module(node, p.relative_to(root))
            if not module:
                continue
            target = resolve.py_resolve(root, module)
            if target is None:
                continue          # not ours
            if target.is_dir():
                # A namespace package binds exactly its children. Nothing else
                # can be imported from it, and nothing else can be missing.
                for al in node.names:
                    if al.name == "*" or resolve.py_submodule(target, al.name) is not None:
                        continue
                    try:
                        rel = str(p.relative_to(root))
                    except ValueError:
                        rel = str(p)
                    out.append((rel, node.lineno, module, al.name))
                continue
            defined = _defined(target)
            if defined is None:
                continue
            # A module that re-exports with a star cannot be judged this way.
            if "*" in {al.name for al in node.names}:
                continue
            src = target.read_text(encoding="utf-8", errors="replace")
            if "import *" in src:
                continue
            for al in node.names:
                if al.name in defined:
                    continue
                # `from pkg import submodule` binds a module, not a name in
                # `__init__.py`. Without this the reference repo reported 417
                # findings, every one of them a package importing its own
                # children -- a green failure large enough to make the checker
                # unusable, which is what the adversarial pass keeps catching in
                # rules of this shape.
                if resolve.py_submodule(target, al.name) is not None:
                    continue
                try:
                    rel = str(p.relative_to(root))
                except ValueError:
                    rel = str(p)
                out.append((rel, node.lineno, module, al.name))
    return out


def go_ignored(source: str) -> bool:
    """Is this file excluded from every ordinary Go build.

    The constraint is a comment, and the emitter parses without comments on
    purpose -- a comment naming a semaphore is not a bound. So this reads the
    text, and only the part of it the language says a constraint may live in:
    before the `package` clause.
    """
    for raw in source.splitlines():
        line = raw.strip()
        if line.startswith("package "):
            return False
        if _GO_IGNORED.match(raw):
            return True
    return False


def _go_package_names(pkg_dir: Path):
    """(package name, {top-level names}) for a Go package directory, or None.

    `_test.go` files are left out: they are not compiled into the package
    another file can import, so a name only they define is not a name an
    import resolves to.
    """
    from . import gosource, symbols
    files = sorted(f for f in pkg_dir.glob("*.go")
                   if not f.name.endswith("_test.go"))
    if not files:
        return None
    names, pkg = set(), ""
    for f in files:
        shape = gosource.shape(f)
        if shape is None:
            return None           # no verdict, never "no names"
        pkg = pkg or (shape.get("package") or "")
        found = symbols.names_in(f)
        if found is None:
            return None
        names |= found
    return pkg, names


def go_scan(root: Path, files=None, subject=None):
    """`(findings, unread)` -- references the compiler never reads, and what could not be read.

    Only files under a never-satisfied build constraint, and only names
    qualified by an import of this module's own packages. Everything else is
    either the compiler's answer or somebody else's package.

    **Two values, because one of them was being thrown away.** `gosource.shape`
    returns `None` for every failure it can have -- the helper would not build,
    the build timed out, `go` is not installed -- and this loop answered all of
    them with `continue`. So a run where nothing could be read returned `[]`,
    the checker printed "every same-repo reference resolves" and exited 0, and
    a Go file with a dangling reference in it was reported as clean.

    Measured 2026-08-27: `tests/test_a_name_in_a_language_this_can_read.py`
    unlinks every `v4-go-*` binary in the system temp directory and rebuilds
    only the one it needs, so every later caller pays a cold `go build` -- and
    under a loaded `v4 accept` that build is what times out. The trigger was
    the test; what made it silent was here.

    `_go_package_names` two functions up already keeps this apart -- "no
    verdict, never 'no names'" -- so this is the same rule reaching the caller
    that could act on it.
    """
    from . import layers
    root = Path(root)
    unread = []
    prefix = layers.go_module_prefix(root)
    if not prefix:
        return [], unread         # no go.mod: no import is ours to resolve
    if files:
        paths = [root / f for f in files if str(f).endswith(".go")]
    else:
        from .subject_files import tracked
        paths = [root / f for f in tracked(subject or {}, root, suffixes=(".go",))]

    from . import gosource
    packages = {}
    out = []
    for p in sorted(set(paths)):
        if not p.is_file():
            continue
        source = p.read_text(encoding="utf-8", errors="replace")
        if not go_ignored(source):
            continue              # the compiler read this one
        shape = gosource.shape(p)
        if shape is None:
            unread.append(str(p.relative_to(root) if p.is_relative_to(root) else p))
            continue
        # alias -> the names that package defines.
        here = {}
        for imp in shape.get("imports") or []:
            path = imp.get("name") or ""
            if path != prefix and not path.startswith(prefix + "/"):
                continue
            rel = path[len(prefix):].lstrip("/")
            pkg_dir = root.joinpath(*rel.split("/")) if rel else root
            if pkg_dir not in packages:
                packages[pkg_dir] = _go_package_names(pkg_dir)
            found = packages[pkg_dir]
            if found is None:
                # The same silence one level down: the package this import
                # names could not be read, so every reference through this
                # alias is unjudged rather than resolved.
                unread.append(str(pkg_dir.relative_to(root)
                                  if pkg_dir.is_relative_to(root) else pkg_dir))
                continue
            pkg_name, names = found
            alias = imp.get("alias") or pkg_name
            if alias in (".", "_"):
                # A dot import binds every exported name unqualified and a
                # blank one binds none. Neither leaves a qualifier to resolve.
                continue
            here[alias] = names
        if not here:
            continue
        # A local name shadows the package it is spelled like, and then
        # `client.Do` is a method call on a value. `_REQUEST_ROOTS` records
        # what ignoring that costs on the other side of this repo.
        shadowed = {b.get("name") for b in shape.get("binds") or []}
        for a in shape.get("assigns") or []:
            shadowed |= set(a.get("targets") or [])
        try:
            rel_path = str(p.relative_to(root))
        except ValueError:
            rel_path = str(p)
        seen = set()
        for r in shape.get("refs") or []:
            name = r.get("name") or ""
            if "." not in name:
                continue
            head, _, member = name.partition(".")
            if "." in member or head in shadowed or head not in here:
                continue
            if member in here[head]:
                continue
            key = (rel_path, r.get("line", 0), head, member)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return sorted(out), sorted(set(unread))


def ts_scan(root: Path, files=None, subject=None):
    """`scan`, for TypeScript and JavaScript.

    Same question, and the two halves it needs already exist:
    `symbols.ts_imports` says which specifier bound which names,
    `resolve.ts_resolve` says which file in this repo that specifier
    is, and `symbols.ts_exported_names` says what that file publishes.

    Three things are deliberately not findings, and each is a different reason:

    * a specifier that resolves to nothing -- a package, or a tsconfig alias.
      Not this repo, or not readable without a second config format. Measured
      at 906 aliased specifiers of 6,948 in the eval corpus, 898 in one repo.
    * `default` -- a module can export a default with no name attached, and
      `ts_exported_names` reports the presence, not the identity.
    * a target that re-exports with `export *`. That file publishes a set it
      cannot see itself, and `ts_exported_names` returns the empty set to say
      so. "I do not know" is not "this name is absent" -- reporting the second
      when the first is true is the fail-open shape this whole layer is about.
    """
    from . import symbols
    from .subject_files import tracked, TS_SUFFIXES
    root = Path(root)
    if files:
        rels = [str(f) for f in files if str(f).endswith(TS_SUFFIXES)]
    else:
        rels = [str(f) for f in tracked(subject or {}, root,
                                        suffixes=TS_SUFFIXES)]
    published: dict[str, set] = {}
    out = []
    for rel in sorted(set(rels)):
        p = root / rel
        if not p.is_file():
            continue
        try:
            source = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for spec, names, line in symbols.ts_imports(source):
            target = resolve.ts_resolve(root, rel, spec)
            if not target:
                continue
            if target not in published:
                tp = root / target
                try:
                    published[target] = symbols.ts_exported_names(
                        tp.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    published[target] = set()
            known = published[target]
            if not known:
                continue          # `export *`, or nothing readable: not a finding
            for name in names:
                if name == "default" or name in known:
                    continue
                out.append((rel, line, spec, name))
    return out
