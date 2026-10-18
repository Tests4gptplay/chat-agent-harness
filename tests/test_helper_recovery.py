import copy, importlib.util, json, sys, unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
spec=importlib.util.spec_from_file_location('lean_fixtures', REPO/'tests/test_taskcell_lean.py')
fx=importlib.util.module_from_spec(spec);spec.loader.exec_module(fx)
from playwright_host.runtime import BrowserRuntime
from playwright_host.bridge import BridgeError
from playwright_host.ui import ChatGPTUI
from local_bridge.planner_runtime import _planner_helper_prompt, _worker_helper_prompt

from harness.worker_lineage_compat import (
    WORKER_LINEAGES_KEY,
)


class GitRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f=fx.RuntimeGitTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.cell=copy.deepcopy(self.f.cell)
        self.cell['roles'].pop('helper')
        self.cell['planner_control']['activity']='WAKING'
        self.pending={'kind':'PLANNER_TURN','ref':self.f.request['required_output_ref'],'request_id':self.f.request['request_id'],'conversation_id':'planner-old','status':'WAITING','started_at':'2000-01-01T00:00:00Z','wake_prompt':'the exact original work order'}
        self.cell['planner_control']['runtime']['pending_output']=self.pending
        self.state={'control_request':{**self.f.request,'status':'DONE'}}
        self.save()
    def save(self):self.f.commit({fx.CELL:self.cell,'state/chatgpt.json':self.state})
    def test_old_pending_output_age_does_not_wake_helper(self):
        result=self.f.tick()
        self.assertNotEqual(result.get('action'),'planner_output_helper_staged',result)
        cell=self.f.read(fx.CELL)
        self.assertEqual(cell['planner_control']['runtime']['pending_output'],self.pending)
        self.assertEqual(cell['planner_control']['runtime'].get('pending_role_requests') or [],[])
        control=self.f.read('state/chatgpt.json')['control_request']
        self.assertEqual(control['role'],'planner')
    def test_helper_result_routes_before_done_then_done_only_exits_helper(self):
        helper_id='helper-return-001'
        output_ref=f'evidence/{fx.TASK}/roles/helper/{helper_id}.json'
        cell=copy.deepcopy(self.f.cell)
        cell['roles']['helper']={
            **cell['roles']['helper'],
            'request_id':helper_id,
            'conversation_id':'helper-old',
        }
        control=cell['planner_control']
        control['activity']='PARKED_WAIT_EVENT'
        control['wait']={'kind':'WAIT_HELPER','selector':{'output_ref':output_ref},'refs':[]}
        runtime=control['runtime']
        runtime['pending_role_outputs']=[{
            'role':'helper','kind':'helper_result','request_id':helper_id,
            'challenge':'helper-challenge','artifact_ref':output_ref,
            'state':'WAITING','decision_ref':'decision-helper-001',
        }]
        self.f.commit({
            fx.CELL:cell,
            'state/chatgpt.json':{'v':1,'control_request':{
                'v':1,'kind':'task_cell_role_prompt','request_id':helper_id,
                'task_id':fx.TASK,'control_epoch':1,'role':'helper',
                'challenge':'helper-challenge','task_cell_project_key':'g-p-task-cell',
                'status':'DONE','prompt':'fixture helper',
                'required_output_ref':output_ref,
            }},
            output_ref:{
                'diagnosis':'owned incident recovered',
                'repair_result':'canonical return path restored',
            },
        })

        routed=self.f.tick()
        self.assertEqual(routed.get('action'),'helper_result_event_enqueued',routed)
        routed_cell=self.f.read(fx.CELL)
        pending=routed_cell['planner_control']['runtime']['pending_role_outputs'][0]
        self.assertEqual(pending['state'],'EMITTED')
        self.assertTrue(pending['event_id'])
        self.assertNotIn('done_observed_at',pending)
        self.assertNotIn('turn_signal',self.f.read(output_ref))

        staged=self.f.tick()
        self.assertEqual(staged.get('action'),'planner_turn_staged',staged)
        planner_request=self.f.read('state/chatgpt.json')['control_request']
        self.assertEqual(planner_request['role'],'planner')
        self.assertEqual(planner_request['status'],'PENDING')
        self.f.started(planner_request)
        started=self.f.read('state/chatgpt.json')['control_request']
        self.assertTrue(started['result']['response_started'])

        self.f.commit({output_ref:{
            'diagnosis':'owned incident recovered',
            'repair_result':'canonical return path restored',
            'turn_signal':'done',
        }})
        done=rt.sync_semantic_turn(self.f.store,{
            **self.f.api,'role':'helper','task_id':fx.TASK,'control_epoch':1,
            'conversation_id':'helper-old','request_id':helper_id,'output_ref':output_ref,
        })
        self.assertTrue(done['accepted'],done)
        finished=self.f.read(fx.CELL)
        pending=finished['planner_control']['runtime']['pending_role_outputs'][0]
        self.assertEqual(pending['state'],'EMITTED')
        self.assertTrue(pending['done_observed_at'])

    def test_worker_helper_same_generation_result_does_not_become_planner_event(self):
        helper_id='worker-helper-result-001'
        output_ref=f'evidence/{fx.TASK}/roles/helper/{helper_id}.json'
        cell=copy.deepcopy(self.f.cell)
        control=cell['planner_control']
        control['activity']='PARKED_WAIT_EVENT'
        control['wait']={'kind':'WAIT_WORKER_HELPER','selector':{'output_ref':output_ref},'refs':[]}
        runtime=control['runtime']
        runtime['pending_role_requests']=[]
        runtime['pending_role_outputs']=[{
            'role':'helper','kind':'worker_helper_result','request_id':helper_id,
            'challenge':'worker-helper-challenge','artifact_ref':output_ref,
            'state':'WAITING','decision_ref':'worker-watchdog-incident-001',
            'helper_source':'worker_watchdog',
        }]
        self.f.commit({
            fx.CELL:cell,
            'state/chatgpt.json':{'v':1,'control_request':{
                'v':1,'kind':'task_cell_worker_helper_prompt','request_id':helper_id,
                'task_id':fx.TASK,'control_epoch':1,'role':'helper',
                'challenge':'worker-helper-challenge','task_cell_project_key':'g-p-task-cell',
                'status':'DONE','prompt':'fixture worker helper',
                'required_output_ref':output_ref,'helper_source':'worker_watchdog',
                'semantic_output_kind':'WORKER_HELPER_RESULT',
            }},
            output_ref:{
                'diagnosis':'exact worker remained unresolved after watchdog inspection',
                'repair_result':'same-generation worker path restored',
                'return_target':'worker',
                'recovery_action':'resume_current',
            },
        })

        result=self.f.tick()
        self.assertEqual(result.get('action'),'worker_helper_current_worker_resumed',result)
        self.assertEqual(result.get('return_target'),'worker')
        routed=self.f.read(fx.CELL)
        pending=routed['planner_control']['runtime']['pending_role_outputs'][0]
        self.assertEqual(pending['state'],'WORKER_STARTED')
        self.assertEqual(pending['return_mode'],'WORKER_SAME_GENERATION')
        inbox=(routed['planner_control'].get('inbox') or {}).get('events') or {}
        self.assertFalse(any(
            isinstance(event,dict) and event.get('kind') in {'helper_result','worker_helper_result'}
            for event in inbox.values()
        ))

    def test_worker_helper_planner_return_can_finish_after_planner_route(self):
        helper_id='worker-helper-planner-return-001'
        output_ref=f'evidence/{fx.TASK}/roles/helper/{helper_id}.json'
        cell=copy.deepcopy(self.f.cell)
        cell['roles']['helper']={
            **cell['roles']['helper'],
            'request_id':helper_id,
            'conversation_id':'helper-worker-to-planner',
        }
        control=cell['planner_control']
        control['activity']='PARKED_WAIT_EVENT'
        control['wait']={'kind':'WAIT_WORKER_HELPER','selector':{'output_ref':output_ref},'refs':[]}
        control['runtime']['pending_role_requests']=[]
        control['runtime']['pending_role_outputs']=[{
            'role':'helper',
            'kind':'worker_helper_result',
            'request_id':helper_id,
            'challenge':'worker-helper-planner-challenge',
            'artifact_ref':output_ref,
            'state':'WAITING',
            'decision_ref':'worker-watchdog-planner-return',
            'helper_source':'worker_watchdog',
        }]
        self.f.commit({
            fx.CELL:cell,
            'state/chatgpt.json':{'v':1,'control_request':{
                'v':1,
                'kind':'task_cell_worker_helper_prompt',
                'request_id':helper_id,
                'task_id':fx.TASK,
                'control_epoch':1,
                'role':'helper',
                'challenge':'worker-helper-planner-challenge',
                'task_cell_project_key':'g-p-task-cell',
                'status':'DONE',
                'prompt':'fixture worker helper returning to Planner',
                'required_output_ref':output_ref,
                'helper_source':'worker_watchdog',
                'semantic_output_kind':'WORKER_HELPER_RESULT',
            }},
            output_ref:{
                'diagnosis':'same-generation Worker recovery is not the legal next transition',
                'repair_result':'return exact incident to authoritative Planner for review',
                'return_target':'planner',
            },
        })

        routed=self.f.tick()
        self.assertEqual(routed.get('action'),'worker_helper_planner_event_enqueued',routed)
        routed_cell=self.f.read(fx.CELL)
        pending=routed_cell['planner_control']['runtime']['pending_role_outputs'][0]
        self.assertEqual(pending['state'],'EMITTED')
        self.assertEqual(pending['return_mode'],'PLANNER_EVENT')

        staged=self.f.tick()
        self.assertEqual(staged.get('action'),'planner_turn_staged',staged)
        planner_request=self.f.read('state/chatgpt.json')['control_request']
        self.assertEqual(planner_request['role'],'planner')
        self.f.started(planner_request)
        self.assertTrue(self.f.read('state/chatgpt.json')['control_request']['result']['response_started'])

        self.f.commit({output_ref:{
            'diagnosis':'same-generation Worker recovery is not the legal next transition',
            'repair_result':'return exact incident to authoritative Planner for review',
            'return_target':'planner',
            'turn_signal':'done',
        }})
        done=rt.sync_semantic_turn(self.f.store,{
            **self.f.api,
            'role':'helper',
            'task_id':fx.TASK,
            'control_epoch':1,
            'conversation_id':'helper-worker-to-planner',
            'request_id':helper_id,
            'output_ref':output_ref,
        })
        self.assertTrue(done['accepted'],done)
        finished=self.f.read(fx.CELL)
        self.assertTrue(
            finished['planner_control']['runtime']['pending_role_outputs'][0]['done_observed_at']
        )

    def test_worker_helper_planner_return_cannot_exit_before_result_is_routed(self):
        helper_id='worker-helper-planner-unrouted-001'
        output_ref=f'evidence/{fx.TASK}/roles/helper/{helper_id}.json'
        cell=copy.deepcopy(self.f.cell)
        cell['roles']['helper']={
            **cell['roles']['helper'],
            'request_id':helper_id,
            'conversation_id':'helper-worker-unrouted',
        }
        cell['planner_control']['runtime']['pending_role_outputs']=[{
            'role':'helper',
            'kind':'worker_helper_result',
            'request_id':helper_id,
            'challenge':'worker-helper-unrouted-challenge',
            'artifact_ref':output_ref,
            'state':'WAITING',
            'decision_ref':'worker-watchdog-unrouted',
            'helper_source':'worker_watchdog',
        }]
        self.f.commit({
            fx.CELL:cell,
            output_ref:{
                'diagnosis':'Planner return required',
                'repair_result':'not routed yet',
                'return_target':'planner',
                'turn_signal':'done',
            },
        })
        result=rt.sync_semantic_turn(self.f.store,{
            **self.f.api,
            'role':'helper',
            'task_id':fx.TASK,
            'control_epoch':1,
            'conversation_id':'helper-worker-unrouted',
            'request_id':helper_id,
            'output_ref':output_ref,
        })
        self.assertFalse(result['accepted'],result)
        self.assertEqual(result['reason'],'worker_helper_return_path_unresolved')

    def test_worker_helper_done_is_rejected_before_replacement_worker_starts(self):
        helper_id='worker-helper-gate-001'
        output_ref=f'evidence/{fx.TASK}/roles/helper/{helper_id}.json'
        cell=copy.deepcopy(self.f.cell)
        cell['roles']['helper']={
            **cell['roles']['helper'],
            'request_id':helper_id,
            'conversation_id':'helper-worker-gate',
        }
        control=cell['planner_control']
        control['activity']='PARKED_WAIT_EVENT'
        control['wait']={'kind':'WAIT_WORKER_HELPER','selector':{'output_ref':output_ref},'refs':[]}
        control['runtime']['pending_role_outputs']=[{
            'role':'helper',
            'kind':'worker_helper_result',
            'request_id':helper_id,
            'challenge':'worker-helper-gate-challenge',
            'artifact_ref':output_ref,
            'state':'RETRY_STAGED',
            'decision_ref':'worker-watchdog-gate',
            'helper_source':'worker_watchdog',
            'retry_request_id':'worker-retry-gate-001',
            'return_mode':'WORKER_SAME_GENERATION',
            'recovery_action':'same_generation_retry',
        }]
        self.f.commit({
            fx.CELL:cell,
            'state/chatgpt.json':{'v':1,'control_request':{
                'v':1,
                'kind':'worker_same_generation_retry',
                'request_id':'worker-retry-gate-001',
                'task_id':fx.TASK,
                'control_epoch':1,
                'role':'helper',
                'challenge':'worker-retry-gate-challenge',
                'task_cell_project_key':'g-p-task-cell',
                'status':'APPLYING',
            }},
            output_ref:{
                'diagnosis':'old physical Worker was deleted',
                'repair_result':'same-generation retry staged but replacement has not response-started yet',
                'return_target':'worker',
                'recovery_action':'same_generation_retry',
                'turn_signal':'done',
            },
        })

        result=rt.sync_semantic_turn(self.f.store,{
            **self.f.api,
            'role':'helper',
            'task_id':fx.TASK,
            'control_epoch':1,
            'conversation_id':'helper-worker-gate',
            'request_id':helper_id,
            'output_ref':output_ref,
        })
        self.assertFalse(result['accepted'],result)
        self.assertEqual(result['reason'],'worker_helper_next_worker_not_started')

    def test_paused_task_does_not_gain_timeout_helper(self):
        self.cell['status']='PAUSED_BY_USER';self.save();self.f.tick()
        self.assertEqual(self.f.read('state/chatgpt.json')['control_request']['role'],'planner')
        cell=self.f.read(fx.CELL)
        self.assertEqual(cell['planner_control']['runtime'].get('pending_role_requests') or [],[])

class BrowserRecoveryTests(unittest.TestCase):
    def test_control_dispatch_does_not_starve_watchdog(self):
        rt=BrowserRuntime.__new__(BrowserRuntime);rt.bridge=MagicMock()
        rt.bridge.call.side_effect=lambda op,**kw: {'control_request':{'kind':'fixture','status':'PENDING'}} if op=='control_status' else {'ok':True}
        rt._bound_semantic_turns=MagicMock(return_value=[]);rt._finish_git_turns=MagicMock();rt._process_final_delivery=MagicMock();rt._planner_watchdog_sweep=MagicMock(return_value=[]);rt._dispatch_control_request=MagicMock(return_value={'ok':False})
        rt.process_control()
        self.assertIn('planner_runtime_tick',[c.args[0] for c in rt.bridge.call.call_args_list])

    def test_closed_page_error_does_not_kill_loop(self):
        rt=BrowserRuntime.__new__(BrowserRuntime);rt.connect=MagicMock();rt.close=MagicMock();rt.ui=MagicMock();rt.bridge=MagicMock()
        rt.tick=MagicMock(side_effect=[RuntimeError('Target page has been closed'),KeyboardInterrupt])
        with patch('playwright_host.runtime.time.sleep'),self.assertRaises(KeyboardInterrupt):rt.run_forever()
        self.assertEqual(rt.tick.call_count,2);rt.bridge.event.assert_called()

class HelperSemanticCleanupTests(unittest.TestCase):
    def setUp(self):
        self.f=fx.RuntimeGitTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_accepted_git_helper_result_deletes_without_response_end(self):
        rt=BrowserRuntime.__new__(BrowserRuntime)
        rt.cfg=MagicMock(project_id='git-agent-harness')
        rt.bridge=MagicMock()
        rt.bridge.call.side_effect=lambda op,**kw: (
            {'ok':True,'cleaned':True}
            if op=='helper_cleanup_complete'
            else {'ok':False,'error':'unexpected:'+op}
        )
        rt.state=MagicMock()
        rt.state.data={'task_cell':{'roles':{'helper':{
            'role':'helper','task_id':'task-001','control_epoch':1,
            'conversation_id':'helper-conv-001','active_request_id':'helper-request-001',
            'active_output_ref':'evidence/task-001/helper.json'
        }},'planner_successors':{}}}
        page=MagicMock()
        page.locator.return_value.count.return_value=0
        rt.ui=MagicMock()
        rt.ui.find_page.return_value=page
        rt.ui.marker_state.return_value={
            'response_ended':False,
            'assistant_texts':[]
        }
        rt._delete_exact=MagicMock(return_value=True)

        record=rt.state.data['task_cell']['roles']['helper']
        result=rt._finish_helper_output(record, {'ok':True,'accepted':True})

        self.assertTrue(result['accepted'])
        self.assertTrue(result['helper_deleted'])
        rt._delete_exact.assert_called_once()
        rt.state.clear_role.assert_called_once_with('task-001',1,'helper','helper-conv-001')
        self.assertIn('helper_cleanup_complete',[call.args[0] for call in rt.bridge.call.call_args_list])
        page.locator.assert_not_called()
        self.assertFalse(hasattr(BrowserRuntime,'_observe_helper_outputs'))

    def test_physical_delete_keeps_local_cleanup_receipt_when_canonical_clear_fails(self):
        runtime=BrowserRuntime.__new__(BrowserRuntime)
        runtime.cfg=MagicMock(project_id='git-agent-harness')
        runtime.bridge=MagicMock()
        def bridge_call(op, **kw):
            if op == 'helper_cleanup_complete':
                raise BridgeError('helper_cleanup_complete: GIT_TEMPORARY')
            return {'ok':False,'error':'unexpected:'+op}
        runtime.bridge.call.side_effect=bridge_call
        runtime.state=MagicMock()
        role={
            'role':'helper','task_id':'task-001','control_epoch':1,
            'conversation_id':'helper-conv-cleanup-pending',
            'request_id':'helper-request-cleanup-pending',
            'active_request_id':'helper-request-cleanup-pending',
            'active_output_ref':'evidence/task-001/helper-cleanup-pending.json',
        }
        runtime.state.data={'task_cell':{'roles':{'helper':role},'planner_successors':{}}}
        page=MagicMock()
        page.locator.return_value.count.return_value=0
        runtime.ui=MagicMock()
        runtime.ui.find_page.return_value=page
        runtime.ui.marker_state.return_value={
            'response_ended':False,
            'assistant_texts':[]
        }
        runtime._delete_exact=MagicMock(return_value=True)

        record=runtime.state.data['task_cell']['roles']['helper']
        result=runtime._finish_helper_output(record, {'ok':True,'accepted':True})

        self.assertTrue(result['accepted'])
        self.assertTrue(result['helper_deleted'])
        self.assertFalse(result['canonical_helper_cleaned'])
        runtime.state.clear_role.assert_not_called()
        retained=runtime.state.set_role.call_args.args[0]
        self.assertTrue(retained['canonical_cleanup_pending'])
        self.assertEqual(retained['conversation_id'],'helper-conv-cleanup-pending')

    def test_pending_canonical_helper_cleanup_retries_without_browser_page(self):
        runtime=BrowserRuntime.__new__(BrowserRuntime)
        runtime.bridge=MagicMock()
        runtime.bridge.call.return_value={'ok':True,'cleaned':True}
        runtime.state=MagicMock()
        runtime.state.data={'task_cell':{'roles':{'helper':{
            'role':'helper','task_id':'task-001','control_epoch':1,
            'conversation_id':'helper-conv-cleanup-pending',
            'request_id':'helper-request-cleanup-pending',
            'active_request_id':'helper-request-cleanup-pending',
            'active_output_ref':'evidence/task-001/helper-cleanup-pending.json',
            'canonical_cleanup_pending':True,
        }},'planner_successors':{}}}
        runtime.ui=MagicMock()

        record=runtime.state.data['task_cell']['roles']['helper']
        result=runtime._finish_helper_output(record, {'ok':True,'accepted':True})

        self.assertTrue(result['canonical_helper_cleaned'])
        runtime.ui.find_page.assert_not_called()
        runtime.state.clear_role.assert_called_once_with(
            'task-001',1,'helper','helper-conv-cleanup-pending'
        )

    def test_canonical_helper_cleanup_removes_only_exact_completed_binding(self):
        helper_id='helper-cleanup-canonical-001'
        output_ref=f'evidence/{fx.TASK}/roles/helper/{helper_id}.json'
        cell=copy.deepcopy(self.f.cell)
        cell['roles']['helper']={
            **cell['roles']['helper'],
            'request_id':helper_id,
            'conversation_id':'helper-cleanup-canonical-conv',
        }
        cell['planner_control']['runtime']['pending_role_outputs']=[{
            'role':'helper',
            'kind':'helper_result',
            'request_id':helper_id,
            'artifact_ref':output_ref,
            'state':'EMITTED',
            'done_observed_at':'2026-09-29T00:00:00+00:00',
        }]
        self.f.commit({fx.CELL:cell})
        result=rt.complete_helper_cleanup(self.f.store,{
            **self.f.api,
            'task_id':fx.TASK,
            'control_epoch':1,
            'conversation_id':'helper-cleanup-canonical-conv',
            'request_id':helper_id,
            'output_ref':output_ref,
        })
        self.assertTrue(result['ok'],result)
        self.assertTrue(result['cleaned'],result)
        self.assertNotIn('helper',self.f.read(fx.CELL)['roles'])

    def test_canonical_helper_cleanup_rejects_wrong_conversation_without_removing_binding(self):
        helper_id='helper-cleanup-canonical-002'
        output_ref=f'evidence/{fx.TASK}/roles/helper/{helper_id}.json'
        cell=copy.deepcopy(self.f.cell)
        cell['roles']['helper']={
            **cell['roles']['helper'],
            'request_id':helper_id,
            'conversation_id':'helper-cleanup-canonical-real',
        }
        cell['planner_control']['runtime']['pending_role_outputs']=[{
            'role':'helper',
            'kind':'helper_result',
            'request_id':helper_id,
            'artifact_ref':output_ref,
            'state':'EMITTED',
            'done_observed_at':'2026-09-29T00:00:00+00:00',
        }]
        self.f.commit({fx.CELL:cell})
        result=rt.complete_helper_cleanup(self.f.store,{
            **self.f.api,
            'task_id':fx.TASK,
            'control_epoch':1,
            'conversation_id':'helper-cleanup-canonical-wrong',
            'request_id':helper_id,
            'output_ref':output_ref,
        })
        self.assertFalse(result['ok'],result)
        self.assertEqual(result['error'],'HELPER_CLEANUP_BINDING_MISMATCH')
        self.assertEqual(
            self.f.read(fx.CELL)['roles']['helper']['conversation_id'],
            'helper-cleanup-canonical-real',
        )


class HelperWakeTests(unittest.TestCase):
    def test_wake_assigns_active_incident_ownership(self):
        prompt = _planner_helper_prompt('Inspect incident evidence/incident.json.', 'evidence/result.json', 'fixture-branch')
        for phrase in (
            'Act as HELPER',
            'ACTIVE RECOVERY REQUIRED',
            'FIRST CHECK RUNNERS BEFORE ANY RECOVERY MUTATION',
            'cah-shot is the normal bounded rescue/control channel',
            '__CAH_COLD_RUNNER_NAME__',
            '__CAH_COLD_RUNNER_ROOT__',
            'Planner may require 02 open/keep/release',
            'alternate emergency/control channel',
            'There is no required backup-runner wrapper protocol',
            'Never commandeer ACTIVE_BUSY 02',
            'never move Managed workload onto cah-shot',
            'PLANNER AUTHORITY',
            'highest autonomous task-level semantic authority below Foreground/user intent',
            'MUST execute it after verifying exact target/ownership/safety',
            'TWO MANDATORY MISSIONS',
            'STOP repeated diagnosis and switch to mutation',
            'Helper MUST NOT request exit',
            'Verify that the next Harness reconciliation can proceed without another manual correction',
        ):
            self.assertIn(phrase, prompt)
        self.assertIn('fixture-branch', prompt)
        self.assertIn('evidence/result.json', prompt)

    def test_bound_input_recovery_uses_canonical_lifecycle_not_chat_tool(self):
        prompt = _planner_helper_prompt('Recover this stopped Planner input.', 'evidence/result.json')
        self.assertIn('restore the same retained control request/event', prompt)
        self.assertIn('Harness re-delivers it mechanically', prompt)
        self.assertNotIn('CAH_TOOL_CALL', prompt)
        self.assertNotIn('CAH_TOOL_RESULT', prompt)
        self.assertNotIn('browser.recover_input', prompt)

    def test_other_incidents_keep_same_active_recovery_contract(self):
        prompt = _planner_helper_prompt('Inspect a Worker deletion failure.', 'evidence/result.json')
        self.assertIn('TWO MANDATORY MISSIONS', prompt)
        self.assertIn('closure-complete', prompt)
        self.assertNotIn('TOOL_SYSTEM', prompt)

    def test_worker_watchdog_helper_is_visibly_distinct_from_planner_helper(self):
        prompt = _worker_helper_prompt(
            'Inspect exact Worker generation 32 after watchdog expiry.',
            'evidence/worker-helper.json',
            'fixture-branch',
        )
        for phrase in (
            'WORKER_HELPER SOURCE=PLAYWRIGHT_WATCHDOG',
            'managed-Worker 30-minute watchdog incident',
            'Worker watchdog section is authoritative',
            'HIGH-PRIORITY WORKER RULES',
            'REVIEW BOUNDARY',
            'g5/g10/g15',
            'exact current Reply + Result',
            'existing normal W->P finalize/review path',
            'same_generation_retry',
            'WORKER_HELPER INCIDENT',
            'return_target=planner',
            'coupled parent wait/activity',
            'Worker/Planner pointers',
        ):
            self.assertIn(phrase, prompt)
        self.assertLess(len(prompt), 9000)
        self.assertNotIn('PLANNER AUTHORITY', prompt)
        self.assertNotIn('normal ACK/RUNNING admission', prompt)
        self.assertNotIn('Planner wake verification requires the helper_result event', prompt)


from local_bridge import planner_runtime as rt
from playwright_host.ui import conversation_key

class InitialPlannerDeliveryFailureTests(unittest.TestCase):
    def setUp(self):
        self.f = fx.RuntimeGitTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.request = dict(
            self.f.request,
            kind='task_cell_planner_foreground_handoff',
            phase='CREATE_INITIAL_PLANNER',
            status='PENDING',
            planner_request_id='initial-planner-wake-0001',
            planner_generation=1,
            planner_fence_token='initial-fence-0001',
            create_attempts=5,
            planner_challenge='initial-challenge-0001',
            initial_turn={'doorbell_id':'initial-doorbell-0001'},
        )
        self.cell = {k: copy.deepcopy(v) for k, v in self.f.cell.items() if k != 'planner_control'}
        self.cell.update(status='BOOTSTRAPPING', roles={})
        self.f.commit({fx.CELL:self.cell, 'state/chatgpt.json':{'control_request':self.request}})

    def test_exhausted_initial_delivery_stays_explicit_error_and_never_stages_helper(self):
        result = rt.record_foreground_planner_create_attempt(
            self.f.store,
            {**self.f.api, 'task_id':fx.TASK, 'source_request_id':self.request['request_id']},
        )
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error'], 'INITIAL_PLANNER_CREATE_RETRY_EXHAUSTED')

        state = self.f.read('state/chatgpt.json')
        control = state['control_request']
        self.assertEqual(control['status'], 'ERROR')
        self.assertEqual(control['error'], 'INITIAL_PLANNER_CREATE_RETRY_EXHAUSTED')
        self.assertEqual(control['create_attempts'], 5)

        cell = self.f.read(fx.CELL)
        self.assertNotIn('helper', cell.get('roles') or {})
        self.assertNotIn('planner_control', cell)

        self.f.tick()
        cell_after = self.f.read(fx.CELL)
        self.assertNotIn('helper', cell_after.get('roles') or {})
        self.assertNotIn('planner_control', cell_after)


class InitialPlannerHostFailureTests(unittest.TestCase):
    def test_exhausted_attempt_result_prevents_another_planner_conversation(self):
        from playwright_host.config import TASK_CELL

        runtime = BrowserRuntime.__new__(BrowserRuntime)
        runtime.bridge = MagicMock()
        runtime.ui = MagicMock()
        runtime.state = MagicMock()
        runtime._arm_planner_watchdog = MagicMock()
        runtime.bridge.call.return_value = {
            'ok': False,
            'error': 'INITIAL_PLANNER_CREATE_RETRY_EXHAUSTED',
        }
        control = {
            'request_id':'foreground-handoff-001',
            'task_id':'task-001',
            'control_epoch':1,
            'task_cell_project_key':TASK_CELL['project_key'],
            'phase':'CREATE_INITIAL_PLANNER',
            'planner_request_id':'initial-planner-wake-001',
            'planner_challenge':'initial-planner-challenge',
            'planner_generation':1,
            'planner_fence_token':'initial-planner-fence-001',
            'required_output_ref':'evidence/task-001/planner-turn.json',
            'prompt':'initial Planner work',
        }

        with self.assertRaisesRegex(RuntimeError, 'INITIAL_PLANNER_CREATE_RETRY_EXHAUSTED'):
            runtime._handle_planner_foreground_handoff(control)

        runtime.ui.bootstrap_conversation.assert_not_called()
        runtime.bridge.call.assert_called_once_with(
            'planner_foreground_handoff_attempt',
            source_request_id='foreground-handoff-001',
            task_id='task-001',
        )


class BootstrapTargetTests(unittest.TestCase):
    def test_temporary_url_is_not_a_truncated_conversation_key(self):
        url='https://chatgpt.com/g/g-p-test-project/c/local-chatgpt%3Atemporary-123'
        self.assertNotEqual(conversation_key(url),'c:local-chatgpt')

    def test_target_must_match_project_and_original_marker(self):
        ui=ChatGPTUI('http://unused');ui.context=MagicMock()
        wrong=MagicMock();wrong.url='https://chatgpt.com/g/g-p-other/project'
        target=MagicMock();target.url='https://chatgpt.com/g/g-p-test/c/local-chatgpt%3Atemporary-123'
        ui.context.pages=[wrong,target];ui.marker_state=MagicMock(return_value={'marker_visible':True})
        self.assertIs(ui.bootstrap_page('g-p-test','WAKE'),target)
        ui.context.pages=[target,target]
        with self.assertRaisesRegex(RuntimeError,'BOOTSTRAP_TARGET_AMBIGUOUS'):ui.bootstrap_page('g-p-test','WAKE')


class HelperPostAcceptTests(unittest.TestCase):
    """AI owns exit timing; accepted completion restores only this incident."""
    def setUp(self):
        from playwright_host.state import HostState
        from playwright_host.worker import WorkerManager
        self.f=fx.RuntimeGitTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.helper_id='helper-resume-current'
        self.output=f'evidence/{fx.TASK}/roles/helper/{self.helper_id}.json'
        self.recovery={
            'parent_task_id':fx.TASK,'parent_control_epoch':1,
            'child_task_id':'child-resume-current','backend_cl':'cl/child-resume-current.backend.json',
            'dispatch_id':'dispatch-resume-current','dispatch_generation':7,'fence_token':'fence-resume-current',
            'worker_ref':'pool-resume-current','wake_id':'wake-resume-current',
            'old_conversation_id':'worker-resume-current','lane_id':'lane-00','worker_project_key':'g-p-worker',
        }
        self.cell=copy.deepcopy(self.f.cell)
        self.cell['roles']['helper'].update(request_id=self.helper_id,conversation_id='helper-current')
        pc=self.cell['planner_control'];pc['activity']='PARKED_WAIT_EVENT'
        pc['wait']={'kind':'WAIT_WORKER_HELPER','selector':{'output_ref':self.output},'refs':[]}
        pc['runtime']['pending_role_outputs']=[{
            'role':'helper','kind':'worker_helper_result','request_id':self.helper_id,
            'artifact_ref':self.output,'state':'WORKER_STARTED','return_mode':'WORKER_SAME_GENERATION',
            'recovery_action':'resume_current','recovery':self.recovery,
        }]
        pc['runtime']['owned_children']=[{**self.recovery,'event_state':'WAITING'}]
        self.backend={'task_id':'child-resume-current','overall':'RUNNING','dispatch':{
            'state':'RUNNING','generation':7,'dispatch_id':self.recovery['dispatch_id'],
            'fence_token':self.recovery['fence_token'],'wake_id':self.recovery['wake_id'],
            'acked_by_worker_ref':self.recovery['worker_ref']}}
        self.f.commit({fx.CELL:self.cell,self.recovery['backend_cl']:self.backend,
            'tasks/child-resume-current.json':{'task_id':'child-resume-current','kind':'planner_worker_child'},
            self.output:{'diagnosis':'runtime inspected','repair_result':'current Worker resumed','turn_signal':'done'}})
        state=HostState(self.f.root/'host.json')
        state.data['lanes']={'lane-00':{'lane_id':'lane-00','project_key':'g-p-worker',
            'project_root_url':'https://chatgpt.com/g/g-p-worker/project','enabled':True,WORKER_LINEAGES_KEY:{}}}
        self.current={'conversation_id':'worker-resume-current','handoff_id':'pool-resume-current',
            'wake_id':'wake-resume-current','status':'current','handoff_verified':True,
            'worker_watchdog':{**self.recovery,'state':'WORKER_HELPER_REQUESTED',
                'helper_request_id':self.helper_id,'started_at':'2000-01-01T00:00:00+00:00',
                'deadline_at':'2000-01-01T00:30:00+00:00'}}
        state.set_lineage('lane-00',{'owner_task_id':fx.TASK,'owner_control_epoch':1,
            'current':self.current,'managed':[self.current]})
        state.set_role({'role':'helper','task_id':fx.TASK,'control_epoch':1,'request_id':self.helper_id,
            'conversation_id':'helper-current','active_request_id':self.helper_id,'active_output_ref':self.output})
        self.host=BrowserRuntime.__new__(BrowserRuntime);self.host.state=state
        self.host.cfg=MagicMock(project_id='git-agent-harness')
        self.host.ui=MagicMock();self.host.bridge=MagicMock()
        def call(op,**kw):
            if op=='helper_cleanup_complete':return rt.complete_helper_cleanup(self.f.store,{**self.f.api,**kw})
            raise AssertionError(op)
        self.host.bridge.call.side_effect=call
        self.host.ui.marker_state.side_effect=AssertionError('Git completion must not inspect DOM')
        self.host.ui.find_page.return_value.locator.return_value.count.return_value=0
        self.host._delete_exact=MagicMock(return_value=True)
        self.host.workers=WorkerManager.__new__(WorkerManager)
        self.host.workers.state=state

    def finish_from_git(self):
        record=self.host.state.get_role(fx.TASK,1,'helper')
        result=rt.sync_semantic_turn(self.f.store, {**self.f.api,'role':'helper',
            'task_id':fx.TASK,'control_epoch':1,'conversation_id':record['conversation_id'],
            'request_id':record['active_request_id'],'output_ref':record['active_output_ref']})
        if result.get('accepted'):
            return self.host._finish_helper_output(record,result)
        return result

    def watchdog(self):
        return self.host.state.get_lineage('lane-00',fx.TASK,1)['current']['worker_watchdog']

    def test_accepted_done_restores_wait_and_watchdog_before_helper_cleanup(self):
        from datetime import datetime
        result=self.finish_from_git()
        self.assertTrue(result['accepted'],result)
        wd=self.watchdog()
        self.assertEqual(wd['state'],'ARMED')
        self.assertIsNone(wd['helper_request_id'])
        self.assertEqual((datetime.fromisoformat(wd['deadline_at'])-datetime.fromisoformat(wd['started_at'])).total_seconds(),1800)
        after=self.f.read(fx.CELL)
        self.assertEqual(after['planner_control']['wait']['kind'],'WAIT_RESULT')
        self.assertNotIn('helper',after['roles'])
        self.assertEqual(after['planner_control']['authority'],self.cell['planner_control']['authority'])
        self.assertEqual(self.f.read(self.recovery['backend_cl']),self.backend)
        self.host.ui.bootstrap_conversation.assert_not_called()

    def test_cleanup_retry_does_not_extend_an_already_rearmed_deadline(self):
        self.host._delete_exact.return_value=False
        self.finish_from_git()
        before=copy.deepcopy(self.watchdog())
        self.finish_from_git()
        self.assertEqual(self.watchdog(),before)
        self.assertEqual(self.host._delete_exact.call_count,2)

    def test_accepted_done_does_not_resume_user_wait_or_a_replacement_worker(self):
        self.cell['planner_control'].update(activity='WAIT_USER',wait={'kind':'NEED_USER','selector':{},'refs':[]})
        self.f.commit({fx.CELL:self.cell})
        result=self.finish_from_git()
        self.assertTrue(result['accepted'],result)
        after=self.f.read(fx.CELL)['planner_control']
        self.assertEqual(after['activity'],'WAIT_USER')
        self.assertEqual(after['wait']['kind'],'NEED_USER')
        self.assertEqual(self.watchdog()['state'],'DISARMED')
        # A delayed completion for an old conversation cannot touch its successor.
        lineage=self.host.state.get_lineage('lane-00',fx.TASK,1)
        current={**lineage['current'],'conversation_id':'new-worker'}
        self.host.state.set_lineage('lane-00',{**lineage,'current':current,'managed':[current]})
        before=copy.deepcopy(current)
        self.host.workers.restore_after_helper(self.helper_id,{**self.recovery,'watchdog_state':'ARMED'})
        self.assertEqual(self.host.state.get_lineage('lane-00',fx.TASK,1)['current'],before)

    def test_rearmed_cycle_has_a_new_incident_without_changing_generation(self):
        req={**self.f.api,**self.recovery,'conversation_id':self.recovery['old_conversation_id'],
             'watchdog_started_at':'2000-01-01T00:00:00+00:00'}
        first=rt.stage_worker_watchdog_helper(self.f.store,req)
        self.assertTrue(first['ok'],first)
        duplicate=rt.stage_worker_watchdog_helper(self.f.store,req)
        self.assertTrue(duplicate.get('duplicate'),duplicate)
        later=rt.stage_worker_watchdog_helper(self.f.store,{**req,'watchdog_started_at':'2000-01-01T01:00:00+00:00'})
        self.assertTrue(later['ok'],later)
        self.assertNotEqual(first['helper_request_id'],later['helper_request_id'])
        self.assertEqual(self.f.read(self.recovery['backend_cl']),self.backend)

if __name__=='__main__':unittest.main()
