"""Public setup changes only a fresh private copy, not the executing host."""
import json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
class PublicSetupTests(unittest.TestCase):
    def test_private_configuration_binds_custom_project_slugs(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'instance'
            shutil.copytree(ROOT,root,ignore=shutil.ignore_patterns('.git','__pycache__','dist','showcases','PUBLIC_MANIFEST.json'))
            runner=Path(td)/'runner';runner.mkdir();(runner/'run.cmd').write_text('@echo off')
            subprocess.run(['git','init',str(root)],check=True,capture_output=True)
            subprocess.run(['git','-C',str(root),'remote','add','origin','https://github.com/demo-owner/cah-instance.git'],check=True)
            urls=['https://chatgpt.com/g/g-p-demoCell-control/project','https://chatgpt.com/g/g-p-demoLane0-worker-a/project','https://chatgpt.com/g/g-p-demoLane1-worker-b/project']
            result=subprocess.run([sys.executable,str(root/'tools/configure_install.py'),'--repo','demo-owner/cah-instance','--runner-root',str(runner),'--task-cell',urls[0],'--lane',urls[1],'--lane',urls[2]],text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            state=json.loads((root/'state/chatgpt.json').read_text())
            self.assertEqual(state['repository_trust']['primary_repo'],'demo-owner/cah-instance')
            lanes=json.loads((root/'state/lanes.json').read_text())['lanes']
            self.assertEqual([x['project_root_url'] for x in lanes],urls[1:])
            self.assertTrue((root/'extension/dist/chromium/manifest.json').is_file())
            background=(root/'extension/background.js').read_text()
            self.assertIn("enabled: 'yes' === 'yes'",background)
            self.assertNotIn('g-p-examplelane00',(root/'extension/lane_registry.js').read_text())
            self.assertIn('cah.local.json',(root/'.gitignore').read_text())
