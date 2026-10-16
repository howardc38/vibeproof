"""Offline audit uses committed inputs in independent clones, never a live DB."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from kernel import ledger


ROOT = Path(__file__).resolve().parents[1]
EXPORT = ".v4/ledger_export.jsonl"
TASK_RECORD = ".v4/risks/c1.json"
REPO_RECORD = ".v4/risks/repo/lint.json"


class OfflineSignatures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source"
        self.source.mkdir()
        self.git(self.source, "init", "-q", "-b", "main")
        self.git(self.source, "config", "user.name", "offline audit fixture")
        self.git(self.source, "config", "user.email", "test@example.invalid")
        self.git(self.source, "config", "commit.gpgsign", "false")
        for name in ("kernel", "bin"):
            shutil.copytree(ROOT / name, self.source / name,
                            ignore=shutil.ignore_patterns("__pycache__"))
        (self.source / ".v4").mkdir()
        self.conn = ledger.connect(self.source)
        self.addCleanup(self.conn.close)
        ledger.insert(self.conn, "task", id="t", request="offline test", scope_globs=["**"],
                      base_commit="", created_at="2026")
        for cid in ("c1", "c2"):
            ledger.insert(self.conn, "claim", id=cid, task_id="t", kind="lint", question="q",
                          subject_refs="[]", checker="lint", origin="derive", created_at="2026")

    def git(self, root, *args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self):
        self.git(self.source, "add", "--all")
        self.git(self.source, "commit", "-qm", "synthetic audit fixture")

    def signature(self, cid="c1", scope="task", why="a synthetic reason for this fixture", signed_by="agent"):
        rec = TASK_RECORD if scope == "task" else REPO_RECORD
        covers = {"kind": "lint", "checker_sha": why} if scope == "repo" else None
        ledger.insert(self.conn, "accepted_risk", claim_id=cid, kind="unprovable",
                      who="test@example.invalid", why=why, was_tty=0, signed_by=signed_by,
                      git_record=rec, subject_digest="{}", created_at="2026", scope=scope,
                      cover_key=json.dumps(covers, sort_keys=True) if covers else "")
        record = {"claim": cid, "kind": "unprovable", "scope": scope, "who": "test@example.invalid",
                  "why": why, "stdin_was_a_tty": False, "signed_by": signed_by}
        if covers:
            record["covers"] = covers
        path = self.source / rec
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record))
        ledger.insert(self.conn, "event", task_id="t", kind="engagement", actor="worker",
                      payload={"fixture": cid}, created_at="2026")
        self.commit()

    def export(self, seal=False):
        (self.source / ".v4").mkdir(exist_ok=True)
        ledger.export_jsonl(self.conn, self.source / EXPORT, self.source,
                            seal_at=1 if seal else ledger.SEAL_AT)
        self.commit()

    def clone(self):
        clone = Path(self.temp.name) / "clone"
        self.git(self.source, "clone", "--no-local", "-q", str(self.source), str(clone))
        self.assertFalse((clone / ".git/v4").exists())
        return clone

    def audit(self, clone, *, signatures=True, extra=(), expected=0):
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        result = subprocess.run(
            [str(clone / "bin/v4"), "--repo", str(clone), "audit",
             "--events", str(clone / EXPORT), *(["--signatures"] if signatures else []), *extra],
            cwd=clone, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse((clone / ".git/v4").exists(), "offline audit created live ledger state")
        return result.stdout + result.stderr

    def rows(self, clone):
        return [json.loads(line) for line in (clone / EXPORT).read_text().splitlines()]

    def write_rows(self, clone, rows):
        (clone / EXPORT).write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    def matching_clone(self):
        self.signature()
        self.export()
        return self.clone()

    def test_task_repo_and_superseded_records_across_segments(self):
        self.signature()
        self.export()
        self.signature(scope="repo", why="first repo cover")
        self.export(seal=True)
        self.signature(cid="c2", scope="repo", why="second repo cover")
        self.export(seal=True)
        clone = self.clone()
        self.assertEqual(len(ledger.segment_files(clone / EXPORT)), 2)
        output = self.audit(clone)
        self.assertIn("consistent across 3 accepted-risk row(s)", output)
        self.assertIn("rows are unchained", output)

    def test_record_missing_on_current_branch_is_read_from_real_history(self):
        self.signature()
        self.git(self.source, "branch", "signature-history")
        self.git(self.source, "rm", TASK_RECORD)
        self.commit()
        self.export()
        clone = self.clone()
        self.assertFalse((clone / TASK_RECORD).exists())
        self.audit(clone)

    def test_orphan_task_and_repo_records_fail(self):
        clone = self.matching_clone()
        for rec in (".v4/risks/orphan-f533.json", REPO_RECORD):
            with self.subTest(rec=rec):
                path = clone / rec
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text((clone / TASK_RECORD).read_text())
                self.git(clone, "add", rec)
                self.assertIn("signature the ledger never made", self.audit(clone, expected=1))
                path.unlink()
                self.git(clone, "rm", "--cached", rec)

    def test_absent_record_in_all_history_fails(self):
        clone = self.matching_clone()
        rows = self.rows(clone)
        next(r for r in rows if r["_table"] == "accepted_risk")["git_record"] = ".v4/risks/absent.json"
        self.write_rows(clone, rows)
        self.assertIn("on no branch", self.audit(clone, expected=1))

    def test_untracked_record_fails(self):
        clone = self.matching_clone()
        self.git(clone, "rm", "--cached", TASK_RECORD)
        self.assertIn("not tracked", self.audit(clone, expected=1))

    def test_alternate_index_cannot_authorize_untracked_record(self):
        clone = self.matching_clone()
        alternate = clone.parent / 'alternate-index'
        shutil.copyfile(clone / '.git/index', alternate)
        self.git(clone, 'rm', '--cached', TASK_RECORD)
        self.assertIn('not tracked', self.audit(clone, expected=1))
        with patch.dict(os.environ, {'GIT_INDEX_FILE': str(alternate)}):
            self.assertIn('not tracked', self.audit(clone, expected=1))
            _count, problems = ledger.verify_exported(clone / EXPORT, signature_root=clone)
            self.assertIn('not tracked', ' '.join(problems))
        self.assertFalse((clone / '.git/v4').exists())

    def test_alternate_index_cannot_hide_an_absent_tracked_orphan(self):
        clone = self.matching_clone()
        alternate = clone.parent / 'alternate-index'
        shutil.copyfile(clone / '.git/index', alternate)
        orphan = '.v4/risks/orphan.json'
        (clone / orphan).write_bytes((clone / TASK_RECORD).read_bytes())
        self.git(clone, 'add', orphan)
        (clone / orphan).unlink()
        with patch.dict(os.environ, {'GIT_INDEX_FILE': str(alternate)}):
            self.assertIn('signature the ledger never made', self.audit(clone, expected=1))

    def test_git_repository_selectors_cannot_replace_audited_history(self):
        self.signature()
        self.git(self.source, 'branch', 'signature-history')
        self.git(self.source, 'rm', TASK_RECORD)
        self.commit()
        self.export()
        clone = self.clone()
        other = clone.parent / 'other'
        other.mkdir()
        self.git(other, 'init', '-q')
        for overrides in ({'GIT_DIR': str(other / '.git')},
                          {'GIT_WORK_TREE': str(other)},
                          {'GIT_COMMON_DIR': str(other / '.git')},
                          {'GIT_DIR': str(other / '.git'), 'GIT_WORK_TREE': str(other)}):
            with self.subTest(overrides=overrides), patch.dict(os.environ, overrides):
                self.audit(clone)
                _count, problems = ledger.verify_exported(clone / EXPORT, signature_root=clone)
                self.assertEqual(problems, [])
        self.assertFalse((clone / '.git/v4').exists())

    def test_changed_fields_fail_without_printing_reason_values(self):
        clone = self.matching_clone()
        path = clone / TASK_RECORD
        original = json.loads(path.read_text())
        for field, value in (("who", "other@example.invalid"), ("why", "private reason do not print"),
                             ("signed_by", "monitor"), ("claim", "c2"), ("scope", "repo")):
            with self.subTest(field=field):
                changed = {**original, field: value}
                if field == "scope":
                    changed["covers"] = {}
                path.write_text(json.dumps(changed))
                output = self.audit(clone, expected=1)
                self.assertIn(f"disagree about '{field}'", output)
                self.assertNotIn(value, output)

    def test_missing_attribution_when_row_records_it_fails_live_and_offline(self):
        self.signature()
        record_path = self.source / TASK_RECORD
        record = json.loads(record_path.read_text())
        del record["signed_by"]
        record_path.write_text(json.dumps(record))
        self.assertTrue(any("signed_by" in p for p in ledger.reconcile_signatures(self.conn, self.source)))
        self.export()
        self.assertIn("signed_by", self.audit(self.clone(), expected=1))

    def test_precolumn_empty_attribution_remains_explicitly_supported(self):
        self.signature(signed_by="")
        path = self.source / TASK_RECORD
        record = json.loads(path.read_text())
        record["signed_by"] = "agent"
        path.write_text(json.dumps(record))
        self.assertEqual(ledger.reconcile_signatures(self.conn, self.source), [])
        self.export()
        self.audit(self.clone())

    def test_untracked_literal_wildcard_cannot_borrow_another_records_tracking(self):
        clone = self.matching_clone()
        rows = self.rows(clone)
        extra = dict(next(r for r in rows if r["_table"] == "accepted_risk"))
        extra.update(id=2, git_record=".v4/risks/*.json")
        self.write_rows(clone, rows + [extra])
        (clone / extra["git_record"]).write_text((clone / TASK_RECORD).read_text())
        self.assertTrue(any("not tracked" in p for p in ledger.verify_exported(
            clone / EXPORT, signature_root=clone)[1]))
        self.assertIn("not tracked", self.audit(clone, expected=1))
        self.git(clone, "--literal-pathspecs", "add", "--", extra["git_record"])
        self.audit(clone)

    def test_redacted_reason_is_only_a_view_and_nonsecret_changes_fail(self):
        # Synthetic test credential, never a real token.
        token = "ghp_" + "SYNTHETICfixture" * 3
        self.signature(why="permitted reason " + token)
        self.export()
        clone = self.clone()
        self.assertNotIn(token, (clone / EXPORT).read_text())
        self.assertIn("original redacted values", self.audit(clone))
        record = json.loads((clone / TASK_RECORD).read_text())
        record["why"] = "changed reason " + token
        (clone / TASK_RECORD).write_text(json.dumps(record))
        output = self.audit(clone, expected=1)
        self.assertIn("disagree about 'why'", output)
        self.assertNotIn(token, output)

    def test_malformed_and_empty_record_objects_fail(self):
        clone = self.matching_clone()
        for text in ("{", "[]", "{}", '{"claim":"c1","claim":"c2"}',
                     '{"claim":"c1","kind":"unprovable","why":5}'):
            with self.subTest(text=text):
                (clone / TASK_RECORD).write_text(text)
                self.audit(clone, expected=1)
        record = json.loads((self.source / TASK_RECORD).read_text())
        for field in ("scope", "who", "stdin_was_a_tty"):
            with self.subTest(missing=field):
                changed = {k: v for k, v in record.items() if k != field}
                (clone / TASK_RECORD).write_text(json.dumps(changed))
                self.assertIn("malformed signature record", self.audit(clone, expected=1))

    def test_unsafe_record_paths_fail_without_reading_outside_checkout(self):
        clone = self.matching_clone()
        original = self.rows(clone)
        for rec in ("../outside.json", str(self.source / TASK_RECORD), ".v4/risks/../outside.json",
                    ".v4/risks/bad\0.json"):
            with self.subTest(rec=rec):
                rows = json.loads(json.dumps(original))
                next(r for r in rows if r["_table"] == "accepted_risk")["git_record"] = rec
                self.write_rows(clone, rows)
                self.assertIn("invalid signature record path", self.audit(clone, expected=1))

    def test_symlink_and_nonregular_record_fail(self):
        clone = self.matching_clone()
        path = clone / TASK_RECORD
        path.unlink()
        path.symlink_to(self.source / TASK_RECORD)
        self.audit(clone, expected=1)
        path.unlink()
        path.mkdir()
        self.assertIn("regular file", self.audit(clone, expected=1))

    def test_cyclic_record_symlink_is_a_controlled_input_failure(self):
        clone = self.matching_clone()
        path = clone / TASK_RECORD
        path.unlink()
        path.symlink_to(path.name)
        self.assertIn("regular file", self.audit(clone, expected=1))

    def test_symlinked_record_directory_is_not_walked(self):
        clone = self.matching_clone()
        records = clone / ".v4/risks"
        shutil.rmtree(records)
        records.symlink_to(self.source / ".v4/risks", target_is_directory=True)
        self.assertIn("directory must not be a symlink", self.audit(clone, expected=1))

    def test_malformed_export_and_duplicate_inputs_fail(self):
        clone = self.matching_clone()
        original = self.rows(clone)
        for text in ("{\n", "[]\n", '{"_table":"unknown"}\n', '{"_table":[]}\n',
                     '{"_table":"event","id":1,"id":2}\n'):
            with self.subTest(text=text):
                (clone / EXPORT).write_text(text)
                self.assertIn("invalid export input", self.audit(clone, expected=1))
        for table in ("accepted_risk", "event", "_chain_head"):
            with self.subTest(table=table):
                self.write_rows(clone, original + [next(r for r in original if r["_table"] == table)])
                self.assertIn("duplicate", self.audit(clone, expected=1))
        self.write_rows(clone, original + [{"_table": "attempt", "id": 1}])
        self.assertIn("chain fields", self.audit(clone, expected=1))

    def test_missing_risk_fields_fail(self):
        clone = self.matching_clone()
        rows = self.rows(clone)
        del next(r for r in rows if r["_table"] == "accepted_risk")["who"]
        self.write_rows(clone, rows)
        self.assertIn("malformed signature row", self.audit(clone, expected=1))

    def test_first_continuation_header_cannot_claim_another_segment_number(self):
        clone = self.matching_clone()
        rows = self.rows(clone)
        self.write_rows(clone, [{"_table": "_segment", "seq": 99, "skip": {},
                                "starts_after": ledger.GENESIS}] + rows)
        self.assertIn("continuation", self.audit(clone, expected=1))

    def test_missing_open_file_and_segment_fail(self):
        self.signature()
        self.export()
        self.signature(scope="repo")
        self.export(seal=True)
        clone = self.clone()
        ledger.segment_files(clone / EXPORT)[0].unlink()
        self.assertIn("missing", self.audit(clone, expected=1))
        (clone / EXPORT).unlink()
        self.assertIn("invalid export input", self.audit(clone, expected=1))

    def test_chain_only_does_not_claim_signature_reconciliation(self):
        clone = self.matching_clone()
        (clone / TASK_RECORD).write_text("{}")
        output = self.audit(clone, signatures=False)
        self.assertIn("signature records were not reconciled", output)
        self.assertNotIn("signature records consistent", output)
        self.audit(clone, extra=("--compositions",), expected=2)

    def test_legacy_projection_and_signature_consistency_are_separate_scopes(self):
        self.signature()
        # Reproduce the old writer only in this disposable ledger. Current
        # inserts redact before hashing; no production row is rewritten.
        token = "ghp_" + "SYNTHETICfixture" * 3  # synthetic, never issued
        prev = self.conn.execute("SELECT row_hash FROM event ORDER BY id DESC LIMIT 1").fetchone()[0]
        row = {"task_id": "t", "claim_id": None, "kind": "engagement", "actor": "worker",
               "payload": json.dumps({"sentence": "fixture " + token}), "created_at": "2026",
               "prev_hash": prev, "scheme": ledger.EVENT_SCHEME}
        row["row_hash"] = ledger._event_hash(prev, row)
        for trigger in list(self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='event'")):
            self.conn.execute(f'DROP TRIGGER "{trigger[0]}"')
        self.conn.execute("INSERT INTO event (" + ",".join(row) + ") VALUES (" +
                          ",".join("?" for _ in row) + ")", list(row.values()))
        self.conn.commit()
        self.export()
        clone = self.clone()
        output = self.audit(clone)
        self.assertIn("redacted projections", output)
        self.assertIn("signature records consistent", output)
        self.assertNotIn("chain intact", output)
        self.assertNotIn(token, output + (clone / EXPORT).read_text())
        self.assertEqual(self.conn.execute("SELECT payload FROM event ORDER BY id DESC LIMIT 1").fetchone()[0],
                         row["payload"])
        ledger.projection_path(clone / EXPORT).unlink()
        self.assertIn("cannot be verified", self.audit(clone, expected=1))

    def test_duplicate_risk_identity_across_segments_fails(self):
        self.signature()
        self.export()
        self.signature(scope="repo")
        self.export(seal=True)
        clone = self.clone()
        sealed = ledger.segment_files(clone / EXPORT)[0]
        risk_row = next(json.loads(line) for line in sealed.read_text().splitlines()
                        if json.loads(line)["_table"] == "accepted_risk")
        self.write_rows(clone, self.rows(clone) + [risk_row])
        self.assertIn("duplicate accepted_risk", self.audit(clone, expected=1))

    def test_historical_symlink_is_not_a_record_even_when_its_text_is_json(self):
        self.signature()
        path = self.source / TASK_RECORD
        text = path.read_text()
        path.unlink()
        path.symlink_to(text)
        self.commit()
        self.git(self.source, "rm", TASK_RECORD)
        self.commit()
        self.export()
        self.assertIn("regular Git blob", self.audit(self.clone(), expected=1))

    def test_non_utf8_historical_record_is_a_controlled_input_failure(self):
        self.signature()
        (self.source / TASK_RECORD).write_bytes(b"\xff")
        self.commit()
        self.git(self.source, "rm", TASK_RECORD)
        self.commit()
        self.export()
        self.assertIn("UTF-8", self.audit(self.clone(), expected=1))


if __name__ == "__main__":
    unittest.main()
