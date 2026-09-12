"""Import spelling does not hide changed arity or missing local names."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from kernel.analysis import signature_change as signature, dangling_ref

BEFORE='def send(to): return to\n'
AFTER='def send(to, subject): return to, subject\n'

class Imports(unittest.TestCase):
    def setUp(self):
        td=tempfile.TemporaryDirectory();self.addCleanup(td.cleanup);self.root=Path(td.name)
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        self.put('.v4/config.json',json.dumps({'test_command':'true','policy':'allow_accepted_risk'}))
        self.put('pkg/__init__.py','');self.put('pkg/mail.py',AFTER)
    def put(self,name,text):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
        subprocess.run(['git','-C',str(self.root),'add','--',name],check=True,capture_output=True)
    def arity(self):return signature.scan(self.root,lambda n: BEFORE if n=='pkg/mail.py' else None,['pkg/mail.py'])
    def test_relative_and_absolute_module_and_symbol_callers(self):
        forms=[('from .mail import send',"send('x')"),('from .mail import send as s',"s('x')"),
               ('from . import mail',"mail.send('x')"),('from . import mail as m',"m.send('x')"),
               ('from pkg import mail as m',"m.send('x')"),('import pkg.mail',"pkg.mail.send('x')"),
               ('import pkg.mail as m',"m.send('x')")]
        for i,(imp,call) in enumerate(forms):self.put(f'pkg/c{i}.py',imp+'\ndef go():return '+call+'\n')
        self.put('pkg/good.py',"from .mail import send\ndef go():return send('x','ok')\n")
        self.assertEqual({x[0] for x in self.arity()},{f'pkg/c{i}.py' for i in range(len(forms))})
    def test_parent_relative_and_namespace_package(self):
        self.put('pkg/nested/caller.py',"from ..mail import send\ndef go():return send('x')\n")
        self.assertEqual({x[0] for x in self.arity()},{'pkg/nested/caller.py'})
        (self.root/'pkg/__init__.py').unlink();subprocess.run(['git','-C',str(self.root),'add','-A'],check=True)
        self.assertEqual({x[0] for x in self.arity()},{'pkg/nested/caller.py'})
    def test_missing_names_and_existing_submodules_are_distinguished(self):
        self.put('pkg/bad.py','from .mail import missing\n')
        self.put('pkg/bad_child.py','from . import missing_child\n')
        self.put('pkg/good.py','from . import mail\nfrom .mail import send\n')
        self.put('pkg/namespace/member.py','ok=1\n')
        self.put('pkg/nested.py','from . import namespace\nfrom .namespace import member\n')
        got=dangling_ref.scan(self.root)
        self.assertEqual({(r[0],r[3]) for r in got},{('pkg/bad.py','missing'),('pkg/bad_child.py','missing_child')})
    def test_namespace_parent_still_detects_missing_child(self):
        (self.root/'pkg/__init__.py').unlink();subprocess.run(['git','-C',str(self.root),'add','-A'],check=True)
        self.put('pkg/bad.py','from . import absent\n');self.put('pkg/good.py','from . import mail\n')
        self.assertEqual([(r[0],r[3]) for r in dangling_ref.scan(self.root)],[('pkg/bad.py','absent')])
    def test_destructured_exports_are_real_bindings_but_subscript_names_are_not(self):
        self.put('pkg/values.py', "PERSON, (AGENT, *REST) = 'person', ['agent', 'monitor']\ntable = {}\ntable[len] = 1\n")
        self.put('pkg/good.py', 'from .values import PERSON, AGENT, REST\n')
        self.put('pkg/bad.py', 'from .values import len\n')
        self.assertEqual({(r[0],r[3]) for r in dangling_ref.scan(self.root)}, {('pkg/bad.py','len')})

    def test_root_relative_unknown_and_dynamic_exports_are_not_guessed(self):
        self.put('root.py','from .mail import absent\n')
        self.put('pkg/dynamic.py','def __getattr__(name): return object()\n')
        self.put('pkg/user.py','from .dynamic import dynamic_name\n')
        self.assertEqual(dangling_ref.scan(self.root),[])
    def test_explicit_package_attribute_does_not_become_its_same_named_file(self):
        self.put('pkg/__init__.py','class Facade:\n def send(self,to):return to\nmail=Facade()\n')
        self.put('pkg/caller.py',"from . import mail\ndef go():return mail.send('x')\n")
        self.assertEqual(self.arity(),[])
        self.assertEqual(dangling_ref.scan(self.root),[])

    def test_relative_fixtures_keep_their_package_root_after_installation(self):
        from kernel import register
        source=Path(__file__).resolve().parents[1]
        fixtures=self.root/'.v4/fixtures/dangling_ref'
        shutil.copytree(source/'tests/fixtures/dangling_ref',fixtures)
        ok, report=register.verify_checker(repo_root=self.root,
            checker_path=source/'checkers/dangling_ref.py', fixtures_dir=fixtures,
            kind='dangling-ref')
        self.assertTrue(ok, report)
        cases={(x['colour'],x['case']) for x in report['cases']}
        self.assertIn(('red','relative_deleted_name'),cases)
        self.assertIn(('bypass','relative_aliased_missing_child'),cases)

if __name__=='__main__':unittest.main()
