"""Exercise real HTTP/Git retry boundaries rather than mocked error dictionaries."""
import json
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from local_bridge import worker_retry
from local_bridge.server import Handler, WakeStore
from playwright_host.bridge import BridgeClient, BridgeError
from playwright_host.runtime import BrowserRuntime
from playwright_host.state import HostState
from playwright_host.worker import WorkerManager
import test_worker_same_generation_retry as fixtures


from harness.worker_lineage_compat import (
    WORKER_LINEAGES_KEY,
)


class WorkerRetryTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work, self.x = fixtures.WorkerSameGenerationRetryTests()._seed(self.root)
        self.store = WakeStore(self.root / 'spool', repo_root=self.work)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.http.store = self.store
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.bridge = BridgeClient(f'http://127.0.0.1:{self.http.server_port}/api',
                                   'client-worker', 'git-agent-harness')
        self.req = self.control()
        self.req['lane_id'] = self.x['lane']
        self.state = HostState(self.root / 'host.json')
        x = self.x
        self.state.data['lanes'] = {x['lane']: {
            'lane_id': x['lane'], 'project_key': x['project'],
            'project_root_url': f"https://chatgpt.com/g/{x['project']}/project",
            'enabled': True, WORKER_LINEAGES_KEY: {},
        }}
        old = {'conversation_id': x['old_conv'], 'handoff_id': x['worker_ref'],
               'wake_id': x['wake_id'], 'status': 'current', 'handoff_verified': True,
               'worker_watchdog': {
                   'state': 'WORKER_HELPER_REQUESTED', 'child_task_id': x['child'],
                   'backend_cl': self.req['backend_cl'], 'dispatch_id': x['dispatch_id'],
                   'dispatch_generation': 32, 'fence_token': x['fence'], 'wake_id': x['wake_id'],
               }}
        self.state.set_lineage(x['lane'], {'owner_task_id': x['parent'], 'owner_control_epoch': 1,
                                        'current': old, 'managed': [old], 'handoff': None})
        self.runtime = BrowserRuntime.__new__(BrowserRuntime)
        self.runtime.state = self.state
        self.runtime.bridge = self.bridge
        self.runtime.ui = MagicMock()
        self.runtime.ui.confirm_project_conversation_absent.return_value = True
        self.manager = WorkerManager.__new__(WorkerManager)
        self.manager.state = self.state
        self.manager.bridge = self.bridge
        self.manager.ui = self.runtime.ui
        self.manager.project_id = 'git-agent-harness'
        self.runtime.workers = self.manager
        page = MagicMock()
        page.url = f"https://chatgpt.com/g/{x['project']}/c/replacement-worker"
        self.runtime.ui.bootstrap_conversation.return_value = (page, 'replacement-worker', page.url)

    def stop_server(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=5)

    def read(self, path):
        fixtures.git(self.work, 'fetch', 'origin', 'main')
        return json.loads(fixtures.git(self.work, 'show', 'origin/main:' + path))

    def control(self):
        return self.read('state/chatgpt.json')['control_request']

    def put(self, path, value):
        fixtures.git(self.work, 'reset', '--hard', 'origin/main')
        (self.work / path).write_text(json.dumps(value) + '\n', encoding='utf-8')
        fixtures.git(self.work, 'add', path)
        fixtures.git(self.work, 'commit', '-m', 'change isolated fixture')
        fixtures.git(self.work, 'push', 'origin', 'HEAD:main')

    @staticmethod
    def push_failure(original, after_success=False):
        def run(cwd, *args, **kwargs):
            if args and args[0] == 'push':
                if after_success:
                    original(cwd, *args, **kwargs)
                raise subprocess.CalledProcessError(1, ['git', *args])
            return original(cwd, *args, **kwargs)
        return run

    def test_begin_push_failure_does_not_publish_a_runnable_wake(self):
        with patch.object(worker_retry, 'run_git', side_effect=self.push_failure(worker_retry.run_git)):
            with self.assertRaises(BridgeError):
                self.runtime._handle_worker_same_generation_retry(self.control())
        self.assertEqual(self.control()['status'], 'PENDING')
        self.assertIsNone(self.bridge.call('claim', lane_id=self.x['lane']).get('wake'))
        current = self.state.get_lineage(self.x['lane'], self.x['parent'], 1)['current']
        self.assertEqual(current['worker_watchdog']['state'], 'WORKER_HELPER_REQUESTED')
        self.runtime.ui.bootstrap_conversation.assert_not_called()
        self.assertTrue(self.runtime._handle_worker_same_generation_retry(self.control())['ok'])
        self.assertEqual(self.control()['status'], 'APPLYING')
        self.assertIsNotNone(self.bridge.call('claim', lane_id=self.x['lane']).get('wake'))

    def test_lost_begin_push_reply_reenters_existing_applying_control(self):
        with patch.object(worker_retry, 'run_git',
                          side_effect=self.push_failure(worker_retry.run_git, after_success=True)):
            with self.assertRaises(BridgeError):
                self.runtime._handle_worker_same_generation_retry(self.control())
        self.assertEqual(self.control()['status'], 'APPLYING')
        self.assertFalse(list(self.store.inbox.glob('*.json')))
        out = self.runtime._dispatch_control_request(self.control())
        self.assertTrue(out['ok'], out)
        self.assertTrue(self.manager.poll_lane(self.x['lane'])['ok'])
        self.assertEqual(self.control()['status'], 'DONE')
        self.runtime.ui.bootstrap_conversation.assert_called_once()

    def test_failed_local_publication_can_reenter_without_stuck_queued_state(self):
        original = worker_retry.atomic_json
        def fail_inbox(path, value):
            if Path(path).parent == self.store.inbox:
                raise OSError('isolated inbox write failure')
            return original(path, value)
        with patch.object(worker_retry, 'atomic_json', side_effect=fail_inbox):
            with self.assertRaises(BridgeError):
                self.runtime._handle_worker_same_generation_retry(self.control())
        self.assertEqual(self.control()['status'], 'APPLYING')
        self.assertTrue(self.runtime._dispatch_control_request(self.control())['ok'])
        # Repeated control ticks before poll preserve the same queued attempt.
        self.assertTrue(self.runtime._dispatch_control_request(self.control())['ok'])
        self.assertEqual(len(list(self.store.inbox.glob('*.json'))), 1)
        self.assertTrue(self.manager.poll_lane(self.x['lane'])['ok'])
        self.assertEqual(self.control()['status'], 'DONE')
        self.runtime.ui.bootstrap_conversation.assert_called_once()

    def start_with_failed_completion(self):
        self.assertTrue(self.runtime._dispatch_control_request(self.control())['ok'])
        with patch.object(worker_retry, 'run_git', side_effect=self.push_failure(worker_retry.run_git)):
            with self.assertRaises(BridgeError):
                self.manager.poll_lane(self.x['lane'])
        self.assertEqual(self.control()['status'], 'APPLYING')
        self.assertTrue(self.store._path(self.store.inbox, self.x['wake_id']).exists())
        self.assertFalse(self.store._path(self.store.consumed, self.x['wake_id']).exists())
        self.assertEqual(self.state.get_lineage(self.x['lane'], self.x['parent'], 1)['current']
                         ['conversation_id'], 'replacement-worker')

    def test_completed_business_does_not_discard_replacement_receipt(self):
        self.start_with_failed_completion()
        path = self.req['backend_cl']
        backend = self.read(path)
        backend['overall'] = backend['dispatch']['state'] = 'DONE'
        self.put(path, backend)
        result = self.manager.poll_lane(self.x['lane'])
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['deduped_physical_worker'])
        self.assertEqual(self.read(path), backend)
        self.assertEqual(self.control()['status'], 'DONE')
        self.runtime.ui.bootstrap_conversation.assert_called_once()
        self.assertTrue(self.store._path(self.store.consumed, self.x['wake_id']).exists())

    def test_applying_control_reconciles_retained_replacement_after_next_generation(self):
        self.start_with_failed_completion()
        path = self.req['backend_cl']
        backend = self.read(path)
        backend['dispatch'].update(dispatch_id='dispatch-next', generation=33,
                                   fence_token='fence-next', wake_id='wake-next')
        self.put(path, backend)
        lineage = self.state.get_lineage(self.x['lane'], self.x['parent'], 1)
        replacement = {**lineage['current'], 'status': 'standby'}
        newer = {'conversation_id': 'worker-next', 'wake_id': 'wake-next',
                 'handoff_id': 'pool-next', 'status': 'current', 'handoff_verified': True}
        self.state.set_lineage(self.x['lane'], {**lineage, 'current': newer, 'managed': [replacement, newer]})
        result = self.runtime._dispatch_control_request(self.control())
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.control()['status'], 'DONE')
        self.assertEqual(self.read(path), backend)
        self.assertEqual(self.state.get_lineage(self.x['lane'], self.x['parent'], 1)['current'], newer)
        self.runtime.ui.bootstrap_conversation.assert_called_once()

    def test_late_completion_still_requires_the_original_control_identity(self):
        self.assertTrue(self.runtime._dispatch_control_request(self.control())['ok'])
        bad = {**self.req, 'fence_token': 'wrong-fence', 'new_conversation_id': 'replacement-worker'}
        with self.assertRaises(BridgeError):
            self.bridge.call('worker_same_generation_retry_complete', **bad)
        self.assertEqual(self.control()['status'], 'APPLYING')


if __name__ == '__main__':
    unittest.main()
