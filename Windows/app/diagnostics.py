#!/usr/bin/env python3
"""Local preflight. Never starts interactive login or prints credential data."""
import argparse
import json
import os
import platform
import subprocess
import sys
import urllib.request
import urllib.error
from route_health import NoRedirect, classify_error

from launcher_common import ROOT, configuration, runtime_path, acp_environment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--offline', action='store_true', help='check files and architecture only')
    parser.add_argument('--model-test', action='store_true',
                        help='send two small synthetic model requests (uses account quota)')
    parser.add_argument('--probe-model', action='append',
                        help='with --model-test, test only these explicit model IDs (repeatable)')
    args = parser.parse_args()
    if args.offline and args.model_test:
        parser.error('--offline cannot be combined with --model-test')
    if args.probe_model and not args.model_test:
        parser.error('--probe-model requires --model-test')
    config = configuration()
    manifest = json.loads((ROOT / 'BUILD-MANIFEST.json').read_text(encoding='utf-8'))
    target = manifest['platform']
    actual = ('windows' if os.name == 'nt' else platform.system().lower())
    expected = 'windows' if target.startswith('windows') else 'darwin'
    if actual != expected:
        print('ERROR: this package is for ' + target + '; current OS: ' + actual)
        return 1
    machine = platform.machine().lower()
    allowed = ('arm64', 'aarch64') if 'arm64' in target else ('amd64', 'x86_64')
    if machine not in allowed:
        print('ERROR: package architecture does not match ' + machine)
        return 1
    for name in ('agy', 'acp', 'harness'):
        path = runtime_path(name)
        if not path.is_file() or (os.name != 'nt' and not os.access(path, os.X_OK)):
            print('ERROR: missing/non-executable runtime: ' + path.name)
            return 1
    print('OK: Python ' + platform.python_version() + ', package ' + target + ', all three runtime files present.')
    print('Profile: ' + str(ROOT / 'profile'))
    print('Configured HTTP proxies: ' + ', '.join([config['http_proxy'], *config.get('fallback_http_proxies', [])]))
    if args.offline:
        print('Offline check complete. Network, login and model response were not checked.')
        return 0
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        request = urllib.request.Request('https://example.com/')
        from urllib.parse import urlsplit
        request.add_unredirected_header('Host', 'example.com')
        request.set_proxy(urlsplit(config['http_proxy']).netloc, 'http')
        with opener.open(request, timeout=20) as response:
            if response.status != 200:
                raise RuntimeError('Unexpected proxy response')
    except Exception:
        print('WARNING: HTTPS test site through the primary proxy is unavailable. This is not a model-region verdict.')
        if not args.model_test:
            return 1
    else:
        print('OK: HTTPS via primary HTTP proxy. This is not proof of model access.')
    result = subprocess.run([sys.executable, str(ROOT / 'acp_terminal.py'), '--check'],
                            env=acp_environment(), timeout=45)
    if result.returncode:
        return result.returncode
    token = ROOT / 'profile/gemini/antigravity-acp/acp_token.json'
    if not token.exists():
        print('NEXT: run Login in setup, then repeat this check.')
        print('No account has been copied into this package.')
        return 1 if args.model_test else 0
    from agy_native import Backend
    try:
        backend = Backend(token, config)
        catalog = backend.models()
        backend.catalog = catalog.get('models', {})
    except urllib.error.HTTPError as error:
        category = classify_error(error.code, error.read())
        print('ERROR: catalog check HTTP ' + str(error.code) + ', category: ' + category)
        error.close()
        print('Region: check Happ route/server. Authentication: repeat Login. Do not share profile or logs.')
        return 1
    except Exception as error:
        print('ERROR: profile/proxy check failed (' + type(error).__name__ + '). Do not share profile or logs.')
        return 1
    print('OK: account tier/project and model catalog available. This does NOT prove generation works.')
    if not args.model_test:
        print('NEXT: Check --model-test verifies actual model responses (two small requests use quota).')
        return 0
    from model_probe import probe_model
    models = args.probe_model or [config['default_model'], 'gemini-3.1-flash-lite-preview']
    results = [probe_model(backend, model) for model in dict.fromkeys(models)]
    for result in results:
        print(json.dumps(result, ensure_ascii=False))
    print('Routes: ' + json.dumps(backend.routes.state(), ensure_ascii=False))
    if not all(result['status'] == 'model_answer' for result in results):
        print('NOT READY: a catalog/HTTPS check alone is insufficient. Region failures require a different working exit.')
        return 1
    print('OK: every tested model returned actual content through the configured proxy routes.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print('Check failed (' + type(exc).__name__ + '). See README.md.', file=sys.stderr)
        sys.exit(1)
