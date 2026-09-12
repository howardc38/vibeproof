"""File evidence is inside the repo; folded strings contain only known values."""
import json
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from kernel import redgreen
from kernel.analysis import secret_patterns

SOURCE=Path(__file__).resolve().parent.parent

class TracePaths(unittest.TestCase):
    def setUp(self):
        td=tempfile.TemporaryDirectory();self.addCleanup(td.cleanup);self.parent=Path(td.name).resolve()
        self.root=self.parent/'repo';self.root.mkdir();(self.root/'src').mkdir()
        (self.root/'src/calc.py').write_text('def half(x):return x/2\n')
    def run_script(self,body,root=None):
        (self.root/'driver.py').write_text(body)
        code,ran,out=redgreen.executed_files(root or self.root,shlex.join([sys.executable,'-B',str((root or self.root)/'driver.py')]),timeout=20)
        self.assertEqual(code,0,out);return ran
    def test_dot_and_parent_spellings_become_the_actual_repo_key(self):
        (self.root/'tests').mkdir()
        for suffix in ['', '/.', '/tests/..']:
            with self.subTest(suffix=suffix):
                ran=self.run_script('import sys\nsys.path.insert(0,'+repr(str(self.root)+suffix)+')\nfrom src.calc import half\nprint(half(4))\n')
                self.assertIn('src/calc.py',ran)
                self.assertFalse(any('..' in Path(x).parts or x.startswith('./') for x in ran))
    def test_same_prefix_sibling_never_counts_as_an_internal_file(self):
        sibling=self.parent/'repo_shadow';sibling.mkdir();(sibling/'payload.py').write_text("print('outside')\n")
        (self.root/'_shadow').mkdir();(self.root/'_shadow/payload.py').write_text("raise AssertionError('must not run')\n")
        ran=self.run_script('import runpy\nrunpy.run_path('+repr(str(sibling/'payload.py'))+')\n')
        self.assertIn('driver.py',ran);self.assertNotIn('_shadow/payload.py',ran)
    def test_a_root_symlink_does_not_hide_the_executed_files(self):
        alias=self.parent/'alias';alias.symlink_to(self.root,target_is_directory=True)
        ran=self.run_script('import sys\nsys.path.insert(0,'+repr(str(alias))+')\nfrom src.calc import half\nprint(half(4))\n',root=alias)
        self.assertIn('driver.py',ran);self.assertIn('src/calc.py',ran)

    def test_a_symlink_inside_the_repo_cannot_credit_outside_code(self):
        outside=self.parent/'outside.py';outside.write_text("print('outside')\n")
        (self.root/'linked.py').symlink_to(outside)
        ran=self.run_script('import runpy\nrunpy.run_path('+repr(str(self.root/'linked.py'))+')\n')
        self.assertIn('driver.py',ran);self.assertNotIn('linked.py',ran)
        (self.root/'linked.py').unlink();(self.root/'linked.py').symlink_to(self.root/'src/calc.py')
        ran=self.run_script('import runpy\nrunpy.run_path('+repr(str(self.root/'linked.py'))+')\n')
        self.assertIn('linked.py',ran)

class KnownLiteralJoins(unittest.TestCase):
    def setUp(self):
        text=(SOURCE/'tests/fixtures/secret/bypass/key_split_across_concatenation/cfg.py').read_text()
        self.head,self.tail=text.split('"')[1],text.split('"')[3]
    def scan(self,body):return secret_patterns.analyse_source(body,path='cfg.py').findings
    def test_literal_list_tuple_and_separator_preserve_the_real_value(self):
        for seq in ['['+repr(self.head)+','+repr(self.tail)+']','('+repr(self.head)+','+repr(self.tail)+')']:
            self.assertTrue(self.scan('KEY="".join('+seq+')'))
        found=self.scan('KEY="-".join(['+repr(self.head)+','+repr(self.tail)+'])')
        self.assertEqual([f.candidate.text for f in found],[self.head+'-'+self.tail])
    def test_unknown_names_or_f_string_parts_are_not_omitted(self):
        pieces='['+repr(self.head)+','+repr(self.tail)+']'
        for body in ['KEY=f"-{unknown}".join('+pieces+')',
                     'KEY="".join([f"'+self.head+'{unknown}",'+repr(self.tail)+'])',
                     'KEY=f"'+self.head+'{unknown}" + '+repr(self.tail),
                     'KEY="".join([head,tail])']:
            with self.subTest(body_shape=body[:12]):self.assertEqual(list(self.scan(body)),[])
    def test_whole_literal_and_concatenation_remain_findings(self):
        self.assertTrue(self.scan('KEY='+repr(self.head+self.tail)))
        self.assertTrue(self.scan('KEY='+repr(self.head)+'+'+repr(self.tail)))
        self.assertEqual(list(self.scan('KEY='+repr(self.head))),[])
        self.assertEqual(list(self.scan('KEY='+repr(self.tail))),[])

if __name__=='__main__':unittest.main()
