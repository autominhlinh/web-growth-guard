import importlib.util
import tempfile
import unittest
from pathlib import Path
from web_growth_guard import __version__
from web_growth_guard.core import canonical_json
from web_growth_guard.mcp_server import VERSION

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_check', ROOT/'tools'/'release_check.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class ReleaseTests(unittest.TestCase):
    def test_unsafe_source_paths_and_private_patterns(self):
        for name in ('../README.md','/README.md','src/../../x.py','src/.env','examples/export.csv','state/db.sqlite','docs/private.pem','foo.py'):
            try:
                self.assertFalse(check.allowed_source(name), name)
            except ValueError:
                pass
        self.assertTrue(check.allowed_source('examples/analytics/gsc_pages-current.csv'))
        self.assertTrue(check.allowed_source('.github/ISSUE_TEMPLATE/bug.yml'))
        self.assertTrue(check.allowed_source('.github/PULL_REQUEST_TEMPLATE.md'))
        self.assertFalse(check.allowed_source('.github/ISSUE_TEMPLATE/private.yml'))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'README.md').write_text('ghp_'+'x'*36)
            with self.assertRaisesRegex(ValueError,'Sensitive-looking'):
                check.read_source(root,'README.md')
            (root/'README.md').unlink()
            (root/'outside.txt').write_text('test')
            (root/'README.md').symlink_to(root/'outside.txt')
            with self.assertRaisesRegex(ValueError,'symlink'):
                check.read_source(root,'README.md')

    def test_source_manifest_and_version(self):
        files = check.source_map(ROOT)
        self.assertIn('tools/release_check.py',files)
        self.assertIn('docs/release-checklist.md',files)
        self.assertIn('docs/quickstart.md',files)
        self.assertIn('.github/ISSUE_TEMPLATE/bug.yml',files)
        self.assertEqual(__version__,VERSION)
        import tomllib
        self.assertEqual(__version__,tomllib.loads(files['pyproject.toml'].decode())['project']['version'])
        with self.assertRaises(ValueError):
            canonical_json({'invalid':float('inf')})
