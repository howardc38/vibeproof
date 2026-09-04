"""Putting the rules into a repo that wants them.  SPEC.md §8.

`v4 init` wrote a config and two empty registries, and stopped. Everything that
actually judges anything -- every checker, detector, fixture set and lens --
stayed in this repo, and an adopter was expected to copy files by hand and
then run `v4 register` once per checker with four arguments each. Measured in a
sandbox: a freshly initialised repo has three kinds and zero lenses, so layers
2 and 3 of a four-layer design were simply absent, and nothing said so.

Hand-picking was never a real option either. Choosing among them means
knowing which ones have a subject in your repo, and that is exactly the question
the detector layer answers automatically, per task, at derive time. A person
reading filenames cannot do it and should not be asked to.

So: install everything, let the detectors decide what fires. Two things this
does NOT do, both deliberate:

  it does not skip the gate     every checker still has to fail on its red
                                fixtures and pass on its green ones before it
                                enters the registry. Installing in bulk must
                                not be a way in for a checker that would be
                                refused one at a time.

  it does not install this framework's own    `dead-wiring` reads
                                kernel/ledger.py and `spec-coverage` resolves
                                this project's SPEC.md against kernel/cli.py.
                                In another repo they exit 4 or crash. They say
                                so themselves, in claim_kinds.json.
"""

import json
import shutil
from pathlib import Path

from . import config as config_mod
from . import layout

from . import hashing

#: A kind may declare that it is about this framework rather than about whatever
#: repo it is installed into.  Read from `claim_kinds.json` rather than listed
#: here, so a new one arrives with its checker instead of in this file.
FRAMEWORK_ONLY = "framework"

#: A kind whose checker cannot answer anything until the repo declares a file it
#: names -- `layer-boundary` needs `.v4/layers.json`, `control-plane-budget`
#: needs a ceiling. Installing one into a repo that has not declared it is not a
#: rule waiting to be useful; it is a claim that returns UNSUPPORTED on every
#: task forever, and the only way past is a signature per kind that nothing
#: prompts anyone to write.
#:
#: Measured on a first adoption: two of the twelve claims raised on the first
#: task were these, and neither could ever be answered. So they are held back
#: until the file exists, listed by `install` and by `doctor` as available, and
#: turning one on is one deliberate edit rather than a permanent blocker.
NEEDS_DECLARED = "declared"


def _declared(root: Path, needs: str) -> bool:
    """Does this repo hold the file a kind says it needs?

    Literal for `.v4/layers.json` and `.v4/control_plane_budget.json`, and a
    glob for the one file in `.v4/` whose name is the repo's: the facts table is
    `facts.<root-name>.json`, so `facts-coverage` could not name it and was held
    back on a repo that has one. A held-back kind is invisible -- it does not
    raise, so nothing reports that it is not raising -- which is the failure that
    kind exists to catch, one level out.

    A pattern rather than resolving `<repo>` against the directory name: the
    loader in `kernel/facts.py` already falls back to the first `facts*.json`
    when the preferred name is absent, and a second rule for the same file here
    would disagree with it the first time either moved.
    """
    if not needs:
        return False
    if any(ch in needs for ch in "*?[<"):
        pattern = needs.replace("<repo>", "*")
        return any(root.glob(pattern))
    return (root / needs).exists()


#: Where a checker's fixtures land in the adopter.
#:
#: In this repo they live at `tests/fixtures/`, which is right here and wrong
#: there: `tests/` belongs to the adopter, and a red fixture is deliberately
#: broken Python. Measured on a real adoption -- `pytest` collected them and
#: stopped on 78 collection errors before running one of the repo's own tests.
#:
#: Everything else this framework writes goes under `.v4/` or into a directory
#: it owns outright. This was the one exception, and it put unparsable files in
#: the one directory an adopter's test runner is guaranteed to walk.
ADOPTER_FIXTURES = ".v4/fixtures"


def fixture_dest(rel: str) -> str:
    """Where `tests/fixtures/scope` goes in an adopter: `.v4/fixtures/scope`."""
    name = Path(rel).name
    return f"{ADOPTER_FIXTURES}/{name}"


def source_root() -> Path:
    """Where this framework lives -- the repo containing `kernel/`."""
    return Path(__file__).resolve().parent.parent


def installable_kinds(src: Path, dst: Path = None) -> tuple:
    """(kinds to install, [(name, why) for the ones held back]).

    `dst` is the adopting repo. Without it, a `declared` kind is held back on
    the assumption its file is absent -- the safe direction, since a kind that
    is missing announces itself and one that blocks every task does not.

    Three rules hold a kind back, and the third one used to live in
    `cmd_install`: nine lines that popped kinds out of the table this function
    had just returned. `held` is the list an adopter reads to find out what
    they did not get, so a holdback the list does not carry is a kind that
    vanishes silently -- and `installable_kinds` is the name of the question,
    not `installable_kinds_except_for_the_ones_the_caller_also_removes`.
    """
    kinds = json.loads((src / config_mod.CLAIM_KINDS).read_text())
    keep, held = {}, []
    for name, spec in sorted(kinds.items()):
        if spec.get("applies_to") == FRAMEWORK_ONLY:
            held.append((name, spec.get("applies_to_why", "declared framework-only")))
            continue
        if spec.get("applies_to") == NEEDS_DECLARED:
            needs = spec.get("needs", "")
            if dst is None or not _declared(Path(dst), needs):
                # `applies_to_why` is where the kind says what is actually lost
                # by holding it back, and this branch built its own sentence and
                # ignored the field -- so the only two kinds that reach here
                # stated a reason nothing would ever print.
                why = spec.get("applies_to_why", "")
                held.append((name, f"needs {needs}, which this repo has not "
                                   f"declared. Write it and re-run `v4 install` "
                                   f"— until then this kind would raise a claim "
                                   f"nothing can answer, on every task."
                                   + (f" ({why})" if why else "")))
                continue
        keep[name] = {k: v for k, v in spec.items()
                      if k not in ("applies_to", "applies_to_why", "needs")}
    return _drop_unreadable(src, dst, keep, held)


def _drop_unreadable(src: Path, dst: Path, keep: dict, held: list) -> tuple:
    """Hold back a kind whose checker can read no file in this repo.

    Asked before anything is copied, and only over the repo's own files:
    `copy_files` puts 83 Python files into the tree, so asking afterwards
    whether the repo has Python answers about the framework, and every
    Python-AST checker installs itself into a Go repo on the strength of its
    own source.

    This is the whole of the multi-stack answer. A Go tree has no `**/*.py`, so
    the Python-AST checkers are held back by the same machinery that holds back
    `layer-boundary` for an undeclared layer file -- rather than installed and
    returning PASS over source they never parsed.
    """
    if dst is None:
        return keep, held
    from .analysis import subject_files
    reg = json.loads((src / config_mod.CHECKERS).read_text())
    theirs = repo_own_files(Path(dst))
    drop = set()
    for name, spec in sorted(keep.items()):
        reads = (reg.get(spec.get("checker")) or {}).get("reads") or []
        if reads and not subject_files.readable(theirs, reads):
            drop.add(name)
            shown = ", ".join(reads[:3]) + ("…" if len(reads) > 3 else "")
            # Short, unlike the `needs` sentence above it: fifteen kinds reach
            # here on a Go repo, and fifteen copies of a three-line explanation
            # is a wall an adopter skips rather than a list they read.
            held.append((name, f"reads {shown}, and this repo has none"))
    return {n: s for n, s in keep.items() if n not in drop}, held


def repo_own_files(dst: Path) -> list:
    """The adopting repo's own files, before this framework lands in it.

    `not_this_framework` already knows which paths belong to the framework
    rather than to whoever installed it -- `kernel/`, `checkers/`, `detectors/`,
    `hooks/`, `.v4/`. Asking "does this repo contain Python" after `copy_files`
    has put 83 Python files into it answers about the framework, and every
    Python-AST checker then installs itself into a Go repo on the strength of
    its own source.
    """
    from .facts import not_this_framework
    theirs = not_this_framework(dst)
    out = []
    for p in dst.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(dst)
        if ".git" in rel.parts:
            continue
        if theirs(rel):
            out.append(str(rel))
    return out


#: What this framework put in the repo, and the bytes it put there. Written by
#: `copy_files`, read by the next `install` to tell an untouched copy from one
#: the repo has edited.
MANIFEST = ".v4/installed.json"

#: What `copy_files(dry=True)` reports, and the distinction that made it worth
#: writing. `installed.json` answers "has this repo edited the file"; it cannot
#: answer "has the framework moved on since", because it records what was
#: shipped and not what is shipped now. So an adopter three weeks behind looked
#: exactly like one installed this morning -- and the only mechanism that would
#: have caught it is `.github/monitor/PROMPT.md` §3, a document that is itself
#: one of the files going stale.
#:
#: Measured on the reference adopter the day this was written: 17 behind,
#: 8 missing, and two of the seventeen were the monitor brief, whose §1 pointed
#: at a `facts-current` claim that nothing has raised since it was renamed.
CURRENT = "current"          # bytes match the framework
BEHIND = "behind"            # untouched here, and the framework has moved on
MISSING = "missing"          # the framework ships it and this repo has none
YOURS = "yours"              # this repo edited it; `install` leaves it alone
#: Shipped by an earlier install, unedited here, and not shipped any more.
#: There was no such status, so a program the framework had stopped shipping
#: was carried forward in the manifest for ever -- the record kept saying we
#: own this file, about a file we would never send again.
RETIRED = "retired"

#: Where a program lives. Retirement is limited to these: a fixture set is a
#: place an adopter adds cases of their own, and neither it nor a lens is
#: answered by "the framework stopped shipping it".
PROGRAM_DIRS = ("checkers/", "detectors/")


def _same(s: Path, d: Path) -> bool:
    """Byte-identical, whether the path is a file or a directory."""
    if s.is_file() != d.is_file():
        return False
    if s.is_file():
        return hashing.file_sha(s) == hashing.file_sha(d)
    sf = {f.relative_to(s).as_posix(): f for f in s.rglob("*") if f.is_file()}
    df = {f.relative_to(d).as_posix(): f for f in d.rglob("*") if f.is_file()}
    return set(sf) == set(df) and all(
        hashing.file_sha(sf[k]) == hashing.file_sha(df[k]) for k in sf)


def _manifest(dst: Path) -> dict:
    try:
        return json.loads((dst / MANIFEST).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _retired_here(src: Path, rel: str, never_here: set) -> bool:
    """Is this program one an adopter is never getting again?

    Two permanent answers and one temporary one, and only the permanent ones
    retire a file:

    * the framework has no such source any more -- a kind trim deleted it;
    * every kind it raises is `applies_to: framework`, which is a decision
      about adopters and not about this repo's contents;
    * anything else. A kind held back because the repo has no file its checker
      reads, or because the checker was refused, comes back the day that
      changes -- and `test_a_kind_held_back_does_not_lose_its_checker_s_record`
      is about exactly that file keeping its record, so `scope` does not report
      it as the worker rewriting what judges them.

    The first version did not separate these and deleted the third case too,
    which traded one real repair for a documented one.

    The second rule is asked of detectors only. `copy_files` puts a checker
    only for a kind it is installing, so a framework-only kind's checker was
    never shipped and cannot be in the manifest to retire; a checker that *was*
    shipped and later became framework-only is covered by the first rule the
    day its source moves, and by nothing until then. That is a narrower
    promise than "every program", and it is the one the measured case needs --
    the five stranded files were all detectors, which `derive` globs off the
    directory rather than addressing through the kind table.
    """
    s = src / rel
    if not s.exists():
        return True
    if not never_here or not rel.startswith("detectors/"):
        return False
    raised = _kinds_raised_by(s)
    return bool(raised) and raised <= never_here


def copy_files(src: Path, dst: Path, kinds, dry=False) -> list:
    """Everything an adopter gets a copy of, and nothing it shares.

    Four kinds of thing leave here: the programs (`checkers/`, `detectors/`,
    `hooks/`), their fixture sets (`.v4/fixtures/`, renamed by `fixture_dest`),
    the reviewer layer (`.v4/lenses/*.json`) and the operating documents
    (`.claude/`, `.github/monitor/*.md`). SPEC.md §8.8's table named the first
    and the last and not the two in the middle -- while `hashing.SHIPPED_DIRS`
    lists the fixtures and the lenses as clones answered by hash, so the
    framework knew they were copies and the table a reader consults did not.

    Returns [(relative path, "written"|"updated"|"yours")], or with `dry`,
    [(relative path, CURRENT|BEHIND|MISSING|YOURS)] and not one byte written.

    `dry` is a flag here rather than a second function, because the value of
    this answer is entirely in *which paths* it walks -- the registry, the
    held-back kinds, the conditional detectors' own fixture sets, the lenses,
    the hooks, the monitor brief, `.claude/`. A second list of those would be a
    second truth, and this repo has removed that shape more than once.

    Never overwriting was half right. It is right about a file the repo has
    edited: silently restoring the original answers that repo's claims with a
    program its author did not write. It was wrong about every other file, and
    the consequence is that **no fix ever reaches an adopter** -- a whole day of
    them sat undeliverable behind one `if d.exists()`.

    Measured: `bash_guard` in an adopting repo could not import `kernel`, so the
    one hook whose reason for existing is `sed -i .v4/config.json` allowed that
    command, silently. Fixing it here changed nothing there, because `install`
    saw the file and kept it.

    So the manifest records what was shipped. Identical to what we shipped means
    nobody touched it, and it is updated. Different means somebody said
    something, and it is left alone and named.
    """
    reg = json.loads((src / config_mod.CHECKERS).read_text())
    wanted = {spec["checker"] for spec in kinds.values() if spec.get("checker")}
    was = _manifest(dst)
    now, out = {}, []

    def stamp(rel_dst: Path):
        """Record what *we* put there -- never what the repo changed it to.

        The first version stamped the bytes on disk in every branch, including
        the one that had just decided the repo had edited the file. So the next
        run compared the edit against itself, found them equal, called the file
        untouched, and overwrote it. A second `v4 install` silently destroying
        an adopter's edit is worse than never updating at all, which is the
        behaviour this replaced.
        """
        d = dst / rel_dst
        files = [d] if d.is_file() else \
            [f for f in sorted(d.rglob("*")) if f.is_file()]
        for f in files:
            now[f.relative_to(dst).as_posix()] = hashing.file_sha(f)

    def carry(rel_dst: Path):
        """Keep the shipped sha for a path the repo has made its own."""
        d = dst / rel_dst
        files = [d] if d.is_file() else \
            [f for f in sorted(d.rglob("*")) if f.is_file()]
        for f in files:
            key = f.relative_to(dst).as_posix()
            if key in was:
                now[key] = was[key]

    def edited(rel_dst: Path) -> bool:
        """Has the repo changed anything under this path since we shipped it?"""
        d = dst / rel_dst
        files = [d] if d.is_file() else [f for f in d.rglob("*") if f.is_file()]
        for f in files:
            key = f.relative_to(dst).as_posix()
            if key not in was or was[key] != hashing.file_sha(f):
                return True
        return False

    def put(rel_src: Path, rel_dst: Path = None):
        rel_dst = rel_dst or rel_src
        s, d = src / rel_src, dst / rel_dst
        if dry:
            # Order matters. An edited file can also be behind, and saying so
            # would be true and useless: `install` will not touch it either way,
            # so what the reader needs is the reason it is being left alone.
            if not d.exists():
                out.append((rel_dst.as_posix(), MISSING))
            elif edited(rel_dst):
                out.append((rel_dst.as_posix(), YOURS))
            else:
                out.append((rel_dst.as_posix(),
                            CURRENT if _same(s, d) else BEHIND))
            return
        if d.exists():
            if edited(rel_dst):
                out.append((rel_dst.as_posix(), "yours"))
                carry(rel_dst)
                return
            (shutil.rmtree if d.is_dir() else Path.unlink)(d)
            (shutil.copytree if s.is_dir() else shutil.copy2)(s, d)
            out.append((rel_dst.as_posix(), "updated"))
            stamp(rel_dst)
            return
        d.parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if s.is_dir() else shutil.copy2)(s, d)
        out.append((rel_dst.as_posix(), "written"))
        stamp(rel_dst)

    for cid in sorted(wanted):
        entry = reg.get(cid)
        if not entry:
            continue
        put(Path(entry["path"]))
        if entry.get("fixtures") and (src / entry["fixtures"]).is_dir():
            put(Path(entry["fixtures"]), Path(fixture_dest(entry["fixtures"])))
    # Detectors are not addressed by the kind table -- `derive` globs the
    # directory -- so the whole directory goes, minus the ones whose only
    # kind stayed behind.
    # Whatever `installable_kinds` did not keep, whatever its reason. Deriving
    # it from `applies_to == FRAMEWORK_ONLY` a second time meant that adding a
    # holdback reason in one function left this one shipping the detector for a
    # kind the other had just refused to register -- `derive` globs the
    # directory, so the claim gets raised anyway and nothing answers it.
    held_kinds = set(json.loads(
        (src / config_mod.CLAIM_KINDS).read_text())) - set(kinds)
    for det in sorted((src / "detectors").glob("*.py")):
        if det.name.startswith("_"):
            continue
        raised = _kinds_raised_by(det)
        if raised and raised <= held_kinds:
            continue
        put(Path("detectors") / det.name)
    # A conditional detector has its own fixture set and its own gate. Copying
    # the detector without them leaves `v4 register-detector` with nothing to
    # run, and an unregistered detector is skipped by `derive` -- so the repo
    # would look installed and raise none of those claims.
    for entry in json.loads((src / ".v4" / "detectors.json").read_text()).values():
        if entry.get("fixtures") and (src / entry["fixtures"]).is_dir():
            put(Path(entry["fixtures"]), Path(fixture_dest(entry["fixtures"])))
    for lens in sorted((src / ".v4" / "lenses").glob("*.json")):
        put(Path(".v4/lenses") / lens.name)
    # Layer 3's reviewers and layer 1's doctrine both arrive as files; the hooks
    # are the one part that needs a line in the agent's own settings, so the
    # template is copied and named rather than silently activated.
    for hook in sorted((src / "hooks").glob("*.py")):
        put(Path("hooks") / hook.name)
    # `.github/monitor/` is pointed at by a claim that is `applies_to: always`.
    # `checkers/sweep_current.py` tells the operator that `PROTECT` -- sorry,
    # `.github/monitor/PROMPT.md` -- is what to paste into the session that does
    # the sweep, and nothing here copied it: in every adopter that FAIL text
    # named a file `v4 install` had never put there. `.github/**` is on
    # `PROTECTED_DEFAULT` precisely because these two files are the contract.
    for doc in sorted((src / ".github" / "monitor").glob("*.md")):
        put(Path(".github/monitor") / doc.name)
    tmpl = src / ".claude" / "settings.template.json"
    if tmpl.is_file():
        put(Path(".claude/settings.template.json"))
    # The operating loop, which is as much a part of this system as the kernel.
    #
    # These stayed behind for as long as `install` existed, so an adopter got 23
    # checkers, 9 detectors, 3 hooks and no way to run any of it: no `/run`, and
    # none of the four roles SPEC §12.5 names. The only way to work in such a
    # repo was to write the loop into a prompt by hand -- which means copying
    # `task-splitter`'s splitting rule, `worker`'s "you may not ship yourself",
    # and the doctrine's constraints into a fourth place, where they drift.
    #
    # `put` leaves an edited file alone and names it, so a repo that has written
    # its own `worker` keeps it.
    for kind in ("agents", "commands"):
        d = src / ".claude" / kind
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            put(Path(".claude") / kind / f.name)
    # Carry forward what this run did not touch. `now` is built only from the
    # paths this install decided about, and a kind held back -- because the repo
    # has no file its checker reads, or because it was refused -- is not among
    # them. Its checker is still on disk from the last install, and dropping the
    # record turned it into somebody's edit: `scope` reported
    # `checkers/bundle_secret.py` as the worker changing what judges it, about a
    # file byte-identical to the one this framework shipped.
    #
    # Only where the bytes still match what was recorded. An edited file fails
    # that and is reported, which is the whole point; a deleted one is gone from
    # the manifest, which is also right.
    # What this run decided to ship, in both modes. `now` is stamped only on
    # the writing path -- `put` returns early under `dry` -- so a retirement
    # rule that read `now` called every program retired under `--check`, which
    # is the whole registry. Measured against the reference adopter's real
    # manifest before this line existed: 20 detectors reported retired, of
    # which 5 were.
    try:
        never_here = {n for n, s in json.loads(
            (src / config_mod.CLAIM_KINDS).read_text()).items()
            if s.get("applies_to") == FRAMEWORK_ONLY}
    except (OSError, json.JSONDecodeError):
        never_here = set()
    shipped = {rel for rel, _status in out}
    for rel, sha in was.items():
        if rel in now or rel in shipped:
            continue
        f = dst / rel
        if not (f.is_file() and hashing.file_sha(f) == sha):
            continue
        if rel.startswith(PROGRAM_DIRS) and rel.endswith(".py") \
                and _retired_here(src, rel, never_here):
            # Shipped once, not shipped now, and untouched since: this
            # framework put it there and no longer would. `install` only ever
            # added and updated, so a kind trim left its detector on disk with
            # the manifest still claiming we own it -- and `--check` had no
            # status that could say otherwise. Measured on the reference
            # adopter: five `always_*` detectors stranded by a 28-to-16 trim,
            # each raising a kind `derive` refuses, on every task since.
            #
            # Only programs. A fixture set is a place an adopter adds cases,
            # and a lens is a document somebody may be mid-edit on; neither is
            # answered by "we stopped shipping it". The measured harm is a
            # program that still runs.
            out.append((rel, RETIRED if dry else "retired"))
            if not dry:
                f.unlink()
            continue
        now[rel] = sha
    if dry:
        # The one write outside `put`, and it slipped past the early return
        # there: a `--check` against a repo with no manifest created one, so
        # asking the question changed the answer. Caught by the test that says
        # dry writes nothing.
        return out
    (dst / MANIFEST).parent.mkdir(parents=True, exist_ok=True)
    (dst / MANIFEST).write_text(json.dumps(now, indent=2, sort_keys=True) + "\n")
    return out


#: Every checker and detector imports `kernel` -- measured: 24 of the 25 files
#: an install copies.  They are run as subprocesses by the kernel that imports
#: them back, so in this repo the path is already right and nothing said what
#: an adopter needs.  This is what an adopter needs.
LAUNCHER = """#!/usr/bin/env bash
# Written by `v4 install`. The checkers copied into this repo import `kernel`,
# which lives in the framework rather than here, so `python3 -m kernel.cli`
# alone would fail on the first command with ModuleNotFoundError.
V4_HOME="${{V4_HOME:-{src}}}"
if [ ! -d "$V4_HOME/kernel" ]; then
  echo "v4: no kernel/ under $V4_HOME -- set V4_HOME to where the framework lives" >&2
  exit 2
fi
exec env PYTHONPATH="$V4_HOME${{PYTHONPATH:+:$PYTHONPATH}}" \\
     python3 -m kernel.cli --repo "$(git rev-parse --show-toplevel)" "$@"
"""


#: Where the framework lives, written once and read by everything that is not
#: `bin/v4`. The launcher carried this path and nothing else did, so a hook --
#: which the coding agent runs directly as `python3 hooks/x.py`, with no
#: launcher in the picture -- could not find `kernel`. Measured: `bash_guard`
#: imports `kernel.analysis.shell_command`, that import raised
#: ModuleNotFoundError in every adopting repo, and the `except Exception:
#: print("{}")` around it turned a dead guard into a silent allow. The one hook
#: whose whole reason for existing is `sed -i .v4/config.json` let it through.
HOME_FILE = ".v4/home"


def stamp_generated(dst: Path, rels) -> None:
    """Record files `install` writes itself, beside the ones it copies.

    The manifest answered "did we put this here" for copied files only, and
    `install` also generates `.v4/claim_kinds.json`, `.v4/detectors.json`,
    `.v4/installed.json`, `bin/v4` and `CLAUDE.md`. Those showed up in the next
    task's `git diff` with nobody's name on them, and `scope` reported them as
    the worker changing what judges it -- three of the eight paths it flagged on
    a real run, none of them anybody's edit.

    `.v4/config.json` is deliberately not in the list. `install` appends one
    entry to it and does not own the rest; a worker who edits a threshold there
    should still be reported.
    """
    m = _manifest(dst)
    for rel in rels:
        f = dst / rel
        if f.is_file():
            m[Path(rel).as_posix()] = hashing.file_sha(f)
    (dst / MANIFEST).write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")


def write_launcher(dst: Path, src: Path) -> Path:
    """`./bin/v4` in the adopter, pointing at wherever the framework lives."""
    path = dst / "bin" / "v4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(LAUNCHER.format(src=src))
    path.chmod(0o755)
    (dst / HOME_FILE).write_text(str(src) + "\n")

    # And keep it out of git. It holds one machine's absolute path, so on
    # anybody else's clone it names a directory that is not there -- and the
    # hooks' answer to "I cannot reach the kernel" is to allow the write. The
    # launcher carries the same path and fails loudly; this one fails quietly,
    # which is why it is the one that must not travel. Appended rather than
    # written, because `.gitignore` is the adopter's file.
    ignore = dst / ".gitignore"
    have = ignore.read_text(encoding="utf-8") if ignore.is_file() else ""
    if not any(l.strip() == HOME_FILE for l in have.splitlines()):
        with ignore.open("a", encoding="utf-8") as fh:
            if have and not have.endswith("\n"):
                fh.write("\n")
            fh.write(f"\n# One machine's path to the framework. `v4 install`\n"
                     f"# writes it; a clone must write its own.\n{HOME_FILE}\n")
    return path


def _kinds_raised_by(detector: Path) -> set:
    """Every claim kind this detector file can print.

    Two spellings, because detectors use both. `V4-CLAIM: kind=dead-wiring` is
    literal; `f"V4-CLAIM: kind={KIND} ..."` puts the name in a module constant,
    and reading only the literal form returned the empty set for every detector
    written that way -- `runtime_proof.py`, `surface_proof.py` and the rest.

    That empty set is not harmless here: `copy_files` holds a detector back when
    `raised and raised <= held_kinds`, and an empty `raised` is falsey, so a
    detector whose only kind was held back would ship anyway. It reads as "this
    detector raises nothing", which is the one answer that is never true.
    """
    import re
    try:
        src = detector.read_text(encoding="utf-8")
    except OSError:
        return set()
    return (set(re.findall(r"kind=([a-z0-9-]+)", src))
            | set(re.findall(r"""^KIND\b[^"'\n]*["']([a-z0-9-]+)["']""", src, re.M)))


def write_facts(dst: Path) -> tuple:
    """A first facts *draft*, read out of the repo.  Returns (path, guessed rows).

    FACTS.md refuses to ship a template and is right to: a table nobody chose is
    a vocabulary nobody chose. But what an adopter actually got was no file at
    all, so every detector fell back to a generic vocabulary and found less --
    `doctor` warns about it and nothing produced the thing it was warning about.

    So: not a table, a draft, under the name FACTS.md already documents. It is
    written as `.json.draft` and **is expected not to validate** -- `validate`
    refuses an empty `auth_decision`, and a proposal cannot answer whether this
    repo decides who may do what. That refusal is the point: asked properly of
    this framework's own repo, the answer was five rules nobody had written
    down. Renaming it over the real name is the person's move, not this one's.

    Returns without writing if a table or a draft is already here. A table
    somebody pruned is exactly what this must never overwrite.
    """
    from . import facts as facts_mod
    # The repo's name, not the checkout's directory. `layout.repo_name` asks git
    # which repo this is, so a worktree does not get a draft under its own
    # directory name -- which the loader would then not prefer, and which
    # `doctor` would report as a table under the wrong name. Three call sites
    # were fixed for this and this fourth one was missed; a failure appearing
    # twice means the first repair was made in the wrong place.
    path = dst / ".v4" / f"facts.{layout.repo_name(dst)}.json.draft"
    if any((dst / ".v4").glob("facts*.json")) or path.exists():
        return None, 0
    table = facts_mod.propose(dst)
    n = sum(len(table.get(k) or []) for k in
            ("outbound_write", "outbound_read", "auth_decision"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(table, indent=2, ensure_ascii=False) + "\n")
    return path, n


def write_layers(dst: Path) -> tuple:
    """A first `.v4/layers.json` *draft*.  Returns (path, edge count).

    `layer-boundary` is `applies_to: declared` and nothing proposed the file it
    declares, so the only mechanical architecture rule V4 has could not be
    turned on without authoring the whole declaration from blank. `write_facts`
    above had that shape and this is the same repair.

    Reads the facts draft this function is called after, because `entry` comes
    from `entrypoint_globs` and a data layer only from a declared `dal_globs`
    -- neither is guessed here. Returns without writing if a declaration or a
    draft is already there: one somebody pruned is what this must never
    overwrite.
    """
    from .analysis import layers as layers_mod
    path = dst / ".v4" / "layers.json.draft"
    if (dst / ".v4" / "layers.json").is_file() or path.exists():
        return None, 0
    facts = {}
    for candidate in sorted((dst / ".v4").glob("facts*.json*")):
        try:
            facts = json.loads(candidate.read_text(encoding="utf-8"))
            break
        except (OSError, ValueError):
            continue
    try:
        cfg = json.loads((dst / layout.CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    from .facts import not_this_framework
    draft = layers_mod.propose(
        dst,
        entrypoint_globs=facts.get("entrypoint_globs") or (),
        dal_globs=facts.get("dal_globs") or (),
        subject={"params": {"derive_exclude": cfg.get("derive_exclude") or []}},
        theirs=not_this_framework(dst))
    if draft is None:
        return None, 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(draft, indent=2, ensure_ascii=False) + "\n")
    return path, len(draft["allow"])


#: A worked pin, dropped into the adopter once.
#:
#: `design-pins` is generic, it installs, it is repo-scoped and it verifies that
#: every `<!-- pinned: file::symbol -->` in every tracked markdown still
#: resolves. Nothing ever told an adopter to write one, so the whole mechanism
#: sat installed and idle -- and `spec-coverage` and `dead-wiring`, the two
#: checkers that hold documents to code, are `applies_to: framework` and do not
#: install at all. So an adopter's "changed the code, did not change the doc"
#: had exactly one home left: a lens item, in the layer whose trigger this same
#: session found missing.
#:
#: A convention, not a kernel change. `v4 doctrine` writes CLAUDE.md; this
#: writes one line into a file the adopter owns, once, and never again.
PIN_EXAMPLE = """
<!-- Written once by `v4 install`, and yours from here.

     `design-pins` reads every tracked markdown for lines of this shape and
     fails when the symbol is gone. That is the whole mechanism: a document
     that names code, and a program that notices when the code moves away
     from it.

     Point one at something this document actually describes, then delete
     this comment. -->
<!-- pinned: {example} -->
"""


def write_pin_example(dst: Path) -> Path | None:
    """One pin in the adopter's architecture doc.  Returns the path, or None.

    Never overwrites and never invents a symbol: if there is no obvious file to
    point at, this writes nothing rather than a pin that fails on the first run.
    """
    import ast
    doc = None
    for name in ("ARCHITECTURE.md", "docs/ARCHITECTURE.md", "README.md"):
        p = dst / name
        if p.is_file():
            doc = p
            break
    if doc is None or "pinned:" in doc.read_text(encoding="utf-8", errors="replace"):
        return None

    from .facts import tracked_source_files, not_this_framework
    theirs = not_this_framework(dst)
    # `.py` only: the example this writes into a document is a `path::symbol`
    # pin, and the pin checker resolves it with Python's `ast`.
    for path in tracked_source_files(dst, suffixes=(".py",)):
        rel = path.relative_to(dst)
        if not theirs(rel):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)) and not node.name.startswith("_"):
                with doc.open("a", encoding="utf-8") as fh:
                    fh.write(PIN_EXAMPLE.format(
                        example=f"{rel.as_posix()}::{node.name}"))
                return doc
    return None


#: A value that means "nobody answered", as opposed to "the repo said this".
#: `detector: null` is what the framework itself shipped for six kinds before
#: they had one; an adopter carrying it has not made a decision this must
#: preserve.
_UNANSWERED = (None, "", [], {})


def write_kinds(dst: Path, kinds) -> None:
    """Merge into whatever the repo already declares; never drop its edits.

    Per field, not per kind. `setdefault(name, spec)` left an existing kind
    untouched forever, and that is not "never drop its edits" -- it also never
    repairs a field the adopter never set. Measured on the reference adopter:
    `dal-write`, `design-pins`, `signature-change` and `webhook-replay` shipped
    with `detector: null` back when none of them had a detector; after the
    detectors were written and installed, all four still read `null`, and would
    have on every future install. Four checkers copied into a repo that could
    never raise a claim for any of them.

    So a field the adopter's copy does not have, or holds an unanswered value
    for, is filled in. A field it holds a real value for is left alone, which
    is the edit the docstring was about.
    """
    path = dst / config_mod.CLAIM_KINDS
    have = json.loads(path.read_text()) if path.is_file() else {}
    for name, spec in kinds.items():
        if name not in have:
            have[name] = spec
            continue
        mine = have[name]
        for field, value in spec.items():
            if mine.get(field) in _UNANSWERED and value not in _UNANSWERED:
                mine[field] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(have, indent=2, ensure_ascii=False,
                               sort_keys=True) + "\n")
