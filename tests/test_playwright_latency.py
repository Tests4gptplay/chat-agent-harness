import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PlaywrightLatencyContractTests(unittest.TestCase):
    def test_fast_wake_poll_keeps_heavy_maintenance_slow(self):
        config = (ROOT / "playwright_host" / "config.py").read_text(encoding="utf-8")
        self.assertRegex(config, r"wake_poll_seconds:\s*float\s*=\s*1\.0")
        self.assertRegex(config, r"maintenance_seconds:\s*float\s*=\s*60\.0")

    def test_git_reconciliation_is_not_gated_by_maintenance(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        from playwright_host.runtime import BrowserRuntime
        host = BrowserRuntime.__new__(BrowserRuntime)
        host.cfg = SimpleNamespace(wake_poll_seconds=1.0, maintenance_seconds=60.0)
        host._last_wake_poll = host._last_maintenance_tick = 10.0
        host.state = MagicMock(); host.state.data = {"lanes": {}}
        host.bridge = MagicMock(); host.workers = MagicMock()
        host._poll_harness_errors = MagicMock()
        host._service_error_helper = MagicMock()
        host._process_semantic_tool_calls = MagicMock(return_value=[])
        host._observe_foreground_terminal = MagicMock()
        host.process_control = MagicMock()
        with patch("playwright_host.runtime.time.monotonic", return_value=11.0):
            host.tick()
        host.process_control.assert_called_once()
        host.workers.maintain.assert_not_called()
        host.workers.liveness_sweep.assert_not_called()

    def test_dedicated_cah_chrome_starts_minimized_without_extensions(self):
        launcher = (ROOT / "host" / "start_playwright_host.ps1").read_text(encoding="utf-8")
        self.assertNotIn("--headless", launcher)
        self.assertIn("-WindowStyle Minimized", launcher)
        self.assertIn("'--start-minimized'", launcher)
        self.assertIn("'--window-size=1920,1080'", launcher)
        self.assertIn("'--disable-extensions'", launcher)

    def test_new_tabs_request_background_creation(self):
        import inspect
        from playwright_host.ui import ChatGPTUI
        source = inspect.getsource(ChatGPTUI.new_page)
        self.assertIn('"background": True', source)
        self.assertNotIn("self.context.new_page()", source)


if __name__ == "__main__":
    unittest.main()
