#!/usr/bin/env python3
"""Run the native agy UI against a private, loopback-only Gemini/CCPA bridge."""
import contextlib
import http.server
import io
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from launcher_common import (configuration, clean_environment, proxy_environment,
                             private_home_environment, runtime_path, client_user_agent)

ROOT = Path(__file__).resolve().parent
PROD = 'https://cloudcode-pa.googleapis.com'
DAILY = 'https://daily-cloudcode-pa.googleapis.com'
USER_AGENT = client_user_agent()


class ReauthenticationRequired(RuntimeError):
    """The saved refresh token was revoked/expired; no retry can repair it."""


def unwrap(value):
    return value.get('response', value) if isinstance(value, dict) else value


def resolve_entitlement(data):
    """Honor an existing tier; do not create or fabricate entitlements."""
    if not data.get('currentTier') or not data.get('cloudaicompanionProject'):
        raise RuntimeError('Google не подтвердил действующий тариф и проект. Выполните отдельный ACP-вход.')
    endpoint = PROD if (data.get('paidTier') or {}).get('usesGcpTos', False) else DAILY
    return data['cloudaicompanionProject'], endpoint


def canonical_model(model, catalog):
    if model in catalog:
        return model
    if model + '-tiered' in catalog:
        return model + '-tiered'
    if model.endswith('-preview') and model[:-8] in catalog:
        return model[:-8]
    return model


def native_command(binary, arguments, default_model='gemini-3.8-flash-high'):
    args = []
    settings = {}
    iterator = iter(arguments)
    for arg in iterator:
        if arg == '--settings':
            value = next(iterator, None)
            if value is None:
                raise ValueError('--settings requires a value')
        elif arg.startswith('--settings='):
            value = arg.split('=', 1)[1]
        else:
            args.append(arg)
            continue
        try:
            supplied = json.loads(value)
        except ValueError:
            supplied = json.loads(Path(value).expanduser().read_text())
        if not isinstance(supplied, dict):
            raise ValueError('--settings must be a JSON object')
        settings.update(supplied)
    settings['modelProvider'] = 'gemini'
    command = [str(binary), *args, '--settings', json.dumps(settings)]
    if not any(a == '--model' or a.startswith('--model=') for a in args):
        command += ['--model', default_model]
    return command


class Backend:
    def __init__(self, token_path, config=None):
        self.token_path = token_path
        config = configuration() if config is None else config
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({
            'http': config['http_proxy'], 'https': config['http_proxy']}))
        self.lock = threading.Lock()
        self.access_token = None
        self.expires = 0
        self.expires_at = 0
        self.project = None
        self.endpoint = None
        self.catalog = None

    def post(self, url, data, token=None, form=False):
        body = (urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode())
        headers = {'Content-Type': 'application/x-www-form-urlencoded' if form else 'application/json',
                   'User-Agent': USER_AGENT}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        return self.opener.open(urllib.request.Request(url, data=body, headers=headers), timeout=180)

    def credentials(self, rejected_token=None):
        with self.lock:
            if rejected_token is not None and self.access_token == rejected_token:
                self.expires = self.expires_at = 0
            # The wall-clock deadline also catches sleep on clocks that pause in suspend.
            # Keep monotonic time too, so setting the wall clock backwards cannot extend TTL.
            if (self.access_token and time.monotonic() < self.expires
                    and time.time() < self.expires_at):
                return self.access_token, self.project, self.endpoint
            try:
                data = json.loads(self.token_path.read_text())
            except FileNotFoundError:
                raise RuntimeError('Нет отдельного ACP-профиля. Сначала запустите Login из папки setup.') from None
            # Credentials only go to Google's fixed OAuth endpoint, never a URL from a file.
            form = {'grant_type': 'refresh_token', 'client_id': data['client_id'],
                    'client_secret': data['client_secret'], 'refresh_token': data['refresh_token']}
            started_monotonic, started_wall = time.monotonic(), time.time()
            try:
                with self.post('https://oauth2.googleapis.com/token', form, form=True) as response:
                    fresh = json.load(response)
            except urllib.error.HTTPError as error:
                if error.code == 400:
                    raw = error.read()
                    try:
                        reason = json.loads(raw).get('error')
                    except (ValueError, AttributeError):
                        reason = None
                    if reason == 'invalid_grant':
                        error.close()
                        raise ReauthenticationRequired(
                            'Google отозвал или просрочил refresh token. Выполните Login из папки setup.'
                        ) from None
                    error.close()
                    raise urllib.error.HTTPError(error.url, error.code, error.reason,
                                                 error.headers, io.BytesIO(raw)) from None
                raise
            token = fresh['access_token']
            with self.post(PROD + '/v1internal:loadCodeAssist',
                           {'metadata': {'ideType': 'ANTIGRAVITY'}}, token) as response:
                entitlement = json.load(response)
            project, endpoint = resolve_entitlement(entitlement)
            self.access_token, self.project, self.endpoint = token, project, endpoint
            lifetime = max(0, int(fresh.get('expires_in', 3600)) - 60)
            self.expires = started_monotonic + lifetime
            self.expires_at = started_wall + lifetime
            return token, project, endpoint

    def authenticated_post(self, method, payload):
        token, project, endpoint = self.credentials()
        try:
            return self.post(endpoint + '/v1internal:' + method, payload(project), token)
        except urllib.error.HTTPError as error:
            if error.code != 401:
                raise
            error.close()
        # Refresh under the credential lock only if this is still the rejected token.
        # Another request may already have refreshed it while we were in flight.
        token, project, endpoint = self.credentials(rejected_token=token)
        # Exactly one retry, before any response body is handed to the caller.
        return self.post(endpoint + '/v1internal:' + method, payload(project), token)

    def models(self):
        with self.authenticated_post('fetchAvailableModels', lambda project: {'project': project}) as response:
            return json.load(response)

    def generate(self, model, body, streaming):
        if self.catalog is None:
            self.catalog = self.models().get('models', {})
        model = canonical_model(model, self.catalog)
        rpc = 'streamGenerateContent?alt=sse' if streaming else 'generateContent'
        return self.authenticated_post(rpc, lambda project: {
            'project': project, 'model': model, 'request': body})


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


class Handler(http.server.BaseHTTPRequestHandler):
    # HTTP/1.0 closes streaming responses at EOF, without a chunked-encoding mismatch.
    protocol_version = 'HTTP/1.0'

    def log_message(self, *args):
        pass  # Never log keys, authorization headers, prompt bodies or OAuth responses.

    def reply(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        key = self.headers.get('x-goog-api-key') or query.get('key', [''])[0]
        bearer = self.headers.get('Authorization', '')
        if bearer.startswith('Bearer '):
            key = bearer[7:]
        if secrets.compare_digest(key, self.server.local_key):
            return True
        self.reply(401, {'error': {'code': 401, 'message': 'Local bridge authentication required'}})
        return False

    def do_GET(self):
        if not self.authorized():
            return
        try:
            data = self.server.backend.models()
            models = data.get('models', {})
            if isinstance(models, dict):
                models = [dict(v, name='models/' + k) for k, v in models.items()]
            self.reply(200, {'models': models})
        except ReauthenticationRequired as error:
            self.reply(401, {'error': {'code': 401, 'message': str(error)}})
        except Exception:
            self.reply(502, {'error': {'code': 502, 'message': 'Failed to load backend models'}})

    def do_POST(self):
        if not self.authorized():
            return
        match = re.fullmatch(r'/(?:v1beta|v1)/models/([^/:]+):(streamGenerateContent|generateContent|countTokens)',
                             urllib.parse.urlsplit(self.path).path)
        if not match:
            self.reply(404, {'error': {'code': 404, 'message': 'Unsupported Gemini method'}})
            return
        streaming = match[2] == 'streamGenerateContent'
        if match[2] == 'countTokens':
            self.reply(501, {'error': {'code': 501, 'message': 'countTokens is not supported by this bridge'}})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 32 * 1024 * 1024:
                self.reply(413, {'error': {'code': 413, 'message': 'Invalid request size'}})
                return
            body = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError):
            self.reply(400, {'error': {'code': 400, 'message': 'Invalid JSON request'}})
            return
        try:
            upstream = self.server.backend.generate(urllib.parse.unquote(match[1]), body, streaming)
        except ReauthenticationRequired as error:
            self.reply(401, {'error': {'code': 401, 'message': str(error)}})
            return
        except urllib.error.HTTPError as error:
            # Relay Google's actual error; no eligibility or model response is forged.
            raw = error.read()
            print('Шлюз: Google HTTP ' + str(error.code) + ', модель ' + match[1], file=sys.stderr)
            error.close()
            try:
                data = unwrap(json.loads(raw))
            except ValueError:
                data = {'error': {'code': error.code, 'message': 'Google backend rejected the request'}}
            self.reply(error.code, data)
            return
        except (OSError, ValueError, RuntimeError, KeyError):
            self.reply(502, {'error': {'code': 502, 'message': 'Local bridge backend connection failed'}})
            return
        with contextlib.closing(upstream):
            if not streaming:
                self.reply(upstream.status, unwrap(json.load(upstream)))
                return
            self.send_response(upstream.status)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            try:
                for line in upstream:
                    if not line.strip():
                        self.wfile.write(b'\n')
                        self.wfile.flush()
                        continue
                    if line.startswith(b'data:'):
                        payload = line[5:].strip()
                        if payload and payload != b'[DONE]':
                            try:
                                payload = json.dumps(unwrap(json.loads(payload))).encode()
                            except (ValueError, UnicodeError):
                                pass
                        self.wfile.write(b'data: ' + payload + b'\n')
                    else:
                        self.wfile.write(line)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass


def run():
    os.umask(0o077)
    native_home = ROOT / 'profile' / 'native-home'
    native_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = configuration()
    backend = Backend(ROOT / 'profile/gemini/antigravity-acp/acp_token.json', config)
    # Resolve and verify the account before opening any local listener.
    backend.credentials()
    if sys.argv[1:] == ['--check']:
        backend.models()
        print('OK: отдельная авторизация, действующий тариф и каталог моделей доступны через Happ.')
        print('Профиль штатного CLI: ' + str(native_home))
        return 0
    server = Server(('127.0.0.1', 0), Handler)
    server.backend = backend
    server.local_key = secrets.token_urlsafe(32)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    env = private_home_environment(proxy_environment(clean_environment(), config), native_home)
    env.update(GEMINI_API_KEY=server.local_key,
               GOOGLE_GEMINI_BASE_URL='http://127.0.0.1:' + str(server.server_port),
               AGY_CLI_DISABLE_AUTO_UPDATE='true')
    if os.name == 'nt':
        Path(env['APPDATA']).mkdir(parents=True, exist_ok=True)
        Path(env['LOCALAPPDATA']).mkdir(parents=True, exist_ok=True)
    binary = runtime_path('agy')
    command = native_command(binary, sys.argv[1:], config['default_model'])
    print('agy CLI → отдельный локальный шлюз; профиль: ' + str(native_home), flush=True)
    try:
        return subprocess.call(command, env=env)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


if __name__ == '__main__':
    try:
        sys.exit(run())
    except KeyboardInterrupt:
        sys.exit(130)
    except urllib.error.HTTPError as exc:
        print('Google отклонил запрос авторизации/тарифа: HTTP ' + str(exc.code), file=sys.stderr)
        sys.exit(1)
    except (OSError, RuntimeError, KeyError, ValueError) as exc:
        print('Не удалось запустить локальный шлюз (' + type(exc).__name__ + '). '
              'Проверьте Happ/app/config.json и выполните Login из папки setup.', file=sys.stderr)
        sys.exit(1)
