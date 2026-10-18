#!/usr/bin/env python3
"""Maintainer-only v1.0.5 release manifest builder. Run only on reviewed public source."""
from pathlib import Path
import hashlib, json, re
ROOT = Path(__file__).resolve().parents[1]
manifest_path = ROOT / 'audit/file-manifest.json'
old = json.loads(manifest_path.read_text(encoding='utf-8'))
spec_path = ROOT / 'installation/placeholders.json'
spec = json.loads(spec_path.read_text(encoding='utf-8'))
locations = {}
files_map = {}
for rel, declared_keys in spec['files'].items():
    target = ROOT / rel
    if not target.is_file():
        raise SystemExit('CONFIGURATION_MAP_MISSING_FILE: ' + rel)
    lines = target.read_text(encoding='utf-8').splitlines()
    present = []
    for key in declared_keys:
        marker = spec['tokens'][key]
        numbers = [i for i, line in enumerate(lines, 1) if marker in line]
        if numbers:
            present.append(key)
            locations.setdefault(key, []).append({'file': rel, 'lines': numbers})
    if present:
        files_map[rel] = present
mandatory = {
    'Start_CAH.ps1': {'BRIDGE_ROOT', 'MANAGED_RUNNER_ROOT', 'SHOT_RUNNER_ROOT', 'COLD_RUNNER_ROOT', 'COLD_RUNNER_NAME', 'BROWSER_ROOT'},
    'playwright_host/config.py': {'BROWSER_ROOT', 'TASK_CELL_KEY'},
    'state/lanes.json': {'LANE00_KEY', 'LANE01_KEY'},
}
for rel, expected in mandatory.items():
    if not expected.issubset(set(files_map.get(rel, []))):
        raise SystemExit('MISSING_DEPLOYMENT_PLACEHOLDER: ' + rel)
spec['files'] = files_map
spec['locations'] = locations
spec['policy'] = 'Render into a NEW, independently verified private operational copy only. Never restore maintainer identities.'
spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
data = {
    'v': 2,
    'source_commit': 'v1.0.5-sanitized-source',
    'algorithm': 'sha256',
    'files': {},
    'denied_literal_sha256': old.get('denied_literal_sha256', []),
    'approved_public_binary_files': ["assets/brand/cah-mark.png","assets/brand/icons/cah-icon-128.png","assets/brand/icons/cah-icon-16.png","assets/brand/icons/cah-icon-24.png","assets/brand/icons/cah-icon-32.png","assets/brand/icons/cah-icon-48.png","assets/brand/icons/cah-icon-transparent.png","assets/brand/icons/cah-start-launcher.png","assets/donations/alipay-qr.png","showcases/ue-parallel-viewer/historical-2026-09-20/images/below.png","showcases/ue-parallel-viewer/historical-2026-09-20/images/bottom.png","showcases/ue-parallel-viewer/historical-2026-09-20/images/perspective.png","showcases/ue-parallel-viewer/images/phase1-below-lit.png","showcases/ue-parallel-viewer/images/phase1-front-lit.png","showcases/ue-parallel-viewer/images/phase1-perspective-lit.png","showcases/ue-parallel-viewer/images/phase1-right-lit.png"]
}
for f in sorted(ROOT.rglob('*')):
    if '.git' in f.parts or '__pycache__' in f.parts or not f.is_file(): continue
    rel = f.relative_to(ROOT).as_posix()
    if rel == 'audit/file-manifest.json': continue
    data['files'][rel] = hashlib.sha256(f.read_bytes()).hexdigest()
manifest_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
print('v1.0.5 reviewed-source manifest updated:', len(data['files']), 'files')
