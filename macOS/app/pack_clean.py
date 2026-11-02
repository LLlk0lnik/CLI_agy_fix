#!/usr/bin/env python3
"""Repack only distribution files. Never traverse or include the local profile."""
import json
import hashlib
from pathlib import Path
import stat
import zipfile


def main(root=None):
    root = Path(__file__).resolve().parent.parent if root is None else Path(root)
    metadata = root / 'app' if (root / 'app/BUILD-MANIFEST.json').exists() else root
    manifest = json.loads((metadata / 'BUILD-MANIFEST.json').read_text(encoding='utf-8'))
    files = manifest['distribution_files']
    forbidden = {'profile', '__pycache__', '.git', 'dist', 'logs'}
    for name in files:
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or any(part in forbidden for part in relative.parts):
            raise ValueError('Unsafe distribution manifest entry')
        if not (root / relative).is_file() or (root / relative).is_symlink():
            raise ValueError('Missing/symlink distribution file: ' + name)
    destination = metadata / 'dist'
    destination.mkdir(exist_ok=True)
    target = destination / (manifest['package_name'] + '-clean.zip')
    sources = {name: (root / name).with_name('config.example.json') if Path(name).name == 'config.json' else root / name for name in files}
    hashes = []
    for name, source in sources.items():
        if Path(name).name == 'SHA256SUMS.txt':
            continue
        digest = hashlib.sha256()
        with source.open('rb') as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b''):
                digest.update(chunk)
        hashes.append(digest.hexdigest() + '  ' + name)
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in files:
            # config.json in a shared package always resets to the public defaults.
            arcname = manifest['package_name'] + '/' + name
            if Path(name).name == 'SHA256SUMS.txt':
                info = zipfile.ZipInfo.from_file(root / name, arcname)
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, '\n'.join(hashes) + '\n')
            else:
                archive.write(sources[name], arcname)
    print('Created: ' + str(target))
    print('Excluded: all profiles, accounts, logs, history and caches. config.json reset to example defaults.')


if __name__ == '__main__':
    main()
