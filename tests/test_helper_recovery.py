import copy, importlib.util, json, sys, unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO))
spec=importlib.util.spec_from_file_location('lean_fixtures', REPO/'tests/test_taskcell_lean.py')
fx=importlib.util.module_from_spec(spec);spec.loader.exec_module(fx)
from playwright_host.runtime import BrowserRuntime
from playwright_host.ui import ChatGPTUI
from local_bridge.planner_runtime import _planner_helper_prompt, _worker_helper_prompt

class GitRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f=fx.RuntimeGitTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.cell=copy.deepcopy(self.f.cell)
        self.cell['roles'].pop('helper')
        self.cell['planner_control']['activity']='WAKING'
        self.pending={'kind':'PLANNER_TURN','ref':self.f.request['required_output_ref'],'request_id':self.f.request['request_id'],'conversation_id':'planner-old','status':'WAITING','started_at':'2000-01-01T00:00:00Z','deadline_at':'2000-01-01T00:05:00Z','wake_prompt':'the exact original work order'}
        self.cell['planner_control']['runtime']['pending_output']=self.pending
        self.state={'control_request':{**self.f.request,'status':'DONE'}}
        self.save()
    def save(self):self.f.commit({fx.CELL:self.cell,'state/chatgpt.json':self.state})
    def test_expired_output_wakes_helper_without_planner_approval_once(self):
        result=self.f.tick()
        self.assertEqual(result.get('action'),'planner_output_helper_staged',result)
        cell=self.f.read(fx.CELL);control=self.f.read('state/chatgpt.json')['control_request']
        self.assertEqual(control['role'],'helper')
        self.assertEqual(cell['planner_control']['authority'],self.cell['planner_control']['authority'])
        self.assertEqual(cell['planner_control']['runtime']['pending_output'],self.pending)
        recovery=cell['planner_control']['runtime']['pending_role_requests'][0]['recovery']
        self.assertEqual(recovery['prompt'],'the exact original work order')
        self.assertEqual(recovery['conversation_id'],'planner-old')
        self.assertEqual(len(cell['planner_control']['runtime']['pending_role_requests']),1)
        self.assertEqual(self.f.tick().get('idle'),'control_slot_busy')
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
            },
        })

        result=self.f.tick()
        self.assertEqual(result.get('action'),'worker_helper_result_ready',result)
        self.assertEqual(result.get('return_target'),'worker')
        routed=self.f.read(fx.CELL)
        pending=routed['planner_control']['runtime']['pending_role_outputs'][0]
        self.assertEqual(pending['state'],'RESULT_READY')
        self.assertEqual(pending['return_mode'],'WORKER_SAME_GENERATION')
        inbox=(routed['planner_control'].get('inbox') or {}).get('events') or {}
        self.assertFalse(any(
            isinstance(event,dict) and event.get('kind') in {'helper_result','worker_helper_result'}
            for event in inbox.values()
        ))

    def test_fresh_or_paused_task_does_not_wake_helper(self):
        self.pending['deadline_at']='2999-01-01T00:00:00Z';self.save();self.f.tick()
        self.assertEqual(self.f.read('state/chatgpt.json')['control_request']['role'],'planner')
        self.pending['deadline_at']='2000-01-01T00:00:00Z';self.cell['status']='PAUSED_BY_USER';self.save();self.f.tick()
        self.assertEqual(self.f.read('state/chatgpt.json')['control_request']['role'],'planner')

class BrowserRecoveryTests(unittest.TestCase):
    def test_control_dispatch_does_not_starve_watchdog(self):
        rt=BrowserRuntime.__new__(BrowserRuntime);rt.bridge=MagicMock()
        rt.bridge.call.side_effect=lambda op,**kw: {'control_request':{'kind':'fixture','status':'PENDING'}} if op=='control_status' else {'ok':True}
        rt._process_final_delivery=MagicMock();rt._dispatch_control_request=MagicMock(return_value={'ok':False})
        rt.process_control()
        self.assertIn('planner_runtime_tick',[c.args[0] for c in rt.bridge.call.call_args_list])

    def test_closed_page_error_does_not_kill_loop(self):
        rt=BrowserRuntime.__new__(BrowserRuntime);rt.connect=MagicMock();rt.close=MagicMock();rt.ui=MagicMock();rt.bridge=MagicMock()
        rt.tick=MagicMock(side_effect=[RuntimeError('Target page has been closed'),KeyboardInterrupt])
        with patch('playwright_host.runtime.time.sleep'),self.assertRaises(KeyboardInterrupt):rt.run_forever()
        self.assertEqual(rt.tick.call_count,2);rt.bridge.event.assert_called()

class HelperSemanticCleanupTests(unittest.TestCase):
    def test_accepted_helper_sync_deletes_without_response_end(self):
        rt=BrowserRuntime.__new__(BrowserRuntime)
        rt.cfg=MagicMock(project_id='git-agent-harness')
        rt._seen_role_syscalls=set()
        rt.bridge=MagicMock()
        rt.bridge.call.return_value={'ok':True,'accepted':True,'role':'helper'}
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
            'assistant_texts':['GAH_SYSCALL_BEGIN\n{"v":1,"kind":"semantic_sync","call_id":"semantic-sync-001"}\nGAH_SYSCALL_END']
        }
        rt._delete_exact=MagicMock(return_value=True)

        result=rt._process_role_syscalls()

        self.assertEqual(len(result),1)
        self.assertTrue(result[0]['accepted'])
        self.assertTrue(result[0]['result']['helper_deleted'])
        rt._delete_exact.assert_called_once()
        rt.state.clear_role.assert_called_once_with('task-001',1,'helper','helper-conv-001')
        page.locator.assert_not_called()
        self.assertFalse(hasattr(BrowserRuntime,'_observe_helper_outputs'))


class HelperWakeTests(unittest.TestCase):
    def test_wake_assigns_active_incident_ownership(self):
        prompt = _planner_helper_prompt('Inspect incident evidence/incident.json.', 'evidence/result.json', 'fixture-branch')
        for phrase in (
            'Act as HELPER',
            'ACTIVE RECOVERY REQUIRED',
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
            '30-minute watchdog',
            'NOT a Planner-requested Helper turn',
            'WORKER_HELPER RECOVERY ORDER',
            'exact-delete that physical Worker conversation',
            'HELPER INCIDENT',
            'ORIGINAL SAME-GENERATION wake/binding',
            'Do not synthesize G+1',
            'worker_helper_result event',
        ):
            self.assertIn(phrase, prompt)

from local_bridge import planner_runtime as rt
from playwright_host.ui import conversation_key

class BootstrapRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fx.RuntimeGitTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.request = dict(self.f.request, kind='task_cell_planner_foreground_handoff',
            phase='CREATE_INITIAL_PLANNER', planner_request_id='initial-planner-wake-0001',
            planner_generation=1, planner_fence_token='initial-fence-0001', create_attempts=3,
            planner_challenge='initial-challenge-0001', initial_turn={'doorbell_id':'initial-doorbell-0001'})
        self.cell = {k: copy.deepcopy(v) for k, v in self.f.cell.items() if k != 'planner_control'}
        self.cell.update(status='BOOTSTRAPPING', roles={})
        self.f.commit({fx.CELL:self.cell, 'state/chatgpt.json':{'control_request':self.request}})

    def stage(self):
        result = self.f.tick()
        self.assertEqual(result.get('action'), 'planner_bootstrap_helper_staged', result)
        return self.f.read(fx.CELL), self.f.read('state/chatgpt.json')['control_request']

    def test_expired_initial_delivery_stages_helper_without_fabricated_authority(self):
        cell, helper = self.stage()
        self.assertEqual(helper['role'], 'helper')
        self.assertNotIn('authority', cell['planner_control'])
        self.assertFalse(cell['planner_control']['enabled'])
        incident = cell['planner_control']['runtime']['pending_role_requests'][0]
        self.assertEqual(incident['recovery']['request_id'], self.request['planner_request_id'])
        self.assertEqual(incident['recovery']['prompt'], self.request['prompt'])
        self.assertEqual(cell['bootstrap_recovery']['control_request'], self.request)
        self.assertEqual(self.f.tick().get('idle'), 'control_slot_busy')

    def test_exhausted_initial_delivery_is_not_silently_abandoned(self):
        self.request.update(status='ERROR', error='INITIAL_PLANNER_CREATE_RETRY_EXHAUSTED', create_attempts=5)
        self.f.commit({'state/chatgpt.json': {'control_request':self.request}})
        self.stage()

    def test_pause_or_fresh_request_does_not_escalate(self):
        for change in ({'status':'PAUSED_BY_USER'}, {'control_epoch':2}):
            self.f.commit({fx.CELL:{**self.cell, **change}})
            self.assertEqual(self.f.tick().get('idle'), 'control_slot_busy')
        self.request['requested_at']='2999-01-01T00:00:00Z'
        self.f.commit({fx.CELL:self.cell, 'state/chatgpt.json':{'control_request':self.request}})
        self.assertEqual(self.f.tick().get('idle'), 'control_slot_busy')

    def test_result_ready_restores_bootstrap_before_helper_done(self):
        cell, helper = self.stage()
        cell['roles']['helper']={
            'role':'helper',
            'task_id':fx.TASK,
            'control_epoch':1,
            'conversation_id':'helper-current',
            'request_id':helper['request_id'],
        }
        self.f.commit({
            fx.CELL:cell,
            'state/chatgpt.json':{'control_request':{**helper,'status':'DONE'}},
            helper['required_output_ref']:{
                'diagnosis':'stopped',
                'repair_result':'same original input resumed',
            },
        })

        routed=self.f.tick()
        self.assertEqual(routed.get('action'),'bootstrap_helper_result_routed',routed)
        resumed=self.f.read('state/chatgpt.json')['control_request']
        for key in ('request_id','planner_request_id','prompt','required_output_ref','create_attempts'):
            self.assertEqual(resumed[key], self.request[key])
        self.assertEqual(resumed['status'],'PENDING')
        self.assertTrue(resumed['bootstrap_recovery_ref'])
        routed_cell=self.f.read(fx.CELL)
        self.assertEqual(routed_cell['bootstrap_recovery']['state'],'RESUMING')
        self.assertEqual(
            routed_cell['planner_control']['runtime']['pending_role_outputs'][0]['state'],
            'EMITTED',
        )
        self.assertNotIn('done_observed_at',routed_cell['planner_control']['runtime']['pending_role_outputs'][0])

        bound=rt.complete_foreground_planner_handoff_binding(self.f.store,{
            **self.f.api,'task_id':fx.TASK,'source_request_id':self.request['request_id'],
            'planner_request_id':self.request['planner_request_id'],'conversation_id':'initial-planner-real',
            'conversation_url':'https://chatgpt.com/g/g-p-task-cell/c/initial-planner-real',
            'task_cell_project_key':'g-p-task-cell','response_started':True})
        self.assertTrue(bound['ok'],bound)
        boundcell=self.f.read(fx.CELL)
        self.assertEqual(boundcell['bootstrap_recovery']['state'],'BOUND')
        self.assertEqual(boundcell['planner_control']['authority']['conversation_id'],'initial-planner-real')

        # Only after the next Planner is really bound/started does Helper add
        # the durable exit flag to the same result.
        result_ref=helper['required_output_ref']
        self.f.commit({result_ref:{
            'diagnosis':'stopped',
            'repair_result':'same original input resumed',
            'turn_signal':'done',
        }})
        req={**self.f.api,'task_id':fx.TASK,'control_epoch':1,
             'conversation_id':'helper-current','request_id':helper['request_id'],
             'output_ref':result_ref,'role':'helper'}
        received=rt.sync_semantic_turn(self.f.store,req)
        self.assertTrue(received['accepted'],received)
        finished=self.f.read(fx.CELL)
        pending=finished['planner_control']['runtime']['pending_role_outputs'][0]
        self.assertEqual(pending['state'],'EMITTED')
        self.assertTrue(pending['done_observed_at'])


class BootstrapTargetTests(unittest.TestCase):
    def test_temporary_url_is_not_a_truncated_conversation_key(self):
        url='https://chatgpt.com/g/g-p-test-project/c/local-chatgpt%3Atemporary-123'
        self.assertNotEqual(conversation_key(url),'c:local-chatgpt')

    def test_resume_never_creates_another_conversation_if_target_disappeared(self):
        ui=ChatGPTUI('http://unused');ui.context=MagicMock();ui.context.pages=[];ui.project_root=MagicMock()
        with self.assertRaisesRegex(RuntimeError,'BOOTSTRAP_TARGET_MISSING'):
            ui.bootstrap_conversation('g-p-test','https://chatgpt.com/g/g-p-test/project','WAKE\noriginal','WAKE',require_existing=True)
        ui.project_root.assert_not_called()

    def test_target_must_match_project_and_original_marker(self):
        ui=ChatGPTUI('http://unused');ui.context=MagicMock()
        wrong=MagicMock();wrong.url='https://chatgpt.com/g/g-p-other/project'
        target=MagicMock();target.url='https://chatgpt.com/g/g-p-test/c/local-chatgpt%3Atemporary-123'
        ui.context.pages=[wrong,target];ui.marker_state=MagicMock(return_value={'marker_visible':True})
        self.assertIs(ui.bootstrap_page('g-p-test','WAKE'),target)
        ui.context.pages=[target,target]
        with self.assertRaisesRegex(RuntimeError,'BOOTSTRAP_TARGET_AMBIGUOUS'):ui.bootstrap_page('g-p-test','WAKE')

if __name__=='__main__':unittest.main()
