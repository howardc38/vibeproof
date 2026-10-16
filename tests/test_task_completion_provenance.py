"""Completion records the source it observed, separately from current proof state."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from kernel import cli, config, hashing, ledger, lifecycle, runner
from kernel.analysis import subject_files

ROOT = Path(__file__).resolve().parents[1]
CHECKER = '''import argparse, json, sys
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--subject'); p.add_argument('--out'); p.add_argument('--facts')
a = p.parse_args()
s = json.loads(Path(a.subject).read_text())
value = (Path(s['repo_root']) / 'product.txt').read_text()
Path(a.out).write_text(json.dumps({'observed': value}))
print('read product.txt: ' + value)
sys.exit(0 if value.startswith('good') else 1)
'''


class CompletionProvenance(unittest.TestCase):
    def git(self, root, *args):
        result = subprocess.run(['git', *args], cwd=root, capture_output=True, check=True)
        return result.stdout.decode().strip()

    def repo(self, *, commit=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.git(root, 'init', '-q')
        self.git(root, 'config', 'user.email', 'test@example.invalid')
        self.git(root, 'config', 'user.name', 'completion test')
        self.git(root, 'config', 'core.filemode', 'true')
        (root / '.v4').mkdir()
        (root / 'detectors').mkdir()
        (root / 'checker.py').write_text(CHECKER)
        (root / 'product.txt').write_text('good original\n')
        (root / 'old.txt').write_text('tracked deletion fixture')
        (root / '.gitignore').write_text('__pycache__/\nignored.txt\n')
        (root / '.v4/config.json').write_text(json.dumps({
            'repo': 'fixture', 'test_command': 'python3 checker.py',
            'policy': 'allow_accepted_risk', 'derive_exclude': ['fixtures/**']}))
        (root / '.v4/claim_kinds.json').write_text(json.dumps({
            'probe': {'checker': 'probe', 'staleness': 'repo', 'gate': 'ship'}}))
        (root / '.v4/checkers.json').write_text(json.dumps({'probe': {
            'path': 'checker.py', 'sha256': hashing.file_sha(root / 'checker.py'),
            'program_sha': hashing.program_sha(root, 'checker.py'),
            'reads': ['product.txt'], 'kinds': ['probe']}}))
        if commit:
            self.commit(root)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        return root, conn, config.RepoConfig(root)

    def commit(self, root):
        self.git(root, 'add', '-A')
        self.git(root, 'commit', '-qm', 'fixture source')
        return self.git(root, 'rev-parse', 'HEAD')

    def task(self, conn, cfg, task='a', **kwargs):
        base = lifecycle.open_task(conn, cfg, task_id=task, request='Observe product.txt',
                                   scope_globs=['product.txt'], **kwargs)
        ledger.insert(conn, 'claim', id=task + '-probe', task_id=task, kind='probe',
                      question='Does the real product begin with good?', subject_refs=[],
                      checker='probe', origin='derive', created_at='2026')
        return base

    def checked(self, conn, cfg, task='a'):
        results = lifecycle.check(conn, cfg, task)
        self.assertEqual(results[0][2].exit_code, 0, results[0][2].stderr)
        attempt = ledger.latest_attempt(conn, task + '-probe')
        self.assertIn('read product.txt:', attempt['stdout'])
        return attempt

    def history(self, root, task='a'):
        with ledger.connect_readonly(root) as fresh:
            return lifecycle.completion_history(fresh, task)

    def ship(self, conn, cfg, task='a'):
        ok, report = lifecycle.ship(conn, cfg, task)
        self.assertTrue(ok, report)
        return self.history(cfg.root, task)[-1]['source_observation']

    def test_clean_completion_is_read_back_without_relabeling_attempt_stamp(self):
        root, conn, cfg = self.repo()
        base = self.task(conn, cfg)
        attempt = self.checked(conn, cfg)
        observed = self.ship(conn, cfg)
        self.assertEqual(observed['scheme'], 'v4-source-observation-1')
        self.assertEqual(observed['original_base_commit'], base)
        self.assertEqual(observed['head_commit'], self.git(root, 'rev-parse', 'HEAD'))
        self.assertEqual(observed['head_tree'], self.git(root, 'rev-parse', 'HEAD^{tree}'))
        self.assertEqual(observed['actual_worktree'], str(root))
        self.assertEqual(observed['assigned_worktree'], str(root))
        self.assertFalse(observed['git_reported_status']['dirty'])
        self.assertEqual(attempt['head_commit'], hashing.worktree_digest(root, reads=['product.txt']))
        self.assertNotEqual(attempt['head_commit'], observed['head_commit'])

    def test_dirty_staged_untracked_deleted_content_and_commit_identity(self):
        root, conn, cfg = self.repo()
        base = self.task(conn, cfg)
        (root / 'product.txt').write_text('good changed\n')
        (root / 'new.txt').write_text('new')
        (root / 'old.txt').unlink()
        (root / 'checker-copy.txt').write_text('will disappear')
        self.git(root, 'add', 'checker-copy.txt')
        (root / 'checker-copy.txt').unlink()
        self.checked(conn, cfg)
        dirty = self.ship(conn, cfg)
        self.assertTrue(dirty['git_reported_status']['dirty'])
        statuses = {e['path']: e['status'] for e in dirty['git_reported_status']['entries']}
        self.assertEqual(statuses['product.txt'], ' M')
        self.assertEqual(statuses['new.txt'], '??')
        self.assertEqual(statuses['old.txt'], ' D')
        self.assertEqual(statuses['checker-copy.txt'], 'AD')
        self.git(root, 'add', '-A')
        staged = self.ship(conn, cfg)
        head = self.commit(root)
        committed = self.ship(conn, cfg)
        self.assertEqual(dirty['product_content'], staged['product_content'])
        self.assertEqual(dirty['product_content'], committed['product_content'])
        self.assertNotEqual(dirty['git_reported_status'], staged['git_reported_status'])
        self.assertNotEqual(dirty['head_commit'], head)
        self.assertEqual(committed['head_commit'], head)
        self.assertFalse(committed['git_reported_status']['dirty'])
        self.assertEqual(committed['original_base_commit'], base)
        self.assertEqual(len(self.history(root)), 3)

    def test_mode_and_symlink_targets_are_content_even_without_proof_staleness(self):
        root, conn, cfg = self.repo()
        self.task(conn, cfg)
        self.checked(conn, cfg)
        first = self.ship(conn, cfg)
        proof = hashing.worktree_digest(root, reads=['product.txt'])
        (root / 'product.txt').chmod(0o755)
        second = self.ship(conn, cfg)
        self.assertEqual(proof, hashing.worktree_digest(root, reads=['product.txt']))
        self.assertNotEqual(first['product_content']['sha256'], second['product_content']['sha256'])
        (root / 'link').symlink_to('product.txt')
        linked = hashing.source_observation(root)
        (root / 'link').unlink()
        (root / 'link').symlink_to('missing target')
        dangling = hashing.source_observation(root)
        self.assertNotEqual(linked['product_content'], dangling['product_content'])
        self.commit(root)
        self.assertEqual(dangling['product_content'], hashing.source_observation(root)['product_content'])

    def test_nul_inventory_handles_unusual_names_and_rename_pairs(self):
        root, _conn, _cfg = self.repo()
        names = ['tab\tname', 'line\nname', 'quote"name', 'wild*[name]', '雪', 'back\\slash']
        for name in names:
            (root / name).write_text(name)
        before = hashing.source_observation(root)
        self.commit(root)
        self.assertEqual(before['product_content'], hashing.source_observation(root)['product_content'])
        self.git(root, 'mv', names[1], 'renamed\nfile')
        observed = hashing.source_observation(root)
        entry = next(e for e in observed['git_reported_status']['entries'] if e['path'] == 'renamed\nfile')
        self.assertEqual(entry['from'], names[1])
        self.assertNotEqual(before['product_content'], observed['product_content'])

    def test_status_flags_do_not_hide_actual_bytes_from_observation(self):
        for flag in ('--assume-unchanged', '--skip-worktree'):
            with self.subTest(flag=flag):
                root, _conn, _cfg = self.repo()
                before = hashing.source_observation(root)
                self.git(root, 'update-index', flag, 'product.txt')
                (root / 'product.txt').write_text('different actual bytes')
                after = hashing.source_observation(root)
                self.assertFalse(after['git_reported_status']['dirty'])
                self.assertNotEqual(before['product_content'], after['product_content'])

    def test_alternate_index_cannot_hide_tracked_ignored_content(self):
        root, conn, cfg = self.repo()
        (root / 'ignored.txt').write_text('tracked despite ignore rule')
        self.git(root, 'add', '-f', 'ignored.txt')
        self.commit(root)
        self.task(conn, cfg)
        self.checked(conn, cfg)
        with tempfile.TemporaryDirectory() as directory:
            alternate = Path(directory) / 'index'
            shutil.copyfile(root / '.git/index', alternate)
            env = dict(os.environ, GIT_INDEX_FILE=str(alternate))
            subprocess.run(['git', 'update-index', '--force-remove', 'ignored.txt'],
                           cwd=root, env=env, check=True, capture_output=True)
            normal = hashing.source_observation(root)
            with patch.dict(os.environ, {'GIT_INDEX_FILE': str(alternate)}):
                self.assertEqual(hashing.source_observation(root), normal)
                (root / 'ignored.txt').write_text('changed actual tracked bytes')
                changed = hashing.source_observation(root)
                self.assertNotEqual(normal['product_content'], changed['product_content'])
                observed = self.ship(conn, cfg)
            self.assertEqual(observed['product_content'], hashing.source_observation(root)['product_content'])

    def test_git_repository_selectors_cannot_relabel_another_checkout(self):
        root, _conn, _cfg = self.repo()
        other, _other_conn, _other_cfg = self.repo()
        (other / 'product.txt').write_text('other checkout content')
        self.commit(other)
        normal = hashing.source_observation(root)
        for overrides in ({'GIT_DIR': str(other / '.git')},
                          {'GIT_WORK_TREE': str(other)},
                          {'GIT_COMMON_DIR': str(other / '.git')},
                          {'GIT_DIR': str(other / '.git'), 'GIT_WORK_TREE': str(other)}):
            with self.subTest(overrides=overrides), patch.dict(os.environ, overrides):
                self.assertEqual(hashing.source_observation(root), normal)

    def test_only_exact_operational_outputs_are_excluded(self):
        root, _conn, _cfg = self.repo()
        before = hashing.source_observation(root)
        for rel in (*hashing.SOURCE_OUTPUTS, '.v4/ledger_export.jsonl.0001'):
            (root / rel).write_text('operational output')
        for directory in hashing.SOURCE_RECORD_DIRS:
            (root / directory).mkdir()
            (root / directory / 'record.json').write_text('private operational record')
        (root / 'ignored.txt').write_text('ignored output')
        self.assertEqual(before, hashing.source_observation(root))
        for rel in ('.gitignore', '.v4/config.json', '.v4/checkers.json',
                    '.v4/installed.json', '.v4/ledger_export.jsonl.product', 'fixtures/source.txt'):
            with self.subTest(rel=rel):
                path = root / rel
                path.parent.mkdir(exist_ok=True)
                previous = hashing.source_observation(root)
                path.write_bytes((path.read_bytes() if path.exists() else b'') + b'\nchanged')
                self.assertNotEqual(previous['product_content'], hashing.source_observation(root)['product_content'])

    def test_status_retains_historical_completion_beside_current_stale_claim(self):
        root, conn, cfg = self.repo()
        self.task(conn, cfg)
        self.checked(conn, cfg)
        self.ship(conn, cfg)
        saved = self.history(root)
        (root / 'product.txt').write_text('bad later\n')
        self.commit(root)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(['--repo', str(root), 'status', '--task', 'a', '--json'])
        data = json.loads(out.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(data['completion_history'], saved)
        self.assertEqual(data['claims'][0]['state'], 'STALE')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(['--repo', str(root), 'status', '--task', 'a'])
        self.assertIn('historical completions: 1', out.getvalue())
        self.assertIn('current claim states:', out.getvalue())

    def test_open_abandoned_and_legacy_completion_are_distinct(self):
        root, conn, cfg = self.repo()
        self.task(conn, cfg)
        self.assertEqual(self.history(root), [])
        lifecycle.abandon(conn, cfg, 'a', 'This fixture work will not be delivered; preserve its unanswered claim.')
        self.assertEqual(self.history(root), [])
        with self.assertRaisesRegex(hashing.SourceUnreadable, 'abandoned'):
            lifecycle.ship(conn, cfg, 'a')
        self.task(conn, cfg, 'legacy')
        ledger.insert(conn, 'event', task_id='legacy', kind='shipped', actor='kernel',
                      payload={'claims': 1}, created_at='old')
        self.assertIsNone(self.history(root, 'legacy')[0]['source_observation'])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(['--repo', str(root), 'status', '--task', 'legacy'])
        self.assertIn('unknown (legacy completion)', out.getvalue())

    def test_another_task_commit_does_not_reset_base_or_hide_protected_diff(self):
        root, conn, cfg = self.repo()
        base = self.task(conn, cfg)
        self.task(conn, cfg, 'b')
        (root / '.v4/config.json').write_text((root / '.v4/config.json').read_text() + '\n')
        committed = self.commit(root)
        self.assertIn('.v4/config.json', subject_files.changed_since(root, base))
        # The actual scope checker must still reject this protected, out-of-scope edit.
        subject = runner.Subject(repo_root=str(root), claim_id='scope', claim_kind='scope',
                                 task_id='a', diff_base=base, subject_refs=[],
                                 params={'scope_globs': ['product.txt']}).as_dict()
        result = runner.run_checker(repo_root=root, checker_path=ROOT / 'checkers/scope.py',
                                    registered_sha=None, subject_payload=subject, subject_refs=[])
        self.assertEqual(result.exit_code, 1, result.stdout + result.stderr)
        self.assertIn('.v4/config.json', result.stdout + result.stderr)
        cfg = config.RepoConfig(root)
        self.checked(conn, cfg)
        observed = self.ship(conn, cfg)
        self.assertEqual(observed['original_base_commit'], base)
        self.assertEqual(observed['head_commit'], committed)
        opened = lifecycle.open_task(conn, cfg, task_id='continued', request='Next work',
                                     scope_globs=['product.txt'], after='a')
        explicit = lifecycle.open_task(conn, cfg, task_id='explicit', request='Earlier work',
                                       scope_globs=['product.txt'], after='a', base=base)
        self.assertEqual(opened, committed)
        self.assertEqual(explicit, base)
        self.assertEqual(lifecycle.continues(conn, 'continued'), 'a')

    def test_divergent_worktrees_share_history_but_cannot_mislabel_completion(self):
        root, conn, cfg = self.repo()
        self.task(conn, cfg)
        self.checked(conn, cfg)
        first = self.ship(conn, cfg)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        other = Path(temp.name) / 'other'
        self.git(root, 'worktree', 'add', '-qb', 'other', str(other))
        self.addCleanup(self.git, root, 'worktree', 'remove', '--force', str(other))
        (other / 'product.txt').write_text('good divergent\n')
        other_head = self.commit(other)
        other_cfg = config.RepoConfig(other)
        self.task(conn, other_cfg, 'b')
        self.checked(conn, other_cfg, 'b')
        second = self.ship(conn, other_cfg, 'b')
        self.assertEqual(second['head_commit'], other_head)
        self.assertNotEqual(first['product_content'], second['product_content'])
        saved = self.history(root)
        with self.assertRaisesRegex(hashing.SourceUnreadable, 'different worktree'):
            lifecycle.ship(conn, other_cfg, 'a')
        self.assertEqual(self.history(other), saved)

    def test_held_and_unconfirmed_facts_do_not_append_completion(self):
        root, conn, cfg = self.repo()
        self.task(conn, cfg)
        self.assertFalse(lifecycle.ship(conn, cfg, 'a')[0])
        self.assertEqual(self.history(root), [])
        self.checked(conn, cfg)
        cfg.facts = {'absent': {'outbound_write': 'AUTO: no write found'}}
        self.assertFalse(lifecycle.ship(conn, cfg, 'a')[0])
        self.assertEqual(self.history(root), [])

    def test_export_prefix_and_explicit_later_export(self):
        root, conn, cfg = self.repo()
        self.task(conn, cfg)
        self.checked(conn, cfg)
        self.ship(conn, cfg)
        out = root / '.v4/ledger_export.jsonl'
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        self.assertFalse(any(r.get('kind') == 'shipped' for r in rows))
        self.assertFalse(ledger.verify_exported(out)[1])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['--repo', str(root), 'export', '--out', str(out)]), 0)
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        completed = next(r for r in rows if r.get('kind') == 'shipped')
        self.assertEqual(json.loads(completed['payload'])['source_observation'],
                         self.history(root)[0]['source_observation'])
        self.assertFalse(ledger.verify_exported(out)[1])

    def test_export_failure_preserves_old_completion_and_returns_non_success(self):
        root, conn, cfg = self.repo()
        self.task(conn, cfg)
        self.checked(conn, cfg)
        self.ship(conn, cfg)
        saved = self.history(root)
        out = root / '.v4/ledger_export.jsonl'
        out.unlink()
        out.mkdir()
        with contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(['--repo', str(root), 'ship', '--task', 'a'])
        self.assertEqual(code, 2)
        self.assertEqual(self.history(root), saved)

    def test_real_sqlite_insert_failure_has_no_new_completion(self):
        root, conn, cfg = self.repo()
        self.task(conn, cfg)
        self.checked(conn, cfg)
        self.ship(conn, cfg)
        saved = self.history(root)
        conn.execute("CREATE TRIGGER refuse_completion BEFORE INSERT ON event "
                     "WHEN NEW.kind = 'shipped' BEGIN SELECT RAISE(ABORT, 'fixture refusal'); END")
        conn.commit()
        with contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(['--repo', str(root), 'ship', '--task', 'a'])
        self.assertEqual(code, 5)
        self.assertEqual(self.history(root), saved)

    def test_source_changes_during_real_export_refuse_new_completion(self):
        for change in ('bytes', 'mode', 'head'):
            with self.subTest(change=change):
                root, conn, cfg = self.repo()
                self.task(conn, cfg)
                self.checked(conn, cfg)
                self.ship(conn, cfg)
                saved = self.history(root)
                export = ledger.export_jsonl
                def moved(*args, **kwargs):
                    result = export(*args, **kwargs)
                    if change == 'bytes':
                        (root / 'product.txt').write_text('good concurrent\n')
                    elif change == 'mode':
                        (root / 'product.txt').chmod(0o755)
                    else:
                        self.git(root, 'commit', '--allow-empty', '-qm', 'concurrent commit')
                    return result
                with patch.object(ledger, 'export_jsonl', moved):
                    with self.assertRaisesRegex(hashing.SourceUnreadable, 'changed during ship'):
                        lifecycle.ship(conn, cfg, 'a')
                self.assertEqual(self.history(root), saved)

    def test_abandon_during_real_export_prevents_completion(self):
        root, conn, cfg = self.repo()
        self.task(conn, cfg)
        self.checked(conn, cfg)
        export = ledger.export_jsonl
        def abandoned(*args, **kwargs):
            result = export(*args, **kwargs)
            with ledger.connect(root) as other:
                lifecycle.abandon(other, cfg, 'a', 'This task is deliberately abandoned while its real export runs.')
            return result
        with patch.object(ledger, 'export_jsonl', abandoned):
            with self.assertRaisesRegex(hashing.SourceUnreadable, 'abandoned'):
                lifecycle.ship(conn, cfg, 'a')
        self.assertFalse(conn.in_transaction)
        self.assertEqual(self.history(root), [])
        with ledger.connect_readonly(root) as fresh:
            self.assertEqual([r[0] for r in fresh.execute(
                "SELECT kind FROM event WHERE kind IN ('shipped','abandoned')")], ['abandoned'])

    def test_ship_and_abandon_serialize_their_final_eligibility_reads(self):
        root, conn, cfg = self.repo()
        self.task(conn, cfg)
        self.checked(conn, cfg)
        ready = {name: threading.Event() for name in ('ship', 'abandon')}
        start = {name: threading.Event() for name in ready}
        at_append, at_competing_begin, release_append = (threading.Event() for _ in range(3))
        results = {}
        real_insert = lifecycle.insert
        def paused_insert(connection, table, **columns):
            if table == 'event' and columns.get('kind') == 'shipped':
                at_append.set()
                if not release_append.wait(10):
                    raise RuntimeError('fixture failed to release completion append')
            return real_insert(connection, table, **columns)
        def worker(name):
            connection = ledger.connect(root)
            try:
                if name == 'abandon':
                    connection.set_trace_callback(lambda sql: at_competing_begin.set()
                                                  if sql == 'BEGIN IMMEDIATE' else None)
                ready[name].set()
                if not start[name].wait(10):
                    raise RuntimeError('fixture did not start competing writer')
                if name == 'ship':
                    results[name] = lifecycle.ship(connection, config.RepoConfig(root), 'a')[0]
                else:
                    results[name] = lifecycle.abandon(connection, cfg, 'a',
                        'Concurrent abandonment must re-read state after obtaining its write lock.')
            except Exception as exc:
                results[name] = exc
            finally:
                connection.close()
        threads = [threading.Thread(target=worker, args=(name,)) for name in ready]
        with patch.object(lifecycle, 'insert', paused_insert):
            for thread in threads:
                thread.start()
            try:
                self.assertTrue(all(event.wait(10) for event in ready.values()))
                start['ship'].set()
                self.assertTrue(at_append.wait(10))
                start['abandon'].set()
                self.assertTrue(at_competing_begin.wait(10))
            finally:
                for event in start.values():
                    event.set()
                release_append.set()
                for thread in threads:
                    thread.join(10)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertIs(results['ship'], True)
        self.assertIsInstance(results['abandon'], lifecycle.CannotAbandon)
        with ledger.connect_readonly(root) as fresh:
            self.assertEqual([r[0] for r in fresh.execute(
                "SELECT kind FROM event WHERE kind IN ('shipped','abandoned')")], ['shipped'])

    def test_rejected_endings_and_failed_appends_release_the_write_lock(self):
        root, conn, cfg = self.repo()
        self.task(conn, cfg)
        self.checked(conn, cfg)
        self.ship(conn, cfg)
        saved = self.history(root)
        with self.assertRaises(lifecycle.CannotAbandon):
            lifecycle.abandon(conn, cfg, 'a', 'A completed task must retain its earlier successful completion.')
        self.assertFalse(conn.in_transaction)
        conn.execute("CREATE TRIGGER refuse_completion BEFORE INSERT ON event "
                     "WHEN NEW.kind = 'shipped' BEGIN SELECT RAISE(ABORT, 'fixture refusal'); END")
        conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            lifecycle.ship(conn, cfg, 'a')
        self.assertFalse(conn.in_transaction)
        with ledger.connect(root) as other:
            lifecycle.open_task(other, cfg, task_id='after-refusal', request='Independent later task',
                                scope_globs=['product.txt'])
        self.assertEqual(self.history(root), saved)

    def test_unborn_is_explicit_but_broken_git_is_not_unborn(self):
        root, conn, cfg = self.repo(commit=False)
        self.task(conn, cfg)
        self.checked(conn, cfg)
        observed = self.ship(conn, cfg)
        self.assertIsNone(observed['head_commit'])
        self.assertIsNone(observed['head_tree'])
        self.assertEqual(observed['original_base_commit'], 'no-head')
        saved = self.history(root)
        (root / '.git/HEAD').write_text('broken head\n')
        with self.assertRaises(hashing.SourceUnreadable):
            lifecycle.ship(conn, cfg, 'a')
        # Read the already-known database without asking broken Git for its path.
        with sqlite3.connect(root / '.git/v4/ledger.db') as fresh:
            self.assertEqual(fresh.execute("SELECT count(*) FROM event WHERE kind='shipped'").fetchone()[0], len(saved))

    def test_unreadable_or_unsupported_content_does_not_complete(self):
        root, conn, cfg = self.repo()
        self.task(conn, cfg)
        self.checked(conn, cfg)
        self.ship(conn, cfg)
        saved = self.history(root)
        (root / 'product.txt').unlink()
        os.mkfifo(root / 'product.txt')
        with self.assertRaisesRegex(hashing.SourceUnreadable, 'unsupported source type'):
            lifecycle.ship(conn, cfg, 'a')
        self.assertEqual(self.history(root), saved)

    def test_corrupt_git_index_is_an_error_not_an_empty_inventory(self):
        root, conn, cfg = self.repo()
        self.task(conn, cfg)
        self.checked(conn, cfg)
        self.ship(conn, cfg)
        saved = self.history(root)
        (root / '.git/index').write_bytes(b'not a Git index')
        with self.assertRaisesRegex(hashing.SourceUnreadable, 'git ls-files failed'):
            lifecycle.ship(conn, cfg, 'a')
        self.assertEqual(self.history(root), saved)

    def test_unreadable_source_after_export_refuses_completion(self):
        root, conn, cfg = self.repo()
        self.task(conn, cfg)
        self.checked(conn, cfg)
        self.ship(conn, cfg)
        saved = self.history(root)
        export = ledger.export_jsonl
        def unreadable(*args, **kwargs):
            result = export(*args, **kwargs)
            (root / '.git/index').write_bytes(b'not a Git index')
            return result
        with patch.object(ledger, 'export_jsonl', unreadable):
            with self.assertRaises(hashing.SourceUnreadable):
                lifecycle.ship(conn, cfg, 'a')
        self.assertEqual(self.history(root), saved)


if __name__ == '__main__':
    unittest.main()
