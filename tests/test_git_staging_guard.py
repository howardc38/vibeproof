"""Staging reads working files; it does not rewrite protected source content."""
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

from kernel.analysis.shell_command import writes_to_protected


class GitStaging(unittest.TestCase):
    def test_explicit_protected_sources_can_be_staged_without_changing_them(self):
        with tempfile.TemporaryDirectory(prefix='v4-stage-') as td:
            root=Path(td);(root/'.v4').mkdir()
            source=root/'.v4/config.json';source.write_text('{"policy":"strict"}\n')
            subprocess.run(['git','init','-q'],cwd=root,check=True)
            before=hashlib.sha256(source.read_bytes()).hexdigest()
            command='git add -- .v4/config.json'
            self.assertEqual(writes_to_protected(command,['.v4/**']),[])
            subprocess.run(['git','add','--','.v4/config.json'],cwd=root,check=True)
            indexed=subprocess.check_output(['git','show',':.v4/config.json'],cwd=root)
            self.assertEqual(indexed,source.read_bytes())
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)

    def test_metadata_override_and_interactive_editor_are_not_classified_as_source_reads(self):
        for cmd in ['git --git-dir=.v4/internal add x.py','git add --edit .v4/config.json','git add --patch .v4/config.json']:
            with self.subTest(command=cmd):
                self.assertIsNone(writes_to_protected(cmd,['.v4/**']))

    def test_staging_does_not_hide_a_real_worktree_write(self):
        for cmd in ['git add .v4/config.json > .v4/config.json','git add .v4/config.json && git restore .v4/config.json']:
            with self.subTest(command=cmd):
                self.assertTrue(writes_to_protected(cmd,['.v4/**']))

    def test_a_repo_can_protect_git_metadata_too(self):
        self.assertTrue(writes_to_protected('git add app.py',['.git/**']))


if __name__=='__main__':unittest.main()
