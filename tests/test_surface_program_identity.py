"""Installed proof code must invalidate old evidence, not disappear as bookkeeping."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from kernel import hashing

ROOT=Path(__file__).resolve().parents[1]


class SurfaceProgramIdentity(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'.v4/surface').mkdir(parents=True)
        self.install('playwright.cjs','module.exports = {version: 1};\n')
        (self.root/'app.py').write_text('value = 1\n')
        for cmd in [['git','init','-q'],['git','add','.'],['git','-c','user.name=Proof test','-c','user.email=test@localhost','commit','-qm','fixture']]:
            subprocess.run(cmd,cwd=self.root,check=True,capture_output=True)

    def install(self,name,code):
        path=self.root/'.v4/surface'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(code)
        manifest=self.root/'.v4/installed.json'
        rows=json.loads(manifest.read_text()) if manifest.exists() else {}
        rows[str(path.relative_to(self.root))]=hashing.file_sha(path)
        manifest.write_text(json.dumps(rows))

    def program(self,entry):
        return hashing.program_sha(self.root,ROOT/'checkers'/entry,framework_root=ROOT)

    def test_installed_replacement_changes_both_proof_programs_while_product_tree_stays_same(self):
        before={p:self.program(p) for p in ['surface_proof.py','review_finding.py']}
        tree=hashing.worktree_digest(self.root)
        self.install('playwright.cjs','module.exports = {version: 2};\n')
        self.assertEqual(tree,hashing.worktree_digest(self.root))
        for p,digest in before.items():self.assertNotEqual(digest,self.program(p),p)

    def test_added_or_removed_runtime_adapter_changes_program(self):
        before=self.program('surface_proof.py')
        self.install('helpers/another.mjs','export const observation = 1;\n')
        added=self.program('surface_proof.py');self.assertNotEqual(before,added)
        (self.root/'.v4/surface/helpers/another.mjs').unlink()
        self.assertEqual(before,self.program('surface_proof.py'))

    def test_documentation_and_types_do_not_change_executed_program(self):
        before=self.program('surface_proof.py')
        self.install('INTEGRATION.md','Updated explanation\n')
        self.install('playwright.d.cts','export declare const value: number;\n')
        self.assertEqual(before,self.program('surface_proof.py'))

    def test_an_unrelated_checker_does_not_gain_surface_dependencies(self):
        before=self.program('design_pins.py')
        self.install('playwright.cjs','module.exports = {version: 3};\n')
        self.assertEqual(before,self.program('design_pins.py'))
