"""Official Playwright MCP over stdio; no RPC in the mechanical DOM polling path."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from typing import Any


TOOLS_ROOT = Path(os.environ.get("CAH_PLAYWRIGHT_TOOLS_ROOT", r"__CAH_TOOLS_ROOT__"))
BOUND_ENDPOINT_FILE = Path(os.environ.get(
    "CAH_PLAYWRIGHT_BOUND_ENDPOINT_FILE",
    r"__CAH_BROWSER_ROOT__\browser-endpoint.json",
))
ALIASES = {
    "browser.tabs": "browser_tabs",
    "browser.read": "browser_evaluate",
    "browser.snapshot": "browser_snapshot",
    "browser.console": "browser_console_messages",
    "browser.errors": "browser_console_messages",
    "browser.network": "browser_network_requests",
}


class MCPCallPending(Exception):
    """A semantic tool is in flight; let the native host keep observing other roles."""


class MCPClient:
    def __init__(
        self,
        cdp_url: str,
        root: Path = TOOLS_ROOT,
        endpoint_path: Path | str = BOUND_ENDPOINT_FILE,
    ):
        self.root = Path(root)
        self.cdp_url = cdp_url
        self.endpoint_path = Path(endpoint_path)
        self.process = None
        self._serial = 0
        self._messages = queue.Queue()
        self._stderr = None
        self._lock = threading.RLock()

    def _write(self, message: dict) -> None:
        self.process.stdin.write(json.dumps(message, ensure_ascii=True) + "\n")
        self.process.stdin.flush()

    def _read(self, process, messages) -> None:
        try:
            for line in process.stdout:
                messages.put(json.loads(line))
        except Exception as exc:
            messages.put(exc)
        finally:
            messages.put(EOFError("Playwright MCP stdio closed"))

    def _bound_endpoint(self) -> str:
        try:
            value = json.loads(self.endpoint_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"CAH Playwright bound endpoint is unavailable: {self.endpoint_path}"
            ) from exc
        endpoint = str(value.get("endpoint") or "")
        if not endpoint.startswith("ws://127.0.0.1:"):
            raise RuntimeError("CAH Playwright bound endpoint is missing or not localhost-only")
        return endpoint

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        config = json.loads((self.root / "runtime.json").read_text(encoding="utf-8-sig"))
        self.workspace = Path(config["workspace"]).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        output = self.root / "output"
        output.mkdir(parents=True, exist_ok=True)
        self._stderr = (self.root / "mcp.stderr.log").open("a", encoding="utf-8")
        command = [config["node"], str(self.root / "node_modules/@playwright/mcp/cli.js"),
                   "--endpoint", self._bound_endpoint(), "--caps", "vision,pdf,devtools",
                   "--idle-timeout", "0", "--output-dir", str(output), "--file-paths", "absolute"]
        self.process = subprocess.Popen(
            command, cwd=self.workspace, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self._stderr, text=True, encoding="utf-8", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._messages = queue.Queue()
        threading.Thread(target=self._read, args=(self.process, self._messages), daemon=True).start()
        self.request("initialize", {"protocolVersion": "2024-11-05",
                     "capabilities": {"roots": {"listChanged": False}},
                     "clientInfo": {"name": "cah-playwright-host", "version": "1"}}, start=False)
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def request(self, method: str, params: dict, *, start: bool = True, timeout: float = 180) -> dict:
        with self._lock:
            if start:
                self.start()
            self._serial += 1
            request_id = self._serial
            self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            deadline = time.monotonic() + timeout
            while True:
                try:
                    message = self._messages.get(timeout=max(0, deadline - time.monotonic()))
                except queue.Empty as exc:
                    # Never retry an unknown-outcome click/type/upload automatically.
                    self.close()
                    raise TimeoutError(f"MCP {method} timed out; outcome unknown, inspect before retry") from exc
                if isinstance(message, Exception):
                    self.close()
                    raise RuntimeError(str(message)) from message
                if "method" in message and "id" in message:
                    if message["method"] == "roots/list":
                        reply = {"result": {"roots": [{"uri": self.workspace.as_uri(), "name": "CAH workspace"}]}}
                    elif message["method"] == "ping":
                        reply = {"result": {}}
                    else:
                        reply = {"error": {"code": -32601, "message": "Unsupported client method"}}
                    self._write({"jsonrpc": "2.0", "id": message["id"], **reply})
                elif message.get("id") == request_id:
                    if "error" in message:
                        raise RuntimeError("MCP RPC error: " + json.dumps(message["error"]))
                    return message["result"]

    def call(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.stdin.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
            process.stdout.close()
            if not process.stdin.closed:
                process.stdin.close()
        if self._stderr:
            self._stderr.close()
            self._stderr = None


class PlaywrightMCP:
    def __init__(self, cdp_url: str, root: Path = TOOLS_ROOT):
        self.client = MCPClient(cdp_url, root)
        # Serializes select+call across roles without blocking native response-end polling.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cah-mcp")
        self._pending = {}

    def execute(self, key: str, call: dict, target_url: str | None) -> dict:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cah-mcp")
        if key not in self._pending:
            self._pending[key] = self._executor.submit(self.execute_sync, call, target_url)
        future = self._pending[key]
        if not future.done():
            raise MCPCallPending()
        return future.result()

    def execute_sync(self, call: dict, target_url: str | None) -> dict:
        tool, args = call["tool"], call.get("args") or {}
        if tool == "browser.mcp.list":
            tools, params = [], {}
            while True:
                page = self.client.request("tools/list", params)
                tools.extend(page["tools"])
                if not page.get("nextCursor"):
                    break
                params = {"cursor": page["nextCursor"]}
            name = args.get("name")
            return {"tools": [t if name or args.get("schemas") else
                              {k: t[k] for k in ("name", "description", "annotations") if k in t}
                              for t in tools if not name or t["name"] == name]}
        if tool == "browser.mcp.call":
            name, arguments = args["name"], dict(args.get("arguments") or {})
        else:
            name, arguments = ALIASES[tool], {}
            if tool == "browser.tabs":
                arguments = {"action": "list"}
            elif tool == "browser.read":
                limit = max(1, min(50000, int(args.get("max_chars") or 50000)))
                arguments = {"function": "() => ({url:location.href,title:document.title,text:document.body.innerText.slice(0," + str(limit) + ")})"}
            elif tool == "browser.snapshot" and args.get("depth") is not None:
                arguments = {"depth": args["depth"]}
            elif tool in {"browser.console", "browser.errors"}:
                arguments = {"level": "error" if tool == "browser.errors" else "info"}
            elif tool == "browser.network":
                arguments = {"static": False}
        global_tab_call = name == "browser_tabs" and (
            arguments.get("action") in {"list", "new"} or "index" in arguments)
        if not global_tab_call:
            if not target_url:
                raise ValueError("MCP page operation requires an exact target; no implicit shared current tab")
            listing = self.client.call("browser_tabs", {"action": "list"})
            lines = "\n".join(c.get("text", "") for c in listing.get("content", []))
            matches = [int(m[0]) for m in re.findall(r"^- (\d+): .*\]\((.*)\)$", lines, re.M) if m[1] == target_url]
            if len(matches) != 1:
                raise RuntimeError("MCP target missing or ambiguous; refresh browser.tabs")
            selected = self.client.call("browser_tabs", {"action": "select", "index": matches[0]})
            if selected.get("isError"):
                return selected
        return self.client.call(name, arguments)

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
        self._pending.clear()
        self.client.close()  # stdio detach; Native Host remains the sole direct Chrome CDP client.
