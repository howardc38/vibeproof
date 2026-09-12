"""Actual command boundaries distinguish missing work from successful work."""
import json
import subprocess
import unittest
from datetime import datetime, timezone

from kernel import ledger, lifecycle, config, review
from tests import test_maintenance as fixture_module

SOURCE=fixture_module.SOURCE


class Inputs(unittest.TestCase):
    def setUp(self):
        fixture=fixture_module.Maintenance();fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.root=fixture.root

    def cli(self,*args):
        return subprocess.run([str(SOURCE/'bin/v4'),'--repo',str(self.root),*args],cwd=self.root,capture_output=True,text=True)

    def test_missing_task_is_not_a_green_or_a_checker_failure(self):
        for command in [('status',),('status','--json'),('derive',),('check',),('ship',),('scope','show')]:
            with self.subTest(command=command):
                r=self.cli(*command,'--task','not-opened')
                self.assertEqual(r.returncode,2,r.stdout+r.stderr)
                self.assertIn('no such task',r.stderr)
                self.assertNotIn('Traceback',r.stderr)
                self.assertNotIn('"total": 0',r.stdout)

    def test_existing_task_and_report_only_counts_keep_their_meaning(self):
        c=ledger.connect(self.root);self.addCleanup(c.close)
        kinds=json.loads((self.root/'.v4/claim_kinds.json').read_text());kinds['review-finding']['gate']='report'
        (self.root/'.v4/claim_kinds.json').write_text(json.dumps(kinds))
        cfg=config.RepoConfig(self.root)
        lifecycle.open_task(c,cfg,task_id='real',request='Review this isolated computation',scope_globs=['app.py'])
        review.raise_finding(c,cfg,task_id='real',file='app.py',symbol='calculate',note='The required result needs independent evidence',lens='')
        r=self.cli('status','--task','real','--json');body=json.loads(r.stdout)
        self.assertEqual((body['total'],body['terminal'],body['not_blocking']),(1,0,1))
        self.assertEqual(body['claims'][0]['state'],'OPEN')

    def test_explicit_standing_task_is_created_without_inventing_other_tasks(self):
        c=ledger.connect(self.root);self.addCleanup(c.close);cfg=config.RepoConfig(self.root)
        first=review.raise_finding(c,cfg,task_id='repo-review',file='app.py',symbol='calculate',note='This calculation needs a concrete proof',lens='')
        second=review.raise_finding(c,cfg,task_id=None,file='app.py',symbol='calculate',note='This calculation needs a concrete proof',lens='')
        self.assertEqual(first[0],second[0]);self.assertFalse(second[1])
        r=self.cli('review','add','--task','not-opened','--file','app.py','--symbol','calculate','--note','A separate observation in the fixture')
        self.assertEqual(r.returncode,2,r.stdout+r.stderr)
        self.assertIsNone(c.execute("SELECT 1 FROM task WHERE id='not-opened'").fetchone())

    def test_agent_filter_cannot_return_another_agents_observations(self):
        c=ledger.connect(self.root);self.addCleanup(c.close)
        with ledger.writing(c):
            for aid,host,session,hook in [('a','codex','s','write_block'),('b','codex','s','bash_guard'),('a','claude','s','stop_gate'),('a','codex','other','stop_gate')]:
                ledger.insert(c,'event',kind='hook_seen',actor='hook',payload={'agent_id':aid,'host':host,'session':session,'hook':hook,'basis':'cleared','scattered':False},created_at=datetime.now(timezone.utc).isoformat())
        for aid,want in [('a',['write_block']),('b',['bash_guard']),('missing',[])]:
            with self.subTest(agent=aid):
                r=self.cli('host','status','--host','codex','--session','s','--agent',aid)
                self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout)['observed'],want)
        self.assertEqual(json.loads(self.cli('host','status','--host','codex','--session','s').stdout)['observed'],['bash_guard','write_block'])

    def test_facts_help_does_not_load_or_rewrite_an_adopter(self):
        for sub in ['propose','validate','verify','scan','restate']:
            for help_flag in ['--help','-h']:
                with self.subTest(sub=sub,flag=help_flag):
                    before={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file() and '.git' not in p.relative_to(self.root).parts}
                    r=self.cli('facts',sub,help_flag)
                    self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertIn('usage:',r.stdout)
                    after={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file() and '.git' not in p.relative_to(self.root).parts}
                    self.assertEqual(after,before)

    def test_restate_help_preserves_stale_facts_and_normal_restate_still_works(self):
        (self.root/'app.py').write_text('# moved sites\n\ndef go():\n    requests.post("unused")\n    requests.get("unused")\n    check_permission()\n')
        data={'repo':self.root.name,'generated_from_commit':'0'*40,
              'outbound_write':[{'pattern':'requests.post','seen_at':'app.py:1','kind':'http'}],
              'outbound_read':[{'pattern':'requests.get','seen_at':'app.py:1','kind':'http'}],
              'auth_decision':[{'pattern':'check_permission','seen_at':'app.py:1','kind':'authz'}],
              'entrypoint_globs':['app.py'],'ui_globs':[],'config_files':['.v4/config.json'],'protected_paths':['.v4/**']}
        p=self.root/('.v4/facts.'+self.root.name+'.json');p.write_text(json.dumps(data));before=p.read_bytes()
        r=self.cli('facts','restate','--help')
        self.assertEqual(r.returncode,0,r.stderr);self.assertIn('usage:',r.stdout);self.assertEqual(p.read_bytes(),before)
        r=self.cli('facts','verify','--gone-only');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(self.cli('facts','verify').returncode,1)
        r=self.cli('facts','restate');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(json.loads(p.read_text())['outbound_write'][0]['seen_at'],'app.py:4')
        self.assertEqual(self.cli('facts','verify').returncode,0)

if __name__=='__main__':unittest.main()
