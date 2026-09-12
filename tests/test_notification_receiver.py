"""Service ownership and launcher contract; Telegram delivery is tested separately."""
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from kernel import notification_receiver as receiver


class Receiver(unittest.TestCase):
    def setUp(self):
        td=tempfile.TemporaryDirectory(prefix="v4-receiver-");self.addCleanup(td.cleanup)
        self.home=Path(td.name).resolve();self.root=self.home/'adopter';self.root.mkdir()
        (self.root/'bin').mkdir();self.launcher=self.root/'bin/v4'
        self.launcher.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n');self.launcher.chmod(0o755)
        self.storage=self.home/'transport';self.storage.mkdir()
        self.cfg={"bot":"123", "storage":str(self.storage)}

    def test_definition_runs_the_real_shell_launcher_and_contains_no_credential(self):
        spec=receiver.definition(self.root,self.cfg)
        result=subprocess.run(spec['ProgramArguments'],env=spec['EnvironmentVariables'],text=True,capture_output=True,check=True)
        self.assertEqual(result.stdout.splitlines(),['--repo',str(self.root),'maintain','listen'])
        self.assertEqual(plistlib.loads(plistlib.dumps(spec)),spec)
        self.assertNotIn('token',json.dumps(spec).lower())

    def test_modified_service_is_not_replaced(self):
        label='org.vibeproof.telegram.123'
        path=self.home/'Library/LaunchAgents'/(label+'.plist');path.parent.mkdir(parents=True)
        path.write_text('user-owned service')
        with patch.object(receiver.Path,'home',return_value=self.home), patch.object(receiver,'observe',return_value={'loaded':False}):
            with self.assertRaisesRegex(ValueError,'not owned'):
                receiver._manage(self.root,'start',self.cfg)
        self.assertEqual(path.read_text(),'user-owned service')

    def test_launchctl_environment_is_not_returned(self):
        result=subprocess.CompletedProcess([],0,'state = running\npid = 42\nenvironment = { TOKEN = secret-value }\nlast exit code = 0\n','')
        with patch.object(receiver.subprocess,'run',return_value=result):
            observed=receiver.observe('org.vibeproof.telegram.123')
        self.assertEqual(observed['state']['pid'],'42')
        self.assertNotIn('secret-value',json.dumps(observed))


if __name__=='__main__':unittest.main()
