#!/usr/bin/env python3
"""Refresh a package's manifest/checksums, run its tests and build the clean ZIP."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
LFS_POINTER = b'version https://git-lfs.github.com/spec/v1'


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def check_not_lfs_pointer(path):
    with Path(path).open('rb') as file:
        if file.read(len(LFS_POINTER)) == LFS_POINTER:
            raise SystemExit(
                'Cannot release package: ' + str(path) +
                ' is an unhydrated Git LFS pointer. Run "git lfs pull" before building release.'
            )


def refresh(package, version=None, build_date=None):
    """Rewrite BUILD-MANIFEST.json metadata and SHA256SUMS.txt from the files on disk."""
    package = Path(package)
    manifest_path = package / 'app/BUILD-MANIFEST.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if version:
        manifest['wrapper_version'] = version
    if build_date:
        manifest['build_date'] = build_date

    # Validate distribution files exist before modifying the manifest
    for name in manifest['distribution_files']:
        if Path(name).name == 'SHA256SUMS.txt':
            continue
        path = package / name
        if not path.is_file():
            raise SystemExit('Missing distribution file: ' + str(path))

    # Validate binaries exist, are hydrated (not LFS stubs), and update their hashes
    for name, meta in manifest['binaries'].items():
        binary = package / 'app/runtime' / name
        if not binary.is_file():
            raise SystemExit('Missing binary: ' + str(binary))
        check_not_lfs_pointer(binary)
        meta['sha256'] = sha256(binary)
        meta['size'] = binary.stat().st_size

    # The manifest is hashed by SHA256SUMS.txt, so write it before checksumming.
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    lines = []
    for name in manifest['distribution_files']:
        if Path(name).name == 'SHA256SUMS.txt':
            continue
        path = package / name
        lines.append(sha256(path) + '  ' + name)
    (package / 'app/SHA256SUMS.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description='Prepare an agyCLI package release')
    parser.add_argument('package', choices=['macOS', 'Windows'])
    parser.add_argument('--version', help='new wrapper_version, for example 1.3.0')
    parser.add_argument('--date', help='build_date, default today (YYYY-MM-DD)')
    parser.add_argument('--no-test', action='store_true', help='skip the package test suite')
    parser.add_argument('--no-zip', action='store_true', help='do not build the clean ZIP')
    args = parser.parse_args()
    package = ROOT / args.package
    build_date = args.date or datetime.date.today().isoformat()
    manifest = refresh(package, args.version, build_date)
    print('Updated ' + str(package / 'app/SHA256SUMS.txt') +
          ' for wrapper ' + manifest['wrapper_version'])
    if not args.no_test:
        subprocess.run([sys.executable, str(package / 'app/run_tests.py')], check=True)
    if not args.no_zip:
        subprocess.run([sys.executable, str(package / 'app/pack_clean.py')], check=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
