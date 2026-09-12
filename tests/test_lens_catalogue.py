"""Moved questions retain identity and have one live text owner."""
import json
from pathlib import Path
import subprocess
import unittest

from kernel import config, install, lens_catalogue, maintenance, review
from tests import test_maintenance as fixture_module


class Catalogue(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture_module.Maintenance()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root

    def install_catalogue(self):
        d=self.root/'.v4/lenses';d.mkdir()
        lens={'name':'Current','source':'fixture','checks':[{'id':'old:1','check':'Inspect the real calculation.'},{'id':'old:2','same_as':'old:1'}],'anti_patterns':[]}
        (d/'current.json').write_text(json.dumps(lens))
        catalogue={'schema':1,'entries':{cid:{'owner':'current','legacy_lens':'prevention'} for cid in ('old:1','old:2')},
                   'legacy':{'prevention':{'name':'Legacy','source':'fixture','check_ids':['old:1','old:2'],'anti_patterns':[]}}}
        (self.root/lens_catalogue.CATALOGUE).write_text(json.dumps(catalogue))

    def test_legacy_view_reads_current_text_without_becoming_an_active_lens(self):
        self.install_catalogue()
        active,bad=review.lens_files(self.root)
        self.assertEqual(bad,{})
        self.assertEqual(set(active),{'current'})
        views,_=review.lens_files(self.root,include_legacy=True)
        self.assertEqual([review.check_text(c) for c in views['prevention']['checks']],['Inspect the real calculation.']*2)
        p=self.root/'.v4/lenses/current.json';d=json.loads(p.read_text());d['checks'][0]['check']='Inspect the changed contract.';p.write_text(json.dumps(d))
        views,_=review.lens_files(self.root,include_legacy=True)
        self.assertEqual([review.check_text(c) for c in views['prevention']['checks']],['Inspect the changed contract.']*2)

    def test_a_missing_responsibility_cannot_silently_reduce_coverage(self):
        self.install_catalogue()
        p=self.root/'.v4/lenses/current.json';d=json.loads(p.read_text());d['checks'].pop();p.write_text(json.dumps(d))
        active,bad=review.lens_files(self.root)
        self.assertEqual(active,{})
        self.assertTrue(bad)
        with self.assertRaises(ValueError):maintenance.start(self.root)

    def test_reference_cycles_are_refused(self):
        self.install_catalogue()
        p=self.root/'.v4/lenses/current.json';d=json.loads(p.read_text());d['checks'][0]={'id':'old:1','same_as':'old:2'};p.write_text(json.dumps(d))
        active,bad=review.lens_files(self.root)
        self.assertEqual(active,{})
        self.assertIn('cyclic',bad['catalogue'])

    def test_a_moved_check_reuses_the_original_claim_without_editing_it(self):
        c=self.fixture.connect()
        args={'task_id':None,'file':'app.py','symbol':'calculate','note':'The calculation contradicts the stated deduction.'}
        old,created,_=review.raise_finding(c,self.fixture.cfg,lens='prevention',**args)
        before=dict(c.execute('SELECT * FROM claim WHERE id=?',(old,)).fetchone())
        self.install_catalogue()
        actual,created,_=review.raise_finding(c,self.fixture.cfg,lens='current',check_id='old:1',**args)
        self.assertEqual(actual,old)
        self.assertFalse(created)
        self.assertEqual(dict(c.execute('SELECT * FROM claim WHERE id=?',(old,)).fetchone()),before)
        other,created,_=review.raise_finding(c,self.fixture.cfg,lens='current',check_id='old:1',**{**args,'note':'A distinct failure condition at the same symbol.'})
        self.assertNotEqual(other,old)
        self.assertTrue(created)

    def test_adopter_custom_legacy_lens_is_not_overwritten_by_a_view(self):
        self.install_catalogue()
        custom={'name':'My review','source':'adopter','checks':['My additional question'],'anti_patterns':[]}
        (self.root/'.v4/lenses/prevention.json').write_text(json.dumps(custom))
        views,bad=review.lens_files(self.root,include_legacy=True)
        self.assertEqual(bad,{})
        self.assertEqual(views['prevention']['checks'],custom['checks'])

    def test_incompatible_custom_owner_is_reported_before_assets_are_copied(self):
        directory=self.root/'.v4/lenses';directory.mkdir()
        custom=directory/'devx.json'
        custom.write_text(json.dumps({'name':'Adopter DevX','source':'adopter','checks':['Our custom review contract'],'anti_patterns':[]}))
        before={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file() and '.git' not in p.parts}
        with self.assertRaisesRegex(config.ConfigError,'reconciling adopter edits'):
            install.copy_files(fixture_module.SOURCE,self.root,{})
        after={str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file() and '.git' not in p.parts}
        self.assertEqual(after,before)

    def test_refused_cli_host_switch_preserves_configuration_and_custom_assets(self):
        directory=self.root/'.v4/lenses';directory.mkdir()
        (directory/'devx.json').write_text(json.dumps({'name':'Adopter DevX','source':'adopter','checks':['Our custom review contract'],'anti_patterns':[]}))
        for rel,text in (('.claude/settings.json','{"permissions":{"deny":["WebFetch"]}}\n'),
                         ('.codex/config.toml','# Adopter configuration\n')):
            p=self.root/rel;p.parent.mkdir(exist_ok=True);p.write_text(text)
        path=self.root/config.CONFIG
        def files():
            return {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*')
                    if p.is_file() and '.git' not in p.relative_to(self.root).parts}
        for original,target in ((['codex'],'both'),(['claude'],'codex')):
            with self.subTest(original=original,target=target):
                obj=json.loads(path.read_text());obj['agent_hosts']=original;path.write_text(json.dumps(obj))
                before=files()
                result=subprocess.run([str(fixture_module.SOURCE/'bin/v4'),'--repo',str(self.root),
                                       'install','--hosts',target,'--activate-hooks'],
                                      capture_output=True,text=True)
                self.assertEqual(result.returncode,5,result.stdout+result.stderr)
                self.assertIn('reconciling adopter edits',result.stderr)
                self.assertEqual(files(),before)


if __name__=='__main__':unittest.main()
