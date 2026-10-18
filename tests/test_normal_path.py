import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from harness.request_scan import git, scan, discover, hydrate, read_receipts
from harness.request_drain import drain
from playwright_host.bridge import BridgeClient, BridgeError
from semantic_runtime_fixture import HarnessCase, PARENT


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
    def test_completed_history_needs_constant_git_processes(self):
        head = self.fixture()
        run = subprocess.run
        with patch('harness.request_drain.latest', return_value=head), patch('harness.request_drain.process_worker_wake_batch', side_effect=lambda repo, records, **kw: {row['request_key']: {'phase': 'DONE'} for row in records}) as process, patch('subprocess.run', wraps=run) as calls:
            out = drain(self.root, kinds=['worker_wake'], max_requests=1)
        self.assertEqual(out['processed'], 1)
        self.assertEqual(out['already_recorded_or_backoff'], 40)
        self.assertEqual(out['payloads_read'], 1)
        self.assertLessEqual(calls.call_count, 5)
        self.assertEqual(process.call_args.args[1][0]['payload']['index'], 40)
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


class RetiredPlannerCompletionTests(unittest.TestCase):
    def case(self):
        case = HarnessCase()
        self.addCleanup(case.close)
        case.tick()
        # Finish the normal bootstrap bookkeeping before measuring idle writes.
        case.tick()
        return case

    def test_retired_chat_syscalls_cannot_complete_or_write_current_turn(self):
        case = self.case()
        role = case.planner()
        output_ref = role['active_output_ref']
        output = case.read(output_ref)
        self.assertTrue(output_ref.startswith(f'state/planner_turns/{PARENT}/'))
        self.assertFalse(output.get('turn_signal'))
        page = case.ui.page_for(role['conversation_id'])
        syscall = {
            'v': 1, 'kind': 'planner_git_cli', 'call_id': 'retired-planner-call',
            'task_id': PARENT, 'control_epoch': 1,
            'planner_generation': role['planner_generation'],
            'planner_fence_token': role['planner_fence_token'],
            'conversation_id': role['conversation_id'], 'output_ref': output_ref,
            'artifact': {**output, 'turn_signal': 'done'},
        }
        texts = [
            'GAH_SYSCALL_BEGIN\n' + json.dumps(syscall) + '\nGAH_SYSCALL_END',
            'GAH_SYSCALL_BEGIN\n' + json.dumps({**syscall, 'kind': 'semantic_sync',
                'role': 'planner', 'request_id': role['active_request_id']}) + '\nGAH_SYSCALL_END',
            'done', 'handoff', 'complete',
        ]
        assistant = MagicMock()
        assistant.count.return_value = 1
        before = case.git('rev-parse', 'origin/main')
        with patch.object(page, 'locator', return_value=assistant):
            for text in texts:
                with self.subTest(text=text):
                    page.answer = [text]
                    page.running = False
                    assistant.nth.return_value.inner_text.return_value = text
                    assistant.last.inner_text.return_value = text
                    case.tick()
                    self.assertEqual(case.children(), [])
                    self.assertEqual(case.read(output_ref), output)
                    self.assertIsNotNone(case.cell()['planner_control']['runtime'].get('active_turn'))
                    self.assertEqual(case.git('rev-parse', 'origin/main'), before)
        self.assertFalse({'planner_git_cli', 'semantic_turn_sync', 'semantic_turn_observe'}
                         & {item['op'] for item in case.ops})

        # The same pre-bound, already-existing output still completes normally
        # when the Planner writes its semantic content and final Git signal.
        case.decide(['lane-00'])
        for _ in range(6):
            case.tick()
            if case.current('lane-00') is not None:
                break
        self.assertEqual(len(case.children()), 1)
        self.assertIsNotNone(case.current('lane-00'))
        self.assertEqual(case.read(output_ref)['turn_signal'], 'done')

    def test_retired_planner_git_cli_bridge_operation_is_unknown(self):
        case = self.case()
        before = case.git('rev-parse', 'origin/main')
        bridge = BridgeClient(case.cfg.bridge_url, case.runtime.state.client_id,
                              case.cfg.project_id)
        with self.assertRaisesRegex(BridgeError, 'planner_git_cli: UNKNOWN_OP'):
            bridge.call('planner_git_cli', task_id=PARENT)
        self.assertEqual(case.git('rev-parse', 'origin/main'), before)


if __name__ == '__main__': unittest.main()
