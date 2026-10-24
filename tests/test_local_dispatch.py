"""Local recovery exercises real Git trees and the existing persistent wake queue."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.git_process import run_git
from harness.request_drain import latest, receipt_path
from harness.request_scan import discover, hydrate, scan
from local_bridge.local_dispatch import reconcile_local_dispatch
from local_bridge.server import WakeStore
import harness.request_scan as request_scan
import test_parallel_branch_finalize as parallel_fixtures
import test_semantic_finalize as semantic_fixtures


class LocalDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.remote, self.repo = self.base / 'remote.git', self.base / 'writer'
        run_git(self.base, 'init', '--bare', str(self.remote))
        run_git(self.base, 'clone', str(self.remote), str(self.repo))
        run_git(self.repo, 'config', 'user.name', 'test')
        run_git(self.repo, 'config', 'user.email', 'test@example.invalid')
        run_git(self.repo, 'checkout', '-b', 'main')
        self.put('state/lanes.json', {'lanes': [{'lane_id': 'lane-00', 'project_key': 'g-p-test', 'enabled': True}]})
        self.publish()
        self.env = patch.dict(os.environ, CAH_LOCAL_WORKER_LANES='lane-00')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.store = self.new_store()

    def new_store(self, name='bridge'):
        return WakeStore(self.base / name, self.repo, git_branch='main')

    def put(self, ref, value):
        path = self.repo / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')

    def publish(self):
        run_git(self.repo, 'add', '-A')
        run_git(self.repo, 'commit', '--allow-empty', '-m', 'test')
        run_git(self.repo, 'push', 'origin', 'HEAD:main')
        return run_git(self.repo, 'rev-parse', 'HEAD').stdout.strip()

    def wake(self, key='a', *, publish=True):
        task, dispatch, fence, wake = 't-' + key, 'dispatch-' + key, 'fence-' + key + '-long', 'wake-' + key + '-long'
        owner = 'owner-' + key
        self.put('tasks/' + task + '.json', {'task_id': task, 'backend_cl': 'cl/' + task + '.json',
                 'lane_id': 'lane-00', 'worker_project_key': 'g-p-test', 'owner_task_id': owner, 'owner_control_epoch': 2})
        self.put('cl/' + task + '.json', {'task_id': task, 'dispatch': {'dispatch_id': dispatch,
                 'generation': 1, 'fence_token': fence, 'wake_id': wake, 'state': 'READY'},
                 'scheduling': {'owner_task_id': owner, 'owner_control_epoch': 2}})
        self.put('requests/worker-wake/' + key + '.json', {'task_id': task, 'backend_cl': 'cl/' + task + '.json',
                 'dispatch_id': dispatch, 'dispatch_generation': 1, 'fence_token': fence, 'wake_id': wake,
                 'lane_id': 'lane-00', 'worker_project_key': 'g-p-test', 'owner_task_id': owner,
                 'owner_control_epoch': 2, 'state': 'NEED_AGENT', 'result_ref': 'tasks/' + task + '.json'})
        return self.publish() if publish else None

    def receipt(self, record, phase, **extra):
        value = {key: record[key] for key in ('v', 'kind', 'path', 'source_commit', 'blob_oid', 'request_key')}
        self.put(receipt_path(record), {**value, 'phase': phase, **extra})

    def queue_ids(self, store=None):
        return {p.stem for p in (store or self.store).inbox.glob('*.json')}

    def test_startup_recovers_missed_wake_and_restart_reuses_persistent_id(self):
        sha = self.wake()
        first = reconcile_local_dispatch(self.store, sha)
        self.assertEqual(first['results'][0]['phase'], 'DONE')
        self.assertEqual(self.queue_ids(), {'wake-a-long'})
        restart = self.new_store()
        again = reconcile_local_dispatch(restart, sha)
        self.assertTrue(again['results'][0]['duplicate'])
        self.assertEqual(self.queue_ids(restart), {'wake-a-long'})
        # Discovery memory and even the queue can be rebuilt from canonical Git.
        fresh = self.new_store('other-bridge')
        self.assertEqual(reconcile_local_dispatch(fresh, sha)['processed'], 1)
        self.assertEqual(self.queue_ids(fresh), {'wake-a-long'})

    def test_same_sha_retries_delivery_failure_without_replaying_successful_neighbor(self):
        self.wake('a', publish=False)
        sha = self.wake('b')
        emit = self.store.emit
        failed = False
        def flaky(wake):
            nonlocal failed
            if wake['wake_id'] == 'wake-a-long' and not failed:
                failed = True
                raise TimeoutError('lost queue reply')
            return emit(wake)
        with patch.object(self.store, 'emit', side_effect=flaky) as calls:
            first = reconcile_local_dispatch(self.store, sha, now_fn=lambda: 1000)
            self.assertEqual([r['phase'] for r in first['results']], ['RETRY', 'DONE'])
            second = reconcile_local_dispatch(self.store, sha, now_fn=lambda: 1002)
        self.assertEqual(second['discovered'], 0)
        self.assertEqual(second['results'][0]['phase'], 'DONE')
        self.assertEqual(calls.call_count, 3)
        self.assertEqual(self.queue_ids(), {'wake-a-long', 'wake-b-long'})

    def test_incremental_scan_covers_multiple_commits(self):
        before = self.wake('a')
        reconcile_local_dispatch(self.store, before, now_fn=lambda: 1000)
        self.wake('b')
        after = self.wake('c')
        with patch('local_bridge.local_dispatch.discover', wraps=discover) as discovery:
            out = reconcile_local_dispatch(self.store, after, now_fn=lambda: 1001)
        self.assertEqual(discovery.call_args.kwargs['before'], before)
        self.assertEqual(discovery.call_args.kwargs['after'], after)
        self.assertEqual(out['processed'], 2)
        self.assertEqual(self.queue_ids(), {'wake-a-long', 'wake-b-long', 'wake-c-long'})

    def test_bad_neighbor_old_fence_and_hold_do_not_starve_good_request(self):
        for key in ('b-old', 'c-held', 'd-good'):
            self.wake(key, publish=False)
        path = self.repo / 'requests/worker-wake/a-bad.json'
        path.write_text('invalid json')
        old = json.loads((self.repo / 'requests/worker-wake/b-old.json').read_text())
        self.put('requests/worker-wake/b-old.json', {**old, 'fence_token': 'stale-fence'})
        sha = self.publish()
        held = next(r for r in scan(self.repo, after=sha, kinds=['worker_wake']) if 'c-held' in r['path'])
        self.receipt(held, 'NEEDS_RECONCILE', retry_after=0)
        sha = self.publish()
        out = reconcile_local_dispatch(self.store, sha, now_fn=lambda: 1000)
        self.assertEqual([r['phase'] for r in out['results']], ['REJECTED', 'SUPERSEDED', 'DONE'])
        self.assertEqual(self.queue_ids(), {'wake-d-good-long'})
        # HOLD survives unchanged-SHA recovery scans and elapsed retry_after.
        reconcile_local_dispatch(self.store, sha, now_fn=lambda: 2000)
        self.assertNotIn('wake-c-held-long', self.queue_ids())
        self.receipt(held, 'UNKNOWN', retry_after=0)
        released = self.publish()
        out = reconcile_local_dispatch(self.store, released, now_fn=lambda: 2001)
        self.assertIn('wake-c-held-long', self.queue_ids())
        self.assertIn('DONE', [r['phase'] for r in out['results']])

    def test_matching_receipts_and_retry_after_are_respected(self):
        for key in ('a-done', 'b-rejected', 'c-superseded', 'd-claimed', 'e-unknown'):
            self.wake(key, publish=False)
        sha = self.publish()
        records = scan(self.repo, after=sha, kinds=['worker_wake'])
        for record, phase in zip(records, ('DONE', 'REJECTED', 'SUPERSEDED', 'CLAIMED', 'UNKNOWN')):
            self.receipt(record, phase, retry_after=1002 if phase == 'UNKNOWN' else 0)
        sha = self.publish()
        first = reconcile_local_dispatch(self.store, sha, now_fn=lambda: 1000)
        self.assertEqual(first['processed'], 1)
        self.assertEqual(self.queue_ids(), {'wake-d-claimed-long'})
        reconcile_local_dispatch(self.store, sha, now_fn=lambda: 1003)
        self.assertEqual(self.queue_ids(), {'wake-d-claimed-long', 'wake-e-unknown-long'})

    def test_current_snapshot_invalidates_cached_request_and_dispatch(self):
        sha = self.wake()
        with patch.object(self.store, 'emit', side_effect=TimeoutError('retry')):
            reconcile_local_dispatch(self.store, sha, now_fn=lambda: 1000)
        bg = json.loads((self.repo / 'cl/t-a.json').read_text())
        bg['dispatch']['fence_token'] = 'new-canonical-fence'
        self.put('cl/t-a.json', bg)
        newer = self.publish()
        result = reconcile_local_dispatch(self.store, newer, now_fn=lambda: 1002)
        self.assertEqual(result['results'][0]['phase'], 'SUPERSEDED')
        self.assertFalse(self.queue_ids())

    def test_nonlocal_lane_waits_and_configuration_change_recovers_same_sha(self):
        sha = self.wake()
        with patch.dict(os.environ, CAH_LOCAL_WORKER_LANES='lane-01'):
            first = reconcile_local_dispatch(self.store, sha, now_fn=lambda: 1000)
        self.assertEqual(first['results'][0]['phase'], 'DEFERRED')
        self.assertFalse(self.queue_ids())
        second = reconcile_local_dispatch(self.store, sha, now_fn=lambda: 1002)
        self.assertEqual(second['results'][0]['phase'], 'DONE')

    def test_held_requests_cannot_fill_runnable_cache_and_hide_later_work(self):
        for key in ('a-held', 'b-held', 'c-held', 'z-good'):
            self.wake(key, publish=False)
        sha = self.publish()
        for record in scan(self.repo, after=sha, kinds=['worker_wake']):
            if 'held' in record['path']:
                self.receipt(record, 'NEEDS_RECONCILE', retry_after=0)
        sha = self.publish()
        with patch('local_bridge.local_dispatch.MAX_PENDING', 2):
            out = reconcile_local_dispatch(self.store, sha)
        self.assertEqual(self.queue_ids(), {'wake-z-good-long'})
        self.assertFalse(out['scan_pending'])
        self.assertEqual(out['pending'], 0)

    def test_permanently_failing_neighbors_cannot_block_new_metadata_pages(self):
        for key in ('a-bad', 'b-bad'):
            self.put('requests/worker-wake/' + key + '.json', {'task_id': 'missing-' + key})
        sha = self.wake('z-good')
        with patch('local_bridge.local_dispatch.MAX_PENDING', 2):
            first = reconcile_local_dispatch(self.store, sha, now_fn=lambda: 1000)
            second = reconcile_local_dispatch(self.store, sha, now_fn=lambda: 1002)
        self.assertTrue(first['scan_pending'])
        self.assertEqual([r['phase'] for r in first['results']], ['RETRY', 'RETRY'])
        self.assertFalse(second['scan_pending'])
        self.assertEqual(self.queue_ids(), {'wake-z-good-long'})
        self.assertLessEqual(second['pending'], 2)
        # Eviction only discards metadata; full recovery still rediscovers the
        # broken request after its canonical Task is repaired.
        self.wake('a-bad', publish=False)
        repaired = self.publish()
        reconcile_local_dispatch(self.store, repaired, now_fn=lambda: 1004)
        self.assertIn('wake-a-bad-long', self.queue_ids())

    def test_claimed_receipt_replays_exact_original_wake(self):
        sha = self.wake()
        record = scan(self.repo, after=sha, kinds=['worker_wake'])[0]
        from harness.request_drain import wake_decision
        _, wake = wake_decision(self.repo, record['payload'])
        wake['created_at'] = '2020-01-01T00:00:00Z'
        self.receipt(record, 'UNKNOWN', wake=wake, retry_after=0)
        sha = self.publish()
        reconcile_local_dispatch(self.store, sha)
        emitted = json.loads((self.store.inbox / (wake['wake_id'] + '.json')).read_text())
        self.assertEqual(emitted, wake)

    def test_current_owner_cell_epoch_and_inactive_states_gate_recovery(self):
        for index, status in enumerate(('PAUSED', 'WAIT_USER', 'CLEANING', 'DONE', 'STOPPED', 'CANCELLED')):
            key = str(index)
            self.wake(key, publish=False)
            self.put(f'state/task_cells/owner-{key}.json', {'task_id': 'owner-' + key,
                     'control_epoch': 2, 'status': status,
                     'planner_control': {'task_id': 'owner-' + key, 'control_epoch': 2, 'activity': status}})
        self.wake('old-epoch', publish=False)
        self.put('state/task_cells/owner-old-epoch.json', {'task_id': 'owner-old-epoch', 'control_epoch': 3,
                 'status': 'ACTIVE', 'planner_control': {'activity': 'ACTIVE'}})
        sha = self.publish()
        out = reconcile_local_dispatch(self.store, sha, now_fn=lambda: 1000)
        self.assertEqual([r['phase'] for r in out['results']].count('DEFERRED'), 6)
        self.assertEqual([r['phase'] for r in out['results']].count('SUPERSEDED'), 1)
        self.assertFalse(self.queue_ids())
        self.assertEqual(out['pending'], 0)
        self.put('state/task_cells/owner-0.json', {'task_id': 'owner-0', 'control_epoch': 2,
                 'status': 'ACTIVE', 'planner_control': {'activity': 'ACTIVE'}})
        resumed = self.publish()
        reconcile_local_dispatch(self.store, resumed, now_fn=lambda: 1001)
        self.assertEqual(self.queue_ids(), {'wake-0-long'})

    def test_lost_finalizer_reply_refreshes_canonical_and_stops_page_before_worker(self):
        self.wake('later', publish=False)
        semantic_fixtures.SemanticFinalizeTests()._fixture(self.repo, status='ERROR')
        sha = self.publish()
        from harness.request_drain import process
        def lost_reply(*args, **kwargs):
            result = process(*args, **kwargs)
            # Simulate another canonical mutation accepted with the same lost
            # acknowledgement: stale READY may no longer authorize this wake.
            head = latest(self.repo, 'origin', 'main')
            run_git(self.repo, 'reset', '--hard', head)
            bg = json.loads((self.repo / 'cl/t-later.json').read_text())
            bg['dispatch']['fence_token'] = 'new-fence-after-finalize'
            self.put('cl/t-later.json', bg)
            self.publish()
            raise TimeoutError('last accepted push response lost')
        with patch('local_bridge.local_dispatch.process', side_effect=lost_reply):
            first = reconcile_local_dispatch(self.store, sha, now_fn=lambda: 1000)
        self.assertEqual(first['results'][0]['phase'], 'RETRY')
        self.assertEqual(first['processed'], 1)
        self.assertFalse(self.queue_ids())
        self.assertNotEqual(first['canonical_commit'], sha)
        self.assertEqual(self.store.peek_git_snapshot(), first['canonical_commit'])
        resumed = reconcile_local_dispatch(self.store, first['canonical_commit'], now_fn=lambda: 1002)
        self.assertEqual(resumed['results'][0]['phase'], 'SUPERSEDED')
        self.assertFalse(self.queue_ids())

    def test_uncertain_finalizer_cannot_resume_page_if_fresh_read_fails(self):
        self.wake('later', publish=False)
        semantic_fixtures.SemanticFinalizeTests()._fixture(self.repo, status='ERROR')
        sha = self.publish()
        self.store.record_git_commit(sha)
        with patch('local_bridge.local_dispatch.process', side_effect=TimeoutError('unknown push')), \
             patch.object(self.store, 'read_git_snapshot', side_effect=TimeoutError('cannot refresh')):
            with self.assertRaisesRegex(TimeoutError, 'cannot refresh'):
                reconcile_local_dispatch(self.store, sha)
        self.assertIsNone(self.store.peek_git_snapshot())
        self.assertFalse(self.queue_ids())

    def test_worker_delivery_adds_no_fetch_worktree_commit_or_git_receipt(self):
        sha = self.wake()
        with patch('local_bridge.local_dispatch.run_git', wraps=run_git) as local_git, \
             patch('harness.request_scan.git', wraps=request_scan.git) as scan_git:
            out = reconcile_local_dispatch(self.store, sha)
        commands = [call.args[1] for spy in (local_git, scan_git) for call in spy.call_args_list]
        self.assertFalse(set(commands) & {'fetch', 'worktree', 'add', 'commit', 'push'})
        self.assertEqual(run_git(self.repo, 'rev-parse', 'HEAD').stdout.strip(), sha)
        self.assertFalse(run_git(self.repo, 'ls-tree', '-r', '--name-only', sha, '--', 'state/request_receipts').stdout)
        self.assertIsNone(out['canonical_commit'])

    def test_periodic_full_scan_recovers_and_deduplicates_without_new_sha(self):
        sha = self.wake()
        reconcile_local_dispatch(self.store, sha, now_fn=lambda: 1000)
        with patch('local_bridge.local_dispatch.discover', wraps=discover) as discovery:
            idle = reconcile_local_dispatch(self.store, sha, now_fn=lambda: 1299)
            self.assertEqual(discovery.call_count, 0)
            recovery = reconcile_local_dispatch(self.store, sha, now_fn=lambda: 1300)
        self.assertEqual(idle['processed'], 0)
        self.assertIsNone(discovery.call_args.kwargs['before'])
        self.assertTrue(recovery['results'][0]['duplicate'])

    def test_payloads_are_hydrated_in_bounded_pages_without_caching_bodies(self):
        for index in range(105):
            self.wake(f'{index:03}', publish=False)
        sha = self.publish()
        with patch('local_bridge.local_dispatch.hydrate', wraps=hydrate) as reads:
            first = reconcile_local_dispatch(self.store, sha, now_fn=lambda: 1000)
            second = reconcile_local_dispatch(self.store, sha, now_fn=lambda: 1001)
        self.assertEqual((first['processed'], second['processed']), (100, 5))
        self.assertTrue(first['scan_pending'])
        self.assertFalse(second['scan_pending'])
        self.assertTrue(all(len(call.args[1]) <= 100 for call in reads.call_args_list))
        self.assertEqual(len(self.queue_ids()), 105)
        self.assertLessEqual(len(self.store._local_dispatch_discovery.pending), 4096)
        self.assertFalse(any('payload' in entry['record'] for entry in self.store._local_dispatch_discovery.pending.values()))

    def test_semantic_finalize_uses_existing_projection_receipt_and_records_commit(self):
        semantic_fixtures.SemanticFinalizeTests()._fixture(self.repo, status='ERROR')
        sha = self.publish()
        out = reconcile_local_dispatch(self.store, sha)
        self.assertEqual(out['results'][0]['phase'], 'DONE')
        self.assertEqual(out['results'][0]['outcome'], 'ERROR')
        self.assertNotEqual(out['canonical_commit'], sha)
        self.assertEqual(self.store.peek_git_snapshot(), out['canonical_commit'])
        head = latest(self.repo, 'origin', 'main')
        bg = json.loads(run_git(self.repo, 'show', f'{head}:cl/t-final.backend.json').stdout)
        self.assertEqual(bg['dispatch']['state'], 'ERROR')
        receipt = json.loads(run_git(self.repo, 'show', f'{head}:state/request_receipts/{out["results"][0]["request_key"]}.json').stdout)
        self.assertEqual(receipt['phase'], 'DONE')
        self.assertEqual(reconcile_local_dispatch(self.store, head)['processed'], 0)

    def test_parallel_finalize_uses_branch_evidence_and_existing_receipt(self):
        task = parallel_fixtures.task()
        self.put('tasks/' + task['task_id'] + '.json', task)
        self.put(task['backend_cl'], parallel_fixtures.backend())
        self.put(task['foreground_cl'], parallel_fixtures.foreground())
        self.put('requests/parallel-branch-finalize/a.json', {'task_id': task['task_id']})
        sha = self.publish()
        run_git(self.repo, 'checkout', '-b', task['work_branch'])
        self.put(task['output_root'] + '/branch_result.json', parallel_fixtures.result())
        self.put(task['output_root'] + '/file.txt', {'artifact': True})
        run_git(self.repo, 'add', '-A')
        run_git(self.repo, 'commit', '-m', 'branch result')
        branch_head = run_git(self.repo, 'rev-parse', 'HEAD').stdout.strip()
        run_git(self.repo, 'push', 'origin', 'HEAD:refs/heads/' + task['work_branch'])
        run_git(self.repo, 'checkout', 'main')
        out = reconcile_local_dispatch(self.store, sha)
        self.assertEqual(out['results'][0]['outcome'], 'PASS')
        self.assertEqual(self.store.peek_git_snapshot(), out['canonical_commit'])
        head = latest(self.repo, 'origin', 'main')
        receipt = json.loads(run_git(self.repo, 'show', f'{head}:state/request_receipts/{out["results"][0]["request_key"]}.json').stdout)
        self.assertEqual(receipt['accepted_evidence']['validated_commit'], branch_head)
        self.assertEqual(reconcile_local_dispatch(self.store, head)['processed'], 0)


if __name__ == '__main__':
    unittest.main()
