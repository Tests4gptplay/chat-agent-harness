#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import struct
import time
import urllib.error
import urllib.parse
import urllib.request


def http_json(url: str, method: str = "GET"):
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=3) as r:
        raw = r.read().decode("utf-8")
    return json.loads(raw)


class WS:
    def __init__(self, url: str):
        u = urllib.parse.urlparse(url)
        if u.scheme != "ws":
            raise RuntimeError("only ws:// CDP endpoints are supported")
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {u.hostname}:{u.port or 80}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self.sock.sendall(req)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        if not buf.startswith(b"HTTP/1.1 101"):
            raise RuntimeError(f"websocket upgrade failed: {buf[:120]!r}")

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def send_text(self, text: str):
        data = text.encode("utf-8")
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            head = bytes([0x81, 0x80 | n])
        elif n < 65536:
            head = bytes([0x81, 0x80 | 126]) + struct.pack("!H", n)
        else:
            head = bytes([0x81, 0x80 | 127]) + struct.pack("!Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(head + mask + masked)

    def recv_text(self, timeout: float = 5.0) -> str:
        self.sock.settimeout(timeout)
        while True:
            b1b2 = self._recv_exact(2)
            b1, b2 = b1b2[0], b1b2[1]
            opcode = b1 & 0x0F
            n = b2 & 0x7F
            masked = bool(b2 & 0x80)
            if n == 126:
                n = struct.unpack("!H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else None
            payload = self._recv_exact(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise EOFError("websocket closed")
            if opcode == 0x9:
                self._send_pong(payload)
                continue
            if opcode == 0x1:
                return payload.decode("utf-8", errors="replace")

    def _send_pong(self, payload: bytes):
        mask = os.urandom(4)
        n = len(payload)
        if n >= 126:
            return
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes([0x8A, 0x80 | n]) + mask + masked)

    def _recv_exact(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise EOFError("websocket closed")
            out += chunk
        return out


def evaluate(ws_url: str, expression: str, req_id: int = 1):
    ws = WS(ws_url)
    try:
        ws.send_text(json.dumps({
            "id": req_id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True},
        }))
        deadline = time.time() + 5
        while time.time() < deadline:
            raw = ws.recv_text(max(0.2, deadline - time.time()))
            msg = json.loads(raw)
            if msg.get("id") == req_id:
                return msg
        raise RuntimeError("CDP evaluate timeout")
    finally:
        ws.close()


def value_of(msg):
    return (((msg or {}).get("result") or {}).get("result") or {}).get("value")


def target_extension_info(target):
    url = str(target.get("url") or "")
    if not url.startswith("chrome-extension://"):
        return None
    ws = target.get("webSocketDebuggerUrl")
    if not ws:
        return None
    expr = "(() => { try { const m=chrome.runtime.getManifest(); return {name:m.name,version:m.version,id:chrome.runtime.id}; } catch(e) { return null; } })()"
    try:
        value = value_of(evaluate(ws, expr))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def discover(base: str, wanted_name: str, timeout: float):
    deadline = time.time() + timeout
    last_count = 0
    while time.time() < deadline:
        try:
            targets = http_json(base + "/json/list")
        except Exception:
            time.sleep(0.5)
            continue
        last_count = len(targets)
        for target in targets:
            info = target_extension_info(target)
            if info and info.get("name") == wanted_name:
                return target, info
        time.sleep(0.5)
    raise RuntimeError(f"extension target not observed within {timeout}s; target_count={last_count}")


def create_extension_page(base: str, extension_id: str):
    url = "chrome-extension://" + extension_id + "/popup.html"
    endpoint = base + "/json/new?" + urllib.parse.quote(url, safe=":/")
    return http_json(endpoint, method="PUT")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--name", default="CAH Wake Bridge")
    p.add_argument("--expected-version", required=True)
    p.add_argument("--discover-timeout", type=float, default=75)
    args = p.parse_args()

    base = f"http://127.0.0.1:{args.port}"
    try:
        version = http_json(base + "/json/version")
    except Exception as exc:
        print(json.dumps({"ok": False, "stage": "cdp_connect", "error": str(exc)}))
        return 2

    try:
        target, info = discover(base, args.name, args.discover_timeout)
    except Exception as exc:
        print(json.dumps({"ok": False, "stage": "discover_extension", "error": str(exc)}))
        return 3

    extension_id = str(info.get("id") or "")
    before = str(info.get("version") or "")
    ws_url = str(target.get("webSocketDebuggerUrl") or "")
    try:
        try:
            evaluate(ws_url, "chrome.runtime.reload(); 'reload-requested'", req_id=7)
        except Exception:
            # Expected: runtime.reload may close the target before CDP returns.
            pass
        time.sleep(2.0)

        page = create_extension_page(base, extension_id)
        page_ws = str(page.get("webSocketDebuggerUrl") or "")
        if not page_ws:
            raise RuntimeError("extension verification page has no websocket endpoint")
        verified = value_of(evaluate(
            page_ws,
            "(() => { const m=chrome.runtime.getManifest(); return {name:m.name,version:m.version,id:chrome.runtime.id}; })()",
            req_id=8,
        ))
        after = str((verified or {}).get("version") or "")
        ok = after == args.expected_version and (verified or {}).get("name") == args.name
        print(json.dumps({
            "ok": ok,
            "stage": "verified" if ok else "version_mismatch",
            "extension_id": extension_id,
            "before_version": before,
            "after_version": after,
            "browser": version.get("Browser"),
        }))
        return 0 if ok else 4
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "stage": "reload_or_verify",
            "extension_id": extension_id,
            "before_version": before,
            "error": str(exc),
        }))
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
