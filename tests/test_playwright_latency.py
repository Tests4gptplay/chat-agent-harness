import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PlaywrightLatencyContractTests(unittest.TestCase):
    def test_fast_wake_poll_keeps_heavy_maintenance_slow(self):
        config = (ROOT / "playwright_host" / "config.py").read_text(encoding="utf-8")
        self.assertRegex(config, r"wake_poll_seconds:\s*float\s*=\s*1\.0")
        self.assertRegex(config, r"maintenance_seconds:\s*float\s*=\s*60\.0")

    def test_semantic_progress_runs_without_maintenance_being_due(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from playwright_host.runtime import BrowserRuntime

        runtime = object.__new__(BrowserRuntime)
        runtime.cfg = SimpleNamespace(wake_poll_seconds=1.0, maintenance_seconds=60.0)
        runtime._last_wake_poll = 100.0
        runtime._last_maintenance_tick = 100.0
        runtime.workers = Mock()
        runtime.workers.observe.return_value = ['worker-observed']
        runtime.bridge = Mock()
        runtime.state = Mock()
        runtime.state.data = {'lanes': {}}
        order = []
        runtime._process_semantic_tool_calls = Mock(return_value=[])
        runtime._process_role_syscalls = Mock(side_effect=lambda: order.append('sync') or ['sync-observed'])
        runtime._observe_foreground_terminal = Mock()
        runtime._observe_planner_output = Mock(side_effect=lambda value: order.append('planner') or {'planner': 'observed'})
        runtime.process_control = Mock(side_effect=lambda: order.append('control') or {'control': 'reduced'})
        with patch('playwright_host.runtime.time.monotonic', return_value=100.5):
            result = runtime.tick()
        self.assertEqual(order, ['sync', 'planner', 'control'])
        runtime._process_role_syscalls.assert_called_once_with()
        runtime._observe_planner_output.assert_called_once_with(None)
        runtime.process_control.assert_called_once_with()
        runtime.workers.maintain.assert_not_called()
        runtime.workers.liveness_sweep.assert_not_called()
        runtime.workers.poll_lane.assert_not_called()
        runtime.bridge.call.assert_not_called()
        self.assertEqual(result['role_syscalls'], ['sync-observed', {'planner': 'observed'}])
        self.assertEqual(result['control'], {'control': 'reduced'})
        self.assertEqual(result['maintenance'], [])

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
