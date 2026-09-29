import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.request_scan import git, scan, discover, hydrate, read_receipts
from harness.request_drain import drain
from local_bridge.planner_runtime import planner_git_cli_fallback
import threading


class NormalPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        git(self.root, 'init'); git(self.root, 'config', 'user.name', 'test'); git(self.root, 'config', 'user.email', 'test@example.invalid')
    def put(self, name, value):
        p = self.root / name; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8'))
    def commit(self):
        git(self.root, 'add', '-A'); git(self.root, 'commit', '-m', 'fixture')
        return git(self.root, 'rev-parse', 'HEAD').decode().strip()
    def fixture(self, count=40):
        for i in range(count + 1):
            path = f'requests/worker-wake/{i:04d}.json'
            self.put(path, {'index': i, 'unicode': '中文\\ntext'})
            oid = git(self.root, 'hash-object', path).decode().strip()
            key = hashlib.sha256(('worker_wake\0' + path + '\0' + oid).encode()).hexdigest()
            if i < count:
                self.put('state/request_receipts/' + key + '.json', {'kind': 'worker_wake', 'path': path, 'blob_oid': oid, 'request_key': key, 'phase': 'DONE'})
        return self.commit()
    def test_batch_scan_keeps_json_and_hash_identity(self):
        head = self.fixture(3)
        rs = scan(self.root, after=head, kinds=['worker_wake'])
        self.assertEqual(len(rs), 4)
        for r in rs:
            self.assertTrue(r['valid'])
            self.assertEqual(r['content_sha256'], hashlib.sha256((self.root / r['path']).read_bytes()).hexdigest())
            self.assertEqual(r['payload']['unicode'], '中文\\ntext')
    def test_metadata_discovery_never_reads_request_payloads(self):
        head = self.fixture(3)
        with patch('harness.request_scan.blob_batch', side_effect=AssertionError('payload read')):
            rs = discover(self.root, after=head, kinds=['worker_wake'])
        self.assertEqual(len(rs), 4)
        self.assertTrue(all('payload' not in r for r in rs))
    def test_batch_scan_has_constant_git_process_budget(self):
        # Mock the actual execution boundary, not the retired per-request path.
        # Increasing fixture sizes make prior pending entries completed history.
        for count in (0, 40, 400):
            with self.subTest(completed_history=count):
                head = self.fixture(count)
                run = subprocess.run
                def completed_batch(repo, records, **kwargs):
                    return {record['request_key']: {'phase': 'DONE'} for record in records}
                with patch('harness.request_drain.latest', return_value=head), patch('harness.request_drain.process_worker_wake_batch', side_effect=completed_batch) as batch, patch('harness.request_drain.process', side_effect=AssertionError('Worker requests must use the batch path')), patch('subprocess.run', wraps=run) as calls:
                    out = drain(self.root, kinds=['worker_wake'], max_requests=1)
                self.assertEqual(out['processed'], 1)
                self.assertEqual(out['already_recorded_or_backoff'], count)
                self.assertEqual(out['payloads_read'], 1)
                self.assertLessEqual(calls.call_count, 5)
                batch.assert_called_once()
                selected = batch.call_args.args[1]
                self.assertEqual(len(selected), 1)
                self.assertEqual(selected[0]['payload']['index'], count)
                self.assertEqual(out['results'][0]['phase'], 'DONE')
    def test_corrupt_receipt_does_not_hide_request(self):
        head = self.fixture(1)
        receipt = next((self.root / 'state/request_receipts').glob('*.json'))
        receipt.write_text('{invalid')
        head = self.commit()
        self.assertEqual(read_receipts(self.root, head), {})
    def test_deadline_includes_discovery_and_does_not_start_late_execution(self):
        head = self.fixture(1)
        with patch('harness.request_drain.latest', return_value=head), patch('harness.request_drain.time.monotonic', side_effect=[0, 2, 2]), patch('harness.request_drain.process') as process:
            out = drain(self.root, kinds=['worker_wake'], max_seconds=1)
        process.assert_not_called()
        self.assertEqual(out['payloads_read'], 0)
        self.assertEqual(out['deferred'], 1)
    def test_oversized_payload_not_in_batch(self):
        self.put('requests/worker-wake/large.json', {'body': 'x' * 100})
        head = self.commit()
        with patch('harness.request_scan.blob_batch', wraps=__import__('harness.request_scan', fromlist=['blob_batch']).blob_batch) as batch:
            result = scan(self.root, after=head, kinds=['worker_wake'], max_bytes=50)
        self.assertEqual(batch.call_args.args[1], [])
        self.assertEqual(result[0]['error'], 'REQUEST_TOO_LARGE')


class PlannerGitCliFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'; self.remote = self.root / 'remote.git'
        subprocess.run(['git', 'init', '--bare', str(self.remote)], check=True, stdout=subprocess.PIPE)
        subprocess.run(['git', 'init', str(self.repo)], check=True, stdout=subprocess.PIPE)
        subprocess.run(['git', '-C', str(self.repo), 'config', 'user.name', 'test'], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'config', 'user.email', 'test@example.invalid'], check=True)
        self.task_id = 'planner-cli-test-001'
        self.conversation_id = '11111111-2222-3333-4444-555555555555'
        self.fence = 'planner-fence-cli-test-001'
        self.output_ref = f'evidence/{self.task_id}/roles/planner/takeover.json'
        cell = {
            'v': 1, 'task_id': self.task_id, 'task_cell_id': self.task_id,
            'task_cell_project_key': 'g-p-test', 'control_epoch': 1,
            'planner_control': {
                'enabled': True,
                'authority': {
                    'planner_generation': 1,
                    'planner_fence_token': self.fence,
                    'conversation_id': self.conversation_id,
                },
                'successor': {'state': 'NONE'},
                'runtime': {'pending_output': {
                    'kind': 'FOREGROUND_TAKEOVER_ACK',
                    'ref': self.output_ref,
                    'conversation_id': self.conversation_id,
                    'status': 'WAITING',
                }},
            },
        }
        path = self.repo / 'state' / 'task_cells' / f'{self.task_id}.json'
        path.parent.mkdir(parents=True); path.write_text(json.dumps(cell, indent=2) + '\n', encoding='utf-8')
        subprocess.run(['git', '-C', str(self.repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'commit', '-m', 'seed'], check=True, stdout=subprocess.PIPE)
        subprocess.run(['git', '-C', str(self.repo), 'branch', '-M', 'main'], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'remote', 'add', 'origin', str(self.remote)], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'push', '-u', 'origin', 'main'], check=True, stdout=subprocess.PIPE)
        runtime = self.root / 'runtime'; runtime.mkdir()
        class Store:
            pass
        self.store = Store()
        self.store.repo_root = self.repo
        self.store.runtime = runtime
        self.store.git_remote = 'origin'
        self.store.git_branch = 'main'
        self.store.git_lock = threading.RLock()
        def run_git(*args):
            return subprocess.run(
                ['git', '-C', str(self.repo), *args], check=True,
                text=True, encoding='utf-8', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        self.store._git = run_git

    def request(self, **overrides):
        value = {
            'task_id': self.task_id,
            'control_epoch': 1,
            'planner_generation': 1,
            'planner_fence_token': self.fence,
            'conversation_id': self.conversation_id,
            'observed_conversation_id': self.conversation_id,
            'output_ref': self.output_ref,
            'artifact': {'v': 1, 'task_id': self.task_id, 'status': 'TAKEOVER_ACK'},
        }
        value.update(overrides)
        return value

    def remote_show(self, ref):
        return subprocess.run(
            ['git', '--git-dir', str(self.remote), 'show', f'main:{ref}'],
            text=True, encoding='utf-8', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def test_exact_pending_output_uses_host_git_cli(self):
        result = planner_git_cli_fallback(self.store, self.request())
        self.assertTrue(result['ok']); self.assertFalse(result['duplicate'])
        self.assertEqual(json.loads(self.remote_show(self.output_ref).stdout)['status'], 'TAKEOVER_ACK')
        duplicate = planner_git_cli_fallback(self.store, self.request())
        self.assertTrue(duplicate['ok']); self.assertTrue(duplicate['duplicate'])

    def test_stale_identity_and_wrong_path_do_not_mutate(self):
        for overrides in (
            {'planner_generation': 2},
            {'planner_fence_token': 'planner-fence-stale'},
            {'observed_conversation_id': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'},
            {'output_ref': f'evidence/{self.task_id}/roles/planner/wrong.json'},
        ):
            result = planner_git_cli_fallback(self.store, self.request(**overrides))
            self.assertFalse(result['ok'])
        self.assertNotEqual(self.remote_show(self.output_ref).returncode, 0)


if __name__ == '__main__': unittest.main()
