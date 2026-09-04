"""Three languages, one question, and `None` is never an empty set.

    python3 -m unittest tests.test_a_name_in_a_language_this_can_read -v

`request_cover.unresolved` asks whether a coverage entry's `path::name` names
something. The repair that put that check there reached Python and stopped, so
on a Go or TypeScript file the entry was accepted unchecked -- 79 of the 113
DeepSWE tasks are not Python.

The single most important assertion here is not that Go parses. It is that
every way of failing to read a file returns `None` rather than an empty set:
`docs/EVIDENCE.md` §4 records eight checkers returning PASS on a Go repo having
parsed no Go, and an empty set is how that happens.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import request_cover                       # noqa: E402
from kernel.analysis import gosource, symbols          # noqa: E402

HAVE_GO = shutil.which("go") is not None

#: Worded to match `run_without_silent_skips.ENVIRONMENT`, deliberately. A
#: machine with no Go toolchain is a smaller world than the question, and this
#: suite's own runner exists to refuse a quiet skip for exactly that: "a gate
#: that has never gone green is one nobody can tell apart from a broken one."
#: If CI loses its Go toolchain the suite says so instead of reporting green on
#: a half it did not run.
GO_ABSENT = "the Go toolchain is not available on this machine"


class NoneIsNotAnEmptySet(unittest.TestCase):
    """Every failure returns no verdict.  The one that matters most is the
    toolchain being absent, because that is the machine, not the code."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_language_with_no_extractor(self):
        """Ruby, because Rust stopped being one.

        This case was `.rs` until `names_in` grew a Rust branch, and it kept
        asserting `None` for a language that had learned to answer -- which is
        the failure the class is about, pointed the other way. The property is
        the same and it needs a suffix nothing here reads: any of them will do
        while this module has four extractors and the world has more.
        """
        (self.tmp / "a.rb").write_text("def thing\nend\n")
        self.assertIsNone(symbols.names_in(self.tmp / "a.rb"))

    def test_and_the_language_that_just_learned_one_answers(self):
        """The control. Without it, the case above passes just as well on the
        day somebody deletes an extractor."""
        (self.tmp / "a.rs").write_text("pub fn thing() {}\n")
        self.assertEqual(symbols.names_in(self.tmp / "a.rs"), {"thing"})

    def test_python_that_will_not_parse(self):
        (self.tmp / "a.py").write_text("def (\n")
        self.assertIsNone(symbols.names_in(self.tmp / "a.py"))

    @unittest.skipUnless(HAVE_GO, GO_ABSENT)
    def test_go_that_will_not_parse(self):
        (self.tmp / "a.go").write_text("package main\nfunc (\n")
        self.assertIsNone(symbols.names_in(self.tmp / "a.go"))

    def test_a_go_file_with_no_toolchain_on_PATH(self):
        """The one `docs/EVIDENCE.md` §4 is about.

        Run in a subprocess with an emptied `PATH`, because `gosource` caches
        the built binary in this process once it has one.
        """
        (self.tmp / "a.go").write_text("package main\nfunc Thing() {}\n")
        script = (
            "import sys, json;"
            f"sys.path.insert(0, {str(ROOT)!r});"
            "from kernel.analysis import symbols;"
            f"print(json.dumps(symbols.names_in({str(self.tmp / 'a.go')!r}) is None))"
        )
        r = subprocess.run([sys.executable, "-c", script], capture_output=True,
                           text=True, env={"PATH": "", "HOME": os.environ.get("HOME", "")},
                           timeout=180)
        self.assertEqual(r.stdout.strip(), "true", r.stdout + r.stderr)

    def test_and_an_unreadable_file_is_not_a_file_with_no_names(self):
        self.assertIsNone(symbols.names_in(self.tmp / "not-here.go"))
        self.assertIsNone(symbols.names_in(self.tmp / "not-here.py"))
        self.assertIsNone(symbols.names_in(self.tmp / "not-here.ts"))


class WhatEachLanguageBinds(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    @unittest.skipUnless(HAVE_GO, GO_ABSENT)
    def test_go_through_gos_own_parser(self):
        """A comment and a string both say `func AlsoFake`. A parser is why
        neither is a definition -- this is the assertion that fails if somebody
        replaces `gosource` with a regular expression."""
        (self.tmp / "a.go").write_text(
            "package main\n"
            "// func FakeInComment() {}\n"
            'const realConst = "func AlsoFake() {}"\n'
            "type Widget struct{ N int }\n"
            'func (w *Widget) Render() string { return "" }\n'
            "func NewWidget(n int) *Widget { return &Widget{n} }\n")
        got = symbols.names_in(self.tmp / "a.go")
        self.assertEqual(got, {"realConst", "Widget", "Render", "NewWidget"})

    def test_typescript_with_comments_and_strings_stripped(self):
        (self.tmp / "a.ts").write_text(
            "/* export type MissingType = string; */\n"
            'const banner = "export function sendPicture() {}";\n'
            "// export function alsoFake() {}\n"
            "export function sendPhoto(id: string): void {}\n"
            "export const sendMediaGroup = (ids: string[]): void => {};\n"
            "export interface MediaGroup { ids: string[] }\n"
            "export type MediaKind = 'photo';\n"
            "export default async function main() {}\n"
            "enum Colour { A }\n")
        got = symbols.names_in(self.tmp / "a.ts")
        self.assertEqual(
            got, {"banner", "sendPhoto", "sendMediaGroup", "MediaGroup",
                  "MediaKind", "main", "Colour"})
        for hidden in ("MissingType", "sendPicture", "alsoFake"):
            self.assertNotIn(hidden, got, f"{hidden} is not defined here")

    def test_python_binds_every_bare_name_and_what_a_class_owns(self):
        """Imports count as bindings, because a re-exported name is in this file
        as far as anybody reading the reference is concerned.

        `C.m` joined the set on 2026-08-26. The expectation moved because the
        behaviour was changed on purpose, not because the old one was wrong:
        this used to be the complete answer, and it made `a.py::m` resolvable
        while `a.py::C.m` -- the same method, named without ambiguity -- did
        not. Nothing was removed, so every reference that resolved before still
        resolves.
        """
        (self.tmp / "a.py").write_text(
            "import os\nfrom x import y as z\n\n"
            "CONST = 1\n\nclass C:\n    def m(self, arg): ...\n")
        self.assertEqual(symbols.names_in(self.tmp / "a.py"),
                         {"os", "z", "CONST", "C", "m", "C.m", "self", "arg"})

    def test_a_method_named_through_its_class_resolves(self):
        """The reference with no ambiguity in it was the one being refused."""
        (self.tmp / "b.py").write_text(
            "class Outer:\n"
            "    def run(self): ...\n"
            "    class Inner:\n"
            "        def go(self): ...\n"
            "def run(): ...\n")
        names = symbols.names_in(self.tmp / "b.py")
        self.assertIn("Outer.run", names)
        self.assertIn("Outer.Inner", names)
        self.assertIn("Outer.Inner.go", names)
        self.assertIn("run", names, "the bare name still binds")
        self.assertNotIn("b.run", names,
                         "a module-level def is not a class member; prefixing "
                         "it would invent a spelling nobody writes")

    def test_a_method_that_is_not_on_that_class_is_still_refused(self):
        """Or the repair would turn the check into a rubber stamp."""
        (self.tmp / "c.py").write_text(
            "class A:\n    def m(self): ...\nclass B:\n    def n(self): ...\n")
        names = symbols.names_in(self.tmp / "c.py")
        self.assertIn("A.m", names)
        self.assertNotIn("A.n", names)
        self.assertNotIn("B.m", names)


class ThreeStrengthsBecameOne(unittest.TestCase):
    """`request_cover.unresolved` had three tiers and two of them checked
    nothing: a non-Python file was waved through, and a reference with no `::`
    was not even checked for existing."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "pkg").mkdir()
        (self.tmp / "pkg" / "thing.go").write_text("package pkg\nfunc DoIt() {}\n")
        (self.tmp / "src").mkdir()
        (self.tmp / "src" / "x.ts").write_text("export const foo = 1;\n")
        (self.tmp / "a.py").write_text("def run(): ...\n")

    def unresolved(self, ref):
        return request_cover.unresolved(self.tmp, ref)

    @unittest.skipUnless(HAVE_GO, GO_ABSENT)
    def test_a_go_symbol_that_is_there_and_one_that_is_not(self):
        self.assertIsNone(self.unresolved("pkg/thing.go::DoIt"))
        self.assertIn("does not define", self.unresolved("pkg/thing.go::NoSuch") or "")

    def test_a_typescript_symbol_that_is_there_and_one_that_is_not(self):
        self.assertIsNone(self.unresolved("src/x.ts::foo"))
        self.assertIn("does not define", self.unresolved("src/x.ts::nope") or "")

    def test_python_is_unchanged(self):
        self.assertIsNone(self.unresolved("a.py::run"))
        self.assertIn("does not define", self.unresolved("a.py::gone") or "")

    def test_a_method_named_through_its_class(self):
        """What an adopter wrote twice and had refused twice, then gave up on:
        `core/workers/dm_ingest.py::DmIngestWorker.run` was rejected as a symbol
        that is not there, so the entry was re-filed against the class -- one
        level coarser than the work it was accounting for."""
        (self.tmp / "w.py").write_text("class W:\n    def go(self): ...\n")
        self.assertIsNone(self.unresolved("w.py::W.go"))
        self.assertIn("does not define", self.unresolved("w.py::W.nope") or "")

    def test_a_bare_path_that_is_here_passes_and_one_that_is_not_does_not(self):
        self.assertIsNone(self.unresolved("pkg/thing.go"))
        self.assertIn("is not a file in this repo",
                      self.unresolved("pkg/gone.go") or "")

    def test_a_runner_invocation_is_still_left_alone(self):
        """The reason the tier existed. `go test -run TestFoo` is a legitimate
        value and calling it a broken reference would make `--test` unusable
        for every project that is not pytest."""
        for ref in ("go test -run TestFoo", "npm test -- --grep sends",
                    "cargo test thing", "some prose about what was done"):
            self.assertIsNone(self.unresolved(ref), ref)

    def test_and_a_language_with_no_extractor_is_still_waved_through(self):
        """Honest absence, kept. A file in a language nothing here reads gets
        no verdict rather than a wrong one -- its existence is checked, its
        names are not.

        `.rb`, because this used `.rs` and Rust learned to answer. That change
        was a repair: a coverage entry naming a function that is not in a `.rs`
        file used to be recorded and counted, while the same entry on a `.ts`
        file was refused."""
        (self.tmp / "a.rb").write_text("def thing\nend\n")
        self.assertIsNone(self.unresolved("a.rb::whatever_name"))
        self.assertIn("is not a file in this repo",
                      self.unresolved("gone.rb::whatever_name") or "")

    def test_but_rust_is_no_longer_one_of_them(self):
        (self.tmp / "a.rs").write_text("pub fn thing() {}\n")
        self.assertIn("does not define",
                      self.unresolved("a.rs::whatever_name") or "")
        self.assertIsNone(self.unresolved("a.rs::thing"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TheCompiledHelperIsSharedAndMustNotBeSeenHalfWritten(unittest.TestCase):
    """One cache path per source sha, and more than one process reaching it.

    `v4 accept` runs the suite while a worktree may be running `v4 check`, so
    two builds can land on the same file -- and a reader that execs a binary
    the other is half way through writing gets a failure that does not
    reproduce. Observed exactly once, as a suite that failed inside `accept`
    and passed on its own a minute later.

    The build goes to a temp name and is moved onto the final one, which is a
    rename on the same filesystem: a reader sees the old file or the new one.
    """

    @unittest.skipUnless(HAVE_GO, GO_ABSENT)
    def test_eight_processes_building_at_once_all_get_a_working_binary(self):
        # Only the binary this test is about. It used to unlink every
        # `v4-go-*` in the system temp directory, which is the cache for every
        # helper `kernel/analysis/gosource.py` builds -- so a run of this one
        # test made every later caller of `shape()` pay a cold `go build`, and
        # under a loaded `v4 accept` that build is what times out. The name is
        # `gosource`'s own, asked rather than spelled again here, so a change
        # to where the cache lives cannot leave this deleting the wrong thing.
        mine = gosource._build(gosource.SOURCE)
        if mine is not None:
            try:
                mine.unlink()
            except OSError:
                pass
            gosource._built.clear()        # the in-process memo, not the disk
        sample = Path(tempfile.mkdtemp()) / "a.go"
        sample.write_text("package main\nfunc Alpha() {}\nfunc Beta() {}\n")
        self.addCleanup(shutil.rmtree, sample.parent, ignore_errors=True)
        script = (
            "import sys, json;"
            f"sys.path.insert(0, {str(ROOT)!r});"
            "from kernel.analysis import gosource;"
            f"print(json.dumps(sorted(gosource.names_in({str(sample)!r}) or [])))"
        )
        procs = [subprocess.Popen([sys.executable, "-c", script],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True) for _ in range(8)]
        for p in procs:
            out, err = p.communicate(timeout=300)
            self.assertEqual(out.strip(), '["Alpha", "Beta"]', err[-400:])
