"""Which files a claim covers, with the repo's exclusions still applied.

Nine checkers re-derive their own file set from git. They have to: a repo-scoped
claim names no `subject_refs`, because the question is about the task rather
than a site, and answering "nothing to scan" would return UNSUPPORTED forever.

The kernel already states one rule about which files a detector may look at --
`derive_exclude`, whose whole reason is that a red fixture is deliberately
broken code and a checker firing on it is the fixture doing its job, not a
finding. `kernel/lifecycle._files_in_scope` applies it. Every one of those nine
fallbacks dropped it on the floor.

Measured on a first adoption: `v4 install` copies each checker's bypass fixtures
into the adopting repo, and the first `v4 check` reported 13 committed
credentials -- every one of them a fixture demonstrating what a leaked
credential looks like. A framework whose first run on a clean repo produces
thirteen false positives is a framework nobody runs twice.

So the exclusion travels in the subject (`params.derive_exclude`) and this is
the one place that reads it. Pure: callers hand in the subject and the changed
list, and get back what survives.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from fnmatch import fnmatch


#: Widening into these is widening into whatever judges the work, so it takes a
#: signature rather than a flag.  SPEC.md §5.
#:
#: It lived in `kernel/scope.py`, which `kernel/analysis/**` may not import --
#: `.v4/layers.json` allows `kernel -> analysis` and not the reverse -- so the
#: analysis layer restated it instead of sharing it, and the restatement was
#: three of the four: `external_write._DEFAULT_TABLE` omitted `.github/**`, the
#: directory holding the workflow and the monitor contract, and `table_from`
#: returns that table for every repo that ships no facts file. `kernel/scope.py`
#: re-exports this name, so nothing above had to move.
PROTECTED_DEFAULT = (".v4/**", "checkers/**", "detectors/**", ".github/**")

#: How TypeScript and JavaScript say "this file is a test". Here rather than in
#: `test_weakened`, where they were first written, because this module is the
#: one owner of that question -- the docstring on `is_test` records what six
#: separate answers cost, and a seventh living next door would be the same
#: mistake with a shorter distance between the copies.
TS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
RS_SUFFIXES = (".rs",)
TS_TEST_NAME = (".test.", ".spec.", ".tests.", "-test.", "_test.")
TS_TEST_DIR = ("/test/", "/tests/", "/__tests/", "/__tests__/", "/__test__/",
               "/spec/")


def matches(path, globs) -> bool:
    """Does this repo-relative path match one of `globs`?  The only answer.

    Six programs decided this and they did not agree. Measured on the same
    inputs before this was one function: `x.py` against `**/*.py`,
    `requirements.txt` against `**/requirements*.txt`, and
    `__pycache__/a.pyc` against `**/__pycache__/**` -- this repo's own
    `derive_exclude` -- were True here and False in `lifecycle._files_in_scope`,
    `checkers/scope.matches`, `kernel/scope._matches`, `write_block.in_scope`
    and `shell_command._protected`. `excluded`'s own docstring named the cost
    of exactly that ("two matchers that disagree is how a file becomes visible
    to one half of the system and not the other") while being one of the six.

    Five rules, each of them a defect somebody paid for:

    * the glob as written;
    * a directory glob covering what is under it -- `app` and `app/` reach
      `app/x.py`;
    * `/**` reaching one level, so `app/**` reaches `app/x.py`;
    * `**/x` reaching `x` at the root, the way git reads it. Without it
      `**/requirements*.txt` missed a top-level `requirements.txt`;
    * a glob covering the directory it names -- `.v4/**` matching `.v4`.
      Measured in `shell_command`: `rm -rf .v4/` was refused and `rm -rf .v4`
      was allowed, so the guard turned on a trailing slash the caller chose.

    All five only ever widen the match, and in the direction git already means,
    so folding the six into one loosens no gate.
    """
    # Not `lstrip("./")`: that strips every leading `.` and `/` as a character
    # set, so `.v4/config.json` becomes `v4/config.json` and stops matching
    # `.v4/**` -- which silently un-protected the one directory that exists for.
    p = str(path).replace("\\", "/")
    p = p[2:] if p.startswith("./") else p
    p = p.rstrip("/") or p            # `.v4/` and `.v4` name the same directory
    for g in globs or ():
        if fnmatch(p, g) or fnmatch(p, g.rstrip("/") + "/*") \
                or fnmatch(p, g.replace("/**", "/*")):
            return True
        if g.startswith("**/") and fnmatch(p, g[3:]):
            return True
        base = g.split("*", 1)[0].rstrip("/")
        if base and p == base:
            return True
    return False


class DiffUnreadable(Exception):
    """git could not take the diff.  Not the same as "nothing changed"."""


def changed_since(root, base: str = "") -> frozenset:
    """Paths this change touched, as git reports them -- untracked included.

    Sibling of `deleted_since`: `git diff --name-only` against the task's base,
    plus what git has not been told about yet, because a file written and never
    staged is still this task's work.

    **Raises when git cannot answer.** The first version of this returned an
    empty set, which is the failure `checkers/scope.py` refuses in as many
    words -- "reporting the empty result as 'nothing changed' would answer the
    claim from a diff nobody read" -- and it would have carried that fail-open
    into every caller that adopted this. A rewritten history, a shallow clone
    or a base this checkout does not have are all real, and all of them look
    like a clean tree to a caller that cannot tell.

    Two of the four checkers that asked this themselves now ask here (`scope`
    and `secret_scan`: diff plus untracked, against the task's base). The other
    two are different questions and stay where they are -- `scope` also wants
    `base..HEAD`, which is the committed range and not the working tree, and
    `facts_coverage` wants tracked Python only.
    """
    import subprocess

    def git(*args):
        r = subprocess.run(["git", *args], cwd=str(root), capture_output=True,
                           text=True)
        if r.returncode != 0:
            raise DiffUnreadable(
                f"`git {' '.join(args)}` exited {r.returncode}"
                + (f": {r.stderr.strip().splitlines()[0][:160]}"
                   if r.stderr.strip() else ""))
        return r.stdout.splitlines()

    out = set(git("diff", "--name-only", base or "HEAD"))
    out |= set(git("ls-files", "--others", "--exclude-standard"))
    return frozenset(p for p in out if p.strip())


def changed_lines(root, base: str = "") -> dict:
    """`{path: {line, ...}}` -- the lines this change wrote, in the new file.

    `changed_since` answers at the granularity of a file, and that is the
    granularity every caller then reported at. Measured on the reference
    adopter: sixteen lines added to a 1600-line module, twenty-six claims
    raised against it, twenty-three of them on symbols the change never
    entered -- and `derive`'s inherited-debt line, which exists to show that
    cost before it is paid, said nothing, because the file was in `changed`.

    A file with no entry here has not been read: an untracked file has no diff
    to parse, and `{}` for it means "cannot tell", never "nothing changed".
    Callers have to keep those apart, which is why untracked paths are absent
    rather than present-and-empty.
    """
    import re
    import subprocess

    r = subprocess.run(["git", "diff", "-U0", base or "HEAD"],
                       cwd=str(root), capture_output=True, text=True)
    if r.returncode != 0:
        raise DiffUnreadable(f"`git diff -U0 {base or 'HEAD'}` exited {r.returncode}")
    out: dict = {}
    path = None
    hunk = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
    for line in r.stdout.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            out.setdefault(path, set())
        elif line.startswith("+++ /dev/null"):
            path = None                      # deleted; `deleted_since` owns it
        elif path is not None:
            m = hunk.match(line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                # A pure deletion is `+start,0`: no line in the new file
                # belongs to it, and claiming line `start` would hand the
                # change a symbol it did not touch.
                out[path].update(range(start, start + count))
    return out


def deleted_since(root, base: str = "") -> frozenset:
    """Paths this change removed, as git reports them.

    A checker handed a subject file that is not on disk has two very different
    situations in front of it: a path somebody typed wrong, and a file this
    change deleted on purpose. Three checkers said "file is missing" for both,
    so the reader could not tell which, and deleting a file -- an ordinary
    change -- read as a subject nobody could verify.

    `base` defaults to the commit the diff is against for the task at hand;
    without one, `HEAD` is what "this change" means for an uncommitted delete.
    """
    import subprocess
    args = ["diff", "--name-only", "--diff-filter=D", base or "HEAD"]
    r = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True)
    if r.returncode != 0:
        return frozenset()
    return frozenset(l for l in r.stdout.splitlines() if l.strip())


def excluded(rel: str, globs) -> bool:
    """`matches`, named for the question `derive_exclude` asks."""
    return matches(rel, globs)


def exclusions(subject: dict):
    """What the subject says to leave out.  `()` when it says nothing.

    An older subject, or one written by `register`'s fixture harness, carries no
    `derive_exclude`. Returning an empty tuple there is deliberate: a fixture run
    is *about* the broken code, and excluding it would make every red case pass.
    """
    params = subject.get("params") or {}
    return tuple(params.get("derive_exclude") or ())


def keep(subject: dict, paths) -> list:
    """`paths` minus what this repo excludes from derivation, sorted."""
    globs = exclusions(subject)
    if not globs:
        return sorted(p for p in paths if str(p).strip())
    return sorted(p for p in paths if str(p).strip() and not excluded(p, globs))


def readable(paths, reads) -> bool:
    """Is there anything here this checker can read?

    Measured on a Go repo with four planted defects: of 27 checkers, 15 said
    exit 4 -- "I cannot answer this" -- and 8 returned 0 while having parsed no
    Go at all. `dependency_audit` reported `PASS: no package manifest declares a
    dependency` about a `go.mod` with an unpinned require and no `go.sum`.

    Every one of those 8 was written by an author who did not think about it,
    and the 15 by authors who did. That is the whole difference, and a rule that
    holds only when each of 27 authors remembers it is not a rule -- it is a
    tally of who was careful. `route_auth`'s own docstring states the principle
    the other 8 break: answering "fine" by looking at nothing is the failure
    this layer exists to refuse.

    So a checker stops deciding. It declares what it reads, and a checker with
    nothing to read is not run -- a program that never executed cannot report a
    clean repo.
    """
    globs = tuple(reads or ())
    if not globs:
        return True            # nothing declared: the registry gate refuses this
    return any(excluded(p, globs) for p in paths)


def tracked(subject, root, suffixes=None):
    """Every file the repo tracks, minus what it excludes from derivation.

    A checker that sweeps the whole repo has to answer "which files are this
    repo's" and every one of them answered it again, by hand. `test_shape` skips
    nine directory names and says why in a comment -- `.venv` alone took the run
    past two minutes. `bundle_secret` skips `node_modules` and `dist`.
    `secret_chain` skipped `tests/fixtures` and `__pycache__`, walked `.venv`
    anyway, and reported a finding inside `jwt/jwks_client.py`.

    The repo already states this once, in `derive_exclude`. Four hand-rolled
    copies of one rule is the shape that let `derive_exclude` be ignored in five
    separate places; this is the sixth through ninth.

    `git ls-files` rather than `rglob` for the same reason: a virtualenv is
    untracked, so the exclusion list never had to name it.
    """
    # `--others --exclude-standard` as well as the index: a file written by this
    # task and not yet `git add`ed is still this repo's, and a sweep that cannot
    # see it reports clean about the newest code in the tree. Plain `ls-files`
    # made every `bundle-secret` fixture exit 4 -- the fixture writes `app.ts`
    # and commits only `.v4/config.json`, so the checker saw no client source at
    # all and said so honestly, about the wrong set of files.
    #
    # `.gitignore` still applies, which is what keeps `.venv` and `node_modules`
    # out without this function naming either of them.
    r = subprocess.run(["git", "ls-files", "--cached", "--others",
                        "--exclude-standard"], cwd=str(root),
                       capture_output=True, text=True)
    if r.returncode != 0:
        return []
    out = keep(subject or {}, r.stdout.splitlines())
    if suffixes:
        out = [f for f in out if Path(f).suffix in set(suffixes)]
    return out


def is_test(path, source=None, root=None) -> bool:
    """Is this file a test, as a test runner would decide?

    Six places asked this and each answered differently -- `test_shape`,
    `test_token_shape`, `test_expectation_diff`, `structural_lint`, `facts`, and
    the `test_shape` detector -- and four of them answered on the filename alone.

    A filename alone is wrong in one direction this repo is full of:
    `checkers/test_shape.py`, `checkers/test_token_shape.py`,
    `detectors/test_weakened.py` and `kernel/analysis/test_expectation_diff.py`
    are programs that *examine* tests. Judged by name they are tests, and
    `test-shape` reported 40 findings on this repo -- every one of them a checker
    doing its job, reading source text, which is the thing the rule forbids a
    test from doing.

    So: under a declared test root, the name is enough. Outside one, a file named
    like a test is a test only if a runner would collect something from it -- a
    module-level `def test_*` or a `TestCase` subclass. `checkers/test_shape.py`
    has neither.

    Unparseable or unreadable falls back to the name. Being unable to tell is not
    a reason to stop applying a rule to something that looks exactly like its
    subject.
    """
    p = Path(str(path).replace("\\", "/"))
    if p.suffix in TS_SUFFIXES:
        # Two conventions, not one, and both were measured rather than
        # recalled: 406 test files across five DeepSWE repos, of which
        # `valibot`'s 285 are named `*.test.ts` and `yjs`'s 17 are named
        # `*.tests.js` under `tests/`, while `csstree`'s 39 sit in
        # `lib/__tests/` with names that say nothing. Answering only by name
        # read yjs and csstree as repos with no tests at all.
        #
        # No source inspection, unlike the Python branch below. That branch
        # exists because this repo is full of programs that examine tests and
        # are named like them; there is no TypeScript equivalent here to
        # protect against, and inventing one would mean deciding what a TS test
        # "collects" without a parser.
        low = p.name.lower()
        if any(m in low for m in TS_TEST_NAME):
            return True
        where = "/" + str(path).replace("\\", "/").lower()
        return any(m in where for m in TS_TEST_DIR)
    if p.suffix == ".rs":
        # The one language here that does not keep its tests in files of their
        # own. Measured on the five corpus repos: 1,316 `.rs` files, 455 of
        # them carrying tests, and only 78 of those under a `tests/` or
        # `benches/` directory. The other 377 are implementation files holding
        # `#[cfg(test)] mod tests`, so the name-and-directory rule that is the
        # whole answer for Go and TypeScript would miss five of every six.
        #
        # So the source is read, and unlike the Python branch below this is not
        # a guard against programs named like tests. `#[cfg(test)]` is the
        # compiler's own marker for "built only into the test binary", so
        # reading it is reading the toolchain's answer rather than guessing.
        where = "/" + str(path).replace("\\", "/").lower()
        if "/tests/" in where or "/benches/" in where:
            return True
        if source is None:
            try:
                source = (Path(root) / path if root else Path(path)) \
                    .read_text(encoding="utf-8", errors="replace")
            except OSError:
                return False
        from . import rssource
        flat = rssource.rs_masked(source).replace(" ", "")
        return "#[cfg(test)]" in flat or "#[test]" in flat
    if p.suffix == ".go":
        # Go decides this by the file name and the toolchain enforces it:
        # `go test` compiles `*_test.go` and nothing else into the test binary.
        # There is no equivalent of the ambiguity the Python branch below
        # exists for -- `checkers/test_shape.py` is a program that examines
        # tests and is named like one, and Go has no such case because the
        # suffix is a compiler rule rather than a convention.
        return p.name.endswith("_test.go")
    if p.suffix != ".py":
        return False
    if "tests" in p.parts or "test" in p.parts:
        return True
    if p.name == "conftest.py":
        # pytest's one reserved filename, and reserved at any depth: it is
        # imported into the test session and never into the program. That makes
        # it the Python equivalent of the `.go` branch above -- a toolchain rule
        # rather than a convention -- so it is answered by name, without the
        # source inspection the branch below needs.
        #
        # Left out, and the sentence `structural_lint._is_config` opens with --
        # "A test is never one, whatever it is called" -- was false of the file
        # pytest puts at the root of a project. Measured: `is_test("conftest.py")`
        # was False, `CONFIG_PATH` matches the substring `conf`, and a repo-root
        # `conftest.py` was read as a source of config bindings for
        # `LINT-CONFIG-DUAL-TRUTH`, so a threshold a fixture states was the
        # truth every module was judged against. `tests/conftest.py` escaped it
        # only by sitting under a directory named `tests`, which is a fact about
        # that layout and not about conftest.
        return True
    if not (p.name.startswith("test_") or p.name.endswith("_test.py")):
        return False
    if source is None:
        try:
            source = (Path(root) / path if root else Path(path)) \
                .read_text(encoding="utf-8", errors="replace")
        except OSError:
            return True
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return True
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name.startswith("test"):
            return True
        if isinstance(node, ast.ClassDef):
            if node.name.startswith("Test"):
                return True
            for b in node.bases:
                name = b.attr if isinstance(b, ast.Attribute) else \
                    (b.id if isinstance(b, ast.Name) else "")
                if name.endswith("TestCase"):
                    return True
    return False
