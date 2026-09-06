"""Subject hashing and claim identity.  SPEC.md §4.

Two decisions here carry most of the design's weight:

  Identity excludes line numbers.  A claim is keyed by the enclosing symbol,
  so adding an import twenty lines above it does not mint a new claim and
  orphan the old one.  rev 1 keyed on file:line and that alone reproduced V3's
  Loop A -- mechanically, on every task, without anybody making a mistake.

  A subject can be another claim's attempt.  A `surface` claim that depends on
  a `runtime` observation cannot express that dependency through file hashes,
  because re-running runtime changes no file.  So refs come in two kinds and
  staleness treats them the same way.
"""

import hashlib
import json
from pathlib import Path

from .analysis import tables

ABSENT = "absent"

#: Paths the kernel writes into the repo.  Excluded from the working-tree
#: digest, because recording an answer must not invalidate it -- and excluded by
#: the `scope` checker, because kernel output is not a change a person made.
#:
#: One tuple rather than one per consumer.  There were two, and they diverged:
#: `ship` writes `.v4/ledger_export.jsonl` on both the pass and the held path,
#: and the copy in `checkers/scope.py` never listed it, so a second `ship`
#: reported the kernel's own output as an unauthorised write to a protected
#: path.  The same shape as the three bugs the exclusion set exists to stop,
#: arriving through a duplicated list instead of through a missing entry.
#:
#: Prefixes, not exact names: `.v4/risks/` is a directory.
KERNEL_WRITTEN = (
    ".v4/risks/",
    ".v4/chain_head.json",
    # `.v4/checkers.json` is deliberately NOT here. The by-name branch exempts a
    # path whatever its content, and this file maps each claim kind to the
    # program that judges it -- so a hand edit repointing a kind at a more
    # permissive checker was never reported by the `scope` claim, while the
    # docstring below argues the exemption "can only recognise the absence of an
    # edit". That is true of the sha branch and was false of this entry.
    # `cli.cmd_install` passes it to `stamp_generated`, so an install still
    # answers for it by hash; a hand edit no longer does.
    ".v4/ledger_export.jsonl",
    #
    # `.v4/chain_head.json` above and `.v4/ledger_export.jsonl` here are the two
    # this list cannot answer for by bytes: `v4 ship` writes them, not
    # `v4 install`, so `stamp_generated` has no record of them and the sha
    # branch is silent. That makes them exactly the shape the comment above
    # gives for removing `.v4/checkers.json` -- exempt whatever their content --
    # and a review asked why they stay.
    #
    # They stay because a stronger mechanism owns both, and this is where that
    # is said. `ledger.audit_chain` compares the anchor against the rows it
    # anchors and `v4 ship` refuses on `chain: BROKEN`; `ledger.verify_exported`
    # walks the committed export the same way. A hand edit of either is caught
    # by the thing whose whole job is to catch it, and caught harder than a
    # `scope` row -- `ship` stops. What `scope` would add is a second reader of
    # the same fact, on a path the kernel rewrites on every ship, which is how
    # `.gitignore` and `.v4/home` got here.
    #
    # `tests/test_gates_that_said_one_thing.py` pins the handover: a forged
    # anchor is refused by the audit. Naming a mechanism and not checking it is
    # the half of this argument that costs nothing to make.
    ".v4/installed.json",
    # `install.write_launcher` appends a block to `.gitignore`, and nothing
    # recorded that it did -- so the next task saw it in `git diff` with nobody
    # able to explain it, which is precisely the failure `stamp_generated`
    # documents ("three of the eight paths `scope` flagged on a real run, none
    # of them anybody's edit"). This was the fourth.
    ".gitignore",
    # Written by `install.write_launcher` beside `bin/v4`, and stamped by
    # neither: `stamp_generated` names the launcher and not this. Fourth miss.
    ".v4/home",
    # Written by `review.defer` whenever a finding moves, so its bytes change
    # after install and the manifest cannot answer for it. Third miss, found by
    # one review and still open at the next.
    ".v4/deferred/",
)

#: The other half of the same question, written down so the partition is total.
#: These live under `.v4/` and a person edits them, so a change to one *is* the
#: tree moving and must not be excluded.  Kept next to the exclusions rather
#: than inside a test, because which files an adopter owns is a fact about this
#: framework and not an assertion about it.
ADOPTER_OWNED = (
    ".v4/config.json",
    ".v4/acceptance.json",
    ".v4/facts",              # `.v4/facts.<repo>.json`, and its `.draft`
    ".v4/layers.json",
    # Where a repo widens a rule the kernel ships. `kernel/` is pointed at and
    # never copied, so a vocabulary that lives there is one only this repo can
    # edit -- these two are how an adopter says "this is a transport here" and
    # "this is a credential here" without a fork.
    ".v4/fail_closed.json",
    ".v4/secret_patterns.json",
    # Where each rule read out of the source corpus landed. Nothing in
    # `kernel/`, `checkers/`, `detectors/` or `hooks/` writes it and
    # `v4 install` does not ship it -- `spec_coverage` and
    # `registry_consistency` only read it, and the second returns nothing at
    # all when it is absent. It reached this list the day `spec_coverage`'s
    # judgement moved into `kernel/`: the path had been unclassified since it
    # was written, and `EveryPathUnderV4IsAccountedFor` only scans `kernel/**`,
    # so nothing had ever had to say which of the two it is.
    ".v4/rule_dispositions.json",
    # A repo's declared ceiling and the written reason for its last raise.
    # `checkers/control_plane_budget.py` reads it and nothing writes it; raising
    # it is the one-line diff its own FAIL message describes, made by a person.
    ".v4/control_plane_budget.json",
    # The three baselines this repo carries. A checker *proposes* one with
    # `--emit-baseline` and a person accepts entries into it, one at a time,
    # each with a reason and an id -- so the file that lands in git is somebody's
    # decision, not the kernel's output. `config.BASELINE_TEMPLATE` names them
    # by pattern, and a pattern is what the scan below could not read: it splits
    # at the brace and leaves `.v4/`, which every entry starts with, so the
    # question was answered trivially for all three.
    ".v4/fail-closed_baseline.json",
    ".v4/test-shape_baseline.json",
    ".v4/test-token-shape_baseline.json",
    # What this repo says it must answer for, and how it grades what it did not.
    # `kernel/coverage.py` reads both and writes neither; `cli` prints when they
    # are absent. Nothing in `kernel/`, `checkers/`, `detectors/` or `hooks/`
    # produces them.
    ".v4/obligation_catalogue.json",
    ".v4/risk_rubric.json",
)

#: The third answer, and the one that scales: `v4 install` copies these in and
#: records a sha per file, so `_shipped` answers for each of them by bytes.
#: Measured on one adopter: 1,461 fixture files and 9 lenses. Naming the
#: directories here rather than the files is deliberate -- the record is what
#: answers, this only says which directories are answered that way.
SHIPPED_DIRS = (
    ".v4/fixtures",
    ".v4/lenses",
)
MISSING_ATTEMPT = "no-attempt"


def _shipped(root: str):
    """`{path: sha}` of what `v4 install` last wrote into this repo."""
    try:
        m = json.loads((Path(root) / ".v4" / "installed.json").read_text())
    except (OSError, ValueError):
        return {}
    # A flat `{path: sha}` map -- what `install.copy_files` writes. Reading it as
    # `m["shipped"]` returned `{}` for every path, so the exemption below was
    # dead the day it was written and said nothing about being dead.
    if not isinstance(m, dict):
        return {}
    return {k: v for k, v in m.items() if isinstance(v, str)}


def kernel_written(rel: str, root=None) -> bool:
    """Did the kernel write this path, rather than a person?

    A trailing slash means "this directory and everything under it", so the
    directory itself matches too. Without that, the set held two shapes -- a
    prefix and an exact name -- and `.v4/risks` fell between them.

    The list answers by name, which is a guess. `.v4/installed.json` answers by
    bytes, which is a record: `v4 install` writes every checker, detector,
    fixture and lens it ships together with the sha it wrote. A path whose
    current bytes still match that sha was not touched by anybody here.

    Measured: running `v4 install` while a task was open put eight `checkers/*.py`
    into `git diff`, and both `scope` and `test` read them as the task's own work
    -- "8 changed path(s) outside the declared scope" and "the suite passed and
    executed none of the 8 changed file(s)". Neither was the worker's doing.

    A worker who edits a checker to weaken it changes its bytes, so the sha stops
    matching and the path is reported again. The exemption cannot be used to hide
    an edit; it can only recognise the absence of one.
    """
    if any(rel == k.rstrip("/") or rel.startswith(k) for k in KERNEL_WRITTEN):
        return True
    if root is None:
        return False
    want = _shipped(str(root)).get(rel)
    if not want:
        return False
    f = Path(root) / rel
    return f.is_file() and file_sha(f) == want


def file_digest(path: Path) -> str:
    if not path.is_file():
        return ABSENT              # deleting a file changes the subject; not an error
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def ref_key(ref: dict) -> str:
    if ref["kind"] == "file":
        return f"file:{ref['path']}"
    if ref["kind"] == "attempt":
        return f"attempt:{ref['claim']}"
    raise ValueError(f"unknown subject ref kind: {ref['kind']!r}")


def subject_digest(repo_root: Path, subject_refs, latest_attempt_id=None) -> dict:
    """{ref_key: digest} for every ref a claim is about.

    `latest_attempt_id` is a callable (claim_id) -> int|None, injected so this
    module stays free of database imports.
    """
    out = {}
    for ref in subject_refs:
        key = ref_key(ref)
        if ref["kind"] == "file":
            out[key] = file_digest(Path(repo_root) / ref["path"])
        else:
            att = latest_attempt_id(ref["claim"]) if latest_attempt_id else None
            out[key] = str(att) if att is not None else MISSING_ATTEMPT
    return out


def claim_id(task_id: str, kind: str, file: str, symbol: str, variant: str) -> str:
    """SPEC.md §4.

    task_id is included because the ledger is shared across worktrees: without
    it, the same call site touched by two tasks collapses to one claim and one
    worker's PASS silently answers the other's, over different file contents.

    subject_files are excluded because `v4 scope widen` changes them, and a
    claim about the same defect must keep the same identity across a widen.

    **16 hex characters is 64 bits, and the odds were never written down.**
    Examined rather than widened, because widening renames every claim in every
    existing ledger and the number turns out to be fine: a birthday collision
    needs about 2**32 ids for even odds, and the collision probability for `n`
    ids is roughly `n**2 / 2**65`. This framework's own ledger holds a few
    hundred; the reference adopter holds under a thousand. At 10,000 claims the
    chance is about 3 in 10**9, and at a million -- which no repo will reach,
    since a claim is one defect in one symbol in one task -- it is 3 in 10**5.
    A collision is also not silent: the two claims would share a row, so the
    second one's question and subject would be the first one's, which is the
    kind of thing a reader notices immediately.
    """
    # subject files are excluded on purpose: `v4 scope widen` changes them,
    # and the same defect has to keep one identity across a widen.
    material = "\x1f".join([task_id, kind, file or "", symbol or "", variant or ""])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def file_sha(path) -> str:
    return file_digest(Path(path))


def program_sha(repo_root, entry_path) -> str:
    """The bytes of a checker **and of everything in this repo it imports**.

    `checker_sha` was the entry file alone, and 20 of 27 checkers are a thin CLI
    over a module in `kernel/analysis/` -- the one that actually decides. So the
    staleness rule "a different program gave that answer" was true of the
    argument parser and false of the judgement.

    Measured here: a false-positive class in `kernel/analysis/dangling_ref.py`
    was fixed, the verdict changed from 42 findings to none, and not one
    recorded PASS went stale. Every claim that checker had ever answered stayed
    answered, by a program that no longer existed.

    Two roots, and the second one matters more than it looks. In an adopter the
    checker is copied in and `kernel/analysis/` is not -- it stays in the
    framework, reached through `PYTHONPATH`. Following same-repo imports only
    therefore hashed the argument parser and skipped the judgement, which is the
    exact failure this function was written to fix, fixed in this repo and left
    standing in every repo that adopts it.

    Measured: `program_sha` for the adopter's `checkers/secret_chain.py` did not
    move when the framework's `kernel/analysis/secret_chain.py` was edited, so
    every claim it had answered stayed answered by a program that no longer
    existed.

    A stdlib or site-packages module is still out. The line is not "same repo",
    it is "something the operator changes on purpose" -- `v4 install` is exactly
    that act, and an interpreter upgrade is the separate question this does not
    try to answer.
    """
    root = Path(repo_root).resolve()
    entry = Path(entry_path).resolve()
    roots = [root]
    home = root / ".v4" / "home"
    if home.is_file():
        try:
            fw = Path(home.read_text().strip()).resolve()
            if fw != root and (fw / "kernel").is_dir():
                roots.append(fw)
        except OSError:
            pass
    seen, order = set(), []

    def walk(path: Path):
        try:
            path = path.resolve()
            rel = next(path.relative_to(r) for r in roots
                       if path.is_relative_to(r))
        except (OSError, ValueError, StopIteration):
            return
        if str(rel) in seen or not path.is_file():
            return
        seen.add(str(rel))
        order.append((str(rel), path))
        # The table beside the module, when there is one.
        #
        # `secret_patterns.py` and `fail_closed.py` both read
        # `Path(__file__).with_suffix(".json")` -- the rule they apply is data
        # now, so that a repo can widen it without editing the kernel. Following
        # imports finds `.py` and `__init__.py` and nothing else, so moving 156
        # literals out of the code took them out of this hash: measured, editing
        # `kernel/analysis/fail_closed.py` moved `program_sha` and editing
        # `fail_closed.json` did not. `fail-closed` is subject-scoped, so the
        # worktree digest does not cover it either, and every claim answered
        # under the old vocabulary stayed answered under a new one.
        #
        # And the repo's own rows, under `.v4/` by the same name. That half was
        # missed the first time this was fixed: the shipped table joined the
        # hash and the adopter's did not, so a repo could widen the rule it is
        # judged by -- `union` only ever adds, and a handler that passed
        # because its call was not recognised as a transport fails once it is
        # -- and every claim answered under the narrower table stayed answered.
        # Measured: writing `.v4/fail_closed_vocabulary.json` moved
        # `program_sha` not at all.
        #
        # By convention and not by a list: `analysis/tables.py` spells both
        # sides of one name, the module reads its two tables through it, and
        # this walks to the same two. A module with no shipped table is not a
        # table-driven rule, which is what keeps `kernel/config.py` from
        # claiming `.v4/config.json`.
        table = tables.shipped(path)
        if table.is_file():
            for t in (table, tables.own(root, path)):
                if not t.is_file():
                    continue
                try:
                    trel = next(t.relative_to(r) for r in roots
                                if t.is_relative_to(r))
                except (ValueError, StopIteration):
                    continue
                if str(trel) not in seen:
                    seen.add(str(trel))
                    order.append((str(trel), t))
        # The Go emitter, on the same reasoning as the table beside a module and
        # for a gap that is larger. `gosource` runs `kernel/analysis/_go/` as a
        # subprocess, so the program that decides what thirteen checkers see of
        # a Go file is not reached by any Python import and has no `.json`
        # table -- it fell through both branches above.
        #
        # Measured: appending one comment line to `_go/shape.go` left
        # `program_sha` for `checkers/fail_closed.py` byte-identical. Every Go
        # verdict this repo has ever recorded therefore stayed answered by a
        # program that no longer exists, which is the exact failure this
        # function's own docstring cites as the reason it was written.
        #
        # By name and not by a list: a module that shells out to the Go side
        # imports `gosource`, and `gosource` is where `_GO_DIR` lives, so the
        # one module that owns the path is the one that pulls its sources in.
        if Path(rel).name == "gosource.py":
            for src in sorted(path.parent.glob("_go/*.go")):
                try:
                    grel = next(src.relative_to(r) for r in roots
                                if src.is_relative_to(r))
                except (ValueError, StopIteration):
                    continue
                if str(grel) not in seen:
                    seen.add(str(grel))
                    order.append((str(grel), src))
        try:
            import ast
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            return
        # Where this file sits, so a relative import can be resolved from it:
        # `kernel/analysis/dal_write.py` is in package `kernel.analysis`.
        pkg = list(Path(rel).parent.parts)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                # `and node.module and not node.level` skipped every relative
                # import: `from . import x` has `module=None`, `from .x import y`
                # has `level=1`, and both failed the guard. Measured: editing
                # `kernel/analysis/pysource.py` left `program_sha` for
                # `checkers/dal_write.py` unchanged, because that module reaches
                # it through `from . import pysource` -- so 20 of 29 checkers
                # missed at least one of the modules that decide their verdict,
                # including `dangling_ref.py`, the module this function's own
                # docstring cites as the failure it was written to fix.
                if node.level:
                    if node.level - 1 > len(pkg):
                        continue
                    head = pkg[:len(pkg) - (node.level - 1)]
                else:
                    head = []
                mod = head + (node.module.split(".") if node.module else [])
                if not mod:
                    continue
                # Both `from pkg import mod` and `from pkg.mod import name`:
                # the first names a file, the second names its parent.
                dotted = ".".join(mod)
                names = [dotted] + [f"{dotted}.{a.name}" for a in node.names]
            else:
                continue
            for dotted in names:
                parts = dotted.split(".")
                for r in roots:
                    base = r.joinpath(*parts)
                    for cand in (base.with_suffix(".py"), base / "__init__.py"):
                        if cand.is_file():
                            walk(cand)

    walk(entry)
    h = hashlib.sha256()
    for rel, path in sorted(order):
        h.update(rel.encode())
        h.update(b"\x1f")
        h.update(file_digest(path).encode())
        h.update(b"\x1e")
    return h.hexdigest()


def declared_exclude(repo_root) -> tuple:
    """The repo's own `derive_exclude`, read from where it is stated.

    Read here rather than threaded through `claim_state`, `_staleness_key`,
    `task_report` and every caller of those, because this value has already been
    restated in three places and each restatement was a place it went missing.
    The rule has one home; the digest reads it.

    Public because a fourth reader arrived that has no `cfg` to thread it from:
    `register.probe_repo_subject` builds a subject for a repo it was handed a
    path to, and its copy of that subject was the one missing `derive_exclude`
    entirely.
    """
    import json
    try:
        cfg = json.loads((Path(repo_root) / ".v4" / "config.json").read_text())
    except (OSError, ValueError):
        return ()
    got = cfg.get("derive_exclude") or ()
    return tuple(got) if isinstance(got, (list, tuple)) else ()


class GitUnreadable(RuntimeError):
    """git ran here and refused.  Never "this directory is not a repository".

    The two were one value -- the empty string -- and this module is the one
    that computes the staleness digests deciding whether an answered claim stays
    answered. An index lock held by another process, a corrupt object, a
    `safe.directory` refusal: each of them made `tree_state` return `{}`, which
    reads as a directory git does not manage, which makes the digest fall back
    to a walk and say nothing. Same digest, different reason, no way to tell.
    """


def _git(root: Path, *args) -> str:
    """git's stdout here, or `GitUnreadable`.  The one adapter in this module.

    There were two, nested inside `tree_state` and `worktree_digest`, with
    identical bodies -- and the second was dead, because that function now takes
    its state from the first. Both collapsed every failure into `""`.

    A non-zero exit is a refusal and it is raised. Whether this is a repository
    at all is a separate question with a separate answer (`_is_repo`), asked
    first, so "no repo here" never has to be recovered from an error string.
    """
    import subprocess
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        first = (r.stderr.strip().splitlines() or [""])[0][:160]
        raise GitUnreadable(
            f"`git {' '.join(args)}` exited {r.returncode} in {root}"
            + (f": {first}" if first else ""))
    return r.stdout


def _is_repo(root: Path) -> bool:
    """Does git manage this directory?  Asked of git, not inferred from a message.

    A fixture directory and a scratch dir are ordinary here -- `worktree_digest`
    and `lifecycle._files_in_scope` both fall back to a walk for them -- and
    that case has to be told apart from git failing inside a real repo. Reading
    it off stderr would be deciding an external system's behaviour from its
    prose; this asks the question git has for it.

    Through `_git` like everything else here, so this module has one place that
    starts a git process. A second `subprocess.run` with the same arguments is
    how the two nested helpers above came to exist.
    """
    try:
        return _git(root, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except GitUnreadable:
        return False


def tree_state(repo_root) -> dict:
    """`{path: blob sha}` for every file this repo owns, as git sees it.

    One fact, one owner. `_files_in_scope` used to answer the same question a
    second way -- `Path.rglob("*")` over the whole tree -- and the two answers
    were not the same answer. Measured on the reference adopter: 94,147 paths in
    4.5s from the walk against 367ms here, and the difference was almost all
    `.venv/` and `node_modules/`, which git has been told to ignore and which
    the walk had no way to know about. Detectors were being pointed at a
    vendored library and the digest was not, so "the files being judged" and
    "the files the staleness key covers" were two different sets.

    Deriving both from `git ls-files` fixes it at the source rather than by
    excluding two directory names by hand -- a hand-written list is the shape
    this project keeps finding and removing, and it would have said nothing
    about `vendor/`, `target/` or `.tox/`.

    A clean file's hash comes from the index and is never read off disk; only
    what `git status` calls dirty is hashed. A directory git does not manage
    returns `{}`, and the one caller that must still work there says so.

    **`{}` means that and only that.** git refusing inside a repository raises
    `GitUnreadable`, because the two used to be the same answer: a held index
    lock made this look like a scratch directory, the digest walked the tree
    instead, and nothing anywhere said the index had not been read.
    """
    root = Path(repo_root)
    if not _is_repo(root):
        return {}

    state = {}
    for line in _git(root, "ls-files", "-s").splitlines():
        meta, _, rel = line.partition("\t")
        parts = meta.split()
        if len(parts) >= 3 and rel:
            state[rel] = parts[1]

    dirty = [line[3:] for line
             in _git(root, "status", "--porcelain", "-z",
                     "--untracked-files=all").split("\0")
             if len(line) > 3]
    live = [d for d in dirty if (root / d).is_file()]
    if live:
        out = _git(root, "hash-object", "--", *live).splitlines()
        for rel, sha in zip(live, out):
            state[rel] = sha
    for d in dirty:
        if not (root / d).is_file():
            state.pop(d, None)          # deleted: it leaves the tree
    return state


def moved(before: dict, after: dict, limit=6) -> list:
    """`["+ path", "- path", "~ path"]` -- what changed between two tree states.

    `SUBJECT_MOVED` said "the working tree changed while the checker ran" and
    stopped there, which sends the reader looking for another worktree when the
    usual answer is that the checker did it to itself. Measured twice in this
    project: `test` wrote `__pycache__/*.pyc` into a repo with no `.gitignore`
    and returned SUBJECT_MOVED on every first run, and a `runtime_proof`
    trigger that wrote its probe file into the repo did the same within minutes
    of being written.

    `worktree_digest`'s own docstring already states the rule -- "a staleness
    key that the act of answering moves is not a staleness key" -- so naming
    the path is the difference between a rule somebody can follow and a riddle.
    """
    out = [f"+ {p}" for p in sorted(set(after) - set(before))]
    out += [f"- {p}" for p in sorted(set(before) - set(after))]
    out += [f"~ {p}" for p in sorted(k for k in set(before) & set(after)
                                     if before[k] != after[k])]
    return out[:limit] + ([f"…and {len(out) - limit} more"] if len(out) > limit else [])


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def worktree_digest(repo_root, exclude=None, reads=None) -> str:
    """What the working tree actually contains right now.

    Repo-scoped claims (test, lint, scope) used to key on HEAD. A worker edits
    files without committing, so HEAD does not move, so those claims never went
    stale: pass a minimal version, collect three greens, then write the real
    code and ship it without any of it having been tested. The ledger stays
    clean and the chain verifies, which is what makes that failure worth
    naming -- it leaves no trace.

    So the key is content -- and the first version wrote that sentence and then
    hashed `HEAD ‖ diff-against-HEAD ‖ untracked`, which is content *relative to
    a commit*. Committing changes the commit and empties the diff. Measured on a
    five-task run: `v4 check` green, `git commit` with not one byte altered,
    `v4 ship`, and every repo-scoped claim came back STALE. The ordinary flow --
    check, commit, ship -- could not converge.

    Now it is one entry per path, each entry git's own blob hash. Bytes move it;
    staging and committing do not.

    `reads` is what the checker that answers this claim declared it reads --
    `.v4/checkers.json`'s `reads`, the same field `doctor` already polices. A
    repo-scoped answer depends on the paths its checker opens and on nothing
    else, and hashing the whole tree instead meant editing `docs/SPEC.md` or a
    baseline expired the `test` claim, whose oracle is a test command that
    reads neither. Measured on this repo: the suite is 300-470 seconds and was
    re-run roughly fifteen times in one day for edits it could not see.

    `None` keeps the whole tree, which is what a caller with no registry entry
    has to fall back to: a checker that declared nothing is a checker that
    might read anything.

    `exclude` is the repo's `derive_exclude`, and it has to be here for the same
    reason it is in `_files_in_scope`: it names files that are not the code being
    judged. Untracked ones were counted, and the test command creates untracked
    files -- in a repo with no `.gitignore`, running the suite writes
    `__pycache__/*.pyc`, which moves this digest between the stamp taken before
    the run and the one taken after. Measured on a first adoption: the `test`
    claim returned SUBJECT_MOVED on every first run and PASS on every second,
    deterministically, and the second only worked because the artefacts already
    existed. A staleness key that the act of answering moves is not a staleness
    key.

    Reaches git through `tree_state` and nothing else. It used to carry its own
    nested `git()` -- byte for byte the one inside `tree_state` -- left behind
    when the state moved there, so this function held a private adapter it never
    called and a second place for a git failure to become the empty string.
    """
    root = Path(repo_root)
    # None means "whatever this repo declares"; `()` is a caller saying it
    # really wants nothing excluded, and a test relies on being able to say so.
    if exclude is None:
        exclude = declared_exclude(root)

    # A signature writes .v4/risks/<claim>.json, and that file is part of the
    # tree. Counting it means signing one claim un-answers every other
    # repo-scoped one -- sign two and neither is ever terminal at the same
    # time. The record is the signature's own output, not a change to what
    # judges the work.
    def _excluded(rel):
        # Everything the kernel writes into the repo it is judging.
        #
        # A signature record, the chain anchor and the checker registry are all
        # kernel output that lands under .v4/. Counting them means the act of
        # recording an answer moves the tree that answer is judged against, so
        # every repo-scoped claim goes stale the moment any of them is written.
        # Three separate bugs, one shape, and the third arrived the same day the
        # first two were fixed.
        #
        # Human-edited config stays in: config.json is the test claim's oracle
        # and is separately part of the staleness key, and claim_kinds.json
        # decides what gets raised at all.
        # `repo_root` matters here. `kernel_written` gained it the day the
        # manifest started answering by bytes rather than by path name, and two
        # of the three call sites were updated -- `scope` and `test` -- while
        # this one was not. So an install mid-task was invisible to those two
        # and counted here: the same eight files were "not the worker's" to the
        # gates and "the tree moved" to the key those gates hang off, and every
        # repo-scoped claim went stale for work nobody had done.
        from .analysis import subject_files
        if reads and not subject_files.matches(rel, reads):
            return True
        return (kernel_written(rel, repo_root)
                or subject_files.excluded(rel, exclude))

    # One entry per path, and each entry is git's own blob hash: taken from the
    # index for a file nothing has touched, and from `git hash-object` for one
    # that has. Same function on both sides, so staging and committing are
    # no-ops and only bytes move it.
    #
    # What this replaced hashed `HEAD ‖ diff-against-HEAD ‖ untracked`, which is
    # content *relative to a commit* -- and committing changes the commit and
    # empties the diff. Measured on a five-task run: `v4 check` green, `git
    # commit` with not one byte altered, `v4 ship`, and every repo-scoped claim
    # came back STALE. The ordinary flow could not converge.
    #
    # It is also cheaper: a clean file is never read. 51ms against 69ms on this
    # repo's 1,929 files.
    state = tree_state(root)
    if not state:
        # `tree_state` returns `{}` where git cannot answer, and hashing nothing
        # gave `sha256("")` -- a fixed constant, identical for every such
        # directory and unmoved by any edit. Measured in a temp dir: adding a
        # file left the digest equal to `hashlib.sha256().hexdigest()`. Every
        # repo-scoped claim there keys on a value that can never move, which is
        # a staleness key that cannot go stale.
        #
        # `tree_state` names this case and says "the one caller that must still
        # work there says so" -- this is that caller, and it did not.
        # `lifecycle._files_in_scope` already answers it the same way: walk the
        # tree, because a directory git does not manage is a fixture or a
        # scratch dir rather than a repo under judgment, and returning nothing
        # there would silently make everything about it constant.
        state = {}
        for f in sorted(root.rglob("*")):
            if not f.is_file() or "/.git/" in f"/{f}":
                continue
            try:
                state[str(f.relative_to(root))] = _sha_bytes(f.read_bytes())
            except OSError:
                continue

    h = hashlib.sha256()
    for rel in sorted(state):
        if _excluded(rel):
            continue
        h.update(rel.encode())
        h.update(b"\x1f")
        h.update(state[rel].encode())
        h.update(b"\x1e")
    return h.hexdigest()
