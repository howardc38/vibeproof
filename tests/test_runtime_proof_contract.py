"""Runtime proofs must correlate a real write and judge the actual change.

These enter the detector/checker processes over disposable Git/SQLite repos.
The truth reader reads the database, never the trigger's response or a mock.
"""
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = shlex.quote(sys.executable)


class RuntimeProofContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.repo = self.work / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Runtime proof test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.write('.gitignore', 'state.db*\n__pycache__/\nevents.jsonl\n')
        self.write('tools/__init__.py', '')
        self.write('app/__init__.py', '')
        self.write('app/feature.py', "def note():\n    return 'persisted feature output'\n")
        self.write('tools/trigger.py',
                   "import json,sqlite3,sys\nfrom app.feature import note\n"
                   "with open('events.jsonl','a') as f: f.write(json.dumps(['trigger',sys.argv[1]])+'\\n')\n"
                   "c=sqlite3.connect('state.db')\n"
                   "c.execute('INSERT INTO notes VALUES (?,?)',(sys.argv[1],note()))\nc.commit()\n")
        self.write('tools/truth.py',
                   "import json,sqlite3,sys\nq=sys.stdin.read()\n"
                   "with open('events.jsonl','a') as f: f.write(json.dumps(['query',q])+'\\n')\n"
                   "if q.strip():\n"
                   " c=sqlite3.connect('file:state.db?mode=ro',uri=True)\n"
                   " print(c.execute(q).fetchone()[0])\n")
        self.write('tools/deaf.py',
                   "import sqlite3\nc=sqlite3.connect('file:state.db?mode=ro',uri=True)\n"
                   "print(c.execute('SELECT count(*) FROM notes').fetchone()[0])\n")
        with sqlite3.connect(self.repo / 'state.db') as c:
            c.execute('CREATE TABLE notes (run_id TEXT, body TEXT)')
        self.proof = {'name': 'persisted note',
                      'trigger': f'{PYTHON} -m tools.trigger {{run_id}}',
                      'truth': "SELECT count(*) FROM notes WHERE run_id='{run_id}'",
                      'expect': 'gt:0', 'covers': ['app/**']}
        self.configure()
        self.base = self.commit()

    def write(self, rel, body):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        return p

    def git(self, *args):
        return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null',
                               '-c', 'commit.gpgsign=false', *args],
                              cwd=self.repo, text=True, capture_output=True,
                              check=True).stdout.strip()

    def commit(self):
        self.git('add', '-A')
        self.git('commit', '-qm', 'fixture state')
        return self.git('rev-parse', 'HEAD')

    def configure(self, *, provider='truth', proofs=None):
        self.write('.v4/config.json', json.dumps({
            'test_command': 'true', 'policy': 'no_accepted_risk',
            'truth_command': f'{PYTHON} -m tools.{provider}',
            'runtime_proof': [self.proof] if proofs is None else proofs}))

    def run_program(self, folder, base=None):
        subject = self.work / 'subject.json'
        out = self.work / 'out.json'
        out.unlink(missing_ok=True)
        subject.write_text(json.dumps({'repo_root': str(self.repo),
                                       'diff_base': self.base if base is None else base}))
        proc = subprocess.run([sys.executable, str(ROOT / folder / 'runtime_proof.py'),
                               '--subject', str(subject), '--out', str(out)],
                              cwd=self.repo, text=True, capture_output=True, timeout=30)
        return proc, json.loads(out.read_text()) if out.exists() else {}

    def check(self, expected, base=None):
        proc, out = self.run_program('checkers', base)
        self.assertEqual(proc.returncode, expected, proc.stdout + proc.stderr)
        return out

    def test_changing_state_cannot_make_a_deaf_reader_pass_twice(self):
        self.proof.pop('covers')
        self.proof['trigger'] = f'{PYTHON} -m tools.trigger fixed-id'
        self.configure(provider='deaf')
        for _ in range(2):
            out = self.check(1)
            with sqlite3.connect(self.repo / 'state.db') as c:
                self.assertEqual(c.execute('SELECT count(*) FROM notes WHERE run_id=?',
                                           (out['run_id'],)).fetchone()[0], 0)

    def test_noop_trigger_is_still_rejected(self):
        self.proof.pop('covers')
        self.proof['trigger'] = 'true'
        self.configure(provider='deaf')
        self.check(1)

    def test_each_proof_reads_a_control_after_its_own_trigger_and_query(self):
        self.proof.pop('covers')
        other = dict(self.proof, name='second write')
        self.configure(proofs=[self.proof, other])
        out = self.check(0)
        events = [json.loads(l) for l in (self.repo / 'events.jsonl').read_text().splitlines()]
        self.assertEqual([e[0] for e in events], ['trigger', 'query', 'query'] * 2)
        self.assertEqual([events[i][1] for i in (2, 5)], ['', ''])
        with sqlite3.connect(self.repo / 'state.db') as c:
            self.assertEqual(c.execute('SELECT count(*) FROM notes WHERE run_id=?',
                                       (out['run_id'],)).fetchone()[0], 2)

    def test_failed_trigger_does_not_query(self):
        self.proof.pop('covers')
        self.proof['trigger'] = 'exit 7'
        self.configure()
        self.check(1)
        self.assertFalse((self.repo / 'events.jsonl').exists())

    def test_new_feature_works_untracked_staged_and_committed(self):
        self.git('rm', 'app/feature.py')
        base = self.commit()
        self.write('app/feature.py', "def note():\n    return 'new real feature'\n")
        for phase in ('untracked', 'staged', 'committed'):
            with self.subTest(phase=phase):
                if phase == 'staged': self.git('add', 'app/feature.py')
                if phase == 'committed': self.commit()
                out = self.check(0, base)
                with sqlite3.connect(self.repo / 'state.db') as c:
                    self.assertEqual(c.execute('SELECT body FROM notes WHERE run_id=?',
                                               (out['run_id'],)).fetchone()[0], 'new real feature')

    def assert_not_applicable(self):
        proc, detector = self.run_program('detectors')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn('V4-CLAIM:', proc.stdout)
        self.assertEqual(detector['scope']['status'], 'not_applicable')
        out = self.check(0)
        self.assertEqual(out['scope'], detector['scope'])
        self.assertEqual(out['results'], [])
        self.assertNotIn('run_id', out)
        self.assertFalse((self.repo / 'events.jsonl').exists())

    def test_committed_test_only_change_has_one_applicability_answer(self):
        self.write('tests/test_note.py', "from app.feature import note\ndef test_note():\n    assert note()\n")
        self.commit()
        self.assert_not_applicable()

    def test_docs_only_change_is_explicit_and_runs_no_trigger(self):
        self.write('docs/usage.md', '# Using the notes feature\n')
        self.assert_not_applicable()

    def test_deleted_test_is_still_test_only(self):
        self.write('tests/test_note.py', 'def test_note():\n    assert True\n')
        self.base = self.commit()
        self.git('rm', 'tests/test_note.py')
        self.assert_not_applicable()

    def test_mixed_change_and_unknown_language_still_require_coverage(self):
        for path, body in [('other/new.py', 'value=1\n'), ('new.ex', 'defmodule New do\nend\n'),
                           ('package.json', '{}\n'), ('docs/page.mdx', 'export const x=1;\n')]:
            with self.subTest(path=path):
                p = self.write(path, body)
                self.write('docs/usage.md', '# Docs\n')
                proc, _ = self.run_program('detectors')
                self.assertIn('V4-CLAIM:', proc.stdout)
                self.check(4)
                p.unlink()

    def test_inline_rust_tests_do_not_hide_production_code(self):
        self.write('src/lib.rs', 'pub fn value()->u8 { 2 }\n#[cfg(test)] mod tests {}\n')
        self.check(4)

    def test_symlinks_and_executable_docs_are_not_exempt(self):
        p = self.repo / 'usage.md'
        p.symlink_to('app/feature.py')
        self.check(4)
        p.unlink()
        self.write('usage.md', '#!/bin/sh\nexit 1\n').chmod(0o755)
        self.check(4)

    def test_a_module_becoming_a_test_still_changes_production_code(self):
        self.write('test_runner.py', 'def run():\n    return 1\n')
        self.base = self.commit()
        self.write('test_runner.py', 'def test_value():\n    assert True\n')
        self.check(4)

    def test_git_paths_keep_their_literal_spelling(self):
        from kernel.analysis.subject_files import changed_since
        paths = ['docs/a b.md', 'docs/a"b.md', 'docs/a\nb.md', ' ']
        for path in paths:
            self.write(path, 'content\n')
        self.assertEqual(changed_since(self.repo, self.base), frozenset(paths))
        self.check(4)  # the unknown whitespace-named file is not lost

    def test_renaming_production_to_a_test_does_not_hide_the_deletion(self):
        self.write('other/service.py', 'value=1\n')
        self.base = self.commit()
        (self.repo / 'tests').mkdir()
        self.git('mv', 'other/service.py', 'tests/test_service.py')
        self.check(4)

    def test_explicit_covers_runs_even_when_the_file_looks_like_a_test(self):
        self.proof['covers'] = ['tests/**']
        self.configure()
        self.base = self.commit()
        self.write('tests/test_note.py', 'def test_note():\n    assert True\n')
        out = self.check(0)
        self.assertEqual(len(out['results']), 1)
        self.assertTrue((self.repo / 'events.jsonl').exists())

    def test_unscoped_legacy_proof_still_runs_for_docs(self):
        self.proof.pop('covers')
        self.configure()
        self.base = self.commit()
        self.write('docs/usage.md', '# Docs\n')
        self.assertEqual(len(self.check(0)['results']), 1)

    def test_no_base_or_unreadable_diff_is_not_not_applicable(self):
        self.write('docs/usage.md', '# Docs\n')
        for base in ('', '0' * 40):
            with self.subTest(base=base):
                proc, detector = self.run_program('detectors', base)
                self.assertIn('V4-CLAIM:', proc.stdout)
                self.assertNotEqual(detector.get('scope', {}).get('status'), 'not_applicable')
                self.assertEqual(len(self.check(0, base)['results']), 1)

    def test_old_claim_gets_a_recorded_answer_and_ships_without_a_waiver(self):
        from kernel import config, ledger, lifecycle
        # Use the shipped, registered runtime programs and actual runner/ledger.
        # Other kinds are outside this isolated runtime-obligation integration.
        for folder in ('checkers', 'detectors'):
            self.write(folder + '/runtime_proof.py', (ROOT / folder / 'runtime_proof.py').read_text())
        for registry, key in [('checkers', 'runtime-proof'), ('detectors', 'runtime_proof.py'),
                              ('claim_kinds', 'runtime-proof')]:
            entry = json.loads((ROOT / '.v4' / (registry + '.json')).read_text())[key]
            self.write('.v4/' + registry + '.json', json.dumps({key: entry}))
        self.write('.v4/home', str(ROOT))
        self.base = self.commit()
        cfg = config.RepoConfig(self.repo)
        conn = ledger.connect(self.repo)
        self.addCleanup(conn.close)
        with patch.dict(os.environ, {'PYTHONPATH': str(ROOT), 'V4_HOME': str(ROOT)}):
            lifecycle.open_task(conn, cfg, task_id='t-runtime-scope',
                                request='Add a test for the existing feature', scope_globs=['**'])
            # At task opening there is no diff yet, so there is a real claim
            # and an unsupported attempt. Neither row is erased by the fix.
            derived = lifecycle.derive(conn, cfg, 't-runtime-scope')
            self.assertEqual(len(derived['created']), 1, derived)
            lifecycle.check(conn, cfg, 't-runtime-scope')
            before = [dict(r) for r in conn.execute('SELECT * FROM attempt ORDER BY id')]
            self.assertEqual([r['exit_code'] for r in before], [4])
            self.write('tests/test_note.py', 'def test_note():\n    assert True\n')
            lifecycle.check(conn, cfg, 't-runtime-scope')
            after = [dict(r) for r in conn.execute('SELECT * FROM attempt ORDER BY id')]
            self.assertEqual(after[:len(before)], before)
            self.assertEqual([r['exit_code'] for r in after], [4, 0])
            details = [json.loads(r[0]) for r in conn.execute(
                "SELECT payload FROM event WHERE kind='checker_out' ORDER BY id")]
            self.assertTrue(any(p.get('scope', {}).get('status') == 'not_applicable' for p in details), details)
            ok, report = lifecycle.ship(conn, cfg, 't-runtime-scope')
            self.assertTrue(ok, report)
            self.assertEqual(conn.execute('SELECT count(*) FROM accepted_risk').fetchone()[0], 0)
            self.assertFalse((self.repo / 'events.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
