#!/usr/bin/env python3
"""Small ACP v1 terminal client. No dependencies beyond Python 3.9+."""
import argparse
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from launcher_common import acp_environment, runtime_path, process_group_options, terminate_owned_process


def display(value):
    # Agent output must not execute terminal control sequences.
    value = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", str(value))
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    return "".join(c for c in value if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127))


class RPCError(RuntimeError):
    def __init__(self, error):
        self.code = error.get("code")
        super().__init__(display(error.get("message", str(error))))


class Client:
    def __init__(self, command, env, log_path):
        self.log = open(log_path, "a", encoding="utf-8")
        os.chmod(log_path, 0o600)
        self.proc = subprocess.Popen(command, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", errors="replace",
                                     bufsize=1, **process_group_options())
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
                    self.events.put(json.loads(line))
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
                print("\n" + display(line), flush=True)

    def send(self, message):
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params, timeout=300):
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
                    print()
                content = update.get("content", {})
                if content.get("type") == "text":
                    print(display(content.get("text", "")), end="", flush=True)
            elif kind in ("tool_call", "tool_call_update"):
                title = update.get("title")
                if title:
                    print("\n[" + display(title) + "]", flush=True)
            self.last_update = kind
        if "id" not in message:
            return
        reply = {"jsonrpc": "2.0", "id": message["id"]}
        if method == "session/request_permission":
            call = params.get("toolCall", {})
            print("\nЗапрос разрешения: " + display(call.get("title", "действие агента")))
            if call.get("rawInput") is not None:
                print(display(json.dumps(call["rawInput"], ensure_ascii=False))[:8000])
            options = params.get("options", [])
            for i, option in enumerate(options, 1):
                print(f"  {i}. {display(option.get('name', option.get('kind', '?')))}")
            try:
                choice = input("Номер разрешения, Enter — отказ: ").strip()
                index = int(choice) - 1
                if not 0 <= index < len(options):
                    raise ValueError()
                outcome = {"outcome": "selected", "optionId": options[index]["optionId"]}
            except (ValueError, EOFError):
                outcome = {"outcome": "cancelled"}
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


def main():
    parser = argparse.ArgumentParser(description="Отдельный Antigravity ACP в терминале")
    parser.add_argument("--check", action="store_true", help="проверка ACP без входа")
    parser.add_argument("--login", action="store_true", help="вход в отдельный профиль")
    parser.add_argument("--prompt", help="один запрос вместо интерактивного диалога")
    parser.add_argument("--model", help="ID модели")
    parser.add_argument("--cwd", type=Path, default=Path.cwd(), help="рабочая папка")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    os.umask(0o077)
    profile = root / "profile"
    profile.mkdir(mode=0o700, exist_ok=True)
    env = child_environment(root)
    cwd = args.cwd.expanduser().resolve(strict=True)
    if not cwd.is_dir():
        parser.error("--cwd должна быть папкой")
    client = Client([str(runtime_path('acp', root))], env, profile / "server.log")
    try:
        info = client.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": "agycli-terminal", "version": "1.0.0"}}, timeout=30)
        print("ACP: " + display(json.dumps(info.get("agentInfo", {}), ensure_ascii=False)))
        if args.check:
            print("OK: отдельный ACP запущен; вход не выполнялся.\nПрофиль: " + str(profile))
            return
        if args.login:
            print("Вход в отдельный профиль. При необходимости откроется браузер.")
            client.request("authenticate", {"methodId": "oauth-personal"}, timeout=600)
            print("Вход завершён.")
            return

        def new_session():
            try:
                return client.request("session/new", {"cwd": str(cwd), "mcpServers": []})
            except RPCError as exc:
                if exc.code != -32000:
                    raise
                print("Нужен вход в отдельный профиль. При необходимости откроется браузер.")
                client.request("authenticate", {"methodId": "oauth-personal"}, timeout=600)
                return client.request("session/new", {"cwd": str(cwd), "mcpServers": []})

        session = new_session()
        client.session_id = session["sessionId"]
        if args.model:
            client.request("session/set_model", {"sessionId": client.session_id, "modelId": args.model})
        print("Рабочая папка: " + str(cwd))
        print("/quit — выход; /new — новая сессия; /models — список; /model ID — модель")
        while True:
            try:
                prompt = args.prompt if args.prompt is not None else input("\nВы> ")
            except EOFError:
                break
            if not prompt.strip():
                if args.prompt is not None:
                    break
                continue
            if args.prompt is None and prompt.strip() in ("/quit", "/exit"):
                break
            if args.prompt is None and prompt.strip() == "/models":
                print(display(json.dumps(session.get("models", session.get("configOptions", [])), ensure_ascii=False, indent=2)))
                continue
            if args.prompt is None and prompt.strip() == "/new":
                session = new_session()
                client.session_id = session["sessionId"]
                continue
            if args.prompt is None and prompt.startswith("/model "):
                client.request("session/set_model", {"sessionId": client.session_id, "modelId": prompt[7:].strip()})
                continue
            client.last_update = None
            result = client.request("session/prompt", {"sessionId": client.session_id,
                                    "prompt": [{"type": "text", "text": prompt}]}, timeout=3600)
            print("\n[" + display(result.get("stopReason", "готово")) + "]")
            if args.prompt is not None:
                break
    finally:
        client.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nОстановлено; отдельный ACP-процесс закрыт.", file=sys.stderr)
        sys.exit(130)
    except (OSError, RuntimeError, TimeoutError) as exc:
        print("\nОшибка: " + display(exc), file=sys.stderr)
        sys.exit(1)
