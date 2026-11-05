#!/usr/bin/env python3
"""Preflight via the official ACP only. Never read OAuth credentials or call Google APIs."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from launcher_common import ROOT, configuration, require_proxy, verify_acp_runtime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--offline', action='store_true', help='check package and hashes only')
    parser.add_argument('--model-test', action='store_true', help='one synthetic prompt through ACP (uses quota)')
    parser.add_argument('--probe-model', action='append', help='model ID advertised by the official agent')
    args = parser.parse_args()
    if args.offline and args.model_test:
        parser.error('--offline cannot be combined with --model-test')
    if args.probe_model and not args.model_test:
        parser.error('--probe-model requires --model-test')
    config = configuration()
    manifest = json.loads((ROOT / 'BUILD-MANIFEST.json').read_text(encoding='utf-8'))
    target = manifest['platform']
    expected = 'windows' if target.startswith('windows') else 'darwin'
    actual = 'windows' if os.name == 'nt' else platform.system().lower()
    allowed = ('arm64', 'aarch64') if 'arm64' in target else ('amd64', 'x86_64')
    if actual != expected or platform.machine().lower() not in allowed:
        raise RuntimeError('Package platform/architecture mismatch: ' + target)
    verify_acp_runtime()
    print('OK: pinned Google ACP ' + manifest['acp_version'] + ' and harness SHA256 verified.')
    print('HTTP proxy: ' + config['http_proxy'])
    print('Profile: ' + str(ROOT / 'profile'))
    if args.offline:
        print('Offline check complete; no agent login or model request.')
        return 0
    require_proxy(config)
    command = [sys.executable, str(ROOT / 'acp_terminal.py')]
    result = subprocess.run(command + ['--check'], timeout=45)
    if result.returncode or not args.model_test:
        if not result.returncode:
            print('ACP handshake passed. Account eligibility and generation were NOT checked.')
        return result.returncode
    # Only an explicit model test initiates a session. No project context is supplied.
    with tempfile.TemporaryDirectory(prefix='agycli-acp-probe-') as directory:
        for model in list(dict.fromkeys(args.probe_model or [config['default_model']])):
            probe = command + ['--no-save-session', '--cwd', directory, '--prompt',
                               'Reply with OK only. Do not use tools, access files or run commands.']
            if model:
                probe += ['--model', model]
            result = subprocess.run(probe, timeout=360)
            if result.returncode:
                return result.returncode
    print('OK: official ACP returned text to the explicit model probe.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print('Check failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
