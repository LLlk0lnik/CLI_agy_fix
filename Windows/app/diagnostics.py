#!/usr/bin/env python3
"""Local preflight. Never starts interactive login or prints credential data."""
import argparse
import json
import os
import platform
import subprocess
import sys
import urllib.request

from launcher_common import ROOT, configuration, runtime_path, acp_environment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--offline', action='store_true', help='check files and architecture only')
    args = parser.parse_args()
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
    if args.offline:
        print('Offline check complete. Network, login and model response were not checked.')
        return 0
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': config['http_proxy'],
                                                                     'https': config['http_proxy']}))
    try:
        with opener.open('https://example.com/', timeout=20) as response:
            if response.status != 200:
                raise RuntimeError('Unexpected proxy response')
    except Exception:
        print('ERROR: HTTPS through the local HTTP proxy is unavailable. Check Happ and config.json.')
        return 1
    print('OK: HTTPS via local HTTP proxy.')
    result = subprocess.run([sys.executable, str(ROOT / 'acp_terminal.py'), '--check'],
                            env=acp_environment(), timeout=45)
    if result.returncode:
        return result.returncode
    token = ROOT / 'profile/gemini/antigravity-acp/acp_token.json'
    if not token.exists():
        print('NEXT: run Login in setup, then repeat this check.')
        print('No account has been copied into this package.')
        return 0
    from agy_native import Backend
    try:
        Backend(token, config).models()
    except Exception:
        print('ERROR: account/model catalog check failed. Repeat Login; do not share profile or server.log.')
        return 1
    print('OK: own account has an active tier/project and model catalog. Ready for Run-AGY.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print('Check failed (' + type(exc).__name__ + '). See README.md.', file=sys.stderr)
        sys.exit(1)
