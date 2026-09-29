import json
import tempfile
import unittest
from pathlib import Path

from harness.tool_runtime import ToolRegistry, ToolRegistryError

ROOT = Path(__file__).resolve().parents[1]


class ToolRegistryTests(unittest.TestCase):
    def test_repository_registry_validates(self):
        out = ToolRegistry(ROOT).validate_all()
        self.assertEqual(out["active_count"], 10)
        self.assertIn("browser.mcp.call", out["tool_ids"])
        self.assertIn("browser.cli", out["tool_ids"])

    def test_chat_browser_tools_exclude_helper(self):
        reg = ToolRegistry(ROOT)
        for role in ("worker", "planner", "foreground"):
            tools = {tool["tool_id"]: tool for tool in reg.list_for_role(role)}
            self.assertEqual(tools["browser.read_conversation"]["provider"], "playwright_host")
            self.assertEqual(tools["browser.cli"]["provider"], "host_runner")
            for name in ("browser.tabs", "browser.read", "browser.snapshot", "browser.console", "browser.errors", "browser.network", "browser.mcp.list", "browser.mcp.call"):
                self.assertEqual(tools[name]["provider"], "playwright_mcp")
            self.assertTrue(tools["browser.mcp.call"]["mutating"])
            self.assertFalse(tools["browser.mcp.list"]["mutating"])

        helper_tools = {tool["tool_id"]: tool for tool in reg.list_for_role("helper")}
        self.assertEqual(set(helper_tools), {"browser.cli"})
        self.assertEqual(helper_tools["browser.cli"]["provider"], "host_runner")

    def test_reusable_tool_requires_real_implementation_ref(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "harness" / "tools").mkdir(parents=True)
            (root / "harness" / "tool.schema.json").write_text(
                (ROOT / "harness" / "tool.schema.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (root / "harness" / "tools" / "registry.json").write_text(json.dumps({
                "v": 1,
                "tools": [{
                    "v": 1,
                    "tool_id": "browser.missing",
                    "title": "Missing",
                    "status": "ACTIVE",
                    "summary": "Missing provider ref.",
                    "provider": "chrome_extension",
                    "mutating": False,
                    "result_scope": "ephemeral",
                    "roles": ["worker"],
                    "args_schema": {"type": "object"},
                    "implementation_refs": ["extension/does-not-exist.js"],
                }],
            }), encoding="utf-8")
            with self.assertRaises(ToolRegistryError):
                ToolRegistry(root).validate_all()


if __name__ == "__main__":
    unittest.main()
