"""Facts-table tests.  Run with: python3 -m pytest tests/ -q   (or unittest)

Two properties matter more than the rest and each has its own class:

* `Rejects` — a broken facts file must stop the run.  A detector handed an
  empty or half-parsed table reports a clean repo, which is worse than no
  detector at all because it looks like an answer.

* `AntiLoop` — `outbound_write` must not match anything in `outbound_read`.
  The previous design listed the outbound set as `requests.*`, which contains
  `requests.get`; every read-back GET added to prove a write landed then
  derived its own write claim.  Two rules kill it: the symbol grammar has no
  wildcard, so `requests.*` is not a writable pattern at all, and a write
  pattern that matches a read pattern fails validation.
"""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kernel import facts  # noqa: E402
from kernel.analysis import facts_grammar  # noqa: E402
from kernel.analysis import facts_grammar  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
ADOPTER_FACTS = REPO / "tests" / "fixtures" / "facts" / "facts.adopter_a.json"

#: The checkout `ADOPTER_FACTS` describes, if somebody points at one.
#:
#: This was the absolute path `/path/to/adopter_a`, which made
#: the six checks below tests about one laptop -- and `run_without_silent_skips`
#: read their `not present` skip as the suite reporting on a smaller world and
#: returned 1, so CI failed on every run for a check CI can never perform.
#:
#: Unset is not a smaller world. The table is a statement about a repo this one
#: does not contain, so with no repo named there is no question to answer, and
#: the skip says that rather than naming something as missing. Named and absent
#: is the other case and stays loud, because then the question *was* put.
ADOPTER_REPO_ENV = "V4_ADOPTER_REPO"

MINIMAL = {
    "repo": "demo",
    "generated_from_commit": "0" * 40,
    "outbound_write": [
        {"pattern": "requests.post", "seen_at": "a/b.py:3", "kind": "http"},
    ],
    "outbound_read": [
        {"pattern": "requests.get", "seen_at": "a/b.py:9", "kind": "http"},
    ],
    "auth_decision": [
        {"pattern": "check_permission", "seen_at": "a/auth.py:1", "kind": "authz"},
    ],
    "entrypoint_globs": ["workers/*.py"],
    "ui_globs": ["ui/src/**"],
    "config_files": ["requirements.txt"],
    "protected_paths": [".v4/**"],
}


def build(**overrides):
    obj = copy.deepcopy(MINIMAL)
    obj.update(overrides)
    return obj


class Accepts(unittest.TestCase):
    def test_a_minimal_table_round_trips(self):
        f = facts.build(build())
        self.assertEqual(f.repo, "demo")
        self.assertEqual(f.outbound_write[0].pattern, "requests.post")
        self.assertEqual(f.outbound_write[0].seen_at_path, "a/b.py")
        self.assertEqual(f.outbound_write[0].seen_at_line, 3)
        self.assertEqual(f.outbound_write[0].match, "symbol")

    def test_load_reads_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "facts.json"
            path.write_text(json.dumps(build()))
            self.assertEqual(facts.load(path).repo, "demo")

    def test_the_shipped_adopter_a_table_is_valid(self):
        """The deliverable itself has to pass its own schema."""
        f = facts.load(ADOPTER_FACTS)
        self.assertEqual(f.repo, "adopter_a")
        self.assertTrue(f.outbound_write and f.outbound_read and f.auth_decision)


class Rejects(unittest.TestCase):
    """Every one of these used to be a way to get an empty table silently."""

    def assert_rejected(self, obj, needle):
        with self.assertRaises(facts.FactsError) as caught:
            facts.build(obj)
        self.assertIn(needle, str(caught.exception))

    def test_a_missing_key(self):
        obj = build()
        del obj["ui_globs"]
        self.assert_rejected(obj, "missing required key")

    def test_an_unknown_key(self):
        self.assert_rejected(build(outbound_writes=[]), "unknown key")

    def test_a_non_object_top_level(self):
        self.assert_rejected([], "top level must be a JSON object")

    def test_a_short_commit(self):
        self.assert_rejected(build(generated_from_commit="abc123"), "40-char lowercase git sha")

    def test_an_empty_symbol_list(self):
        self.assert_rejected(build(outbound_write=[]), "empty")

    def test_an_unknown_kind(self):
        self.assert_rejected(
            build(outbound_write=[{"pattern": "x.y", "seen_at": "a.py:1", "kind": "carrier-pigeon"}]),
            "is not one of",
        )

    def test_an_authz_kind_outside_auth_decision(self):
        self.assert_rejected(
            build(outbound_write=[{"pattern": "x.y", "seen_at": "a.py:1", "kind": "authz"}]),
            "does not belong in",
        )

    def test_a_seen_at_without_a_line(self):
        self.assert_rejected(
            build(outbound_write=[{"pattern": "x.y", "seen_at": "a/b.py", "kind": "http"}]),
            "must look like",
        )

    def test_an_absolute_seen_at(self):
        self.assert_rejected(
            build(outbound_write=[{"pattern": "x.y", "seen_at": "/abs/b.py:2", "kind": "http"}]),
            "must be repo-relative",
        )

    def test_a_duplicate_pattern(self):
        self.assert_rejected(
            build(outbound_write=[
                {"pattern": "x.y", "seen_at": "a.py:1", "kind": "http"},
                {"pattern": "x.y", "seen_at": "b.py:2", "kind": "http"},
            ]),
            "duplicate",
        )

    def test_an_uncompilable_regex(self):
        self.assert_rejected(
            build(outbound_write=[
                {"pattern": "(unclosed", "match": "regex", "seen_at": "a.py:1", "kind": "http"}
            ]),
            "not a valid regex",
        )

    def test_a_regex_smuggled_in_as_a_symbol(self):
        """`\\bINSERT\\s+INTO\\b` is fine, but it must say match=regex."""
        self.assert_rejected(
            build(outbound_write=[
                {"pattern": "\\bINSERT\\s+INTO\\b", "seen_at": "a.py:1", "kind": "db"}
            ]),
            "is not a dotted symbol",
        )

    def test_an_absolute_glob(self):
        self.assert_rejected(build(ui_globs=["/etc/**"]), "must be repo-relative")

    def test_a_glob_escaping_the_repo(self):
        self.assert_rejected(build(protected_paths=["../other/**"]), "must not escape")

    def test_unreadable_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(facts.FactsError) as caught:
                facts.load(Path(tmp) / "nope.json")
            self.assertIn("cannot read", str(caught.exception))

    def test_malformed_json_names_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "facts.json"
            path.write_text('{"repo": "demo",,}')
            with self.assertRaises(facts.FactsError) as caught:
                facts.load(path)
            self.assertIn("not valid JSON", str(caught.exception))


class AntiLoop(unittest.TestCase):
    def test_a_wildcard_outbound_pattern_cannot_be_written(self):
        """`requests.*` is how the loop got in. It is not a legal symbol."""
        with self.assertRaises(facts.FactsError) as caught:
            facts.build(build(outbound_write=[
                {"pattern": "requests.*", "seen_at": "a.py:1", "kind": "http"}
            ]))
        self.assertIn("is not a dotted symbol", str(caught.exception))

    def test_the_same_pattern_on_both_lists_is_refused(self):
        obj = build()
        obj["outbound_read"].append({"pattern": "requests.post", "seen_at": "a.py:1", "kind": "http"})
        with self.assertRaises(facts.FactsError) as caught:
            facts.build(obj)
        self.assertIn("both outbound_write and outbound_read", str(caught.exception))

    def test_a_write_pattern_that_swallows_a_read_is_refused(self):
        """This is the shipped bug, written as a test.

        The original form — an outbound list of `requests.*` — cannot be
        written here at all: the symbol grammar has no wildcard.  The form that
        survives is a bare suffix, `.get` on the write list, which matches the
        `requests.get` read-back and makes it derive its own write claim.
        """
        obj = build(outbound_write=[{"pattern": ".get", "seen_at": "a.py:1", "kind": "http"}])
        with self.assertRaises(facts.FactsError) as caught:
            facts.build(obj)
        self.assertIn("infinite claim loop", str(caught.exception))

    def test_the_shipped_table_keeps_its_read_backs_readable(self):
        """The real read-backs adopter_a relies on must stay reads."""
        f = facts.load(ADOPTER_FACTS)
        writes = [e.pattern for e in f.outbound_write if e.match == "symbol"]
        readbacks = [
            "requests.head",              # staged-object read-back
            ".job.retrieve",              # background inference poll
            ".files.get",                 # file-API poll
            ".reconcile_record",          # "did the publish land?"
            ".verify_readable",
        ]
        for readback in readbacks:
            self.assertIn(readback, [e.pattern for e in f.outbound_read], readback)
            for write in writes:
                self.assertFalse(
                    facts.matches_symbol(write, readback.lstrip(".")),
                    f"{write} would turn the read-back {readback} into a write",
                )


class Matching(unittest.TestCase):
    def test_a_dotted_pattern_is_exact_or_suffixed(self):
        self.assertTrue(facts.matches_symbol("requests.post", "requests.post"))
        self.assertTrue(facts.matches_symbol("requests.post", "core.requests.post"))
        self.assertFalse(facts.matches_symbol("requests.post", "requests.get"))

    def test_a_leading_dot_matches_any_receiver(self):
        self.assertTrue(facts.matches_symbol(".send_message", "self.client.send_message"))
        self.assertTrue(facts.matches_symbol(".send_message", "bot.send_message"))

    def test_segments_not_substrings(self):
        self.assertFalse(facts.matches_symbol(".publish", "result.publish_id"))
        self.assertFalse(facts.matches_symbol(".send_message", "x.send_message_with_reply_markup"))

    def test_a_leading_dot_never_matches_a_bare_name(self):
        """core/workers/dispatch.py:111 binds a local called `publish`.

        Without this rule `.publish` matched `publish.ig_post_url` and the
        posting worker reported eight graph publishes instead of one.
        """
        self.assertFalse(facts.matches_symbol(".publish", "publish"))

    def test_globs(self):
        self.assertTrue(facts.path_matches("auto-dev/scripts/x.py", ["auto-dev/**"]))
        self.assertTrue(facts.path_matches("CLAUDE.md", ["CLAUDE.md"]))
        self.assertTrue(facts.path_matches("core/workers/dispatch.py", ["core/workers/*.py"]))
        self.assertFalse(facts.path_matches("core/workers/sub/x.py", ["core/workers/*.py"]))
        self.assertFalse(facts.path_matches("core/auto-dev/x.py", ["auto-dev/**"]))


class Scanning(unittest.TestCase):
    def entries(self, *specs):
        return [facts.Entry(pattern=p, seen_at="x.py:1", kind=k, match=m) for p, k, m in specs]

    def test_a_bare_method_reference_counts(self):
        """app/chat/poller/poller.py hands the send to asyncio.to_thread.

        `\\.send_message\\s*\\(` found 3 sites; the AST form finds 19.  The
        missing 16 were real chat sends passed without parentheses.
        """
        source = "import asyncio\nasyncio.to_thread(self.client.send_message, chat_id=1)\n"
        hits = facts.scan_source(source, self.entries((".send_message", "http", "symbol")))
        self.assertEqual(hits[".send_message"], [2])

    def test_a_definition_and_an_import_are_not_call_sites(self):
        source = (
            "from core.scheduler_policy.service import bootstrap_launch_agent\n"
            "def bootstrap_launch_agent(dest):\n"
            "    return None\n"
            "bootstrap_launch_agent(dest)\n"
        )
        hits = facts.scan_source(source, self.entries(("bootstrap_launch_agent", "system", "symbol")))
        self.assertEqual(hits["bootstrap_launch_agent"], [4])

    def test_a_receiver_without_a_name_still_matches(self):
        """core/integrations/store/client.py:384 is `(a / b).write_bytes(c)`."""
        source = "(dest_folder / filename).write_bytes(content)\n"
        hits = facts.scan_source(source, self.entries((".write_bytes", "fs", "symbol")))
        self.assertEqual(hits[".write_bytes"], [1])

    def test_a_module_alias_is_matched_by_the_suffix_form(self):
        """core/workers/dispatch.py:531 does `import shutil as _shutil`."""
        source = "import shutil as _shutil\n_shutil.copy2(a, b)\n"
        entries = self.entries((".copy2", "fs", "symbol"), ("shutil.copy2", "fs", "symbol"))
        hits = facts.scan_source(source, entries)
        self.assertEqual(hits[".copy2"], [2])
        self.assertEqual(hits["shutil.copy2"], [])

    def test_a_receiver_used_as_a_table_is_still_a_reference(self):
        source = "TOOL_PERMISSIONS.get(name)\n"
        hits = facts.scan_source(source, self.entries(("TOOL_PERMISSIONS", "authz", "symbol")))
        self.assertEqual(hits["TOOL_PERMISSIONS"], [1])

    def test_a_route_decorator_is_not_an_outbound_post(self):
        """adopter_a has 63 `@router.post(...)`-shaped route decorators."""
        source = '@router.post("/brands/{slug}/media/upload")\ndef upload():\n    pass\n'
        entries = self.entries(("requests.post", "http", "symbol"), ("http.post", "http", "symbol"))
        hits = facts.scan_source(source, entries)
        self.assertEqual(hits["requests.post"], [])
        self.assertEqual(hits["http.post"], [])

    def test_regex_mode_ignores_comments_but_reads_strings(self):
        source = (
            "# INSERT INTO posts -- prose about the schema\n"
            'cur.execute("INSERT INTO posts (id) VALUES (%s)", [x])\n'
        )
        hits = facts.scan_source(source, self.entries(("\\bINSERT\\s+INTO\\b", "db", "regex")))
        self.assertEqual(hits["\\bINSERT\\s+INTO\\b"], [2])

    def test_symbol_mode_ignores_docstring_prose(self):
        source = '"""Callers use requests.post for the publish."""\nx = 1\n'
        hits = facts.scan_source(source, self.entries(("requests.post", "http", "symbol")))
        self.assertEqual(hits["requests.post"], [])

    def test_unparseable_python_does_not_crash_the_scan(self):
        hits = facts.scan_source("def broken(:\n", self.entries(("requests.post", "http", "symbol")))
        self.assertEqual(hits["requests.post"], [])


class AgainstAdopterTable(unittest.TestCase):
    """The table against the checkout it describes.  Opt in with `V4_ADOPTER_REPO`.

        V4_ADOPTER_REPO=/path/to/adopter_a python3 -m unittest tests.test_facts

    A table is only true about a repo, and this repo is not that repo, so the
    question these six ask cannot be put from a clone. Naming the checkout is
    how it gets put; see `ADOPTER_REPO_ENV`.
    """

    @classmethod
    def setUpClass(cls):
        named = os.environ.get(ADOPTER_REPO_ENV)
        if not named:
            # The reason has to name what goes unchecked, because nothing else
            # will. `.github/workflows/v4.yml` excludes this table from the
            # facts step on the grounds that it "is verified by
            # tests/test_facts.py where that repo exists" -- and no environment
            # that runs this suite sets `V4_ADOPTER_REPO`, so "where that repo
            # exists" is nowhere. The old wording said only "set the variable
            # to put this question", which reads as a convenience: it does not
            # match `run_without_silent_skips.ENVIRONMENT` (correctly -- unset
            # is not a smaller world) and so printed nothing at all, and the
            # oracle exited 0 with `OK (skipped=1)` over 153 unverified rows.
            #
            # `run_without_silent_skips` now prints every declared opt-out with
            # its reason, so this sentence is the report.
            table = facts.load(ADOPTER_FACTS)
            rows = (len(table.outbound_write) + len(table.outbound_read)
                    + len(table.auth_decision))
            raise unittest.SkipTest(
                f"{ADOPTER_REPO_ENV} names no checkout, so "
                f"{ADOPTER_FACTS.name} is checked here against nothing it "
                f"describes: its {rows} pattern rows, every seen_at citation, "
                f"the publish path, the read-backs, the declared config paths "
                f"and the globs all go unasked in this environment. What a "
                f"clone still answers is the schema and the read-back/write "
                f"separation (Accepts, AntiLoop in this file); everything "
                f"about the checkout it is a statement about, it does not. "
                f"Point {ADOPTER_REPO_ENV} at that checkout to put the "
                f"question.")
        repo = Path(named)
        if not (repo / ".git").exists():
            raise unittest.SkipTest(f"{ADOPTER_REPO_ENV}={named} is not present")
        cls.repo = repo
        cls.facts = facts.load(ADOPTER_FACTS)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.snapshot = Path(cls.tmp.name) / "snap"
        cls.snapshot.mkdir()
        commit = cls.facts.generated_from_commit
        archive = subprocess.run(
            ["git", "-C", str(repo), "archive", commit],
            capture_output=True,
        )
        if archive.returncode != 0:
            raise unittest.SkipTest(f"commit {commit[:12]} not in adopter_a any more")
        subprocess.run(["tar", "-x", "-C", str(cls.snapshot)], input=archive.stdout, check=True)
        for cmd in (
            ["git", "init", "-q"],
            # `gc.auto=0` and `maintenance.auto=false`: committing ~2,000 files
            # crosses git's loose-object threshold, and the `git gc --auto` it
            # forks keeps writing into `.git/objects` after `commit` returns. The
            # temp directory is then removed underneath it and `shutil.rmtree`
            # dies with "Directory not empty" -- an error in a passing test, in
            # teardown, on a schedule nobody controls. Seen twice on full runs
            # and not reproducible on demand, which is what a background process
            # looks like from inside the suite.
            ["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", "add", "-A"],
            ["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
             "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "snap"],
        ):
            subprocess.run(cmd, cwd=cls.snapshot, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "tmp"):
            # Belt as well as braces. `gc.auto=0` above stops the process that
            # caused this; a temp directory that outlives the run is the OS's to
            # collect, and is not worth turning a green suite red over.
            shutil.rmtree(cls.tmp.name, ignore_errors=True)
            try:
                cls.tmp.cleanup()
            except OSError:
                pass

    def test_every_seen_at_still_holds(self):
        """A citation nobody re-checks is how the table becomes a wish list."""
        self.assertEqual(facts.check_seen_at(self.facts, self.snapshot), [])

    def test_the_publish_path_is_covered(self):
        """core/workers/dispatch.py is the repo's most consequential writer."""
        source = (self.snapshot / "core/workers/dispatch.py").read_text()
        hits = facts.scan_source(source, self.facts.outbound_write)
        lines = {n for v in hits.values() for n in v}
        for expected in (415, 466, 531, 562):  # gcs upload, meta publish, copy2, gcs delete
            self.assertIn(expected, lines)

    def test_the_read_backs_in_the_publish_path_are_reads(self):
        source = (self.snapshot / "core/workers/dispatch.py").read_text()
        write_lines = {n for v in facts.scan_source(source, self.facts.outbound_write).values() for n in v}
        read_lines = {n for v in facts.scan_source(source, self.facts.outbound_read).values() for n in v}
        for readback in (123, 244, 434, 491):  # permalink, reconcile, gcs readback, baseline
            self.assertIn(readback, read_lines)
            self.assertNotIn(readback, write_lines)

    def test_the_framework_is_excluded_from_the_scan(self):
        """adopter_a vendors V3 under auto-dev/; its rmtree calls are not the product's."""
        hits = facts.scan_repo(self.facts, self.snapshot, "outbound_write")
        sites = [s for v in hits.values() for s in v]
        self.assertTrue(sites)
        self.assertFalse([s for s in sites if s.startswith("auto-dev/")])

    def test_every_declared_path_still_exists(self):
        missing = []
        for name in ("config_files",):
            for entry in getattr(self.facts, name):
                if not (self.snapshot / entry).exists():
                    missing.append(f"{name}: {entry}")
        self.assertEqual(missing, [])

    def test_every_glob_still_matches_something(self):
        """A glob that matches nothing is a fact about a repo that moved on."""
        tracked = subprocess.run(
            ["git", "-C", str(self.snapshot), "ls-files"],
            capture_output=True, text=True, check=True,
        ).stdout.split()
        empty = []
        for name in ("entrypoint_globs", "ui_globs"):
            for glob in getattr(self.facts, name):
                if not any(facts.path_matches(p, [glob]) for p in tracked):
                    empty.append(f"{name}: {glob}")
        self.assertEqual(empty, [])


if __name__ == "__main__":
    unittest.main()


class DeclaredAbsence(unittest.TestCase):
    """A repo with genuinely no outbound write has to be able to say so.

    Measured on a first adoption: a library whose only call is `requests.get`
    could not adopt at all. `outbound_write: []` was refused, and the refusal
    asked for entries that do not exist.
    """

    def table(self, **over):
        t = {
            "repo": "r",
            "generated_from_commit": "a" * 40,
            "outbound_write": [],
            "outbound_read": [{"pattern": "requests.get", "seen_at": "a.py:1",
                               "kind": "http"}],
            "auth_decision": [{"pattern": "check_auth", "seen_at": "a.py:2",
                               "kind": "authz"}],
            "entrypoint_globs": ["app/**"],
            "ui_globs": [],
            "config_files": [],
            "protected_paths": [".v4/**"],
        }
        t.update(over)
        return t

    def test_empty_alone_is_still_refused(self):
        with self.assertRaises(facts.FactsError) as e:
            facts.validate(self.table())
        self.assertIn("absent", str(e.exception))

    def test_declared_absence_validates(self):
        t = self.table(absent={"outbound_write":
                               "grep -rn 'requests.post' app/ at aaaaaa -> 0 hits"})
        facts.validate(t)
        self.assertEqual(facts.build(t).absence_reason("outbound_write"),
                         "grep -rn 'requests.post' app/ at aaaaaa -> 0 hits")

    def test_a_reason_that_cannot_be_rerun_is_refused(self):
        for word in ("n/a", "none", "no writes"):
            with self.assertRaises(facts.FactsError, msg=word):
                facts.validate(self.table(absent={"outbound_write": word}))

    def test_absent_and_populated_is_a_contradiction(self):
        t = self.table(
            outbound_write=[{"pattern": "requests.post", "seen_at": "a.py:3",
                             "kind": "http"}],
            absent={"outbound_write": "grep -rn post app/ at aaaaaa -> 0 hits"})
        with self.assertRaises(facts.FactsError) as e:
            facts.validate(t)
        self.assertIn("out of date", str(e.exception))

    def test_absence_cannot_be_declared_for_a_table_that_is_not_one(self):
        with self.assertRaises(facts.FactsError):
            facts.validate(self.table(
                absent={"nonesuch": "grep -rn x app/ at aaaaaa -> 0 hits"}))

    def test_absence_is_pointless_where_empty_already_speaks(self):
        with self.assertRaises(facts.FactsError):
            facts.validate(self.table(
                absent={"ui_globs": "grep -rn html app/ at aaaaaa -> 0 hits"}))

    def test_a_table_with_no_absence_key_reports_none(self):
        t = self.table(outbound_write=[{"pattern": "requests.post",
                                        "seen_at": "a.py:3", "kind": "http"}])
        self.assertEqual(facts.build(t).absence_reason("outbound_write"), "")


class ScanningGo(unittest.TestCase):
    """The table is the rule; the extractor is what changes per language.

    Every assertion here has a Python twin above it. If one of these drifts
    from its twin, the same row means two things -- which is the failure
    `scan_source` grew a `lang` argument instead of a second copy to avoid.
    """

    def entries(self, *specs):
        return [facts.Entry(pattern=p, seen_at="x.go:1", kind=k, match=m)
                for p, k, m in specs]

    SOURCE = (
        'package m\n'
        '\n'
        'import "net/http"\n'
        '\n'
        '// http.Post here is prose, not a call\n'
        'func Send(url string) {\n'
        '\t/* db.Execute("INSERT INTO t") is prose too */\n'
        '\tendpoint := "https://example.com/a"\n'
        '\thttp.Post(endpoint, "application/json", nil)\n'
        '\tdb.Execute("INSERT INTO brands (id) VALUES ($1)")\n'
        '}\n'
    )

    def test_a_symbol_entry_matches_a_go_call(self):
        hits = facts.scan_source(self.SOURCE,
                                 self.entries(("http.Post", "http", "symbol")),
                                 lang="go")
        self.assertEqual(hits["http.Post"], [9])

    def test_a_leading_dot_pattern_matches_the_tail_of_a_go_chain(self):
        hits = facts.scan_source(self.SOURCE,
                                 self.entries((".Execute", "db", "symbol")),
                                 lang="go")
        self.assertEqual(hits[".Execute"], [10])

    def test_a_line_comment_is_not_a_call(self):
        """Line 5 says `http.Post` and line 9 does it."""
        hits = facts.scan_source(self.SOURCE,
                                 self.entries((r"http\.Post", "http", "regex")),
                                 lang="go")
        self.assertEqual(hits[r"http\.Post"], [9])

    def test_a_block_comment_is_not_a_write(self):
        """Line 7 spells INSERT INTO inside `/* … */`; line 10 runs it."""
        hits = facts.scan_source(self.SOURCE,
                                 self.entries((r"\bINSERT\s+INTO\b", "db", "regex")),
                                 lang="go")
        self.assertEqual(hits[r"\bINSERT\s+INTO\b"], [10])

    def test_a_slash_inside_a_string_does_not_start_a_comment(self):
        """`"https://example.com/a"` -- the `//` is two characters of a URL."""
        hits = facts.scan_source(self.SOURCE,
                                 self.entries((r"example\.com", "http", "regex")),
                                 lang="go")
        self.assertEqual(hits[r"example\.com"], [8])

    def test_go_source_that_will_not_parse_reports_nothing(self):
        hits = facts.scan_source("package m\nfunc (\n",
                                 self.entries(("http.Post", "http", "symbol")),
                                 lang="go")
        self.assertEqual(hits["http.Post"], [])


class TrackedSource(unittest.TestCase):
    def _repo(self):
        import subprocess, tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        subprocess.run(["git", "init", "-q", str(tmp)], check=True)
        for rel, text in (("app.py", "x = 1\n"),
                          ("svc.go", "package svc\n"),
                          ("svc_test.go", "package svc\n"),
                          ("tests/test_app.py", "x = 1\n")):
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        subprocess.run(["git", "-C", str(tmp), "add", "-A"], check=True)
        return tmp

    def test_go_counts_as_source(self):
        names = {p.name for p in facts.tracked_source_files(self._repo())}
        self.assertEqual(names, {"app.py", "svc.go"})

    def test_a_go_test_file_is_a_test(self):
        """`_test.go` is the compiler's rule, not a naming convention."""
        names = {p.name for p in
                 facts.tracked_source_files(self._repo(), include_tests=True)}
        self.assertIn("svc_test.go", names)

    def test_one_language_can_be_asked_for(self):
        names = {p.name for p in
                 facts.tracked_source_files(self._repo(), suffixes=(".py",))}
        self.assertEqual(names, {"app.py"})


class ProposingFromGo(unittest.TestCase):
    """A Go repo adopting this gets a vocabulary, not an empty table.

    Before `tracked_source_files`, `propose` read `.py` and nothing else, so a
    Go repo's table came out empty -- and every rule that reads the table
    answered UNSUPPORTED while the ship report listed it as having run.
    """

    def _repo(self):
        import subprocess, tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        subprocess.run(["git", "init", "-q", str(tmp)], check=True)
        (tmp / "go.mod").write_text("module example.com/app\n\ngo 1.21\n")
        (tmp / "internal" / "api").mkdir(parents=True)
        (tmp / "internal" / "api" / "routes.go").write_text(
            'package api\n'
            '\n'
            'import (\n'
            '\t"crypto/hmac"\n'
            '\t"net/http"\n'
            ')\n'
            '\n'
            'func Hook(w http.ResponseWriter, r *http.Request) {\n'
            '\tif !hmac.Equal(sig(r), want()) {\n'
            '\t\treturn\n'
            '\t}\n'
            '\thttp.Post("https://example.com/ack", "application/json", nil)\n'
            '}\n')
        subprocess.run(["git", "-C", str(tmp), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(tmp), "-c", "user.email=a@b",
                        "-c", "user.name=a", "commit", "-qm", "init"], check=True)
        return tmp

    def test_an_outbound_write_is_proposed(self):
        table = facts.propose(self._repo())
        self.assertIn("http.Post", [r["pattern"] for r in table["outbound_write"]])

    def test_the_go_constant_time_compare_is_proposed_as_auth(self):
        """`Equal` alone is `assert.Equal` and `reflect.DeepEqual`.

        So the verb list carries `hmac.equal` dotted, and the leaf-only match
        would never have found it.
        """
        table = facts.propose(self._repo())
        self.assertIn("hmac.Equal", [r["pattern"] for r in table["auth_decision"]])

    def test_a_handler_signature_names_an_entrypoint_directory(self):
        """Go has no decorators. It has a handler signature, which is better."""
        table = facts.propose(self._repo())
        self.assertIn("internal/api/**", table["entrypoint_globs"])


class WhatInstallProposesMustPassWhatInstallValidates(unittest.TestCase):
    """`v4 install` handed adopters a table the next command rejects.

    `dotted_names` renders a chain whose base is not a name --
    `(folder / name).write_bytes(...)`, `dict(self.absent).get(...)` -- with a
    `?` base, deliberately, so a suffix pattern still matches it. That
    rendering is for matching. It is not a pattern a table can hold:
    `facts_grammar.validate` refuses anything that is not a dotted symbol
    unless it declares `"match": "regex"`.

    Measured against dadac9e on this repo: the draft proposed `?.exists` and
    `?.get`, and `validate` refused the whole table. The first command an
    adopter runs produced a file the second one would not read.
    """

    def _repo(self, files):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for cmd in (["git", "init", "-q"],
                    ["git", "config", "user.email", "t@t"],
                    ["git", "config", "user.name", "t"]):
            subprocess.run(cmd, cwd=tmp, capture_output=True)
        for rel, body in files.items():
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "in"], cwd=tmp,
                       capture_output=True)
        return tmp

    ANON_BASE = (
        "from pathlib import Path\n\n"
        "def go(folder, name, payload):\n"
        "    # the base of this chain is an expression, not a name\n"
        "    (Path(folder) / name).write_bytes(payload)\n"
        "    return dict(payload).get('k')\n"
    )

    def test_a_chain_with_no_name_at_its_base_is_not_proposed(self):
        table = facts.propose(self._repo({"app.py": self.ANON_BASE}))
        for bucket in ("outbound_write", "outbound_read", "auth_decision"):
            for row in table.get(bucket, []):
                self.assertNotIn("?", row["pattern"],
                                 f"{bucket} holds a pattern no table can key on")

    def test_and_the_table_it_drafts_passes_its_own_validator(self):
        table = facts.propose(self._repo({"app.py": self.ANON_BASE}))
        facts_grammar.validate(table)          # raises FactsError if it does not

    def test_a_named_chain_beside_it_is_still_proposed(self):
        """Skipping the `?` form must not skip the sighting next to it."""
        table = facts.propose(self._repo({
            "app.py": self.ANON_BASE
            + "\nimport requests\n\ndef send(x):\n    return requests.post(x)\n"}))
        self.assertIn("requests.post",
                      [r["pattern"] for r in table["outbound_write"]])

    def test_this_repo_own_draft_validates(self):
        """The measurement that started this: run it on the framework itself."""
        facts_grammar.validate(facts.propose(REPO))


class WhereTheViewFilesActuallyAre(unittest.TestCase):
    """`ui_globs` named the top-level ancestor, which is not where they live.

    Measured on `valibot`: the `.tsx` files sit under `website/src/`, the
    proposal said `website/**`, and that glob swallowed
    `website/scripts/contributors.ts` -- a build-time Node script that reads a
    GitHub token, imports `node:fs` and runs through
    `tsm ./scripts/contributors.ts`. A checker pointed at it reported a secret
    shipping to a browser from a file that never reaches one. A false positive
    is what gets a checker switched off, so the glob has to come from the files
    it was drawn from.
    """

    def _repo(self, files):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for cmd in (["git", "init", "-q"],
                    ["git", "config", "user.email", "t@t"],
                    ["git", "config", "user.name", "t"]):
            subprocess.run(cmd, cwd=tmp, capture_output=True)
        for rel, body in files.items():
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "in"], cwd=tmp,
                       capture_output=True)
        return tmp

    VIEW = "export const A = () => null\n"
    SCRIPT = ("import fs from 'node:fs'\n"
              "const T = process.env.GITHUB_TOKEN\n")

    def test_a_build_script_beside_the_views_is_not_client_source(self):
        table = facts.propose(self._repo({
            "website/src/app.tsx": self.VIEW,
            "website/scripts/build.ts": self.SCRIPT,
        }))
        self.assertEqual(table["ui_globs"], ["website/src/**"])

    def test_sibling_view_directories_fold_to_their_parent(self):
        table = facts.propose(self._repo({
            "src/routes/a/page.tsx": self.VIEW,
            "src/routes/b/page.tsx": self.VIEW,
        }))
        self.assertEqual(table["ui_globs"], ["src/routes/**"])

    def test_a_glob_already_covered_by_a_shallower_one_is_dropped(self):
        table = facts.propose(self._repo({
            "web/src/app.tsx": self.VIEW,
            "web/src/routes/a/x.tsx": self.VIEW,
            "web/src/routes/b/y.tsx": self.VIEW,
        }))
        self.assertEqual(table["ui_globs"], ["web/src/**"])

    def test_one_directory_is_not_generalised_to_its_parent(self):
        """Nothing to generalise from a single sighting."""
        table = facts.propose(self._repo({"web/ui/app.tsx": self.VIEW}))
        self.assertEqual(table["ui_globs"], ["web/ui/**"])

    def test_two_unrelated_trees_stay_apart(self):
        table = facts.propose(self._repo({
            "web/app.tsx": self.VIEW,
            "docs/demo/x.tsx": self.VIEW,
        }))
        self.assertEqual(sorted(table["ui_globs"]), ["docs/demo/**", "web/**"])


class TheFrameworksOwnFilesAreNotTheAdopters(unittest.TestCase):
    """`not_this_framework` reads the registries, and one set is not in them.

    `always_*` detectors are exempt from the registration gate (SPEC.md §2:
    one fixed line, no dependence on the tree, so "should not fire" cannot be
    written as a fixture), so they never reach `detectors.json`. Measured on a
    fresh Go adoption: 26 detector files on disk, 15 in the registry, and the
    11 `always_*` ones came back as the adopter's own code.
    """

    def _adopted(self):
        import subprocess, tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "checkers.json").write_text(json.dumps(
            {"lint": {"path": "checkers/structural_lint.py"}}))
        (tmp / ".v4" / "detectors.json").write_text(json.dumps(
            {"dal_write.py": {"path": "detectors/dal_write.py"}}))
        for rel in ("checkers/structural_lint.py", "detectors/dal_write.py",
                    "detectors/always_scope.py", "detectors/mine.py",
                    "hooks/stop_gate.py", "app/service.py"):
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x = 1\n")
        return tmp

    def test_a_registered_detector_is_the_frameworks(self):
        theirs = facts.not_this_framework(self._adopted())
        self.assertFalse(theirs("detectors/dal_write.py"))

    def test_an_always_detector_is_the_frameworks_too(self):
        """The one the registry cannot name, because the gate exempts it."""
        theirs = facts.not_this_framework(self._adopted())
        self.assertFalse(theirs("detectors/always_scope.py"))

    def test_a_detector_the_adopter_wrote_is_still_theirs(self):
        """Globbed by the `always_` prefix, not by the directory. An adopter
        may write its own detector beside the installed ones, and `v4 install`
        already says so by leaving edited files alone."""
        theirs = facts.not_this_framework(self._adopted())
        self.assertTrue(theirs("detectors/mine.py"))

    def test_the_adopters_own_code_is_theirs(self):
        theirs = facts.not_this_framework(self._adopted())
        self.assertTrue(theirs("app/service.py"))


class DraftingLayers(unittest.TestCase):
    """`layer-boundary` is `applies_to: declared` and nothing proposed the file.

    So the only mechanical architecture rule V4 has could not be turned on
    without authoring the whole declaration from blank -- `facts.propose` had
    that shape and this is the same repair.
    """

    def _repo(self, files):
        import subprocess, tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        subprocess.run(["git", "init", "-q", str(tmp)], check=True)
        for rel, body in files.items():
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        subprocess.run(["git", "-C", str(tmp), "add", "-A"], check=True)
        return tmp

    APP = {
        "api/routes.py": "from core.tenants import all_tenants\n\n"
                         "def list_tenants():\n    return all_tenants()\n",
        "core/tenants.py": "def all_tenants():\n    return []\n",
    }

    def _propose(self, tmp, **kw):
        from kernel.analysis import layers
        return layers.propose(tmp, entrypoint_globs=["api/**"], **kw)

    def test_it_drafts_the_two_layers_the_repo_shows(self):
        d = self._propose(self._repo(self.APP))
        self.assertEqual([l["name"] for l in d["layers"]], ["entry", "core"])

    def test_allow_is_what_the_repo_does_today_so_the_first_run_is_green(self):
        """A draft that fires the day it is written is a draft nobody keeps.
        The pruning is the work, and deleting a line is how it is done."""
        d = self._propose(self._repo(self.APP))
        self.assertEqual(d["allow"], [["entry", "core"]])

    def test_one_layer_is_not_a_boundary(self):
        d = self._propose(self._repo({"core/tenants.py": "x = 1\n"}))
        self.assertIsNone(d)

    def test_a_data_layer_is_never_guessed(self):
        """The first version looked for DML per directory, and on this
        framework's own repo that made all of `kernel/**` the data layer
        because `ledger.py` holds the schema -- one file in thirty."""
        d = self._propose(self._repo({
            **self.APP,
            "core/store.py": 'SQL = "INSERT INTO brands (id) VALUES (%s)"\n'}))
        self.assertNotIn("dal", [l["name"] for l in d["layers"]])

    def test_a_declared_dal_glob_is_used_because_somebody_wrote_it(self):
        d = self._propose(self._repo({
            **self.APP, "dal/store.py": "def save():\n    return 1\n"}),
            dal_globs=["dal/**"])
        self.assertIn("dal", [l["name"] for l in d["layers"]])

    def test_tests_are_left_out(self):
        """Layers are about the shipped architecture, and a test directory
        reaches into every one of them by design."""
        d = self._propose(self._repo({
            **self.APP,
            "tests/test_api.py": "from api.routes import list_brands\n"}))
        paths = [p for l in d["layers"] for p in l["paths"]]
        self.assertNotIn("tests/**", paths)

    def test_the_frameworks_own_files_are_left_out(self):
        """`v4 install` copies them in minutes before this runs."""
        tmp = self._repo({**self.APP, "detectors/always_scope.py": "x = 1\n"})
        d = self._propose(tmp, theirs=lambda rel: not str(rel).startswith("detectors/"))
        paths = [p for l in d["layers"] for p in l["paths"]]
        self.assertNotIn("detectors/**", paths)

    def test_install_does_not_overwrite_a_declaration_somebody_pruned(self):
        from kernel import install
        tmp = self._repo(self.APP)
        (tmp / ".v4").mkdir(exist_ok=True)
        (tmp / ".v4" / "layers.json").write_text('{"layers": [], "allow": []}')
        path, edges = install.write_layers(tmp)
        self.assertIsNone(path)

    def test_install_does_not_overwrite_an_existing_draft(self):
        from kernel import install
        tmp = self._repo(self.APP)
        (tmp / ".v4").mkdir(exist_ok=True)
        (tmp / ".v4" / "layers.json.draft").write_text("{}")
        path, edges = install.write_layers(tmp)
        self.assertIsNone(path)


class RestatingALineTheToolAlreadyDerived(unittest.TestCase):
    """`seen_at` is `file:line`, and the line was maintained by hand.

    `scan_source` says where a pattern matches now, and `check_seen_at` uses it
    to tell a move ("matched at 216 rather than 268") from a deletion ("nowhere
    in this file"). Only the move was ever repaired, and repairing it meant a
    person transcribing a number the tool had just printed. Measured
    2026-08-27: one row moved twice in a single day, both times a hand edit,
    both times with every gate green.

    What this must never do is make the deletion disappear too, so the two
    refusals are pinned harder than the rewrite.
    """

    def _table(self, seen_at, body):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "app.py").write_text(body)
        table = root / "facts.json"
        table.write_text(json.dumps({
            "repo": "r", "generated_from_commit": "0" * 40,
            "auth_decision": [{"pattern": "authorise", "seen_at": seen_at,
                               "kind": "authz", "match": "symbol"}],
            "outbound_write": [], "outbound_read": [],
            "entrypoint_globs": [], "ui_globs": [],
            "config_files": [], "protected_paths": [],
            # The grammar refuses an empty symbol list without a stated reason,
            # which is the rule that stops a blank table reading as a clean
            # repo. This fixture is about one row in one list, so the other two
            # say so rather than being quietly empty.
            # `ui_globs` and `config_files` may be empty without a reason;
            # the other four may not, which is the rule that stops a blank
            # table reading as a clean repo. Asked of the grammar rather than
            # listed from memory, so a change there cannot leave this stale.
            "absent": {n: "fixture: one auth_decision row only"
                       for n in facts_grammar.SYMBOL_LISTS
                       + facts_grammar.PATH_LISTS
                       if n != "auth_decision"
                       and n not in facts_grammar.MAY_BE_EMPTY},
        }, indent=2))
        return root, table

    def _seen_at(self, table):
        return json.loads(table.read_text())["auth_decision"][0]["seen_at"]

    def test_a_line_that_moved_is_rewritten_to_where_it_is(self):
        root, table = self._table("app.py:1", "\n\n\nauthorise()\n")
        moved, refused = facts.restate(table, root)
        self.assertEqual(refused, [])
        self.assertEqual(len(moved), 1, moved)
        self.assertEqual(self._seen_at(table), "app.py:4")

    def test_a_pattern_that_is_gone_is_refused_not_rewritten(self):
        """The half `verify --gone-only` fails on. It must stay failing."""
        root, table = self._table("app.py:1", "def other():\n    pass\n")
        moved, refused = facts.restate(table, root)
        self.assertEqual(moved, [])
        self.assertTrue(any("gone, not moved" in r for r in refused), refused)
        self.assertEqual(self._seen_at(table), "app.py:1", "the row was edited")

    def test_two_candidates_are_a_judgement_and_are_refused(self):
        # `app.py:9` is neither of the two matches: a row that already sits on
        # one of them is not drifting and there is nothing to decide.
        root, table = self._table("app.py:9", "authorise()\n\n\nauthorise()\n")
        moved, refused = facts.restate(table, root)
        self.assertEqual(moved, [])
        self.assertTrue(any("judgement" in r for r in refused), refused)
        self.assertEqual(self._seen_at(table), "app.py:9")

    def test_a_table_that_already_holds_is_not_rewritten(self):
        root, table = self._table("app.py:1", "authorise()\n")
        before = table.read_text()
        moved, refused = facts.restate(table, root)
        self.assertEqual((moved, refused), ([], []))
        self.assertEqual(table.read_text(), before, "a clean table was touched")
