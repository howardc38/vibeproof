"""Repo facts — the external table detectors read instead of hard-coding.

SPEC.md §2 says the `external-write` detector finds call sites by AST and
matches the symbol against "registry 嗰張 outbound 清單".  This module is the
loader for that list.  Nothing repo-specific lives in a detector: the kernel
loads `<repo>/.v4/facts.<repo>.json`, validates it, and hands it over.

Two things this file refuses to let through, because both have already shipped
as bugs in a previous design:

1. **A pattern that cannot be matched.**  A malformed or unreadable facts file
   raises `FactsError`.  It never degrades to "no facts, therefore no findings"
   — a detector that silently scans with an empty table reports a clean repo.

2. **A write pattern that swallows a read.**  The old list said the outbound
   set was `requests.*`, which contains `requests.get`.  Every read-back GET
   added to answer "did the write land?" then became a new write needing its
   own read-back.  `validate()` fails the file when any `outbound_write`
   pattern matches any `outbound_read` pattern (see `_check_write_read_disjoint`).

See docs/FACTS.md for where each field comes from and how to re-derive it.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

from . import layout

from .analysis.subject_files import PROTECTED_DEFAULT
from .analysis.facts_grammar import (  # noqa: F401  (public surface)
    ABSENT_KEY, ABSENT_MIN_CHARS, KINDS, MATCH_MODES, MAY_BE_EMPTY,
    OPTIONAL_LISTS, PATH_LISTS, REQUIRED_KEYS, SYMBOL_LISTS, Entry, Facts,
    FactsError, build, dotted_names, matches_symbol, path_matches, scan_source,
    validate,
)

#: Marks an absence the installer wrote rather than a person.  `doctor` reports
#: every row still carrying it and `ship` refuses while any remain -- so this is
#: a ship gate held together by a string literal typed in three modules.
#: Changing the writing side alone would make the reading side see a repo where
#: every absence had been confirmed.
AUTO_PREFIX = "AUTO:"

# --------------------------------------------------------------------------
# load + validate
# --------------------------------------------------------------------------


def load(path: str | Path) -> Facts:
    """Read and validate a facts file.  Any problem raises `FactsError`."""

    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FactsError(f"{path}: cannot read facts file: {exc}") from exc
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FactsError(f"{path}: not valid JSON (line {exc.lineno}): {exc.msg}") from exc
    return build(obj, source=str(path))


#: Verb endings a proposal treats as a candidate.  Not a vocabulary a detector
#: may use -- FACTS.md forbids a detector hard-coding a repo symbol, and this is
#: the other side of that rule: a proposal is read by a person who deletes most
#: of it, so it may guess where a detector may not.
PROPOSE_WRITE = ("post", "put", "patch", "delete", "write", "send", "upload",
                 "publish", "insert", "execute", "create", "set_password")
PROPOSE_READ = ("get", "head", "read", "fetch", "list", "query", "select",
                "exists", "download", "get_password")
PROPOSE_AUTH = ("authenticate", "authorize", "authorise", "require_permission",
                "check_permission", "verify_token", "current_user", "compare_digest",
                # Dotted, because the leaf alone cannot carry them. Go's
                # constant-time compare is `hmac.Equal`, and `Equal` on its own
                # is `assert.Equal`, `reflect.DeepEqual` and `strings.EqualFold`
                # -- proposing every one of those as an auth decision is how a
                # draft stops being read. `compare_digest` above needs no
                # qualifier because Python spells it uniquely.
                "hmac.equal", "subtle.constanttimecompare")


#: Fixture trees are deliberately-broken sample code -- a red case exists to be
#: wrong.  Whatever vocabulary they use is a rule's test data, never a statement
#: about the repo.
NOT_SOURCE = ("tests/fixtures/",)


def not_this_framework(root: Path):
    """(rel path) -> is this the adopter's own code?

    `v4 install` copies every checker, detector and hook into the repo, so after
    adopting, a plain scan describes the framework rather than the repo that
    installed it -- measured: the first proposal for a four-file sandbox named
    `hooks/stop_gate.py` and `checkers/dependency_audit.py` and none of the
    sandbox's own calls. The registries already say which files arrived that
    way, so this reads them rather than assuming directory names.
    """
    installed = set(NOT_SOURCE)
    for name in ("checkers.json", "detectors.json"):
        try:
            reg = json.loads((root / ".v4" / name).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        installed |= {e["path"] for e in reg.values() if e.get("path")}
    hooks = {f"hooks/{p.name}" for p in (root / "hooks").glob("*.py")} \
        if (root / "hooks").is_dir() else set()
    installed |= hooks
    # `always_*` detectors are exempt from the registration gate -- SPEC.md §2:
    # one fixed line, no dependence on the tree, so "should not fire" cannot be
    # written as a fixture -- and being exempt they are absent from
    # `detectors.json`, which is the registry this function reads. Measured on
    # a fresh Go adoption: 26 detector files on disk, 15 in the registry, and
    # the 11 `always_*` ones came back as the adopter's own code.
    #
    # Globbed by that prefix and not by the directory: an adopter may write its
    # own detector beside these, and `v4 install` already says so by leaving
    # edited files alone. `checkers/` is untouched here for the same reason.
    installed |= {f"detectors/{p.name}"
                  for p in (root / "detectors").glob("always_*.py")}

    def theirs(rel) -> bool:
        s = Path(rel).as_posix()
        return not any(s == i or s.startswith(i) for i in installed)
    return theirs


def propose(root: str | Path) -> dict:
    """A facts table built from what the repo actually calls.  FACTS.md.

    Adopting used to start with an empty file and a schema, which is the point
    at which most adopters write nothing: the table is long, every row needs a
    real `file:line`, and there is no way to know what belongs in it without
    reading the repo. This reads the repo.

    **Every row is a proposal, and the file it produces is not a table yet.**
    A verb ending is a guess -- `requests.get` reads and `cache.get` does not
    reach anything -- so the output is meant to be edited down by someone who
    knows the codebase, and the `seen_at` on each row is where to look. That is
    why this is a separate command and not something `derive` calls: a table
    nobody read is a vocabulary nobody chose.
    """
    root = Path(root).resolve()
    found = {"outbound_write": {}, "outbound_read": {}, "auth_decision": {}}
    entry_dirs, config_files = set(), []
    theirs = not_this_framework(root)

    # The repo's own `derive_exclude`, honoured here as everywhere else. It is
    # the fifth place that re-derived a file set around a rule stated once.
    #
    # Measured on a real adoption: this proposed 574 rows for a repo with 298
    # source files, because it read `artifacts/` (2,044 tracked evidence files
    # from the predecessor's 64 phases) and the predecessor framework itself.
    # A draft nobody can read is a draft nobody prunes, and an unpruned facts
    # table is the vocabulary every detector then works from.
    from .analysis import subject_files
    try:
        import json as _json
        _cfg = _json.loads((root / layout.CONFIG).read_text())
        excluded = tuple(_cfg.get("derive_exclude") or ())
    except (OSError, ValueError):
        excluded = ()
    for path in tracked_source_files(root):
        rel = path.relative_to(root)
        if not theirs(rel) or subject_files.excluded(rel, excluded):
            continue
        if path.suffix == ".go":
            chains, entry = _go_proposals(path)
        else:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError):
                continue
            chains = list(dotted_names(tree))
            entry = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and n.decorator_list for n in ast.walk(tree))
        if chains is None:
            continue
        for dotted, lineno in chains:
            leaf = dotted.rsplit(".", 1)[-1]
            if "." not in dotted:
                continue                  # a bare local call names no boundary
            # `dotted_names` renders a chain whose base is not a name --
            # `(folder / name).write_bytes(…)`, `dict(self.absent).get(…)` --
            # with a `?` base, on purpose, so a suffix pattern still matches it.
            # That rendering is for *matching*. It is not a pattern a table can
            # hold: `facts_grammar.validate` refuses anything that is not a
            # dotted symbol unless it declares `"match": "regex"`.
            #
            # Measured on this repo: the draft proposed `?.exists` and `?.get`,
            # so `v4 install` -- the first command an adopter runs -- handed
            # them a table the next command rejects. Dropping the `?` and
            # proposing `exists` would be worse: a bare leaf matches every
            # `.exists` anywhere, and the loop above already refuses bare names
            # for that reason. So it is skipped, and the sighting is not lost --
            # a `?` chain reaches this line only because its leaf is in a verb
            # list, and `absent` reports the bucket when nothing else lands.
            if "?" in dotted:
                continue
            # Lowered, and compared whole. Go exports `Post` where Python
            # writes `post`, and that is the only difference the verb list
            # cannot absorb. It is not widened to "starts with a verb": that
            # would propose `SendMessage` on the Go side while Python's
            # `send_message` is still passed over, and a proposal list that is
            # generous in one language and strict in the other is a table
            # nobody can read. Both miss a wrapper, and `absent` says so.
            if path.suffix == ".go":
                leaf = leaf.lower()
            whole = dotted.lower()
            for bucket, verbs in (("outbound_write", PROPOSE_WRITE),
                                  ("outbound_read", PROPOSE_READ),
                                  ("auth_decision", PROPOSE_AUTH)):
                if (leaf in verbs or whole in verbs) \
                        and dotted not in found[bucket]:
                    found[bucket][dotted] = f"{rel}:{lineno}"
        if entry:
            entry_dirs.add(str(rel.parent))
    for name in ("pyproject.toml", "setup.cfg", "requirements.txt",
                 "package.json", ".env.example"):
        if (root / name).is_file():
            config_files.append(name)

    # A write pattern that also matches a read pattern is refused by validate(),
    # and a proposal that cannot validate is a proposal nobody can use.
    reads = set(found["outbound_read"])
    found["outbound_write"] = {k: v for k, v in found["outbound_write"].items()
                               if k not in reads}

    # A table this scan found nothing for. Written as a stated absence rather
    # than left as `[]`, because `[]` alone does not validate and a first
    # adoption of a repo that genuinely has no outbound write could not get past
    # it -- the message asked for entries that do not exist.
    #
    # The reason says what was actually searched, which is the only thing this
    # scan can honestly claim: a verb-ending sweep of tracked Python. That misses
    # a write behind `subprocess` or a client wrapper, so every one of these
    # carries AUTO: and `doctor` keeps reporting until a person has replaced it.
    scanned = sum(1 for p in tracked_source_files(root)
                  if theirs(p.relative_to(root))
                  and not subject_files.excluded(p.relative_to(root), excluded))
    absent = {}
    for bucket, verbs in (("outbound_write", PROPOSE_WRITE),
                          ("outbound_read", PROPOSE_READ),
                          ("auth_decision", PROPOSE_AUTH)):
        if not found[bucket]:
            absent[bucket] = (
                f"{AUTO_PREFIX} `v4 install` read {scanned} tracked source "
                f"file(s) at "
                f"{_head(root)[:12]} for a dotted call named or ending in "
                f"{{{', '.join(sorted(verbs)[:6])}…}} and found none. A wrapper "
                f"or a subprocess would not show up here — confirm and rewrite "
                f"this line, or add the rows.")
    # `ui_globs` was drafted as `[]` for every repo, front end or not, and
    # nothing here ever looked. `[]` is the right value for a repo with no
    # client code -- `facts_grammar` lists `ui_globs` in `MAY_BE_EMPTY` and
    # refuses an `absent` entry for it, so the empty list is the declaration,
    # not a gap to be explained. What was missing is the other half: a repo
    # that does have a front end got `[]` too, which is why `surface_proof`'s
    # hint about a repo that has a surface with no way to drive it could not
    # print for anybody. So propose from the tree, like every other bucket in
    # this dict, and leave `[]` to mean what it says.
    #
    # Tests excluded, as everywhere else here. Measured on this repo with them
    # included: the proposal came back `["tests/**"]`, off fixture `.tsx` files
    # that exist to be scanned, never rendered. A repo whose only view files sit
    # under `tests/` has no front end to point `surface_proof` at.
    #
    # The directory the view files are actually in, not their top-level
    # ancestor. Collapsing to the first path segment is what produced the first
    # false positive this proposal ever caused: `valibot`'s `.tsx` files live
    # under `website/src/`, the proposal said `website/**`, and that glob
    # swallowed `website/scripts/contributors.ts` -- a build-time Node script
    # that reads a GitHub token, imports `node:fs` and runs through
    # `tsm ./scripts/contributors.ts`. A checker pointed at it reported a
    # secret shipping to the browser from a file that never reaches a browser,
    # and a false positive is what gets a checker switched off.
    #
    # Common prefixes are folded so a repo with views in twenty route folders
    # gets one glob rather than twenty: if `src/routes/a` and `src/routes/b`
    # both hold views, `src/routes` is what is proposed.
    ui_dirs = _common_dirs(
        str(p.relative_to(root).parent)
        for p in tracked_source_files(root, suffixes=UI_SUFFIXES))
    ui_dirs.discard(".")
    if not entry_dirs:
        absent["entrypoint_globs"] = (
            f"{AUTO_PREFIX} nothing named itself an entry point in {scanned} "
            f"tracked source file(s) at {_head(root)[:12]} -- no decorated "
            f"function in Python, no `func main` and no HTTP handler signature "
            f"in Go. A `__main__`, a CLI or a worker loop would not show up "
            f"here.")

    return {
        **({ABSENT_KEY: absent} if absent else {}),
        "repo": layout.repo_name(root),
        "generated_from_commit": _head(root),
        **{bucket: [{"pattern": pat, "seen_at": at, "kind": "proposed",
                     "note": "猜測,由 verb 結尾估出嚟 —— 核咗 seen_at 先改 kind"}
                    for pat, at in sorted(rows.items())]
           for bucket, rows in found.items()},
        "entrypoint_globs": sorted(f"{d}/**" if d != "." else "**"
                                   for d in sorted(entry_dirs))[:12],
        "ui_globs": sorted(f"{d}/**" for d in ui_dirs)[:12],
        "config_files": config_files,
        "protected_paths": list(PROTECTED_DEFAULT),
    }


def _go_proposals(path: Path):
    """(chains, is_entry_dir) for one Go file, or (None, False) if unreadable.

    An entry point in Go is not a decorated function -- Go has no decorators.
    It is `func main()`, or a function with the signature the language itself
    defines for an HTTP handler. Both are structural facts rather than a
    naming guess, which is more than the Python half can say.
    """
    from .analysis import facts_grammar, gosource, webhook_replay
    shape = gosource.shape(path)
    if shape is None:
        return None, False
    names = {f.get("name") for f in shape.get("funcs") or []}
    entry = "main" in names or bool(webhook_replay.go_handlers(shape))
    return list(facts_grammar.go_dotted_names(shape)), entry


def _head(root: Path) -> str:
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                       capture_output=True, text=True)
    return r.stdout.strip() or "0" * 40


#: The languages this repo's own tooling can read a file in.
#:
#: `.py` alone was the whole list, and every consumer of it inherited that:
#: `v4 install` proposed a facts table from Python and nothing else, so a Go
#: repo adopting this got an empty vocabulary and every rule that reads the
#: table answered UNSUPPORTED -- silently, with the ship report listing them
#: as having run.
SOURCE_SUFFIXES = (".py", ".go")


def _common_dirs(dirs) -> set:
    """The shallowest directories that cover all of `dirs`, without over-reaching.

    A set of leaf directories becomes one glob per sibling group: `src/routes/a`
    and `src/routes/b` fold to `src/routes`, while `src/routes` and `docs/demo`
    stay apart. What it will not do is fold to a single top-level segment, which
    is what made `website/**` cover `website/scripts/`.

    A parent is only taken when more than one child is under it. One directory
    stays exactly where it is -- there is nothing to generalise from a single
    sighting, and generalising is how the glob grew past the files it was
    drawn from.
    """
    leaves = {d for d in dirs if d and d != "."}
    if len(leaves) < 2:
        return set(leaves)
    parents: dict[str, int] = {}
    for d in leaves:
        parent = str(Path(d).parent)
        parents[parent] = parents.get(parent, 0) + 1
    out = set()
    for d in leaves:
        parent = str(Path(d).parent)
        out.add(parent if parent not in (".", "") and parents[parent] > 1 else d)
    # A glob already covered by a shallower one in the same set is noise. Left
    # in, `valibot` came back with four overlapping entries -- `website/src`
    # and three of its own descendants -- and a facts table nobody can read is
    # a facts table nobody corrects.
    return {d for d in out
            if not any(o != d and d.startswith(o + "/") for o in out)}

# View files, not "anything a browser could run". `.ts` and `.js` are as much a
# Node service as a front end, so counting them would propose a `ui_globs` for
# repos that have no client at all. These four are only ever client code.
UI_SUFFIXES = (".tsx", ".jsx", ".vue", ".svelte")


def tracked_source_files(root: str | Path, *, include_tests: bool = False,
                         suffixes=SOURCE_SUFFIXES) -> list[Path]:
    """Git-tracked source under `root`, in the languages asked for.

    Tracked-only on purpose: untracked scratch (adopter_a keeps a gitignored
    `runtime/` full of one-off proof scripts) is not repo behaviour and would
    make the same facts file measure differently on two machines.
    """

    root = Path(root)
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--"]
        + [f"*{suffix}" for suffix in suffixes],
        capture_output=True,
        text=True,
        check=True,
    )
    test_parts = {"tests", "test", "__tests__", "e2e"}
    files = []
    for rel in sorted(x for x in result.stdout.split("\0") if x):
        parts = set(Path(rel).parts)
        if not include_tests:
            if parts & test_parts:
                continue
            name = Path(rel).name
            # `_test.go` is the compiler's rule rather than a convention: a Go
            # file not named that way is not in the test binary at all.
            if name.startswith("test_") or name.endswith(("_test.py", "_test.go")):
                continue
        files.append(root / rel)
    return files


def scan_repo(facts: Facts, root: str | Path, list_name: str, *, include_tests: bool = False):
    """Return `{pattern: ["relative/path.py:12", ...]}` across a repo.

    `protected_paths` are skipped: they are the framework's own files, not the
    behaviour of the repo under test.  adopter_a vendors the whole V3
    framework under `auto-dev/`, whose adoption scripts do plenty of
    `shutil.rmtree` — counting those as the product's filesystem writes would
    be measuring the wrong program.
    """

    entries = facts.entries(list_name)
    root = Path(root)
    hits: dict[str, list[str]] = {e.pattern: [] for e in entries}
    for path in tracked_source_files(root, include_tests=include_tests):
        rel_str = path.relative_to(root).as_posix()
        if path_matches(rel_str, facts.protected_paths):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(root)
        lang = "go" if path.suffix == ".go" else "py"
        for pattern, linenos in scan_source(source, entries, lang=lang).items():
            hits[pattern].extend(f"{rel}:{n}" for n in linenos)
    return hits


def check_seen_at(facts: Facts, root: str | Path, *, gone_only=False) -> list[str]:
    """Return a problem string per entry whose `seen_at` does not hold.

    Empty list means every cited `file:line` exists and the pattern really
    matches there.  This is what stops the table drifting into a wish list.

    Two failures, and only one of them is a defect.  A citation is true of a
    commit: add an import above a symbol and every line below it moves, so
    `matched at line 216 rather than 268` says the tree advanced, not that the
    table is wrong.  `nowhere in this file` says something else entirely -- the
    row cites a file that no longer contains what it names.

    `gone_only` keeps the second and drops the first, which is what makes this
    runnable in CI.  Verifying everything meant a job that failed on any commit
    shifting a line, so the check was never wired at all -- and this repo's own
    table sat failing its own verifier, four rows of it not drift, with nothing
    anywhere saying so.
    """

    root = Path(root)
    problems: list[str] = []
    for list_name in SYMBOL_LISTS:
        for entry in facts.entries(list_name):
            path = root / entry.seen_at_path
            if not path.is_file():
                problems.append(f"{list_name}: {entry.pattern}: no such file {entry.seen_at_path}")
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                problems.append(f"{list_name}: {entry.pattern}: cannot read {entry.seen_at_path}: {exc}")
                continue
            linenos = scan_source(source, [entry])[entry.pattern]
            if entry.seen_at_line in linenos:
                continue
            if gone_only and linenos:
                continue                       # moved, not gone
            near = ", ".join(str(n) for n in linenos[:5]) or "nowhere in this file"
            problems.append(
                f"{list_name}: {entry.pattern}: not matched at {entry.seen_at} (matches: {near})"
            )
    return problems


def restate(facts_path: str | Path, root: str | Path) -> tuple[list[str], list[str]]:
    """`(moved, refused)` -- rewrite the `seen_at` lines a refactor moved.

    `seen_at` is `file:line`, and the line is a value this module already
    derives: `scan_source` says where the pattern matches now, and
    `check_seen_at` uses that to tell a move ("matched at 216 rather than 268")
    from a deletion ("nowhere in this file"). Only the first was ever repaired
    by hand -- the second is a real defect -- and repairing it meant a person
    transcribing a number the tool had just printed. Measured 2026-08-27: one
    row moved twice in a single day, both times a hand edit, both times while
    every gate was green.

    So the tool writes what the tool computed. What it must not do is make a
    defect disappear, and there are exactly two ways it could:

    * the pattern matches **nowhere** -- that is `gone`, the thing
      `--gone-only` keeps and CI fails on. Refused, never rewritten.
    * the pattern matches **more than one** line -- which line the row meant is
      a judgement, and guessing it would put a citation in the table that
      nobody chose. Refused, with the candidates named.

    A row that already holds is untouched, so running this on a clean table
    writes nothing and reports nothing.
    """
    facts_path, root = Path(facts_path), Path(root)
    facts = load(facts_path)
    raw = json.loads(facts_path.read_text(encoding="utf-8"))
    moved, refused = [], []
    for list_name in SYMBOL_LISTS:
        for i, entry in enumerate(facts.entries(list_name)):
            path = root / entry.seen_at_path
            if not path.is_file():
                refused.append(f"{list_name}: {entry.pattern}: no such file "
                               f"{entry.seen_at_path}")
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                refused.append(f"{list_name}: {entry.pattern}: cannot read "
                               f"{entry.seen_at_path}: {exc}")
                continue
            linenos = scan_source(source, [entry])[entry.pattern]
            if entry.seen_at_line in linenos:
                continue                       # already true
            if not linenos:
                refused.append(f"{list_name}: {entry.pattern}: nowhere in "
                               f"{entry.seen_at_path} -- gone, not moved")
                continue
            if len(linenos) > 1:
                refused.append(
                    f"{list_name}: {entry.pattern}: matches "
                    f"{', '.join(str(n) for n in linenos[:5])} in "
                    f"{entry.seen_at_path} -- which one this row means is a "
                    f"judgement, not a rewrite")
                continue
            was, now = entry.seen_at, f"{entry.seen_at_path}:{linenos[0]}"
            raw[list_name][i]["seen_at"] = now
            moved.append(f"{list_name}: {entry.pattern}: {was} -> {now}")
    if moved:
        facts_path.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    return moved, refused


def tree_drift(facts: Facts, root: str | Path) -> list[str]:
    """Reasons the `seen_at` lines may not line up with what is on disk.

    A citation is true of a commit, not of a directory.  Verifying against a
    tree that has moved on produces failures that look like a wrong table when
    they are really a stale checkout, so say which it is up front.
    """

    warnings: list[str] = []
    try:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return ["not a git worktree — cannot tell whether the tree matches generated_from_commit"]
    if head != facts.generated_from_commit:
        warnings.append(
            f"HEAD is {head[:12]}, facts were derived at "
            f"{facts.generated_from_commit[:12]} — line numbers may have moved"
        )
    if dirty:
        modified = [line[3:] for line in dirty.splitlines() if line[:2].strip()]
        warnings.append(f"{len(modified)} uncommitted change(s) in the worktree")
    return warnings


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    usage = (
        "usage: python -m kernel.facts propose <repo-root>\n"
        "       python -m kernel.facts validate <facts.json>\n"
        "       python -m kernel.facts verify <facts.json> <repo-root> "
        "[--gone-only]\n"
        "       python -m kernel.facts restate <facts.json> <repo-root>\n"
        "       python -m kernel.facts scan <facts.json> <repo-root> "
        "[outbound_write|outbound_read|auth_decision] [--sites] [--tests]"
    )
    if not argv or argv[0] in {"-h", "--help"}:
        print(usage)
        return 0

    command, rest = argv[0], argv[1:]
    try:
        if command == "propose":
            print(json.dumps(propose(rest[0] if rest else "."),
                             indent=2, ensure_ascii=False))
            return 0

        if command == "validate":
            facts = load(rest[0])
            print(f"ok: {facts.repo} @ {facts.generated_from_commit[:12]}")
            for name in SYMBOL_LISTS:
                print(f"  {name}: {len(facts.entries(name))} patterns")
            for name in PATH_LISTS:
                print(f"  {name}: {len(getattr(facts, name))} entries")
            return 0

        if command == "verify":
            facts = load(rest[0])
            gone_only = "--gone-only" in rest
            for warning in tree_drift(facts, rest[1]):
                print(f"WARN {warning}")
            problems = check_seen_at(facts, rest[1], gone_only=gone_only)
            for problem in problems:
                print(f"BAD  {problem}")
            what = "rows citing something that is gone" if gone_only else "bad seen_at"
            print(f"{'FAIL' if problems else 'ok'}: {len(problems)} {what}")
            if gone_only and not problems:
                moved = len(check_seen_at(facts, rest[1]))
                if moved:
                    print(f"     ({moved} row(s) moved rather than gone; "
                          f"`restate` rewrites them)")
            return 1 if problems else 0

        if command == "restate":
            moved, refused = restate(rest[0], rest[1])
            for line in moved:
                print(f"ok   {line}")
            for line in refused:
                print(f"BAD  {line}")
            print(f"{'FAIL' if refused else 'ok'}: {len(moved)} row(s) "
                  f"rewritten, {len(refused)} left for a person")
            # Non-zero on a refusal, because the refusals are exactly the rows
            # `verify --gone-only` fails on and the two must not disagree.
            return 1 if refused else 0

        if command == "scan":
            facts = load(rest[0])
            root = rest[1]
            names = [rest[2]] if len(rest) > 2 and not rest[2].startswith("-") else list(SYMBOL_LISTS)
            include_tests = "--tests" in rest
            show = "--sites" in rest
            for name in names:
                hits = scan_repo(facts, root, name, include_tests=include_tests)
                total = sum(len(v) for v in hits.values())
                print(f"== {name}: {total} call sites")
                for entry in facts.entries(name):
                    sites = hits[entry.pattern]
                    print(f"{len(sites):5d}  [{entry.kind}] {entry.pattern}")
                    if show:
                        for site in sites:
                            print(f"         {site}")
            return 0
    except FactsError as exc:
        print(f"FactsError: {exc}", file=sys.stderr)
        return 2
    except IndexError:
        print(usage, file=sys.stderr)
        return 2

    print(usage, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
