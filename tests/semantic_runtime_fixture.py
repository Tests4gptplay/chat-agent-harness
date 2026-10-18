"""Isolated lifecycle fixture: real Git, HTTP, state, queues and full ticks.

Only browser effects, semantic-role writes and the clock are simulated. Initial
machine state is a Foreground Task/Plan/handoff. Subsequent machine transitions
are owned by production functions, not handwritten test state changes.
"""
from __future__ import annotations
import sys, json, subprocess, tempfile, threading, copy, traceback, re, os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from http.server import ThreadingHTTPServer

SOURCE=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(SOURCE),str(SOURCE/'tests')]
from playwright_host.runtime import BrowserRuntime
from playwright_host.bridge import BridgeClient, BridgeError
from playwright_host.config import HostConfig, TASK_CELL
from playwright_host.ui import ChatGPTUI, parse_dispatch_context, chatgpt_location
from playwright_host.error_helper import ERROR_HELPER_OWNER
from harness.request_drain import drain
from local_bridge.server import Handler, WakeStore
from harness.worker_lineage_compat import WORKER_LINEAGES_KEY

PARENT='git-completion-task'
class Clock:
    elapsed=0.0
    base=datetime.now(timezone.utc)
    def advance(self,s):self.elapsed+=s
class ClockDate(datetime):
    clock=None
    @classmethod
    def now(cls,tz=None):
        v=cls.clock.base+timedelta(seconds=cls.clock.elapsed)
        v=v.astimezone(tz) if tz else v.replace(tzinfo=None)
        # Preserve the clock's datetime type contract, including isinstance checks
        # in production terminal handling. No production clock code is changed.
        return cls(v.year,v.month,v.day,v.hour,v.minute,v.second,v.microsecond,tzinfo=v.tzinfo,fold=v.fold)
class EmptyNodes:
    def count(self):return 0
class Page:
    def __init__(self,ui,url,text=''):
        self.ui=ui;self.url=url;self.text=text;self.open=True;self.running=True;self.answer=[]
    def locator(self,*a,**kw):return EmptyNodes()
    def close(self):
        if self.url in self.ui.close_fail:
            self.ui.close_fail.remove(self.url)
            raise RuntimeError('Simulated page close failure during retention cleanup')
        self.open=False
    def wait_for_url(self,predicate,timeout=None):
        if callable(predicate):assert predicate(self.url)
class BrowserAdapter:
    """Stateful tabs/conversations. Actual production find/conversation_page logic."""
    find_page=ChatGPTUI.find_page
    matching_pages=ChatGPTUI.matching_pages
    conversation_page=ChatGPTUI.conversation_page
    def __init__(self,audit):
        self.audit=audit;self.all=[];self.created=[];self.deleted=[];self.nav_errors=set();self.close_fail=set()
        self.cdp_url='http://127.0.0.1:1';self.browser=SimpleNamespace(is_connected=lambda:True)
    def connect(self):pass
    def close(self):pass
    def pages(self):return [p for p in self.all if p.open]
    def new_page(self,url):
        loc=chatgpt_location(url)
        if loc and loc.conversation_id in self.nav_errors:raise RuntimeError('Simulated per-conversation navigation failure')
        existing=next((p for p in self.all if p.url==url),None)
        if existing:existing.open=True;return existing
        p=Page(self,url);self.all.append(p);return p
    def project_root(self,key,url,reuse=True):return self.new_page(url)
    def bootstrap_conversation(self,key,url,text,marker,before_submit=None,**kw):
        role='worker' if parse_dispatch_context(text) else ('helper' if ('HARNESS_ERROR_HELPER' in text or 'Act as HELPER' in text or 'Act as Helper' in text) else 'planner')
        cid=f'chat-{len(self.created)+1:04d}-{role}'
        p=Page(self,f'https://chatgpt.com/g/{key}/c/{cid}',text)
        self.all.append(p)
        if before_submit:p.text=before_submit(p,text)
        self.created.append({'cid':cid,'role':role,'text':p.text,'at':self.audit.clock.elapsed})
        return p,cid,p.url
    def response_running(self,p):return p.running
    def marker_state(self,p,marker):
        return {'marker_visible':marker in p.text,'response_started':True,'response_ended':not p.running,
                'assistant_texts':list(p.answer)}
    def dispatch_turn_state(self,p):
        ctx=parse_dispatch_context(p.text)
        if not ctx:return None
        return {'dispatch_context':ctx,'response_started':True,'response_ended':not p.running,
                'assistant_texts':list(p.answer),'superseded':False}
    def submit_text(self,p,text,marker=None,**kw):
        p.text=text;p.running=True;p.answer=[];return {'ok':True,'response_started':True}
    def stop_response(self,p):p.running=False;return {'ok':True}
    def delete_project_conversation(self,p,key,cid):
        self.deleted.append(cid)
        # Deleting a Project conversation does not itself close every open tab.
        # Production callers perform their own explicit page.close afterwards.
        return True
    def confirm_project_conversation_absent(self,p,key,cid,**kw):return cid in self.deleted
    def page_for(self,cid):
        return next(p for p in self.all if (chatgpt_location(p.url) and chatgpt_location(p.url).conversation_id==cid))

class FixtureBridge(BridgeClient):
    def _request(self,op,**kw):
        audit=self.audit
        item={'tick':audit.n,'op':op,'ids':{k:kw[k] for k in ('task_id','parent_task_id','child_task_id','lane_id','request_id','message_id') if k in kw}}
        if op=='event':
            item['telemetry']={k:kw[k] for k in ('event','level','data') if k in kw}
        audit.ops.append(item)
        if audit.fail_ops.get(op,0):
            audit.fail_ops[op]-=1;item['error']='injected transient transport fault'
            raise BridgeError('isolated transient transport failure: '+op)
        if audit.consume_fail and op=='consume' and (audit.fail_message is None or kw.get('message_id')==audit.fail_message):
            audit.consume_fail-=1;item['error']='injected pre-consume transport fault'
            raise BridgeError('isolated transport failure before consume')
        try:
            result=super()._request(op,**kw)
            item['result']={k:result[k] for k in ('ok','accepted','action','idle','reason','completed','staged','duplicate','cleaned','state','matched','suppressed') if k in result}
            return result
        except Exception as exc:
            item['error']=str(exc);raise
class HarnessCase:
    def __init__(self,lanes=1,idle=False):
        self.temp=tempfile.TemporaryDirectory(prefix='cah-semantic-');self.root=Path(self.temp.name)
        self.work=self.root/'repo';self.work.mkdir();self.origin=self.root/'remote.git'
        subprocess.run(['git','init','--bare','-q',str(self.origin)],check=True)
        self.git('init','-q');self.git('config','user.name','audit');self.git('config','user.email','audit@example.invalid')
        self.git('checkout','-qb','main');self.git('remote','add','origin',str(self.origin))
        topology=json.loads((SOURCE/'state/lanes.json').read_text())
        topology['lanes']=topology['lanes'][:lanes];topology.update(registered_count=lanes,enabled_count=lanes)
        state={'v':1,'agent':'chatgpt','phase':'NEED_AGENT','active_task':None,'control_request':None}
        self.n=0;self.ops=[];self.ticks=[];self.writes=[];self.consume_fail=0;self.fail_message=None;self.fail_ops={}
        self.clock=Clock();self.clock.elapsed=0;ClockDate.clock=self.clock;self.patches=[]
        for mod in ['playwright_host.worker','playwright_host.runtime','local_bridge.planner_runtime','local_bridge.topology','local_bridge.server','local_bridge.worker_retry','local_bridge.scheduler','harness.dispatch','harness.wake','playwright_host.error_helper']:
            self.patches.append(patch(mod+'.datetime',ClockDate))
        self.patches.append(patch('playwright_host.runtime.time',SimpleNamespace(monotonic=lambda:10000+self.clock.elapsed)))
        self.patches.append(patch.dict(os.environ,{'CAH_LOCAL_WORKER_LANES':','.join(x['lane_id'] for x in topology['lanes']),'CAH_MCP_LIVE':'0'}))
        for p in self.patches:p.start()
        files={'state/chatgpt.json':state,'state/lanes.json':topology}
        if not idle:
            state['active_task']=PARENT
            state['control_request']={'v':1,'kind':'task_cell_planner_foreground_handoff','request_id':'foreground-handoff-review',
                'status':'PENDING','task_id':PARENT,'control_epoch':1,'task_cell_project_key':TASK_CELL['project_key']}
            files.update({f'tasks/{PARENT}.json':{'v':1,'task_id':PARENT,'goal':'Preserve accepted output and continue a long-running experiment','status':'ACTIVE'},
                          f'tasks/{PARENT}.plan.json':{'v':1,'task_id':PARENT,'plan':'Run bounded experiments and carry verified results forward'}})
        self.write('initial user/Foreground input',files)
        self.store=WakeStore(self.root/'bridge',repo_root=self.work)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.store=self.store
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.cfg=HostConfig(bridge_url=f'http://127.0.0.1:{self.server.server_port}/api',runtime_root=self.root/'host')
        self.runtime=BrowserRuntime(self.cfg);self.ui=BrowserAdapter(self)
        bridge=FixtureBridge(self.cfg.bridge_url,self.runtime.state.client_id,self.cfg.project_id,self.runtime._on_harness_error);bridge.audit=self
        self.runtime.bridge=bridge;self.runtime.ui=self.ui;self.runtime.workers.bridge=bridge;self.runtime.workers.ui=self.ui
        self.runtime.connect()
    def git(self,*args):return subprocess.check_output(['git','-C',str(self.work),*args],text=True,encoding='utf-8',stderr=subprocess.DEVNULL).strip()
    def read(self,path):
        self.git('fetch','origin','main');return json.loads(self.git('show','FETCH_HEAD:'+path))
    def text(self,path):self.git('fetch','origin','main');return self.git('show','FETCH_HEAD:'+path)
    def write(self,actor,objects):
        if (self.work/'state/chatgpt.json').exists():self.git('fetch','origin','main');self.git('reset','--hard','origin/main')
        for rel,value in objects.items():
            f=self.work/rel;f.parent.mkdir(parents=True,exist_ok=True)
            f.write_text(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        self.git('add','--',*objects);self.git('commit','--allow-empty','-qm',actor);self.git('push','origin','HEAD:main')
        self.writes.append({'tick':self.n,'actor':actor,'paths':list(objects)})
    def tick(self,seconds=1,deliver=True):
        self.n+=1;self.clock.advance(seconds)
        start=len(self.ops);result=self.runtime.tick()
        if deliver:
            drained=drain(self.work,kinds=['worker_wake'],emitter=lambda w:self.runtime.bridge.call('emit',wake=w),max_seconds=30)
        lineage_views=[]
        for lane_id,lane in self.runtime.state.data.get('lanes',{}).items():
            for lineage in lane.get(WORKER_LINEAGES_KEY,{}).values():
                cur=lineage.get('current') or {}
                lineage_views.append({'lane_id':lane_id,'owner_task_id':lineage.get('owner_task_id'),
                    'owner_control_epoch':lineage.get('owner_control_epoch'),
                    'current_conversation':cur.get('conversation_id'),'worker_ref':cur.get('handoff_id'),
                    'wake_id':cur.get('wake_id'),'watchdog':copy.deepcopy(cur.get('worker_watchdog')),
                    'retained_conversations':len(lineage.get('managed') or [])})
        roles=[{k:v for k,v in role.items() if k in ('task_id','role','request_id','conversation_id','active_output_ref','helper_source')}
               for role in self.runtime.state.data.get('task_cell',{}).get('roles',{}).values()]
        self.ticks.append({'n':self.n,'elapsed':self.clock.elapsed,'result':result,'ops':self.ops[start:],
                          'lineages_after':lineage_views,'roles_after':roles})
        return result
    def cell(self):return self.read(f'state/task_cells/{PARENT}.json')
    def children(self):return self.cell()['planner_control']['runtime'].get('owned_children',[])
    def current(self,lane):return (self.runtime.state.get_lineage(lane,PARENT,1) or {}).get('current')
    def planner(self):return self.runtime.state.get_role(PARENT,1,'planner')
    def decide(self,lanes=None,signal='done'):
        cell=self.cell();turn=cell['planner_control']['runtime']['active_turn'];out=self.read(turn['outcome_ref'])
        out['turn_signal']=signal
        writes={turn['memory_entry_ref']:self.text(turn['memory_entry_ref'])+'\nSimulated Planner: preserve prior accepted work; choose next bounded action.\n',turn['outcome_ref']:out}
        if lanes:
            for b in turn['slots']:
                if b.get('lane_id') in lanes:
                    slot=self.read(b['slot_ref']);slot.update(entry_type='DIRECTION',semantic={'goal':'Continue bounded output processing','acceptance':'write verified results'})
                    writes[b['slot_ref']]=slot
        self.write('Planner semantic write',writes)
        role=self.planner();page=self.ui.page_for(role['conversation_id']);page.running=True;page.answer=[]
    def worker_result(self,lane,signal='continue',note='Read existing run/job output; preserve prior accepted evidence before the next experiment.'):
        child=next(c for c in self.children() if c['lane_id']==lane);task=self.read(child['task_ref']);result=self.read(task['expected_result_ref'])
        result.update(summary='Existing external output inspected; accepted frontier preserved',turn_signal=signal)
        self.write('Worker semantic write',{task['worker_reply_entry_ref']:self.text(task['worker_reply_entry_ref'])+'\n'+note+'\n',task['expected_result_ref']:result})
        page=self.ui.page_for(self.current(lane)['conversation_id']);page.running=True;page.answer=[]
    def start(self,lanes):
        self.tick();assert self.planner(),self.ticks[-1]
        self.decide(lanes)
        for _ in range(3):self.tick()
        for lane in lanes:assert self.current(lane)['handoff_verified']
    def error_helper(self):return self.runtime.state.get_role(ERROR_HELPER_OWNER,1,'helper')
    def repair_done(self,role=None):
        role=role or self.error_helper();assert role
        self.write('Helper semantic write',{role['active_output_ref']:{'diagnosis':'identified reported runtime fault','repair_result':'restored existing operation; no business identity changed','turn_signal':'done'}})
        page=self.ui.page_for(role['conversation_id']);page.answer=[];page.running=True
    def close(self):
        self.runtime.workers.mcp.close();self.server.shutdown();self.server.server_close();self.thread.join(5)
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
