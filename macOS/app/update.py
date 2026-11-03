#!/usr/bin/env python3
"""Update an installed git checkout of this package in place."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from launcher_common import runtime_path

ROOT = Path(__file__).resolve().parent
POINTER = b'version https://git-lfs.github.com/spec/v1'
RUNTIMES = ('agy', 'acp', 'harness')


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('git ' + ' '.join(args) + ': ' + (result.stderr.strip() or 'failed'))
    return result.stdout.strip()


def manifest(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def runtime_files(root):
    return [runtime_path(name, root) for name in RUNTIMES]


def lfs_pointers(root):
    """Runtime files still holding an LFS pointer instead of the original binary."""
    leftovers = []
    for path in runtime_files(root):
        with path.open('rb') as file:
            if file.read(len(POINTER)) == POINTER:
                leftovers.append(path)
    return leftovers


def verify(root):
    result = subprocess.run([sys.executable, str(root / 'verify_package.py')],
                            capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def version_text(root=ROOT):
    current = manifest(root / 'BUILD-MANIFEST.json')
    print('Обёртка: ' + current['wrapper_version'])
    print('agy ' + current['agy_version'] + ', ACP ' + current['acp_version'])
    print('Платформа: ' + current['platform'])
    print('Установка: ' + str(root.parent))
    print('Обновление: agym update (только проверка: agym update --check)')
    return 0


def update(root=ROOT, check_only=False, git=git, verify=verify):
    try:
        toplevel = Path(git(root, 'rev-parse', '--show-toplevel')).resolve()
        upstream = git(root, 'rev-parse', '--abbrev-ref', '@{u}')
        package = Path(root.parent).resolve().relative_to(toplevel).as_posix()
        git(root, 'fetch', '--prune', 'origin')
        behind = int(git(root, 'rev-list', '--count', 'HEAD..@{u}'))
        remote = json.loads(git(root, 'show', upstream + ':' + package + '/app/BUILD-MANIFEST.json'))
    except (RuntimeError, ValueError, KeyError) as error:
        print('Не удалось проверить обновления: ' + str(error), file=sys.stderr)
        print('agym update работает в git-клоне, где текущая ветка отслеживает origin/main.',
              file=sys.stderr)
        return 1
    current = manifest(root / 'BUILD-MANIFEST.json')
    if behind == 0:
        print('Обновлений нет: установлена последняя версия ' + current['wrapper_version'] + '.')
        return 0
    if remote['wrapper_version'] == current['wrapper_version']:
        print('Доступны новые коммиты ' + upstream + ' (' + str(behind) +
              '); версия обёртки не изменилась: ' + current['wrapper_version'], flush=True)
    else:
        print('Доступно обновление: ' + current['wrapper_version'] + ' -> ' +
              remote['wrapper_version'], flush=True)
    if current['agy_version'] != remote['agy_version'] or current['acp_version'] != remote['acp_version']:
        print('Вложенные бинарники: agy ' + current['agy_version'] + ' -> ' + remote['agy_version'] +
              ', ACP ' + current['acp_version'] + ' -> ' + remote['acp_version'] +
              ' (загрузка через Git LFS)')
    if check_only:
        print('Ничего не изменено. Применить: agym update')
        return 0
    dirty = git(root, 'status', '--porcelain', '--untracked-files=no')
    if dirty:
        print('Обновление отменено: в отслеживаемых файлах есть незакоммиченные изменения:',
              file=sys.stderr)
        print(dirty, file=sys.stderr)
        print('Сохраните их (git commit или git stash) и повторите agym update.', file=sys.stderr)
        return 1
    try:
        git(root, 'merge', '--ff-only', '@{u}')
        leftovers = lfs_pointers(root)
        if leftovers:
            git(root, 'lfs', 'pull')
            leftovers = lfs_pointers(root)
            if leftovers:
                print('Git LFS не восстановил бинарники: ' +
                      ', '.join(path.name for path in leftovers), file=sys.stderr)
                print('Установите git-lfs с https://git-lfs.com и повторите agym update.',
                      file=sys.stderr)
                return 1
    except RuntimeError as error:
        print('Обновление не завершено: ' + str(error), file=sys.stderr)
        return 1
    if verify(root):
        print('Проверка SHA256 не прошла, обновление не подтверждено. См. вывод выше.',
              file=sys.stderr)
        return 1
    print('Обновлено: ' + current['wrapper_version'] + ' -> ' + remote['wrapper_version'])
    print('Проверка после обновления: agym check')
    return 0


def main():
    parser = argparse.ArgumentParser(description='Обновление этой установки agym')
    parser.add_argument('--check', action='store_true', help='только показать доступные версии')
    parser.add_argument('--version', action='store_true', help='показать установленные версии')
    args = parser.parse_args()
    return version_text() if args.version else update(check_only=args.check)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
