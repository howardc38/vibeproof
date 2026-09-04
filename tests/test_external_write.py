"""Acceptance for the ``external-write`` detector and checker.

Run either way, no third-party anything::

    python3 -m unittest discover -s tests -t .
    python3 tests/test_external_write.py

Both executables are driven as **subprocesses**, never imported.  The exit code
is the product -- an in-process call would test the analysis and quietly skip
the only thing the kernel actually reads (SPEC.md §3: the exit code comes from
the OS, never from a claim).

What is asserted:

* every file under ``red/`` exits 1 and names a real line of itself on stdout
* every file under ``green/`` exits 0, individually and all together
* ``known_miss/`` exits 0 -- a documented blind spot, asserted so it cannot rot
  into an unexamined belief
* the two ``adopter_a`` defects this kind exists for, pre-fix and post-fix, on
  source lifted verbatim from the commits: ``f0060ebb`` and ``317e10a7``
* unverifiable input is 4, never 0; a broken checker is 5, never 1
* an unusable facts table is 4, never 0 -- scanning with no vocabulary would
  report every repo clean
* the detector is byte-identical across runs, including order
* **the convergence property** (SPEC.md §4 ②): answering a claim this detector
  raises does not raise another claim of the same kind
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DETECTOR = ROOT / "detectors" / "external_write.py"
CHECKER = ROOT / "checkers" / "external_write.py"
FIXTURES = ROOT / "tests" / "fixtures" / "external_write"
ADOPTER_FACTS = ROOT / "tests" / "fixtures" / "facts" / "facts.adopter_a.json"

RED = sorted((FIXTURES / "red").glob("*.py"))
GREEN = sorted((FIXTURES / "green").glob("*.py"))
KNOWN_MISS = sorted((FIXTURES / "known_miss").glob("*.py"))
#: The same defect rewritten to look like it evades. The gate demands
#: three and its first run found 13 real evasions across 20 checkers, so
#: this is the colour that carries the most information -- and it was the
#: one no test in this file read.
BYPASS = sorted((FIXTURES / "bypass").glob("*.py"))

EXIT_PASS, EXIT_FAIL, EXIT_CANNOT_VERIFY, EXIT_BROKEN = 0, 1, 4, 5
DETECTOR_SCANNED = 0


# ==============================================================================
# Driving the two executables the way the kernel does
# ==============================================================================

def subject(paths, *, repo_root=ROOT, symbol="", variant="", extra_refs=()):
    return {
        "claim_id": "test-claim",
        "claim_kind": "external-write",
        "task_id": "t-test",
        "repo_root": str(repo_root),
        "diff_base": "HEAD",
        "subject_refs": [{"kind": "file", "path": str(Path(p))} for p in paths]
        + list(extra_refs),
        "symbol": symbol,
        "variant": variant,
        "params": {},
    }


def run(tool: Path, payload, *, facts=None, argv_extra=()) -> subprocess.CompletedProcess:
    """Exactly ``kernel/runner.py``'s argv shape: --subject, optional --facts, --out."""
    with tempfile.TemporaryDirectory() as td:
        subj = Path(td) / "subject.json"
        subj.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        argv = [sys.executable, str(tool), "--subject", str(subj)]
        if facts is not None:
            fp = Path(td) / "facts.json"
            fp.write_text(facts if isinstance(facts, str) else json.dumps(facts))
            argv += ["--facts", str(fp)]
        argv += ["--out", str(Path(td) / "out.json"), *argv_extra]
        proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
        out = Path(td) / "out.json"
        proc.payload = json.loads(out.read_text()) if out.is_file() else None  # type: ignore[attr-defined]
        return proc


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def check(paths, **kw):
    return run(CHECKER, subject([rel(p) for p in paths], **kw))


def detect(paths, **kw):
    return run(DETECTOR, subject([rel(p) for p in paths], **kw))


# ==============================================================================
# Source lifted verbatim from the two commits this claim kind exists for
# ==============================================================================

#: ``adopter_a f0060ebb^:core/integrations/feed_graph.py::_recent_media_ids``.
#: One of the three duplicate-publish causes ``f0060ebb`` names.  Verbatim; the
#: enclosing class and its imports are the only things removed.
F0060EBB_PRE = '''
class MetaGraphAdapter:
    def _recent_media_ids(self, *, edge: str, limit: int = 25) -> set:
        """F210 B4: read back the set of recent IG media ids."""
        payload = self._request_with_retry(
            "GET", f"{self._ig_user_id}/{edge}", {"fields": "id", "limit": str(limit)}, retryable=True,
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return set()
        return {str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")}
'''

#: The same function at ``f0060ebb``.  One branch differs.
F0060EBB_POST = '''
class MetaGraphAdapter:
    def _recent_media_ids(self, *, edge: str, limit: int = 25) -> set:
        """Raises on a payload that does not carry a list."""
        payload = self._request_with_retry(
            "GET", f"{self._ig_user_id}/{edge}", {"fields": "id", "limit": str(limit)}, retryable=True,
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise RuntimeError(
                f"Meta {edge} listing returned no data array; the media surface is unreadable"
            )
        return {str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")}
'''

#: A facts table with the two rows that make the verbatim source above
#: matchable: ``feed_graph.py`` passes the HTTP verb as an argument, so the
#: read is found by regex over the source line, not by a callee name.
FEED_FACTS = {
    "repo": "adopter_a",
    "generated_from_commit": "b" * 40,
    "outbound_write": [
        {"pattern": r"^\s*\"POST\",", "kind": "http", "seen_at": "core/x.py:1", "match": "regex"},
        {"pattern": ".publish", "kind": "http", "seen_at": "core/x.py:2"},
    ],
    "outbound_read": [
        {"pattern": r"^\s*\"GET\",", "kind": "http", "seen_at": "core/x.py:3", "match": "regex"},
    ],
    "auth_decision": [{"pattern": "check_permission", "kind": "authz", "seen_at": "core/x.py:4"}],
    "entrypoint_globs": ["core/workers/*.py"],
    "ui_globs": ["web/ui/**"],
    "config_files": ["pytest.ini"],
    "protected_paths": [".v4/**"],
}


class Fixtures(unittest.TestCase):
    def test_enough_fixtures_to_mean_something(self):
        self.assertGreaterEqual(len(RED), 5, "SPEC.md §3 requires at least 5 red")
        self.assertGreaterEqual(len(GREEN), 5, "SPEC.md §3 requires at least 5 green")
        self.assertGreaterEqual(len(KNOWN_MISS), 1)

    def test_every_fixture_parses(self):
        import ast

        for path in RED + GREEN + KNOWN_MISS:
            with self.subTest(path.name):
                ast.parse(path.read_text(encoding="utf-8"))

    def test_red_fixtures_fail(self):
        for path in RED:
            with self.subTest(path.name):
                res = check([path])
                self.assertEqual(res.returncode, EXIT_FAIL, res.stdout + res.stderr)

    def test_red_fixtures_name_a_real_line(self):
        """A checker that fails without saying where has answered nothing."""
        for path in RED:
            with self.subTest(path.name):
                res = check([path])
                lines = path.read_text(encoding="utf-8").splitlines()
                cited = [
                    int(part.split(":")[-1])
                    for part in res.stdout.split()
                    if part.startswith(rel(path) + ":")
                ]
                self.assertTrue(cited, res.stdout)
                for line in cited:
                    self.assertTrue(1 <= line <= len(lines), f"line {line} is not in the file")
                    self.assertTrue(lines[line - 1].strip(), f"line {line} is blank")

    def test_green_fixtures_pass(self):
        for path in GREEN:
            with self.subTest(path.name):
                res = check([path])
                self.assertEqual(res.returncode, EXIT_PASS, res.stdout + res.stderr)

    def test_whole_directories(self):
        self.assertEqual(check(GREEN).returncode, EXIT_PASS)
        self.assertEqual(check(RED).returncode, EXIT_FAIL)

    def test_one_red_among_greens_still_fails(self):
        res = check(GREEN + [RED[0]])
        self.assertEqual(res.returncode, EXIT_FAIL, res.stdout)

    def test_green_fixtures_also_pass_under_the_real_repo_table(self):
        """The gate runs without ``--facts``; a real adopter has one.

        A fixture that is only green because the built-in table cannot see its
        writes would prove nothing, so every green case is re-run against
        ``adopter_a``' 51-pattern table as well.
        """
        for path in GREEN:
            with self.subTest(path.name):
                res = run(CHECKER, subject([rel(path)]),
                          facts=json.loads(ADOPTER_FACTS.read_text()))
                self.assertEqual(res.returncode, EXIT_PASS, res.stdout + res.stderr)


class RealDefects(unittest.TestCase):
    """The two commits named in the task, on source lifted from them."""

    def _analyse(self, source: str, facts_obj):
        from kernel.analysis import external_write as analysis

        return analysis.analyse_source(source, path="feed_graph.py",
                                       table=analysis.table_from(facts_obj))

    def test_f0060ebb_pre_fix_is_flagged(self):
        findings = self._analyse(F0060EBB_PRE, FEED_FACTS)
        self.assertTrue(findings, "the f0060ebb^ read-back defect was not flagged")
        self.assertEqual({f.variant for f in findings}, {"readback"})
        self.assertEqual({f.shape for f in findings}, {"uninformative-readback"})

    def test_f0060ebb_post_fix_is_clean(self):
        """The fix answers the claim; without this the claim is unanswerable."""
        self.assertEqual(self._analyse(F0060EBB_POST, FEED_FACTS), [])

    def test_317e10a7_pair(self):
        """A fresh idempotency key fails; the sha256-derived one passes."""
        pre = FIXTURES / "red" / "per_call_idempotency_key.py"
        post = FIXTURES / "green" / "real_stable_idempotency_key.py"
        self.assertEqual(check([pre]).returncode, EXIT_FAIL)
        self.assertEqual(check([post]).returncode, EXIT_PASS)
        payload = check([pre]).payload
        self.assertEqual({f["shape"] for f in payload["findings"]}, {"per-call-idempotency-key"})
        self.assertEqual({f["variant"] for f in payload["findings"]}, {"replay"})


class KnownMiss(unittest.TestCase):
    """SPEC.md §10: a blind spot is a fixture with an assertion, not a paragraph."""

    def test_checker_misses_the_exception_classifier_defect(self):
        for path in KNOWN_MISS:
            with self.subTest(path.name):
                res = check([path])
                self.assertEqual(
                    res.returncode, EXIT_PASS,
                    "known_miss now fails -- if the rule really grew to see an "
                    "exception-classification allowlist, promote this file to red/ "
                    "and delete this test",
                )

    def test_detector_raises_no_claim_for_it(self):
        res = detect(KNOWN_MISS)
        self.assertEqual(res.returncode, DETECTOR_SCANNED)
        self.assertNotIn("V4-CLAIM", res.stdout)

    def test_the_construct_is_still_in_the_fixture(self):
        """Guards against the miss being 'fixed' by deleting the evidence."""
        text = (FIXTURES / "known_miss" / "exception_classifier_allowlist.py").read_text()
        self.assertIn("submission_outcome_is_unknown", text)
        self.assertIn("isinstance(", text)
        self.assertIn("_close_submission_baseline", text)


class CannotVerify(unittest.TestCase):
    """4 is not 0.  "I could not read it" must never be "there is nothing wrong"."""

    def test_non_python_file(self):
        res = check([ROOT / "docs" / "SPEC.md"])
        self.assertEqual(res.returncode, EXIT_CANNOT_VERIFY, res.stdout)

    def test_missing_file(self):
        res = run(CHECKER, subject(["does/not/exist.py"]))
        self.assertEqual(res.returncode, EXIT_CANNOT_VERIFY, res.stdout)

    def test_syntax_error(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.py"
            bad.write_text("def broken(:\n")
            res = run(CHECKER, subject(["bad.py"], repo_root=td))
            self.assertEqual(res.returncode, EXIT_CANNOT_VERIFY, res.stdout)

    def test_no_files_at_all(self):
        res = run(CHECKER, subject([]))
        self.assertEqual(res.returncode, EXIT_CANNOT_VERIFY, res.stdout)

    def test_a_found_defect_outranks_an_unreadable_sibling(self):
        res = run(CHECKER, subject([rel(RED[0]), "does/not/exist.py"]))
        self.assertEqual(res.returncode, EXIT_FAIL, res.stdout)

    def test_unusable_facts_table_is_four_not_zero(self):
        """An empty vocabulary finds no writes anywhere -- fail-open, silently."""
        broken = dict(FEED_FACTS, outbound_write=[])
        res = run(CHECKER, subject([rel(GREEN[0])]), facts=broken)
        self.assertEqual(res.returncode, EXIT_CANNOT_VERIFY, res.stdout)
        self.assertIn("CANNOT VERIFY", res.stdout)

    def test_malformed_facts_json_is_four_not_zero(self):
        res = run(CHECKER, subject([rel(GREEN[0])]), facts="{not json")
        self.assertEqual(res.returncode, EXIT_CANNOT_VERIFY, res.stdout)

    def test_a_write_read_overlap_in_the_table_is_refused(self):
        """The anti-loop invariant, enforced by kernel.facts, surfaced as 4."""
        looping = dict(
            FEED_FACTS,
            outbound_write=FEED_FACTS["outbound_write"]
            + [{"pattern": ".get", "kind": "http", "seen_at": "core/x.py:9"}],
            outbound_read=[{"pattern": ".session.get", "kind": "http", "seen_at": "core/x.py:10"}],
        )
        res = run(CHECKER, subject([rel(GREEN[0])]), facts=looping)
        self.assertEqual(res.returncode, EXIT_CANNOT_VERIFY, res.stdout)

    def test_unparseable_subject_is_broken_not_a_verdict(self):
        res = run(CHECKER, "{not json")
        self.assertEqual(res.returncode, EXIT_BROKEN, res.stdout + res.stderr)

    def test_subject_without_repo_root_is_broken(self):
        res = run(CHECKER, json.dumps({"subject_refs": []}))
        self.assertEqual(res.returncode, EXIT_BROKEN, res.stdout + res.stderr)


class Narrowing(unittest.TestCase):
    def test_symbol_narrows_the_verdict(self):
        path = FIXTURES / "red" / "publish_result_dropped.py"
        self.assertEqual(check([path], symbol="run").returncode, EXIT_FAIL)
        self.assertEqual(check([path], symbol="not_a_symbol_here").returncode, EXIT_PASS)

    def test_variant_narrows_the_verdict(self):
        path = FIXTURES / "red" / "per_call_idempotency_key.py"
        self.assertEqual(check([path], variant="replay").returncode, EXIT_FAIL)
        self.assertEqual(check([path], variant="readback").returncode, EXIT_PASS)

    def test_non_file_refs_are_ignored(self):
        res = run(CHECKER, subject([rel(GREEN[0])],
                                   extra_refs=[{"kind": "attempt", "claim": "abc123"}]))
        self.assertEqual(res.returncode, EXIT_PASS, res.stdout)


class Detector(unittest.TestCase):
    def test_exit_zero_even_with_no_claims(self):
        res = detect(GREEN)
        self.assertEqual(res.returncode, DETECTOR_SCANNED)
        self.assertNotIn("V4-CLAIM", res.stdout)

    def test_claim_line_format(self):
        """Exactly the six fields SPEC.md §2 allows; a seventh is refused, not ignored."""
        from kernel.derive import ALLOWED_FIELDS, parse_claim_lines

        res = detect(RED)
        self.assertEqual(res.returncode, DETECTOR_SCANNED)
        fields_list = parse_claim_lines(res.stdout)
        self.assertTrue(fields_list)
        for fields in fields_list:
            self.assertEqual(fields["kind"], "external-write")
            self.assertIn(fields["variant"], ("readback", "replay"))
            self.assertTrue(fields["line"].isdigit())
            self.assertLessEqual(set(fields), set(ALLOWED_FIELDS))

    def test_kind_is_registered(self):
        kinds = json.loads((ROOT / ".v4" / "claim_kinds.json").read_text())
        self.assertIn("external-write", kinds)
        self.assertEqual(kinds["external-write"]["detector"], "external_write.py")

    def test_symbol_is_the_enclosing_definition(self):
        res = detect([FIXTURES / "red" / "publish_result_dropped.py"])
        self.assertIn("symbol=run", res.stdout)

    def test_byte_identical_across_runs(self):
        first = detect(RED + GREEN)
        second = detect(RED + GREEN)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.returncode, second.returncode)

    def test_order_does_not_follow_the_subject(self):
        forward = detect(RED)
        backward = detect(list(reversed(RED)))
        self.assertEqual(forward.stdout, backward.stdout)

    def test_survives_a_file_it_cannot_parse(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "bad.py").write_text("def broken(:\n")
            (Path(td) / "ok.py").write_text("import requests\n\n\ndef go(c):\n    c.post('/x')\n")
            res = run(DETECTOR, subject(["bad.py", "ok.py"], repo_root=td))
            self.assertEqual(res.returncode, DETECTOR_SCANNED, res.stderr)
            self.assertIn("will not parse", res.stderr)

    def test_detector_and_checker_agree(self):
        """A claim the checker cannot reproduce is a permanently OPEN claim."""
        for path in RED:
            with self.subTest(path.name):
                det = detect([path])
                claims = [line for line in det.stdout.splitlines() if line.startswith("V4-CLAIM")]
                self.assertTrue(claims, det.stdout)
                for line in claims:
                    fields = dict(
                        part.split("=", 1) for part in line.split(": ", 1)[1].split()
                    )
                    res = check([path], symbol=fields["symbol"], variant=fields["variant"])
                    self.assertEqual(res.returncode, EXIT_FAIL, line + "\n" + res.stdout)


class Hygiene(unittest.TestCase):
    def test_stdlib_only(self):
        """SPEC.md §10: a checker has to run in any adopter repo."""
        import ast

        allowed = {
            "__future__", "argparse", "ast", "dataclasses", "json", "pathlib",
            "subprocess", "sys", "tempfile", "unittest", "kernel",
            # `re` arrived with the TypeScript extractor, which reads
            # comment-stripped source rather than an AST -- SPEC.md §8.7 names
            # that as the current compromise for TS/JS. It is stdlib, so the
            # contract this test states is still met: the list is what a
            # checker may import, not a preference for one parsing style.
            "re",
        }
        for path in (CHECKER, DETECTOR, ROOT / "kernel" / "analysis" / "external_write.py",
                     Path(__file__)):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertIn(alias.name.split(".")[0], allowed, path.name)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    self.assertIn(node.module.split(".")[0], allowed, path.name)


class SelfTrigger(unittest.TestCase):
    """SPEC.md §4 ②: answering a claim must not raise another of the same kind.

    Not an argument -- the fix is written out, the detector is re-run on it, and
    the claim count has to be zero.  If it were not, ``v4 ship`` would burn its
    three re-derive rounds and mark the task ``ship_not_converging``.
    """

    #: The fix a worker would write for
    #: ``app/chat/notifier.py::_safe_send`` -- one of the 23 claims this
    #: detector raises on ``adopter_a`` HEAD.  It adds the read-back the claim
    #: asks for *and* a durable receipt, which is the write a naive rule would
    #: turn into the next claim.
    FIX = '''
import requests


class Notifier:
    def __init__(self, client, base_url, audit_repo):
        self.client = client
        self.base_url = base_url
        self.audit_repo = audit_repo

    def send_text(self, chat_id, text):
        sent = self.client.post(
            f"{self.base_url}/sendMessage", json={"chat_id": chat_id, "text": text},
        ).json()
        message_id = sent.get("result", {}).get("message_id")
        if not message_id:
            raise RuntimeError("the platform accepted the send without returning a message id")
        confirmed = requests.get(
            f"{self.base_url}/getChat", params={"chat_id": chat_id}, timeout=10,
        )
        if confirmed.status_code != 200:
            raise RuntimeError("cannot read the chat back to confirm the send")
        self.audit_repo.record_delivery(chat_id, message_id)
        return message_id
'''

    def _claims(self, source: str, facts_obj):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "notifier.py").write_text(source)
            return run(DETECTOR, subject(["notifier.py"], repo_root=td), facts=facts_obj)

    def test_the_unfixed_form_raises_a_claim(self):
        """Control: without the fix there is something to converge from."""
        unfixed = self.FIX.split("    def send_text")[0] + '''
    def send_text(self, chat_id, text):
        self.client.post(
            f"{self.base_url}/sendMessage", json={"chat_id": chat_id, "text": text},
        )
'''
        res = self._claims(unfixed, None)
        self.assertIn("V4-CLAIM", res.stdout)

    def test_answering_a_claim_raises_no_new_claim_builtin_table(self):
        res = self._claims(self.FIX, None)
        self.assertEqual(res.returncode, DETECTOR_SCANNED, res.stderr)
        self.assertNotIn("V4-CLAIM", res.stdout, "the fix raised a fresh claim of its own")

    def test_answering_a_claim_raises_no_new_claim_real_table(self):
        """The real table is where the loop would bite.

        ``adopter_a``' caller-layer DB row matches ``\\w*repo\\.record\\w*\\(``,
        so the receipt the fix persists *is* an outbound write under it.  It
        still raises no claim, because the scope now reads the world back.
        """
        res = self._claims(self.FIX, json.loads(ADOPTER_FACTS.read_text()))
        self.assertEqual(res.returncode, DETECTOR_SCANNED, res.stderr)
        self.assertNotIn("V4-CLAIM", res.stdout, "the fix raised a fresh claim of its own")

    def test_the_receipt_really_is_a_write_under_the_real_table(self):
        """Without this the previous test could pass for a boring reason."""
        from kernel import facts as facts_mod

        table = facts_mod.load(ADOPTER_FACTS)
        hits = facts_mod.scan_source(self.FIX, table.outbound_write)
        self.assertTrue(
            any(lines for lines in hits.values()),
            "the fix contains no outbound write under the real table, so this test "
            "would prove nothing about convergence",
        )

    def test_the_default_table_cannot_loop(self):
        """``kernel.facts`` refuses a table whose reads are also writes."""
        from kernel.analysis import external_write as analysis

        table = analysis.default_table()          # validate() runs on every call
        self.assertTrue(table.outbound_write and table.outbound_read)
        writes = {e.pattern for e in table.outbound_write}
        reads = {e.pattern for e in table.outbound_read}
        self.assertEqual(writes & reads, set())


DEBT_SRC = """import requests


def charge(url, amount):
    requests.post(url, json={"amount": amount}, timeout=5)
"""


class StandingDebtHasSomewhereToGo(unittest.TestCase):
    """`external-write` had no baseline, so a verified false positive was re-signed.

    Measured before this was built: seven handlers in one adopter's `poller.py` at six or seven signatures each, several of them signed `baseline_raise` with the reason 'PRE-EXISTING, NOT ADDED BY THIS TASK'. SPEC's Baseline section forbids
    exactly that -- `ACCEPTED_RISK` expires on the same key a PASS does, so a
    signature carrying standing debt is re-bought every time any file moves.

    A temp repo, because the point is the file the checker reads and not this
    repo's own debt.
    """

    def _repo(self, red_src, baseline_doc=None):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        tmp = Path(td.name)
        (tmp / ".v4").mkdir()
        (tmp / "a.py").write_text(red_src, encoding="utf-8")
        if baseline_doc is not None:
            (tmp / ".v4" / "external-write_baseline.json").write_text(
                baseline_doc, encoding="utf-8")
        return tmp

    def _run(self, tmp):
        return run(CHECKER, subject(["a.py"], repo_root=tmp))

    def _first_id(self, tmp):
        r = self._run(tmp)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        for line in r.stdout.splitlines():
            if line.strip().startswith("id "):
                return line.split()[1]
        self.fail("the checker failed and printed no id to accept it with:\n"
                  + r.stdout)

    def test_an_absent_file_forgives_nothing(self):
        """Fail-closed, and the direction matters: no file must not read as
        everything is accepted."""
        tmp = self._repo(DEBT_SRC)
        self.assertEqual(self._run(tmp).returncode, 1)

    def test_an_accepted_id_carries_instead_of_failing(self):
        tmp = self._repo(DEBT_SRC)
        fid = self._first_id(tmp)
        (tmp / ".v4" / "external-write_baseline.json").write_text(
            json.dumps({"accepted": [fid]}), encoding="utf-8")
        r = self._run(tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("carrying 1 accepted finding(s)", r.stdout,
                      "debt carried in silence is the record nobody reads")

    def test_an_unreadable_file_is_cannot_verify_and_not_a_pass(self):
        """A list of what is forgiven that cannot be read leaves the checker not
        knowing what is forgiven. 4 says that; 0 would be the hollow scan."""
        tmp = self._repo(DEBT_SRC, baseline_doc="{not json")
        self.assertEqual(self._run(tmp).returncode, 4)

    def test_the_id_survives_an_edit_above_it(self):
        """SPEC: finding id 唔准含行號. A baseline of line numbers forgives the
        wrong finding on the next commit."""
        tmp = self._repo(DEBT_SRC)
        before = self._first_id(tmp)
        (tmp / "a.py").write_text("# pushed down\n# by two lines\n" + DEBT_SRC,
                                  encoding="utf-8")
        self.assertEqual(self._first_id(tmp), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
