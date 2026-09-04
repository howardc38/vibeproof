"""``(ran=True, claims=0)`` had four causes and one row.  This is the fourth.

    python3 -m unittest tests.test_nothing_seen -v

Measured on the reference adopter: 2,842 of 3,502 ``detector_run`` rows are
``ran=True, claims=0``, and nothing on the row separates a clean tree from a
subject holding nothing the detector reads.  Measured on a TypeScript and Go
fixture with no ``.py`` at all: 7 claims from 11 detectors, and ``v4 ship``
printed SHIP.

The first version answered the fourth cause by counting ``.py`` in the subject.
That was a proxy, and it stopped being one on 2026-08-22 when thirteen checkers
learned to read Go: on a Go repo every detector that raised nothing was listed
as "not asked", including ``fail_closed.py`` -- whose claim was printed as
blocked eight lines below, in the same report.  ``derive`` now asks each
detector's own ``reads``.

SPEC.md §2 already names the shape -- *change the suffix set from ``.py`` to
``.pyx`` and every task passes, while the ship report keeps printing that it
ran* -- so what was missing was not the insight but a field.

Three things pinned here:

* **A subject holding nothing this detector reads is reported.**  That is the
  whole point.
* **A subject it *can* read is not**, whatever language it is written in.
* **A subject with no files at all is not.**  Nothing to read because nothing
  changed is a clean answer, and warning on it would mark every
  documentation-only task.
* **Rows written before the field existed are not read as evidence.**  Absence
  of an opinion is not an opinion, and treating it as one would drop every
  historical task into this list on the first ship after the upgrade.
"""

from __future__ import annotations

import sys
import json
import shutil
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import cli as cli_mod  # noqa: E402
from kernel.lifecycle import _nothing_seen  # noqa: E402


def row(*, ran=True, claims=0, files=None, could_read=None):
    out = {"ran": ran, "claims": claims}
    if files is not None:
        out["considered"] = {"files": files}
    if could_read is not None:
        out["could_read"] = could_read
    return out


class WhatCountsAsNothingSeen(unittest.TestCase):
    def test_a_subject_it_cannot_read_is_reported(self):
        got = _nothing_seen({"route_auth.py": row(files=3, could_read=False)})
        self.assertEqual(got, [("route_auth.py", 3)])

    def test_a_subject_it_can_read_is_not(self):
        self.assertEqual(
            _nothing_seen({"route_auth.py": row(files=3, could_read=True)}), [])

    def test_a_go_subject_does_not_silence_a_detector_that_reads_go(self):
        """The regression this field was rewritten for.

        Counting `.py` made every detector on a Go repo look unasked -- and one
        of them, `fail_closed.py`, had a claim in the blocked list of the same
        report. Nothing about the subject's language decides this now: the
        detector's own `reads` does.
        """
        self.assertEqual(
            _nothing_seen({"fail_closed.py": row(files=3, could_read=True)}), [])

    def test_a_detector_that_raised_something_is_not_reported(self):
        self.assertEqual(
            _nothing_seen({"route_auth.py": row(claims=2, files=3, could_read=False)}), [])

    def test_a_detector_that_did_not_run_is_left_to_detectors_not_run(self):
        """Refused and completed-blind are different failures and are printed as
        two lines. Counting a refusal here would put it in both."""
        self.assertEqual(
            _nothing_seen({"route_auth.py": row(ran=False, files=3, could_read=False)}), [])


class WhatMustNotBeReported(unittest.TestCase):
    def test_an_empty_subject_is_silent(self):
        """A documentation-only task changes no files a detector reads. That is
        a clean answer, not a blind one."""
        self.assertEqual(_nothing_seen({"route_auth.py": row(files=0, could_read=False)}), [])

    def test_a_row_without_the_field_is_silent(self):
        """Every `detector_run` row written before this field existed. Reading
        their silence as "saw nothing" would flood the first ship after the
        upgrade with history."""
        self.assertEqual(_nothing_seen({"route_auth.py": row()}), [])

    def test_no_verdict_is_not_a_no(self):
        """`could_read` is None for an unregistered detector, or a kind that
        declared no `reads`. `None` means nobody answered, and reading it as
        "there was nothing to read" is the fail-open this whole field exists
        to close."""
        self.assertEqual(_nothing_seen({"route_auth.py": row(files=3)}), [])


class TheReportIsStable(unittest.TestCase):
    def test_detectors_come_back_sorted(self):
        got = _nothing_seen({
            "route_auth.py": row(files=2, could_read=False),
            "always_lint.py": row(files=2, could_read=False),
            "dal_write.py": row(files=2, could_read=False)})
        self.assertEqual([n for n, _ in got],
                         ["always_lint.py", "dal_write.py", "route_auth.py"])


class TheFieldIsWritten(unittest.TestCase):
    def test_derive_records_what_the_subject_held(self):
        """The rollup is worth nothing if the row it reads is never written.

        Run, not grepped. The first version asserted that `derive.py` contains
        `considered = {"files": len(subject_files)`, and mutating the count to
        `1 + sum(...)` -- so the count can never be 0 and this rollup can never
        fire -- left every test green. The string was still there; the number
        was wrong.

        Both directions, in one repo: a detector whose kind reads `**/*.py`
        against a subject holding `notes.md` records False, and one whose kind
        reads `**/*.md` against the same subject records True. One direction
        alone would pass with `_could_read` hard-wired to its answer.
        """
        import json
        import subprocess
        import tempfile
        from kernel import config as config_mod, derive as derive_mod, ledger
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps({
            "probe-py": {"checker": "probe-py", "detector": "always_probe.py",
                         "applies_to": "always", "staleness": "repo",
                         "engagement": False, "question_template": "q"},
            "probe-md": {"checker": "probe-md", "detector": "always_prose.py",
                         "applies_to": "always", "staleness": "repo",
                         "engagement": False, "question_template": "q"}}))
        (tmp / ".v4" / "checkers.json").write_text(json.dumps({
            "probe-py": {"path": "checkers/probe_py.py", "reads": ["**/*.py"]},
            "probe-md": {"path": "checkers/probe_md.py", "reads": ["**/*.md"]}}))
        (tmp / "detectors").mkdir()
        (tmp / "detectors" / "always_probe.py").write_text("pass\n")
        (tmp / "detectors" / "always_prose.py").write_text("pass\n")
        (tmp / "notes.md").write_text("# not python\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "in"], cwd=tmp, capture_output=True)
        conn = ledger.connect(tmp)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026-08-19T00:00:00+00:00")
        derive_mod.derive(conn, config_mod.RepoConfig(tmp), task_id="t1",
                          scope_globs=["**"], subject_files=["notes.md"],
                          phase="open", detectors_dir=tmp / "detectors")
        rows = {json.loads(r["payload"])["detector"]: json.loads(r["payload"])
                for r in conn.execute(
                    "SELECT payload FROM event WHERE kind = 'detector_run'")}
        self.assertEqual(rows["always_probe.py"]["considered"]["files"], 1, rows)
        self.assertIs(rows["always_probe.py"]["could_read"], False,
                      "its kind reads **/*.py and the subject is notes.md")
        self.assertIs(rows["always_prose.py"]["could_read"], True,
                      "its kind reads **/*.md and the subject is notes.md")

    def test_ship_prints_it(self):
        """Behavioural, not a grep.

        The first version of this asserted that `cli.py` contains the string
        "NOTHING SEEN". A monitor session mutated the line to
        `if False and rep.get("nothing_seen"):` and all 777 tests stayed green,
        which is the exact shape `test-sufficiency` exists to catch: the source
        still contains the string, and the branch is dead.
        """
        import io, contextlib
        rep = {"converged": True, "rounds": [1], "claims": [], "blocked": [],
               "chain_ok": True, "chain_problems": [], "facts_unconfirmed": [],
               "deferred": [], "detectors": {}, "detectors_not_run": [],
               "nothing_seen": [("route_auth.py", 3), ("dal_write.py", 3)],
               "lenses_run": []}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_mod._print_nothing_seen(rep)
        out = buf.getvalue()
        self.assertIn("NOTHING SEEN", out)
        self.assertIn("route_auth.py", out)
        self.assertIn("dal_write.py", out)

    def test_ship_says_nothing_when_there_is_nothing_to_say(self):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_mod._print_nothing_seen({"nothing_seen": []})
        self.assertEqual(buf.getvalue(), "")

    def test_the_report_carries_it(self):
        """Asked of the report, not of the source that builds it."""
        import inspect
        from kernel import lifecycle
        sig = inspect.signature(lifecycle._nothing_seen)
        self.assertEqual(len(sig.parameters), 1)
        rep_keys = _ship_report_keys()
        self.assertIn("nothing_seen", rep_keys)


def _ship_report_keys():
    """Every key `lifecycle.ship` puts in its report, by running it.

    A repo with one task, no claims and no detectors converges immediately,
    which is enough to see the shape of what ship hands back.
    """
    import json
    import subprocess
    import tempfile
    from kernel import config as config_mod, ledger, lifecycle
    tmp = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk"}))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    (tmp / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "in"], cwd=tmp, capture_output=True)
    conn = ledger.connect(tmp)
    try:
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026-08-19T00:00:00+00:00")
        conn.commit()
        _ok, rep = lifecycle.ship(conn, config_mod.RepoConfig(tmp), "t1")
        return set(rep)
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
