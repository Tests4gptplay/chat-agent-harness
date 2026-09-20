"""One-time setup of an explicitly owned private CAH checkout."""
from pathlib import Path
import argparse, json, os, re, subprocess
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parents[1]
def project(value):
    u=urlparse(value.strip())
    m=re.fullmatch(r'/g/(g-p-[A-Za-z0-9]+)(?:-[^/]+)?/project/?',u.path)
    if u.scheme!='https' or u.hostname!='chatgpt.com' or not m:
        raise ValueError('Copy the exact ChatGPT Project root URL ending in /project')
    return m[1], 'https://chatgpt.com'+u.path.rstrip('/')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',required=True,help='owner/private-repository')
    p.add_argument('--runner-root',type=Path,required=True)
    p.add_argument('--task-cell',required=True)
    p.add_argument('--lane',action='append',required=True,help='Repeat twice for initial tested topology')
    p.add_argument('--bridge-root',type=Path,default=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local/share')))/'CAH')
    p.add_argument('--work-root',type=Path,default=Path.home()/'CAH'/'Workloads')
    a=p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',a.repo) or a.repo in {'Tests4gptplay/chat-agent-harness','example-owner/cah-private'}:
        p.error('supply your own private repository, not the public distribution')
    if len(a.lane)!=2:p.error('initial setup requires two lane Projects; add more through the extension later')
    try:cell=project(a.task_cell);lanes=[project(x) for x in a.lane]
    except ValueError as e:p.error(str(e))
    if len({cell[0],*[x[0] for x in lanes]})!=3:p.error('Task Cell and lanes must be different Projects')
    if not (a.runner_root/'run.cmd').is_file():p.error('register the official Windows runner first; run.cmd not found')
    config=ROOT/'cah.local.json'
    if config.exists():p.error('already configured; edit the existing private configuration deliberately rather than resetting live state')
    origin=subprocess.check_output(['git','-C',str(ROOT),'remote','get-url','origin'],text=True).strip().removesuffix('.git')
    if not origin.endswith('/'+a.repo) and not origin.endswith(':'+a.repo):p.error('origin does not match --repo; point it to your private repository first')
    mapping={'example-owner/cah-private':a.repo,'__CAH_CONFIGURED__':'yes'}
    for old,actual in zip(['exampletaskcell','examplelane00','examplelane01'],[cell,*lanes]):
        mapping['g-p-'+old]=actual[0]
        suffix={'exampletaskcell':'cah-task-cell','examplelane00':'cah-sandbox0','examplelane01':'cah-sandbox1'}[old]
        mapping['https://chatgpt.com/g/g-p-'+old+'-'+suffix+'/project']=actual[1]
    for folder in ['extension','harness','local_bridge','executors','tests','docs','browser','examples','ai']:
        for path in (ROOT/folder).rglob('*'):
            if not path.is_file() or path.suffix not in {'.py','.js','.cjs','.json','.md','.yml'} or 'dist' in path.parts:continue
            text=path.read_text(encoding='utf-8')
            for old in sorted(mapping,key=len,reverse=True):text=text.replace(old,mapping[old])
            path.write_text(text,encoding='utf-8',newline='\n')
    path=ROOT/'AGENTS.md';path.write_text(path.read_text(encoding='utf-8').replace('example-owner/cah-private',a.repo),encoding='utf-8',newline='\n')
    lane_state={'v':1,'topology_version':1,'registered_count':2,'enabled_count':2,'lanes':[{'lane_id':f'lane-{i:02d}','display_name':f'CAH Sandbox{i}','project_key':key,'project_root_url':url,'enabled':True,'status':'IDLE','last_pool_takeover_id':None,'worker_rollover_request':None} for i,(key,url) in enumerate(lanes)]}
    (ROOT/'state/lanes.json').write_text(json.dumps(lane_state,indent=2)+'\n',encoding='utf-8')
    state=json.loads((ROOT/'state/chatgpt.json').read_text(encoding='utf-8'))
    state.update(unresolved=[],next_action='Await an explicit task or control request.',writeback_reason='Private installation configured; no task has been run yet.',repository_trust={'primary_repo':a.repo,'authorized_by_user':True})
    (ROOT/'state/chatgpt.json').write_text(json.dumps(state,indent=2)+'\n',encoding='utf-8')
    value={'repository':a.repo,'runner_root':str(a.runner_root.resolve()),'bridge_root':str(a.bridge_root.resolve()),'work_root':str(a.work_root.resolve()),'projects':[cell[1],*[x[1] for x in lanes]]}
    config.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    subprocess.run([os.sys.executable,str(ROOT/'extension/build.py'),'chromium'],check=True)
    print('Configured. Review and commit bindings only in your private repository.')
    print('Load unpacked:',ROOT/'extension/dist/chromium')

if __name__=='__main__':main()
