#!/usr/bin/env python3
"""Validate the shipped allowlist and hashes; profile is intentionally untracked."""
import argparse
import hashlib
from pathlib import Path
import sys

LFS_PREFIX = b'version https://git-lfs.github.com/spec/v1'


def lfs_pointer_oid(path):
    try:
        with path.open('rb') as file:
            header = file.read(len(LFS_PREFIX))
            if header == LFS_PREFIX:
                file.seek(0)
                for line in file.read().splitlines():
                    if line.startswith(b'oid sha256:'):
                        return line.split(b':', 1)[1].strip().decode('ascii')
    except OSError:
        pass
    return None


def main():
    parser = argparse.ArgumentParser(description='Verify package checksums')
    parser.add_argument('--allow-lfs-pointers', action='store_true',
                        help='accept unhydrated Git LFS pointer stubs matching the expected OID')
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    errors = []
    lines = (root / 'app/SHA256SUMS.txt').read_text(encoding='utf-8').splitlines()
    for line in lines:
        expected, relative = line.split('  ', 1)
        path = root / relative
        if not path.is_file():
            errors.append('Missing: ' + relative)
            continue
        if args.allow_lfs_pointers:
            pointer_oid = lfs_pointer_oid(path)
            if pointer_oid == expected:
                continue
        digest = hashlib.sha256()
        with path.open('rb') as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            errors.append('Changed: ' + relative)
    if errors:
        print('\n'.join(errors))
        print('Tracked files were changed after the release. Restore them or run tools/release.py.')
        return 1
    print('OK: all shipped files match SHA256SUMS.txt. Local profile files are not part of the distribution.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
