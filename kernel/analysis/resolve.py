"""Which file does this import name.  One question, one answer per language.

The question had no home, so it grew two answers in two modules that own
neither: `dangling_ref` held the Python one because it walks references, and
`signature_change` held the TypeScript one because it follows callers.  Each
then imported the other to borrow the half it lacked, and the borrowing is what
`LINT-IMPORT-CYCLE` reported --

    dangling_ref -> layers -> signature_change -> dangling_ref

with `signature_change`'s edge the only top-level one, which is why the other
two had to be written inside functions to keep the interpreter from meeting the
cycle.  A deferred import is the ordinary way to break a cycle and it is the
wrong repair here: it hides that two modules are each holding a piece of a
concern that belongs to neither.

So the concern gets a module.  Three callers, two functions, no new behaviour:
`py_resolve` is `dangling_ref._module_path` and `ts_resolve` is
`signature_change.ts_resolve`, moved rather than rewritten.  This module
imports nothing from its own package, so nothing here can lie on a cycle.

The two are deliberately not unified.  Python resolution is a dotted name
against a package tree and answers with a `Path`; TypeScript resolution is a
relative specifier against a directory and answers with a repo-relative string.
A single function taking a language flag would be one signature describing two
different questions.
"""

from __future__ import annotations

from pathlib import Path


def py_resolve(root: Path, dotted: str):
    """`a.b.c` -> the file that would define it, or None if it is not ours.

    A directory with no `__init__.py` is a module too -- PEP 420 -- and walking
    past it to a shorter prefix answers about the wrong module. Measured on this
    repo: `kernel/analysis/` has no `__init__.py`, so every
    `from kernel.analysis import x` resolved to `kernel/__init__.py`, found no
    `x` there, and was reported. 42 findings, all of them the same false one,
    on a checker whose whole job is to be believed.
    """
    parts = dotted.split(".")
    for n in range(len(parts), 0, -1):
        base = root.joinpath(*parts[:n])
        for cand in (base.with_suffix(".py"), base / "__init__.py"):
            if cand.is_file():
                return cand
        # The exact module, as a directory that binds its children by name.
        # Only at the full length: a shorter prefix landing on a directory is
        # the walk-past this exists to stop.
        if n == len(parts) and base.is_dir():
            return base
    return None


def py_submodule(package_path: Path, name: str):
    """An exact child module; a same-named package attribute may still win."""
    if package_path.is_dir():
        directory = package_path
    elif package_path.name == "__init__.py":
        directory = package_path.parent
    else:
        return None
    base = directory / name
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return base if base.is_dir() else None


# ── TypeScript and JavaScript ────────────────────────────────────────────────
#
# The same refusal to guess. What "resolution" means here was measured on five
# DeepSWE repos rather than recalled: of 6,948 import specifiers, 4,702 are
# relative, 1,340 name a package (not this repo, so not this rule), and 906 go
# through a tsconfig alias.
#
# The relative ones are resolvable with path joining and three extension rules,
# all of which the corpus exercises: 2,757 write `.ts` outright, 1,289 write no
# extension at all, and 621 write `.js` meaning the `.ts` beside it -- the ESM
# convention, and the one that would silently resolve to nothing if taken
# literally.
#
# The aliased ones are not resolved, and the coverage note belongs here rather
# than in a commit message: a call site reaching its target through `@/x` is
# not judged. 898 of the 906 were in one repo, so this is a per-repo gap rather
# than a uniform one. Reading `tsconfig.json` would close it and would mean
# this module resolving a second configuration format; it is left undone and
# said out loud, which is the same call `GO_ASSERT_CALLS` makes about matchers
# it has not seen.

#: Resolution order, which is not the same list as `subject_files.TS_SUFFIXES`
#: and must not be merged with it. That one answers "is this file TypeScript",
#: a membership test where order carries nothing. This one is tried in order
#: and includes `.mts`/`.cts`, which a file-set question has no reason to name.
_TS_SUFFIX_ORDER = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")


def ts_resolve(root: Path, importer_rel: str, spec: str):
    """The repo-relative file a relative import names, or None.

    None for a package (`vitest`), for a tsconfig alias (`@/x`), and for a
    relative path with nothing behind it. Each of those is a different reason
    and none of them is a finding.
    """
    if not spec.startswith("."):
        return None
    base = (Path(importer_rel).parent / spec)
    parts = []
    for part in base.parts:                       # normalise `..` without disk
        if part == "..":
            if parts:
                parts.pop()
        elif part not in (".", ""):
            parts.append(part)
    cand = Path(*parts) if parts else None
    if cand is None:
        return None
    tries = []
    suffix = cand.suffix
    if suffix in (".js", ".jsx", ".mjs", ".cjs"):
        # `./x.js` in a TypeScript project means the `x.ts` beside it. 621 of
        # the corpus's relative imports are written this way, and resolving the
        # name literally finds nothing for every one of them.
        stem = cand.with_suffix("")
        tries += [stem.with_suffix(s) for s in _TS_SUFFIX_ORDER]
    if suffix in _TS_SUFFIX_ORDER:
        tries.append(cand)
    if not suffix:
        tries += [cand.with_suffix(s) for s in _TS_SUFFIX_ORDER]
        tries += [cand / f"index{s}" for s in _TS_SUFFIX_ORDER]
    for t in tries:
        if (root / t).is_file():
            return str(t)
    return None
