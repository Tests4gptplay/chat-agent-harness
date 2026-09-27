"""Prepare the first release from a reviewed public snapshot, not a private checkout."""
import hashlib
import json
from pathlib import Path

root = Path.cwd()
licenses = {
    'LICENSE': 'a4c4241100b108ce611d06f7163e6f21cfe7d85180ddde5edf8b8a0c6268aadd',
    'NOTICE': '84b9967afedb76c7ebea278281b2d5c6a632e0b376a21978661b5a5902e945d2',
    'CONTRIBUTING.md': 'a40e199b9021ab34c7f5efc66e2c6da55533856f0bade7474dea0f43bbada83f',
    '.github/pull_request_template.md': '719a4188555b9a02c1355a2313ead4d75a2fe84db37843653266742eba98924a',
}
for name, expected in licenses.items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name

# Already-authorized public discussion pages are untouched on main. They are not
# shipped in the runtime release or in this tag's automatically generated archives.
excluded = ('docs/vision/', 'requests/', 'evidence/')
removed = {'docs/REFERENCES.md', 'docs/UPSTREAM_PATTERNS.md',
           '.github/workflows/publish-vision-discussions.yml'}
for path in list(root.rglob('*')):
    if path.is_file() and (path.relative_to(root).as_posix().startswith(excluded)
                           or path.relative_to(root).as_posix() in removed):
        path.unlink()

p = root / 'README.md'
s = p.read_text(encoding='utf-8')
start = s.index('## Future architecture\n')
end = s.index('## License and contribution\n', start)
p.write_text(s[:start] + s[end:], encoding='utf-8')
p = root / 'docs/FOREGROUND_MONITOR.md'
s = p.read_text(encoding='utf-8').replace('backend scheduler CLs described in the distributed-agent memo',
    'backend scheduler CLs described in `docs/SCHEDULER_MODEL.md`')
p.write_text(s, encoding='utf-8')
p = root / 'browser/playwright/README.md'
s = p.read_text(encoding='utf-8').split('## Single-thread migration rule\n')[0]
s += '''## Instance configuration

Configure your own private instance and Project bindings using `docs/INSTALL_WINDOWS.md` before launching. This optional browser backend does not inherit the maintainer's account, browser profile or Project identities. Each persistent profile owns its local browser/extension storage. Do not start two clients against the same active Worker binding.
'''
p.write_text(s, encoding='utf-8')
(root / 'docs/PARALLEL_STAGE1.md').write_text('''# Parallel scheduling primitives

This release includes the deterministic task-DAG, dependency, lane and reducer primitives in `harness/parallel.py`. Task state remains in Git; browser windows are execution hosts, not proof that useful work ran in parallel.

## Run the deterministic smoke

```sh
python harness/parallel.py smoke
python -m unittest discover -s tests -p 'test_parallel_scheduler.py'
```

The smoke covers two ready independent branches, a reducer waiting for both results, fresh dispatch fences and rejection of stale results. A one-lane topology executes the graph sequentially. It does not launch live model sessions or prove concurrent application throughput.

## Runtime use

Configure your own registered lanes through the private-installation guide. The release preserves lane-addressed wakes, independent bounded Worker conversation pools and persistent task/result records. Task Cell is a control context, not disposable Worker capacity.

Use a single Worker when there is no independently useful work to split. For useful parallel work, make inputs, output ownership, dependencies and the join explicit. Use the existing task/result contracts in `harness/parallel_task.schema.json` and `docs/SCHEDULER_MODEL.md`.

The included camera showcase records a single-lane application workload. The self-update showcase records both bindings surviving an update, followed by one delegated task. Neither is advertised as a benchmark of general concurrent application throughput.
''', encoding='utf-8')

files = {}
for p in sorted(root.rglob('*')):
    rel = p.relative_to(root).as_posix()
    if not p.is_file() or '.git' in p.relative_to(root).parts or '__pycache__' in p.parts or rel.startswith('extension/dist/') or rel == 'PUBLIC_MANIFEST.json':
        continue
    files[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
manifest = {'v': 1, 'version': '1.0.4',
    'export_policy': 'Reviewed public source snapshot. No development memos, future-plan drafts, live maintainer state or private Git history. Existing license files retained byte-for-byte.',
    'files': files}
(root / 'PUBLIC_MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'file_count': len(files) + 1, 'manifest_sha256': hashlib.sha256((root / 'PUBLIC_MANIFEST.json').read_bytes()).hexdigest(),
                  'licenses_unchanged': True}, indent=2))
