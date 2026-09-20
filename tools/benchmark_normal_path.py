#!/usr/bin/env python3
"""Same-input request-discovery benchmark; no remote or browser work is measured."""
from __future__ import annotations
import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--historical', type=int, default=200)
    parser.add_argument('--iterations', type=int, default=3)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root.resolve()))
    import harness.request_drain as driver
    if not 0 <= args.historical <= 10000 or not 1 <= args.iterations <= 10:
        parser.error('historical=0..10000, iterations=1..10')
    original_run = subprocess.run
    with tempfile.TemporaryDirectory(prefix='cah-discovery-bench-') as temp:
        root = Path(temp)
        def git(*cmd):
            return subprocess.check_output(['git', '-C', str(root), *cmd], stderr=subprocess.DEVNULL)
        git('init'); git('config', 'user.name', 'CAH benchmark'); git('config', 'user.email', 'benchmark@example.invalid')
        for i in range(args.historical + 1):
            rel = f'requests/worker-wake/{i:05d}.json'
            p = root / rel; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({'index': i, 'note': 'fixed benchmark input'}), encoding='utf-8')
            oid = git('hash-object', rel).decode().strip()
            kind = 'worker_wake'
            key = hashlib.sha256((kind + '\0' + rel + '\0' + oid).encode()).hexdigest()
            if i < args.historical:
                receipt = root / 'state/request_receipts' / (key + '.json')
                receipt.parent.mkdir(parents=True, exist_ok=True)
                receipt.write_text(json.dumps({'kind': kind, 'path': rel, 'blob_oid': oid, 'request_key': key, 'phase': 'DONE'}))
        git('add', '-A'); git('commit', '-m', 'same-input benchmark fixture')
        head = git('rev-parse', 'HEAD').decode().strip()
        runs = []
        for _ in range(args.iterations):
            calls = []
            def observe(cmd, *a, **kw):
                if cmd and cmd[0] == 'git': calls.append(cmd[3:])
                return original_run(cmd, *a, **kw)
            def no_execution(repo, record, **kw):
                if not record['valid'] or record['payload']['index'] != args.historical:
                    raise AssertionError('wrong pending payload')
                return {'phase': 'DONE'}
            with patch.object(driver, 'latest', return_value=head), patch.object(driver, 'process', side_effect=no_execution), patch('subprocess.run', side_effect=observe):
                start = time.perf_counter()
                result = driver.drain(root, kinds=['worker_wake'], max_requests=1, max_seconds=600)
                elapsed = (time.perf_counter() - start) * 1000
            if result['processed'] != 1 or result['already_recorded_or_backoff'] != args.historical:
                raise AssertionError(result)
            runs.append({'elapsed_ms': round(elapsed, 3), 'git_processes': len(calls),
                         'individual_payload_reads': sum(c[:2] == ['cat-file', 'blob'] for c in calls),
                         'batched_reads': sum(c[:2] == ['cat-file', '--batch'] for c in calls),
                         'payloads_read_reported': result.get('payloads_read')})
        state = args.source_root / 'state/chatgpt.json'
        print(json.dumps({'v': 1, 'scope': 'local discovery/receipt filtering only; latest fetch and execution stubbed identically',
                          'platform': platform.platform(), 'python': platform.python_version(),
                          'historical_done': args.historical, 'pending': 1, 'iterations': args.iterations,
                          'hot_state_bytes': state.stat().st_size if state.exists() else None,
                          'median_ms': round(statistics.median(r['elapsed_ms'] for r in runs), 3), 'runs': runs}, indent=2))

if __name__ == '__main__': main()
