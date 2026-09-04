"""Repairs in `kernel/analysis/fail_closed.py`, `kernel/analysis/dal_write.py`,
`checkers/registry_consistency.py` and `checkers/spec_coverage.py`.

    python3 -m unittest tests.test_a_rule_reads_the_thing_it_names -v

Each of these is a rule that answered about something adjacent to its subject:
a key's presence instead of its value, a raise about another variable, the
letters in a path instead of its segments, whichever table sorted first.

All of them fail against 0ad6b61.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "checkers"))

from kernel import config as config_mod  # noqa: E402
from kernel.analysis import fail_closed  # noqa: E402

import registry_consistency  # noqa: E402
# The judgement, not the CLI: `spec_coverage`'s rules moved to
# `kernel/analysis/` where SPEC §12 step 1 puts them, and reaching them
# without spawning a process is the whole point of the move.
from kernel import spec_coverage  # noqa: E402


def _repo(case):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    return tmp


class AKeyBeingThereIsNotAnErrorBeingReported(unittest.TestCase):
    """`{"ok": True, "error": None}` was read as an explicit error report.

    `_verdict_dict_says_failure` returned True on the presence of the key,
    whatever its value, and `assigned_failure` was set the same way by
    `meta["error"] = None` -- after which *any* `return <name>` counted as
    reporting the failure.
    """

    def _swallows(self, src):
        return [f.variant for f in fail_closed.analyse_source(src, path="x.py")]

    def test_a_bare_swallow_is_reported(self):
        self.assertIn("swallow", self._swallows(
            "def f(url, body):\n"
            "    try:\n        requests.post(url, json=body)\n"
            "    except Exception:\n        pass\n"))

    def test_a_handler_saying_there_was_no_error_is_still_a_swallow(self):
        self.assertIn("swallow", self._swallows(
            "def f(url, body):\n"
            "    try:\n        requests.post(url, json=body)\n"
            "    except Exception:\n        return {'ok': True, 'error': None}\n"))

    def test_and_the_same_through_an_assignment(self):
        self.assertIn("swallow", self._swallows(
            "def f(url, body, cached):\n"
            "    try:\n        requests.post(url, json=body)\n"
            "    except Exception:\n"
            "        meta = {}\n        meta['error'] = None\n        return cached\n"))

    def test_a_handler_that_does_report_one_is_not(self):
        self.assertEqual(self._swallows(
            "def f(url, body):\n"
            "    try:\n        requests.post(url, json=body)\n"
            "    except Exception as e:\n        return {'ok': False, 'error': str(e)}\n"),
            [])


class TheRaiseHasToBeAboutWhatWasSwallowed(unittest.TestCase):
    """Reachability was the only narrowing there was.

    So an ordinary guard clause -- `if payload is None: raise ValueError(...)`,
    which is in almost every function that takes an argument -- sealed a
    `requests.post` swallow, and the scan has no distance limit, so it could be
    two hundred lines away and about a different variable.
    """

    def _swallows(self, src):
        return [f.variant for f in fail_closed.analyse_source(src, path="x.py")]

    def test_an_unrelated_guard_seals_nothing(self):
        self.assertIn("swallow", self._swallows(
            "def f(url, body, payload):\n"
            "    try:\n        requests.post(url, json=body)\n"
            "    except Exception:\n        pass\n"
            "    if payload is None:\n        raise ValueError('x')\n"
            "    return 1\n"))

    def test_and_the_read_back_idiom_still_does(self):
        """The case the docstring cites: the raise reads what the write wrote."""
        self.assertEqual(self._swallows(
            "def f(service, account):\n"
            "    try:\n        keyring.delete_password(service, account)\n"
            "    except Exception:\n        pass\n"
            "    if read_provider_secret(account) is not None:\n"
            "        raise SecureStoreError('x')\n"
            "    return 1\n"), [])


class WhichTableThisRepoIsJudgedBy(unittest.TestCase):
    """`sorted(root.glob(".v4/facts*.json"))[0]`, with no preference.

    A repo carrying two tables is judged against whichever name sorts first.
    This one used to carry two and escaped that only because its own sorts
    first; it carries one now, and the second lives under
    `tests/fixtures/facts/`. The cases below build their own tables in a
    temporary tree, which is why they still ask the question after the move --
    and why the second table in `.v4/` was never what proved it.
    """

    def test_the_repos_own_name_wins_over_alphabetical_order(self):
        root = _repo(self)
        (root / ".v4" / "facts.aaa_other.json").write_text(json.dumps({"absent": {}}))
        (root / ".v4" / f"facts.{root.name}.json").write_text(json.dumps({"absent": {}}))
        got = config_mod.facts_path_for(root)
        self.assertEqual(got.name, f"facts.{root.name}.json")

    def test_and_a_draft_is_never_the_table(self):
        root = _repo(self)
        (root / ".v4" / f"facts.{root.name}.json.draft").write_text("{}")
        self.assertIsNone(config_mod.facts_path_for(root))


class AFileThatWillNotParseIsNotARuleThatDidNotLand(unittest.TestCase):
    """Two `except (OSError, JSONDecodeError): pass` blocks.

    A lens file or `claim_kinds.json` that does not parse contributed no landed
    ids, so every rule that landed there was reported as not landed -- and the
    repair a reader is pushed toward (re-land the rule) is not the repair the
    repo needs (fix the file).
    """

    def test_the_unreadable_file_is_named(self):
        root = _repo(self)
        (root / ".v4" / "rule_dispositions.json").write_text(json.dumps(
            {"rules": [{"id": "R-1", "layer": 3, "lands_in": ".v4/lenses/one.json"}]}))
        (root / ".v4" / "lenses").mkdir()
        (root / ".v4" / "lenses" / "one.json").write_text("{ not json")
        problems = registry_consistency._dispositions_hold(root)
        self.assertTrue([p for p in problems if "one.json" in p], problems)

    def test_an_absence_a_scan_contradicts_is_reported(self):
        """The other half of the same call: a declaration that says this repo
        has none, against a scan of the repo that finds one."""
        root = _repo(self)
        (root / ".v4" / f"facts.{root.name}.json").write_text(json.dumps(
            {"absent": {"outbound_write": "nobody here calls out"}}))
        (root / "app.py").write_text(
            "import requests\n\n\ndef go(u):\n    requests.post(u)\n")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        got = registry_consistency._absence_still_holds(root)
        self.assertTrue([p for p in got if "outbound_write" in p], got)

    def test_the_installer_written_absences_are_filtered_by_the_shared_constant(self):
        """`"AUTO:"` was a literal here while `facts.AUTO_PREFIX` defines it,
        and its own warning says a ship gate held together by a string typed in
        three modules is what this is."""
        from kernel import facts as facts_mod
        root = _repo(self)
        (root / ".v4" / f"facts.{root.name}.json").write_text(json.dumps(
            {"absent": {"outbound_write": f"{facts_mod.AUTO_PREFIX} nobody read this"}}))
        (root / "app.py").write_text("import requests\nrequests.post('u')\n")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        self.assertEqual(registry_consistency._absence_still_holds(root), [],
                         "an installer-written absence is not a person's claim")


class WhatCountsAsADocumentThatTellsYouWhatToType(unittest.TestCase):
    """`docs/*.md` plus `CLAUDE.md`, and nothing else.

    `.github/monitor/PROMPT.md` and `SCOPE.md` -- the two files a monitor
    session is told to paste and follow, and the only user-facing documents in
    a protected path -- were read by no checker at all, which is how PROMPT.md
    shipped a `review close` with no `--claim`.
    """

    def test_the_monitor_briefs_are_read(self):
        names = {p.name for p in spec_coverage.user_facing_docs(ROOT)}
        self.assertIn("PROMPT.md", names)
        self.assertIn("SCOPE.md", names)
        self.assertIn("CLAUDE.md", names)

    def test_and_a_document_showing_a_command_says_where_v4_comes_from(self):
        self.assertEqual(spec_coverage.launcher_is_reachable(ROOT), [])

    def test_a_flag_no_subcommand_takes_is_reported_wherever_it_is_written(self):
        """The check itself, run against this repo: whatever it now reads, it
        has to agree with the CLI about every flag in it."""
        self.assertEqual(spec_coverage.flags_resolve(ROOT), [])


class WhatTheTraceabilityPromiseReaches(unittest.TestCase):
    """`改個 symbol,SPEC 就 fail 一個 check` did not hold for three directories.

    `PATH` matched `kernel|checkers|detectors|tests|docs|.v4` only, so the
    existence loop never saw `hooks/`, `bin/` or `tools/` -- and SPEC names
    `hooks/stop_gate.py`, `bin/v4` and `tools/runtime_probe.sh`. Renaming a
    hook file broke no check.
    """

    def test_a_hook_the_spec_names_and_the_repo_lacks_is_reported(self):
        problems = spec_coverage.check(
            ROOT, "The gate lives in `hooks/no_such_hook.py` and runs first.\n")
        self.assertTrue([p for p in problems if "no_such_hook" in p], problems)

    def test_and_so_is_a_launcher_and_a_tool(self):
        for named in ("bin/no_such_launcher", "tools/no_such_probe.sh"):
            problems = spec_coverage.check(ROOT, f"See `{named}` for the rest.\n")
            self.assertTrue([p for p in problems if named in p], (named, problems))

    def test_a_path_that_is_there_is_not(self):
        problems = spec_coverage.check(
            ROOT, "The gate lives in `hooks/write_block.py` and runs first.\n")
        self.assertEqual([p for p in problems if "write_block" in p], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
