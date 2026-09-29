"""Offline clean-template tests. Never register runners, use accounts or launch CAH."""
from __future__ import annotations
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

@unittest.skipIf((ROOT/'installation/configured.local.json').exists(), 'Template-only tests: use the unchanged clean template')
class InstallationTemplateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='cah-template-test-')
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.out=self.base/'operational'
        self.spec=json.loads((ROOT/'installation/placeholders.json').read_text(encoding='utf-8'))
        values={key:str(self.base/key.lower()) for key in self.spec['tokens']}
        values.update(REPO_ROOT=str(self.out),REPOSITORY='example-owner/private-cah',RECORD_REPOSITORY='',TRANSFER_REPOSITORY='',TASK_CELL_KEY='g-p-SYNTHETICTASKCELL',LANE00_KEY='g-p-SYNTHETICLANE00',LANE01_KEY='',FOREGROUND_URL='https://chatgpt.com/c/synthetic-foreground')
        self.config={'operational_repository_is_private':True,'projects_verified_in_authenticated_browser':True,'enabled_lanes':['lane-00'],'values':values}

    def run_config(self,config=None):
        source=self.base/'input.local.json'
        source.write_text(json.dumps(self.config if config is None else config),encoding='utf-8')
        return subprocess.run([sys.executable,'-B',str(ROOT/'installation/configure.py'),'--config',str(source),'--output',str(self.out)],capture_output=True,text=True,timeout=60,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})

    def test_untouched_template_fails_preflight(self):
        result=subprocess.run([sys.executable,'-B',str(ROOT/'installation/preflight.py')],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        self.assertIn('CAH_NOT_CONFIGURED',result.stderr)
        self.assertFalse((ROOT/'installation/configured.local.json').exists())

    def test_example_without_ownership_verification_is_rejected(self):
        example=json.loads((ROOT/'installation/config.example.json').read_text(encoding='utf-8'))
        result=self.run_config(example)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.out.exists())

    def test_single_lane_copy_has_no_phantom_project_or_old_state(self):
        result=self.run_config()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        topology=json.loads((self.out/'state/lanes.json').read_text(encoding='utf-8'))
        self.assertEqual(topology['registered_count'],1)
        self.assertEqual(topology['enabled_count'],1)
        self.assertEqual([x['lane_id'] for x in topology['lanes']],['lane-00'])
        self.assertEqual(topology['lanes'][0]['task_pools'],{})
        self.assertEqual(topology['lanes'][0]['project_root_url'],'https://chatgpt.com/g/g-p-SYNTHETICLANE00/project')
        state=json.loads((self.out/'state/chatgpt.json').read_text(encoding='utf-8'))
        self.assertEqual(state['phase'],'IDLE')
        self.assertIsNone(state['active_task'])
        self.assertIsNone(state['control_request'])
        self.assertFalse((self.out/'.git').exists())
        check=subprocess.run([sys.executable,'-B',str(self.out/'installation/preflight.py')],capture_output=True,text=True,timeout=20)
        self.assertEqual(check.returncode,0,check.stdout+check.stderr)
        for rel in self.spec['files']:
            text=(self.out/rel).read_text(encoding='utf-8')
            for token in self.spec['tokens'].values():self.assertNotIn(token,text,rel)
        for path in self.out.rglob('*.py'):
            ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
        check=subprocess.run([sys.executable,'-B','-c','from playwright_host.config import BOOTSTRAP_LANES,TASK_CELL; import json; print(json.dumps([len(BOOTSTRAP_LANES),TASK_CELL["project_key"]]))'],cwd=self.out,capture_output=True,text=True,timeout=20)
        self.assertEqual(check.returncode,0,check.stderr)
        self.assertEqual(json.loads(check.stdout),[1,'g-p-SYNTHETICTASKCELL'])

    def test_two_lane_copy_has_consistent_topology(self):
        self.config['values']['LANE01_KEY']='g-p-SYNTHETICLANE01'
        self.config['enabled_lanes']=['lane-00','lane-01']
        result=self.run_config()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        topology=json.loads((self.out/'state/lanes.json').read_text(encoding='utf-8'))
        self.assertEqual((topology['registered_count'],topology['enabled_count']),(2,2))
        self.assertTrue(all(x['enabled'] and x['status']=='IDLE' for x in topology['lanes']))

    def test_duplicate_project_binding_is_rejected(self):
        self.config['values']['LANE00_KEY']=self.config['values']['TASK_CELL_KEY']
        result=self.run_config()
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.out.exists())

    def test_enabled_unconfigured_second_lane_is_rejected(self):
        self.config['enabled_lanes']=['lane-00','lane-01']
        result=self.run_config()
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.out.exists())

    def test_existing_output_is_preserved(self):
        self.out.mkdir()
        sentinel=self.out/'user-data.txt'
        sentinel.write_text('KEEP',encoding='utf-8')
        result=self.run_config()
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(sentinel.read_text(encoding='utf-8'),'KEEP')
        self.assertEqual(list(self.out.iterdir()),[sentinel])

    def test_unresolved_and_shell_metacharacter_paths_are_rejected(self):
        for bad in (self.spec['tokens']['BRIDGE_ROOT'],str(self.base/'quoted\"path'),str(self.base/'unsafe{expression}'),str(self.base/'shell&path')):
            with self.subTest(value=bad):
                self.config['values']['BRIDGE_ROOT']=bad
                result=self.run_config()
                self.assertNotEqual(result.returncode,0)
                self.assertFalse(self.out.exists())

    def test_repo_root_must_match_destination(self):
        self.config['values']['REPO_ROOT']=str(self.base/'different')
        result=self.run_config()
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.out.exists())

    def test_runtime_data_inside_checkout_is_rejected(self):
        self.config['values']['PROFILE_ROOT']=str(self.out/'browser-profile')
        result=self.run_config()
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.out.exists())

    def test_runner_roots_must_be_distinct(self):
        self.config['values']['SHOT_RUNNER_ROOT']=self.config['values']['MANAGED_RUNNER_ROOT']
        result=self.run_config()
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(self.out.exists())

    def test_manifest_and_excluded_payloads(self):
        result=subprocess.run([sys.executable,'-B',str(ROOT/'installation/audit.py')],capture_output=True,text=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue(json.loads(result.stdout)['ok'])
        for rel in ('.github/workflows','toolbox','showcases','imports','memory','tasks','evidence','results','requests','cl','actions','cases'):
            self.assertFalse((ROOT/rel).exists(),rel)

    def test_launcher_and_reset_preflight_precedes_git_mutation(self):
        text=(ROOT/'Start_CAH.ps1').read_text(encoding='utf-8')
        self.assertLess(text.index('installation\\preflight.py'),text.index('& git -C'))
        text=(ROOT/'Reset_CAH_Hot_State.bat').read_text(encoding='utf-8')
        self.assertLess(text.index('installation\\preflight.py'),text.index('git fetch'))

if __name__=='__main__':unittest.main()
