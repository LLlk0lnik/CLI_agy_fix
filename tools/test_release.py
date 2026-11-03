import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import release


def make_package(temp):
    package = Path(temp) / 'macOS'
    (package / 'app/runtime').mkdir(parents=True)
    (package / 'app/runtime/agy').write_bytes(b'original binary')
    (package / 'README.md').write_text('readme\n')
    (package / 'app/BUILD-MANIFEST.json').write_text(json.dumps({
        'package_name': 'test',
        'wrapper_version': '1.0.0',
        'build_date': '2020-01-01',
        'binaries': {'agy': {'sha256': '', 'size': 0}},
        'distribution_files': ['README.md', 'app/BUILD-MANIFEST.json', 'app/SHA256SUMS.txt'],
    }))
    return package


class ReleaseTests(unittest.TestCase):
    def test_refresh_updates_version_binary_metadata_and_checksums(self):
        with tempfile.TemporaryDirectory() as temp:
            package = make_package(temp)
            manifest = release.refresh(package, '1.3.0', '2030-01-01')
            self.assertEqual(manifest['wrapper_version'], '1.3.0')
            self.assertEqual(manifest['build_date'], '2030-01-01')
            self.assertEqual(manifest['binaries']['agy']['sha256'],
                             hashlib.sha256(b'original binary').hexdigest())
            self.assertEqual(manifest['binaries']['agy']['size'], len(b'original binary'))
            stored = json.loads((package / 'app/BUILD-MANIFEST.json').read_text())
            lines = (package / 'app/SHA256SUMS.txt').read_text().splitlines()
            self.assertEqual([line.split('  ', 1)[1] for line in lines],
                             ['README.md', 'app/BUILD-MANIFEST.json'])
            for line in lines:
                digest, name = line.split('  ', 1)
                self.assertEqual(hashlib.sha256((package / name).read_bytes()).hexdigest(), digest)
            self.assertEqual(stored['wrapper_version'], '1.3.0')

    def test_refresh_reports_missing_distribution_file(self):
        with tempfile.TemporaryDirectory() as temp:
            package = make_package(temp)
            (package / 'README.md').unlink()
            with self.assertRaises(SystemExit):
                release.refresh(package)


if __name__ == '__main__':
    unittest.main()
