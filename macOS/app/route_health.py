"""Per-process route evidence; no DNS, routing-table or VPN configuration changes."""
import json
import threading
import time
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A new redirected Request could lose the explicit loopback proxy binding.
        return None


class NoAvailableRoute(RuntimeError):
    def __init__(self):
        super().__init__('Все настроенные прокси получили региональный отказ. Смените сервер Happ '
                         'или повторите через две минуты; прямое подключение отключено.')


def classify_error(status, body):
    try:
        data = json.loads(body) if isinstance(body, (bytes, str)) else body
        error = data.get('error', {})
        message = error.get('message', '') if isinstance(error, dict) else ''
    except (ValueError, AttributeError):
        message = ''
    if status == 401:
        return 'authentication'
    if status == 400 and 'user location is not supported' in message.lower():
        return 'region'
    if status == 403:
        return 'permission'
    if status == 429:
        return 'quota'
    return 'http'


def has_model_answer(value):
    if not isinstance(value, dict):
        return False
    value = value.get('response', value)
    if not isinstance(value, dict):
        return False
    for candidate in value.get('candidates', []) or []:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get('content') or {}
        if not isinstance(content, dict):
            continue
        for part in content.get('parts', []) or []:
            if isinstance(part, dict) and (part.get('text') or part.get('functionCall')):
                return True
    return False


class RoutePool:
    def __init__(self, primary, fallbacks=()):
        self.routes = tuple(dict.fromkeys([primary, *fallbacks]))
        self.lock = threading.Lock()
        self.blocked = {}
        self.proved = {}

    def choose(self, excluded=()):
        with self.lock:
            now = time.monotonic()
            eligible = [route for route in self.routes if route not in excluded
                        and self.blocked.get(route, 0) <= now]
            # Recent real generation beats a route with only a successful TCP/TLS probe.
            eligible.sort(key=lambda route: 0 if route in self.proved and now - self.proved[route] < 900 else 1)
            return eligible[0] if eligible else None

    def reject_region(self, route):
        with self.lock:
            now = time.monotonic()
            # A simultaneous auxiliary-model refusal must not globally condemn a
            # route that just delivered a real model answer.
            if route in self.proved and now - self.proved[route] < 90:
                return
            self.blocked[route] = now + 120

    def answer(self, route):
        if route not in self.routes:
            return
        with self.lock:
            self.proved[route] = time.monotonic()
            self.blocked.pop(route, None)

    def state(self):
        with self.lock:
            now = time.monotonic()
            return [{'proxy': route,
                     'model_answer_seen': route in self.proved,
                     'region_cooldown_seconds': max(0, int(self.blocked.get(route, 0) - now))}
                    for route in self.routes]
