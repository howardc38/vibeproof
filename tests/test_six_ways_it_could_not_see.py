"""Six mechanisms that answered without looking, found by a lens sweep.

    python3 -m unittest tests.test_six_ways_it_could_not_see -v

Each was reproduced before it was repaired, and the reproduction is what these
cases assert. The sweep raised seventy-four findings; these are the six that
made this repo unable to see something about itself, and they are the ones with
a mechanical answer.

The one that cost most is not in this file, because its oracle is not a test:
`.github/workflows/v4.yml` derived the facts table's name from the checkout
directory, a checkout is named after the GitHub repo rather than after the
table, and the step exited 1 on every push since the rename -- with the two
`v4 accept` gates below it in the same job never running once. What proves that
repaired is a green run on GitHub, not an assertion here.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import ledger as ledger_mod                         # noqa: E402
from kernel import redgreen                                     # noqa: E402
from kernel.analysis import redaction                           # noqa: E402
from kernel.analysis.shell_command import writes_to_protected   # noqa: E402

PROTECTED = [".github/**", ".v4/**", "checkers/**", "detectors/**"]


class AnAncestorOperandNamesEverythingUnderIt(unittest.TestCase):
    """`rm -rf .` measured ALLOW while `rm -rf checkers` measured DENY: the
    guard recognised the name and not the directory holding it, so the widest
    form of the write it exists to stop was the form it passed."""

    def verdict(self, cmd):
        return writes_to_protected(cmd, PROTECTED)

    def test_the_four_shapes_that_walked_through(self):
        for cmd in ("rm -rf .", "git checkout .", "git restore .", "mv /tmp/x ."):
            self.assertTrue(self.verdict(cmd), cmd)

    def test_and_one_level_further_out(self):
        self.assertTrue(self.verdict("rm -rf .."))

    def test_a_named_protected_directory_is_unchanged(self):
        self.assertTrue(self.verdict("rm -rf checkers"))

    def test_reading_through_an_ancestor_is_not_a_write(self):
        """The narrowing that keeps this from being a blanket refusal: the
        ancestor still has to be an operand of something that writes."""
        self.assertEqual(self.verdict("ls ."), [])
        self.assertEqual(self.verdict("git status ."), [])

    def test_and_a_repo_with_nothing_protected_is_not_dragged_in(self):
        self.assertEqual(writes_to_protected("rm -rf .", []), [])


class TheTracerAsksWhichFileNotWhichName(unittest.TestCase):
    """`endswith(basename)` let a covered `internal/cache/store.go` answer for
    a claim filed at `internal/db/store.go`. It stayed cheap while Go was the
    only non-Python tracer; a TypeScript tree carries `index.ts` in every
    directory."""

    def test_the_repo_relative_path_is_what_a_line_must_end_with(self):
        self.assertEqual(redgreen._identity("internal/db/store.go"),
                         "internal/db/store.go")

    def test_one_leading_dot_slash_comes_off(self):
        self.assertEqual(redgreen._identity("./src/a.ts"), "src/a.ts")

    def test_but_a_dotted_directory_keeps_its_dot(self):
        """`lstrip("./")` ate the dot of `.v4/x` -- the first spelling of this
        repair did exactly that."""
        self.assertEqual(redgreen._identity(".v4/x.json"), ".v4/x.json")
        self.assertEqual(redgreen._identity("./.v4/y.json"), ".v4/y.json")

    def test_two_files_sharing_a_basename_no_longer_answer_for_each_other(self):
        a, b = "internal/db/store.go", "internal/cache/store.go"
        self.assertNotEqual(redgreen._identity(a), redgreen._identity(b))
        covered = "example.com/m/internal/cache/store.go"
        self.assertTrue(covered.endswith(redgreen._identity(b)))
        self.assertFalse(covered.endswith(redgreen._identity(a)),
                         "the cache file must not answer for the db file")

    def test_and_the_module_prefix_still_resolves(self):
        """Still a suffix test: neither producer emits a repo-relative path."""
        self.assertTrue(
            "file:///Users/x/repo/src/api/reg.ts".endswith(
                redgreen._identity("src/api/reg.ts")))


class AFilterThatCouldNotRunDoesNotPassTheInputOn(unittest.TestCase):
    """`ledger._redact` returned the unredacted text when redaction raised --
    into an append-only file that is committed and scanned."""

    def test_a_redactor_that_raises_does_not_leak_its_input(self):
        real = redaction.redact

        def boom(text, root=None):
            raise RuntimeError("table unreadable")

        redaction.redact = boom
        self.addCleanup(setattr, redaction, "redact", real)
        out = ledger_mod._redact("token AKIAIOSFODNN7EXAMPLE", ROOT)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", out)
        self.assertIn("could not be filtered", out)
        self.assertIn("RuntimeError", out)

    def test_and_the_working_path_still_redacts_rather_than_blanking(self):
        """The control. If every value came back as the failure notice, this
        would be a filter that never runs rather than one that fails safe."""
        out = ledger_mod._redact("a plain sentence with no credential", ROOT)
        self.assertEqual(out, "a plain sentence with no credential")


class TheStructuredPayloadIsFilteredLikeTheStreams(unittest.TestCase):
    """`redact_json` had no `root` parameter, so a repo's own declared
    credential families reached a checker's streams and never its `--out`."""

    def test_the_root_reaches_the_leaf(self):
        seen = []
        real = redaction.redact

        def spy(text, root=None):
            seen.append(root)
            return real(text, root)

        redaction.redact = spy
        self.addCleanup(setattr, redaction, "redact", real)
        redaction.redact_json({"a": ["x", {"b": "y"}]}, root=ROOT)
        self.assertTrue(seen)
        self.assertEqual(set(seen), {ROOT}, "every leaf, not just the first")

    def test_the_caller_passes_it(self):
        """Run, not read. This asserted `"root=repo_root" in runner.py`, which
        is true of a file that mentions it in a comment; what matters is that a
        real `--out` payload arrives at `redact_json` with the repo root, so
        the checker writes one and the runner is asked."""
        import shutil
        import tempfile
        from kernel import runner

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        checker = tmp / "c.py"
        checker.write_text(
            "import argparse, json, pathlib\n"
            "p = argparse.ArgumentParser()\n"
            "p.add_argument('--subject'); p.add_argument('--facts')\n"
            "p.add_argument('--out'); a = p.parse_args()\n"
            "pathlib.Path(a.out).write_text(json.dumps({'note': 'hello'}))\n")

        seen = {}
        real = runner.redact_json

        def spy(value, key=None, root=None):
            seen["root"] = root
            return real(value, key, root)

        runner.redact_json = spy
        self.addCleanup(setattr, runner, "redact_json", real)
        runner.run_checker(repo_root=ROOT, checker_path=checker,
                           registered_sha=None, subject_payload={},
                           subject_refs=[])
        self.assertEqual(seen.get("root"), ROOT)

    def test_a_secret_key_name_is_still_blanked_whole(self):
        out = redaction.redact_json({"password": "hunter2"}, root=ROOT)
        self.assertEqual(out, {"password": "[redacted]"})


class AGuardHandedNothingSaysSo(unittest.TestCase):
    """Three ways to hand `bash_guard` something it cannot read, and all three
    printed `{}` with no stderr and no mark -- an allow indistinguishable from
    a clean command."""

    def _run(self, payload: bytes):
        return subprocess.run(
            [sys.executable, str(ROOT / "hooks" / "bash_guard.py")],
            input=payload, capture_output=True, cwd=ROOT)

    def test_an_unreadable_payload_is_allowed_out_loud(self):
        for payload in (b"", b"{not json"):
            r = self._run(payload)
            self.assertEqual(r.returncode, 0, payload)
            self.assertEqual(r.stdout.decode().strip(), "{}", payload)
            self.assertIn("allowed without checking", r.stderr.decode(), payload)

    def test_a_bash_event_with_no_command_is_allowed_out_loud(self):
        r = self._run(json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": "  "}}).encode())
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.decode().strip(), "{}")
        self.assertIn("no command", r.stderr.decode())

    def test_another_tool_stays_quiet(self):
        """The narrowing. One line per tool call would drown the record this
        is trying to keep."""
        r = self._run(json.dumps({"tool_name": "Read"}).encode())
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr.decode().strip(), "")

    def test_and_it_answers_even_when_it_cannot_record(self):
        """The first spelling of this repair wrote the mark before printing,
        so the hook exited 1 with an empty stdout on exactly the inputs it
        could not read -- no verdict at all, which is worse than the silence."""
        r = self._run(b"{not json")
        self.assertEqual(r.stdout.decode().strip(), "{}")

    def test_and_the_mark_actually_lands(self):
        """The second spelling was loud and still unrecorded. `_mark` names the
        row after `cmd.split()[0]`, and an unreadable payload has no command --
        `IndexError` before `record_seen`, swallowed by the caller's `except`.
        A lens reviewer found that in the working tree while this was being
        written, which is what the sweep is for; the cases above assert the
        stream and the exit code and could not see it.

        Asked of the function rather than the process, because the row it
        writes belongs to whichever tasks are open here."""
        from hooks import bash_guard
        seen = {}

        def spy(root, tid, head, **kw):
            seen["head"] = head
            return ""

        real = bash_guard._framework.record_seen
        bash_guard._framework.record_seen = spy
        self.addCleanup(setattr, bash_guard._framework, "record_seen", real)
        bash_guard._mark(ROOT, "", allowed=True, basis="unreadable payload")
        self.assertEqual(seen.get("head"), "(no command)")

    def test_and_a_real_command_still_names_itself(self):
        from hooks import bash_guard
        seen = {}

        def spy(root, tid, head, **kw):
            seen["head"] = head
            return ""

        real = bash_guard._framework.record_seen
        bash_guard._framework.record_seen = spy
        self.addCleanup(setattr, bash_guard._framework, "record_seen", real)
        bash_guard._mark(ROOT, "grep -n x checkers/scope.py", allowed=True,
                         basis="no protected path")
        self.assertEqual(seen.get("head"), "grep")


if __name__ == "__main__":
    unittest.main()
