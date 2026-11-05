import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import update
from launcher_common import runtime_path


CURRENT = {'package_name': 'agyCLI-macos-arm64', 'wrapper_version': '1.2.0',
           'platform': 'macos-arm64', 'agy_version': '1.4.12', 'acp_version': '1.4.5'}
REMOTE = dict(CURRENT, wrapper_version='1.3.0', agy_version='1.5.0', acp_version='1.5.0')
POINTER = (b'version https://git-lfs.github.com/spec/v1\n'
           b'oid sha256:34423fc9b0af3841e7646aa72a2d40ffeab8218b22d1cb99eac7355f9eba31e3\n'
           b'size 197589952\n')


class FakeGit:
    """Serves canned repository state and records every git invocation."""

    def __init__(self, behind=1, status='', remote=None, upstream='origin/main',
                 pointers=(), restore_lfs=False):
        self.behind = behind
        self.status = status
        self.remote = remote if remote is not None else REMOTE
        self.upstream = upstream
        self.pointers = list(pointers)
        self.restore_lfs = restore_lfs
        self.calls = []

    def __call__(self, root, *args):
        command = ' '.join(args)
        self.calls.append(command)
        if command == 'rev-parse --show-toplevel':
            return str(Path(root).parent.parent)
        if command == 'rev-parse --abbrev-ref @{u}':
            return self.upstream
        if command.startswith('fetch --prune'):
            return ''
        if command == 'rev-list --count HEAD..@{u}':
            return str(self.behind)
        if command.startswith('show '):
            return json.dumps(self.remote)
        if command == 'status --porcelain --untracked-files=no':
            return self.status
        if command == 'merge --ff-only @{u}':
            return 'Fast-forward'
        if command == 'lfs pull':
            if self.restore_lfs:
                for path in self.pointers:
                    path.write_bytes(b'real original binary')
            return ''
        raise AssertionError('unexpected git call: ' + command)

    def ran(self, prefix):
        return any(call.startswith(prefix) for call in self.calls)


def make_package(temp, manifest=None):
    """Build a package tree; returns the app directory update.py receives."""
    package = Path(temp) / 'macOS'
    app = package / 'app'
    app.mkdir(parents=True)
    (app / 'BUILD-MANIFEST.json').write_text(json.dumps(manifest or CURRENT))
    for name in ('agy', 'acp', 'harness'):
        path = runtime_path(name, app)
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b'real original binary')
    (package / 'agym').write_text('#!/bin/bash\n')
    return app


class UpdateTests(unittest.TestCase):
    def run_update(self, app, git, verify=None, check_only=False):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = update.update(app, check_only=check_only, git=git,
                                 verify=verify or (lambda root: 0))
        return code, output.getvalue()

    def test_version_text_reports_wrapper_runtime_and_install_dir(self):
        with tempfile.TemporaryDirectory() as temp:
            app = make_package(temp)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = update.version_text(app)
            self.assertEqual(code, 0)
            text = output.getvalue()
            self.assertIn('1.2.0', text)
            self.assertIn('agy 1.4.12', text)
            self.assertIn('ACP 1.4.5', text)
            self.assertIn(str(app.parent), text)

    def test_check_only_reports_new_version_without_changing_anything(self):
        with tempfile.TemporaryDirectory() as temp:
            app = make_package(temp)
            git = FakeGit(behind=2)
            code, text = self.run_update(app, git, check_only=True)
            self.assertEqual(code, 0)
            self.assertIn('1.2.0 -> 1.3.0', text)
            self.assertIn('agy 1.4.12 -> 1.5.0', text)
            self.assertFalse(git.ran('merge'))
            self.assertFalse(git.ran('lfs'))

    def test_same_version_with_new_commits_is_reported_without_fake_arrow(self):
        with tempfile.TemporaryDirectory() as temp:
            app = make_package(temp)
            git = FakeGit(behind=3, remote=CURRENT)
            code, text = self.run_update(app, git, check_only=True)
            self.assertEqual(code, 0)
            self.assertIn('новые коммиты', text)
            self.assertNotIn('1.2.0 -> 1.2.0', text)

    def test_same_version_update_reports_applied_commits(self):
        with tempfile.TemporaryDirectory() as temp:
            app = make_package(temp)
            git = FakeGit(behind=3, remote=CURRENT)
            code, text = self.run_update(app, git)
            self.assertEqual(code, 0)
            self.assertIn('применены новые коммиты', text)
            self.assertNotIn('1.2.0 -> 1.2.0', text)

    def test_already_up_to_date_does_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            app = make_package(temp)
            git = FakeGit(behind=0, remote=CURRENT)
            code, text = self.run_update(app, git)
            self.assertEqual(code, 0)
            self.assertIn('Обновлений нет', text)
            self.assertFalse(git.ran('merge'))
            self.assertFalse(git.ran('status'))

    def test_already_up_to_date_with_leftover_lfs_pointers_heals_installation(self):
        with tempfile.TemporaryDirectory() as temp:
            app = make_package(temp)
            pointer = runtime_path('agy', app)
            pointer.write_bytes(POINTER)
            git = FakeGit(behind=0, remote=CURRENT, pointers=[pointer], restore_lfs=True)
            code, text = self.run_update(app, git)
            self.assertEqual(code, 0)
            self.assertIn('успешно восстановлены', text)
            self.assertTrue(git.ran('lfs pull'))

    def test_local_uncommitted_changes_block_the_update(self):
        with tempfile.TemporaryDirectory() as temp:
            app = make_package(temp)
            git = FakeGit(status=' M app/agy_native.py')
            code, text = self.run_update(app, git)
            self.assertEqual(code, 1)
            self.assertIn('незакоммиченные', text)
            self.assertFalse(git.ran('merge'))

    def test_fast_forward_pulls_lfs_and_verifies_hashes_in_order(self):
        with tempfile.TemporaryDirectory() as temp:
            app = make_package(temp)
            pointer = runtime_path('agy', app)
            pointer.write_bytes(POINTER)
            git = FakeGit(pointers=[pointer], restore_lfs=True)
            checked = []

            def record_verify(root):
                checked.append(root)
                return 0

            code, text = self.run_update(app, git, verify=record_verify)
            self.assertEqual(code, 0)
            self.assertEqual(checked, [app])
            self.assertLess(git.calls.index('merge --ff-only @{u}'), git.calls.index('lfs pull'))
            self.assertIn('1.2.0 -> 1.3.0', text)

    def test_pointer_left_behind_is_reported_with_lfs_instructions(self):
        with tempfile.TemporaryDirectory() as temp:
            app = make_package(temp)
            pointer = runtime_path('acp', app)
            pointer.write_bytes(POINTER)
            git = FakeGit(pointers=[pointer], restore_lfs=False)
            code, text = self.run_update(app, git)
            self.assertEqual(code, 1)
            self.assertIn('Git LFS', text)

    def test_checksum_mismatch_fails_after_merge(self):
        with tempfile.TemporaryDirectory() as temp:
            app = make_package(temp)
            git = FakeGit()
            code, text = self.run_update(app, git, verify=lambda root: 1)
            self.assertEqual(code, 1)
            self.assertIn('SHA256', text)

    def test_broken_git_state_is_reported_not_raised(self):
        def broken(root, *args):
            raise RuntimeError('fatal: no upstream configured')
        with tempfile.TemporaryDirectory() as temp:
            app = make_package(temp)
            code, text = self.run_update(app, broken)
            self.assertEqual(code, 1)
            self.assertIn('no upstream configured', text)

    def test_missing_git_executable_reports_clean_error(self):
        def missing(root, *args):
            raise FileNotFoundError('No such file or directory: git')
        with tempfile.TemporaryDirectory() as temp:
            app = make_package(temp)
            code, text = self.run_update(app, missing)
            self.assertEqual(code, 1)
            self.assertIn('No such file or directory: git', text)


if __name__ == '__main__':
    unittest.main()
