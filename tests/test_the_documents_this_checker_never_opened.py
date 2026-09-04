"""Four ranges `spec-coverage` never read, and the one that hid the other three.

`checkers/spec_coverage.py` is the only thing in this repo that resolves a
documented `v4 …` line against the CLI, so what it does not open is unchecked by
anything at all. Three ranges were outside it and each was found by a different
route:

    the seven `.claude/` files    SPEC §12.5 makes them contract; nothing read
                                  them, and `reviewer.md`'s own opening line
                                  `v4 --repo . review lens` exits 127
    §13's command table           the reverse check asked only whether the
                                  string appears somewhere in SPEC, so five of
                                  thirty-two commands had no row and it stayed
                                  green
    a facts-table population      `_reality` could settle checkers, kinds,
                                  lenses, detectors, hooks, agents, subcommands
                                  and pins, and not the one table whose
                                  incompleteness makes every detector report a
                                  clean repo

Each case below is built as a small tree rather than asserted against this
repo's current documents, for the reason `tests/test_spec_coverage.py` gives at
the top of itself: a checker that only ever runs against documents somebody has
just fixed by hand is a checker that passes because they were fixed by hand. The
two that *are* asserted against this repo are marked as such, and they are there
because the repair to the documents is half of the repair.
"""

import ast
import json
import sys
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import spec_coverage as sc                    # noqa: E402
from kernel.config import facts_path_for                  # noqa: E402


#: The smallest tree `spec_coverage.check` will read without complaining about
#: something else. Every case here differs from it in one file.
def _mini_repo(tmp: Path, *, commands=("audit",), rows=("audit",), extra=""):
    (tmp / ".v4").mkdir(parents=True, exist_ok=True)
    (tmp / "kernel").mkdir(parents=True, exist_ok=True)
    (tmp / "docs").mkdir(parents=True, exist_ok=True)
    (tmp / ".v4" / "config.json").write_text(
        json.dumps({"test_command": "true", "policy": "allow_accepted_risk"}))
    (tmp / ".v4" / "claim_kinds.json").write_text(
        json.dumps({"probe": {"checker": "probe", "staleness": "repo"}}))
    (tmp / ".v4" / "checkers.json").write_text(json.dumps(
        {"probe": {"path": "x", "sha256": "y", "kinds": ["probe"],
                   "fixtures": "f"}}))
    (tmp / "kernel" / "ledger.py").write_text("SCHEMA = 1\n")
    # `p<n> = sub.add_parser(...)`, the form `kernel/cli.py` uses. `flags_resolve`
    # parses the block each parser variable owns, so the bare-call form leaves it
    # with no subcommands at all and every flag in the document unexamined.
    (tmp / "kernel" / "cli.py").write_text(
        "".join(f'p{i} = sub.add_parser("{c}")\n'
                for i, c in enumerate(commands)))
    table = "\n".join(f"| `v4 {c}` | 做乜 |" for c in rows)
    spec = textwrap.dedent("""\
        ## 4. Kernel
        <!-- pinned: kernel/ledger.py::SCHEMA -->

        Long enough to count as a mechanism section. It describes how the ledger
        is laid out and what the append-only triggers do, and it pins the schema
        so renaming it fails this check instead of quietly making the paragraph
        wrong. Padding follows so the four-hundred-character rule fires here
        rather than skipping this as too short to be a mechanism. The `probe`
        checker is named so the other-direction rule is satisfied: probe.

        ### 指令表

        | 指令 | 做乜 |
        |---|---|
        """) + table + "\n" + extra + textwrap.dedent("""

        <!-- unbuilt-list -->

        | 未起 | 點解 |
        |---|---|
        | 跨 task 並行 | 押後到停損過咗先起 |

        ### 收尾

        ```
        ./bin/v4 --repo . audit
        ```
        """)
    (tmp / "docs" / "SPEC.md").write_text(spec, encoding="utf-8")
    return spec


class TheSevenFilesSpecTwelveFiveCallsContract(unittest.TestCase):
    """`user_facing_docs` -- what the only reader of documented commands reads."""

    def test_every_claude_role_and_command_is_in_the_corpus(self):
        read = {p.relative_to(ROOT).as_posix() for p in sc.user_facing_docs(ROOT)}
        owed = {p.relative_to(ROOT).as_posix()
                for sub in ("agents", "commands")
                for p in (ROOT / ".claude" / sub).glob("*.md")}
        self.assertTrue(owed, ".claude/ has no role or command documents")
        self.assertEqual(sorted(owed - read), [],
                         "SPEC §12.5 makes these contract and nothing opens them")

    def test_a_role_document_that_would_exit_127_is_reported(self):
        # The defect verbatim: `.claude/agents/reviewer.md` line 10 opened with
        # `v4 --repo . review lens`, measured at exit 127, command not found.
        with TemporaryDirectory() as td:
            tmp = Path(td)
            _mini_repo(tmp)
            (tmp / ".claude" / "agents").mkdir(parents=True)
            (tmp / ".claude" / "agents" / "reviewer.md").write_text(
                "開工:\n\n```\nv4 --repo . review lens --lens <名>\n```\n",
                encoding="utf-8")
            found = sc.launcher_is_reachable(tmp)
        self.assertEqual(len(found), 1, found)
        self.assertIn(".claude/agents/reviewer.md", found[0])
        self.assertIn("127", found[0])

    def test_the_same_document_saying_where_v4_comes_from_is_not(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            _mini_repo(tmp)
            (tmp / ".claude" / "commands").mkdir(parents=True)
            (tmp / ".claude" / "commands" / "run.md").write_text(
                "開工:\n\n```\n./bin/v4 --repo . doctor\n```\n", encoding="utf-8")
            self.assertEqual(sc.launcher_is_reachable(tmp), [])

    def test_a_finding_names_the_file_a_reader_can_open(self):
        # `flags_resolve` printed `docs/<name>` whatever it was handed, so a
        # finding about `.claude/agents/reviewer.md` read `docs/reviewer.md` --
        # a path nobody can open, in the one line whose job is to send somebody
        # to look.
        with TemporaryDirectory() as td:
            tmp = Path(td)
            _mini_repo(tmp)
            (tmp / ".claude" / "agents").mkdir(parents=True)
            (tmp / ".claude" / "agents" / "reviewer.md").write_text(
                "```\n./bin/v4 --repo . audit --nonesuch\n```\n", encoding="utf-8")
            found = sc.flags_resolve(tmp)
        self.assertEqual(len(found), 1, found)
        self.assertTrue(found[0].startswith(".claude/agents/reviewer.md:"), found[0])


class TheCommandTableIsAListNotAMentions(unittest.TestCase):
    """`cli_commands` -- and the reverse question asked of an inventory."""

    def test_a_command_mentioned_in_prose_but_absent_from_the_table(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            spec = _mini_repo(tmp, commands=("audit", "trend"), rows=("audit",),
                              extra="\n`v4 trend` 喺呢句度俾人提過一次。")
            self.assertIn("trend", sc.cli_commands(tmp))
            found = sc.check(tmp, spec)
        self.assertEqual(len(found), 1, found)
        self.assertIn("`v4 trend`", found[0])
        self.assertIn("table", found[0])

    def test_a_document_with_no_table_is_still_asked_the_older_question(self):
        # An adopter's SPEC is a page and a half with no inventory in it, and a
        # document with no command table cannot have an incomplete one.
        with TemporaryDirectory() as td:
            tmp = Path(td)
            _mini_repo(tmp, commands=("audit",), rows=(),
                       extra="\n`v4 audit` 喺呢句度俾人提過一次。")
            spec = (tmp / "docs" / "SPEC.md").read_text(encoding="utf-8")
            self.assertEqual(sc.command_rows(spec), set())
            self.assertEqual(sc.check(tmp, spec), [])

    def test_this_repo_keeps_a_row_for_every_command_it_has(self):
        spec = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
        missing = sorted(sc.cli_commands(ROOT) - sc.command_rows(spec))
        self.assertEqual(missing, [], "SPEC §13 says every command is in it")


class AFactsTableIsAPopulationThisRepoCanCount(unittest.TestCase):
    """`_reality` -- the one count nothing could settle."""

    def test_every_list_in_the_facts_table_is_counted(self):
        truth = sc._reality(ROOT)
        table = json.loads(facts_path_for(ROOT).read_text(encoding="utf-8"))
        owed = {f"{k} row": len(v) for k, v in table.items()
                if isinstance(v, list)}
        self.assertTrue(owed, "this repo declares no facts table")
        self.assertEqual({k: truth.get(k) for k in owed}, owed)

    def test_a_row_count_that_moved_is_reported(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            _mini_repo(tmp)
            (tmp / ".v4" / "facts.probe.json").write_text(json.dumps({
                "repo": "probe",
                "auth_decision": [{"pattern": p} for p in "abc"],
            }))
            (tmp / "docs" / "FACTS.md").write_text(
                "So: 2 auth_decision rows. All of them decide who may do what.\n",
                encoding="utf-8")
            found = sc.counted_claims(tmp)
        self.assertEqual(len(found), 1, found)
        self.assertIn("says 2 auth_decision row, and there are 3", found[0])

    def test_a_bare_number_is_still_not_a_claim_about_a_table(self):
        # Dropping the quantifier outright turns 0 findings into 50 on this
        # repo's `docs/`, and `v4 check` read as four checks is the shortest of
        # the fifty to explain.
        with TemporaryDirectory() as td:
            tmp = Path(td)
            _mini_repo(tmp)
            (tmp / ".v4" / "facts.probe.json").write_text(json.dumps({
                "repo": "probe",
                "auth_decision": [{"pattern": p} for p in "abc"],
            }))
            (tmp / "docs" / "FACTS.md").write_text(
                "So: 2 rows, and `v4 check` is not four checks.\n",
                encoding="utf-8")
            self.assertEqual(sc.counted_claims(tmp), [])

    def test_this_repos_own_documents_settle(self):
        self.assertEqual(sc.counted_claims(ROOT), [])


class HowManyCheckersKeepTheirJudgementOutOfTheEntryFile(unittest.TestCase):
    """SPEC §4 stated a number and nothing could settle it.

    The sentence said "13 of 21 are a thin CLI", and `thin` had no derivation
    anybody could reproduce: defines only `main` gives 7, has a same-named
    analysis module gives 9, under a hundred inline lines gives 14, imports
    `kernel.analysis` at all gives 17. Four answers to one number, in the
    document this checker exists to hold to its own claims -- and
    `21 個 checker` was checked while the other half of the same sentence was
    not. So the document moved onto the one measure that needs no judgement,
    and the measure moved into `_reality` where the checker can reach it.
    """

    #: The population, and the phrase a sentence has to use to be settled
    #: against it. `_reality` carries both spellings while this document is
    #: part-way through a translation; the test anchors on the one SPEC.md
    #: actually says, which is now the English.
    KEY = "main-only checker"

    def test_the_repo_can_count_the_checkers_that_only_dispatch(self):
        truth = sc._reality(ROOT)
        want = 0
        for f in (ROOT / "checkers").glob("*.py"):
            if f.name.startswith("_"):
                continue
            names = [n.name for n in ast.parse(f.read_text()).body
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef))]
            want += 1 if names == ["main"] else 0
        self.assertEqual(truth.get(self.KEY), want)
        self.assertGreater(want, 0, "no checker keeps its judgement outside")

    def test_and_a_number_that_moved_is_reported(self):
        """The half that was unreachable: a stated count nothing settled."""
        spec = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
        n = sc._reality(ROOT)[self.KEY]
        good = f"{n} {self.KEY}"
        self.assertIn(good, spec, "SPEC no longer states this count")
        broken = spec.replace(good, f"{n + 7} {self.KEY}")
        # `counted_claims` reads the document from disk, so the moved number is
        # put where it reads from rather than passed in -- the same shape the
        # checker runs in.
        spec_path = ROOT / "docs" / "SPEC.md"
        try:
            spec_path.write_text(broken, encoding="utf-8")
            problems = sc.counted_claims(ROOT)
        finally:
            spec_path.write_text(spec, encoding="utf-8")
        self.assertTrue([p for p in problems if self.KEY in p], problems[:5])

    def test_the_judgement_answers_without_a_process(self):
        """The finding that started this, asked as behaviour rather than shape.

        1052 of 1344 lines lived in the CLI, so the only way to ask this
        checker anything was to spawn it and read an exit code. What changed is
        not that the entry file is short -- it is that `check` runs here, in
        this process, and finds what a subprocess would have printed. A test
        that only parsed the entry file would go green on an empty module,
        which is the shape `test-shape` exists to refuse.
        """
        with TemporaryDirectory() as td:
            tmp = Path(td)
            _mini_repo(tmp)
            spec = tmp / "docs" / "SPEC.md"
            spec.write_text("# s\n\n## 機制\n\n`v4 no-such-command` 做一啲嘢。\n"
                            + "散文一行。\n" * 40, encoding="utf-8")
            problems = sc.check(tmp, spec.read_text(encoding="utf-8"))
        self.assertTrue([p for p in problems if "no-such-command" in p],
                        problems[:5])

    # There was a second test here that parsed `checkers/spec_coverage.py` and
    # asserted it defines only `main`. `test-shape` refused it -- "a test that
    # reads source text passes whatever the code does" -- and it was right: the
    # property is that the judgement is reachable, and the test above proves
    # that by reaching it. The count is not lost either: `_reality` derives it
    # the same way and `counted_claims` holds SPEC to it, which is a mechanism
    # rather than an assertion about one file.


if __name__ == "__main__":
    unittest.main()
