"""Which layer may import which.  SPEC.md §3.

The rule a repo already states in prose and nothing reads. `kernel/analysis/`
says "pure analysis, no I/O" in its docstrings, and measured across the
directory: 10 of 22 modules read files and 3 run git. The sentence was never
true and nothing was ever going to make it true, because "what is in this repo"
is part of what these modules decide.

What this checker compares is import edges. It cannot see I/O at all, so the
half of that sentence about I/O had no mechanism behind it -- which is why the
sentence was corrected rather than the ten modules. The contract this layer
really holds is that the judgement lives here and argv and exit codes do not.

Config rather than code, because the layers are the repo's, not the framework's:

    {"layers": [{"name": "analysis", "paths": ["kernel/analysis/**"]},
                {"name": "kernel",   "paths": ["kernel/**"]}],
     "allow": [["kernel", "analysis"]]}

`layers` is ordered most specific first, so `kernel/analysis/**` wins over
`kernel/**` for a file under both. An edge is allowed when it is listed; every
other cross-layer import is a violation. **An allowlist of edges rather than a
denylist of them**, and that direction is deliberate here: the set of legal
edges is small, named, and argued for, while the set of illegal ones is every
pair nobody thought about.

Not ArchUnit. That library expresses the same rules and is a pytest plugin; a
V4 checker is stdlib-only, takes three flags and returns one of four exit
classes, so importing it would mean wrapping a test runner in a checker. The
rule language is worth copying. The dependency is not.
"""

import ast
from fnmatch import fnmatch
from pathlib import Path

from . import pysource
from . import resolve
from .pysource import imported_modules


def layer_of(rel: str, layers) -> str:
    """The first layer whose globs match, or "" for a file in none."""
    for spec in layers:
        for glob in spec.get("paths", []):
            if fnmatch(rel, glob) or fnmatch(rel, glob.rstrip("/*") + "/*"):
                return spec["name"]
    return ""


def go_module_prefix(root: Path) -> str:
    """This repo's Go module path, from `go.mod`, or "".

    An import is `example.com/app/core/db` and the repo knows it as `core/db`;
    the only thing that can bridge the two is the `module` line the repo wrote
    down. Without a `go.mod` there is no bridge, and every import is somebody
    else's package -- which is the right answer, not a reason to guess.
    """
    f = Path(root) / "go.mod"
    if not f.is_file():
        return ""
    for raw in f.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("//", 1)[0].strip()
        if line.startswith("module "):
            return line[len("module "):].strip()
    return ""


def _go_import_layer(imp: str, layers, root: Path, prefix: str) -> str:
    """The layer of the package a Go import path names, or "".

    A Go import names a directory, not a file, so this resolves to the
    directory and asks the layer of a `.go` file inside it -- the same move
    `_module_layer` makes for a Python package with no `__init__.py`.

    Anything outside this module's own prefix is a third-party package and not
    this rule's business, exactly as `dangling_ref` says of a name that
    resolves outside the repo.
    """
    if not prefix or not (imp == prefix or imp.startswith(prefix + "/")):
        return ""
    rel = imp[len(prefix):].lstrip("/")
    d = Path(root) / rel if rel else Path(root)
    if not d.is_dir():
        return ""
    for f in sorted(d.glob("*.go")):
        got = layer_of(str(Path(rel) / f.name) if rel else f.name, layers)
        if got:
            return got
    return ""


def _module_layer(module: str, layers, root: Path) -> str:
    """The layer of the file a dotted module name resolves to."""
    rel = Path(*module.split("."))
    for cand in (rel.with_suffix(".py"), rel / "__init__.py"):
        if (root / cand).is_file():
            return layer_of(str(cand), layers)
    return ""


def scan(root: Path, config, files=None, subject=None):
    """[(path, line, from_layer, to_layer, module)] -- imports going the wrong way."""
    root = Path(root)
    layers = config.get("layers", [])
    allowed = {(a, b) for a, b in config.get("allow", [])}
    if not layers:
        return None                   # no declaration: the caller reports UNSUPPORTED

    # The `else` had no exclusion of any kind -- not even the hand-rolled skip
    # set its two siblings carry -- and `checkers/layer_boundary.py:47` always
    # calls it that way, so on an adopter with a virtualenv this parsed every
    # `.py` under `.venv`. `kernel/hashing.py::tree_state` records the same walk
    # being replaced: 94,147 paths in 4.5s against 367ms.
    from .subject_files import tracked, TS_SUFFIXES
    kinds = (".py", ".go") + TS_SUFFIXES
    candidates = ([Path(f) for f in files if str(f).endswith(kinds)] if files
                  else [Path(f) for f in tracked(subject or {}, root,
                                                 suffixes=kinds)])
    go_prefix = go_module_prefix(root)
    out = []
    for rel in sorted(set(candidates)):
        src = layer_of(str(rel), layers)
        if not src:
            continue
        path = root / rel
        if not path.is_file():
            continue
        if path.suffix == ".go":
            # Go's imports through Go's own parser. `shape` is `None` where the
            # toolchain is absent or the file will not parse, and skipping is
            # the honest absence -- a layer rule that reported no crossings
            # because nobody could read the file is `EVIDENCE.md` §4 again.
            from . import gosource
            shape = gosource.shape(path)
            if shape is None:
                continue
            for imp in shape.get("imports") or []:
                module = imp.get("name") or ""
                dst = _go_import_layer(module, layers, root, go_prefix)
                if not dst or dst == src or (src, dst) in allowed:
                    continue
                out.append((str(rel), imp.get("line", 0), src, dst, module))
            continue
        if path.suffix in TS_SUFFIXES:
            # A layer is a path prefix, so the question for TypeScript is the
            # one `ts_resolve` already answers: which file in this repo does
            # this specifier name. A package (`vitest`) and a tsconfig alias
            # (`@/x`) both resolve to nothing, and nothing is the right answer
            # -- a layer rule is about this repo's own shape, and a specifier
            # this cannot place is not a crossing it can report. That is a
            # stated limit, not a clean bill: `resolve`'s measurement
            # puts aliases at 906 of 6,948 specifiers, 898 of them in one repo.
            from . import symbols
            for spec, _names, line in symbols.ts_imports(
                    path.read_text(encoding="utf-8", errors="replace")):
                target = resolve.ts_resolve(root, str(rel), spec)
                if not target:
                    continue
                dst = layer_of(target, layers)
                if not dst or dst == src or (src, dst) in allowed:
                    continue
                out.append((str(rel), line, src, dst, spec))
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        # Line numbers come from the AST rather than from `imported_modules`,
        # which returns a set: a violation a reader cannot find is a violation
        # they will not fix.
        # This file's own package, which is what a relative import needs to
        # become a name. `imported_modules` skipped them without it, so
        # `from ..kernel import x` -- an import crossing a layer, written the
        # short way -- was invisible to the checker whose whole subject is
        # which direction an import goes.
        package = ".".join(rel.parts[:-1])
        lines = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    lines.setdefault(a.name, node.lineno)
            elif isinstance(node, ast.ImportFrom):
                base = pysource._resolve(node, package)
                if base is None:
                    continue
                if base:
                    lines.setdefault(base, node.lineno)
                for a in node.names:
                    lines.setdefault(f"{base}.{a.name}" if base else a.name,
                                     node.lineno)
        for module in sorted(imported_modules(path.read_text(
                encoding="utf-8", errors="replace"), package)):
            dst = _module_layer(module, layers, root)
            if not dst or dst == src or (src, dst) in allowed:
                continue
            out.append((str(rel), lines.get(module, 0), src, dst, module))
    return out


def finding_id(path: str, src: str, dst: str) -> str:
    """Identity without a line number, for the same reason claim ids have none."""
    import hashlib
    return hashlib.sha256(f"{path}\x1f{src}\x1f{dst}".encode()).hexdigest()[:16]


# ==============================================================================
# A draft a reader prunes, rather than a file nobody writes
# ==============================================================================
#
# This kind installs only when `.v4/layers.json` is there, and nothing proposed
# one -- so an adopter had to author the whole declaration from a blank file
# before the rule could say anything. `facts.propose` had the same shape and
# the same outcome, and its fix is the one copied here: draft it, mark what was
# guessed, and let the person delete rather than compose.
#
# **Only what the repo already states structurally.** Entry surfaces are
# detectable -- a decorated function, `func main`, an HTTP handler signature --
# and a data layer is not. The first draft of this guessed one by looking for
# DML string literals per directory, and on this repo that made all of
# `kernel/**` the data layer because `ledger.py` holds the schema: one file in
# thirty, and a boundary drawn around twenty-nine that have nothing to do with
# it. So the data layer is taken from `dal_globs` when the facts table declares
# one, and left out otherwise. A guess that wrong is worse than a layer nobody
# drafted, which is the trap `prevention`'s eleven "判準闊到會把正常 code 判紅"
# entries are all versions of.
#
# `allow` is drafted as **what this repo does today**, not as what a layered
# design would allow. Two reasons, and the second is the one that matters:
#
#   a draft that fires on the day it is written is a draft nobody keeps. The
#   first run has to be green, or the rule is a wall rather than a ratchet.
#
#   and the pruning is the work. An edge listed in `allow` is a question
#   somebody answers by deleting a line; an edge missing from it is a failure
#   somebody answers by adding one -- the same edit with the burden on the
#   wrong side.

DRAFT_ENTRY, DRAFT_DAL, DRAFT_CORE = "entry", "dal", "core"

#: Edges that usually point the wrong way, named in the draft's comment rather
#: than refused. Nothing is refused here: the draft records what is there.
_SUSPECT = ((DRAFT_DAL, DRAFT_CORE), (DRAFT_DAL, DRAFT_ENTRY),
            (DRAFT_CORE, DRAFT_ENTRY))


def _in_globs(directory: str, globs) -> bool:
    probe = f"{directory}/x" if directory not in ("", ".") else "x"
    return any(fnmatch(probe, g) or fnmatch(directory, g.rstrip("/*"))
               for g in globs)


def _glob_for(directory: str) -> str:
    return "**" if directory in ("", ".") else f"{directory}/**"


def propose(root: Path, entrypoint_globs=(), dal_globs=(), subject=None,
            theirs=None):
    """A `.v4/layers.json` draft, or `None` when fewer than two layers show.

    `entrypoint_globs` and `dal_globs` come from the facts table -- the first
    proposed structurally by `facts.propose`, the second only ever declared by
    a person. Neither is guessed here.

    `theirs(rel) -> bool` says which files are the adopter's own. It is passed
    in rather than imported: `facts.not_this_framework` is the one that knows,
    and `kernel/analysis/` may not import `kernel` -- which is the very edge
    `layer-boundary` was written after catching. Without it the framework's own
    `checkers/`, `detectors/` and `hooks/`, which `v4 install` had copied in
    minutes earlier, were drafted as a layer of the adopter's architecture.
    """
    root = Path(root)
    from .subject_files import is_test, tracked
    # Tests are left out. Layers are about the shipped architecture, and a test
    # directory reaches into every one of them by design -- drafted as a layer
    # it produced a `core -> entry` edge on this repo whose whole content was
    # `tests/` importing `checkers/`, flagged as suspect, and pointing at
    # nothing anybody should change. `facts.tracked_source_files` leaves them
    # out of its own draft for the same reason.
    files = [rel for rel in tracked(subject or {}, root, suffixes=(".py", ".go"))
             if not is_test(rel) and (theirs is None or theirs(rel))]
    dirs = {str(Path(rel).parent) for rel in files}
    if not dirs:
        return None

    entry = {d for d in dirs if _in_globs(d, entrypoint_globs)}
    dal = {d for d in dirs if _in_globs(d, dal_globs)} - entry
    core = dirs - entry - dal

    layers = []
    for name, group in ((DRAFT_ENTRY, entry), (DRAFT_DAL, dal),
                        (DRAFT_CORE, core)):
        if group:
            layers.append({"name": name,
                           "paths": sorted(_glob_for(d) for d in group)})
    if len(layers) < 2:
        return None                    # one layer is not a boundary

    # Every crossing this repo makes today, asked of the checker's own scan
    # with nothing allowed. One walk, one rule, and the draft cannot disagree
    # with the program that will judge it.
    every = scan(root, {"layers": layers, "allow": []}, files=files,
                 subject=subject) or []
    edges = sorted({(a, b) for _, _, a, b, _ in every})
    suspect = [f"{a} -> {b}" for a, b in edges if (a, b) in _SUSPECT]

    comment = [
        "草稿 —— `v4 install` 由呢個 repo 而家嘅樣讀返嚟,唔係一個「應該點分」嘅建議。",
        "`layers` 由最具體排到最闊:一個檔喺兩個 glob 之下,前者贏。",
        "`allow` 列住嘅係**今日真係存在嘅每一條跨層 import**,所以第一次跑一定綠。",
        "工作係刪 —— 每刪一條邊,就係講咗一次「呢個方向唔應該有」。",
    ]
    if suspect:
        comment.append("由呢幾條睇起 —— 佢哋指嘅方向通常係反嘅:" + "、".join(suspect))
    else:
        comment.append("冇一條邊指住通常係反嘅方向。")
    if not dal:
        comment.append("冇草 data layer:`entry` 由結構認得出(decorator / `func main` / "
                       "handler signature),data layer 認唔出。facts 表宣告咗 "
                       "`dal_globs` 就會用,冇宣告就唔估。")
    comment.append("核完 `mv .v4/layers.json.draft .v4/layers.json`,再行 `v4 install`。")

    return {"_comment": comment,
            "layers": layers,
            "allow": [[a, b] for a, b in edges]}
