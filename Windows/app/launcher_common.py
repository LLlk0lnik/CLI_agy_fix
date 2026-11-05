"""Shared, process-local settings for the portable macOS/Windows packages."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
DEFAULTS = {
    'http_proxy': 'http://127.0.0.1:10809',
    'socks_proxy': 'socks5h://127.0.0.1:10808',
    'default_model': '',
    'fallback_http_proxies': [],
    'routing': 'proxy',
}


def configuration(root=ROOT):
    config = DEFAULTS.copy()
    # config.json is a local, untracked override; the shipped template is the fallback.
    path = root / 'config.json'
    if not path.exists():
        path = root / 'config.example.json'
    if path.exists():
        supplied = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(supplied, dict) or set(supplied) - set(DEFAULTS):
            raise ValueError('config.json: unknown configuration keys')
        config.update(supplied)
    for key in ('http_proxy', 'socks_proxy'):
        if not isinstance(config[key], str) or not config[key]:
            raise ValueError('config.json: invalid ' + key)
    if not isinstance(config['default_model'], str):
        raise ValueError('config.json: default_model must be a string (empty uses agent default)')
    # Proxy URLs never carry credentials in this distribution's settings.
    from urllib.parse import urlsplit
    fallbacks = config['fallback_http_proxies']
    if not isinstance(fallbacks, list) or len(fallbacks) > 3 or not all(isinstance(v, str) for v in fallbacks):
        raise ValueError('fallback_http_proxies must be a list of up to three loopback HTTP proxies')
    http = urlsplit(config['http_proxy'])
    socks = urlsplit(config['socks_proxy'])
    if http.scheme != 'http' or http.hostname not in ('127.0.0.1', 'localhost', '::1') or not http.port:
        raise ValueError('http_proxy must be a loopback HTTP proxy URL with a port')
    if socks.scheme not in ('socks5', 'socks5h') or socks.hostname not in ('127.0.0.1', 'localhost', '::1') or not socks.port:
        raise ValueError('socks_proxy must be a loopback SOCKS5 proxy URL with a port')
    if http.username or http.password or socks.username or socks.password:
        raise ValueError('Do not store proxy credentials in config.json')
    for url in [config['http_proxy'], *fallbacks]:
        proxy = urlsplit(url)
        if (proxy.scheme != 'http' or proxy.hostname not in ('127.0.0.1', 'localhost', '::1')
                or not proxy.port or proxy.username or proxy.password
                or proxy.path not in ('', '/') or proxy.query or proxy.fragment):
            raise ValueError('HTTP proxies must be loopback URLs with a port and no credentials/path/query')
    # Migrate the old default without ever enabling its direct SmartDNS fallback.
    if config['routing'] == 'auto':
        config['routing'] = 'proxy'
    if config['routing'] != 'proxy':
        raise ValueError("SmartDNS removed: set routing to 'proxy' in app/config.json")
    if fallbacks:
        raise ValueError('ACP uses one explicit proxy: clear fallback_http_proxies in app/config.json')
    return config


def runtime_path(name, root=ROOT, windows=None):
    windows = os.name == 'nt' if windows is None else windows
    names = {'agy': 'agy.exe' if windows else 'agy',
             'acp': 'agy_acp_server.exe' if windows else 'agy_acp_server.par',
             'harness': 'localharness_external.exe' if windows else 'localharness_external'}
    return root / 'runtime' / names[name]


def clean_environment(base=None):
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key.upper().startswith(('AGY_', 'JETSKI_', 'ANTIGRAVITY_', 'GOOGLE_', 'GEMINI_', 'BAICODE_', 'AICODE_')) or key.upper() == 'CLOUD_CODE_URL':
            env.pop(key, None)
    return env


def proxy_environment(env, config):
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy'):
        env[key] = config['http_proxy']
    env['ALL_PROXY'] = env['all_proxy'] = config['socks_proxy']
    env['NO_PROXY'] = env['no_proxy'] = 'localhost,127.0.0.1,::1'
    return env


def private_home_environment(env, home, windows=None):
    windows = os.name == 'nt' if windows is None else windows
    env['HOME'] = str(home)
    env['GEMINI_HOME'] = str(home / '.gemini')
    if windows:
        # Go uses USERPROFILE on Windows, many other tools use APPDATA.
        from pathlib import PureWindowsPath
        profile = PureWindowsPath(str(home))
        env.update(USERPROFILE=str(home), APPDATA=str(home / 'AppData' / 'Roaming'),
                   LOCALAPPDATA=str(home / 'AppData' / 'Local'),
                   HOMEDRIVE=profile.drive, HOMEPATH=str(profile.root + str(profile.relative_to(profile.anchor))))
    return env


def acp_environment(root=ROOT):
    env = proxy_environment(clean_environment(), configuration(root))
    env.update(GEMINI_HOME=str(root / 'profile' / 'gemini'), AGY_ACP_FORCE_FILE_STORAGE='1',
               ANTIGRAVITY_HARNESS_PATH=str(runtime_path('harness', root)))
    # Keep ACP's documented GEMINI_HOME isolation; do not replace HOME here,
    # because its browser launcher must find the real user's browser registration.
    return env


def process_group_options():
    if os.name == 'nt':
        return {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
    return {'start_new_session': True}


def terminate_owned_process(proc):
    """Stop only the process tree created by this client, never all agy/Zed tasks."""
    if os.name == 'nt':
        if proc.poll() is None:
            subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()

def require_proxy(config):
    """Fail locally if Happ is absent; never switch to a direct/DNS route."""
    import socket
    from urllib.parse import urlsplit
    proxy = urlsplit(config['http_proxy'])
    try:
        with socket.create_connection((proxy.hostname, proxy.port), timeout=2):
            pass
    except OSError:
        raise RuntimeError('Прокси Happ недоступен. Включите Happ и проверьте http_proxy; '
                           'автоматического прямого/SmartDNS-маршрута нет.') from None


def verify_acp_runtime(root=ROOT):
    """Require the pinned, unmodified Google ACP and its companion harness."""
    import hashlib
    manifest = json.loads((root / 'BUILD-MANIFEST.json').read_text(encoding='utf-8'))
    for name in ('acp', 'harness'):
        path = runtime_path(name, root)
        metadata = manifest['binaries'][path.name]
        if not path.is_file() or path.stat().st_size != metadata['size']:
            raise RuntimeError('Неверный размер runtime: ' + path.name + '. Проверьте Git LFS и пакет.')
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(block)
        if digest.hexdigest() != metadata['sha256']:
            raise RuntimeError('SHA256 runtime не совпадает: ' + path.name + '. Агент не запущен.')
