"""A value-coordinate correction needs actual instruction hits and behavior."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from kernel import config, hashing, ledger, lifecycle, redgreen, review, review_coordinates, state

ROOT = Path(__file__).resolve().parents[1]
APP = '''// 🌈 non-BMP prefix keeps UTF16 coordinates honest
const port = Number(process.env.PORT || 4173);
if (!Number.isInteger(port) || port < 0 || port > 65535) {
  console.error('invalid port');
  process.exit(2);
}
console.log(port);
'''
TEST = '''const assert = require('node:assert/strict');
const {spawnSync} = require('node:child_process');
const result = spawnSync(process.execPath, ['app.cjs'], {env:{...process.env,PORT:'not-a-number'},encoding:'utf8'});
assert.equal(result.status, 2, result.stderr);
assert.match(result.stderr, /invalid port/);
'''
MUTATION=('app.cjs','!Number.isInteger(port) || ','')


@unittest.skipUnless(shutil.which('node'), 'Node is required for real declaration evidence')
class DeclarationProof(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='declaration proof ')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for command in [['git','init','-q'],['git','config','user.name','Declaration test'],['git','config','user.email','test@example.invalid']]:
            subprocess.run(command,cwd=self.root,check=True,capture_output=True)
        (self.root/'app.cjs').write_text(APP)
        (self.root/'test.cjs').write_text(TEST)
        self.commit()

    def commit(self):
        subprocess.run(['git','add','-A'],cwd=self.root,check=True,capture_output=True)
        subprocess.run(['git','-c','commit.gpgsign=false','commit','-qm','test fixture'],cwd=self.root,check=True,capture_output=True)

    def prove(self,**kw):
        args=dict(command=['node','test.cjs'],test_path='test.cjs',target_file='app.cjs',target_symbol='port',mutation=MUTATION,declaration=True)
        args.update(kw)
        return redgreen.verify(self.root,**args)

    def test_new_value_finding_is_refused_before_it_can_become_unclosable(self):
        with self.assertRaisesRegex(review.BadCoordinates,'value initializer'):
            review.resolve_symbol(self.root,'app.cjs','port')
        self.assertEqual(review.resolve_symbol(self.root,'app.cjs',''),'')

    def test_old_coordinate_gets_real_red_green_and_instruction_evidence(self):
        before=(self.root/'app.cjs').read_bytes()
        proof=self.prove()
        self.assertTrue(proof.ok,proof.as_dict())
        self.assertTrue(proof.red_failed and proof.green_passed and proof.symbol_executed)
        self.assertGreater(proof.calls,0)
        self.assertEqual(proof.source_assertion_check['status'],'passed')
        self.assertEqual((self.root/'app.cjs').read_bytes(),before)

    def test_valid_functions_and_ambiguous_or_nested_bindings_cannot_be_downgraded(self):
        for source in ['const port = () => 1;\n','function port() { return 1; }\n',
                       'function outer(){const port=1;}\n','const port=1; function x(){const port=2;}\n']:
            (self.root/'app.cjs').write_text(source)
            with self.subTest(source=source),self.assertRaises(ValueError):
                review_coordinates.resolve_declaration(self.root,'app.cjs','port')

    def test_implicit_throw_before_declaration_does_not_count_as_execution(self):
        (self.root/'app.cjs').write_text('function stop(){throw new Error("before");}\nstop();\n'+APP)
        rc,ran,calls,_=redgreen._run_traced(self.root,['node','app.cjs'],'app.cjs','port',declaration=True)
        self.assertNotEqual(rc,0)
        self.assertIs(ran,False)
        self.assertEqual(calls,0)

    def test_indirect_function_value_cannot_downgrade_to_initializer_proof(self):
        (self.root/'app.cjs').write_text('function factory(){return ()=>42;}\nconst port=factory();\nconsole.log(typeof port);\n')
        rc,ran,calls,_=redgreen._run_traced(self.root,['node','app.cjs'],'app.cjs','port',declaration=True)
        self.assertEqual(rc,0)
        self.assertIs(ran,False)
        self.assertEqual(calls,0)

    def test_startup_failure_before_red_initializer_is_not_behavioral_red(self):
        marker='const port = Number(process.env.PORT || 4173);'
        proof=self.prove(mutation=('app.cjs',marker,"throw new Error('setup broke');\n"+marker))
        self.assertFalse(proof.ok)
        self.assertTrue(proof.green_passed and proof.symbol_executed)
        self.assertFalse(proof.red_failed)

    def test_text_only_bypass_is_refused_even_after_real_instruction_hit(self):
        (self.root/'test.cjs').write_text('''const fs=require('node:fs');const assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
spawnSync(process.execPath,['app.cjs']);
const source=fs.readFileSync('app.cjs','utf8');
assert.ok(source.includes('!Number.isInteger(port)'));
''')
        self.commit()
        proof=self.prove()
        self.assertFalse(proof.ok)
        self.assertTrue(proof.green_passed and proof.red_failed)
        self.assertGreater(proof.calls,0)
        self.assertEqual(proof.source_assertion_check['status'],'failed')

    def fixture(self):
        (self.root/'.v4').mkdir()
        (self.root/'checkers').mkdir()
        checker=self.root/'checkers/review_finding.py'
        shutil.copyfile(ROOT/'checkers/review_finding.py',checker)
        (self.root/'.v4/config.json').write_text(json.dumps({'test_command':'node test.cjs','policy':'allow_accepted_risk'}))
        (self.root/'.v4/claim_kinds.json').write_text(json.dumps({'review-finding':{'checker':'review-finding','question_template':'q','staleness':'subject'}}))
        (self.root/'.v4/checkers.json').write_text(json.dumps({'review-finding':{'path':'checkers/review_finding.py','sha256':hashing.file_sha(checker),'reads':['**']}}))
        self.commit()
        conn=ledger.connect(self.root);self.addCleanup(conn.close)
        ledger.insert(conn,'task',id='t',request='test the port diagnostic',scope_globs=['**'],base_commit='HEAD',created_at='2026')
        # Historical input: the old producer accepted this named value.
        ledger.insert(conn,'claim',id='legacy',task_id='t',kind='review-finding',question='q',subject_refs=[{'kind':'file','path':'app.cjs'}],checker='review-finding',origin='review',file='app.cjs',symbol='port',note='original finding',created_at='2026')
        return conn,config.RepoConfig(self.root)

    def bind(self,conn,**changes):
        args=dict(claim_id='legacy',root=self.root,test_path='test.cjs',command=['node','test.cjs'],mutation=MUTATION,declaration=True,why='The original coordinate is a value; prove its actual initializer and diagnostic behavior.')
        args.update(changes)
        review.bind_closing_test(conn,**args)

    def test_immutable_claim_real_checker_and_changed_test_staleness(self):
        conn,cfg=self.fixture();row=conn.execute("SELECT * FROM claim WHERE id='legacy'").fetchone();original=dict(row)
        self.bind(conn)
        lifecycle.check(conn,cfg,'t',only=['legacy'])
        report=lambda:state.claim_state(conn,self.root,row,kinds_cfg=cfg.kinds,config_sha=cfg.sha,checker_sha_of=cfg.checker_sha_on_disk)
        self.assertEqual(report(),state.ANSWERED)
        self.assertEqual(dict(conn.execute("SELECT * FROM claim WHERE id='legacy'").fetchone()),original)
        (self.root/'test.cjs').write_text(TEST+'\n// a changed proof must be checked again\n')
        self.assertEqual(report(),state.STALE)

    def test_rebinding_or_removing_coordinate_proof_expires_old_pass(self):
        conn,cfg=self.fixture();self.bind(conn);lifecycle.check(conn,cfg,'t',only=['legacy'])
        self.bind(conn,declaration=False,why=None)
        row=conn.execute("SELECT * FROM claim WHERE id='legacy'").fetchone()
        self.assertEqual(state.claim_state(conn,self.root,row,kinds_cfg=cfg.kinds,config_sha=cfg.sha,checker_sha_of=cfg.checker_sha_on_disk),state.STALE)
        lifecycle.check(conn,cfg,'t',only=['legacy'])
        self.assertNotEqual(state.claim_state(conn,self.root,row,kinds_cfg=cfg.kinds,config_sha=cfg.sha,checker_sha_of=cfg.checker_sha_on_disk),state.ANSWERED)

    def test_changed_proof_during_check_is_not_absorbed_into_pass(self):
        conn,cfg=self.fixture();self.bind(conn)
        original=lifecycle.runner.run_checker
        def changing(*args,**kwargs):
            result=original(*args,**kwargs)
            (self.root/'test.cjs').write_text(TEST+'\n// changed during the proof\n')
            return result
        with patch.object(lifecycle.runner,'run_checker',changing):lifecycle.check(conn,cfg,'t',only=['legacy'])
        self.assertEqual(conn.execute('SELECT exit_code FROM attempt ORDER BY id DESC LIMIT 1').fetchone()[0],lifecycle.runner.SUBJECT_MOVED)

    def test_installer_owned_adapter_replacement_expires_a_real_closing_attempt(self):
        conn,cfg=self.fixture()
        directory=self.root/'.v4/surface';directory.mkdir()
        adapter=directory/'playwright.cjs'
        def install(code):
            adapter.write_text(code)
            (self.root/'.v4/installed.json').write_text(json.dumps({'.v4/surface/playwright.cjs':hashing.file_sha(adapter)}))
        install('module.exports = {version:1};\n')
        self.bind(conn);lifecycle.check(conn,cfg,'t',only=['legacy'])
        row=conn.execute("SELECT * FROM claim WHERE id='legacy'").fetchone()
        current=lambda:state.claim_state(conn,self.root,row,kinds_cfg=cfg.kinds,config_sha=cfg.sha,checker_sha_of=cfg.checker_sha_on_disk)
        self.assertEqual(current(),state.ANSWERED)
        tree=hashing.worktree_digest(self.root)
        install('module.exports = {version:2};\n')
        self.assertEqual(tree,hashing.worktree_digest(self.root))
        self.assertEqual(current(),state.STALE)
