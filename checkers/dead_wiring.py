#!/usr/bin/env python3
"""Declared and never wired.  SPEC.md §4.

The failure this answers has happened here four times and was found by a person
each time, never by the system:

    cost_observation      a ledger table with no code writing to it
    detector_sha          a column written on every claim and read by nothing
    --out                 a payload the kernel parses and then drops
    read_observation      a whole table whose only stated purpose was to
                          measure whether collecting it had been worth it

None of these is a bug in the ordinary sense. Everything runs, every test
passes, and the schema reads as complete. What is missing is the wire, and a
missing wire is invisible in exactly the way a missing feature is not.

So: every table in the ledger schema is asked who writes it and who reads it,
and every threshold in the config is asked who reads it. Something on one end
and nothing on the other is the finding. It is a coarse rule -- a name spelled
differently in the two places reads as dead -- and coarse is the right side to
err on, because the cost of a false positive is a minute and the cost of a miss
is a table that looks like protection for a year.

Tables, not columns -- and `detector_sha` in the list above is a column, so
this checker does not in fact answer one of the four failures it names. That is
deliberate, and measured rather than assumed. A column-level pass was written
and run against two real states of this repo:

    read = SELECT[^;]*<col>      spans newlines, so a `SELECT` anywhere in the
                                 file marks every column below it as read. It
                                 reported nothing, ever, including on the commit
                                 where `attempt.worktree` really was dead.
    read = SELECT[^\n;]*<col>    bounded to one line, so it misses SQL split
                                 across string literals. It reported
                                 `cost_observation.wall_ms` as dead, and
                                 `kernel/cli.py` reads it in
                                 `SUM(COALESCE(wall_ms,0)) ... FROM
                                 cost_observation` with the SELECT on the line
                                 before.

A table name is distinctive: it appears as `FROM <name>`. A column name is not,
and the two obvious tunings fail in opposite directions. A checker that reports
`wall_ms` teaches people to stop reading this one, which costs more than the
column it would have caught. So the gap stays open and stays named, and
`attempt.worktree` -- found by hand on 2026-08-14, the same shape as
`detector_sha` -- is the standing evidence that it is worth closing properly.

Exit: 0 everything has both ends | 1 something has one | 4 no kernel to read
| >=5 broke.
"""

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel import baseline  # noqa: E402

KIND = "dead-wiring"

TABLE = re.compile(r"CREATE TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?(\w+)", re.I)

#: A migration directory is a history, not a state. `reference_runs` was
#: created in one file and dropped in another, and reading only the creates
#: reported a table nobody reads -- correctly, and uselessly, because the table
#: is gone. Whatever was dropped last wins.
DROPPED = re.compile(r"DROP TABLE\s+(?:IF\s+EXISTS\s+)?[\"`]?(\w+)", re.I)

def code_only(src: str) -> str:
    """The source with comments and docstrings blanked, structure intact.

    A name mentioned in prose is not a name anybody reads, and this checker
    proved it on itself: a comment here explaining how `remerge_max` had slipped
    past made `remerge_max` look wired, so the check passed by talking about the
    thing it was meant to find.

    Blanked rather than removed, because the searches that follow are written
    against real call syntax -- `insert(conn, "task"` has to survive.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src

    lines = src.splitlines()
    blank = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                blank.update(range(first.lineno - 1, (first.end_lineno or first.lineno)))
    out = []
    for i, line in enumerate(lines):
        if i in blank:
            out.append("")
            continue
        # Strip a trailing comment, but not a `#` inside a string literal.
        if "#" in line:
            in_s, quote, cut = False, "", None
            for j, ch in enumerate(line):
                if in_s:
                    if ch == quote:
                        in_s = False
                elif ch in "\"'":
                    in_s, quote = True, ch
                elif ch == "#":
                    cut = j
                    break
            line = line[:cut] if cut is not None else line
        out.append(line)
    return "\n".join(out)



def sql_code_only(src: str) -> str:
    """SQL with its comments blanked.  The same rule as `code_only`, one dialect over.

    `code_only` knows Python's `#` and its docstrings. A migration directory is
    SQL, and its comments are `--` and `/* */` -- measured, a commented-out
    `CREATE TABLE` and a note about one both came back as tables named `IF` and
    `with`. A name in prose is not a declaration in either language.
    """
    import re as _re
    src = _re.sub(r"/\*.*?\*/", " ", src, flags=_re.S)
    return "\n".join(line.split("--", 1)[0] for line in src.splitlines())


def _facts(root: Path) -> dict:
    """The adopting repo's declared vocabulary, or `{}`.

    `facts` is where a repo says where its own things live -- `dal_globs` for
    the layer that talks to a database, `entrypoint_globs` for what starts.
    This checker used to look only at `kernel/` and `checkers/` and read the
    schema out of `kernel/ledger.py`, so it answered about this framework and
    was marked `applies_to: framework` for that reason. The question it asks --
    is anything declared at one end and absent at the other -- is not about this
    framework at all, and every adopter has the same question with nothing
    asking it.
    """
    # `config.facts_path_for` -- the only answer. This spelled it again, with
    # no preference for `facts.<repo>.json`, so a repo carrying a second table
    # is judged against another project's vocabulary.
    from kernel.config import facts_path_for
    p = facts_path_for(root)
    if p is None:
        return {}
    try:
        f = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return f.get("present", f) if isinstance(f, dict) else {}



#: Every file this repo owns, as git sees it -- and read once.
#:
#: Two separate costs, both measured on the reference adopter. The walk was
#: `root.rglob("*.py")`, which is 23,677 files there against 1,696 the repo
#: actually owns: `.venv/` alone is 21,954. And nothing cached, so the four
#: passes below each re-read and re-parsed whatever they saw -- `code_only`
#: was entered 23,789 times and `ast.walk` ran 37 million times, for a checker
#: whose median runtime had reached 60 seconds. The same shape as
#: `_files_in_scope` before it asked git: one question about which files are
#: this repo's, answered twice, one of the answers wrong.
_TRACKED: dict = {}
_SOURCE: dict = {}


def tracked_files(root: Path) -> list:
    """Paths git knows about, relative to `root`.  Cached for the process."""
    key = str(root)
    if key not in _TRACKED:
        r = subprocess.run(["git", "ls-files", "--cached", "--others",
                            "--exclude-standard"], cwd=root,
                           capture_output=True, text=True)
        _TRACKED[key] = ([root / f for f in r.stdout.splitlines() if f.strip()]
                         if r.returncode == 0 else
                         [q for q in root.rglob("*") if q.is_file()])
    return _TRACKED[key]


def source_of(p: Path) -> str:
    """`code_only` of this file, read and parsed at most once per run."""
    key = str(p)
    if key not in _SOURCE:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        _SOURCE[key] = sql_code_only(text) if p.suffix == ".sql" else code_only(text)
    return _SOURCE[key]


def _globbed(root: Path, globs, suffixes=(".py",)) -> dict:
    import fnmatch
    out = {}
    for p in tracked_files(root):
        if p.suffix not in suffixes:
            continue
        rel = str(p.relative_to(root))
        if "/.git/" in f"/{rel}":
            continue
        if any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(rel, g.rstrip("/") + "/*")
               for g in globs):
            out[p] = source_of(p)
    return out


def schema_sources(root: Path) -> list:
    """Files that declare tables.  This framework's ledger, or the repo's own.

    An adopter's schema is not one Python file: it is a directory of `.sql`
    migrations, which is what `dal_globs` names. `CREATE TABLE` reads the same
    in both.
    """
    # `code_only` here for the same reason it is everywhere else in this file:
    # a table name inside a comment is not a declaration. Measured on this repo
    # -- a docstring reading "`CREATE TABLE IF NOT EXISTS` is a no-op" produced
    # a table called `IF`, which is the prose-shaped false finding this checker
    # already caught in itself once.
    own = root / "kernel" / "ledger.py"
    if own.is_file():
        return [source_of(own)]
    dal = _facts(root).get("dal_globs") or []
    if not dal:
        return []
    src = _globbed(root, dal, suffixes=(".sql", ".py"))   # already code_only
    return list(src.values())


def sources(root: Path):
    """(schema-side sources, everything-else sources).

    In this repo those are `kernel/` and `checkers/`. In an adopter they are
    whatever `dal_globs` names and whatever else the repo owns -- decided by
    `facts.not_this_framework`, so the framework's own installed copies do not
    count as the adopter reading its own tables.
    """
    if (root / "kernel" / "ledger.py").is_file():
        kernel = {p: source_of(p) for p in (root / "kernel").rglob("*.py")}
        checkers = {p: source_of(p) for p in (root / "checkers").glob("*.py")}
        return kernel, checkers

    facts = _facts(root)
    dal = _globbed(root, facts.get("dal_globs") or [])
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from kernel.facts import not_this_framework
        theirs = not_this_framework(root)
    except Exception:                                           # noqa: BLE001
        def theirs(rel):
            return True
    rest = {}
    for p in tracked_files(root):
        if p.suffix != ".py":
            continue
        rel = p.relative_to(root)
        if ".git" in rel.parts or p in dal or not theirs(rel):
            continue
        rest[p] = source_of(p)
    return dal, rest



def framework_source(root: Path) -> str:
    """This framework's own kernel and checkers, when we are inside an adopter.

    `.v4/home` records where it lives; `bin/v4` and every hook read the same
    file. Returns "" when it cannot be reached, which makes the checks that use
    it report more rather than fewer -- the direction that says something.
    """
    home = root / ".v4" / "home"
    if not home.is_file():
        return ""
    src = Path(home.read_text(encoding="utf-8", errors="replace").strip())
    if not src.is_dir():
        return ""
    out = []
    for sub, pattern in (("kernel", "**/*.py"), ("checkers", "*.py")):
        for p in (src / sub).glob(pattern):
            try:
                out.append(code_only(p.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
    return "\n".join(out)


def check(root: Path):
    problems = []
    schema_src = "\n".join(schema_sources(root))
    kernel, checkers = sources(root)
    all_src = "\n".join(list(kernel.values()) + list(checkers.values()))

    dropped = set(DROPPED.findall(schema_src))
    for table in TABLE.findall(schema_src):
        if table in dropped:
            continue
        writes = len(re.findall(rf'insert\(conn,\s*["\']{table}["\']', all_src)) \
            + len(re.findall(rf"INSERT INTO {table}\b", all_src))
        reads = len(re.findall(rf"FROM {table}\b", all_src))
        if table == "attempt":
            writes += len(re.findall(r"append_attempt\(", all_src))
        if writes and not reads:
            problems.append(
                f"table {table!r} is written {writes} time(s) and read by nothing. "
                f"A record nobody reads is the same gesture as a severity field "
                f"nobody branches on.")
        elif reads and not writes:
            problems.append(
                f"table {table!r} is read and never written. Every query against "
                f"it answers about an empty set.")
        elif not reads and not writes:
            problems.append(f"table {table!r} is declared and neither read nor written")

    # `.v4/config.json` and `.v4/claim_kinds.json` are the framework's files
    # wherever they sit, and in an adopter the code that reads them is the
    # framework's kernel -- which is not in this repo. Asking "does anything
    # read this dial" against the adopter's own source alone reports every
    # framework threshold as dead. Measured: `min_chars` came back as a dial
    # that turns nothing, while `kernel/cli.py:469` reads it.
    control_src = all_src
    if not (root / "kernel" / "ledger.py").is_file():
        control_src += "\n" + framework_source(root)

    cfg_path = root / ".v4" / "config.json"
    if cfg_path.is_file():
        cfg = json.loads(cfg_path.read_text())
        # A default table declaring the key is not a reader. Counting it as one
        # is how `remerge_max` sat in three files, read by nothing, and passed
        # this very check.
        readers = "\n".join(v for k, v in kernel.items()
                            if k != root / "kernel" / "config.py")
        readers += "\n" + "\n".join(checkers.values())
        readers += "\n" + (control_src if control_src is not all_src else "")
        for key in sorted(cfg.get("thresholds", {})):
            if not _names(key, readers):
                problems.append(
                    f"threshold {key!r} is configured, and the only place the name "
                    f"appears is where it is declared. It looks like a dial and "
                    f"turns nothing.")
        for key in sorted(k for k in cfg if k != "thresholds"):
            if not _names(key, control_src):
                problems.append(f"config key {key!r} is set and no code reads it")

    kinds_path = root / ".v4" / "claim_kinds.json"
    if kinds_path.is_file():
        fields = set()
        for spec in json.loads(kinds_path.read_text()).values():
            fields |= set(spec)
        for field in sorted(fields):
            if not _names(field, control_src):
                problems.append(
                    f"claim kinds carry a {field!r} field and no code reads it")

        # And the mirror. This checker's own contract is "one end declared and
        # the other end not following", and only one of the two directions was
        # implemented. Measured: `depends_on_kind` is read at
        # kernel/derive.py:151 and builds the whole attempt-ref mechanism
        # SPEC.md §4 rests on -- and **no kind has ever set it**, so the
        # machinery that lets one claim depend on another claim's observation
        # has never run once. This check said nothing about it for as long as
        # it existed.
        #
        # Only the idioms that actually reach a kind's config. A bare
        # `spec.get(...)` is `register.py` reading a fixture spec, and matching
        # it reported eleven fields that are not kind fields at all -- noise,
        # from the checker whose whole job is to be believed.
        import re as _re
        FETCH = _re.compile(
            r"(?:kind_cfg|kinds\[[^\]]+\]|cfg\.kind\([^)]*\)|kinds\.get\([^)]*\))"
            r"\.get\(\s*[\"']([a-z_]+)[\"']")
        fetched = set()
        for src in kernel.values():
            fetched |= set(FETCH.findall(src))
        # A field the spec names as machinery waiting for its first user. The
        # marker is a line in a diff with an author, the same shape as
        # `facts.absent` and `applies_to_why` -- and it is not an exemption from
        # saying anything, because `spec-coverage` reads §10 and refuses an
        # unbuilt list that has stopped being true.
        #
        # Without it this check turned SPEC.md:80 -- a sentence that has said
        # "no kind uses `depends_on_kind` today" since it was written -- into a
        # failure on every task. Reporting a documented decision as a defect is
        # how a checker teaches people to stop reading it.
        spec = (root / "docs" / "SPEC.md")
        declared = spec.read_text(encoding="utf-8") if spec.is_file() else ""
        for field in sorted(fetched - fields):
            if f"<!-- awaiting-first-user: {field} -->" in declared:
                continue
            problems.append(
                f"the kernel reads a claim-kind field {field!r} and no kind sets "
                f"it. Whatever it switches on has never happened. If that is "
                f"deliberate, say so where a reader will find it: "
                f"`<!-- awaiting-first-user: {field} -->` in docs/SPEC.md.")

    problems += _claim_lines_have_a_reader(root)
    return problems


def _in_a_fence(text: str) -> bool:
    """Is `V4-CLAIM:` shown as something to run, rather than mentioned?

    The same distinction `pysource` draws for Python, in the form markdown has
    it: a fenced block is an instruction, prose is a description. Without it
    this check fires on the paragraph telling a reviewer *not* to print the
    line -- a rule caught by the sentence explaining the rule, which is the
    failure `dead_wiring` first found on itself.
    """
    inside = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            inside = not inside
            continue
        if inside and "V4-CLAIM:" in line:
            return True
    return False


def _names(key: str, source: str) -> bool:
    """Is `key` read here as a key, rather than appearing as letters?

    `if key not in source` over every kernel and checker file concatenated
    means any short name passes on a substring. Measured: `.v4/config.json`
    declares `"repo": "vibeproof"`, nothing reads it -- `config.RepoConfig`
    never touches it -- and this check reported no problem, because the letters
    `repo` are in every second line of this repo.

    Quoted, and no narrower: a key reaches its reader as `cfg.get("x")`,
    `cfg["x"]`, `declared(cfg, "x")` or a name in a table of them, and a
    pattern that insists on the first two calls the third dead. What this still
    cannot separate is one repo's config key from another table's field of the
    same name -- `"repo"` is a facts field here as well -- so a key whose name
    is used elsewhere in quotes is not answered by this check. That is a
    narrower claim than the one it used to make, and it is the true one.
    """
    return re.search(rf"""["']{re.escape(key)}["']""", source) is not None


def _claim_lines_have_a_reader(root: Path):
    """`V4-CLAIM:` written where nothing parses it.

    `parse_claim_lines` is called from exactly two places: `derive`, on a
    detector's stdout, and the registration gates, on a fixture run. Anything
    else that emits the line is writing into a transcript.

    `.claude/agents/reviewer.md` did, for as long as it existed. It instructed
    the reviewer to print
    `V4-CLAIM: kind=review-finding file=... symbol=... note=...`, which no
    reader consumes -- so the whole reviewer layer produced findings that went
    nowhere, and the real entry point (`v4 review add`) went unmentioned. The
    format also handed the reviewer `kind=` and `file=`, which is precisely
    what SPEC.md §8.5 exists to take away from it.

    A prompt is not code, so no other checker here looks at one. This is the
    same question this checker already owns -- something written at one end
    that nothing reads at the other -- and the file it lives in does not
    change that.
    """
    problems = []
    # Only the two whose results survive. The loop walked five directories and
    # the body then `continue`d unconditionally on anything under `detectors/`,
    # `kernel/` or `checkers/` -- so three of the five were rglobbed in full and
    # every result thrown away, in the checker whose own comment block is about
    # having stopped walking files it did not need (23,677 against 1,696). It
    # also bypassed the `tracked_files()`/`source_of()` caching the rest of this
    # module routes through, so it re-read those trees off disk on every run.
    for d in (".claude", "hooks"):
        base = root / d
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in (".md", ".py", ".json"):
                continue
            rel = path.relative_to(root)
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            emits = (_in_a_fence(text) if path.suffix == ".md"
                     else "V4-CLAIM:" in code_only(text))
            if emits:
                problems.append(
                    f"{rel} emits a V4-CLAIM line, and only a detector's stdout "
                    f"is parsed for those. Findings written here reach nothing "
                    f"-- see `v4 review add` for the entry point that does.")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--facts")
    ap.add_argument("--out")
    a = ap.parse_args()

    try:
        root = Path(json.loads(Path(a.subject).read_text())["repo_root"])
    except Exception as exc:                                    # noqa: BLE001
        print(f"cannot read subject: {exc}", file=sys.stderr)
        return 5

    if not schema_sources(root):
        # Not "this repo is fine" -- "this repo has not said where its schema
        # lives". `dal_globs` in `.v4/facts.*.json` is the answer, and until it
        # is there this checker is guessing. Exit 4 says that; exit 0 would be a
        # clean report about nothing.
        print("no schema to trace: this repo has no `kernel/ledger.py` and its "
              "facts declare no `dal_globs`. Declare where the layer that talks "
              "to a database lives, and this can answer.")
        return 4

    try:
        problems = check(root)
    except Exception as exc:                                    # noqa: BLE001
        print(f"checker failed: {exc}", file=sys.stderr)
        return 5

    # Standing debt, the same shape as the other six delta checkers. This one
    # sweeps the whole repo and runs on every task -- `staleness: repo` -- and
    # it landed in adopters on 2026-08-12 with no baseline, while `lint`,
    # `dependency`, `layer-boundary`, `secret-chain`, `test-shape` and
    # `test-token-shape` all had one. Measured on the first adopter it reached:
    # seven tables declared by migrations and read by nothing, none of them
    # created by the task that had to answer for them. That is one task paying
    # everyone's bill, which this project decided against by name.
    try:
        accepted, _ = baseline.load(root, KIND)
    except baseline.Unreadable as exc:
        print(f"{exc}", file=sys.stderr)
        return 4
    fid = lambda t: baseline.finding_id(KIND, t)                # noqa: E731
    problems, carried, stale = baseline.partition(problems, accepted, fid)
    if carried:
        print(f"carrying {len(carried)} accepted finding(s) from "
              f"{baseline.where(KIND)}")
    for sid in stale:
        print(f"  baseline entry {sid} matches nothing any more -- delete it")

    if a.out:
        Path(a.out).write_text(json.dumps(
            {"problems": [{"what": t, "id": fid(t)} for t in problems],
             "carried": [fid(t) for t in carried], "stale": stale}, indent=2))
    if problems:
        print(f"FAIL: {len(problems)} thing(s) declared with one end unwired.\n")
        for p in problems:
            print(f"  {p}")
            print(f"      id {fid(p)}  -- to accept it, add that to "
                  f"{baseline.where(KIND)}")
        return 1
    print("every table and configured value has something on both ends")
    return 0


if __name__ == "__main__":
    sys.exit(main())
