"""Final Git writes drive recovery/continuation without assistant DOM output."""
import copy
import unittest
from unittest.mock import patch
from semantic_runtime_fixture import HarnessCase, PARENT
from playwright_host.bridge import BridgeError


class GitSemanticCompletionTests(unittest.TestCase):
    def case(self, **kw):
        c = HarnessCase(**kw)
        self.addCleanup(c.close)
        return c

    def until(self, c, predicate, count=12, seconds=1):
        for _ in range(count):
            c.tick(seconds)
            if predicate():
                return
        self.fail(str(c.ticks[-1]))

    @staticmethod
    def generation(c, lane='lane-00'):
        child = next(x for x in c.children() if x['lane_id'] == lane)
        return c.read(child['backend_cl'])['dispatch']['generation']

    def assert_no_completion_text(self, c):
        self.assertTrue(all(not p.answer for p in c.ui.all))
        self.assertTrue(all('"kind":"semantic_sync"' not in x['text'] for x in c.ui.created))

    def test_planner_and_worker_running_without_text_continue_on_git_alone(self):
        c = self.case()
        c.tick()
        original = c.planner()
        c.decide(['lane-00'])
        self.assertTrue(c.ui.page_for(original['conversation_id']).running)
        self.until(c, lambda: bool(c.children()) and c.current('lane-00') is not None)
        self.assertFalse(c.ui.page_for(original['conversation_id']).running)
        memory = c.text(f'memory/planner/{PARENT}/memory.md')
        old_worker = c.current('lane-00')
        c.worker_result('lane-00')
        self.until(c, lambda: self.generation(c) == 2)
        self.assertFalse(c.ui.page_for(old_worker['conversation_id']).running)
        for _ in range(3):
            c.tick()
        self.assertEqual(c.text(f'memory/planner/{PARENT}/memory.md'), memory)
        self.assertEqual(self.generation(c), 2)
        self.assertEqual(len([x for x in c.ui.created if x['role'] == 'worker']), 2)
        self.assert_no_completion_text(c)

    def test_no_git_signal_and_chat_word_cannot_complete_a_turn(self):
        c = self.case(); c.tick()
        role = c.planner()
        for _ in range(3):
            c.tick()
        self.assertEqual(c.children(), [])
        self.assertTrue(c.ui.page_for(role['conversation_id']).running)
        result = c.runtime.bridge.call('semantic_turn_observe', role='planner',
            task_id=PARENT, control_epoch=1, conversation_id=role['conversation_id'],
            request_id=role['active_request_id'], output_ref=role['active_output_ref'], signal='done')
        self.assertFalse(result['accepted'])
        self.assertEqual(result['reason'], 'planner_turn_signal_missing')
        self.assertIsNotNone(c.cell()['planner_control']['runtime'].get('active_turn'))

    def test_system_error_helper_done_is_read_without_a_page_or_syscall(self):
        c = self.case(idle=True)
        c.runtime.bridge.event('harness.execution_error', 'error', message='isolated test error')
        role = c.error_helper(); self.assertIsNotNone(role)
        c.repair_done()
        c.ui.page_for(role['conversation_id']).close()
        self.until(c, lambda: c.error_helper() is None)
        self.assertIn(role['conversation_id'], c.ui.deleted)
        self.assertFalse((c.work/'state/task_cells').exists())
        self.assertIsNone(c.read('state/chatgpt.json')['active_task'])
        self.assert_no_completion_text(c)

    def test_worker_helper_two_phases_rearm_without_chat_completion(self):
        c = self.case(); c.start(['lane-00']); old = copy.deepcopy(c.current('lane-00'))
        self.until(c, lambda: c.runtime.state.get_role(PARENT, 1, 'helper') is not None, seconds=1801)
        helper = c.runtime.state.get_role(PARENT, 1, 'helper')
        body = {'diagnosis': 'Owned process inspected; current Worker can resume.',
                'repair_result': 'Concrete obstacle removed; original execution continued.',
                'return_target': 'worker', 'recovery_action': 'resume_current'}
        c.write('Helper phase-1', {helper['active_output_ref']: body})
        c.tick()
        self.assertIsNotNone(c.runtime.state.get_role(PARENT, 1, 'helper'))
        c.write('Helper final Git done', {helper['active_output_ref']: {**body, 'turn_signal': 'done'}})
        self.until(c, lambda: c.runtime.state.get_role(PARENT, 1, 'helper') is None)
        now = c.current('lane-00')
        self.assertEqual(now['conversation_id'], old['conversation_id'])
        self.assertEqual(now['worker_watchdog']['state'], 'ARMED')
        self.assertEqual(c.cell()['planner_control']['wait']['kind'], 'WAIT_RESULT')
        deadline = now['worker_watchdog']['deadline_at']
        c.tick(); c.tick()
        self.assertEqual(c.current('lane-00')['worker_watchdog']['deadline_at'], deadline)
        c.worker_result('lane-00')
        self.until(c, lambda: self.generation(c) == 2)
        self.assert_no_completion_text(c)

    def test_worker_helper_same_generation_retry_then_normal_continue(self):
        c = self.case(); c.start(['lane-00']); old = copy.deepcopy(c.current('lane-00'))
        self.until(c, lambda: c.runtime.state.get_role(PARENT, 1, 'helper') is not None, seconds=1801)
        helper = c.runtime.state.get_role(PARENT, 1, 'helper')
        child = c.children()[0]
        # These are the documented Helper's physical action and semantic writes.
        c.ui.delete_project_conversation(None, None, old['conversation_id'])
        c.ui.page_for(old['conversation_id']).close()
        body = {'diagnosis': 'Old Worker is unresponsive; existing output retained.',
                'repair_result': 'Only bound physical conversation removed.',
                'return_target': 'worker', 'recovery_action': 'same_generation_retry',
                'deleted_conversation_id': old['conversation_id']}
        c.write('Helper incident and phase-1', {
            child['child_reply_ref']: c.text(child['child_reply_ref']) + '\n## WORKER_HELPER INCIDENT ' + helper['request_id'] + '\nRetry the same owned work.\n',
            helper['active_output_ref']: body})
        self.until(c, lambda: c.current('lane-00')['conversation_id'] != old['conversation_id'])
        replacement = c.current('lane-00')
        for key in ('dispatch_id', 'dispatch_generation', 'fence_token', 'wake_id'):
            self.assertEqual(replacement['worker_watchdog'][key], old['worker_watchdog'][key])
        c.write('Helper final Git done', {helper['active_output_ref']: {**body, 'turn_signal': 'done'}})
        self.until(c, lambda: c.runtime.state.get_role(PARENT, 1, 'helper') is None)
        c.worker_result('lane-00')
        self.until(c, lambda: self.generation(c) == 2)
        self.assert_no_completion_text(c)

    def test_review_boundary_uses_git_and_does_not_skip_planner(self):
        c = self.case(); c.start(['lane-00'])
        for g in range(1, 5):
            c.worker_result('lane-00')
            self.until(c, lambda: self.generation(c) == g + 1)
        c.worker_result('lane-00')
        self.until(c, lambda: bool(c.planner().get('active_request_id')))
        self.assertEqual(self.generation(c), 5)
        c.decide()
        self.until(c, lambda: self.generation(c) == 6)
        self.assert_no_completion_text(c)

    def test_planner_handoff_still_performs_existing_rotation(self):
        c = self.case(); c.tick(); old = c.planner()
        c.decide(signal='handoff')
        self.until(c, lambda: c.planner()['planner_generation'] == old['planner_generation'] + 1
                   and bool(c.planner().get('active_output_ref'))
                   and c.cell()['planner_control']['successor']['state'] == 'NONE')
        self.assertNotEqual(c.planner()['conversation_id'], old['conversation_id'])
        self.assertIn(old['conversation_id'], c.ui.deleted)
        c.decide(['lane-00'])
        self.until(c, lambda: bool(c.children()) and c.current('lane-00') is not None)
        self.assert_no_completion_text(c)

    def test_planner_non_dispatch_signals_are_consumed_from_git(self):
        for signal, semantic, expected in [
            ('wait', {'wait': {'kind': 'WAIT_DEP', 'selector': {'dependency': 'owned-job'}, 'refs': []}}, 'PARKED_WAIT_EVENT'),
            ('need_user', {'question': 'Which input should this task use?'}, 'WAIT_USER'),
            ('error', {'code': 'INPUT_NOT_USABLE', 'refs': []}, 'ERROR'),
        ]:
            with self.subTest(signal=signal):
                c = HarnessCase()
                try:
                    c.tick(); role = c.planner(); c.decide(signal=signal)
                    out = c.read(role['active_output_ref']); out['semantic'] = semantic
                    c.write('Planner final semantic payload', {role['active_output_ref']: out})
                    c.tick()
                    self.assertEqual(c.cell()['planner_control']['activity'], expected)
                    self.assertIsNone(c.cell()['planner_control']['runtime'].get('active_turn'))
                    self.assertEqual(c.children(), [])
                    self.assert_no_completion_text(c)
                finally:
                    c.close()

    def test_planner_complete_starts_existing_terminal_path_without_dom(self):
        c = self.case(); c.tick(); role = c.planner(); c.decide(signal='complete')
        out = c.read(role['active_output_ref']); out['semantic'] = {'final_result_ref': 'results/accepted.txt'}
        c.write('Planner accepted final result', {'results/accepted.txt': 'Accepted task output.\n', role['active_output_ref']: out})
        c.tick()
        self.assertEqual(c.read(role['active_output_ref'])['outcome'], 'COMPLETE')
        self.assertIsNone(c.cell()['planner_control']['runtime'].get('active_turn'))
        self.assertTrue(c.cell()['planner_control']['semantic_authority_closed'])
        self.assert_no_completion_text(c)

    def test_one_transport_failure_does_not_lose_committed_completion(self):
        c = self.case(); c.tick(); c.decide(['lane-00'])
        c.fail_ops['planner_runtime_tick'] = 1
        c.tick(); self.assertIsNotNone(c.error_helper())
        self.until(c, lambda: bool(c.children()) and c.current('lane-00') is not None)
        self.assertEqual(len([x for x in c.ui.created if x['role'] == 'worker']), 1)
        self.assert_no_completion_text(c)

    def test_stop_failure_cannot_rollback_semantic_progress(self):
        c = self.case(); c.tick(); c.decide(['lane-00'])
        with patch.object(c.ui, 'stop_response', side_effect=RuntimeError('isolated Stop failure')):
            c.tick()
        self.assertIsNone(c.cell()['planner_control']['runtime'].get('active_turn'))
        self.assertIsNotNone(c.error_helper())
        self.until(c, lambda: c.current('lane-00') is not None)
        self.assertEqual(len([x for x in c.ui.created if x['role'] == 'worker']), 1)

    def test_idle_bound_outputs_share_one_snapshot_and_make_no_commits(self):
        c = self.case(lanes=2); c.start(['lane-00', 'lane-01'])
        before = c.git('rev-parse', 'origin/main')
        original = c.store._git
        fetches = []
        def traced(*args, **kwargs):
            if args[0] == 'fetch':
                fetches.append(args)
            return original(*args, **kwargs)
        with patch.object(c.store, '_git', side_effect=traced):
            result = c.runtime.bridge.call('planner_runtime_tick', bound_turns=c.runtime._bound_semantic_turns())
        self.assertEqual(result['semantic_turns'], [])
        self.assertEqual(len(fetches), 1)
        self.assertEqual(c.git('rev-parse', 'origin/main'), before)

    def test_planner_requested_helper_routes_result_and_done_without_dom(self):
        c = self.case(); c.tick(); role = c.planner()
        c.decide(signal='wait')
        output = c.read(role['active_output_ref'])
        output['semantic'] = {'helper_prompt': 'Repair the exact bounded input, then return to Planner.'}
        c.write('Planner requests operational recovery', {role['active_output_ref']: output})
        self.until(c, lambda: c.runtime.state.get_role(PARENT, 1, 'helper') is not None)
        helper = c.runtime.state.get_role(PARENT, 1, 'helper')
        phase1 = {'diagnosis': 'Input issue identified.', 'repair_result': 'Input restored; Planner can proceed.'}
        c.write('Helper phase-1 repair result', {helper['active_output_ref']: phase1})
        self.until(c, lambda: bool(c.planner().get('active_output_ref')))
        self.assertIsNotNone(c.runtime.state.get_role(PARENT, 1, 'helper'))
        c.write('Helper final done after next role starts', {helper['active_output_ref']: {**phase1, 'turn_signal': 'done'}})
        self.until(c, lambda: c.runtime.state.get_role(PARENT, 1, 'helper') is None)
        c.decide(['lane-00'])
        self.until(c, lambda: c.current('lane-00') is not None)
        self.assert_no_completion_text(c)

    def test_worker_terminal_signals_reach_planner_without_text(self):
        for signal, terminal in [('complete', 'DONE'), ('blocked', 'BLOCKED'), ('error', 'ERROR')]:
            with self.subTest(signal=signal):
                c = HarnessCase()
                try:
                    c.start(['lane-00']); child = c.children()[0]
                    c.worker_result('lane-00', signal=signal)
                    self.until(c, lambda: bool(c.planner().get('active_request_id')))
                    self.assertEqual(c.read(child['backend_cl'])['dispatch']['state'], terminal)
                    self.assertEqual(self.generation(c), 1)
                    self.assert_no_completion_text(c)
                finally:
                    c.close()

    def test_rework_is_consumed_like_done_without_an_extra_signal(self):
        c = self.case(); c.tick(); c.decide(['lane-00'], signal='rework')
        self.until(c, lambda: c.current('lane-00') is not None)
        self.assertEqual(c.cell()['planner_control']['last_decision']['outcome'], 'CONTINUE')
        self.assert_no_completion_text(c)

    def test_canonical_reconciliation_consumes_without_host_local_candidates(self):
        c = self.case(); c.tick(); c.decide(['lane-00']); role = c.planner()
        result = c.runtime.bridge.call('planner_runtime_tick')
        self.assertTrue(any(r['result'].get('accepted') for r in result['semantic_turns']))
        self.assertIsNone(c.cell()['planner_control']['runtime'].get('active_turn'))
        # The next Host tick receives duplicate completion and closes its old turn.
        self.until(c, lambda: c.current('lane-00') is not None)
        self.assertFalse(c.ui.page_for(role['conversation_id']).running)
        self.assertEqual(len([x for x in c.ui.created if x['role'] == 'worker']), 1)
        self.assert_no_completion_text(c)

    def test_lost_reconciliation_response_after_commit_does_not_repeat_planning(self):
        c = self.case(); c.tick(); c.decide(['lane-00'])
        request = c.runtime.bridge._request
        lost = []
        def lose_reply(op, **payload):
            result = request(op, **payload)
            if op == 'planner_runtime_tick' and not lost:
                lost.append(True)
                raise BridgeError('isolated lost response after canonical commit')
            return result
        with patch.object(c.runtime.bridge, '_request', side_effect=lose_reply):
            c.tick()
        self.assertIsNotNone(c.error_helper())
        memory = c.text(f'memory/planner/{PARENT}/memory.md')
        self.until(c, lambda: c.current('lane-00') is not None)
        c.tick(); c.tick()
        self.assertEqual(c.text(f'memory/planner/{PARENT}/memory.md'), memory)
        self.assertEqual(len([x for x in c.ui.created if x['role'] == 'worker']), 1)
        self.assert_no_completion_text(c)

    def test_two_current_worker_results_are_consumed_in_one_git_pass(self):
        c = self.case(lanes=2); c.start(['lane-00', 'lane-01'])
        c.worker_result('lane-00'); c.worker_result('lane-01')
        result = c.runtime.bridge.call('planner_runtime_tick', bound_turns=c.runtime._bound_semantic_turns())
        accepted = [r for r in result['semantic_turns'] if r['binding']['role'] == 'worker' and r['result'].get('accepted')]
        self.assertEqual(len(accepted), 2)
        c.runtime._finish_git_turns(result['semantic_turns'])
        self.until(c, lambda: all(self.generation(c, lane) == 2 for lane in ('lane-00', 'lane-01')))
        self.assert_no_completion_text(c)


if __name__ == '__main__':
    unittest.main()
