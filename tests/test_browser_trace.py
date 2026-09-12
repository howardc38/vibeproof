"""Browser provenance cannot be replaced by matching a script's filename."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from kernel import browser_trace


class BrowserTrace(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'client.js').write_text('function save() {}\n')
        self.sha = browser_trace.target_sha(self.root, 'client.js')
        self.out = self.root / 'coverage'
        self.out.mkdir()
        self.data = {'schema':1,'run_id':'fresh','repo':str(self.root),'cwd':str(self.root),
                     'browser':'chromium','retries':0,'retry':0,'update_snapshots':'none',
                     'expected_status':'passed','status':'passed',
                     'managed_servers':[{'cwd':str(self.root),'reuse':False}],
                     'scripts':[{'url':'http://127.0.0.1/app.js','source_sha256':self.sha,
                                 'functions':[{'functionName':'save','ranges':[{'count':1}]}]}]}

    def read(self, data=None, symbol='save', target='client.js'):
        (self.out / 'trace.json').write_text(json.dumps(self.data if data is None else data))
        return browser_trace.observed(self.out, self.root, 'fresh', target, symbol, self.sha)

    def test_exact_bytes_connect_a_different_asset_url_and_retain_browser_origin(self):
        executed, calls, detail = self.read()
        self.assertIs(executed, True)
        self.assertEqual(calls, 1)
        self.assertEqual(detail['source'], 'chromium-v8')
        self.assertEqual(detail['matched'][0]['url'], 'http://127.0.0.1/app.js')
        self.assertTrue(detail['valid'])

    def test_loaded_but_uncalled_function_is_distinct_from_unknown(self):
        self.data['scripts'][0]['functions'][0]['ranges'][0]['count'] = 0
        self.assertIs(self.read()[0], False)
        self.assertIsNone(self.read(symbol='missing')[0])

    def test_stale_wrong_bytes_other_root_and_unmanaged_context_do_not_prove(self):
        mutations = [
            lambda d: d.update(run_id='old'),
            lambda d: d.update(repo=str(self.root)+'_other'),
            lambda d: d.update(cwd=str(self.root)+'_other'),
            lambda d: d.update(browser='firefox'),
            lambda d: d.update(retries=1),
            lambda d: d.update(expected_status='failed'),
            lambda d: d.update(status='skipped'),
            lambda d: d.update(update_snapshots='all'),
            lambda d: d.update(managed_servers=[]),
            lambda d: d['managed_servers'][0].update(reuse=True),
            lambda d: d['managed_servers'][0].update(cwd=str(self.root)+'_other'),
            lambda d: d['scripts'][0].update(source_sha256='wrong'),
            lambda d: d['scripts'][0].update(url='about:blank'),
            lambda d: d['scripts'][0]['functions'][0]['ranges'][0].update(count=True),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)):
                data=copy.deepcopy(self.data);mutation(data)
                self.assertIsNone(self.read(data)[0])

    def test_changed_target_or_outside_target_cannot_match(self):
        (self.root / 'client.js').write_text('changed\n')
        self.assertIsNone(self.read()[0])
        self.assertIsNone(self.read(target='../client.js')[0])

    def test_mapped_exact_function_position_and_stale_map_content(self):
        script=self.data['scripts'][0]
        script['source_sha256']='a'*64
        script['source_map']={'sha256':'b'*64,'url':'inline','functions':[
            {'source_sha256':self.sha,'original_line':0,'original_column':9,'count':2}]}
        self.assertEqual(self.read()[:2],(True,2))
        for field,value in [('original_column',0),('source_sha256','stale'),('original_line',True)]:
            data=copy.deepcopy(self.data)
            data['scripts'][0]['source_map']['functions'][0][field]=value
            self.assertIsNone(self.read(data)[0])

    def test_mapped_names_in_comments_other_functions_and_ambiguous_names_are_not_proof(self):
        for source,line,column,name in [('// function save() {}\n',0,12,'save'),
                                       ('function other() {}\n',0,9,'save'),
                                       ('function save() {}\nfunction save() {}\n',0,9,'save')]:
            self.assertIsNone(browser_trace.mapped_function(source,line,column,name))
        source='// 🌈\nexport const save = (value: string): string => value;\n'
        self.assertEqual(browser_trace.mapped_function(source,1,13,'save'),'save')
        self.assertIsNone(browser_trace.mapped_function(source,1,14,'save'))


if __name__ == '__main__':
    unittest.main()
