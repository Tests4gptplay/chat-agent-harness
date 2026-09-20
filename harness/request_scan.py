"""Read-only Git request discovery. Events are hints; tree scans are complete.

No command execution, wake delivery or executor retry occurs in this module.
Consumers must validate canonical dispatch identity and claim before side effects.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

KINDS = {
    'worker_wake': ('requests/worker-wake/', '.json'),
    'parallel_finalize': ('requests/parallel-branch-finalize/', '.json'),
    'semantic_finalize': ('results/', '.analysis.json'),
    'stage0': ('actions/stage0/', '.json'),
    'host_update': ('requests/host-update/', '.json'),
    'host_diagnostic': ('requests/host-diagnostic/', '.json'),
}
SHA = re.compile(r'^[0-9a-f]{40}(?:[0-9a-f]{24})?$')

class RequestScanError(ValueError):
    pass


def git(root: Path, *args: str, input: bytes | None = None) -> bytes:
    import os
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never', GIT_PAGER='cat')
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=20, env=env, input=input).stdout


def normalize_path(value: Any) -> str:
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise RequestScanError('invalid request path')
    p = PurePosixPath(value)
    if p.is_absolute() or '..' in p.parts or str(p) != value or any(ord(c) < 32 for c in value):
        raise RequestScanError('unsafe request path')
    return value


def is_kind(path: str, kind: str) -> bool:
    if kind not in KINDS:
        raise RequestScanError('unsupported request kind')
    prefix, suffix = KINDS[kind]
    return path.startswith(prefix) and path.endswith(suffix) and '/' not in path[len(prefix):]


def commit(root: Path, value: str) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value) or set(value) == {'0'}:
        raise RequestScanError('exact nonzero commit SHA required')
    actual = git(root, 'rev-parse', '--verify', value + '^{commit}').decode().strip()
    if actual != value:
        raise RequestScanError('commit identity changed')
    return actual


def changed_paths(root: Path, *, before: str, after: str) -> set[str]:
    after = commit(root, after)
    if isinstance(before, str) and SHA.fullmatch(before) and set(before) == {'0'}:
        # A new branch has no prior tree: enumerate the full accepted snapshot.
        raw = git(root, 'ls-tree', '-r', '--name-only', '-z', after)
    else:
        before = commit(root, before)
        raw = git(root, 'diff', '--name-only', '--diff-filter=AM', '-z', before, after, '--')
    return {normalize_path(p.decode('utf-8')) for p in raw.split(b'\0') if p}


def tree_entries(root: Path, revision: str, prefixes: list[str]) -> list[tuple[str, str, str, int, str]]:
    """Read mode, type, object ID, size and path in one Git invocation."""
    raw = git(root, 'ls-tree', '-r', '-l', '-z', '--full-tree', revision, '--', *prefixes)
    entries = []
    for item in raw.split(b'\0'):
        if item:
            meta, path = item.split(b'\t', 1)
            mode, typ, oid, size = meta.decode('ascii').split()
            entries.append((mode, typ, oid, int(size) if size != '-' else -1,
                            normalize_path(path.decode('utf-8'))))
    return entries


def blob_batch(root: Path, oids: list[str]) -> dict[str, bytes]:
    """Read already-discovered immutable blobs without a process per file."""
    oids = list(dict.fromkeys(oids))
    if not oids:
        return {}
    if any(not SHA.fullmatch(oid) for oid in oids):
        raise RequestScanError('exact object IDs required')
    raw = git(root, 'cat-file', '--batch', '--buffer', input=('\n'.join(oids) + '\n').encode('ascii'))
    result = {}
    offset = 0
    for oid in oids:
        end = raw.find(b'\n', offset)
        header = raw[offset:end].decode('ascii').split()
        if end < 0 or len(header) != 3 or header[:2] != [oid, 'blob']:
            raise RequestScanError('unexpected batch object header')
        size = int(header[2])
        offset = end + 1
        data = raw[offset:offset + size]
        if len(data) != size or raw[offset + size:offset + size + 1] != b'\n':
            raise RequestScanError('truncated batch object')
        result[oid] = data
        offset += size + 1
    return result


def discover(root: Path, *, after: str, kinds: list[str], before: str | None = None,
             max_bytes: int = 1048576) -> list[dict[str, Any]]:
    """Cheap request identities only. Completed payloads need not be read."""
    after = commit(root, after)
    if not kinds or any(k not in KINDS for k in kinds):
        raise RequestScanError('supported request kinds required')
    prefixes = sorted({KINDS[k][0] for k in kinds})
    changed = changed_paths(root, before=before, after=after) if before is not None else None
    records = []
    for mode, typ, oid, size, path in tree_entries(root, after, prefixes):
        kind = next((k for k in kinds if is_kind(path, k)), None)
        if not kind or (changed is not None and path not in changed):
            continue
        key = hashlib.sha256((kind + '\0' + path + '\0' + oid).encode()).hexdigest()
        record = {'v': 1, 'kind': kind, 'path': path, 'source_commit': after,
                  'blob_oid': oid, 'request_key': key, 'valid': False}
        if typ != 'blob' or mode not in {'100644', '100755'}:
            record['error'] = 'REQUEST_NOT_REGULAR_FILE'
        elif size > max_bytes:
            record['error'] = 'REQUEST_TOO_LARGE'
        records.append(record)
    return sorted(records, key=lambda r: (r['kind'], r['path']))


def hydrate(root: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blobs = blob_batch(root, [r['blob_oid'] for r in records if not r.get('error')])
    for record in records:
        if record.get('error'):
            continue
        data = blobs[record['blob_oid']]
        try:
            value = json.loads(data.decode('utf-8-sig'))
            if not isinstance(value, dict):
                raise ValueError('request must be an object')
            record.update(valid=True, payload=value, content_sha256=hashlib.sha256(data).hexdigest())
        except (UnicodeError, ValueError) as exc:
            record.update(error='REQUEST_INVALID_JSON', detail=str(exc)[:200])
    return records


def scan(root: Path, *, after: str, kinds: list[str], before: str | None = None,
         max_bytes: int = 1048576) -> list[dict[str, Any]]:
    return hydrate(root, discover(root, after=after, kinds=kinds, before=before, max_bytes=max_bytes))


def read_receipts(root: Path, revision: str) -> dict[str, Any]:
    entries = [e for e in tree_entries(root, revision, ['state/request_receipts/'])
               if e[0] in {'100644', '100755'} and e[1] == 'blob' and e[3] <= 1048576
               and re.fullmatch(r'state/request_receipts/[0-9a-f]{64}\.json', e[4])]
    blobs = blob_batch(root, [e[2] for e in entries])
    receipts = {}
    for _, _, oid, _, path in entries:
        try:
            value = json.loads(blobs[oid].decode('utf-8-sig'))
            if isinstance(value, dict):
                receipts[PurePosixPath(path).stem] = value
        except (UnicodeError, ValueError):
            pass  # An unreadable receipt never hides its request.
    return receipts


def receipt_matches(record: dict[str, Any], receipt: Any) -> bool:
    return isinstance(receipt, dict) and all(receipt.get(k) == record.get(k)
        for k in ('kind', 'path', 'blob_oid', 'request_key'))


def pending(records: list[dict[str, Any]], receipts: dict[str, Any]) -> list[dict[str, Any]]:
    # Ambiguous CLAIMED/UNKNOWN outcomes are held for reconciliation, never retried
    # just because time passed. Only the domain reconciler can resolve the claim.
    terminal = {'DONE', 'REJECTED', 'SUPERSEDED', 'CLAIMED', 'UNKNOWN', 'NEEDS_RECONCILE'}
    return [r for r in records if not (receipt_matches(r, receipts.get(r['request_key']))
            and receipts[r['request_key']].get('phase') in terminal)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--after', required=True)
    parser.add_argument('--before')
    parser.add_argument('--kind', action='append', choices=sorted(KINDS), required=True)
    args = parser.parse_args()
    print(json.dumps(scan(args.root, after=args.after, before=args.before, kinds=args.kind), ensure_ascii=False))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
