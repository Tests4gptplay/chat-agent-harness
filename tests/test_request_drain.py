import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.git_process import run_git
from harness.request_scan import scan
from harness.request_drain import drain, process, latest, receipt_path, transaction
import test_semantic_finalize as semantic_fixtures
import test_parallel_branch_finalize as parallel_fixtures


class DrainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name); self.remote = base/'remote.git'; self.repo = base/'writer'
        run_git(base, 'init', '--bare', str(self.remote)); run_git(base, 'clone', str(self.remote), str(self.repo))
        run_git(self.repo, 'config', 'user.name', 'test'); run_git(self.repo, 'config', 'user.email', 'test@example.invalid')
        run_git(self.repo, 'checkout', '-b', 'main')
        self.put('state/lanes.json', {'lanes':[{'lane_id':'lane-00','project_key':'g-p-test','enabled':True}]})
        self.put('README.md', {'test': True}); self.publish()
        self.emitted = set()
    def put(self, path, value):
        p=self.repo/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value))
    def publish(self):
        run_git(self.repo,'add','-A');run_git(self.repo,'commit','--allow-empty','-m','test');run_git(self.repo,'push','origin','HEAD:main')
    def sync(self):
        h=latest(self.repo,'origin','main');run_git(self.repo,'reset','--hard',h)
    def wake(self, key='a', state='READY'):
        t='t-'+key; d='dispatch-'+key; f='fence-'+key+'-long'; w='wake-'+key+'-long'
        self.put('tasks/'+t+'.json', {'task_id':t,'backend_cl':'cl/'+t+'.json','lane_id':'lane-00','worker_project_key':'g-p-test'})
        self.put('cl/'+t+'.json', {'task_id':t,'dispatch':{'dispatch_id':d,'generation':1,'fence_token':f,'wake_id':w,'state':state}})
        self.put('requests/worker-wake/'+key+'.json', {'task_id':t,'backend_cl':'cl/'+t+'.json','dispatch_id':d,'dispatch_generation':1,'fence_token':f,'wake_id':w,'lane_id':'lane-00','worker_project_key':'g-p-test','state':'NEED_AGENT','result_ref':'tasks/'+t+'.json'})
        self.publish()
    def emit(self, wake):
        self.emitted.add(wake['wake_id']);return {'ok':True,'wake_id':wake['wake_id']}
    def read(self, path):
        return json.loads(run_git(self.repo,'show',f'origin/main:{path}').stdout)
    def test_missing_events_and_repeat_emit_once(self):
        self.wake(); out=drain(self.repo,kinds=['worker_wake'],emitter=self.emit)
        self.assertEqual(out['results'][0]['phase'],'DONE');self.assertEqual(len(self.emitted),1)
        out=drain(self.repo,kinds=['worker_wake'],emitter=self.emit)
        self.assertEqual(out['processed'],0);self.assertEqual(len(self.emitted),1)
    def test_claim_then_crash_replays_exact_idempotent_wake(self):
        self.wake(); record=scan(self.repo,after=latest(self.repo,'origin','main'),kinds=['worker_wake'])[0]
        prepared=process(self.repo,record)
        self.assertEqual(prepared['phase'],'CLAIMED');self.emit(prepared['wake'])
        result=process(self.repo,record,emitter=self.emit)
        self.assertEqual(result['phase'],'DONE');self.assertEqual(len(self.emitted),1)
    def test_paused_and_stale_never_emit(self):
        self.wake(state='WAIT_DEP');out=drain(self.repo,kinds=['worker_wake'],emitter=self.emit)
        self.assertEqual(out['results'][0]['phase'],'SUPERSEDED');self.assertFalse(self.emitted)
    def test_invalid_neighbor_does_not_starve_good(self):
        self.wake('b');p=self.repo/'requests/worker-wake/a.json';p.write_text('broken');self.publish()
        out=drain(self.repo,kinds=['worker_wake'],emitter=self.emit)
        self.assertEqual([r['phase'] for r in out['results']],['REJECTED','DONE'])
    def test_completed_entries_do_not_consume_batch_limit(self):
        self.wake('a');drain(self.repo,kinds=['worker_wake'],emitter=self.emit);self.sync();self.wake('b')
        out=drain(self.repo,kinds=['worker_wake'],emitter=self.emit,max_requests=1)
        self.assertEqual(out['results'][0]['path'],'requests/worker-wake/b.json');self.assertEqual(len(self.emitted),2)
    def test_changed_same_path_cannot_reuse_old_claim(self):
        self.wake(); record=scan(self.repo,after=latest(self.repo,'origin','main'),kinds=['worker_wake'])[0]
        process(self.repo,record);self.sync()
        req=json.loads((self.repo/record['path']).read_text());req['fence_token']='fence-new-value';self.put(record['path'],req);self.publish()
        result=process(self.repo,record,emitter=self.emit)
        self.assertEqual(result['phase'],'SUPERSEDED');self.assertFalse(self.emitted)
    def test_network_unknown_is_persisted_not_done(self):
        self.wake()
        def fail(w):raise TimeoutError('response missing')
        out=drain(self.repo,kinds=['worker_wake'],emitter=fail)
        self.assertEqual(out['results'][0]['phase'],'UNKNOWN');self.assertFalse(self.emitted)
    def test_protected_control_project_is_not_worker(self):
        self.wake();self.put('state/task_cells/cell.json',{'project_key':'g-p-test'});self.publish()
        out=drain(self.repo,kinds=['worker_wake'],emitter=self.emit)
        self.assertEqual(out['results'][0]['phase'],'NEEDS_RECONCILE');self.assertFalse(self.emitted)
    def test_semantic_business_failure_published_with_receipt(self):
        semantic_fixtures.SemanticFinalizeTests()._fixture(self.repo,status='ERROR');self.publish()
        out=drain(self.repo,kinds=['semantic_finalize'])
        self.assertEqual(out['results'][0]['phase'],'DONE');self.assertEqual(out['results'][0]['outcome'],'ERROR')
        self.assertEqual(self.read('cl/t-final.backend.json')['dispatch']['state'],'ERROR')
        self.assertEqual(drain(self.repo,kinds=['semantic_finalize'])['processed'],0)
    def test_stale_semantic_does_not_mutate_cl(self):
        semantic_fixtures.SemanticFinalizeTests()._fixture(self.repo,status='ERROR',analysis_dispatch={'dispatch_id':'old','generation':1,'fence_token':'old'})
        before=json.loads((self.repo/'cl/t-final.backend.json').read_text());self.publish()
        out=drain(self.repo,kinds=['semantic_finalize'])
        self.assertEqual(out['results'][0]['phase'],'SUPERSEDED')
        self.assertEqual(self.read('cl/t-final.backend.json'),before)
    def test_race_recomputes_instead_of_rebasing_stale_projection(self):
        barrier=threading.Barrier(2); errors=[]; outputs=[]
        def work(label):
            first=True
            def operation(root, head):
                nonlocal first
                path=root/'cl/counter.json'
                value=json.loads(path.read_text()) if path.exists() else {'count':0}
                if first:
                    first=False;barrier.wait(timeout=10)
                # Distinct requests must create distinct receipts, even in the same Git timestamp second.
                value['operations'] = sorted([*value.get('operations', []), label])
                value['count']+=1;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
                return {'label':label,'count':value['count']},['cl/counter.json']
            try:outputs.append(transaction(self.repo,operation))
            except Exception as e:errors.append(e)
        threads=[threading.Thread(target=work,args=(s,)) for s in ['a','b']]
        for t in threads:t.start()
        for t in threads:t.join(timeout=30)
        self.assertFalse(errors,errors);self.assertEqual(len(outputs),2)
        latest(self.repo,'origin','main');self.assertEqual(self.read('cl/counter.json')['count'],2)
        self.assertEqual(self.read('cl/counter.json')['operations'], ['a', 'b'])
    def test_parallel_receipt_pins_accepted_evidence(self):
        task=parallel_fixtures.task(); bg=parallel_fixtures.backend(); fg=parallel_fixtures.foreground()
        self.put('tasks/'+task['task_id']+'.json', task)
        self.put(task['backend_cl'],bg);self.put(task['foreground_cl'],fg)
        self.put('requests/parallel-branch-finalize/a.json', {'task_id':task['task_id']});self.publish()
        run_git(self.repo,'checkout','-b',task['work_branch'])
        self.put(task['output_root']+'/branch_result.json',parallel_fixtures.result())
        self.put(task['output_root']+'/file.txt',{'artifact':True})
        run_git(self.repo,'add','-A');run_git(self.repo,'commit','-m','result')
        branch_head=run_git(self.repo,'rev-parse','HEAD').stdout.strip()
        run_git(self.repo,'push','origin','HEAD:refs/heads/'+task['work_branch']);run_git(self.repo,'checkout','main')
        out=drain(self.repo,kinds=['parallel_finalize'])
        self.assertEqual(out['results'][0]['outcome'],'PASS')
        accepted=self.read(task['backend_cl'])['accepted_evidence']
        self.assertEqual(accepted['validated_commit'],branch_head)
        receipt=self.read('state/request_receipts/'+out['results'][0]['request_key']+'.json')
        self.assertEqual(receipt['accepted_evidence'],accepted)
    def test_throwing_finalizer_partial_projection_discarded(self):
        self.wake();self.sync()
        self.put('results/bad.analysis.json',{'task_id':'t-a'});self.publish()
        before=self.read('cl/t-a.json')
        def broken(path,root):
            target=root/'cl/t-a.json';target.write_text('{"corrupted":true}')
            raise ValueError('deliberate partial write')
        with patch('harness.request_drain.finalize',broken):
            result=drain(self.repo,kinds=['semantic_finalize'])
        self.assertEqual(result['results'][0]['phase'],'NEEDS_RECONCILE')
        self.assertEqual(self.read('cl/t-a.json'),before)
    def test_wrong_wake_ack_is_unknown(self):
        self.wake()
        result=drain(self.repo,kinds=['worker_wake'],emitter=lambda w:{'ok':True,'wake_id':'other'})
        self.assertEqual(result['results'][0]['phase'],'UNKNOWN')
    def test_non_idempotent_stage0_is_not_in_control_drain(self):
        with self.assertRaises(ValueError):drain(self.repo,kinds=['stage0'])

if __name__=='__main__':unittest.main()
