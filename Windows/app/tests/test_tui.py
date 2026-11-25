import asyncio
from concurrent.futures import Future
from pathlib import Path
from typing import Any
import unittest

from ui import textual_available

tui: Any = None
if textual_available():
    import tui as tui_module
    tui = tui_module


OPTIONS = [
    {"optionId": "allow", "kind": "allow_once", "name": "Allow once"},
    {"optionId": "reject", "kind": "reject_once", "name": "Reject"},
]

CONFIG_OPTIONS = [
    {"id": "model-choice", "category": "model", "type": "select",
     "currentValue": "google-default",
     "options": [{"value": "google-default", "name": "Default"},
                 {"value": "offered-model", "name": "Offered"}]},
    {"id": "mode-choice", "category": "mode", "type": "select",
     "currentValue": "ask",
     "options": [{"value": "ask", "name": "Ask"},
                 {"value": "edit", "name": "Edit"}]},
]


class DummyClient:
    session_id = "session"
    config_options: list[Any] = []


@unittest.skipUnless(tui is not None, "textual is not installed")
class TuiBridgeTests(unittest.TestCase):
    def test_permission_without_running_app_is_denied(self):
        bridge = tui.TuiUI()
        self.assertEqual(bridge.permission({"title": "Write"}, OPTIONS),
                         {"outcome": "cancelled"})

    def test_auto_approve_selects_allow_once(self):
        bridge = tui.TuiUI()
        self.assertEqual(bridge.permission({"title": "Write"}, OPTIONS, auto_approve=True),
                         {"outcome": "selected", "optionId": "allow"})


@unittest.skipUnless(tui is not None, "textual is not installed")
class AppTests(unittest.TestCase):
    def make_app(self):
        bridge = tui.TuiUI()
        app = tui.AcpApp(DummyClient(), bridge,
                         {"name": "antigravity-acp", "version": "1.4.5"},
                         {"sessionId": "session"}, Path("/tmp/project"),
                         {"model": ""}, {"http_proxy": "http://127.0.0.1:10809"},
                         Path("/tmp"))
        bridge.attach(app)
        return app, bridge

    def test_app_mounts_and_renders_assistant_text(self):
        async def run() -> None:
            app, _ = self.make_app()
            async with app.run_test() as pilot:
                await pilot.pause()
                self.assertIsNotNone(app.query_one("#prompt"))
                app.add_user("hello")
                app.begin_assistant()
                app.append_assistant("Привет")
                app.end_assistant()
                await pilot.pause()
                self.assertEqual(app.buffer, "Привет")
        asyncio.run(run())

    def test_permission_modal_returns_advertised_option(self):
        async def run() -> None:
            app, _ = self.make_app()
            async with app.run_test() as pilot:
                await pilot.pause()
                future: Future[dict[str, str]] = Future()
                app.ask_permission({"title": "Write"}, OPTIONS, future)
                await pilot.pause()
                await pilot.click("#choice-1")
                await pilot.pause()
                self.assertEqual(future.result(),
                                 {"outcome": "selected", "optionId": "allow"})
        asyncio.run(run())

    def test_escape_denies_a_pending_permission(self):
        async def run() -> None:
            app, _ = self.make_app()
            async with app.run_test() as pilot:
                await pilot.pause()
                future: Future[dict[str, str]] = Future()
                app.ask_permission({"title": "Write"}, OPTIONS, future)
                await pilot.pause()
                await pilot.press("escape")
                await pilot.pause()
                self.assertEqual(future.result(), {"outcome": "cancelled"})
        asyncio.run(run())

    def test_ctrl_c_denies_a_pending_permission(self):
        async def run() -> None:
            app, _ = self.make_app()
            async with app.run_test() as pilot:
                await pilot.pause()
                future: Future[dict[str, str]] = Future()
                app.ask_permission({"title": "Write"}, OPTIONS, future)
                await pilot.pause()
                await pilot.press("ctrl+c")
                await pilot.pause()
                self.assertTrue(future.done())
                self.assertEqual(future.result(), {"outcome": "cancelled"})
        asyncio.run(run())

    def test_models_are_listed_instead_of_raw_json(self):
        text = tui.models_text(CONFIG_OPTIONS)
        self.assertIn("model-choice", text)
        self.assertIn("*Default", text)
        self.assertIn("Offered", text)
        self.assertNotIn('"currentValue"', text)
        self.assertEqual(tui.models_text({"models": {}}),
                         '{\n  "models": {}\n}')

    def test_help_lists_chat_commands(self):
        async def run() -> None:
            app, _ = self.make_app()
            async with app.run_test() as pilot:
                await pilot.pause()
                notices = [str(widget.render()) for widget in app.query("Static.notice")]
                self.assertTrue(any("/models" in text for text in notices))
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
