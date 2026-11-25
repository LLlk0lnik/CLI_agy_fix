import io
import unittest

import ui


OPTIONS = [
    {"optionId": "allow", "kind": "allow_once", "name": "Allow once"},
    {"optionId": "always", "kind": "allow_always", "name": "Allow always"},
    {"optionId": "reject", "kind": "reject_once", "name": "Reject"},
]


class PermissionTests(unittest.TestCase):
    def test_empty_choice_is_denied(self):
        self.assertEqual(ui.permission_outcome(OPTIONS, ""), {"outcome": "cancelled"})

    def test_out_of_range_is_denied(self):
        self.assertEqual(ui.permission_outcome(OPTIONS, "9"), {"outcome": "cancelled"})

    def test_selection_uses_advertised_option_id(self):
        self.assertEqual(ui.permission_outcome(OPTIONS, "2"),
                         {"outcome": "selected", "optionId": "always"})

    def test_auto_approve_selects_only_allow_once(self):
        self.assertEqual(ui.auto_outcome(OPTIONS),
                         {"outcome": "selected", "optionId": "allow"})
        self.assertEqual(ui.auto_outcome([OPTIONS[2]]), {"outcome": "cancelled"})

    def test_labels_are_numbered_and_sanitized(self):
        labels = ui.permission_labels([{"optionId": "x", "name": "\x1b[31mAllow\x1b[0m"}])
        self.assertEqual(labels, ["1. Allow"])


class PlainUITests(unittest.TestCase):
    def test_streams_text_and_tool_calls_without_ansi(self):
        output = io.StringIO()
        terminal = ui.TerminalUI(stream=output, rich=False, prompt=False)
        terminal.assistant_start()
        terminal.assistant_chunk("hello")
        terminal.assistant_end()
        terminal.tool_call("\x1b[31mRead file\x1b[0m")
        self.assertIn("hello", output.getvalue())
        self.assertIn("[Read file]", output.getvalue())
        self.assertNotIn("\x1b", output.getvalue())

    def test_permission_prompt_returns_denial_on_empty_input(self):
        output = io.StringIO()
        terminal = ui.TerminalUI(stream=output, rich=False, prompt=False,
                                 input_fn=lambda _: "")
        outcome = terminal.permission({"title": "Write"}, OPTIONS, auto_approve=False)
        self.assertEqual(outcome, {"outcome": "cancelled"})
        self.assertIn("Allow once", output.getvalue())

    def test_permission_honors_auto_approve(self):
        terminal = ui.TerminalUI(stream=io.StringIO(), rich=False, prompt=False,
                                 input_fn=lambda _: self.fail("must not ask"))
        outcome = terminal.permission({"title": "Write"}, OPTIONS, auto_approve=True)
        self.assertEqual(outcome, {"outcome": "selected", "optionId": "allow"})


class CompletionTests(unittest.TestCase):
    def test_commands_and_models_are_suggested(self):
        words = ui.completion_words(["model-a"])
        self.assertIn("/models", words)
        self.assertIn("/model ", words)
        self.assertIn("/quit", words)
        self.assertIn("model-a", words)


if __name__ == "__main__":
    unittest.main()
