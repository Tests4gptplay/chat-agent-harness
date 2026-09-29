import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock

from playwright_host.mcp import MCPCallPending, MCPClient, PlaywrightMCP
from playwright_host.runtime import BrowserRuntime
from playwright_host.worker import WorkerManager
from harness.tool_runtime import ToolRegistry


class PlaywrightMCPTests(unittest.TestCase):
    def setUp(self):
        self.provider = PlaywrightMCP("http://127.0.0.1:9222")
        self.client = self.provider.client = MagicMock()

    def tearDown(self):
        self.provider.close()

    def test_mcp_reads_host_bound_endpoint(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "browser-endpoint.json"
            path.write_text(json.dumps({
                "v": 1,
                "endpoint": "ws://127.0.0.1:43123/secret",
            }), encoding="utf-8")
            client = MCPClient("http://127.0.0.1:9222", endpoint_path=path)
            self.assertEqual(client._bound_endpoint(), "ws://127.0.0.1:43123/secret")

    def test_mcp_missing_or_nonlocal_endpoint_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "browser-endpoint.json"
            client = MCPClient("http://127.0.0.1:9222", endpoint_path=path)
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                client._bound_endpoint()
            path.write_text(json.dumps({
                "v": 1,
                "endpoint": "ws://0.0.0.0:43123/unsafe",
            }), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "localhost-only"):
                client._bound_endpoint()

    def test_full_discovery_schema_on_demand_and_pagination(self):
        self.client.request.side_effect = [
            {"tools": [{"name": "browser_click", "description": "click", "inputSchema": {"required": ["target"]}}], "nextCursor": "next"},
            {"tools": [{"name": "browser_new_upstream_capability", "inputSchema": {}}]},
        ]
        result = self.provider.execute_sync({"tool": "browser.mcp.list"}, None)
        self.assertEqual(len(result["tools"]), 2)
        self.assertNotIn("inputSchema", result["tools"][0])
        self.client.request.assert_called_with("tools/list", {"cursor": "next"})
        self.client.request.side_effect = None
        self.client.request.return_value = {"tools": [{"name": "browser_click", "inputSchema": {"required": ["target"]}}]}
        result = self.provider.execute_sync({"tool": "browser.mcp.list", "args": {"name": "browser_click"}}, None)
        self.assertIn("inputSchema", result["tools"][0])

    def test_exact_selection_precedes_unchanged_official_arguments(self):
        self.client.call.side_effect = [
            {"content": [{"type": "text", "text": "### Result\n- 0: (current) [Other](https://other/)\n- 7: [Target](https://target/)"}]},
            {"content": []}, {"content": [{"type": "text", "text": "clicked"}]},
        ]
        result = self.provider.execute_sync({"tool": "browser.mcp.call", "args": {
            "name": "browser_click", "arguments": {"target": "e12", "button": "left"}}}, "https://target/")
        self.assertEqual(result["content"][0]["text"], "clicked")
        self.assertEqual(self.client.call.call_args_list[1].args, ("browser_tabs", {"action": "select", "index": 7}))
        self.assertEqual(self.client.call.call_args.args, ("browser_click", {"target": "e12", "button": "left"}))

    def test_no_implicit_current_tab_and_no_fallback_after_ambiguous_target(self):
        call = {"tool": "browser.mcp.call", "args": {"name": "browser_click", "arguments": {"target": "e1"}}}
        with self.assertRaises(ValueError):
            self.provider.execute_sync(call, None)
        self.client.call.return_value = {"content": [{"type": "text", "text": "- 0: [A](https://same/)\n- 1: [B](https://same/)"}]}
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            self.provider.execute_sync(call, "https://same/")
        self.assertEqual(self.client.call.call_count, 1)

    def test_tools_run_off_tick_thread_and_same_call_is_not_reexecuted(self):
        entered, release = threading.Event(), threading.Event()
        def slow(call, target):
            entered.set()
            release.wait(3)
            return {"content": []}
        self.provider.execute_sync = MagicMock(side_effect=slow)
        try:
            with self.assertRaises(MCPCallPending):
                self.provider.execute("foreground|id|call1", {}, None)
            self.assertTrue(entered.wait(1))
            with self.assertRaises(MCPCallPending):
                self.provider.execute("foreground|id|call1", {}, None)
        finally:
            release.set()
        for _ in range(100):
            try:
                self.provider.execute("foreground|id|call1", {}, None)
                break
            except MCPCallPending:
                time.sleep(.01)
        self.provider.execute_sync.assert_called_once()

    def test_worker_role_cannot_call_host_runner_as_chat_tool(self):
        worker = WorkerManager(MagicMock(), MagicMock(), MagicMock(cdp_url="http://127.0.0.1:9222"), "test")
        try:
            with self.assertRaises(RuntimeError):
                worker._execute_browser_tool({"tool": "browser.cli", "args": {}}, MagicMock())
        finally:
            worker.mcp.close()

    def test_images_attach_without_base64_in_prompt(self):
        worker = WorkerManager(MagicMock(), MagicMock(), MagicMock(cdp_url="http://127.0.0.1:9222"), "test")
        try:
            worker.ui.submit_text.return_value = {"response_started": True}
            result = {"content": [{"type": "image", "mimeType": "image/png", "data": "cGl4ZWxz"}]}
            worker._submit_tool_result(MagicMock(), {"call_id": "image-001", "tool": "browser.mcp.call"}, "PASS", result, None)
            worker.ui.attach_image.assert_called_once()
            prompt = worker.ui.submit_text.call_args.args[1]
            self.assertNotIn("cGl4ZWxz", prompt)
            self.assertIn("attached_file", prompt)
            self.assertIn("data", result["content"][0])
        finally:
            worker.mcp.close()

    def test_role_transport_keeps_pending_and_routes_mcp_errors_to_same_role(self):
        for role in ("foreground", "planner"):
            rt = BrowserRuntime.__new__(BrowserRuntime)
            rt.ui = MagicMock()
            rt.ui.response_running.return_value = False
            call = {"v": 1, "call_id": "mcp-test-001", "tool": "browser.mcp.call", "args": {"name": "browser_snapshot", "arguments": {}, "target": {"self": True}}}
            rt.ui.marker_state.return_value = {"response_ended": True, "assistant_texts": ["CAH_TOOL_CALL_BEGIN\n"+json.dumps(call)+"\nCAH_TOOL_CALL_END"]}
            rt.tool_registry = ToolRegistry()
            rt.workers = MagicMock()
            rt.workers._execute_browser_tool.side_effect = [MCPCallPending(), {"isError": True, "content": [{"type": "text", "text": "upstream error"}]}]
            rt._seen_role_tool_calls = set()
            page = MagicMock()
            pending = rt._process_bound_tool_call(page, role=role, identity="conversation", marker="wake")
            self.assertTrue(pending["pending"])
            rt.workers._submit_tool_result.assert_not_called()
            done = rt._process_bound_tool_call(page, role=role, identity="conversation", marker="wake")
            self.assertEqual(done["status"], "ERROR")
            self.assertIs(rt.workers._submit_tool_result.call_args.args[0], page)
            self.assertIn(role, rt.workers._submit_tool_result.call_args.kwargs["context_line"])
            self.assertEqual(len(rt._seen_role_tool_calls), 1)

    def test_official_packages_are_pinned(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root / "host/playwright-tools/package.json").read_text(encoding="utf-8-sig"))
        lock = json.loads((root / "host/playwright-tools/package-lock.json").read_text())
        for name, version in manifest["dependencies"].items():
            self.assertRegex(version, r"^\d+\.\d+\.\d+$")
            self.assertEqual(lock["packages"]["node_modules/"+name]["version"], version)
            self.assertTrue(lock["packages"]["node_modules/"+name]["integrity"].startswith("sha512-"))


if __name__ == "__main__":
    unittest.main()
