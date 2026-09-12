"""The import graph must follow the source the installed worktree resolves."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from kernel import config, doctrine, hashing, install, layout, runner


class ProgramIdentity(unittest.TestCase):
    def setUp(self):
        td=tempfile.TemporaryDirectory(prefix='v4-program-location-');self.addCleanup(td.cleanup)
        self.base=Path(td.name).resolve();self.root=self.base/'adopter';self.root.mkdir()
        self.framework=self.base/'framework';(self.framework/'kernel').mkdir(parents=True)
        (self.framework/'kernel/__init__.py').write_text('')
        self.logic=self.framework/'kernel/decision.py';self.logic.write_text('VALUE = 1\n')
        (self.root/'.v4').mkdir();(self.root/'checkers').mkdir()
        (self.root/'checkers/probe.py').write_text('from kernel.decision import VALUE\nprint(VALUE)\n')
        for args in [('init','-q'),('config','user.name','Probe'),('config','user.email','probe@example.invalid')]:self.git(*args)
        install.write_launcher(self.root,self.framework)
        self.git('add','-A');self.git('-c','commit.gpgsign=false','commit','-qm','baseline')
        self.worker=self.base/'worker';self.git('worktree','add','--detach',str(self.worker),'HEAD')
        clean=patch.dict(os.environ,{'V4_HOME':''});clean.start();self.addCleanup(clean.stop)

    def git(self,*args):
        return subprocess.check_output(['git',*args],cwd=self.root,text=True,stderr=subprocess.PIPE).strip()

    def digest(self,root):return hashing.program_sha(root,root/'checkers/probe.py')

    def execute(self,root,framework=None):
        env={**os.environ,'PYTHONPATH':str(framework or self.framework),'PYTHONDONTWRITEBYTECODE':'1'}
        return subprocess.check_output([sys.executable,str(root/'checkers/probe.py')],cwd=root,env=env,text=True).strip()

    def test_main_and_worktree_hash_the_same_real_program(self):
        self.assertFalse((self.worker/'.v4/home').exists())
        self.assertEqual(layout.framework_home(self.worker),self.framework)
        before=self.digest(self.root)
        self.assertEqual(self.digest(self.worker),before)
        self.assertEqual(self.execute(self.worker),'1')
        self.logic.write_text('VALUE = 2\n')
        after=self.digest(self.root)
        self.assertNotEqual(after,before)
        self.assertEqual(self.digest(self.worker),after)
        self.assertEqual(self.execute(self.worker),'2')

    def test_explicit_override_is_used_by_both_contexts(self):
        other=self.base/'other';(other/'kernel').mkdir(parents=True)
        (other/'kernel/__init__.py').write_text('');(other/'kernel/decision.py').write_text('VALUE = 9\n')
        original=self.digest(self.root)
        with patch.dict(os.environ,{'V4_HOME':str(other)}):
            self.assertEqual(layout.framework_home(self.root),other)
            self.assertEqual(layout.framework_home(self.worker),other)
            self.assertEqual(self.digest(self.root),self.digest(self.worker))
            self.assertNotEqual(self.digest(self.worker),original)
            self.assertEqual(self.execute(self.worker,other),'9')

    def test_relative_entry_is_relative_to_the_requested_repo(self):
        self.assertEqual(hashing.program_sha(self.worker,'checkers/probe.py'),self.digest(self.worker))

    def test_recorded_program_matches_actual_runner_even_with_a_stale_marker(self):
        other=self.base/'executing';(other/'kernel').mkdir(parents=True)
        (other/'kernel/__init__.py').write_text('');(other/'kernel/decision.py').write_text('VALUE = 9\n')
        (self.root/'.v4/config.json').write_text(json.dumps({'test_command':'python3 -m unittest','policy':'allow_accepted_risk'}))
        (self.root/'.v4/checkers.json').write_text(json.dumps({'probe':{'path':'checkers/probe.py','sha256':hashing.file_sha(self.root/'checkers/probe.py')}}))
        with patch.object(runner,'V4_HOME',str(other)):
            result=runner.run_checker(repo_root=self.root,checker_path=self.root/'checkers/probe.py',registered_sha='',subject_payload={},subject_refs=[])
            self.assertEqual(result.exit_code,0)
            self.assertEqual(result.stdout.strip(),'9')
            actual=config.RepoConfig(self.root).checker_sha_on_disk('probe')
            self.assertEqual(actual,result.checker_sha)
            self.assertNotEqual(actual,self.digest(self.root),'the stale marker must not label a different executing program')

    def test_custom_nondetector_kind_is_not_misrepresented_as_review_add(self):
        (self.root/'.v4/config.json').write_text(json.dumps({'test_command':'python3 -m unittest','policy':'allow_accepted_risk'}))
        (self.root/'.v4/claim_kinds.json').write_text(json.dumps({'probe-kind':{'checker':'probe','staleness':'subject'},'review-finding':{'checker':'probe','staleness':'subject'}}))
        text=doctrine.render(config.RepoConfig(self.root))
        line=next(x for x in text.splitlines() if x.startswith('`probe-kind`'))
        self.assertIn('未配置 detector',line)
        self.assertNotIn('佢由 `v4 review add` 提出',line)


if __name__=='__main__':unittest.main()
