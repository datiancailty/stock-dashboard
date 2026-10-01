"""Offline slim-package coverage; no frozen release manifest is rewritten."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import dashboard_refresh_daily as daily

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SCRIPTS = {
    'confirmed_dividend_basis.py', 'dashboard_data_sources.py', 'dashboard_diagnostics.py',
    'dashboard_private_session.py', 'dashboard_refresh_daily.py', 'dashboard_refresh_health.py',
    'dashboard_refresh_sync.py', 'dashboard_secret_store.py', 'dashboard_technical_indicators.py',
    'forward_dividend_basis.py', 'part4_date_sync.py', 'part4_dividend_date_materializer.py',
    'part4_official_announcement_sync.py', 'personal_dividend_refresh_sync.py',
    'personal_market_snapshot_sync.py', 'personal_news_sync.py', 'personal_public_forward_sync.py',
    'personal_recommendation_eval_sync.py', 'personal_technical_snapshot_sync.py', 'public_forward_basis.py',
    'dashboard_news_normalization.py', 'dashboard_recommendation_history.py',
    'dashboard_recommendation_outcomes.py',
}
RETIRED = {'plus_strategy_worker', 'update_market', 'update_news', 'process_trade_records',
           'part4_daily_sync', 'personal_forward_basis_sync', 'personal_future_dividend_grid_sync'}


class SharedRuntimeReleaseTests(unittest.TestCase):
    def package(self, directory):
        root = Path(directory)
        (root / 'scripts').mkdir()
        files = {}
        for name in sorted(RUNTIME_SCRIPTS):
            target = root / 'scripts' / name
            shutil.copyfile(ROOT / 'scripts' / name, target)
            files['scripts/' + name] = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest = {'version': 'synthetic-shared-runtime', 'files': files}
        (root / 'dashboard-refresh-release.json').write_text(json.dumps(manifest))
        return root, manifest

    def test_release_accepts_closed_package_without_paused_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root, manifest = self.package(directory)
            with patch.object(daily, 'ROOT', root):
                try:
                    result = daily.verify_release()
                except ValueError as error:
                    self.fail('slim package still requires retired scripts: ' + str(error))
                self.assertEqual(result, manifest['version'])
                for name in ('dashboard_private_session.py', 'dashboard_secret_store.py',
                             'dashboard_diagnostics.py', 'dashboard_news_normalization.py',
                             'dashboard_recommendation_history.py', 'dashboard_recommendation_outcomes.py',
                             'part4_date_sync.py', 'part4_dividend_date_materializer.py'):
                    reduced = dict(manifest, files={k: v for k, v in manifest['files'].items() if k != 'scripts/' + name})
                    (root / 'dashboard-refresh-release.json').write_text(json.dumps(reduced))
                    with self.subTest(missing=name), self.assertRaisesRegex(ValueError, 'coverage_incomplete'):
                        daily.verify_release()

    def test_runtime_imports_and_script_references_are_closed(self):
        for name in sorted(RUNTIME_SCRIPTS | {'part0_snapshot_sync.py'}):
            tree = ast.parse((ROOT / 'scripts' / name).read_text())
            references = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    references.update(alias.name.split('.')[0] + '.py' for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    references.add(node.module.split('.')[0] + '.py')
                elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.endswith('.py'):
                    references.add(Path(node.value).name)
            for target in references:
                if (ROOT / 'scripts' / target).is_file():
                    with self.subTest(consumer=name, dependency=target):
                        self.assertIn(target, RUNTIME_SCRIPTS)

    def test_fresh_slim_process_imports_without_io_or_retired_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _ = self.package(directory)
            shutil.copyfile(ROOT / 'scripts/part0_snapshot_sync.py', root / 'scripts/part0_snapshot_sync.py')
            code = f'''
import importlib, importlib.abc, json, sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, {str(root / 'scripts')!r})
retired = {RETIRED!r}
class NoRetired(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in retired:
            raise AssertionError('retired import: ' + fullname)
sys.meta_path.insert(0, NoRetired())
def forbidden(*args, **kwargs):
    raise AssertionError('import side effect')
import requests, pty
with patch('requests.sessions.Session.request', forbidden), patch('subprocess.run', forbidden), patch('pty.fork', forbidden), patch.object(Path, 'read_text', forbidden):
    for name in {sorted(RUNTIME_SCRIPTS | {'part0_snapshot_sync.py'})!r}:
        importlib.import_module(name[:-3])
    import part4_official_announcement_sync as adapter
    import dashboard_private_session as session
    assert adapter.load_private_worker_module() is session
    import personal_recommendation_eval_sync as outcomes
    assert outcomes.legacy_fetcher().__module__ == 'dashboard_recommendation_history'
assert not retired.intersection(sys.modules)
print(json.dumps({{'passed': True, 'retiredLoaded': False, 'importNetworkCalls': 0}}))
'''
            result = subprocess.run([sys.executable, '-B', '-c', code], cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['passed'])


if __name__ == '__main__':
    unittest.main()
