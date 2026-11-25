"""Full-screen Textual client for the official ACP agent."""
from __future__ import annotations

from concurrent.futures import Future
import json
from pathlib import Path
import threading
from typing import Any, Literal, Sequence

from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, Static
from textual.widgets.markdown import Markdown as MarkdownWidget

from ui import auto_outcome, permission_outcome, stop_reason_text

HELP_TEXT = ("/help — команды · /quit — выход · /new — новая сессия · "
             "/models — список моделей · /model ID — выбрать модель")


def models_text(payload: Any) -> str:
    """Render ACP config options as one line per option, marking the current value."""
    lines: list[str] = []
    if isinstance(payload, list):
        for option in payload:
            choices = option.get("options") if isinstance(option, dict) else None
            if not isinstance(choices, list):
                continue
            names = []
            for choice in choices:
                if isinstance(choice, dict):
                    name = str(choice.get("name", choice.get("value", "?")))
                    names.append("*" + name if choice.get("value") == option.get("currentValue")
                                 else name)
            if names:
                lines.append(f"{option.get('id', 'model')}: " + ", ".join(names))
    if not lines:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    return "Модели (текущая отмечена *):\n" + "\n".join(lines)


class PermissionScreen(ModalScreen[dict[str, Any]]):
    """Modal list of the permission options advertised by the agent."""

    BINDINGS = [("escape", "cancel", "Отмена"), ("ctrl+c", "cancel", "Отмена")]

    def __init__(self, call: dict[str, Any], options: Sequence[dict[str, Any]]) -> None:
        super().__init__()
        self.call_data = call
        self.options = list(options)

    def compose(self) -> ComposeResult:
        from rich.text import Text
        title = str(self.call_data.get("title", "действие агента"))
        with Container(id="dialog"):
            yield Label(Text("Запрос разрешения", style="bold yellow"))
            yield Label(Text(title))
            if self.call_data.get("rawInput") is not None:
                raw = json.dumps(self.call_data["rawInput"], ensure_ascii=False)[:8000]
                yield Static(Text(raw, style="dim"))
            for index, option in enumerate(self.options, 1):
                kind = option.get("kind", "")
                variant: Literal["default", "success", "error"] = (
                    "success" if kind == "allow_once" else (
                        "error" if "reject" in kind else "default"))
                yield Button(f"{index}. {option.get('name', kind or '?')}",
                             id=f"choice-{index}", variant=variant)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id and event.button.id.startswith("choice-"):
            self.dismiss(permission_outcome(self.options, event.button.id.split("-", 1)[1]))

    def action_cancel(self) -> None:
        self.dismiss({"outcome": "cancelled"})


class TuiUI:
    """Adapter between the synchronous ACP client and the Textual app."""

    def __init__(self) -> None:
        self.app: AcpApp | None = None
        self.pending: list[tuple[str, tuple[Any, ...]]] = []
        self._permission_future: Future[dict[str, Any]] | None = None

    def attach(self, app: AcpApp) -> None:
        self.app = app

    def flush(self) -> None:
        if self.app is None:
            return
        pending, self.pending = self.pending, []
        for method, args in pending:
            getattr(self.app, method)(*args)

    def _call(self, method: str, *args: Any) -> None:
        if self.app is None or not self.app.is_running:
            self.pending.append((method, args))
            return
        self.app.call_from_thread(getattr(self.app, method), *args)

    def banner(self, agent: dict[str, Any], cwd: Path, model: str, proxy: str) -> None:
        self._call("set_banner", agent, cwd, model, proxy)

    def info(self, text: str) -> None:
        self._call("add_notice", text)

    def notice(self, text: str) -> None:
        self._call("add_notice", text)

    def assistant_start(self) -> None:
        self._call("begin_assistant")

    def assistant_chunk(self, text: str) -> None:
        self._call("append_assistant", text)

    def assistant_end(self) -> None:
        self._call("end_assistant")

    def tool_call(self, title: str) -> None:
        self._call("add_tool", title)

    def models(self, payload: Any) -> None:
        self._call("add_models", payload)

    def permission(self, call: dict[str, Any], options: Sequence[dict[str, Any]],
                   auto_approve: bool = False) -> dict[str, Any]:
        if auto_approve:
            return auto_outcome(options)
        if self.app is None or not self.app.is_running:
            return {"outcome": "cancelled"}
        future: Future[dict[str, Any]] = Future()
        self._permission_future = future
        self.app.call_from_thread(self.app.ask_permission, call, options, future)
        try:
            return future.result()
        finally:
            self._permission_future = None

    def cancel_pending(self) -> None:
        if self._permission_future is not None and not self._permission_future.done():
            self._permission_future.set_result({"outcome": "cancelled"})


class AcpApp(App[None]):
    """Chat: transcript on top, input at the bottom, status in between."""

    CSS = """
    Screen { background: $surface; }
    #chat { height: 1fr; padding: 1 2; }
    #composer { dock: bottom; height: auto; padding: 0 1; }
    #status { height: 1; color: $text-muted; }
    #prompt { margin: 0 0 1 0; }
    .tool { color: $secondary; }
    .error { color: $error; }
    PermissionScreen { align: center middle; }
    #dialog {
        width: 80%;
        height: auto;
        border: thick $warning;
        padding: 1 2;
        background: $surface;
    }
    #dialog Button { width: 100%; margin: 1 0 0 0; }
    """

    BINDINGS = [("ctrl+q", "quit", "Выход"), ("ctrl+c", "cancel", "Отмена")]

    def __init__(self, client: Any, bridge: TuiUI, info: dict[str, Any], session: dict[str, Any],
                 cwd: Path, state: dict[str, str], config: dict[str, Any], profile: Path) -> None:
        super().__init__()
        self.client = client
        self.bridge = bridge
        self.info = info
        self.session = session
        self.cwd = cwd
        self.state = state
        self.config = config
        self.profile = profile
        self.current: MarkdownWidget | None = None
        self.buffer = ""
        self.busy = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield VerticalScroll(id="chat")
        with Vertical(id="composer"):
            yield Static(id="status")
            yield Input(placeholder="Сообщение агенту...", id="prompt")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "agyCLI"
        self.sub_title = str(self.cwd)
        self.update_status()
        self.set_banner(self.info.get("agentInfo", {}), self.cwd,
                        self.state.get("model", ""), self.config.get("http_proxy", ""))
        self.add_notice(HELP_TEXT)
        self.query_one("#prompt", Input).focus()
        self.bridge.flush()

    def set_banner(self, agent: dict[str, Any], cwd: Path, model: str, proxy: str) -> None:
        lines = [f"Агент: {agent.get('name', 'official ACP')} {agent.get('version', '')}".rstrip(),
                 f"Папка: {cwd}",
                 f"Модель: {model or 'выбор агента'}",
                 f"Прокси: {proxy}"]
        for line in lines:
            self.add_notice(line)

    def update_status(self) -> None:
        model = self.state.get("model") or "выбор агента"
        self.query_one("#status", Static).update(
            f"{self.cwd.name} · {model} · {self.config.get('http_proxy', '')}")

    def _mount(self, widget: Any) -> None:
        chat = self.query_one("#chat", VerticalScroll)
        chat.mount(widget)
        chat.scroll_end(animate=False)

    def add_notice(self, text: str) -> None:
        from rich.text import Text
        self._mount(Static(Text(text, style="dim"), classes="notice"))

    def add_error(self, text: str) -> None:
        from rich.text import Text
        self._mount(Static(Text(text, style="bold red"), classes="error"))

    def add_user(self, text: str) -> None:
        from rich.text import Text
        self._mount(Static(Text.assemble(("Вы: ", "bold cyan"), (text, ""))))

    def add_tool(self, title: str) -> None:
        from rich.text import Text
        self._mount(Static(Text("▸ " + title, style="bold blue"), classes="tool"))

    def add_models(self, payload: Any) -> None:
        self.add_notice(models_text(payload))

    def begin_assistant(self) -> None:
        self.buffer = ""
        self.current = MarkdownWidget("")
        self._mount(self.current)

    def append_assistant(self, text: str) -> None:
        if self.current is None:
            self.begin_assistant()
        assert self.current is not None
        self.buffer += text
        self.current.update(self.buffer)
        self.query_one("#chat", VerticalScroll).scroll_end(animate=False)

    def end_assistant(self) -> None:
        self.current = None

    def ask_permission(self, call: dict[str, Any], options: Sequence[dict[str, Any]],
                       future: Future[dict[str, Any]]) -> None:
        def finish(result: dict[str, Any] | None) -> None:
            if not future.done():
                future.set_result(result or {"outcome": "cancelled"})
        self.push_screen(PermissionScreen(call, options), callback=finish)

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self.busy = busy
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = busy
        if busy:
            self.query_one("#status", Static).update(text)
        else:
            self.update_status()
            prompt.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or self.busy:
            return
        event.input.value = ""
        self.add_user(text)
        if text in ("/quit", "/exit"):
            self.exit()
            return
        if text == "/models":
            self.add_models(self.client.config_options or self.session.get("models", {}))
            return
        if text in ("/help", "?"):
            self.add_notice(HELP_TEXT)
            return
        if text == "/new":
            self._start(self._new_session, "Создаю новую сессию...")
            return
        if text.startswith("/model "):
            self._start(lambda: self._set_model(text[7:].strip()), "Переключаю модель...")
            return
        self._start(lambda: self._prompt(text), "Агент думает...")

    def _start(self, action: Any, status: str) -> None:
        self._set_busy(True, status)
        threading.Thread(target=action, daemon=True).start()

    def _after(self, method: Any, *args: Any) -> None:
        if self.is_running:
            self.call_from_thread(method, *args)

    def _prompt(self, text: str) -> None:
        try:
            self.client.last_update = None
            result = self.client.request("session/prompt", {
                "sessionId": self.client.session_id,
                "prompt": [{"type": "text", "text": text}]}, timeout=3600)
            reason = stop_reason_text(str(result.get("stopReason", "end_turn")))
            self._after(self._finish_turn, reason)
        except Exception as error:  # The worker must always return control to the UI.
            self._after(self._fail_turn, str(error))

    def _finish_turn(self, reason: str) -> None:
        self.end_assistant()
        self._set_busy(False)
        if reason:
            self.add_notice("[" + reason + "]")

    def _fail_turn(self, message: str) -> None:
        self.end_assistant()
        self._set_busy(False)
        self.add_error(message)

    def _new_session(self) -> None:
        from acp_terminal import open_session, remember_session, select_model
        try:
            session = open_session(self.client, self.info, self.cwd)
            if self.state.get("model"):
                select_model(self.client, session, self.state["model"])
            remember_session(self.profile, self.cwd, self.client.session_id)
            self._after(self._session_changed, session)
        except Exception as error:
            self._after(self._fail_turn, str(error))

    def _set_model(self, model: str) -> None:
        from acp_terminal import select_model
        try:
            select_model(self.client, self.session, model)
            self.state["model"] = model
            self._after(self._model_changed, model)
        except Exception as error:
            self._after(self._fail_turn, str(error))

    def _session_changed(self, session: dict[str, Any]) -> None:
        self.session = session
        self._set_busy(False)
        self.add_notice("Создана новая ACP-сессия.")

    def _model_changed(self, model: str) -> None:
        self._set_busy(False)
        self.add_notice("Модель: " + model)

    def action_cancel(self) -> None:
        send = getattr(self.client, "send", None)
        if self.busy and self.client.session_id and send is not None:
            try:
                send({"jsonrpc": "2.0", "method": "session/cancel",
                      "params": {"sessionId": self.client.session_id}})
                self._set_busy(True, "Отмена отправлена; ждём ответа агента...")
            except OSError:
                pass

    async def action_quit(self) -> None:
        self.exit()

    def on_unmount(self) -> None:
        self.bridge.cancel_pending()
        send = getattr(self.client, "send", None)
        if self.client.session_id and send is not None:
            try:
                send({"jsonrpc": "2.0", "method": "session/cancel",
                      "params": {"sessionId": self.client.session_id}})
            except OSError:
                pass


def run_tui(client: Any, info: dict[str, Any], session: dict[str, Any], cwd: Path,
            state: dict[str, str], config: dict[str, Any], profile: Path) -> int:
    bridge = TuiUI()
    client.ui = bridge
    app = AcpApp(client, bridge, info, session, cwd, state, config, profile)
    bridge.attach(app)
    app.run()
    return 0
