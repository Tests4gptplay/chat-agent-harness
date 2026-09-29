#!/usr/bin/env python3
"""Fail closed before launch/reset while deployment placeholders remain."""
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=json.loads((ROOT/'installation/placeholders.json').read_text(encoding='utf-8'))
errors=[]
for rel in spec['files']:
 if rel.startswith(('tests/','docs/','installation/')) or rel.endswith('.md'): continue
 text=(ROOT/rel).read_text(encoding='utf-8')
 for key,marker in spec['tokens'].items():
  if marker in text: errors.append(rel+': '+key)
if errors:
 print('CAH_NOT_CONFIGURED: run installation/configure.py first.\n'+'\n'.join(errors),file=sys.stderr)
 raise SystemExit(2)
if not (ROOT/'installation/configured.local.json').is_file():
 raise SystemExit('CAH_NOT_CONFIGURED: missing configured.local.json receipt')
print('Deployment placeholders resolved; live ownership/readiness still requires installer verification.')
