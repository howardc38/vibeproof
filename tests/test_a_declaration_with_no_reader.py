"""Repairs in `kernel/analysis/facts_grammar.py`, `docs/FACTS.md`,
`.v4/facts.vibeproof.json`, `hooks/stop_gate.py` and `kernel/doctor.py`.

    python3 -m unittest tests.test_a_declaration_with_no_reader -v

One fact, four places: something is written down and nothing can act on it.

  a key a repo may declare whose meaning is documented nowhere
  a table of who may do what that names a classifier and misses the surface
    that refuses
  a report whose table of contents is twenty-six names cut off mid-phrase

All of them fail against 0785a00.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hooks"))

from kernel import config, doctor, facts, ledger  # noqa: E402
from kernel.analysis import facts_grammar  # noqa: E402

# Asked, not spelled. This held the table's filename as a literal until the repo
# was renamed, and a test that pins the repo's name fails for a reason that has
# nothing to do with what it checks. Rebuilding the name from the directory was
# the second version and no better: it is the tenth spelling of a question
# `config.facts_path_for` exists to be the only answer to, and it breaks in any
# clone whose directory is not named after the repo.
TABLE = config.facts_path_for(ROOT)


class EveryOptionalFactsKeyHasADocumentedMeaning(unittest.TestCase):
    """`OPTIONAL_LISTS` declared three keys and the document named two.

    `docs/FACTS.md` is what README makes the owner of this file's format, and it
    opened its Optional fields section with "Two lists a repo may declare" while
    `validate` had been accepting `route_receivers` for as long as
    `route_auth.receivers` had been reading it. A repo whose app object is
    `admin_api` could have said so in one line and had no way to learn that.
    """

    def _documented(self) -> str:
        return (ROOT / "docs" / "FACTS.md").read_text(encoding="utf-8")

    def test_every_declarable_key_is_in_the_document(self):
        said = self._documented()
        for key in facts_grammar.OPTIONAL_LISTS:
            self.assertIn(f"`{key}`", said,
                          f"{key} can be declared and means nothing to a reader")

    def test_the_document_does_not_undercount_them(self):
        said = self._documented()
        self.assertNotIn("Two lists a repo may declare", said,
                         "the count in the prose is the thing that went stale")

    def test_and_the_grammar_still_accepts_all_three(self):
        """The document is only right if the code agrees: this is the table a
        repo would write after reading it, handed to the validator."""
        table = {
            "repo": "fx", "generated_from_commit": "a" * 40,
            "outbound_write": [{"pattern": "requests.post", "seen_at": "a.py:1",
                                "kind": "http"}],
            "outbound_read": [{"pattern": "requests.get", "seen_at": "a.py:2",
                               "kind": "http"}],
            "auth_decision": [{"pattern": "check_auth", "seen_at": "a.py:3",
                               "kind": "authz"}],
            "entrypoint_globs": ["app/**"], "ui_globs": [], "config_files": [],
            "protected_paths": [".v4/**"],
            "public_routes": ["app/health.py::health"],
            "dal_globs": ["app/store/**"],
            "route_receivers": ["admin_api"],
        }
        facts_grammar.validate(table, source="<fixture>")
        built = facts_grammar.build(table, source="<fixture>")
        self.assertEqual(built.repo, "fx")


class TheAuthTableNamesEverySurfaceThatRefuses(unittest.TestCase):
    """The table named a classifier and missed a refusal.

    `_matches_auth` returns a label or `None` -- it can neither raise nor refuse
    -- and sat in `kernel/analysis/**`, the layer whose own contract is that it
    decides nothing and touches nothing. Meanwhile `hooks/stop_gate.py` emitted
    three `decision: block` refusals and appeared in this table nowhere, while
    both sibling hooks were covered. One edit, because the table was wrong in
    both directions at once.
    """

    def setUp(self):
        if TABLE is None:
            # `config.facts_path_for` refuses to guess between two tables when
            # the checkout's directory is not named after the repo, which is
            # every clone into a directory of the cloner's choosing. Saying so
            # is the point: the alternative was reading the adopter's table and
            # reporting, quietly, that this repo declares no auth decisions.
            raise unittest.SkipTest(
                "no facts table can be identified in a checkout named "
                f"{ROOT.name!r}, so the three checks below -- that every hook "
                "which refuses is reached by a row, that no row names the "
                "pure-analysis layer, and that the stop-gate row cites a line "
                "that holds -- are not asked here. Clone into a directory "
                "named after the repo to put them.")
        self.table = facts.load(TABLE)
        self.hits = facts.scan_repo(self.table, ROOT, "auth_decision")
        self.files = {w.split(":")[0] for ws in self.hits.values() for w in ws}

    def test_every_hook_that_refuses_is_reached_by_a_row(self):
        for hook in ("hooks/bash_guard.py", "hooks/write_block.py",
                     "hooks/stop_gate.py"):
            self.assertIn(hook, self.files,
                          f"{hook} decides who may do what and this table "
                          f"says nothing about it")

    def test_no_row_names_the_layer_that_decides_nothing(self):
        for entry in self.table.auth_decision:
            self.assertFalse(
                entry.seen_at_path.startswith("kernel/analysis/"),
                f"{entry.pattern} is cited in the pure-analysis layer, which "
                f"classifies names and cannot refuse anything")

    def test_the_row_for_the_stop_gate_cites_a_line_that_holds(self):
        problems = [p for p in facts.check_seen_at(self.table, ROOT)
                    if "refuse_the_stop" in p]
        self.assertEqual(problems, [])


class TheStopGateRefusalGoesThroughOneNamedDecision(unittest.TestCase):
    """Three `decision: block` payloads, built inline, named by nothing.

    The cost was not duplication. `hooks/**` is in `entrypoint_globs` and this
    is the one entry surface here that refuses, so "who may end a turn" was
    decided by a file the repo's record of who may do what could not point at.
    """

    def _repo(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"repo": "fx", "test_command": "true",
             "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        # What `install.write_launcher` records: where the framework lives, so
        # the gate's own child process can import the kernel the way it does
        # in an adopter.
        (tmp / ".v4" / "home").write_text(str(ROOT))
        conn = ledger.connect(tmp)
        ledger.insert(conn, "task", id="t-fx", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026-08-27T00:00:00+00:00")
        conn.close()
        return tmp

    def _stop(self, root, payload):
        """Run the gate the way the platform does: a payload in, a verdict out."""
        import stop_gate
        said, stdin = io.StringIO(), sys.stdin
        env = {"V4_REPO": str(root), "V4_TASK": "t-fx"}
        with unittest.mock.patch.dict(os.environ, env):
            sys.stdin = io.StringIO(json.dumps(payload))
            try:
                with contextlib.redirect_stdout(said):
                    code = stop_gate.main()
            finally:
                sys.stdin = stdin
        return code, said.getvalue()

    def test_a_turn_that_owes_something_is_refused_through_the_named_symbol(self):
        import stop_gate
        root = self._repo()
        seen = []
        real = stop_gate.refuse_the_stop

        def recording(reason):
            seen.append(reason)
            return real(reason)

        with unittest.mock.patch.object(stop_gate, "refuse_the_stop", recording):
            code, said = self._stop(root, {"session_id": "s1"})
        self.assertEqual(code, 0, "a hook that refuses still exits 0")
        self.assertEqual(len(seen), 1, "the refusal did not go through the "
                                       "symbol the facts table names")
        self.assertEqual(json.loads(said)["decision"], "block")
        self.assertIn("t-fx", json.loads(said)["reason"])

    def test_and_the_second_ask_is_let_through(self):
        """`stop_hook_active` is the loop guard, and it still runs first."""
        root = self._repo()
        _code, said = self._stop(root, {"session_id": "s1",
                                        "stop_hook_active": True})
        self.assertEqual(json.loads(said), {})

    def test_the_refusal_shape_is_the_one_the_platform_reads(self):
        import stop_gate
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            code = stop_gate.refuse_the_stop("a reason a worker can act on")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(said.getvalue()),
                         {"decision": "block",
                          "reason": "a reason a worker can act on"})


#: Words a name cannot end on. A name cut off before its object -- `..._the`,
#: `..._that_need`, `..._is` -- is the shape `run()`'s call list was made of.
_DANGLING = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "at", "for",
             "with", "that", "which", "what", "is", "are", "was", "were", "be",
             "not", "no", "this", "it", "its", "has", "have", "had", "can",
             "will", "from", "by", "as", "still", "into", "than", "then",
             "when", "where", "who", "how"}

#: The docstring twenty-two of them shared.
_BOILERPLATE = "One row group of `v4 doctor`."


class EveryDoctorRowSaysWhatItAsks(unittest.TestCase):
    """`run()`'s call list is the table of contents for the whole report.

    Twenty-six checks were named by truncating their section comment mid-phrase
    and every one carried the same docstring, so the list said nothing about any
    row in it -- and the command's own subject is whether this repo only looks
    wired. The names are the summary; this is what keeps them one.
    """

    def _checks(self):
        return {name: fn for name, fn in vars(doctor).items()
                if name.startswith("_check") and callable(fn)}

    def test_no_name_stops_before_its_object(self):
        for name in self._checks():
            self.assertNotIn(name.rsplit("_", 1)[-1], _DANGLING,
                             f"{name} is a phrase cut off mid-way")

    def test_each_one_says_what_it_asks_and_no_two_say_the_same(self):
        firsts = {}
        for name, fn in self._checks().items():
            doc = (fn.__doc__ or "").strip()
            self.assertTrue(doc, f"{name} has no docstring")
            first = doc.splitlines()[0].strip()
            self.assertNotEqual(first, _BOILERPLATE,
                                f"{name} carries the shared docstring")
            self.assertNotIn(first, firsts,
                             f"{name} and {firsts.get(first)} say the same thing")
            firsts[first] = name

    def test_the_report_still_runs_and_answers(self):
        """The rename is only right if the call list still calls them."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"repo": "fx", "test_command": "true"}))
        doctor._close_reader()
        rows = doctor.run(tmp)
        doctor._close_reader()
        self.assertTrue(rows)
        self.assertIn("config", {r["what"] for r in rows})


class ATableCitingNothingIsNotATableThatValidates(unittest.TestCase):
    """`v4 doctor` printed `ok facts ... validates` and asked nothing else.

    `facts.validate` reads the shape of the table -- keys, kinds, match modes
    -- and never whether a row still cites something that is here. So a table
    whose every row named a symbol a refactor had renamed away read exactly
    like a sound one, in the command whose stated job is telling wired apart
    from looks-wired -- while two rows of the same report already ask that
    question of protected paths and of `file::symbol` references.

    Measured 2026-08-27 in the reference adopter: two refactors on one day left
    rows citing nothing, both with every gate green. What would have caught the
    first is wired into this framework's own CI, and `v4 install` does not copy
    a workflow -- so the obligation travelled to adopters and the mechanism did
    not.
    """

    def _repo(self, seen_at, body):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / ".v4").mkdir()
        (root / "app.py").write_text(body)
        table = {"repo": root.name, "generated_from_commit": "0" * 40,
                 "auth_decision": [{"pattern": "authorise", "seen_at": seen_at,
                                    "kind": "authz", "match": "symbol"}],
                 "outbound_write": [], "outbound_read": [],
                 "entrypoint_globs": [], "ui_globs": [],
                 "config_files": [], "protected_paths": [],
                 "absent": {n: "fixture: one auth_decision row only"
                            for n in facts_grammar.SYMBOL_LISTS
                            + facts_grammar.PATH_LISTS
                            if n != "auth_decision"
                            and n not in facts_grammar.MAY_BE_EMPTY}}
        (root / ".v4" / f"facts.{root.name}.json").write_text(
            json.dumps(table, indent=2))
        return root

    def _rows(self, root, what="facts"):
        out = []
        doctor._check_facts_load_under_the_name_the_loader_prefers(root, out)
        return [c for c in out if c["what"] == what]

    def test_a_row_citing_nothing_is_not_ok(self):
        root = self._repo("app.py:1", "def something_else():\n    pass\n")
        rows = self._rows(root)
        self.assertEqual([c["status"] for c in rows], [doctor.BAD], rows)
        self.assertIn("no longer there", rows[0]["detail"])

    def test_a_row_that_still_cites_something_is_ok(self):
        root = self._repo("app.py:1", "authorise()\n")
        self.assertEqual([c["status"] for c in self._rows(root)], [doctor.OK])

    def test_a_line_that_only_moved_is_a_warning_beside_the_verdict(self):
        """A citation is true of a commit. Drift is not the table being wrong,
        and calling it BAD would fire this row on every ordinary edit."""
        root = self._repo("app.py:1", "\n\n\nauthorise()\n")
        self.assertEqual(self._rows(root)[0]["status"], doctor.OK)
        drift = self._rows(root, what="facts drift")
        self.assertEqual([c["status"] for c in drift], [doctor.WARN], drift)


if __name__ == "__main__":
    unittest.main(verbosity=2)
