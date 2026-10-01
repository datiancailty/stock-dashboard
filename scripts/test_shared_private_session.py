"""Offline extraction contracts: the non-AI session is the canonical runtime."""
import importlib
from pathlib import Path
import unittest
from unittest.mock import patch


class SharedPrivateSessionTests(unittest.TestCase):
    def test_part4_and_paused_ai_share_the_non_ai_runtime(self):
        import part4_official_announcement_sync as part4
        import dashboard_private_session as session
        with patch('requests.post', side_effect=AssertionError('network on import')), \
                patch('subprocess.run', side_effect=AssertionError('process on import')), \
                patch('pty.fork', side_effect=AssertionError('PTY on import')), \
                patch.object(Path, 'read_text', side_effect=AssertionError('config on import')):
            importlib.reload(session)
            runtime = part4.load_private_worker_module()
            self.assertIs(runtime, session, 'Part4 must not dynamically load the paused AI worker')
            worker = importlib.import_module('plus_strategy_worker')
            for name in ('WorkerError', 'load_config', 'refresh_session', 'keychain_read',
                         'keychain_write', 'keychain_delete', 'private_rpc', 'login_request'):
                self.assertIs(getattr(worker, name), getattr(session, name), name)
        self.assertFalse(hasattr(runtime, 'run_codex'))
        self.assertFalse(hasattr(runtime, 'main'))


    def test_extracted_auth_definitions_are_identical_to_frozen_source(self):
        import ast
        import inspect
        import json
        import dashboard_private_session as session
        frozen = json.loads((Path(__file__).parent / 'fixtures/shared_runtime_7e378a41.json').read_text())
        for name, source in frozen['modules']['plus_strategy_worker']['functions'].items():
            with self.subTest(function=name):
                self.assertEqual(ast.dump(ast.parse(inspect.getsource(getattr(session, name)))),
                                 ast.dump(ast.parse(source)))

    def test_config_keeps_owner_binding_and_rejects_credentials(self):
        import json
        import tempfile
        import dashboard_private_session as session
        value = dict(schemaVersion=1, supabaseUrl=session.EXPECTED_SUPABASE_URL,
                     supabaseAnonKey='synthetic', username='owner-a',
                     keychainService=session.KEYCHAIN_SERVICE, keychainAccount='owner-a')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for change, category in [({}, None), ({'keychainAccount': 'owner-b'}, 'worker_keychain_binding_invalid'),
                                     ({'refresh_token': 'synthetic'}, 'worker_config_contains_credential'),
                                     ({'schemaVersion': 2}, 'worker_config_version_unsupported'),
                                     ({'supabaseUrl': 'https://wrong.invalid'}, 'worker_config_invalid')]:
                with self.subTest(category=category):
                    (root / 'config.json').write_text(json.dumps({**value, **change}))
                    if category:
                        with self.assertRaisesRegex(session.WorkerError, '^' + category + '$'):
                            session.load_config(root)
                    else:
                        self.assertEqual(session.load_config(root)['keychainAccount'], 'owner-a')

    def test_rpc_keeps_bearer_body_unwrapping_and_safe_error_categories(self):
        from types import SimpleNamespace
        import dashboard_private_session as session
        cfg = {'supabaseUrl': 'https://synthetic.invalid', 'supabaseAnonKey': 'synthetic-public'}
        body = {'p_value': {'nullable': None}, 'p_writer_secret': 'synthetic-writer'}
        response = SimpleNamespace(status_code=200, ok=True, json=lambda: [{'owner': 'a'}])
        with patch.object(session.requests, 'post', return_value=response) as post:
            self.assertEqual(session.private_rpc(cfg, 'synthetic-a', 'personal_get_part1', body), {'owner': 'a'})
            self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer synthetic-a')
            self.assertIs(post.call_args.kwargs['json'], body)
            self.assertEqual(post.call_args.kwargs['timeout'], 60)
            for code, category in [(401, 'private_session_rejected'), (403, 'private_session_rejected'),
                                   (404, 'hosted_worker_contract_missing'), (409, 'private_payload_rejected'),
                                   (422, 'private_payload_rejected'), (500, 'private_service_failed')]:
                response.status_code, response.ok = code, False
                with self.assertRaisesRegex(session.WorkerError, '^' + category + '$'):
                    session.private_rpc(cfg, 'synthetic-b', 'personal_get_part1', {})
            response.status_code, response.ok = 200, True
            response.json = lambda: (_ for _ in ()).throw(ValueError('sensitive body'))
            with self.assertRaisesRegex(session.WorkerError, '^private_service_invalid_response$'):
                session.private_rpc(cfg, 'synthetic-a', 'personal_get_part1', {})

    def test_real_lock_excludes_overlap_and_releases_after_exception(self):
        import tempfile
        import dashboard_private_session as session
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'lock'
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                with session.session_lock(root):
                    with self.assertRaisesRegex(ValueError, 'session_lock_timeout'):
                        with session.session_lock(root, timeout=0):
                            self.fail('overlapping refresh')
                    raise RuntimeError('synthetic failure')
            with session.session_lock(root, timeout=0):
                self.assertEqual((root / 'auth-session.lock').stat().st_mode & 0o777, 0o600)
            (root / 'auth-session.lock').chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'session_lock_invalid'):
                with session.session_lock(root):
                    self.fail('unsafe lock')

    def test_adapter_resolves_worker_directory_at_session_time(self):
        # The old dynamic worker load observed environment changes per session,
        # rather than freezing the directory when Part4 was first imported.
        import os
        from contextlib import nullcontext
        import dashboard_private_session as session
        import part4_official_announcement_sync as part4
        with patch.dict(os.environ, {'STOCK_DASHBOARD_WORKER_DIR': '/synthetic/owner-worker'}), \
                patch.object(session, 'load_config', return_value={}) as load, \
                patch.object(session, 'refresh_session', return_value='synthetic'), \
                patch.object(session, 'session_lock', return_value=nullcontext()):
            self.assertEqual(part4.load_private_session()[2], 'synthetic')
            load.assert_called_once_with(Path('/synthetic/owner-worker'))


if __name__ == '__main__':
    unittest.main()
