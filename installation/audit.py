#!/usr/bin/env python3
"""Offline file-inventory, approved-public-asset and privacy checks, not a security certification."""
import hashlib, json, re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads((ROOT/'audit/file-manifest.json').read_text(encoding='utf-8'))
errors = []
files = {}
approved = set(manifest.get('approved_public_binary_files', []))
allowed_release_workflow = '.github/workflows/public-source-release.yml'
exclusions = ('actions/', 'cases/', 'cl/', 'evidence/', 'imports/', 'memory/', 'requests/', 'results/', 'tasks/', 'test-samples/')
secret_patterns = [
    r'(?i)C:[\\/]+Users[\\/]+(?!Public\b|Default\b|EXAMPLE\b)[^\\/\s]+',
    r'(?i)(?:/home/|/Users/)(?!example\b)[A-Za-z0-9_.-]+',
    r'\bg-p-[0-9a-f]{20,}\b',
    r'(?i)Tests4gptplay/(?:git-agent-harness)',
    r'\bE:\\{1,2}[A-Za-z0-9_][A-Za-z0-9_-]{2,}',
    r'(?i)gah-win-0[12]',
    r'\b(?:ghp|gho|ghu|ghs|github_pat|sk-proj)_[A-Za-z0-9_-]{20,}\b',
    r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
]
for path in sorted(ROOT.rglob('*')):
    if '.git' in path.parts or '__pycache__' in path.parts: continue
    if path.is_symlink():
        errors.append(str(path.relative_to(ROOT)) + ': symlink forbidden')
        continue
    if not path.is_file(): continue
    rel = path.relative_to(ROOT).as_posix()
    if rel == 'audit/file-manifest.json': continue
    raw = path.read_bytes()
    files[rel] = hashlib.sha256(raw).hexdigest()
    if rel not in manifest['files'] or files[rel] != manifest['files'][rel]:
        errors.append(rel + ': missing/incorrect manifest digest')
    if rel.startswith(exclusions):
        errors.append(rel + ': private runtime payload forbidden')
    if rel.startswith('.github/') and rel not in ('.github/FUNDING.yml', allowed_release_workflow):
        errors.append(rel + ': unauthorized workflow/configuration')
    if rel.startswith('skills/') and rel not in ('skills/index.json', 'skills/README.md'):
        errors.append(rel + ': private skill/import forbidden')
    if rel.startswith('showcases/') and not rel.startswith('showcases/ue-parallel-viewer/'):
        errors.append(rel + ': unreviewed showcase forbidden')
    if path.name in ('.env','.runner','.credentials','.credentials_rsaparams') or path.name.endswith('.local.json'):
        errors.append(rel + ': private credential/configuration forbidden')
    if path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.blend', '.zip', '.rar', '.exe', '.dll', '.pyc', '.pem', '.pfx', '.key'):
        if rel not in approved: errors.append(rel + ': unreviewed binary/file')
        continue
    try:
        value = raw.decode('utf-8')
    except UnicodeError:
        errors.append(rel + ': non-UTF8 text')
        continue
    for pattern in secret_patterns:
        if re.search(pattern, value):
            errors.append(rel + ': privacy pattern ' + str(secret_patterns.index(pattern)))
    for needle_hash in manifest.get('denied_literal_sha256', []):
        for word in re.findall(r'[A-Za-z0-9_.@/-]{4,}', value):
            if hashlib.sha256(word.casefold().encode()).hexdigest() == needle_hash:
                errors.append(rel + ': private identity fingerprint')
if set(files) != set(manifest['files']):
    errors.append('File inventory differs from reviewed release manifest')
if set(f for f in files if Path(f).suffix.lower() in ('.png','.jpg','.jpeg','.webp','.blend','.zip','.rar','.exe','.dll','.pyc','.pem','.pfx','.key')) != approved:
    errors.append('Approved binary allowlist and actual source diverge')
for rel in ('state/chatgpt.json','state/lanes.json','skills/index.json'):
    x = json.loads((ROOT/rel).read_text(encoding='utf-8'))
    if rel == 'state/chatgpt.json' and (x.get('active_task') or x.get('phase') != 'IDLE'):
        errors.append(rel + ': non-idle runtime')
    if rel == 'state/lanes.json' and any(z.get('task_pools') for z in x.get('lanes', [])):
        errors.append(rel + ': nonempty private lane pool')
    if rel == 'skills/index.json' and (x.get('active_count') or x.get('skill_count') or x.get('skills')):
        errors.append(rel + ': private skill registry')
if not (ROOT/'LICENSE').read_text(encoding='utf-8').startswith('GNU AFFERO GENERAL PUBLIC LICENSE'):
    errors.append('LICENSE: AGPL body absent')
if 'AGPL-3.0-only' not in (ROOT/'NOTICE').read_text(encoding='utf-8'):
    errors.append('NOTICE: AGPL statement absent')
install=(ROOT/'installation/UPGRADE_AND_REDEPLOY_V1_0_5.md').read_text(encoding='utf-8')
if not all(x in install for x in ('PRIVATE', 'COLD_RUNNER_ROOT', 'COLD_RUNNER_NAME', 'WORKFLOW_NOT_PROVISIONED')):
    errors.append('Installation re-deployment/privacy plan incomplete')
print(json.dumps({'ok': not errors, 'files_checked': len(files), 'errors': sorted(set(errors)), 'scope': 'Reviewed public tree only; not private Git history, live install, external packages or independent security certification'}, indent=2))
raise SystemExit(0 if not errors else 1)
