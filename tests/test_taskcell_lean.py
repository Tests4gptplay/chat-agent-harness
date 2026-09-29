"""F-owned regression of the role contract, using real isolated Git transactions.
These tests simulate role outputs; they do not claim live browser/model acceptance.
"""
import copy
import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from local_bridge import planner_runtime as rt
from local_bridge.server import WakeStore
from local_bridge.task_cell_roles import complete_task_cell_role_prompt
from local_bridge.planner_memory import (
    PlannerMemoryError, memory_blob_sha, planner_current_path, planner_handoff_path,
    promote_planner_authority_with_memory,
    validate_predecessor_retirement_eligibility,
)
from local_bridge.planner_control import insert_planner_event

spec = importlib.util.spec_from_file_location('f097_fixtures', Path(__file__).with_name('test_planner_hybrid_control.py'))
fx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fx)
TASK = fx.TASK
CELL = f'state/task_cells/{TASK}.json'


def promoted_bundle():
    current, sealed, handoff = fx.prepared_handoff()
    control = fx.base_control('ROTATING')
    control['authority']['semantic_authority'] = False
    control['authority']['fenced_for_rotation'] = True
    control['successor'] = {
        'state': 'BOUND',
        'handoff_id': 'h1',
        'from_generation': 1,
        'to_generation': 2,
        'pending_fence_token': 'planner-fence-g2',
        'candidate_conversation_id': 'planner-new',
        'candidate_request_id': 'planner-new-request',
        'candidate_challenge': 'planner-new-challenge',
    }
    return promote_planner_authority_with_memory(
        current, handoff, control, expected_memory_version=current['memory_version'],
        promotion_ref='replacement-route:planner-new', promoted_at='2026-09-21T00:00:00Z',
    )


class HealthyHandoffTests(unittest.TestCase):
    def test_verified_promotion_enables_predecessor_retirement(self):
        bundle = promoted_bundle()
        result = validate_predecessor_retirement_eligibility(
            bundle['handoff'], bundle['planner_control'], task_cell_project_key='g-p-task-cell',
            protected_conversation_ids=['planner-new'],
        )
        self.assertTrue(result['eligible'])
        self.assertNotEqual(result['conversation_id'], bundle['planner_control']['authority']['conversation_id'])

    def test_corrupt_replacement_binding_still_prevents_deletion(self):
        for field, value in [('conversation_id', 'wrong-planner'), ('semantic_authority', False)]:
            bundle = promoted_bundle()
            bundle['handoff']['successor_binding'][field] = value
            with self.subTest(field=field), self.assertRaises(PlannerMemoryError):
                validate_predecessor_retirement_eligibility(
                    bundle['handoff'], bundle['planner_control'], task_cell_project_key='g-p-task-cell')

    def test_active_conversation_is_not_a_cleanup_candidate(self):
        bundle = promoted_bundle()
        bundle['handoff']['predecessor_conversation_id'] = bundle['planner_control']['authority']['conversation_id']
        with self.assertRaises(PlannerMemoryError):
            validate_predecessor_retirement_eligibility(
                bundle['handoff'], bundle['planner_control'], task_cell_project_key='g-p-task-cell')


class RuntimeGitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / 'work'
        self.origin = self.root / 'origin.git'
        subprocess.run(['git', 'init', '--bare', str(self.origin)], check=True, capture_output=True)
        subprocess.run(['git', 'clone', str(self.origin), str(self.work)], check=True, capture_output=True)
        self.git('config', 'user.name', 'test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('checkout', '-b', 'main')
        self.store = WakeStore(self.root / 'spool', repo_root=self.work)
        self.api = {'client_id': 'client-f097-0001', 'project_id': 'git-agent-harness'}
        self.roles = {r: {'role': r, 'conversation_id': f'{r}-old', 'request_id': f'{r}-request',
                          'challenge': f'{r}-challenge', 'response_started': True, 'semantic_ready': True,
                          'status': 'BOUND'} for r in ('planner', 'helper')}
        self.control = fx.base_control()
        self.control['runtime'] = {'foreground_runtime_dependency': False, 'semantic_output_timeout_seconds': 30}
        self.cell = {'v': 1, 'task_id': TASK, 'task_cell_id': TASK, 'control_epoch': 1,
                     'task_cell_project_key': 'g-p-task-cell', 'status': 'ACTIVE',
                     'roles': self.roles, 'planner_control': self.control}
        self.request = {'v': 1, 'kind': 'task_cell_role_prompt', 'request_id': 'planner-delivery-0001',
                        'task_id': TASK, 'control_epoch': 1, 'role': 'planner', 'challenge': 'planner-challenge',
                        'task_cell_project_key': 'g-p-task-cell', 'status': 'PENDING',
                        'requested_at': '2000-01-01T00:00:00Z',
                        'required_output_ref': f'evidence/{TASK}/roles/planner/decision-test.json',
                        'semantic_output_kind': 'PLANNER_DECISION', 'prompt': 'fixture'}
        current = fx.current_memory()
        self.commit({'state/lanes.json': {'v': 1, 'lanes': [{'lane_id': 'lane-00', 'project_key': 'g-p-worker', 'enabled': True}]}, CELL: self.cell, 'state/chatgpt.json': {'v': 1, 'control_request': self.request},
                     f'tasks/{TASK}.json': {'v': 1, 'task_id': TASK, 'status': 'ACTIVE'},
                     f'tasks/{TASK}.plan.json': {'v': 1, 'task_id': TASK},
                     planner_current_path(TASK): current})
        current['task_contract_blob_sha'] = self.git('rev-parse', f'origin/main:tasks/{TASK}.json')
        self.commit({planner_current_path(TASK): current})

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.work), *args], text=True, encoding='utf-8', stderr=subprocess.DEVNULL).strip()

    def sync(self):
        self.git('fetch', 'origin', 'main')
        self.git('reset', '--hard', 'origin/main')

    def read(self, path):
        self.git('fetch', 'origin', 'main')
        return json.loads(self.git('show', f'origin/main:{path}'))

    def commit(self, objects):
        if (self.work / 'state/chatgpt.json').exists():
            self.sync()
        for path, value in objects.items():
            target = self.work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
        self.git('add', '.')
        self.git('commit', '--allow-empty', '-m', 'fixture update')
        self.git('push', 'origin', 'HEAD:main')

    def tick(self):
        return rt.planner_runtime_tick(self.store, self.api)

    def started(self, request):
        result = complete_task_cell_role_prompt(self.store, {
            **self.api, 'request_id': request['request_id'], 'task_id': TASK, 'control_epoch': 1,
            'role': 'planner', 'challenge': 'planner-challenge', 'task_cell_project_key': 'g-p-task-cell',
            'conversation_id': 'planner-old', 'conversation_url': 'https://chatgpt.com/c/planner-old',
            'response_started': True,
        })
        self.assertTrue(result['ok'], result)

    def test_foreground_handoff_creates_plan_note_with_memory(self):
        source_request_id = 'foreground-handoff-plan-note'
        self.commit({'state/chatgpt.json': {'v': 1, 'control_request': {
            'kind': 'task_cell_planner_foreground_handoff',
            'request_id': source_request_id,
            'status': 'PENDING',
        }}})
        result = rt.begin_foreground_planner_handoff(self.store, {
            **self.api,
            'task_id': TASK,
            'source_request_id': source_request_id,
            'task_cell_project_key': 'g-p-task-cell',
            'control_epoch': 1,
        })
        self.assertTrue(result['ok'], result)
        self.assertEqual(
            self.git('show', f'origin/main:memory/planner/{TASK}/memory.md'),
            '# Planner Memory',
        )
        self.assertEqual(
            self.git('show', f'origin/main:memory/planner/{TASK}/plan_note.md'),
            '# Planner Plan Note',
        )

    def test_expired_planner_delivery_releases_control_slot_once(self):
        first = self.tick()
        self.assertEqual(first.get('action'), 'planner_delivery_recovered', first)
        evidence = self.read(first['recovery_ref'])
        self.assertEqual(evidence['request'], self.request)
        self.assertFalse(evidence['output_exists'])
        self.assertTrue(first['helper']['ok'], first)
        self.assertTrue(first['helper']['staged'], first)
        request = self.read('state/chatgpt.json')['control_request']
        self.assertEqual(request['kind'], 'task_cell_role_prompt')
        self.assertEqual(request['role'], 'helper')
        self.assertEqual(request['status'], 'PENDING')
        rotated = self.read(CELL)['planner_control']
        self.assertEqual(rotated['authority']['conversation_id'], self.control['authority']['conversation_id'])
        self.assertEqual(rotated['successor']['state'], 'NONE')
        self.assertIsNone(rotated['runtime'].get('pending_output'))
        second = self.tick()
        self.assertEqual(second.get('idle'), 'control_slot_busy')

    def test_fresh_pending_delivery_keeps_control_slot_busy(self):
        request = {**self.request, 'requested_at': datetime.now(timezone.utc).isoformat()}
        self.commit({'state/chatgpt.json': {'control_request': request}})
        self.assertEqual(self.tick().get('idle'), 'control_slot_busy')
        self.assertEqual(self.read('state/chatgpt.json')['control_request'], request)

    def test_unknown_age_is_not_fabricated_stall(self):
        request = dict(self.request)
        request.pop('requested_at')
        self.commit({'state/chatgpt.json': {'control_request': request}})
        self.assertEqual(self.tick().get('idle'), 'control_slot_busy')
        self.assertEqual(self.read('state/chatgpt.json')['control_request'], request)

    def test_user_pause_and_stale_epoch_are_not_bypassed(self):
        for change in ({'status': 'PAUSED_BY_USER'}, {'control_epoch': 2}):
            cell = copy.deepcopy(self.cell)
            cell.update(change)
            self.commit({CELL: cell, 'state/chatgpt.json': {'control_request': self.request}})
            with self.subTest(change=change):
                self.assertEqual(self.tick().get('idle'), 'control_slot_busy')
                self.assertEqual(self.read('state/chatgpt.json')['control_request'], self.request)

    def test_available_noncommitted_output_does_not_block_helper_recovery(self):
        self.commit({self.request['required_output_ref']: {'fixture': 'durable output'}})
        result = self.tick()
        self.assertEqual(result.get('action'), 'planner_delivery_recovered')
        self.assertTrue(result['output_exists'])
        self.assertTrue(result['helper']['ok'], result)
        self.assertTrue(result['helper']['staged'], result)
        request = self.read('state/chatgpt.json')['control_request']
        self.assertEqual(request['kind'], 'task_cell_role_prompt')
        self.assertEqual(request['status'], 'PENDING')

    def test_retirement_failure_does_not_disable_successor(self):
        bundle = promoted_bundle()
        cell = copy.deepcopy(self.cell)
        cell['planner_control'] = bundle['planner_control']
        cell['planner_control']['runtime'] = {'browser_promotion_complete': True, 'foreground_runtime_dependency': False}
        request = {'kind': 'task_cell_planner_predecessor_retire', 'request_id': 'retire-h1', 'status': 'PENDING',
                   'task_id': TASK, 'control_epoch': 1, 'task_cell_project_key': 'g-p-task-cell',
                   'conversation_id': bundle['handoff']['predecessor_conversation_id'], 'handoff_id': 'h1'}
        self.commit({CELL: cell, 'state/chatgpt.json': {'control_request': request},
                     planner_handoff_path(TASK, 'h1'): bundle['handoff'], planner_current_path(TASK): bundle['current']})
        result = rt.complete_planner_predecessor_retire(self.store, {
            **self.api, **request, 'deleted': False, 'already_absent': False, 'error': 'synthetic delete failure'})
        self.assertFalse(result['completed'], result)
        updated = self.read(CELL)['planner_control']
        self.assertEqual(updated['successor']['state'], 'PROMOTED')
        self.assertEqual(self.read('state/chatgpt.json')['control_request']['status'], 'PENDING')
        self.assertEqual(updated['authority']['conversation_id'], 'planner-new')
        self.assertEqual(updated['authority']['planner_generation'], 2)
        self.assertEqual(self.read(planner_handoff_path(TASK, 'h1'))['handoff_state'], 'RETIREMENT_ERROR')
        recovered = rt.complete_planner_predecessor_retire(self.store, {
            **self.api, **request, 'deleted': True, 'already_absent': False})
        self.assertTrue(recovered['completed'], recovered)
        self.assertEqual(self.read(CELL)['planner_control']['successor']['state'], 'NONE')

    def test_harness_owned_rotation_binds_retires_then_resumes_gen2(self):
        current, sealed, handoff = fx.prepared_handoff()
        task_sha = self.git('rev-parse', f'origin/main:tasks/{TASK}.json')
        current['task_contract_blob_sha'] = task_sha
        sealed['task_contract_blob_sha'] = task_sha
        handoff['current_memory_blob_sha'] = memory_blob_sha(current)
        handoff['sealed_generation_blob_sha'] = memory_blob_sha(sealed)
        handoff = copy.deepcopy(handoff)
        handoff['successor_binding'] = None
        handoff['promotion'] = None
        handoff['handoff_state'] = 'PREPARED'
        handoff['handoff_revision'] = 1

        cell = copy.deepcopy(self.cell)
        control = fx.base_control('ROTATING')
        control['authority']['semantic_authority'] = False
        control['authority']['fenced_for_rotation'] = True
        control['successor'] = {
            'state': 'REQUESTED',
            'handoff_id': 'h1',
            'reason': 'explicit_lifecycle_rollover',
            'from_generation': 1,
            'to_generation': 2,
            'packet_ref': planner_handoff_path(TASK, 'h1'),
            'candidate_conversation_id': None,
            'candidate_request_id': None,
            'candidate_challenge': None,
            'pending_fence_token': 'planner-fence-g2',
            'takeover_ref': None,
        }
        control['runtime'] = {
            'foreground_runtime_dependency': False,
            'browser_promotion_complete': False,
            'pending_output': None,
        }
        cell['planner_control'] = control
        cell['roles']['planner']['status'] = 'FENCED_ROTATING'
        cell['roles']['planner']['semantic_ready'] = False

        bootstrap = {
            'v': 1,
            'kind': 'task_cell_planner_successor_bootstrap',
            'request_id': 'replacement-bootstrap-g2',
            'status': 'PENDING',
            'requested_at': '2026-09-21T00:03:00Z',
            'task_cell_project_key': 'g-p-task-cell',
            'task_id': TASK,
            'control_epoch': 1,
            'role': 'planner',
            'challenge': 'planner-new-challenge',
            'handoff_id': 'h1',
            'to_generation': 2,
            'pending_fence_token': 'planner-fence-g2',
            'transport_bootstrap_only': True,
        }
        self.commit({
            CELL: cell,
            'state/chatgpt.json': {'v': 1, 'control_request': bootstrap},
            planner_handoff_path(TASK, 'h1'): handoff,
            planner_current_path(TASK): current,
        })

        bound = rt.complete_planner_successor_bootstrap(self.store, {
            **self.api,
            'request_id': bootstrap['request_id'],
            'task_id': TASK,
            'control_epoch': 1,
            'task_cell_project_key': 'g-p-task-cell',
            'conversation_id': 'planner-new',
            'conversation_url': 'https://chatgpt.com/c/planner-new',
            'handoff_id': 'h1',
            'response_started': True,
        })
        self.assertTrue(bound['completed'], bound)
        after_bind = self.read(CELL)
        promoted_control = after_bind['planner_control']
        self.assertEqual(promoted_control['authority']['planner_generation'], 2)
        self.assertEqual(promoted_control['authority']['conversation_id'], 'planner-new')
        self.assertTrue(promoted_control['authority']['semantic_authority'])
        self.assertEqual(promoted_control['successor']['state'], 'PROMOTED')
        self.assertEqual(promoted_control['activity'], 'ROTATING')
        self.assertIsNone(promoted_control['runtime'].get('pending_output'))
        promoted_handoff = self.read(planner_handoff_path(TASK, 'h1'))
        self.assertEqual(promoted_handoff['handoff_state'], 'PROMOTED')
        self.assertEqual(promoted_handoff['promotion']['promotion_owner'], 'HARNESS')

        promote_req = self.read('state/chatgpt.json')['control_request']
        self.assertEqual(promote_req['kind'], 'task_cell_planner_successor_promote')
        browser_promoted = rt.complete_planner_successor_promote(self.store, {
            **self.api,
            'request_id': promote_req['request_id'],
            'task_id': TASK,
            'control_epoch': 1,
            'task_cell_project_key': 'g-p-task-cell',
            'conversation_id': 'planner-new',
            'handoff_id': 'h1',
        })
        self.assertTrue(browser_promoted['completed'], browser_promoted)
        after_browser = self.read(CELL)
        self.assertTrue(after_browser['planner_control']['runtime']['browser_promotion_complete'])
        self.assertEqual(after_browser['roles']['planner']['conversation_id'], 'planner-new')
        self.assertEqual(after_browser['roles']['planner']['status'], 'BOUND_ROTATING')
        self.assertFalse(after_browser['roles']['planner']['semantic_ready'])

        retire_staged = self.tick()
        self.assertEqual(retire_staged.get('action'), 'predecessor_retirement_requested', retire_staged)
        retire_req = self.read('state/chatgpt.json')['control_request']
        self.assertEqual(retire_req['kind'], 'task_cell_planner_predecessor_retire')
        retired = rt.complete_planner_predecessor_retire(self.store, {
            **self.api,
            **retire_req,
            'deleted': True,
            'already_absent': False,
        })
        self.assertTrue(retired['completed'], retired)
        after_retire = self.read(CELL)
        self.assertEqual(after_retire['planner_control']['successor']['state'], 'NONE')
        self.assertEqual(after_retire['planner_control']['activity'], 'ACTIVE')
        self.assertEqual(after_retire['roles']['planner']['conversation_id'], 'planner-new')
        self.assertEqual(after_retire['roles']['planner']['status'], 'ACTIVE')
        self.assertTrue(after_retire['roles']['planner']['semantic_ready'])

        event = fx.worker_event('gen2-output')
        control = after_retire['planner_control']
        control, _ = insert_planner_event(control, event)
        after_retire['planner_control'] = control
        self.commit({CELL: after_retire})
        next_turn = self.tick()
        self.assertEqual(next_turn.get('action'), 'planner_turn_staged', next_turn)
        next_req = self.read('state/chatgpt.json')['control_request']
        self.assertEqual(next_req['role'], 'planner')
        self.assertEqual(next_req['planner_generation'], 2)
        prompt = next_req.get('prompt', '')
        self.assertIn(f'Act as PLANNER for managed task {TASK}', prompt)
        self.assertIn(f'tasks/{TASK}.json', prompt)
        self.assertIn(next_req['required_output_ref'], prompt)
        self.assertIn('gen2-output', prompt)
        self.assertIn('turn_signal', prompt)
        self.assertIn('semantic_sync', prompt)

    def test_handoff_first_message_is_real_work_and_keeps_pending_memory(self):
        cell = copy.deepcopy(self.cell)
        control, _ = insert_planner_event(cell['planner_control'], fx.worker_event('handoff-pending'))
        cell['planner_control'] = control
        self.commit({CELL: cell, 'state/chatgpt.json': {'control_request': None}})
        self.assertEqual(self.tick().get('action'), 'planner_turn_staged')
        cell = self.read(CELL)
        old_turn = cell['planner_control']['runtime']['active_turn']
        self.sync()
        path = self.work / old_turn['memory_entry_ref']
        path.write_text(path.read_text(encoding='utf-8') + '\nConfirmed 1+2+3+4+5=15; retain pending continuation.\n', encoding='utf-8')
        self.git('add', str(path)); self.git('commit', '-m', 'fixture semantic memory'); self.git('push', 'origin', 'HEAD:main')
        self.commit({'state/chatgpt.json': {'control_request': None}})
        result = rt.request_planner_rotation(self.store, {'task_id': TASK, 'reason': 'context_compacted', 'caller_role': 'harness'})
        self.assertTrue(result['staged'], result)
        request = self.read('state/chatgpt.json')['control_request']
        self.assertFalse(request['transport_bootstrap_only'])
        self.assertIn('Foreground requirements:', request['prompt'])
        self.assertNotIn('Reply briefly', request['prompt'])
        updated = self.read(CELL)['planner_control']
        turn = updated['runtime']['active_turn']
        self.assertNotEqual(turn['outcome_ref'], old_turn['outcome_ref'])
        self.assertEqual(self.read(turn['outcome_ref'])['planner_generation'], 2)
        memory = self.git('show', f'origin/main:memory/planner/{TASK}/memory.md')
        self.assertIn('1+2+3+4+5=15', memory)
        self.assertTrue(updated['inbox']['active_doorbell']['event_ids'])

    def _signal_child(self, body):
        child = rt._build_worker_child(parent_task_id=TASK, parent_control_epoch=1,
            decision_ref='test/decision.json', project_id='git-agent-harness', repo='test/repo',
            action={'kind': 'DISPATCH_WORKER_CHILD', 'child_task_id': 'signal-child',
                'lane_id': 'lane-00', 'worker_project_key': 'g-p-worker',
                'task_payload': {'goal': 'write actual work'},
                'wait': {'kind': 'WAIT_ADMISSION', 'selector': {'child_task_id': 'signal-child'}}})
        child['cl']['dispatch'].update(state='RUNNING', acked_by_worker_ref='worker-signal')
        cell = copy.deepcopy(self.cell)
        owned = {k: child[k] for k in ['child_task_id','task_ref','backend_cl','lane_id','worker_project_key','child_reply_ref']}
        owned.update(event_state='WAITING', dispatch_id=child['dispatch']['dispatch_id'], dispatch_generation=1)
        cell['planner_control']['runtime']['owned_children'] = [owned]
        self.commit({CELL: cell, 'state/chatgpt.json': {'control_request': None},
            child['task_ref']: child['task'], child['backend_cl']: child['cl'],
            child['task']['expected_result_ref']: {**child['task']['result_contract'], 'summary': ''}})
        self.sync()
        p = self.work / child['worker_reply_entry_ref']; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(child['worker_reply_entry_initial'] + body, encoding='utf8')
        p = self.work / child['child_reply_ref']; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(child['child_reply_initial'], encoding='utf8')
        self.git('add', '.'); self.git('commit', '-m', 'fixture reply'); self.git('push', 'origin', 'HEAD:main')
        req = {**self.api, 'role':'worker', 'signal':'complete', 'task_id':child['child_task_id'],
            'backend_cl':child['backend_cl'], 'dispatch_id':child['dispatch']['dispatch_id'],
            'dispatch_generation':1, 'fence_token':child['dispatch']['fence_token'], 'worker_ref':'worker-signal'}
        return child, req

    def test_empty_reply_signal_cannot_succeed(self):
        for signal in ('continue', 'complete'):
            child, req = self._signal_child('   \n')
            result = rt.observe_semantic_turn(self.store, {**req, 'signal':signal})
            self.assertTrue(result['accepted'], result)
            output = self.read(child['task']['expected_result_ref'])
            self.assertEqual(output['status'], 'ERROR')
            self.assertEqual(output['blocker']['kind'], 'WORKER_REPLY_MISSING')
            self.assertEqual(self.read(child['backend_cl'])['dispatch']['state'], 'ERROR')
            self.assertEqual(self.tick()['action'], 'worker_result_event_enqueued')

    def test_written_reply_signal_succeeds(self):
        child, req = self._signal_child('Produced the requested artifact.\n')
        result = rt.observe_semantic_turn(self.store, req)
        self.assertTrue(result['accepted'], result)
        self.assertEqual(self.read(child['task']['expected_result_ref'])['status'], 'PASS')

    def test_scheduler_cannot_finalize_header_only_pass(self):
        from local_bridge.scheduler import _try_finalize_planner_worker_child
        child, req = self._signal_child('')
        self.commit({child['task']['expected_result_ref']: {**child['task']['result_contract'], 'status':'PASS'}})
        result = _try_finalize_planner_worker_child(self.store, task_id=child['child_task_id'],
            backend_cl_rel=child['backend_cl'], canonical_sha=self.git('rev-parse', 'origin/main'), attempt=0)
        self.assertFalse(result['finalized'], result)
        self.assertEqual(result['reason'], 'worker_reply_missing')

    def test_reject_slot_dispatches_existing_child(self):
        child, req = self._signal_child('Incomplete artifact.\n')
        self.assertTrue(rt.observe_semantic_turn(self.store, req)['accepted'])
        self.assertEqual(self.tick()['action'], 'worker_result_event_enqueued')
        self.assertEqual(self.tick()['action'], 'planner_turn_staged')
        turn = self.read(CELL)['planner_control']['runtime']['active_turn']
        outcome = self.read(turn['outcome_ref']); outcome['outcome'] = 'CONTINUE'
        binding = next(x for x in turn['slots'] if x.get('bound_child_task_id') == child['child_task_id'])
        slot = self.read(binding['slot_ref']); slot.update(entry_type='REJECT', semantic={'correction':'Finish missing work.'})
        self.commit({turn['outcome_ref']:outcome, binding['slot_ref']:slot, 'state/chatgpt.json': {'control_request':None}})
        result = self.tick()
        self.assertEqual(result['dispatched_workers'], 1, result)
        self.assertEqual(self.read(child['backend_cl'])['dispatch']['generation'], 2)

    def test_worker_generation_checkpoint_and_unchanged_task_approval(self):
        child = rt._build_worker_child(parent_task_id=TASK, parent_control_epoch=1,
            decision_ref='test/decision.json', project_id='git-agent-harness', repo='test/repo',
            action={'kind': 'DISPATCH_WORKER_CHILD', 'child_task_id': 'count-child',
                'lane_id': 'lane-00', 'worker_project_key': 'g-p-worker',
                'task_payload': {'goal': 'count through generations', 'material_refs': ['https://example.invalid/image.png']},
                'wait': {'kind': 'WAIT_ADMISSION', 'selector': {'child_task_id': 'count-child'}}})
        cell = copy.deepcopy(self.cell)
        owned = {k: child[k] for k in ['child_task_id','task_ref','backend_cl','lane_id','worker_project_key','child_reply_ref']}
        owned.update(event_state='WAITING', dispatch_id=child['dispatch']['dispatch_id'], dispatch_generation=1)
        cell['planner_control']['runtime']['owned_children'] = [owned]
        self.commit({CELL: cell, 'state/chatgpt.json': {'control_request': None},
            child['task_ref']: child['task'], child['backend_cl']: child['cl']})
        for generation in [1, 5, 10, 15]:
            cell = self.read(CELL)
            cell['planner_control']['runtime']['owned_children'][0]['event_state'] = 'WAITING'
            cl = self.read(child['backend_cl'])
            task = self.read(child['task_ref'])
            cl['dispatch'].update(generation=generation, state='DONE')
            cl.update(overall='DONE', result_ref=task['expected_result_ref'])
            self.commit({CELL: cell, child['backend_cl']: cl, task['expected_result_ref']: {'status': 'CONTINUE'}})
            result = self.tick()
            if generation == 1:
                self.assertEqual(result['action'], 'worker_continued', result)
                self.assertEqual(result['generation'], 2)
                continue
            self.assertEqual(result['action'], 'worker_result_event_enqueued', result)
            self.assertEqual(self.read(CELL)['planner_control']['runtime']['owned_children'][0]['event_state'], 'REVIEW_PENDING')
            self.assertEqual(self.tick()['action'], 'planner_turn_staged')
            cell = self.read(CELL)
            turn = cell['planner_control']['runtime']['active_turn']
            outcome = self.read(turn['outcome_ref']); outcome['outcome'] = 'CONTINUE'
            # No slot/Task edit: pending continuation alone must be enough.
            self.commit({turn['outcome_ref']: outcome, 'state/chatgpt.json': {'control_request': None}})
            approved = self.tick()
            self.assertEqual(approved['dispatched_workers'], 1, approved)
            self.assertEqual(self.read(child['backend_cl'])['dispatch']['generation'], generation + 1)
            self.assertEqual(self.read(child['task_ref'])['material_refs'], ['https://example.invalid/image.png'])



class ProjectRecordTests(unittest.TestCase):
    setUp = RuntimeGitTests.setUp
    git = RuntimeGitTests.git
    sync = RuntimeGitTests.sync
    read = RuntimeGitTests.read
    commit = RuntimeGitTests.commit
    # Inherit the isolated Git fixture, not its unrelated test methods.
    def seed_terminal(self, directory=True):
        self.project = self.root / 'actual-project'
        self.project.mkdir()
        (self.project / 'source.blend').write_bytes(b'PROTECTED-ENGINEERING-SOURCE\x00')
        (self.project / 'output.pak').write_bytes(b'PROTECTED-DELIVERABLE\x01')
        self.protected = {p: p.read_bytes() for p in self.project.iterdir()}
        self.event = {'v': 1, 'kind': 'planner_final_delivery', 'event_id': 'terminal-planner-test',
                      'task_id': TASK, 'control_epoch': 1, 'state': 'PENDING', 'terminal_status': 'PASS',
                      'result_ref': f'evidence/{TASK}/acceptance.json'}
        cell = copy.deepcopy(self.cell)
        cell['planner_control'].update(activity='CLEANING', runtime={'final_delivery': self.event})
        task = {'v': 1, 'task_id': TASK, 'status': 'ACTIVE'}
        if directory:
            task['project_directory'] = str(self.project)
        self.cache = [f'memory/planner/{TASK}/memory.md', f'memory/planner/{TASK}/plan_note.md',
                      'memory/worker/child-owned/reply.md',
                      f'state/planner_turns/{TASK}/turn/memory-entry.md',
                      'state/worker_turns/child-owned/wake/reply-entry.md']
        self.commit({CELL: cell, f'tasks/{TASK}.json': task,
                     'tasks/child-owned.json': {'task_id': 'child-owned', 'kind': 'planner_worker_child',
                        'owner_task_id': TASK, 'owner_control_epoch': 1},
                     self.event['result_ref']: {'sequence': [1,2,3,4,5,6,7], 'sum':28},
                     **{p: {'semantic': '1+2+3+4+5=15; total=28; 交接已确认'} for p in self.cache},
                     'memory/worker/child-unrelated/reply.md': {'semantic': 'KEEP OTHER TASK'}})
        self.claim = rt.planner_final_delivery_status(self.store, self.api)['event']

    def clean(self):
        return rt.planner_final_delivery_update(self.store, {**self.api, 'task_cell_ref': CELL,
            'event_id': self.event['event_id'], 'claim_id': self.claim['claim_id'],
            'cleanup_receipt': {'all_deleted': True, 'deleted': ['test-owned-chat']}}, operation='cleaned')

    def test_records_archive_before_cache_prune_preserves_engineering_files(self):
        self.seed_terminal()
        original = {p: self.git('show', f'origin/main:{p}') for p in self.cache}
        result = self.clean()
        self.assertTrue(result['ok'], result)
        archive = self.project / 'records.md'
        self.assertTrue(archive.is_file(), 'Harness must write project record before reset')
        content = archive.read_text(encoding='utf8')
        for p, body in original.items():
            self.assertIn(p, content); self.assertIn(body, content)
        self.git('fetch','origin','main')
        refs = self.git('ls-tree','-r','--name-only','origin/main').splitlines()
        for p in self.cache: self.assertNotIn(p, refs)
        self.assertIn('memory/worker/child-unrelated/reply.md', refs)
        self.assertIn(self.event['result_ref'], refs)
        for p, body in self.protected.items(): self.assertEqual(p.read_bytes(),body)
        manifest = self.read(result['event']['record_archive']['manifest_ref'])
        self.assertEqual(manifest['sha256'], hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual(sorted(manifest['pruned_cache_refs']), sorted(self.cache + [f'memory/planner/{TASK}/current.json']))
        self.assertTrue(self.clean()['duplicate'])

    def test_records_existing_user_record_not_overwritten(self):
        self.seed_terminal()
        path = self.project / 'records.md'; path.write_text('USER RECORD',encoding='utf8')
        result = self.clean()
        self.assertTrue(result['ok'],result)
        self.assertEqual(path.read_text(encoding='utf8'),'USER RECORD')
        self.assertNotEqual(result['event']['record_archive']['path'], str(path))

    def test_records_archive_failure_retains_caches_and_working_state(self):
        self.seed_terminal()
        task = self.read(f'tasks/{TASK}.json'); task['project_directory'] = str(self.project/'missing')
        self.commit({f'tasks/{TASK}.json':task})
        result = self.clean()
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error'],'PLANNER_RECORD_ARCHIVE_FAILED')
        self.assertEqual(self.read(CELL)['planner_control']['activity'],'CLEANING')
        for p in self.cache: self.assertTrue(self.read(p))

    def test_records_unbound_legacy_task_keeps_caches(self):
        self.seed_terminal(directory=False)
        result = self.clean(); self.assertTrue(result['ok'],result)
        for p in self.cache: self.assertTrue(self.read(p))
        self.assertEqual(result['event']['record_archive']['status'],'RETAINED_UNBOUND')

    def test_records_old_notification_does_not_starve_cleanup(self):
        self.seed_terminal()
        older = copy.deepcopy(self.read(CELL)); older['task_id']='000-old'
        older['planner_control']['activity']='DONE'
        event=older['planner_control']['runtime']['final_delivery']
        event.update(task_id='000-old',event_id='terminal-planner-old',state='CLAIMED',cleanup_complete=True)
        self.commit({'state/task_cells/000-old.json':older})
        result=rt.planner_final_delivery_status(self.store,self.api)
        self.assertEqual(result['event']['task_id'],TASK,result)
        self.assertEqual(self.read('state/task_cells/000-old.json')['planner_control']['runtime']['final_delivery']['state'],'CLAIMED')



if __name__ == '__main__':
    unittest.main()
