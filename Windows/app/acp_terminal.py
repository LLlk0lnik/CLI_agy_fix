#!/usr/bin/env python3
"""Small ACP v1 terminal client. No dependencies beyond Python 3.9+."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from launcher_common import (acp_environment, runtime_path, process_group_options,
                             terminate_owned_process, configuration, require_proxy, verify_acp_runtime)
from ui import TerminalUI, clean


def display(value):
    # Agent output must not execute terminal control sequences.
    return clean(value)


class RPCError(RuntimeError):
    def __init__(self, error):
        self.code = error.get("code")
        super().__init__(display(error.get("message", str(error))))


class Client:
    def __init__(self, command, env, log_path, cwd=None, auto_approve=False, ui=None):
        self.ui = ui or TerminalUI(rich=False, prompt=False)
        self.log = open(log_path, "a", encoding="utf-8")
        os.chmod(log_path, 0o600)
        try:
            self.proc = subprocess.Popen(command, env=env, cwd=cwd, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         text=True, encoding="utf-8", errors="replace",
                                         bufsize=1, **process_group_options())
        except BaseException:
            self.log.close()
            raise
        self.auto_approve = auto_approve
        self.answer_seen = False
        self.config_options = []
        self.events = queue.Queue()
        self.counter = 0
        self.session_id = None
        self.last_update = None
        self.readers = [threading.Thread(target=self.read_stdout, daemon=True),
                        threading.Thread(target=self.read_stderr, daemon=True)]
        for reader in self.readers:
            reader.start()

    def read_stdout(self):
        try:
            for line in self.proc.stdout:
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError("Expected JSON-RPC object")
                    self.events.put(message)
                except ValueError:
                    self.log.write("Non-JSON stdout: " + line)
                    self.log.flush()
        finally:
            self.events.put(None)

    def read_stderr(self):
        for line in self.proc.stderr:
            self.log.write(line)
            self.log.flush()
            # The browser login URL is needed for the separate profile.
            if "Open the following link" in line:
                self.ui.notice("\n" + display(line).rstrip())

    def send(self, message):
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params, timeout=300):
        if method == "session/prompt":
            self.answer_seen = False
        self.counter += 1
        request_id = self.counter
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("ACP не ответил вовремя: " + method)
            try:
                message = self.events.get(timeout=min(remaining, 1))
            except queue.Empty:
                continue
            if message is None:
                raise RuntimeError("ACP-сервер завершился. Подробности в profile/server.log")
            if message.get("method"):
                self.handle(message)
            elif message.get("id") == request_id:
                if "error" in message:
                    raise RPCError(message["error"])
                return message.get("result", {})

    def handle(self, message):
        method = message["method"]
        params = message.get("params", {})
        if method == "session/update":
            update = params.get("update", {})
            kind = update.get("sessionUpdate")
            if kind == "agent_message_chunk":
                if self.last_update != kind:
                    self.ui.assistant_start()
                content = update.get("content", {})
                if content.get("type") == "text":
                    self.answer_seen |= bool(content.get("text", "").strip())
                    self.ui.assistant_chunk(display(content.get("text", "")))
            elif kind in ("tool_call", "tool_call_update"):
                title = update.get("title")
                if title:
                    self.ui.tool_call(display(title))
            elif kind == "config_option_update":
                self.config_options = update.get("configOptions", [])
            self.last_update = kind
        if "id" not in message:
            return
        reply = {"jsonrpc": "2.0", "id": message["id"]}
        if method == "session/request_permission":
            outcome = self.ui.permission(params.get("toolCall", {}), params.get("options", []),
                                         auto_approve=self.auto_approve)
            reply["result"] = {"outcome": outcome}
        else:
            reply["error"] = {"code": -32601, "message": "Client method not supported: " + method}
        self.send(reply)

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                terminate_owned_process(self.proc)
        # Only this client's newly created process group; never Zed's processes.
        terminate_owned_process(self.proc)
        for reader in self.readers:
            reader.join(timeout=2)
        if not any(reader.is_alive() for reader in self.readers):
            self.log.close()
        self.proc.stdout.close()
        self.proc.stderr.close()
        if not self.proc.stdin.closed:
            self.proc.stdin.close()


def child_environment(root):
    return acp_environment(root)


def initialize(client):
    info = client.request("initialize", {
        "protocolVersion": 1,
        "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                               "terminal": False},
        "clientInfo": {"name": "agycli-terminal", "title": "agyCLI Terminal", "version": "2.0.0"}},
        timeout=30)
    if info.get("protocolVersion") != 1:
        raise RuntimeError("Unsupported ACP protocol version")
    return info


def authenticate(client, info):
    # The official agent owns OAuth, tokens, refresh and account eligibility.
    if not any(m.get("id") == "oauth-personal" for m in info.get("authMethods", [])):
        raise RuntimeError("Agent did not advertise oauth-personal authentication")
    print("Вход через официальный агент Google. При необходимости откроется браузер.")
    client.request("authenticate", {"methodId": "oauth-personal"}, timeout=600)


def open_session(client, info, cwd, session_id=None):
    if session_id and not info.get("agentCapabilities", {}).get("loadSession"):
        raise RuntimeError("Agent does not support loading sessions")
    method = "session/load" if session_id else "session/new"
    params = {"cwd": str(cwd), "mcpServers": []}
    if session_id:
        params["sessionId"] = session_id
    try:
        session = client.request(method, params)
    except RPCError as exc:
        if exc.code != -32000:
            raise
        authenticate(client, info)
        session = client.request(method, params)  # Exactly once; never retry a 403.
    client.session_id = session_id or session["sessionId"]
    client.config_options = session.get("configOptions", [])
    return session


def option_values(option):
    for value in option.get("options", []):
        if "options" in value:
            yield from value["options"]
        else:
            yield value


def select_model(client, session, model):
    # Zed uses the server's config options; older ACP agents expose models instead.
    option = next((o for o in client.config_options if o.get("category") == "model"), None)
    if option:
        if model not in {v.get("value") for v in option_values(option)}:
            raise ValueError("Модель не предложена агентом. Посмотрите /models.")
        result = client.request("session/set_config_option", {
            "sessionId": client.session_id, "configId": option["id"], "value": model})
        client.config_options = result.get("configOptions", client.config_options)
    else:
        offered = session.get("models", {}).get("availableModels", [])
        if model not in {v.get("modelId") for v in offered}:
            raise ValueError("Модель не предложена агентом. Посмотрите /models.")
        client.request("session/set_model", {"sessionId": client.session_id, "modelId": model})


def remember_session(profile, cwd, session_id):
    # Only an ACP session ID and workspace path; never open the agent's credentials.
    pointer = profile / "last-session.json"
    temporary = pointer.with_suffix(".tmp")
    temporary.write_text(json.dumps({"sessionId": session_id, "cwd": str(cwd)}) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(pointer)


def main(argv=None, root=None):
    parser = argparse.ArgumentParser(description="Терминальный ACP-клиент официального агента Google")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="ACP-рукопожатие без входа и генерации")
    mode.add_argument("--login", action="store_true", help="вход средствами официального агента")
    parser.add_argument("-p", "--print", "--prompt", dest="prompt", help="один запрос")
    parser.add_argument("--model", help="ID модели из списка официального агента")
    resume = parser.add_mutually_exclusive_group()
    resume.add_argument("-c", "--continue", dest="resume", action="store_true", help="продолжить последнюю ACP-сессию")
    resume.add_argument("--conversation", help="продолжить ACP-сессию по ID")
    parser.add_argument("-y", "-d", "--yolo", "--danger", "--skip-permissions",
                        "--dangerously-skip-permissions", dest="auto_approve", action="store_true",
                        help="одобрять запрошенные агентом разрешения на одну операцию")
    parser.add_argument("--no-save-session", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--plain", action="store_true",
                        help="не использовать rich/prompt_toolkit (только stdlib)")
    parser.add_argument("--cwd", type=Path, default=Path.cwd(), help="рабочая папка")
    arguments = list(sys.argv[1:] if argv is None else argv)
    # Only the leading command is an alias; a prompt whose text is 'yolo' stays intact.
    if arguments[:1] == ["yolo"]:
        arguments[0] = "--yolo"
    args = parser.parse_args(arguments)
    if (args.check or args.login) and (args.prompt is not None or args.resume or args.conversation):
        parser.error("--check/--login cannot be combined with a prompt or session resume")
    if args.prompt is not None and not args.prompt.strip():
        parser.error("Запрос не должен быть пустым")
    root = Path(__file__).resolve().parent if root is None else Path(root)
    os.umask(0o077)
    profile = root / "profile"
    profile.mkdir(mode=0o700, exist_ok=True)
    config = configuration(root)
    cwd = args.cwd.expanduser().resolve(strict=True)
    if not cwd.is_dir():
        parser.error("--cwd должна быть папкой")
    session_id = args.conversation
    if args.resume:
        try:
            previous = json.loads((profile / "last-session.json").read_text())
        except FileNotFoundError:
            raise RuntimeError("Нет предыдущей ACP-сессии. Сначала запустите agym без -c.") from None
        if previous.get("cwd") != str(cwd):
            raise RuntimeError("Последняя ACP-сессия относится к другой папке. Перейдите в неё или укажите --conversation ID.")
        session_id = previous["sessionId"]
    verify_acp_runtime(root)
    if not args.check:
        require_proxy(config)
    plain = args.plain or os.environ.get("AGYM_PLAIN") == "1"
    interactive = not (args.check or args.login or args.prompt is not None)
    state = {"model": args.model or config['default_model']}
    ui = TerminalUI(history_path=profile / "ui-history",
                    rich=False if plain else None,
                    prompt=(False if plain or not interactive else None),
                    status=lambda: (cwd.name + ' · ' + (state['model'] or 'выбор агента') +
                                    ' · ' + config['http_proxy']))
    client = Client([str(runtime_path('acp', root))], child_environment(root),
                    profile / "server.log", cwd=cwd, auto_approve=args.auto_approve, ui=ui)
    try:
        info = initialize(client)
        ui.banner(info.get("agentInfo", {}), cwd, state["model"], config["http_proxy"])
        if args.check:
            ui.info("OK: официальный ACP запущен; вход и запросы к модели не выполнялись.")
            return 0
        if args.login:
            authenticate(client, info)
            ui.info("Вход завершён.")
            return 0
        session = open_session(client, info, cwd, session_id)
        if state["model"]:
            select_model(client, session, state["model"])
        if not args.no_save_session:
            remember_session(profile, cwd, client.session_id)
        ui.info("Рабочая папка: " + str(cwd))
        ui.info("/quit — выход; /new — новая сессия; /models — список; /model ID — модель")
        while True:
            try:
                prompt = args.prompt if args.prompt is not None else ui.ask("\nВы> ")
            except EOFError:
                break
            if not prompt.strip():
                if args.prompt is not None:
                    raise ValueError("Запрос не должен быть пустым")
                continue
            if args.prompt is None and prompt.strip() in ("/quit", "/exit"):
                break
            if args.prompt is None and prompt.strip() == "/models":
                ui.models(client.config_options or session.get("models", {}))
                continue
            if args.prompt is None and prompt.strip() == "/new":
                session = open_session(client, info, cwd)
                if state["model"]:
                    select_model(client, session, state["model"])
                if not args.no_save_session:
                    remember_session(profile, cwd, client.session_id)
                continue
            if args.prompt is None and prompt.startswith("/model "):
                try:
                    chosen = prompt[7:].strip()
                    select_model(client, session, chosen)
                    state["model"] = chosen
                except ValueError as exc:
                    ui.info(str(exc))
                continue
            client.last_update = None
            result = client.request("session/prompt", {"sessionId": client.session_id,
                                    "prompt": [{"type": "text", "text": prompt}]}, timeout=3600)
            ui.info("\n[" + display(result.get("stopReason", "готово")) + "]")
            if args.prompt is not None:
                if not client.answer_seen:
                    raise RuntimeError("Агент не вернул текстовый ответ; успешная генерация не подтверждена.")
                break
        return 0
    except KeyboardInterrupt:
        if client.session_id:
            try:
                client.send({"jsonrpc": "2.0", "method": "session/cancel",
                             "params": {"sessionId": client.session_id}})
            except OSError:
                pass
        raise
    finally:
        client.close()


def entrypoint():
    try:
        return main()
    except KeyboardInterrupt:
        print("\nОстановлено; отдельный ACP-процесс закрыт.", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print("\nОшибка: " + display(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(entrypoint())
