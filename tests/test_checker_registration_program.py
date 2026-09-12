"""Registration must bind the decision module, not only the CLI wrapper."""
import json
from pathlib import Path
import tempfile
import subprocess
import unittest

from kernel import config, doctor, hashing, ledger, lifecycle, register, runner, state

CHECKER = '''import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rules import judge
p = argparse.ArgumentParser()
p.add_argument('--subject'); p.add_argument('--out'); p.add_argument('--facts')
a = p.parse_args()
s = json.loads(Path(a.subject).read_text())
root = Path(s['repo_root'])
code = judge((root / s['subject_refs'][0]['path']).read_text())
print('FAIL: bad input' if code else 'PASS: valid input')
raise SystemExit(code)
'''
RULE = "def judge(text):\n    return int('bad' in text)\n"


class RegisteredProgram(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='checker-program-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True, capture_output=True)
        (self.root / 'checkers').mkdir()
        (self.root / '.v4').mkdir()
        self.checker = self.root / 'checkers/judge.py'
        self.checker.write_text(CHECKER)
        (self.root / 'rules.py').write_text(RULE)
        self.fixtures = self.root / 'fixtures'
        for colour, value, count in [('red', 'bad', 5), ('green', 'good', 5), ('bypass', 'bad alias', 3)]:
            (self.fixtures / colour).mkdir(parents=True)
            for i in range(count):
                (self.fixtures / colour / f'{i}.txt').write_text(value)
        self.conn = ledger.connect(self.root)
        self.addCleanup(self.conn.close)

    def register(self):
        return register.register(self.conn, checkers_json=self.root / '.v4/checkers.json',
                                 checker_id='judge', checker_path=self.checker, kinds=['judge'],
                                 fixtures_dir=self.fixtures, timeout_sec=20, repo_root=self.root,
                                 reads=['**'])

    def run_checker(self, entry):
        return runner.run_checker(repo_root=self.root, checker_path=self.checker,
                                  registered_sha=entry['sha256'],
                                  registered_program_sha=entry.get('program_sha'),
                                  subject_payload={'repo_root': str(self.root), 'subject_refs': [
                                      {'kind': 'file', 'path': 'fixtures/red/0.txt'}]},
                                  subject_refs=[{'kind': 'file', 'path': 'fixtures/red/0.txt'}])

    def test_dependency_edit_requires_registration_and_retains_original_event(self):
        ok, report = self.register()
        self.assertTrue(ok, report)
        entry = json.loads((self.root / '.v4/checkers.json').read_text())['judge']
        self.assertEqual(entry.get('program_sha'), hashing.program_sha(self.root, self.checker))
        before = [tuple(row) for row in self.conn.execute('SELECT * FROM event')]
        self.assertEqual(self.run_checker(entry).exit_code, 1)
        wrapper = self.checker.read_bytes()
        (self.root / 'rules.py').write_text('def judge(text):\n    return 0\n')
        self.assertEqual(self.checker.read_bytes(), wrapper)
        self.assertNotEqual(hashing.program_sha(self.root, self.checker), entry['program_sha'])
        refused = self.run_checker(entry)
        self.assertEqual(refused.exit_code, runner.CHECKER_TAMPERED)
        self.assertEqual(refused.argv, [])
        output = []
        doctor._check_registered_hashes_match_what_is_on_disk(self.root, output)
        self.assertTrue(any(row['status'] == doctor.BAD and 'program' in row['what'] for row in output), output)
        ok, report = self.register()
        self.assertFalse(ok, report)
        self.assertNotIn('judge', json.loads((self.root / '.v4/checkers.json').read_text()))
        self.assertEqual([tuple(row) for row in self.conn.execute('SELECT * FROM event')][:len(before)], before)
        (self.root / 'rules.py').write_text(RULE + '# a legitimate revision\n')
        ok, report = self.register()
        self.assertTrue(ok, report)
        entry = json.loads((self.root / '.v4/checkers.json').read_text())['judge']
        self.assertEqual(self.run_checker(entry).exit_code, 1)
        latest = json.loads(self.conn.execute("SELECT payload FROM event WHERE kind='register' ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertFalse(latest.get('unchanged', False))
        self.assertEqual(latest['program_sha'], entry['program_sha'])

    def test_legacy_registration_is_visible_as_unpinned_not_full_match(self):
        (self.root / '.v4/checkers.json').write_text(json.dumps({'judge': {
            'path': 'checkers/judge.py', 'sha256': hashing.file_sha(self.checker)}}))
        output = []
        doctor._check_registered_hashes_match_what_is_on_disk(self.root, output)
        self.assertTrue(any(row['status'] == doctor.WARN and 'program' in row['what'] for row in output), output)

    def test_program_change_during_execution_cannot_produce_an_answer(self):
        self.checker.write_text(CHECKER.replace("print('FAIL:",
            "(root / 'rules.py').write_text('def judge(text): return 0\\n')\nprint('FAIL:"))
        entry = {'sha256': hashing.file_sha(self.checker),
                 'program_sha': hashing.program_sha(self.root, self.checker)}
        result = self.run_checker(entry)
        self.assertEqual(result.exit_code, runner.SUBJECT_MOVED, result.stderr)
        self.assertIn('program changed', result.stderr)

    def test_normal_claim_check_expires_old_pass_and_refuses_changed_dependency(self):
        ok, report = self.register(); self.assertTrue(ok, report)
        (self.root / '.v4/config.json').write_text(json.dumps({
            'test_command': 'python3 -m unittest', 'policy': 'allow_accepted_risk'}))
        (self.root / '.v4/claim_kinds.json').write_text(json.dumps({'judge': {
            'checker': 'judge', 'staleness': 'subject', 'question_template': 'Is the input valid?'}}))
        subprocess.run(['git', 'add', '-A'], cwd=self.root, check=True, capture_output=True)
        subprocess.run(['git', '-c', 'user.name=Registration test', '-c', 'user.email=test@example.invalid',
                        '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture'],
                       cwd=self.root, check=True, capture_output=True)
        ledger.insert(self.conn, 'task', id='t', request='validate input', scope_globs=['**'], base_commit='HEAD', created_at='2026')
        ledger.insert(self.conn, 'claim', id='c', task_id='t', kind='judge', question='Is the input valid?',
                      subject_refs=[{'kind':'file','path':'fixtures/green/0.txt'}], checker='judge',
                      origin='derive', file='fixtures/green/0.txt', created_at='2026')
        cfg=config.RepoConfig(self.root)
        lifecycle.check(self.conn,cfg,'t',only=['c'])
        row=self.conn.execute("SELECT * FROM claim WHERE id='c'").fetchone()
        current=lambda:state.claim_state(self.conn,self.root,row,kinds_cfg=cfg.kinds,
                                       config_sha=cfg.sha,checker_sha_of=cfg.checker_sha_on_disk)
        self.assertEqual(current(),state.ANSWERED)
        (self.root/'rules.py').write_text('def judge(text):\n    return 0\n')
        self.assertEqual(current(),state.STALE)
        lifecycle.check(self.conn,cfg,'t',only=['c'])
        codes=[r[0] for r in self.conn.execute("SELECT exit_code FROM attempt WHERE claim_id='c' ORDER BY id")]
        self.assertEqual(codes,[0,runner.CHECKER_TAMPERED])

    def test_program_change_during_registration_cannot_be_pinned(self):
        self.checker.write_text(CHECKER.replace("print('FAIL:",
            "with (root / 'rules.py').open('a') as f: f.write('# changed during a fixture\\n')\nprint('FAIL:"))
        ok, report = self.register()
        self.assertFalse(ok, report)
        self.assertTrue(any('program changed' in reason for reason in report['failures']), report)
        self.assertFalse((self.root / '.v4/checkers.json').exists())
