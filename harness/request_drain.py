"""Reconcile retry-safe control requests from Git, not from push event delivery.

This driver intentionally never executes Stage0 actions or host updates. Those
non-idempotent effects need their own claim/result reconciliation protocol.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from harness.git_process import run_git
from harness.request_scan import discover, hydrate, read_receipts, normalize_path, receipt_matches
from harness.semantic_finalize import finalize
from harness.parallel_branch_finalize import finalize_task, ParallelBranchFinalizeError
from harness.wake import make_wake
from local_bridge.emit_wake import post_json

KINDS = {'worker_wake', 'semantic_finalize', 'parallel_finalize'}
FINAL = {'DONE', 'SUPERSEDED', 'REJECTED'}
IDENT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$')


def load(root: Path, rel: str) -> dict[str, Any]:
    path = root / normalize_path(rel)
    path.resolve().relative_to(root.resolve())
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError(f'{rel}: expected object')
    return value


def write(root: Path, rel: str, value: dict[str, Any]) -> None:
    path = root / normalize_path(rel)
    path.resolve().relative_to(root.resolve())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def receipt_path(record: dict[str, Any]) -> str:
    key = record['request_key']
    if not re.fullmatch('[0-9a-f]{64}', key):
        raise ValueError('invalid request key')
    return 'state/request_receipts/' + key + '.json'


def latest(repo: Path, remote: str, branch: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', remote) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./-]*', branch) or '..' in branch:
        raise ValueError('unsafe remote/branch')
    run_git(repo, 'fetch', '--quiet', '--no-tags', remote, f'{branch}:refs/remotes/{remote}/{branch}')
    return run_git(repo, 'rev-parse', f'refs/remotes/{remote}/{branch}').stdout.strip()


def transaction(repo: Path, operation: Callable[[Path, str], tuple[dict[str, Any], list[str]]],
                *, remote: str = 'origin', branch: str = 'main', attempts: int = 3) -> dict[str, Any]:
    """Git-only computation is repeated against fresh state after a failed push."""
    if not 1 <= attempts <= 5:
        raise ValueError('attempts must be 1..5')
    for attempt in range(attempts):
        head = latest(repo, remote, branch)
        with tempfile.TemporaryDirectory(prefix='gah-request-') as temp:
            wt = Path(temp) / 'worktree'
            run_git(repo, 'worktree', 'add', '--quiet', '--detach', str(wt), head)
            try:
                result, changed = operation(wt, head)
                changed = sorted(set(normalize_path(p) for p in changed))
                if not changed:
                    return {**result, 'canonical_commit': head}
                for path in changed:
                    if not (path.startswith('cl/') and path.endswith('.json') or
                            path.startswith('state/request_receipts/') and path.endswith('.json') or
                            path == 'state/chatgpt.json'):
                        raise ValueError('unexpected transaction write domain: ' + path)
                run_git(wt, 'add', '--', *changed)
                if run_git(wt, 'diff', '--cached', '--quiet', check=False).returncode == 0:
                    return {**result, 'canonical_commit': head}
                run_git(wt, '-c', 'user.name=gah-request-reconciler',
                        '-c', 'user.email=actions@users.noreply.github.com',
                        'commit', '--quiet', '-m', 'Reconcile immutable control request [skip ci]')
                new_head = run_git(wt, 'rev-parse', 'HEAD').stdout.strip()
                try:
                    pushed = run_git(wt, 'push', remote, f'HEAD:refs/heads/{branch}', check=False)
                    if pushed.returncode == 0:
                        return {**result, 'canonical_commit': new_head}
                except subprocess.TimeoutExpired:
                    # A timeout can follow a successful remote mutation. The next
                    # fresh transaction detects its receipt; never infer rollback.
                    pass
            finally:
                run_git(repo, 'worktree', 'remove', '--force', str(wt), check=False)
    raise RuntimeError('canonical request transaction could not be reconciled after bounded retries')


def task_bundle(root: Path, payload: dict[str, Any]) -> tuple[dict, dict, str]:
    task_id = payload.get('task_id')
    if not isinstance(task_id, str) or not IDENT.fullmatch(task_id):
        raise ValueError('valid task_id required')
    task = load(root, f'tasks/{task_id}.json')
    if task.get('task_id') != task_id:
        raise ValueError('task identity mismatch')
    contract = task.get('execution_contract') or {}
    bg_ref = task.get('backend_cl') or contract.get('backend_cl')
    if not isinstance(bg_ref, str) or not bg_ref.startswith('cl/'):
        raise ValueError('declared backend CL required')
    bg = load(root, bg_ref)
    if bg.get('task_id') != task_id:
        raise ValueError('backend task identity mismatch')
    return task, bg, bg_ref


def wake_decision(root: Path, payload: dict[str, Any]) -> tuple[str, dict | None]:
    task, bg, bg_ref = task_bundle(root, payload)
    contract = task.get('execution_contract') or {}
    dispatch = bg.get('dispatch') or {}
    identity = {'task_id': task['task_id'], 'backend_cl': bg_ref,
                'dispatch_id': dispatch.get('dispatch_id'),
                'dispatch_generation': dispatch.get('generation'),
                'fence_token': dispatch.get('fence_token')}
    generation = identity['dispatch_generation']
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise ValueError('positive canonical generation required')
    for key in ['dispatch_id', 'fence_token']:
        if not isinstance(identity[key], str) or not identity[key]:
            raise ValueError('complete canonical dispatch identity required')
    if any(payload.get(k) != v for k, v in identity.items()):
        return 'SUPERSEDED', None
    if dispatch.get('state') in {'RUNNING', 'ACKED', 'DONE', 'ERROR', 'BLOCKED', 'CANCELLED', 'WAIT_DEP', 'WAIT_RESOURCE', 'WAIT_RESULT', 'REVOKED'}:
        return 'DONE' if dispatch.get('state') in {'RUNNING', 'ACKED', 'DONE'} else 'SUPERSEDED', None
    if dispatch.get('state') not in {'READY', 'DISPATCHED'}:
        return 'SUPERSEDED', None
    lane = task.get('lane_id') or contract.get('lane_id')
    project = task.get('worker_project_key') or contract.get('worker_project_key')
    if not lane or not project or payload.get('lane_id') != lane or payload.get('worker_project_key') != project:
        raise ValueError('wake/task lane and project mismatch')
    topology = load(root, 'state/lanes.json')
    matches = [v for v in topology.get('lanes', []) if v.get('lane_id') == lane]
    if len(matches) != 1 or not matches[0].get('enabled') or matches[0].get('project_key') != project:
        raise ValueError('lane is not currently enabled for this project')
    for path in (root / 'state/task_cells').glob('*.json'):
        cell = load(root, path.relative_to(root).as_posix())
        if project in [cell.get('project_key'), *cell.get('protected_projects', [])]:
            raise ValueError('ordinary Worker wake cannot target a protected control project')
    if payload.get('wake_id') != dispatch.get('wake_id') or not payload.get('wake_id'):
        raise ValueError('wake key does not match canonical dispatch')
    kwargs = {key: payload.get(key) for key in ('state', 'wake_id', 'repo', 'result_ref', 'lane_id',
              'worker_project_key', 'kind', 'task_id', 'backend_cl', 'dispatch_id', 'dispatch_generation', 'fence_token')}
    return 'CLAIMED', make_wake(str(payload.get('project_id') or 'git-agent-harness'), **kwargs)


def process(repo: Path, record: dict[str, Any], *, remote: str = 'origin', branch: str = 'main',
            emitter: Callable[[dict], Any] | None = None) -> dict[str, Any]:
    if record['kind'] not in KINDS:
        raise ValueError('non-idempotent request kind is outside this driver')
    receipt_ref = receipt_path(record)
    def prepare(root: Path, head: str) -> tuple[dict, list[str]]:
        try:
            old = load(root, receipt_ref)
        except FileNotFoundError:
            old = {}
        if receipt_matches(record, old) and old.get('phase') in FINAL:
            return old, []
        if old.get('retry_after', 0) > time.time():
            return old, []
        current = run_git(root, 'rev-parse', f'{head}:{record["path"]}', check=False)
        if current.returncode or current.stdout.strip() != record['blob_oid']:
            return {'phase': 'SUPERSEDED', 'request_key': record['request_key']}, []
        receipt = {k: record[k] for k in ('v', 'kind', 'path', 'source_commit', 'blob_oid', 'request_key')}
        receipt.update(attempts=int(old.get('attempts', 0)) + 1, updated_at=time.time())
        changed = []
        try:
            if not record['valid']:
                receipt.update(phase='REJECTED', error=record.get('error'))
            elif record['kind'] == 'worker_wake':
                phase, wake = wake_decision(root, record['payload'])
                if phase == 'CLAIMED' and receipt_matches(record, old) and isinstance(old.get('wake'), dict):
                    wake = old['wake']
                receipt.update(phase=phase, wake=wake)
            elif record['kind'] == 'semantic_finalize':
                result = finalize(root / record['path'], root=root)
                if result.get('projected') or result.get('idempotent'):
                    receipt.update(phase='DONE', outcome=result['outcome'])
                    if result.get('projected'):
                        changed.extend([result['backend_cl'], result['foreground_cl'], result['state']])
                else:
                    if result.get('rejection_kind') == 'stale_or_invalid_identity':
                        receipt.update(phase='SUPERSEDED', diagnostic=result)
                    else:
                        receipt.update(phase='WAIT_VALID_RESULT', diagnostic=result, retry_after=time.time() + 60)
            else:
                task_bundle(root, record['payload'])
                result = finalize_task(record['payload']['task_id'], root=root, remote=remote)
                receipt.update(phase='DONE', outcome=result.get('outcome'), accepted_evidence=result.get('accepted_evidence'))
                if not result['already_finalized']:
                    changed.extend([result['backend_cl'], result['foreground_cl']])
        except (ValueError, OSError, SystemExit, ParallelBranchFinalizeError, subprocess.SubprocessError) as exc:
            # Finalizers promise identity-first validation. Never commit partial
            # projections from a throwing operation, even if that promise breaks.
            run_git(root, 'restore', '--worktree', '--', '.', check=False)
            changed = []
            receipt.update(phase='NEEDS_RECONCILE', error=str(exc)[:300], retry_after=time.time() + 60)
        write(root, receipt_ref, receipt)
        return receipt, changed + [receipt_ref]
    prepared = transaction(repo, prepare, remote=remote, branch=branch)
    if prepared.get('phase') != 'CLAIMED' or not prepared.get('wake'):
        return prepared
    if emitter is None:
        return prepared
    error = None
    try:
        # Replaying this exact immutable wake_id is explicitly idempotent in
        # WakeStore.emit, including after a crash between emit and receipt.
        emitted = emitter(prepared['wake'])
        if not isinstance(emitted, dict) or emitted.get('ok') is not True or emitted.get('wake_id') != prepared['wake']['wake_id']:
            raise RuntimeError('emitter did not acknowledge the exact wake')
    except Exception as exc:
        error = str(exc)[:300]
    def finish(root: Path, head: str) -> tuple[dict, list[str]]:
        receipt = load(root, receipt_ref)
        if not receipt_matches(record, receipt) or receipt.get('wake') != prepared['wake']:
            raise ValueError('wake receipt identity changed')
        if receipt.get('phase') in FINAL:
            return receipt, []
        receipt.update(phase='UNKNOWN' if error else 'DONE', last_error=error,
                       retry_after=time.time() + 30 if error else 0, updated_at=time.time())
        write(root, receipt_ref, receipt)
        return receipt, [receipt_ref]
    return transaction(repo, finish, remote=remote, branch=branch)


def drain(repo: Path, *, kinds: list[str], remote: str = 'origin', branch: str = 'main',
          emitter: Callable[[dict], Any] | None = None, max_requests: int = 100,
          max_seconds: float = 240) -> dict[str, Any]:
    if (not kinds or not set(kinds) <= KINDS or not 1 <= max_requests <= 500
            or not math.isfinite(max_seconds) or not 0 < max_seconds <= 600):
        raise ValueError('invalid kinds/request limit')
    started = time.monotonic()
    deadline = started + max_seconds
    head = latest(repo, remote, branch)
    records = discover(repo, after=head, kinds=kinds)
    receipts = read_receipts(repo, head)
    output = []
    # Omit completed/deferred receipts before applying the batch limit. A page of
    # old DONE entries must never starve newer unhandled work.
    eligible = []
    skipped = 0
    for record in records:
        previous = receipts.get(record['request_key'], {})
        if receipt_matches(record, previous) and (previous.get('phase') in FINAL or previous.get('retry_after', 0) > time.time()):
            skipped += 1
            continue
        eligible.append((int(previous.get('attempts', 0)), record))
    eligible.sort(key=lambda pair: (pair[0], pair[1]['path']))
    selected = [record for _, record in eligible[:max_requests]] if time.monotonic() < deadline else []
    hydrate(repo, selected)
    discovery_ms = round((time.monotonic() - started) * 1000, 3)
    for record in selected:
        if len(output) >= max_requests or time.monotonic() >= deadline:
            break
        try:
            out = process(repo, record, remote=remote, branch=branch, emitter=emitter)
        except Exception as exc:
            out = {'phase': 'NEEDS_RECONCILE', 'error': str(exc)[:300]}
        output.append({'path': record['path'], 'request_key': record['request_key'],
                       'phase': out.get('phase'), 'outcome': out.get('outcome'), 'error': out.get('error')})
    return {'v': 1, 'source_commit': head, 'discovered': len(records), 'processed': len(output),
            'results': output, 'discovery_ms': discovery_ms, 'payloads_read': sum(not r.get('error') for r in selected),
            'already_recorded_or_backoff': skipped, 'deferred': len(eligible) - len(output), 'non_idempotent_actions_executed': 0}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, default=Path('.')); p.add_argument('--kind', action='append', choices=sorted(KINDS), required=True)
    p.add_argument('--branch', default='main'); p.add_argument('--remote', default='origin')
    p.add_argument('--max-requests', type=int, default=100); p.add_argument('--max-seconds', type=float, default=240)
    p.add_argument('--endpoint', default='http://127.0.0.1:8765/api')
    a = p.parse_args()
    if a.endpoint != 'http://127.0.0.1:8765/api':
        p.error('only the declared loopback bridge endpoint is permitted')
    result = drain(a.repo.resolve(), kinds=a.kind, remote=a.remote, branch=a.branch,
                   max_requests=a.max_requests, max_seconds=a.max_seconds,
                   emitter=lambda wake: post_json(a.endpoint, {'op': 'emit', 'wake': wake}, timeout=5))
    print(json.dumps(result, ensure_ascii=False))
    return int(any(r['phase'] in {'NEEDS_RECONCILE', 'WAIT_VALID_RESULT', 'UNKNOWN'} or r.get('outcome') in {'ERROR', 'BLOCKED'} for r in result['results']))

if __name__ == '__main__':
    raise SystemExit(main())
