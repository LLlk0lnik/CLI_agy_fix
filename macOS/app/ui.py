"""Optional terminal UI: prompt_toolkit input and rich output, with a stdlib fallback."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Iterable, Sequence, TextIO

COMMANDS = ("/quit", "/exit", "/new", "/models", "/model ")


def clean(value: Any) -> str:
    """Remove terminal control sequences from untrusted agent output."""
    value = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", str(value))
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    return "".join(c for c in value if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127))


def permission_outcome(options: Sequence[dict[str, Any]], choice: str) -> dict[str, Any]:
    """Translate a 1-based menu choice into an ACP permission outcome."""
    try:
        index = int(choice) - 1
    except (TypeError, ValueError):
        return {"outcome": "cancelled"}
    if not 0 <= index < len(options):
        return {"outcome": "cancelled"}
    option_id = options[index].get("optionId")
    if not isinstance(option_id, str) or not option_id:
        return {"outcome": "cancelled"}
    return {"outcome": "selected", "optionId": option_id}


def auto_outcome(options: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Select an advertised allow_once option, or deny if the agent offered none."""
    option = next((item for item in options
                   if item.get("kind") == "allow_once" and item.get("optionId")), None)
    if option is None:
        return {"outcome": "cancelled"}
    return {"outcome": "selected", "optionId": option["optionId"]}


def permission_labels(options: Sequence[dict[str, Any]]) -> list[str]:
    return [f"{index}. {clean(item.get('name', item.get('kind', '?')))}"
            for index, item in enumerate(options, 1)]


def completion_words(models: Iterable[str] = ()) -> list[str]:
    return [*COMMANDS, *models]


def textual_available() -> bool:
    """Whether the optional full-screen UI dependency is importable."""
    try:
        import textual  # noqa: F401
    except ImportError:
        return False
    return True


def stop_reason_text(reason: str) -> str:
    """Human status for abnormal ACP stop reasons; end_turn stays silent."""
    return {
        "end_turn": "",
        "stop": "",
        "done": "",
        "cancelled": "отменено",
        "max_tokens": "лимит токенов",
        "max_turn_requests": "лимит шагов",
        "refusal": "отказ",
    }.get(reason, reason)


class TerminalUI:
    """Render ACP events and read input, with rich/prompt_toolkit when available."""

    def __init__(self, stream: TextIO | None = None,
                 input_fn: Callable[[str], str] | None = None,
                 rich: bool | None = None, prompt: bool | None = None,
                 history_path: Path | None = None,
                 models: Callable[[], Sequence[str]] | None = None,
                 status: Callable[[], str] | None = None,
                 color: bool | None = None) -> None:
        self.stream = stream
        self.color = ((self._output().isatty() if color is None else color)
                      and not os.environ.get("NO_COLOR"))
        self._input = input_fn
        self._models = models or (lambda: ())
        self._status = status
        self._status_text = ""
        self.console = self._make_console(rich)
        self.session = self._make_session(prompt, history_path)

    def _output(self) -> TextIO:
        return self.stream if self.stream is not None else sys.stdout

    def _make_console(self, enabled: bool | None) -> Any:
        if enabled is False or not getattr(self._output(), "isatty", lambda: False)():
            return None
        try:
            from rich.console import Console
        except ImportError:
            return None
        return Console(file=self._output(), highlight=False, soft_wrap=True)

    def _make_session(self, enabled: bool | None, history_path: Path | None) -> Any:
        if (enabled is False or self._input is not None
                or not getattr(sys.stdin, "isatty", lambda: False)()
                or not getattr(self._output(), "isatty", lambda: False)()):
            return None
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.completion import WordCompleter
            from prompt_toolkit.history import FileHistory
        except ImportError:
            return None

        history = None
        if history_path is not None:
            history_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            history_path.touch(mode=0o600, exist_ok=True)
            try:
                os.chmod(history_path, 0o600)
            except OSError:
                pass
            history = FileHistory(str(history_path))
        completer = WordCompleter(completion_words(self._models()), ignore_case=True, sentence=True)
        return PromptSession(history=history, completer=completer,
                              complete_while_typing=True,
                              bottom_toolbar=self._toolbar)

    def _toolbar(self) -> str:
        if self._status_text:
            return self._status_text
        return self._status() if self._status is not None else "agyCLI"

    def _write(self, text: str = "", end: str = "\n") -> None:
        self._output().write(text + end)
        self._output().flush()

    def _style(self, text: str, code: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.color else text

    def set_status(self, text: str) -> None:
        self._status_text = text

    def ask(self, prompt_text: str) -> str:
        if self.session is not None:
            return self.session.prompt(prompt_text)
        if self._input is not None:
            return self._input(prompt_text)
        return input(prompt_text)

    def banner(self, agent: dict[str, Any], cwd: Path, model: str, proxy: str) -> None:
        name = clean(agent.get("name", "official ACP"))
        version = clean(agent.get("version", ""))
        lines = [f"Агент: {name} {version}".rstrip(), f"Папка: {cwd}",
                 f"Модель: {model or 'выбор агента'}", f"Прокси: {proxy}"]
        if self.console is not None:
            from rich.panel import Panel
            from rich.text import Text
            self.console.print(Panel(Text("\n".join(lines)), title="agyCLI", border_style="cyan"))
            return
        for line in lines:
            label, _, value = line.partition(": ")
            self._write(self._style(label + ":", "1;36") + (" " + value if value else ""))

    def info(self, text: str) -> None:
        if self.console is not None:
            self.console.print(clean(text), markup=False, highlight=False)
            return
        self._write(clean(text))

    def notice(self, text: str) -> None:
        if self.console is not None:
            self.console.print(clean(text), style="dim", markup=False, highlight=False)
            return
        self._write(clean(text))

    def assistant_start(self) -> None:
        if self.console is not None:
            from rich.text import Text
            self.console.print(Text("Агент: ", style="bold green"), end="")
            return
        self._write("\n" + self._style("Агент:", "1;32") + " ", end="")

    def assistant_chunk(self, text: str) -> None:
        text = clean(text)
        if self.console is not None:
            self.console.print(text, end="", markup=False, highlight=False, soft_wrap=True)
            return
        self._write(text, end="")

    def assistant_end(self) -> None:
        if self.console is not None:
            self.console.print()
            return
        self._write()

    def tool_call(self, title: str) -> None:
        title = clean(title)
        if self.console is not None:
            from rich.text import Text
            self.console.print(Text("▸ " + title, style="bold blue"))
            return
        self._write("\n" + self._style("▸ " + title, "1;34"))

    def permission(self, call: dict[str, Any], options: Sequence[dict[str, Any]],
                   auto_approve: bool = False) -> dict[str, Any]:
        title = clean(call.get("title", "действие агента"))
        if self.console is not None:
            from rich.panel import Panel
            from rich.table import Table
            from rich.text import Text
            self.console.print()
            self.console.print(Panel(Text(title), title="разрешение", border_style="yellow"))
            if call.get("rawInput") is not None:
                raw = clean(json.dumps(call["rawInput"], ensure_ascii=False))[:8000]
                self.console.print(Text(raw), style="dim")
            table = Table(show_header=True, header_style="bold")
            table.add_column("№", justify="right")
            table.add_column("Действие")
            for index, option in enumerate(options, 1):
                table.add_row(str(index), clean(option.get("name", option.get("kind", "?"))))
            self.console.print(table)
        else:
            self._write("\n" + self._style("Запрос разрешения:", "1;33") + " " + title)
            if call.get("rawInput") is not None:
                self._write(clean(json.dumps(call["rawInput"], ensure_ascii=False))[:8000])
            for label in permission_labels(options):
                self._write("  " + self._style(label, "36"))
        if auto_approve:
            return auto_outcome(options)
        try:
            return permission_outcome(options, self.ask("Номер разрешения, Enter — отказ: "))
        except (ValueError, EOFError):
            return {"outcome": "cancelled"}

    def models(self, payload: Any) -> None:
        if self.console is not None:
            self.console.print_json(data=payload)
            return
        self._write(clean(json.dumps(payload, ensure_ascii=False, indent=2)))

