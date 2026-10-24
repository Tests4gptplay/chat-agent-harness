"""Lineage naming must preserve old stored state and queued wire contracts.

Literal historical keys below are deliberate: using the implementation's key
constants to build these fixtures would hide a breaking protocol rename.
"""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

from harness.wake import make_wake
from local_bridge.scheduler import canonical_worker_owner
from local_bridge.server import Handler, WakeStore
from playwright_host.bridge import BridgeClient
from playwright_host.runtime import BrowserRuntime
from playwright_host.state import HostState
from playwright_host.worker import WorkerManager

ROOT = Path(__file__).resolve().parents[1]


def old_host_state():
    current = {
        'conversation_id': 'worker-current', 'status': 'current',
        'handoff_id': 'pool-existing-worker', 'handoff_verified': True,
        'wake_id': 'wake-existing-worker', 'dispatch_generation': 7,
        'worker_watchdog': {'state': 'ARMED', 'deadline_at': '2099-01-01T00:00:00Z'},
    }
    history = {'conversation_id': 'worker-previous', 'status': 'standby',
               'handoff_id': 'pool-previous-worker'}
    lineage = {'v': 2, 'owner_task_id': 'parent-task', 'owner_control_epoch': 1,
               'current': current, 'managed': [history, current], 'handoff': None}
    return {
        'v': 1, 'client_id': 'playwright-existing-client',
        'task_cell': {'roles': {}, 'planner_successors': {}},
        'lanes': {'lane-00': {
            'lane_id': 'lane-00', 'project_key': 'g-p-existing',
            'project_root_url': 'https://chatgpt.com/g/g-p-existing/project',
            'enabled': True, 'task_pools': {'parent-task::1': lineage},
        }},
        'foreground': {'conversation_url': None},
    }


class WorkerLineageCompatibilityTests(unittest.TestCase):
    def test_existing_host_state_load_save_and_restart_preserve_wire_records(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'state.json'
            original = old_host_state()
            path.write_text(json.dumps(original), encoding='utf-8')
            original_bytes = path.read_bytes()
            state = HostState(path)
            self.assertEqual(path.read_bytes(), original_bytes)
            lineage = state.get_lineage('lane-00', 'parent-task', 1)
            self.assertEqual(lineage, original['lanes']['lane-00']['task_pools']['parent-task::1'])
            self.assertEqual(state.client_id, 'playwright-existing-client')
            state.set_lineage('lane-00', lineage)
            self.assertEqual(json.loads(path.read_text()), original)
            reloaded = HostState(path)
            self.assertEqual(reloaded.get_lineage('lane-00', 'parent-task', 1), lineage)

    def test_new_lineage_and_exact_remove_use_same_stored_collection(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'state.json'
            path.write_text(json.dumps(old_host_state()), encoding='utf-8')
            state = HostState(path)
            first = copy.deepcopy(state.get_lineage('lane-00', 'parent-task', 1))
            second = state.get_lineage('lane-00', 'parent-task', 2, create=True)
            third = state.get_lineage('lane-00', 'other-task', 1, create=True)
            self.assertEqual(second['owner_control_epoch'], 2)
            self.assertEqual(third['owner_task_id'], 'other-task')
            self.assertTrue(state.remove_lineage('lane-00', 'parent-task', 2))
            self.assertEqual(state.get_lineage('lane-00', 'parent-task', 1), first)
            stored = json.loads(path.read_text())['lanes']['lane-00']
            self.assertEqual(set(stored['task_pools']), {'parent-task::1', 'other-task::1'})
            self.assertNotIn('worker_lineages', stored)  # No parallel/empty state copy.

    def test_topology_refresh_preserves_existing_lineage_and_watchdog(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'state.json'
            original = old_host_state()
            path.write_text(json.dumps(original), encoding='utf-8')
            state = HostState(path)
            state.reconcile_lanes({'lanes': [{
                'lane_id': 'lane-00', 'project_key': 'g-p-existing',
                'project_root_url': 'https://chatgpt.com/g/g-p-existing/project',
                'display_name': 'Existing lane', 'enabled': True,
                'task_pools': {'canonical-format-not-host-bindings': {}},
            }]})
            self.assertEqual(state.lane('lane-00')['task_pools'],
                             original['lanes']['lane-00']['task_pools'])

    def test_canonical_legacy_fields_resolve_exact_existing_worker(self):
        canonical = {'lanes': [{
            'lane_id': 'lane-00', 'project_key': 'g-p-existing',
            'task_pools': {'parent-task::1': {
                'owner_task_id': 'parent-task', 'owner_control_epoch': 1,
                'last_pool_takeover_id': 'pool-existing-worker',
                'worker_rollover_request': None,
            }},
        }]}
        before = copy.deepcopy(canonical)
        result = canonical_worker_owner(canonical, lane_id='lane-00',
                                        worker_project_key='g-p-existing',
                                        owner_task_id='parent-task', owner_control_epoch=1)
        self.assertEqual(result, ('pool-existing-worker', False, 'task_pool_owner'))
        self.assertEqual(canonical, before)
        result = canonical_worker_owner(canonical, lane_id='lane-00',
                                        worker_project_key='g-p-existing',
                                        owner_task_id='parent-task', owner_control_epoch=2)
        self.assertEqual(result, ('', False, 'task_pool_missing'))

    def test_queued_wake_and_claim_survive_bridge_recreation_with_same_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wake = make_wake('git-agent-harness', repo='__CAH_REPOSITORY__',
                             wake_id='wake-existing-queue', lane_id='lane-00',
                             worker_project_key='g-p-existing')
            store = WakeStore(root / 'bridge', repo_root=root)
            store.emit(wake)
            claim = store.claim({'client_id': 'client-original', 'lane_id': 'lane-00'})
            pending = (store.claimed / 'wake-existing-queue.json').read_bytes()
            reloaded = WakeStore(root / 'bridge', repo_root=root)
            again = reloaded.claim({'client_id': 'client-original', 'lane_id': 'lane-00'})
            self.assertEqual(again, claim)
            self.assertEqual((reloaded.claimed / 'wake-existing-queue.json').read_bytes(), pending)
            self.assertTrue(reloaded.consume({'client_id': 'client-original',
                                             'message_id': claim['message_id']})['ok'])
            self.assertTrue(reloaded.consume({'client_id': 'client-original',
                                             'message_id': claim['message_id']})['duplicate'])

    def test_worker_ref_prefix_is_not_reissued_by_a_terminology_change(self):
        import uuid
        ref = WorkerManager._handoff_id()
        self.assertTrue(ref.startswith('pool-'))
        self.assertEqual(str(uuid.UUID(ref[5:])), ref[5:])

    def test_legacy_reset_control_routes_through_renamed_host_and_real_http_git(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            origin, work = root / 'origin.git', root / 'work'
            def git(*args):
                return subprocess.check_output(['git', '-C', str(work), *args],
                                               text=True, stderr=subprocess.DEVNULL).strip()
            subprocess.run(['git', 'init', '--bare', str(origin)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['git', 'clone', str(origin), str(work)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            git('config', 'user.name', 'test'); git('config', 'user.email', 'test@example.invalid')
            control = {'kind': 'lane_pool_reset', 'request_id': 'reset-existing-001',
                       'status': 'PENDING', 'control_lane_id': 'lane-00',
                       'worker_project_key': 'g-p-existing'}
            (work / 'state').mkdir()
            (work / 'state/chatgpt.json').write_text(json.dumps({'v': 1, 'control_request': control}), encoding='utf-8')
            git('add', '.'); git('commit', '-m', 'seed old control'); git('push', 'origin', 'HEAD:main')
            store = WakeStore(root / 'bridge', repo_root=work)
            http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            http.store = store
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                state_path = root / 'host.json'
                state_path.write_text(json.dumps(old_host_state()), encoding='utf-8')
                runtime = BrowserRuntime.__new__(BrowserRuntime)
                runtime.state = HostState(state_path)
                runtime.ui = MagicMock()
                runtime.bridge = BridgeClient(f'http://127.0.0.1:{http.server_port}/api',
                                              'client-existing', 'git-agent-harness')
                # Exercise the actual dispatch table, not a new-name-only mocked RPC.
                method_name = runtime.CONTROL_HANDLERS[control['kind']]
                result = getattr(runtime, method_name)(control)
                self.assertTrue(result['ok'], result)
                self.assertEqual(runtime.state.lane('lane-00')['task_pools'], {})
                git('fetch', 'origin', 'main')
                completed = json.loads(git('show', 'FETCH_HEAD:state/chatgpt.json'))['control_request']
                self.assertEqual(completed['status'], 'DONE')
                self.assertEqual(completed['kind'], 'lane_pool_reset')
                self.assertTrue(completed['result']['local_pool_reset'])
                self.assertTrue(getattr(runtime, method_name)(control)['ok'])
                runtime.ui.assert_not_called()
            finally:
                http.shutdown(); http.server_close(); thread.join(timeout=5)

    @unittest.skipUnless(shutil.which('pwsh'), 'PowerShell parser requires pwsh')
    def test_reset_embedded_powershell_parses_without_executing_reset(self):
        lines = (ROOT / 'Reset_CAH_Hot_State.bat').read_text(encoding='utf-8').splitlines()
        blocks = []
        for i, line in enumerate(lines):
            if not line.lower().startswith('powershell.exe ') or '-Command ^' not in line:
                continue
            fragments = []
            for command in lines[i + 1:]:
                part = command.strip()
                more = part.endswith('^')
                part = part[:-1].rstrip() if more else part
                self.assertTrue(part.startswith('"') and part.endswith('"'), part)
                fragments.append(part[1:-1])
                if not more:
                    break
            blocks.append(' '.join(fragments))
        self.assertTrue(blocks)
        with tempfile.TemporaryDirectory() as td:
            payload = Path(td) / 'commands.json'
            payload.write_text(json.dumps(blocks), encoding='utf-8')
            path = str(payload).replace("'", "''")
            script = (
                "$ErrorActionPreference='Stop';"
                f"$blocks=Get-Content -LiteralPath '{path}' -Raw | ConvertFrom-Json;"
                "foreach($block in $blocks){$tokens=$null;$errors=$null;"
                "[System.Management.Automation.Language.Parser]::ParseInput("
                "$block,[ref]$tokens,[ref]$errors) | Out-Null;"
                "if($errors.Count){throw ($errors | Out-String)}}"
            )
            run = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-NonInteractive',
                                  '-Command', script], text=True, capture_output=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_script_entrypoint_imports_the_lineage_compatibility_module(self):
        run = subprocess.run([sys.executable, str(ROOT / 'local_bridge/server.py'), '--help'],
                             cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('--repo-root', run.stdout)


if __name__ == '__main__':
    unittest.main()
