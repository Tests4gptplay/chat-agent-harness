#!/usr/bin/env python3
"""Render a new private operational copy; never push, register or start services."""
from __future__ import annotations
import argparse, ast, hashlib, json, re, shutil, tempfile
from pathlib import Path, PureWindowsPath
ROOT=Path(__file__).resolve().parents[1]

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--config',type=Path,required=True)
 parser.add_argument('--output',type=Path,required=True)
 a=parser.parse_args()
 spec=json.loads((ROOT/'installation/placeholders.json').read_text(encoding='utf-8'))
 data=json.loads(a.config.read_text(encoding='utf-8-sig'))
 values=dict(data.get('values',{}));needed=set(spec['tokens'])
 if set(values)!=needed: raise SystemExit('Configuration keys do not match placeholders.json')
 enabled=data.get('enabled_lanes',['lane-00'])
 if not isinstance(enabled,list) or not enabled or len(enabled)!=len(set(enabled)) or any(x not in ('lane-00','lane-01') for x in enabled): raise SystemExit('Invalid enabled_lanes')
 absent_lane1=not values['LANE01_KEY']
 if absent_lane1 and 'lane-01' in enabled: raise SystemExit('Enabled lane-01 needs its own verified Project')
 if absent_lane1: values['LANE01_KEY']='g-p-UNREGISTEREDLANE01'
 for key in ('RECORD_REPOSITORY','TRANSFER_REPOSITORY'):
  if not values[key]:values[key]='UNCONFIGURED/OPTIONAL'
 for key,value in values.items():
  if not isinstance(value,str) or not value or any(c in value for c in '\r\n\x00\x22\x27`$%&|<>{}'):
   raise SystemExit('Unsafe/empty value: '+key)
  if any(token in value for token in spec['tokens'].values()) or 'REPLACE' in value or 'PLACEHOLDER' in value:
   raise SystemExit('Unresolved placeholder: '+key)
  if key.endswith('_ROOT') or key.endswith('_BASE') or key=='CHROME_PATH':
   if not (PureWindowsPath(value).is_absolute() or Path(value).is_absolute()): raise SystemExit('Absolute path required: '+key)
  if key.endswith('REPOSITORY') and not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',value): raise SystemExit('owner/repository required: '+key)
 for key in ('TASK_CELL_KEY','LANE00_KEY','LANE01_KEY'):
  if not re.fullmatch(r'g-p-[A-Za-z0-9]+',values[key]):raise SystemExit('Invalid Project key: '+key)
 real=[values['TASK_CELL_KEY'],values['LANE00_KEY']]+([] if absent_lane1 else [values['LANE01_KEY']])
 if len(real)!=len(set(real)):raise SystemExit('Project bindings must be unique')
 if not re.fullmatch(r'https://chatgpt\.com/(?:g/g-p-[A-Za-z0-9-]+/)?c/[A-Za-z0-9-]+',values['FOREGROUND_URL']):raise SystemExit('Foreground must be an exact conversation URL')
 if data.get('operational_repository_is_private') is not True or data.get('projects_verified_in_authenticated_browser') is not True:
  raise SystemExit('Installer must verify private repository and authenticated Project ownership first')
 out=a.output.resolve()
 if out==ROOT or ROOT in out.parents or out in ROOT.parents:raise SystemExit('Output must be separate from source')
 if Path(values['REPO_ROOT']).resolve()!=out:raise SystemExit('REPO_ROOT must match --output on this host')
 if out.exists():raise SystemExit('Refusing to overwrite an existing destination')
 for key in ('BRIDGE_ROOT','BROWSER_ROOT','TOOLS_ROOT','PROFILE_ROOT','MANAGED_RUNNER_ROOT','SHOT_RUNNER_ROOT','WORK_ROOT','TEMP_ROOT'):
  target=Path(values[key]).resolve()
  if target==out or out in target.parents:raise SystemExit('Private runtime/data must remain outside the source checkout: '+key)
 if Path(values['MANAGED_RUNNER_ROOT']).resolve()==Path(values['SHOT_RUNNER_ROOT']).resolve():raise SystemExit('Runner roots must be distinct')
 if any(p.is_symlink() for p in ROOT.rglob('*')):raise SystemExit('Symlink in template is not allowed')
 index=json.loads((ROOT/'audit/file-manifest.json').read_text(encoding='utf-8'))
 actual={x.relative_to(ROOT).as_posix() for x in ROOT.rglob('*') if x.is_file() and '__pycache__' not in x.parts and '.git' not in x.parts}
 if actual!=set(index['files'])|{'audit/file-manifest.json'}:raise SystemExit('Source tree differs from integrity manifest')
 for rel,sha in index['files'].items():
  if (ROOT/rel).is_symlink() or hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()!=sha:raise SystemExit('Source integrity mismatch: '+rel)
 out.parent.mkdir(parents=True,exist_ok=True)
 temp=Path(tempfile.mkdtemp(prefix='cah-configure-',dir=out.parent))
 try:
  for rel in sorted(actual):
   src=ROOT/rel;dst=temp/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
  for rel in spec['files']:
   target=temp/rel;text=target.read_text(encoding='utf-8')
   for key,marker in spec['tokens'].items():
    val=values[key]
    if key.endswith('_ROOT') or key.endswith('_BASE') or key=='CHROME_PATH':
     val=val.replace('/','\\') if target.suffix in ('.ps1','.bat') and PureWindowsPath(val).is_absolute() else val.replace('\\','/')
    if target.suffix=='.json':val=json.dumps(val,ensure_ascii=False)[1:-1]
    text=text.replace(marker,val)
   if target.suffix=='.py':ast.parse(text)
   if any(marker in text for marker in spec['tokens'].values()):raise ValueError('Unresolved substitution in '+rel)
   target.write_text(text,encoding='utf-8',newline='\n')
  topology=json.loads((temp/'state/lanes.json').read_text(encoding='utf-8'))
  if absent_lane1:topology['lanes']=[x for x in topology['lanes'] if x['lane_id']!='lane-01']
  for row in topology['lanes']:
   row['enabled']=row['lane_id'] in enabled;row['status']='IDLE' if row['enabled'] else 'DISABLED'
   row['project_root_url']='https://chatgpt.com/g/'+row['project_key']+'/project'
  topology['registered_count']=len(topology['lanes']);topology['enabled_count']=len(enabled)
  for rel in ('Start_CAH.ps1','host/bridge_supervisor.ps1'):
   script=temp/rel; body=script.read_text(encoding='utf-8'); body=body.replace("[string]$LocalWorkerLanes = 'lane-00,lane-01'", "[string]$LocalWorkerLanes = '"+','.join(enabled)+"'",1);script.write_text(body,encoding='utf-8')
  (temp/'state/lanes.json').write_text(json.dumps(topology,indent=2)+'\n',encoding='utf-8')
  # Drop old account-specific display slugs: the stable Project key owns identity.
  for rel in ('playwright_host/config.py','host/reset_cah_semantics_cli.ps1'):
   path=temp/rel;text=path.read_text(encoding='utf-8')
   text=text.replace(values['TASK_CELL_KEY']+'-cah-task-cell/project',values['TASK_CELL_KEY']+'/project')
   path.write_text(text,encoding='utf-8')
  receipt={'v':1,'template_source_sha':index['source_commit'],'configured':True,'live_acceptance':'NOT_RUN','privacy':'Owner settings exist in this operational copy; keep it private.'}
  (temp/'installation/configured.local.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
  temp.rename(out)
  print('CONFIGURED_COPY_CREATED; no Git push, registration, process start or live acceptance performed')
 except BaseException:
  shutil.rmtree(temp,ignore_errors=True);raise
if __name__=='__main__':main()
