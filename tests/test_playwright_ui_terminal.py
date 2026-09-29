import unittest
from unittest.mock import MagicMock

from playwright_host.ui import ChatGPTUI


class ChatGPTUITerminalTests(unittest.TestCase):
    def setUp(self):
        self.ui = ChatGPTUI("http://127.0.0.1:9222")

    def test_assistant_fallback_strips_accessibility_heading(self):
        node = MagicMock()
        markdown = MagicMock()
        markdown.count.return_value = 0
        heading = MagicMock()
        heading.count.return_value = 1
        heading.first.inner_text.return_value = "ChatGPT 说："

        def locator(selector):
            if selector == '[data-markdown-text-style="assistant-message"]':
                return markdown
            if selector == 'h4[data-conversation-role="assistant"]':
                return heading
            raise AssertionError(selector)

        node.locator.side_effect = locator
        node.inner_text.return_value = "ChatGPT 说：\n\ndone"

        self.assertEqual(self.ui.message_text(node, "assistant"), "done")

    def test_turn_local_completion_overrides_stale_page_stop(self):
        user = MagicMock(name="user")
        assistant = MagicMock(name="assistant")
        nodes = MagicMock()
        nodes.count.return_value = 2
        nodes.nth.side_effect = lambda index: [user, assistant][index]

        self.ui.message_role = MagicMock(
            side_effect=lambda node: "user" if node is user else "assistant"
        )
        self.ui.message_text = MagicMock(
            side_effect=lambda node, role: "wake" if role == "user" else "done"
        )
        self.ui.response_running = MagicMock(return_value=True)
        self.ui.assistant_turn_complete = MagicMock(return_value=True)

        result = self.ui._turn_after(MagicMock(), nodes, 0)

        self.assertTrue(result["response_ended"])
        self.assertTrue(result["assistant_complete"])
        self.assertEqual(result["signal"], "done")

    def test_submit_prefers_visible_send_button_over_enter(self):
        page = MagicMock()
        composer = MagicMock()
        composer.get_attribute.return_value = "true"
        composer.inner_text.return_value = ""

        send = MagicMock()
        button = MagicMock()
        send.filter.return_value = send
        send.first = button
        page.locator.return_value = send

        self.ui.dismiss_history_rate_limit = MagicMock(return_value=False)
        self.ui.response_running = MagicMock(return_value=False)
        self.ui.assistant_count = MagicMock(return_value=0)
        self.ui.marker_occurrences = MagicMock(return_value=0)
        self.ui.composer = MagicMock(return_value=composer)

        result = self.ui.submit_text(
            page,
            "hello",
            wait_response_start=False,
        )

        self.assertFalse(result["response_started"])
        composer.fill.assert_called_once_with("hello")
        button.wait_for.assert_called_once_with(state="visible", timeout=2500)
        button.click.assert_called_once()
        composer.press.assert_not_called()

    def test_submit_falls_back_to_enter_when_send_control_missing(self):
        page = MagicMock()
        composer = MagicMock()
        composer.get_attribute.return_value = "true"
        composer.inner_text.return_value = ""

        send = MagicMock()
        button = MagicMock()
        send.filter.return_value = send
        send.first = button
        button.wait_for.side_effect = RuntimeError("missing")
        page.locator.return_value = send

        self.ui.dismiss_history_rate_limit = MagicMock(return_value=False)
        self.ui.response_running = MagicMock(return_value=False)
        self.ui.assistant_count = MagicMock(return_value=0)
        self.ui.marker_occurrences = MagicMock(return_value=0)
        self.ui.composer = MagicMock(return_value=composer)

        self.ui.submit_text(page, "hello", wait_response_start=False)

        composer.press.assert_called_once_with("Enter")
        button.click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
