import json
import os
from pathlib import Path, PureWindowsPath
import tempfile
import unittest
import zipfile
import hashlib
import contextlib
import io
from unittest.mock import patch
from launcher_common import (configuration, runtime_path, clean_environment,
                             proxy_environment, private_home_environment, acp_environment)


class LauncherTests(unittest.TestCase):
    def test_repack_excludes_profile_and_resets_custom_config(self):
        from pack_clean import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'profile').mkdir()
            (root / 'profile/private.json').write_text('never-share-this-profile')
            (root / 'config.json').write_text('private-custom-config')
            (root / 'config.example.json').write_text('{}')
            (root / 'SHA256SUMS.txt').write_text('obsolete checksum')
            files = ['config.json', 'config.example.json', 'SHA256SUMS.txt', 'BUILD-MANIFEST.json']
            (root / 'BUILD-MANIFEST.json').write_text(json.dumps({'package_name': 'test-package', 'distribution_files': files}))
            with contextlib.redirect_stdout(io.StringIO()):
                main(root)
            with zipfile.ZipFile(root / 'dist/test-package-clean.zip') as archive:
                self.assertEqual(len(archive.namelist()), 4)
                self.assertTrue(all('/profile/' not in name for name in archive.namelist()))
                self.assertEqual(archive.read('test-package/config.json'), b'{}')
                for line in archive.read('test-package/SHA256SUMS.txt').decode().splitlines():
                    digest, name = line.split('  ', 1)
                    self.assertEqual(hashlib.sha256(archive.read('test-package/' + name)).hexdigest(), digest)

    def test_windows_runtime_names(self):
        root = Path('/package')
        self.assertEqual(runtime_path('agy', root, True).name, 'agy.exe')
        self.assertEqual(runtime_path('acp', root, True).name, 'agy_acp_server.exe')
        self.assertEqual(runtime_path('harness', root, True).name, 'localharness_external.exe')
        self.assertEqual(runtime_path('acp', root, False).name, 'agy_acp_server.par')

    def test_windows_home_is_process_local(self):
        before = dict(os.environ)
        env = private_home_environment({}, PureWindowsPath(r'C:\Portable Folder\profile\native-home'), True)
        self.assertEqual(env['USERPROFILE'], r'C:\Portable Folder\profile\native-home')
        self.assertEqual(env['HOMEDRIVE'], 'C:')
        self.assertEqual(env['HOMEPATH'], r'\Portable Folder\profile\native-home')
        self.assertEqual(env['APPDATA'], r'C:\Portable Folder\profile\native-home\AppData\Roaming')
        self.assertEqual(dict(os.environ), before)

    def test_configurable_loopback_proxy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'config.json').write_text(json.dumps({'http_proxy': 'http://127.0.0.1:8080'}))
            config = configuration(root)
            self.assertEqual(proxy_environment({}, config)['HTTPS_PROXY'], 'http://127.0.0.1:8080')
            (root / 'config.json').write_text(json.dumps({'http_proxy': 'http://external.example:8080'}))
            with self.assertRaises(ValueError):
                configuration(root)

    def test_parent_secrets_are_not_forwarded(self):
        base = {'PATH': '/bin', 'GEMINI_API_KEY': 'fake', 'AGY_ADC_AUTH': 'true',
                'GOOGLE_APPLICATION_CREDENTIALS': '/do-not-read', 'CLOUD_CODE_URL': 'https://invalid'}
        self.assertEqual(clean_environment(base), {'PATH': '/bin'})
        self.assertEqual(base['GEMINI_API_KEY'], 'fake')

    def test_acp_profile_never_uses_os_keychain(self):
        root = Path('/package')
        env = acp_environment(root)
        self.assertEqual(env['AGY_ACP_FORCE_FILE_STORAGE'], '1')
        self.assertEqual(env['GEMINI_HOME'], str(root / 'profile' / 'gemini'))


if __name__ == '__main__':
    unittest.main()
