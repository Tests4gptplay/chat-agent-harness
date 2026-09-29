#!/usr/bin/env python3
"""Control an already-running QuickLook Viewer; no Unreal/Python packages needed."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import struct
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

READ = {'health', 'status', 'list_meshes', 'list_materials', 'list_textures',
        'inspect_material_slots', 'inspect_dependencies'}
WRITE = {'open_mod', 'select_mesh', 'select_material', 'set_camera',
         'frame_selected', 'set_view_mode', 'close_session'}


def call(base: str, operation: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode('utf-8')
    request = Request(base.rstrip('/') + '/' + operation, data=data,
                      headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except HTTPError as error:
        detail = error.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'{operation}: HTTP {error.code}: {detail}') from error
    if not result.get('ok'):
        raise RuntimeError(f'{operation}: {result}')
    return result


def completed_png(path: Path) -> bool:
    """A PNG is ready only after a complete, correctly framed IEND chunk."""
    try:
        with path.open('rb') as stream:
            if stream.read(8) != b'\x89PNG\r\n\x1a\n':
                return False
            while True:
                header = stream.read(8)
                if len(header) != 8:
                    return False
                size, kind = struct.unpack('>I4s', header)
                if size > 128 * 1024 * 1024:
                    return False
                payload = stream.read(size + 4)
                if len(payload) != size + 4:
                    return False
                if kind == b'IEND':
                    return size == 0
    except (OSError, ValueError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=sorted(READ | WRITE | {'capture'}))
    parser.add_argument('--base', default='http://127.0.0.1:17656/v1')
    parser.add_argument('--json', default='{}', dest='body')
    parser.add_argument('--name', default='inspection', help='Capture name prefix')
    parser.add_argument('--out', type=Path, help='Optional local destination PNG')
    parser.add_argument('--settle', type=float, default=0.75)
    parser.add_argument('--timeout', type=float, default=30.0)
    args = parser.parse_args()
    try:
        body = json.loads(args.body)
        if not isinstance(body, dict):
            raise ValueError('--json must be a JSON object')
        if args.operation == 'capture':
            time.sleep(max(0, args.settle))
            name = Path(args.name).stem + '-' + uuid4().hex + '.png'
            result = call(args.base, 'capture_viewport', {'name': name})
            path = Path(result['viewer_owned_path'])
            deadline = time.monotonic() + args.timeout
            while not completed_png(path):
                if time.monotonic() > deadline:
                    raise TimeoutError(f'PNG did not complete on this host: {path}')
                time.sleep(0.1)
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, args.out)
                result['copied_to'] = str(args.out.resolve())
            result['complete'] = True
            result['bytes'] = path.stat().st_size
        else:
            result = call(args.base, args.operation,
                          None if args.operation in READ else body)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, URLError, RuntimeError, ValueError, KeyError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
