import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.request_scan import git, scan, discover, hydrate, read_receipts
from harness.request_drain import drain


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
        with patch('harness.request_drain.latest', return_value=head), patch('harness.request_drain.process', return_value={'phase': 'DONE'}) as process, patch('subprocess.run', wraps=run) as calls:
            out = drain(self.root, kinds=['worker_wake'], max_requests=1)
        self.assertEqual(out['processed'], 1)
        self.assertEqual(out['already_recorded_or_backoff'], 40)
        self.assertEqual(out['payloads_read'], 1)
        self.assertLessEqual(calls.call_count, 5)
        self.assertEqual(process.call_args.args[1]['payload']['index'], 40)
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

if __name__ == '__main__': unittest.main()
