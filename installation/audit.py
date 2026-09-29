#!/usr/bin/env python3
"""Offline manifest/privacy checks. Not a security certification or live test."""
import hashlib, json, re, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'audit/file-manifest.json').read_text(encoding='utf-8'))
errors=[]; files={}
for path in sorted(ROOT.rglob('*')):
 if path.is_symlink(): errors.append(path.relative_to(ROOT).as_posix()+': symlink is not allowed'); continue
 if not path.is_file() or '__pycache__' in path.parts or '.git' in path.parts: continue
 rel=path.relative_to(ROOT).as_posix()
 if path.is_symlink(): errors.append(rel+': symlink is not allowed'); continue
 if rel=='audit/file-manifest.json': continue
 files[rel]=hashlib.sha256(path.read_bytes()).hexdigest()
 if rel not in manifest['files'] or files[rel]!=manifest['files'][rel]: errors.append(rel+': integrity mismatch')
 if rel.startswith(('.github/','toolbox/','showcase/','showcases/','imports/','memory/','tasks/','evidence/','results/','requests/','cl/','actions/','cases/')): errors.append(rel+': excluded payload')
 if path.name in ('.env','.runner','.credentials','.credentials_rsaparams') or path.name.endswith('.local.json'): errors.append(rel+': local configuration/credential material')
 if path.suffix.lower() in ('.zip','.rar','.blend','.png','.jpg','.exe','.dll','.pyc','.pem','.pfx','.key'): errors.append(rel+': unreviewed binary/key material')
 try: text=path.read_text(encoding='utf-8')
 except UnicodeError: errors.append(rel+': non-UTF8'); continue
 patterns=[r'(?i)C:[\\/]+Users[\\/]+(?!Public\b|Default\b|EXAMPLE\b)[^\\/\s]+',r'(?i)(?:/home/|/Users/)(?!example\b)[A-Za-z0-9_.-]+',r'\bg-p-[0-9a-f]{24,}\b',r'\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b',r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',r'\b(?:192\.168|10\.\d+)\.\d+\.\d+\b']
 for pattern in patterns:
  if re.search(pattern,text): errors.append(rel+': privacy pattern hit')
 for needle_hash in manifest['denied_literal_sha256']:
  # Original private identities are not embedded in this distributable scanner.
  for word in re.findall(r'[A-Za-z0-9_.@/-]{4,}',text):
   if hashlib.sha256(word.casefold().encode()).hexdigest()==needle_hash: errors.append(rel+': private identity fingerprint')
for rel in manifest['files']:
 if rel not in files: errors.append(rel+': missing')
result={'ok':not errors,'files_checked':len(files),'errors':sorted(set(errors)),'scope':'current template text/tree only; not old Git history, account authorization, dynamic dependencies or live runtime'}
print(json.dumps(result,indent=2))
raise SystemExit(0 if result['ok'] else 1)
